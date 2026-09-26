"""Curva de carga anual em pu a partir dos perfis DU/SA/DO da CRVCRG.

Mesma regra do `curvas.py` da CORRECAO_DEMANDA_BDGD:

- cada dia do calendario recebe o perfil do seu dia da semana -- segunda a sexta
  DU, sabado SA, domingo DO. A CRVCRG nao traz feriado, entao feriado cai no
  perfil do seu dia da semana;
- a curva e normalizada pelo maximo do ANO inteiro, e nao pelo maximo do recorte.
  No passo nativo de 15 min isso e o maximo conjunto dos tres perfis, entao
  DEM_MAX de um mes e, literalmente, o pico daquele mes.
"""

import calendar
from functools import lru_cache

import numpy as np
import pandas as pd

from ..domain.calendario import DIA_UTIL, DOMINGO, SABADO
from ..domain.enums import TipoDia

PASSO_NATIVO = 15  # minutos entre os 96 pontos de cada perfil
PONTOS_POR_DIA = 96
ROTULO_DO_TIPO = {TipoDia.DU: DIA_UTIL, TipoDia.SA: SABADO, TipoDia.DO: DOMINGO}


def tipo_do_dia(data):
    """Dia da semana -> tipo de perfil. O banco nao traz feriado, entao ele cai em DU."""
    dia = data.weekday()
    if dia == 5:
        return TipoDia.SA
    if dia == 6:
        return TipoDia.DO
    return TipoDia.DU


def indice_ano(ano, passo_minutos=PASSO_NATIVO):
    """Instantes do ano civil, rotulando o inicio de cada bloco (sem fuso)."""
    dias = 366 if calendar.isleap(int(ano)) else 365
    return pd.date_range(f"{int(ano)}-01-01", periods=dias * 24 * 60 // passo_minutos,
                         freq=f"{passo_minutos}min")


def curva_ano(comportamentos, ano):
    """Serie do ano inteiro em pu (vetor), na base do maximo anual.

    `comportamentos` e o ComportamentosTipicos da tipologia (perfis DU/SA/DO).
    Os perfis ja chegam em pu do maximo conjunto; a divisao pelo maximo do ano
    repete a normalizacao da CORRECAO_DEMANDA_BDGD e deixa o resultado igual
    mesmo que o perfil armazenado tivesse outra base.
    """
    dias = pd.date_range(f"{int(ano)}-01-01", f"{int(ano)}-12-31", freq="D")
    perfis = {tipo: np.asarray(comportamentos.potencia_pu[tipo], dtype=float) for tipo in TipoDia}
    valores = np.concatenate([perfis[tipo_do_dia(dia)] for dia in dias])
    base = float(valores.max())
    return valores / base if base > 0 else valores


def fatias_mensais(ano, passo_minutos=PASSO_NATIVO):
    """Um slice por mes sobre o vetor anual: os meses sao blocos contiguos."""
    meses = indice_ano(ano, passo_minutos).month.to_numpy()
    bordas = np.searchsorted(meses, np.arange(1, 14))
    return [slice(int(bordas[m - 1]), int(bordas[m])) for m in range(1, 13)]


@lru_cache(maxsize=48)
def rotulos_mes(ano, mes):
    """Um rotulo por dia do mes (Dia util, Sabado ou Domingo), na ordem do calendario."""
    dias = calendar.monthrange(int(ano), int(mes))[1]
    return tuple(ROTULO_DO_TIPO[tipo_do_dia(pd.Timestamp(int(ano), int(mes), d))] for d in range(1, dias + 1))
