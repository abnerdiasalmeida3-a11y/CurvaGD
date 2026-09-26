"""Curva sintetica anual do alimentador, por fase, e indicadores de desequilibrio.

Soma, instante a instante, a carga e a geracao de todas as UCs do alimentador, ja
calculadas por `demanda.calcular`, e reparte cada uma entre as fases A, B e C. Nao
ha perdas nem fluxo de potencia: e o balanco idealizado entre o que as UCs
consomem e o que as suas GDs geram.

A atribuicao de fase tem uma armadilha. A letra de fase de uma UC de baixa tensao
e relativa ao SECUNDARIO do transformador, nao ao alimentador: na base da
Energisa MT, 25 mil trafos tem primario na fase B e secundario rotulado "AN". Uma
UC marcada "A" ligada a um deles carrega a fase B do alimentador. Repartir pela
letra da UC chega a inverter a conclusao -- num alimentador testado, a fase B
saia com 1,2% da energia pela letra da UC e 63,8% pelo primario do trafo.

Por isso as regras sao:

  carga  UCMT              -> as fases do proprio FAS_CON (esta ligada na MT)
         UCBT, trafo mono  -> as fases do primario do trafo (FAS_CON_P)
              ou bifasico
         UCBT, trafo       -> as letras da UC; aproximacao, porque num trafo Dyn
              trifasico       uma carga monofasica do secundario puxa corrente de
                              duas fases do primario
         UCBT sem trafo    -> as letras da UC, e conta no relatorio

  geracao                  -> as fases das UGs do mesmo CEG_GD, desde que sejam
                              subconjunto das fases da UC; senao, as da UC, e
                              conta no relatorio. Depois passa pela mesma
                              projecao do trafo que a carga.

Dentro das fases escolhidas a potencia se divide em partes iguais.

UGs que nao pertencem a nenhuma UC do alimentador (uma PCH, por exemplo) ficam
de fora da curva e so aparecem no relatorio.
"""

import numpy as np
import pandas as pd

from . import demanda

FASES = ("A", "B", "C")
# abaixo disto a media das tres fases e pequena demais para o indicador relativo
# significar alguma coisa -- perto do cruzamento de zero da curva liquida ele
# explodiria por divisao por quase nada
LIMIAR_RELATIVO = 0.05


def fases(codigo):
    """Letras de fase de um codigo FAS_CON, na ordem A, B, C; o neutro nao conta."""
    texto = str(codigo).upper()
    return [f for f in FASES if f in texto]


def _vetor(letras):
    """Pesos iguais nas fases dadas, somando 1."""
    v = np.zeros(3)
    for f in letras:
        v[FASES.index(f)] = 1.0
    return v / v.sum()


# ============================================================ fases de cada UC


def pesos_de_fase(ucs, trafos, geradoras_fases, com_gd):
    """Pesos de fase da carga e da geracao de cada UC, e o relatorio das regras.

    `ucs` vem de `bdgd.unidades`, `trafos` de `bdgd.trafos`, `geradoras_fases` de
    `bdgd.geradoras_fases` e `com_gd` e a mascara das UCs com geracao simulada.
    Devolve (pesos_carga, pesos_geracao, relatorio); cada peso e (n_uc, 3), e
    cada linha soma 1.
    """
    primario = dict(zip(trafos["COD_ID"], trafos["FAS_CON_P"]))
    fases_ug = geradoras_fases["FASES"].to_dict() if len(geradoras_fases) else {}

    contagem = dict.fromkeys(
        ["mt_direto", "bt_primario_mono_bi", "bt_trafo_trifasico", "bt_sem_trafo",
         "trafo_sem_fase", "uc_sem_fase", "ug_da_propria_ug", "ug_fora_da_uc",
         "ug_sem_registro"], 0)
    excecoes = []

    def projetar(letras, linha, contar):
        """Leva as letras do lado da UC para as fases do alimentador."""
        if linha.TABELA == "UCMT":
            if contar:
                contagem["mt_direto"] += 1
            return letras
        codigo_primario = primario.get(linha.UNI_TR_MT)
        if codigo_primario is None:
            if contar:
                contagem["bt_sem_trafo"] += 1
                excecoes.append((linha.COD_ID, linha.TABELA, linha.CEG_GD,
                                 "trafo nao encontrado: usa a letra da UC"))
            return letras
        do_primario = fases(codigo_primario)
        if not do_primario:
            if contar:
                contagem["trafo_sem_fase"] += 1
            return letras
        if len(do_primario) == 3:
            if contar:
                contagem["bt_trafo_trifasico"] += 1
            return letras
        if contar:
            contagem["bt_primario_mono_bi"] += 1
        return do_primario

    n = len(ucs)
    carga = np.zeros((n, 3))
    geracao = np.zeros((n, 3))
    for i, linha in enumerate(ucs.itertuples(index=False)):
        da_uc = fases(linha.FAS_CON)
        if not da_uc:
            contagem["uc_sem_fase"] += 1
            da_uc = list(FASES)
        carga[i] = _vetor(projetar(da_uc, linha, contar=True))

        if not com_gd[i]:
            geracao[i] = carga[i]           # nao entra na conta: kva e zero
            continue
        registradas = fases(fases_ug.get(linha.CEG_GD, ""))
        if not registradas:
            contagem["ug_sem_registro"] += 1
            excecoes.append((linha.COD_ID, linha.TABELA, linha.CEG_GD,
                             "UG sem registro de fase: usa as fases da UC"))
            da_geracao = da_uc
        elif not set(registradas) <= set(da_uc):
            contagem["ug_fora_da_uc"] += 1
            excecoes.append((linha.COD_ID, linha.TABELA, linha.CEG_GD,
                             f"UG em {''.join(registradas)} fora das fases da UC "
                             f"({''.join(da_uc)}): usa as fases da UC"))
            da_geracao = da_uc
        else:
            contagem["ug_da_propria_ug"] += 1
            da_geracao = registradas
        geracao[i] = _vetor(projetar(da_geracao, linha, contar=False))

    # UGs do alimentador que nao pertencem a nenhuma UC dele ficam fora da curva
    cegs_das_ucs = set(ucs["CEG_GD"]) - {""}
    orfas = geradoras_fases[~geradoras_fases.index.isin(cegs_das_ucs)] \
        if len(geradoras_fases) else geradoras_fases
    relatorio = {
        **contagem,
        "ugs_sem_uc": int(len(orfas)),
        "ene_ugs_sem_uc_kwh": float(orfas["ENE_INJ"].sum()) if len(orfas) else 0.0,
        "excecoes": pd.DataFrame(excecoes, columns=["COD_ID", "TABELA", "CEG_GD", "MOTIVO"]),
    }
    return carga, geracao, relatorio


# ============================================================ curvas


def curvas(resultado, insumos, pesos_carga, pesos_geracao):
    """Curvas anuais por fase: carga, geracao e liquido (carga - geracao), em kW.

    Nada e feito UC a UC ao longo do tempo. A carga de uma fase e a soma, sobre
    os codigos de curva, de `curva_pu[codigo] x peso`, onde o peso de cada mes
    junta `peso_fase x DEM_MAX_mes` de todas as UCs daquele codigo; a geracao e
    a soma, sobre as razoes kW/kVA, de `curva_kW_por_kVA x soma(kva x peso_fase)`.
    Sao umas dezenas de produtos de vetores de 35 mil pontos.
    """
    indice = insumos["indice"]
    mes = indice.month.to_numpy() - 1
    codigos = resultado["TIP_CC"].to_numpy()
    demandas = np.nan_to_num(resultado[demanda.COLUNAS_DEMANDA].to_numpy(dtype=float))

    carga = np.zeros((len(indice), 3))
    for codigo, curva in insumos["curvas"].items():
        sel = codigos == codigo
        if not sel.any():
            continue
        peso_mensal = pesos_carga[sel].T @ demandas[sel]          # (3 fases, 12 meses)
        carga += curva[:, None] * peso_mensal[:, mes].T

    geracao = np.zeros((len(indice), 3))
    razoes, kva, tem_gd = insumos["razoes"], insumos["kva"], insumos["tem_gd"]
    for razao, curva in insumos["geracao_por_razao"].items():
        sel = tem_gd & (razoes == razao)
        peso = (kva[sel, None] * pesos_geracao[sel]).sum(axis=0)  # kVA por fase
        geracao += curva[:, None] * peso

    liquido = carga - geracao
    tabela = pd.DataFrame(index=indice)
    tabela.index.name = "INSTANTE"
    for prefixo, valores in (("CARGA", carga), ("GER", geracao), ("LIQ", liquido)):
        for j, f in enumerate(FASES):
            tabela[f"{prefixo}_{f}"] = valores[:, j]
    tabela["LIQ_TOTAL"] = liquido.sum(axis=1)
    return tabela


# ============================================================ indicadores


def _indicadores_de(valores, horas):
    """Indicadores de uma curva de tres fases (n, 3)."""
    absoluto = valores.max(axis=1) - valores.min(axis=1)

    # relativo, a moda NEMA, sobre magnitudes: a corrente de cada fase acompanha
    # |P|, qualquer que seja o sentido do fluxo
    modulo = np.abs(valores)
    media = modulo.mean(axis=1)
    validos = media >= LIMIAR_RELATIVO * media.max() if media.max() > 0 else media > 0
    relativo = np.full(len(valores), np.nan)
    relativo[validos] = (np.abs(modulo[validos] - media[validos, None]).max(axis=1)
                         / media[validos])

    energia = horas * valores.sum(axis=0)                           # kWh por fase
    total = valores.sum(axis=1)
    linha = {
        "DESEQ_MAX_kW": float(absoluto.max()),
        "DESEQ_MEDIO_kW": float(absoluto.mean()),
        "DESEQ_P95_kW": float(np.percentile(absoluto, 95)),
        "DESEQ_REL_MAX_%": float(100 * np.nanmax(relativo)) if validos.any() else np.nan,
        "DESEQ_REL_MEDIO_%": float(100 * np.nanmean(relativo)) if validos.any() else np.nan,
        "DESEQ_REL_P95_%": float(100 * np.nanpercentile(relativo, 95)) if validos.any() else np.nan,
        "INSTANTES_DESCARTADOS_%": float(100 * (1 - validos.mean())),
        "PICO_TOTAL_kW": float(total.max()),
        "VALE_TOTAL_kW": float(total.min()),
    }
    for j, f in enumerate(FASES):
        linha[f"ENERGIA_{f}_MWh"] = float(energia[j] / 1e3)
    # participacao so faz sentido quando nenhuma fase tem saldo negativo no ano
    positiva = (energia > 0).all()
    for j, f in enumerate(FASES):
        linha[f"PARTICIPACAO_{f}_%"] = float(100 * energia[j] / energia.sum()) if positiva else np.nan
    return linha


def indicadores(tabela, passo_minutos):
    """Indicadores de desequilibrio da curva liquida e da curva so de carga.

    `DESEQ_*_kW` e a diferenca entre a fase mais carregada e a menos carregada
    em cada instante; `DESEQ_REL_*` e o desvio maximo das fases em relacao a
    media delas, sobre a media (definicao da NEMA), descartando os instantes em
    que a media e menor que 5% do seu maximo anual. O P95 e o mais util para
    ordenar alimentadores: ignora os 5% de instantes mais extremos.
    """
    horas = passo_minutos / 60.0
    linhas = {}
    for nome, prefixo in (("LIQUIDA", "LIQ"), ("CARGA", "CARGA")):
        valores = tabela[[f"{prefixo}_{f}" for f in FASES]].to_numpy()
        linhas[nome] = _indicadores_de(valores, horas)
    return pd.DataFrame(linhas).T.rename_axis("CURVA")


# ============================================================ tudo junto


def sintetizar(fonte, ctmt, ucs, resultado, insumos):
    """Curvas, indicadores e relatorio de um alimentador ja calculado.

    `fonte` e a BDGD aberta por `bdgd.abrir`; `ucs` e `resultado` vem de
    `bdgd.unidades` e `demanda.calcular`, e `insumos` do mesmo `calcular` com
    `devolver_insumos=True`.
    """
    com_gd = insumos["tem_gd"]
    carga, geracao, relatorio = pesos_de_fase(
        ucs, fonte.trafos(ctmt), fonte.geradoras_fases(ctmt), com_gd
    )
    tabela = curvas(resultado, insumos, carga, geracao)
    return tabela, indicadores(tabela, insumos["passo_minutos"]), relatorio

