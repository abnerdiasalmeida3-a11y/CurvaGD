r"""Confere a pilha de calculo da Correcao de demanda, sem passar pela interface.

Script `verificar.py` da ferramenta CORRECAO_DEMANDA_BDGD, apontado para os
modulos que agora vivem dentro do CurvaGD (`app.correcao` e `app.calculo`).
Rode a partir da pasta `codigo`:

  .venv\Scripts\python.exe scripts\verificar_correcao.py --bdgd "C:/.../BDGD.gdb" --nasa "C:/.../NASA.csv" --ctmt 764500

Roda um alimentador de ponta a ponta e checa resultados que, se quebrarem,
quebram em silencio:

  1. a inversao exata da demanda concorda com a bissecao do modulo `gd`;
  2. a energia importada recalculada com a demanda achada bate com o ENE da BDGD;
  3. a curva de um recorte, na base fixa, tem o mesmo pico da curva do mes -- e o
     que permite multiplicar um recorte qualquer por DEM_MAX;
  4. a demanda de janeiro bate com a que o app TESTE_CORRECAO_CARGA calcula para
     a mesma UC, quando as duas ferramentas recebem o mesmo recorte;
  5. a curva sintetica do alimentador fecha: fases somam o total, a energia da
     carga e a da geracao batem com as UCs, e as regras de fase fazem o previsto.

Exemplos:
  python scripts/verificar_correcao.py --bdgd "C:/.../Energisa_MT_405.gdb"
  python scripts/verificar_correcao.py --referencia ../TESTE_CORRECAO_CARGA
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.calculo import gd, irradiancia  # noqa: E402
from app.correcao import alimentador, aneel, bdgd, curvas, demanda, estatisticas  # noqa: E402

TOLERANCIA = 1e-9


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bdgd", required=True,
                   help="GeoPackage (.gpkg) ou File Geodatabase (.gdb) da BDGD")
    p.add_argument("--nasa", required=True)
    p.add_argument("--aneel", nargs="+", default=None,
                   help="CSV(s) tecnicos da ANEEL; sem isto usa a copia local do workspace")
    p.add_argument("--workspace", default=str(RAIZ / "workspace"))
    p.add_argument("--ctmt", required=True)
    p.add_argument("--ano", type=int, default=2024)
    p.add_argument("--passo", type=int, default=curvas.PASSO_NATIVO)
    p.add_argument("--amostra", type=int, default=30,
                   help="UCs com GD conferidas contra a bissecao")
    p.add_argument("--referencia", default=None,
                   help="pasta do TESTE_CORRECAO_CARGA, para comparar a demanda de janeiro")
    return p.parse_args()


def carregar_modulo(nome, caminho):
    """Importa um modulo do projeto de referencia sob outro nome.

    Os arquivos de la se chamam curvas.py e gd.py, iguais aos nossos; carregar
    por caminho evita que um substitua o outro em sys.modules.
    """
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class Placar:
    """Acumula o resultado de cada checagem para o codigo de saida no fim."""

    def __init__(self):
        self.falhas = 0

    def checar(self, titulo, condicao, detalhe):
        marca = "OK   " if condicao else "FALHA"
        print(f"  [{marca}] {titulo}: {detalhe}")
        if not condicao:
            self.falhas += 1


def main():
    args = parse_args()
    placar = Placar()

    print("=== CARREGANDO ===")
    inicio = time.perf_counter()
    bdgd.definir_pasta_cache(Path(args.workspace) / "cache_bdgd")
    fonte = bdgd.abrir(args.bdgd)
    fonte.preparar()
    ucs, resumo = fonte.unidades(args.ctmt)
    if ucs.empty:
        sys.exit(f"O alimentador {args.ctmt} nao tem UC ativa.")
    ugs = fonte.geradoras(args.ctmt)
    banco = fonte.banco_de_curvas()
    print(f"  BDGD: {resumo['total']} UCs ({resumo['bt']} BT / {resumo['mt']} MT), "
          f"{resumo['com_ceg']} com CEG_GD, {resumo['repetidos']} linha(s) repetida(s) "
          f"consolidada(s)  [{time.perf_counter() - inicio:.1f}s]")

    marca = time.perf_counter()
    base = (aneel.potencias(args.aneel, ucs["CEG_GD"]) if args.aneel
            else aneel.potencias_copia_local(args.workspace, ucs["CEG_GD"]))
    potencias = aneel.potencias_por_uc(ucs, base, ugs)
    print(f"  ANEEL: {len(base)} GDs casadas  [{time.perf_counter() - marca:.1f}s]")

    marca = time.perf_counter()
    serie, _ = irradiancia.ler_csv_nasa(args.nasa)
    irrad = irradiancia.reamostrar(
        irradiancia.serie_ano(serie, args.ano), args.passo, passo_origem=60
    )
    print(f"  Irradiancia: {len(irrad)} pontos de {args.passo} min  "
          f"[{time.perf_counter() - marca:.1f}s]")

    print("\n=== CALCULANDO ===")
    resultado, tempos, insumos = demanda.calcular(
        ucs, potencias, banco, irrad, args.ano, args.passo, diagnostico=True,
        devolver_insumos=True,
    )
    print("  " + "   ".join(f"{nome} {valor:.2f}s" for nome, valor in tempos.items()))
    print("  status: " + ", ".join(f"{chave}={valor}" for chave, valor
                                   in resultado["STATUS"].value_counts().items()))

    print("\n=== CONFERENCIAS ===")

    usados = sorted(set(ucs["TIP_CC"]) & set(banco))
    anuais = {codigo: curvas.curva_ano(banco, codigo, args.ano, args.passo)[0]
              for codigo in usados}
    indice = anuais[usados[0]].index
    fatias = demanda._fatias_mensais(indice)
    irrad_alinhada = demanda._alinhar_irradiancia(irrad, indice)

    # 1. inversao exata x bissecao
    kw = potencias["POT_MODULOS"].to_numpy(dtype=float)
    kva = potencias["POT_INVERSOR"].to_numpy(dtype=float)
    com_gd = np.flatnonzero((kw > 0) & (kva > 0) & ucs["TIP_CC"].isin(anuais).to_numpy())
    amostra = com_gd[: args.amostra]
    pior = 0.0
    energias = ucs[demanda.COLUNAS_ENE].to_numpy(dtype=float)
    for i in amostra:
        curva = anuais[ucs["TIP_CC"].iloc[i]].to_numpy()
        razao = kw[i] / kva[i]
        geracao = gd.geracao_por_kva(razao, irrad_alinhada, args.passo).to_numpy() * kva[i]
        for m, fatia in enumerate(fatias):
            exato = gd.demanda_maxima_exata(curva[fatia], geracao[fatia],
                                            energias[i, m], args.passo)
            bissecao = gd.demanda_maxima(curva[fatia], geracao[fatia],
                                         energias[i, m], args.passo)
            if bissecao > 0:
                pior = max(pior, abs(exato - bissecao) / bissecao)
    placar.checar("inversao exata x bissecao", pior < TOLERANCIA,
                  f"erro relativo maximo {pior:.2e} em {len(amostra)} UCs x 12 meses")

    # 2. fechamento da energia
    lida = resultado[demanda.COLUNAS_ENE].to_numpy(dtype=float)
    conferida = resultado[demanda.COLUNAS_IMPORTADA].to_numpy(dtype=float)
    validos = (lida > 0) & np.isfinite(conferida)
    erro = np.abs(conferida[validos] - lida[validos]) / lida[validos]
    placar.checar("energia importada recalculada x ENE da BDGD",
                  bool(erro.max() < TOLERANCIA) if erro.size else True,
                  f"erro relativo maximo {erro.max():.2e} em {int(validos.sum())} pares UC-mes")

    # 3. recorte meia-aberto: o fim e exclusivo e os meses se encaixam
    codigo = max(anuais, key=lambda c: int((ucs["TIP_CC"] == c).sum()))
    mes = curvas.curva_periodo(banco, codigo, f"{args.ano}-01-01",
                               f"{args.ano}-02-01", args.passo)
    esperados = 31 * 24 * 60 // args.passo
    coberto = mes.index[-1] + pd.Timedelta(minutes=args.passo)
    placar.checar("recorte [01/01, 01/02) e janeiro fechado",
                  len(mes) == esperados and coberto == pd.Timestamp(f"{args.ano}-02-01"),
                  f"{len(mes)} blocos (esperado {esperados}), ultimo comeca em "
                  f"{mes.index[-1]:%d/%m %H:%M} e cobre ate {coberto:%d/%m %H:%M}")

    fevereiro = curvas.curva_periodo(banco, codigo, f"{args.ano}-02-01",
                                     f"{args.ano}-03-01", args.passo)
    distintos = len(mes.index.union(fevereiro.index))
    placar.checar("janeiro e fevereiro se encaixam sem repetir bloco",
                  distintos == len(mes) + len(fevereiro),
                  f"{len(mes)} + {len(fevereiro)} = {distintos} blocos distintos")

    # 4. base fixa: o recorte e o mes tem o mesmo pico
    quinzena = curvas.curva_periodo(banco, codigo, f"{args.ano}-01-01",
                                    f"{args.ano}-01-16", args.passo)
    placar.checar(f"pico do mes x pico da quinzena ({codigo})",
                  abs(float(mes.max()) - float(quinzena.max())) < 1e-12,
                  f"mes {float(mes.max()):.6f} pu, quinzena {float(quinzena.max()):.6f} pu")

    # a base e a mesma em qualquer recorte: um domingo isolado tem de sair com os
    # mesmos valores em pu que tem dentro do mes. Com a base movel do app de
    # referencia ele seria reescalado ate 1,0 pu.
    domingos = [d for d in mes.index.normalize().unique() if d.weekday() == 6]
    domingo = curvas.curva_periodo(banco, codigo, domingos[0],
                                   domingos[0] + pd.Timedelta(days=1), args.passo)
    diferenca = float(np.abs(domingo.to_numpy() - mes.loc[domingo.index].to_numpy()).max())
    placar.checar("domingo isolado na mesma base do mes", diferenca == 0.0,
                  f"diferenca maxima {diferenca:.2e} pu, pico do domingo "
                  f"{float(domingo.max()):.4f} pu")

    # uma UC que de fato consumiu em janeiro, senao a checagem passa com 0 == 0
    candidatas = resultado[(resultado["TIP_CC"] == codigo) & (resultado["DEM_MAX_01"] > 0)]
    linha = candidatas.iloc[0] if len(candidatas) else resultado[
        resultado["TIP_CC"] == codigo].iloc[0]
    demandas = linha[demanda.COLUNAS_DEMANDA].to_numpy(dtype=float)
    em_kw = demanda.curva_em_kw(banco, codigo, demandas, f"{args.ano}-01-01",
                                f"{args.ano}-01-16", args.passo)
    placar.checar("pico do recorte em kW x DEM_MAX_01",
                  abs(float(em_kw.max()) - demandas[0]) < 1e-9 * max(demandas[0], 1.0),
                  f"recorte {float(em_kw.max()):.6f} kW, DEM_MAX_01 {demandas[0]:.6f} kW")

    # 6. curva sintetica do alimentador
    tabela, _, relatorio = alimentador.sintetizar(fonte, args.ctmt, ucs, resultado, insumos)
    horas = args.passo / 60.0
    liq = tabela[[f"LIQ_{f}" for f in alimentador.FASES]].sum(axis=1)
    diferenca = float((liq - tabela["LIQ_TOTAL"]).abs().max())
    placar.checar("curva: soma das fases = total", diferenca < 1e-9,
                  f"diferenca maxima {diferenca:.1e} kW em {len(tabela)} instantes")

    carga_mes = (tabela[[f"CARGA_{f}" for f in alimentador.FASES]].sum(axis=1)
                 .groupby(tabela.index.month).sum().to_numpy() * horas)
    demandas = np.nan_to_num(resultado[demanda.COLUNAS_DEMANDA].to_numpy(dtype=float))
    esperada = np.zeros(12)
    for codigo, curva in insumos["curvas"].items():
        sel = resultado["TIP_CC"].to_numpy() == codigo
        for m, fatia in enumerate(insumos["fatias"]):
            esperada[m] += horas * demandas[sel, m].sum() * curva[fatia].sum()
    erro = float(np.abs(carga_mes - esperada).max() / max(esperada.max(), 1e-9))
    placar.checar("curva: energia mensal da carga = soma h*DEM_MAX*somaC das UCs",
                  erro < TOLERANCIA, f"erro relativo maximo {erro:.1e}")

    com_gd = np.flatnonzero(insumos["tem_gd"])
    if len(com_gd):
        uc_a_uc = sum(insumos["geracao_por_razao"][insumos["razoes"][i]] * insumos["kva"][i]
                      for i in com_gd)
        ger = tabela[[f"GER_{f}" for f in alimentador.FASES]].sum(axis=1).to_numpy()
        erro = float(np.abs(ger - uc_a_uc).max() / max(uc_a_uc.max(), 1e-9))
    else:
        erro = 0.0
    placar.checar("curva: geracao total = soma da geracao de cada UC", erro < TOLERANCIA,
                  f"erro relativo maximo {erro:.1e} em {len(com_gd)} UCs com GD")

    pesos_c, pesos_g, _ = alimentador.pesos_de_fase(
        ucs, fonte.trafos(args.ctmt), fonte.geradoras_fases(args.ctmt), insumos["tem_gd"])
    soma = max(float(np.abs(pesos_c.sum(axis=1) - 1).max()),
               float(np.abs(pesos_g.sum(axis=1) - 1).max()))
    placar.checar("curva: pesos de fase somam 1", soma < 1e-12, f"desvio maximo {soma:.1e}")

    # tudo trifasico ligado direto na MT: as tres fases tem de sair identicas
    equilibradas = ucs.assign(TABELA="UCMT", FAS_CON="ABC")
    pc, pg, _ = alimentador.pesos_de_fase(
        equilibradas, fonte.trafos(args.ctmt), fonte.geradoras_fases(args.ctmt),
        np.zeros(len(ucs), dtype=bool))
    plana = alimentador.curvas(resultado, insumos, pc, pg)
    valores = plana[[f"CARGA_{f}" for f in alimentador.FASES]].to_numpy()
    deseq = float((valores.max(axis=1) - valores.min(axis=1)).max())
    placar.checar("curva: alimentador todo trifasico sai sem desequilibrio", deseq < 1e-9,
                  f"desequilibrio maximo {deseq:.1e} kW")

    # regras de fase, em casos montados a mao
    casos = pd.DataFrame({
        "COD_ID": ["mono_B", "tri_AB", "ug_fora"], "TABELA": ["UCBT"] * 3,
        "FAS_CON": ["A", "AB", "AB"], "CEG_GD": ["", "", "GD.X"],
        "UNI_TR_MT": ["T1", "T2", "T2"],
    })
    trafos_teste = pd.DataFrame({"COD_ID": ["T1", "T2"], "FAS_CON_P": ["B", "ABC"]})
    ug_teste = pd.DataFrame({"FASES": ["C"], "ENE_INJ": [0.0]}, index=["GD.X"])
    pc, pg, rel = alimentador.pesos_de_fase(
        casos, trafos_teste, ug_teste, np.array([False, False, True]))
    certo = (np.allclose(pc[0], [0, 1, 0]) and np.allclose(pc[1], [0.5, 0.5, 0])
             and np.allclose(pg[2], [0.5, 0.5, 0]) and rel["ug_fora_da_uc"] == 1)
    placar.checar("curva: regras de fase (trafo mono, trafo tri, UG fora da UC)", certo,
                  "UC A em trafo B -> fase B; UC AB em trafo tri -> A/B; "
                  "UG C em UC AB -> AB, reportada")

    teto = aneel._corrigir_unidade(np.array([6.0, 5000.0, 89154.0, 3000.0]),
                                   np.array(["UCBT", "UCBT", "UCBT", "UCMT"]))
    placar.checar("potencia reserva da UG: trava de unidade",
                  np.allclose(teto, [6.0, 5.0, 0.0, 3000.0]),
                  "6 kW fica; 5000 em BT vira 5 kW (estava em W); 89154 em BT e descartado")
    print(f"  (curva: {relatorio['bt_primario_mono_bi']} UCs de BT pelo primario, "
          f"{relatorio['ug_fora_da_uc']} UGs fora das fases da UC, "
          f"{relatorio['ugs_sem_uc']} UGs sem UC fora da curva)")

    # 7. estatisticas: a contagem da base bate com as UCs da aba Demanda
    brutas = fonte.estatisticas()
    do_alimentador = brutas[brutas["CTMT"] == str(args.ctmt)]
    contadas = {origem: int(do_alimentador.loc[do_alimentador["TABELA"] == origem, "UCS"].sum())
                for origem in ("UCBT", "UCMT")}
    placar.checar("estatisticas: UCs contadas = UCs da aba Demanda",
                  contadas["UCBT"] == resumo["bt"] and contadas["UCMT"] == resumo["mt"],
                  f"BT {contadas['UCBT']} x {resumo['bt']}, MT {contadas['UCMT']} x {resumo['mt']}")
    geral = estatisticas.visao_geral(estatisticas.preparar(brutas[brutas["CTMT"] != ""]),
                                     fonte.alimentadores())
    soma = geral[list(estatisticas.PRINCIPAIS) + [estatisticas.OUTRAS]].sum(axis=1)
    placar.checar("estatisticas: classes somam 100% em todo alimentador",
                  bool(((soma - 100).abs() < 1e-9).all()),
                  f"{len(geral)} alimentadores, desvio maximo {float((soma - 100).abs().max()):.1e}")

    # 5. a mesma UC pelo app de referencia
    if not args.referencia:
        print("  [pulado] demanda de janeiro x app de referencia (use --referencia)")
    else:
        pasta = Path(args.referencia)
        try:
            curvas_ref = carregar_modulo("curvas_ref", pasta / "curvas.py")
            gd_ref = carregar_modulo("gd_ref", pasta / "gd.py")
        except Exception as erro:
            print(f"  [pulado] demanda de janeiro x app de referencia: {erro}")
        else:
            # o recorte que o app de referencia monta para janeiro fechado, na
            # convencao meia-aberta: a janela vai ate 01/02, exclusivo
            janela = (f"{args.ano}-01-01", f"{args.ano}-02-01")
            horaria = irradiancia.recortar(
                irradiancia.serie_ano(irradiancia.ler_csv_nasa(args.nasa)[0], args.ano),
                *janela, ancorar=True,
            )
            irrad_jan = irradiancia.recortar(
                irradiancia.reamostrar(horaria, args.passo, "pchip"), *janela
            )
            pior = 0.0
            comparadas = 0
            for i in amostra:
                codigo_uc = ucs["TIP_CC"].iloc[i]
                nossa = float(resultado["DEM_MAX_01"].iloc[i])
                if not np.isfinite(nossa) or nossa <= 0:
                    continue
                # a janela pedida, e nao os rotulos: com o fim exclusivo,
                # index[-1] e o inicio do ultimo bloco e encurtaria a carga
                carga = curvas_ref.concatenar(banco, codigo_uc, *janela, args.passo)
                unidade = gd_ref.UnidadeConsumidora(str(i), 0.22, codigo_uc,
                                                    kva=kva[i], kw=kw[i])
                geracao = gd_ref.potencia_numpy(unidade, irrad_jan, args.passo).to_numpy()
                deles = gd_ref.demanda_maxima(carga.to_numpy(), geracao,
                                              energias[i, 0], args.passo)
                pior = max(pior, abs(nossa - deles) / deles)
                comparadas += 1
            placar.checar("demanda de janeiro x app de referencia", pior < 1e-6,
                          f"erro relativo maximo {pior:.2e} em {comparadas} UCs com GD")

    print(f"\nTempo total: {time.perf_counter() - inicio:.1f}s")
    if placar.falhas:
        sys.exit(f"{placar.falhas} checagem(ns) falharam.")
    print("Todas as checagens passaram.")


if __name__ == "__main__":
    main()
