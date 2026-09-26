"""Curvas tipicas por tipo de dia: media e faixa min-max, sem dependencia de I/O.

A serie de entrada e a potencia intervalar de 15 minutos ja expandida na ordem
cronologica (96 valores por dia). Agrupando os dias pelo rotulo de calendario
obtem-se o comportamento medio de dia util, sabado, domingo e feriado, alem da
dispersao observada em cada intervalo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from .calendario import ROTULOS_DIA
from .models import FloatArray
from ..core.errors import ErroBDGD

INTERVALOS_DIA = 96
DT = 0.25


@dataclass(frozen=True)
class CurvaTipica:
    rotulo: str
    dias: int
    media_kw: FloatArray
    minimo_kw: FloatArray
    maximo_kw: FloatArray
    energia_media_kwh: float
    pico_medio_kw: float
    minimo_medio_kw: float
    fator_carga: float


def _matriz_diaria(valores) -> FloatArray:
    vetor = np.asarray(valores, dtype=np.float64)
    if vetor.ndim != 1 or vetor.size == 0:
        raise ErroBDGD("PERFIL_INVALIDO", "A série precisa ser um vetor unidimensional não vazio.")
    if vetor.size % INTERVALOS_DIA:
        raise ErroBDGD("PERFIL_INVALIDO", "A série precisa conter 96 intervalos por dia.")
    if not np.all(np.isfinite(vetor)):
        raise ErroBDGD("PERFIL_INVALIDO", "A série contém valores não finitos.")
    return vetor.reshape(vetor.size // INTERVALOS_DIA, INTERVALOS_DIA)


def _ordenar(rotulos):
    vistos = dict.fromkeys(rotulos)
    canonicos = [r for r in ROTULOS_DIA if r in vistos]
    return canonicos + [r for r in vistos if r not in ROTULOS_DIA]


def curvas_tipicas(valores, rotulos, dt_h: float = DT):
    """Resume cada grupo de dias de mesmo rotulo.

    Retorna um mapeamento rotulo -> CurvaTipica na ordem canonica dos tipos de
    dia. Rotulos sem nenhum dia no periodo simplesmente nao aparecem.
    """
    matriz = _matriz_diaria(valores)
    rotulos = tuple(rotulos)
    if len(rotulos) != matriz.shape[0]:
        raise ErroBDGD("CALENDARIO_INCOMPLETO",
                       "A classificação de dias não cobre a série informada.")
    if isinstance(dt_h, bool) or not isinstance(dt_h, (int, float)) or not math.isfinite(dt_h) or dt_h <= 0:
        raise ErroBDGD("INTERVALO_INVALIDO", "O intervalo deve ser positivo e finito.")
    arranjo = np.asarray(rotulos)
    saida = {}
    for rotulo in _ordenar(rotulos):
        bloco = matriz[arranjo == rotulo]
        media = bloco.mean(axis=0)
        minimo = bloco.min(axis=0)
        maximo = bloco.max(axis=0)
        for vetor in (media, minimo, maximo):
            vetor.setflags(write=False)
        pico = float(np.max(media))
        saida[rotulo] = CurvaTipica(
            rotulo=rotulo,
            dias=int(bloco.shape[0]),
            media_kw=media,
            minimo_kw=minimo,
            maximo_kw=maximo,
            energia_media_kwh=float(dt_h * math.fsum(media)),
            pico_medio_kw=pico,
            minimo_medio_kw=float(np.min(media)),
            fator_carga=float(np.mean(media) / pico) if pico > 0 else 0.0,
        )
    return MappingProxyType(saida)


def contar_rotulos(rotulos):
    """Quantidade de dias por rotulo, na ordem canonica."""
    rotulos = tuple(rotulos)
    return MappingProxyType({r: rotulos.count(r) for r in _ordenar(rotulos)})
