"""Motor mensal da demanda maxima de uma UC, como na CORRECAO_DEMANDA_BDGD.

A BDGD da a energia mensal (ENE_01..12), e nao a demanda. A demanda sai
invertendo, mes a mes, a energia que a UC puxou da rede:

    ENE_mes = h * soma( max(Dmax * C - G, 0) )

com C a curva de carga em pu do mes (base: maximo do ano), G a geracao
fotovoltaica em kW e h o passo em horas. Sem geracao a conta fecha direto,
Dmax = ENE / (h * somaC); com geracao, `gd.demanda_maxima_exata` resolve a
funcao linear por partes.

A irradiancia e reamostrada para 15 min no ANO inteiro (PCHIP sobre a energia
acumulada precisa dos blocos vizinhos) e so depois fatiada por mes, e a geracao
e simulada por razao kW/kVA -- as duas coisas ficam em cache, entao tratar um
alimentador inteiro nao repete a simulacao.
"""

from collections import OrderedDict
from dataclasses import dataclass
import math
from pathlib import Path
import threading

import numpy as np
import pandas as pd

from . import curvas, gd, irradiancia
from ..core.errors import ErroBDGD
from ..domain.balanco_gd import ResultadoBalancoGD

PASSO_MINUTOS = curvas.PASSO_NATIVO
HORAS = PASSO_MINUTOS / 60.0
METODO_REAMOSTRAGEM = "pchip"

_trava = threading.Lock()
_cache_irradiancia = OrderedDict()
_cache_geracao = OrderedDict()
LIMITE_CACHE_IRRADIANCIA = 4
LIMITE_CACHE_GERACAO = 512


@dataclass(frozen=True)
class IrradianciaAno:
    """Irradiancia do ano em kW/m2, no passo de 15 min, alinhada a curva de carga."""

    serie: pd.Series
    arquivo: str
    latitude: float | None
    longitude: float | None
    diagnostico: dict
    chave: tuple = ()


def _chave_arquivo(caminho):
    caminho = Path(caminho).expanduser().resolve()
    estado = caminho.stat()
    return str(caminho), estado.st_mtime_ns, estado.st_size


def irradiancia_ano(caminho, ano):
    """CSV da NASA -> serie anual em kW/m2 no passo de 15 min (com cache)."""
    if not caminho or not Path(caminho).is_file():
        raise ErroBDGD("IRRADIANCIA_INVALIDA", "Selecione o CSV horário de irradiância da NASA POWER.")
    chave = (*_chave_arquivo(caminho), int(ano))
    with _trava:
        if chave in _cache_irradiancia:
            _cache_irradiancia.move_to_end(chave)
            return _cache_irradiancia[chave]
    try:
        serie, diagnostico = irradiancia.ler_csv_nasa(caminho)
        horaria = irradiancia.serie_ano(serie, ano)
        fina = irradiancia.reamostrar(horaria, PASSO_MINUTOS, METODO_REAMOSTRAGEM, passo_origem=60)
    except ValueError as exc:
        raise ErroBDGD("IRRADIANCIA_INVALIDA", str(exc)) from exc
    indice = curvas.indice_ano(ano, PASSO_MINUTOS)
    if not fina.index.equals(indice):
        # ano bissexto, lacuna no CSV ou fim do arquivo: mesmo calendario da curva
        fina = fina.reindex(indice, fill_value=0.0)
    resultado = IrradianciaAno(fina, chave[0], diagnostico.get("lat"), diagnostico.get("lon"), diagnostico, chave)
    with _trava:
        _cache_irradiancia[chave] = resultado
        while len(_cache_irradiancia) > LIMITE_CACHE_IRRADIANCIA:
            _cache_irradiancia.popitem(last=False)
    return resultado


def geracao_por_kva_ano(irrad: IrradianciaAno, razao, performance_ratio, cut_in):
    """kW por kVA de inversor para uma razao kW/kVA, no ano inteiro (com cache)."""
    chave = (irrad.chave, float(razao), float(performance_ratio), float(cut_in))
    with _trava:
        if chave in _cache_geracao:
            _cache_geracao.move_to_end(chave)
            return _cache_geracao[chave]
    valores = gd.geracao_por_kva(razao, irrad.serie, PASSO_MINUTOS,
                                 performance_ratio=performance_ratio, cut_in=cut_in).to_numpy(dtype=float)
    valores.setflags(write=False)
    with _trava:
        _cache_geracao[chave] = valores
        while len(_cache_geracao) > LIMITE_CACHE_GERACAO:
            _cache_geracao.popitem(last=False)
    return valores


def geracao_ano(irrad, kw, kva, performance_ratio=gd.PERFORMANCE_RATIO_PADRAO,
                cut_in=gd.CUTIN_PCT_PADRAO):
    """Potencia AC da GD no ano inteiro, em kW: geracao por kVA vezes o kVA."""
    return geracao_por_kva_ano(irrad, kw / kva, performance_ratio, cut_in) * kva


def energia_do_mes(valor):
    """ENE da BDGD como a CORRECAO_DEMANDA_BDGD le: ausente ou invalido vira zero."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float, np.floating)):
        return 0.0
    valor = float(valor)
    return valor if math.isfinite(valor) else 0.0


def demanda_mes(carga_pu, geracao_kw, energia_kwh):
    """DEM_MAX do mes. Energia nula ou negativa, ou curva nula, da demanda zero."""
    if energia_kwh <= 0 or float(np.sum(carga_pu)) <= 0:
        return 0.0
    return gd.demanda_maxima_exata(carga_pu, geracao_kw, energia_kwh, PASSO_MINUTOS)


def balanco_mes(carga_pu, geracao_kw, energia_kwh):
    """Demanda do mes e o balanco carga/geracao/rede no formato do CurvaGD."""
    u = np.asarray(carga_pu, dtype=np.float64)
    g = np.zeros_like(u) if geracao_kw is None else np.asarray(geracao_kw, dtype=np.float64)
    tem_geracao = geracao_kw is not None and bool(np.any(g > 0))
    demanda = demanda_mes(u, g if tem_geracao else None, energia_kwh)

    carga = demanda * u
    liquida = carga - g
    importacao = np.maximum(liquida, 0.0)
    exportacao = np.maximum(-liquida, 0.0)
    autoconsumo = np.minimum(carga, g)
    integral = lambda vetor: float(HORAS * vetor.sum())
    e_importada = integral(importacao)
    e_exportada = integral(exportacao)
    e_gerada = integral(g)
    e_autoconsumo = integral(autoconsumo)
    e_carga = integral(carga)
    # Com ENE <= 0 e geracao, qualquer demanda ate min(G/C) importa zero: a
    # CORRECAO adota zero, e o intervalo fica registrado.
    if energia_kwh <= 0 and tem_geracao:
        positivos = u > 0
        maximo = float(np.min(g[positivos] / u[positivos])) if positivos.any() else 0.0
        intervalo, unica = (0.0, maximo), maximo == 0.0
    else:
        intervalo, unica = (demanda, demanda), True
    for vetor in (carga, g, liquida, importacao, exportacao, autoconsumo):
        vetor.setflags(write=False)
    return ResultadoBalancoGD(
        demanda_carga_kw=float(demanda),
        carga_kw=carga,
        geracao_kw=g,
        curva_liquida_kw=liquida,
        importacao_kw=importacao,
        exportacao_kw=exportacao,
        autoconsumo_kw=autoconsumo,
        energia_importada_kwh=e_importada,
        energia_exportada_kwh=e_exportada,
        energia_gerada_kwh=e_gerada,
        energia_autoconsumida_kwh=e_autoconsumo,
        energia_carga_kwh=e_carga,
        residuo_calibracao_kwh=e_importada - max(energia_kwh, 0.0),
        residuo_balanco_carga_kwh=e_carga - (e_importada + e_autoconsumo),
        residuo_balanco_geracao_kwh=e_gerada - (e_exportada + e_autoconsumo),
        residuo_balanco_rede_kwh=(e_importada - e_exportada) - (e_carga - e_gerada),
        solucao_unica=unica,
        intervalo_solucoes_kw=intervalo,
        bracket_kw=intervalo,
        iteracoes=0,
        chamadas_funcao=0,
        interpretacao_ene="importada",
        energia_ene_kwh=float(energia_kwh),
    )


def limpar_caches():
    with _trava:
        _cache_irradiancia.clear()
        _cache_geracao.clear()
