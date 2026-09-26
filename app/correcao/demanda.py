"""Demanda maxima mes a mes de cada UC de um alimentador.

A BDGD da a energia mensal (ENE_01..12) e nao a demanda. Aqui a demanda sai
invertendo, mes a mes, a energia que a UC puxou da rede:

    ENE_mes = h * soma( max(Dmax * C - G, 0) )

com C a curva de carga em pu do mes, G a geracao fotovoltaica em kW e h o passo
em horas. Sem geracao a conta fecha direto, Dmax = ENE / (h * somaC); com
geracao, `gd.demanda_maxima_exata` resolve a linear por partes.

Duas escolhas fazem o alimentador inteiro caber em segundos:

  - a curva de cada codigo e montada uma vez para o ano e fatiada por mes -- os
    meses sao blocos contiguos do indice, entao a fatia e uma vista, sem copia;
  - a geracao e simulada por razao kW/kVA, nao por UC: `gd.geracao_por_kva`
    explica por que a potencia por kVA so depende dessa razao. A razao entra na
    chave do cache exatamente como e -- arredonda-la juntaria mais UCs por
    simulacao, mas desviaria a demanda de quem nao caisse no valor redondo.
"""

import time

import numpy as np

from . import curvas
from ..calculo import gd

MESES = range(1, 13)
COLUNAS_ENE = [f"ENE_{i:02d}" for i in MESES]
COLUNAS_DEMANDA = [f"DEM_MAX_{i:02d}" for i in MESES]
COLUNAS_IMPORTADA = [f"ENE_IMP_CONF_{i:02d}" for i in MESES]
COLUNAS_EXPORTADA = [f"ENE_EXP_SIM_{i:02d}" for i in MESES]
COLUNAS_IDENTIFICACAO = ["COD_ID", "TABELA", "MUN", "TIP_CC", "GRU_TEN", "CEG_GD"]

STATUS_SEM_CURVA = "SEM_CURVA"


def _fatias_mensais(indice):
    """Um slice por mes.

    O indice do ano e contiguo e ordenado, entao cada mes e um bloco: fatiar por
    posicao evita montar 12 mascaras booleanas para cada UC.
    """
    meses = indice.month.to_numpy()
    bordas = np.searchsorted(meses, np.arange(1, 14))
    return [slice(int(bordas[m - 1]), int(bordas[m])) for m in MESES]


def _alinhar_irradiancia(serie_irrad, indice):
    """Poe a irradiancia no mesmo calendario da curva de carga.

    A serie da NASA e do ano civil e a curva tambem, mas ano bissexto, lacuna no
    CSV ou passo diferente fazem os dois divergirem; reindexar deixa o resto do
    modulo trabalhar com arrays do mesmo tamanho.
    """
    if serie_irrad.index.equals(indice):
        return serie_irrad
    return serie_irrad.reindex(indice, fill_value=0.0)


def _curvas_do_alimentador(banco, codigos_usados, ano, passo_minutos):
    """Curva anual em pu de cada codigo presente, com a base da normalizacao."""
    montadas = {}
    for codigo in codigos_usados:
        serie, base = curvas.curva_ano(banco, codigo, ano, passo_minutos)
        montadas[codigo] = (serie.to_numpy(dtype=float), base, serie.index)
    return montadas


def _geracoes_por_razao(razoes, serie_irrad, passo_minutos,
                        performance_ratio, cut_in, progresso):
    """Uma curva de kW por kVA para cada razao kW/kVA distinta do alimentador."""
    cache = {}
    total = len(razoes)
    for feito, razao in enumerate(sorted(razoes), start=1):
        cache[razao] = gd.geracao_por_kva(
            razao, serie_irrad, passo_minutos,
            performance_ratio, cut_in,
        ).to_numpy(dtype=float)
        if progresso is not None:
            progresso("geracao", feito, total)
    return cache


def calcular(ucs, potencias, banco, serie_irrad, ano, passo_minutos=curvas.PASSO_NATIVO,
             performance_ratio=gd.PERFORMANCE_RATIO_PADRAO,
             cut_in=gd.CUTIN_PCT_PADRAO, diagnostico=False, progresso=None,
             devolver_insumos=False):
    """Tabela de demanda maxima mensal das UCs.

    `ucs` vem de `bdgd.unidades` e `potencias` de `aneel.potencias_por_uc`; os
    dois compartilham o indice. Devolve (DataFrame, tempos por etapa).

    Com `devolver_insumos`, devolve tambem o que foi montado no caminho -- as
    curvas anuais em pu, as fatias mensais e a geracao por razao kW/kVA -- para
    quem precisar remontar a curva do alimentador sem refazer a simulacao.
    """
    marcas = {}
    inicio = time.perf_counter()
    horas = passo_minutos / 60.0

    usados = sorted(set(ucs["TIP_CC"]) & set(banco))
    if not usados:
        raise ValueError("Nenhuma UC do alimentador tem curva de carga conhecida na CRVCRG.")
    montadas = _curvas_do_alimentador(banco, usados, ano, passo_minutos)

    indice = montadas[usados[0]][2]
    fatias = _fatias_mensais(indice)
    serie_irrad = _alinhar_irradiancia(serie_irrad, indice)
    marcas["curvas"] = time.perf_counter() - inicio

    kw = potencias["POT_MODULOS"].to_numpy(dtype=float)
    kva = potencias["POT_INVERSOR"].to_numpy(dtype=float)
    tem_curva = ucs["TIP_CC"].isin(montadas).to_numpy()
    tem_gd = (kw > 0) & (kva > 0) & tem_curva
    razoes = np.zeros(len(ucs))
    razoes[tem_gd] = kw[tem_gd] / kva[tem_gd]

    parcial = time.perf_counter()
    cache_geracao = _geracoes_por_razao(
        set(razoes[tem_gd]), serie_irrad, passo_minutos,
        performance_ratio, cut_in, progresso,
    )
    marcas["geracao"] = time.perf_counter() - parcial

    parcial = time.perf_counter()
    energia = ucs[COLUNAS_ENE].to_numpy(dtype=float)
    demanda = np.full((len(ucs), 12), np.nan)
    importada = np.full((len(ucs), 12), np.nan)
    exportada = np.full((len(ucs), 12), np.nan)

    # ---- sem geracao: a inversao e fechada, entao vale de uma vez para todas ----
    soma_por_codigo = {
        codigo: np.array([float(curva[fatia].sum()) for fatia in fatias])
        for codigo, (curva, _, _) in montadas.items()
    }
    sem_gd = tem_curva & ~tem_gd
    if sem_gd.any():
        somas = np.vstack([soma_por_codigo[c] for c in ucs["TIP_CC"].to_numpy()[sem_gd]])
        energia_sem = energia[sem_gd]
        with np.errstate(divide="ignore", invalid="ignore"):
            direta = np.where(somas > 0, energia_sem / (horas * somas), 0.0)
        demanda[sem_gd] = np.where(energia_sem > 0, direta, 0.0)
        if diagnostico:
            importada[sem_gd] = horas * demanda[sem_gd] * somas
            exportada[sem_gd] = 0.0

    # ---- com geracao: uma inversao exata por UC e mes ----
    indices_gd = np.flatnonzero(tem_gd)
    codigos_uc = ucs["TIP_CC"].to_numpy()
    for contador, i in enumerate(indices_gd, start=1):
        curva = montadas[codigos_uc[i]][0]
        geracao_uc = cache_geracao[razoes[i]] * kva[i]
        for m, fatia in enumerate(fatias):
            carga_mes = curva[fatia]
            geracao_mes = geracao_uc[fatia]
            valor = gd.demanda_maxima_exata(carga_mes, geracao_mes, energia[i, m], passo_minutos)
            demanda[i, m] = valor
            if diagnostico:
                saldo = valor * carga_mes - geracao_mes
                importada[i, m] = horas * np.maximum(saldo, 0.0).sum()
                exportada[i, m] = horas * np.maximum(-saldo, 0.0).sum()
        if progresso is not None and (contador % 50 == 0 or contador == len(indices_gd)):
            progresso("demanda", contador, len(indices_gd))
    marcas["demanda"] = time.perf_counter() - parcial

    resultado = ucs[COLUNAS_IDENTIFICACAO].copy()
    resultado["POT_MODULOS"] = kw
    resultado["POT_INVERSOR"] = kva
    resultado["STATUS"] = np.where(tem_curva, potencias["STATUS"].to_numpy(), STATUS_SEM_CURVA)
    resultado[COLUNAS_DEMANDA] = demanda
    if diagnostico:
        resultado[COLUNAS_ENE] = energia
        resultado[COLUNAS_IMPORTADA] = importada
        resultado[COLUNAS_EXPORTADA] = exportada

    marcas["total"] = time.perf_counter() - inicio
    resultado = resultado.reset_index(drop=True)
    if not devolver_insumos:
        return resultado, marcas
    insumos = {
        "indice": indice,
        "fatias": fatias,
        "curvas": {codigo: curva for codigo, (curva, _, _) in montadas.items()},
        "geracao_por_razao": cache_geracao,
        "razoes": razoes,
        "kva": kva,
        "tem_gd": tem_gd,
        "passo_minutos": passo_minutos,
    }
    return resultado, marcas, insumos


def curva_em_kw(banco, codigo, demandas_mensais, inicio, fim, passo_minutos=curvas.PASSO_NATIVO):
    """Carga em kW de um recorte qualquer: a curva em pu vezes a demanda do mes.

    Cada ponto e escalado pela demanda do seu proprio mes, entao um recorte que
    atravessa a virada do mes tambem sai certo.
    """
    pu = curvas.curva_periodo(banco, codigo, inicio, fim, passo_minutos)
    fator = np.asarray(demandas_mensais, dtype=float)[pu.index.month.to_numpy() - 1]
    return pu * fator
