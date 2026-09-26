"""Agregacao de potencia em energia conservando os intervalos de 15 minutos."""
import math
import numpy as np
import pandas as pd

from ..core.errors import ErroBDGD


def agregar_energia(timestamps, series, periodo="hora"):
    regras = {"15min": "15min", "hora": "h", "dia": "D", "mes": "MS"}
    if periodo not in regras:
        raise ErroBDGD("INTERVALO_INVALIDO", "Escolha 15min, hora, dia ou mes.")
    indice = pd.DatetimeIndex(timestamps)
    if len(indice) == 0 or indice.has_duplicates or not indice.is_monotonic_increasing:
        raise ErroBDGD("INTERVALO_INVALIDO", "Serie temporal vazia, duplicada ou fora de ordem.")
    if len(indice) > 1 and not np.all(np.diff(indice.asi8) == 900_000_000_000):
        raise ErroBDGD("INTERVALO_INVALIDO", "A agregacao exige intervalos consecutivos de 15 minutos.")
    dados = {}
    for nome, vetor in series.items():
        valores = np.asarray(vetor, dtype=float)
        if valores.shape != (len(indice),) or not np.all(np.isfinite(valores)):
            raise ErroBDGD("PERFIL_INVALIDO", "Valores invalidos na agregacao.")
        dados[nome.replace("(kW)", "(kWh)")] = valores * 0.25
    quadro = pd.DataFrame(dados, index=indice).resample(regras[periodo]).sum()
    return quadro


def comparar_demanda(balanco, demanda_medida_kw, tolerancia_percentual=20.0):
    if demanda_medida_kw is None:
        return None
    valor = float(demanda_medida_kw)
    if not math.isfinite(valor) or valor < 0:
        raise ErroBDGD("ENERGIA_INVALIDA", "DEM deve ser finito e nao negativo.")
    # DEM medido na fronteira deve ser comparado a importacao, nao a carga bruta.
    pico = float(np.max(balanco.importacao_kw))
    diferenca = pico - valor
    percentual = 100 * diferenca / valor if valor else None
    compativel = abs(diferenca) <= max(1e-6, valor * tolerancia_percentual / 100)
    return {"demanda_medida_kw": valor, "pico_importacao_kw": pico,
            "pico_carga_kw": float(np.max(balanco.carga_kw)), "diferenca_kw": diferenca,
            "diferenca_percentual": percentual, "compativel": compativel,
            "tolerancia_percentual": tolerancia_percentual}
