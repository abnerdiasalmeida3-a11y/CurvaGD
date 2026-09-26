"""Casos de uso das curvas de uma UC, pelo metodo da CORRECAO_DEMANDA_BDGD.

Para cada UC:

1. curva de carga do ano em pu, pelos perfis DU/SA/DO da CRVCRG encaixados no
   dia da semana e normalizados pelo maximo do ano (`app.calculo.curvas`);
2. se a UC tem GD, a geracao do ano calculada pelo modelo PVSystem com a irradiancia
   NASA reamostrada para 15 min (`app.calculo.gd`); potencias pela ANEEL, com
   reserva no POT_INST da UG (`app.calculo.potencias`);
3. mes a mes, a demanda maxima que reproduz o ENE da UC como energia importada:
   ENE = h * soma(max(Dmax*C - G, 0)) (`app.calculo.motor`).
"""
from __future__ import annotations

import calendar
from functools import lru_cache
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

import numpy as np

from ..calculo import curvas as curvas_calc
from ..calculo import gd as gd_calc
from ..calculo import motor, potencias
from ..core.errors import ErroBDGD
from ..data.repositories.bdgd_complemento import potencias_ug_por_ceg
from ..data.repositories.curva import get_curve
from ..data.repositories.gd import _chave, buscar_equipamento_aneel
from ..data.repositories.uc import UCRepository
from ..domain.agregacao import comparar_demanda

METODO = "CORRECAO_DEMANDA_BDGD"


@lru_cache(maxsize=48)
def _timestamps_mes(ano: int, mes: int) -> tuple[datetime, ...]:
    fuso = timezone(timedelta(hours=-4))
    inicio = datetime(ano, mes, 1, tzinfo=fuso)
    quantidade = calendar.monthrange(ano, mes)[1] * 96
    return tuple(inicio + timedelta(minutes=15 * i) for i in range(quantidade))


def _ano_da_versao(catalog, import_id):
    manifest = catalog.manifest(import_id) or {}
    return int(manifest.get("ano_base") or manifest.get("ano") or 0), manifest


def _gerar_ano(gerar_mes, token=None):
    """Cada mes tem a sua demanda; o ano concatena os meses em ordem temporal."""
    meses = []
    for mes in range(1, 13):
        if token:
            token.verificar()
        try:
            meses.append(gerar_mes(mes))
        except ErroBDGD as exc:
            raise ErroBDGD(exc.codigo, f"Mes {mes:02d}: {exc}", codigos=exc.codigos) from exc
    resultado = dict(meses[0])
    resultado.update(
        mes=0,
        titulo=f"{meses[0]['titulo'].rsplit(' - ', 1)[0]} - Ano completo {meses[0]['ano']}",
        timestamps=tuple(t for parte in meses for t in parte["timestamps"]),
        tipos_dia=tuple(rotulo for parte in meses for rotulo in parte["tipos_dia"]),
        series=_concatenar_series([parte["series"] for parte in meses]),
        resultados_mensais=tuple(meses),
        hipoteses=tuple(dict.fromkeys(h for parte in meses for h in parte["hipoteses"])),
        balanco=None,
        demandas_max_kw=tuple(parte["demanda_max_kw"] for parte in meses),
    )
    resultado["cenarios"] = {"importada": {"series": resultado["series"], "mensais": tuple(
        parte["cenarios"]["importada"] for parte in meses)}}
    if resultado.get("irradiancia"):
        resultado["irradiancia"] = {
            **resultado["irradiancia"],
            "series": _concatenar_series([parte["irradiancia"]["series"] for parte in meses]),
        }
    return resultado


def _concatenar_series(partes):
    series = {nome: np.concatenate([parte[nome] for parte in partes]) for nome in partes[0]}
    for vetor in series.values():
        vetor.setflags(write=False)
    return series


def series_do_balanco(balanco):
    return {"Carga sem GD (kW)": balanco.carga_kw,
            "Geração da GD (kW)": balanco.geracao_kw,
            "Curva unificada - carga menos GD (kW)": balanco.curva_liquida_kw,
            "Importação da rede (kW)": balanco.importacao_kw,
            "Exportação para a rede (kW)": balanco.exportacao_kw,
            "Autoconsumo (kW)": balanco.autoconsumo_kw}


def _aneel_da_uc(workspace, codigo_gd, dados_aneel_path):
    """Potencias da ANEEL para um CEG: arquivo escolhido ou copia local importada."""
    try:
        if dados_aneel_path:
            equipamento = buscar_equipamento_aneel(Path(dados_aneel_path), codigo_gd)
        else:
            cache = Path(workspace) / "solar_aneel" / "equipamentos.parquet"
            if not cache.is_file():
                return None, ""
            from .aneel_solar import _consultar
            equipamento = _consultar(cache, codigo_gd)
    except ErroBDGD:
        return None, ""
    return (equipamento.potencia_modulos_kwp, equipamento.potencia_inversor_kw), equipamento.fonte


def preparar_uc(catalog, import_id, row, ano, *, irradiancia_path="", dados_aneel_path="",
                referencia_potencias="", potencia_modulos_kwp=None, potencia_inversor_kw=None,
                fonte_potencia="", performance_ratio=gd_calc.PERFORMANCE_RATIO_PADRAO,
                cut_in_percentual=gd_calc.CUTIN_PCT_PADRAO, razao_kw_kva=potencias.RAZAO_PADRAO,
                aneel_em_lote=None, pot_ugs=None, curvas_cache=None):
    """Tudo o que vale para o ano inteiro da UC: curva em pu, potencias e geracao.

    `aneel_em_lote` ({chave CEG: (kW, kVA, linhas)}), `pot_ugs` e `curvas_cache`
    vem do tratamento em lote, para nao repetir leituras por UC.
    """
    tipo = row.get("tipo_curva")
    if curvas_cache is not None and tipo in curvas_cache:
        curva_ano = curvas_cache[tipo]
    else:
        comportamentos = get_curve(catalog, import_id, tipo)
        if comportamentos is None:
            raise ErroBDGD("TIPOLOGIA_INCOMPLETA", "A tipologia da UC nao possui curvas validas para DU, SA e DO.")
        curva_ano = curvas_calc.curva_ano(comportamentos, ano)
        curva_ano.setflags(write=False)
        if curvas_cache is not None:
            curvas_cache[tipo] = curva_ano

    codigo_gd = str(row.get("codigo_gd") or "").strip()
    hipoteses = [
        "Carga: perfis DU/SA/DO da CRVCRG encaixados pelo dia da semana (feriado segue o seu dia da semana), "
        "em pu do máximo do ano — método da CORRECAO_DEMANDA_BDGD.",
        "Demanda: DEM_MAX de cada mês resolve ENE = h·Σ max(DEM_MAX·C − G, 0), com o ENE como energia importada da rede.",
    ]
    aneel, fonte_aneel = None, ""
    informadas = None
    if codigo_gd:
        if (potencia_modulos_kwp or 0) > 0 and (potencia_inversor_kw or 0) > 0:
            par = (float(potencia_modulos_kwp), float(potencia_inversor_kw))
            if fonte_potencia == "aneel":
                aneel, fonte_aneel = par, referencia_potencias
            else:
                informadas = par
        elif aneel_em_lote is not None:
            achado = aneel_em_lote.get(_chave(codigo_gd))
            if achado:
                aneel = achado[:2]
                fonte_aneel = f"ANEEL · soma de {achado[2]} linha(s) do CEG GD"
        else:
            aneel, fonte_aneel = _aneel_da_uc(catalog.workspace, codigo_gd, dados_aneel_path)
        if pot_ugs is None and not (aneel or informadas):
            manifest = catalog.manifest(import_id) or {}
            pot_ugs = potencias_ug_por_ceg(UCRepository(catalog, import_id).directory, manifest.get("source"))
    kw, kva, status = potencias.resolver(
        codigo_gd, row.get("entidade"), aneel=aneel, informadas=informadas,
        pot_ug_kw=(pot_ugs or {}).get(codigo_gd), razao_padrao=razao_kw_kva)
    fonte_equipamento = {
        potencias.STATUS_ANEEL: fonte_aneel or "ANEEL",
        potencias.STATUS_INFORMADA: referencia_potencias or "potências informadas na tela",
    }.get(status, potencias.DESCRICAO_STATUS[status])
    if status == potencias.STATUS_UGBT:
        hipoteses.append(f"GD fora da base da ANEEL: POT_INST das UGs = {kva:g} kVA de inversor e "
                         f"{kw:g} kW de módulos pela razão kW/kVA {razao_kw_kva:g}.")
    elif status == potencias.STATUS_SEM_POTENCIA:
        hipoteses.append(f"CEG GD {codigo_gd} sem potência na ANEEL nem nas UGs: a UC foi tratada sem geração.")

    irrad = None
    geracao = None
    if kw > 0 and kva > 0:
        caminho = irradiancia_path
        if not caminho:
            raise ErroBDGD("IRRADIANCIA_INVALIDA", "Selecione o CSV horário de irradiância da NASA POWER.")
        irrad = motor.irradiancia_ano(caminho, ano)
        geracao = motor.geracao_ano(irrad, kw, kva, performance_ratio, cut_in_percentual)
        hipoteses.append(
            "Geração: modelo PVSystem vetorizado — irradiância NASA em kW/m² reamostrada para 15 min por PCHIP, "
            f"correção por temperatura com o dia típico de temperatura, eficiência do inversor, cut-in de {cut_in_percentual:g}% "
            f"e irradiância-base {performance_ratio:g}.")
    energias = [row.get(f"energia_mes_{m:02d}") for m in range(1, 13)]
    invalidos = [m for m, v in enumerate(energias, 1) if motor.energia_do_mes(v) != v or (isinstance(v, (int, float)) and v < 0)]
    if invalidos:
        hipoteses.append("ENE ausente, inválido ou negativo em " + ", ".join(f"{m:02d}" for m in invalidos)
                         + ": demanda zero no mês, como na CORRECAO_DEMANDA_BDGD.")
    return {
        "row": row, "ano": ano, "curva_ano": curva_ano, "fatias": curvas_calc.fatias_mensais(ano),
        "codigo_gd": codigo_gd, "kw": kw, "kva": kva, "status_gd": status, "fonte_equipamento": fonte_equipamento,
        "irradiancia": irrad, "geracao_ano": geracao, "energias": energias, "hipoteses": tuple(hipoteses),
        "parametros_gd": {"performance_ratio": performance_ratio, "cut_in_percentual": cut_in_percentual,
                          "razao_kw_kva": razao_kw_kva},
    }


def resultado_mes(contexto, mes):
    """Demanda, curvas e balanco de um mes da UC, no formato usado pela interface."""
    row, ano = contexto["row"], contexto["ano"]
    fatia = contexto["fatias"][mes - 1]
    carga_pu = contexto["curva_ano"][fatia]
    geracao = contexto["geracao_ano"][fatia] if contexto["geracao_ano"] is not None else None
    energia = motor.energia_do_mes(row.get(f"energia_mes_{mes:02d}"))
    balanco = motor.balanco_mes(carga_pu, geracao, energia)
    demanda_bdgd = row.get(f"demanda_mes_{mes:02d}")
    try:
        comparacao = comparar_demanda(balanco, demanda_bdgd) if isinstance(demanda_bdgd, (int, float)) else None
    except ErroBDGD:
        comparacao = None
    series = series_do_balanco(balanco)
    energias = contexto["energias"]
    irrad = contexto["irradiancia"]
    return {
        "tipo": "uc",
        "metodo": METODO,
        "titulo": f"UC {row.get('id_uc')} - {mes:02d}/{ano}",
        "ano": ano,
        "mes": mes,
        "timestamps": _timestamps_mes(ano, mes),
        "tipos_dia": curvas_calc.rotulos_mes(ano, mes),
        "municipio_calendario": row.get("municipio") or "",
        "series": series,
        "cenarios": {"importada": {"balanco": balanco, "ajuste": None, "series": series, "demanda": comparacao}},
        "interpretacao_ene": "importada",
        "cenario_principal": "importada",
        "modelo_solar": "PVSystem vetorizado (irradiância NASA)" if irrad is not None else "",
        "balanco": balanco,
        "demanda_max_kw": balanco.demanda_kw,
        "uc": row,
        "energias_mensais_kwh": energias,
        "energia_anual_kwh": float(sum(x for x in energias if isinstance(x, (int, float)) and math.isfinite(x) and x >= 0)),
        "codigo_gd": contexto["codigo_gd"],
        "potencias_gd": {"modulos_kw": contexto["kw"], "inversor_kva": contexto["kva"], "status": contexto["status_gd"],
                         **contexto["parametros_gd"]},
        "irradiancia": {
            "series": {"GHI - horizontal (W/m²)": np.asarray(irrad.serie.to_numpy()[fatia] * 1000.0, dtype=np.float64)},
            "arquivo": irrad.arquivo,
            "latitude": irrad.latitude,
            "longitude": irrad.longitude,
        } if irrad is not None else None,
        "fonte_equipamento": contexto["fonte_equipamento"],
        "hipoteses": contexto["hipoteses"],
    }


def gerar_curva_uc(catalog, import_id: str, entidade: str, linha_origem: int, mes: int, *, token=None,
                   irradiancia_path: str = "", dados_aneel_path: str = "", referencia_potencias: str = "",
                   potencia_modulos_kwp: float | None = None, potencia_inversor_kw: float | None = None,
                   fonte_potencia: str = "", performance_ratio: float = gd_calc.PERFORMANCE_RATIO_PADRAO,
                   cut_in_percentual: float = gd_calc.CUTIN_PCT_PADRAO,
                   razao_kw_kva: float = potencias.RAZAO_PADRAO, **_parametros_antigos):
    """Curvas de uma UC num mes (1-12) ou no ano completo (0).

    Parametros de tratamentos antigos (inclinacao, perdas, calibracao...) sao
    aceitos e ignorados: o metodo atual nao usa esses dados.
    """
    if type(mes) is not int or mes not in range(13):
        raise ValueError("Mes invalido")
    for nome, valor, minimo in (("Irradiância-base", performance_ratio, 0.0), ("Cut-in", cut_in_percentual, 0.0),
                                ("Razão kW/kVA", razao_kw_kva, 0.0)):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor < minimo \
                or (nome != "Cut-in" and valor == 0):
            raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"{nome} inválido.")
    repo = UCRepository(catalog, import_id)
    row = repo.detalhe_consolidado(entidade, linha_origem, token)
    if not row:
        raise ErroBDGD("REFERENCIA_ORFA", "A UC selecionada nao foi encontrada.")
    ano, _ = _ano_da_versao(catalog, import_id)
    if token:
        token.verificar()
    contexto = preparar_uc(
        catalog, import_id, row, ano, irradiancia_path=irradiancia_path, dados_aneel_path=dados_aneel_path,
        referencia_potencias=referencia_potencias, potencia_modulos_kwp=potencia_modulos_kwp,
        potencia_inversor_kw=potencia_inversor_kw, fonte_potencia=fonte_potencia,
        performance_ratio=float(performance_ratio), cut_in_percentual=float(cut_in_percentual),
        razao_kw_kva=float(razao_kw_kva))
    if mes == 0:
        return _gerar_ano(lambda numero: resultado_mes(contexto, numero), token)
    return resultado_mes(contexto, mes)
