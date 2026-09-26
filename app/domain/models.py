from dataclasses import dataclass
from typing import Mapping
import numpy as np
import numpy.typing as npt
from .enums import TipoDia, CoberturaCalendario

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PerfisBrutos:
    tipologia: str
    potencia_kw: Mapping[TipoDia, FloatArray]


@dataclass(frozen=True)
class ComportamentosTipicos:
    tipologia: str
    potencia_pu: Mapping[TipoDia, FloatArray]
    base_normalizacao_kw: float
    areas_pu_h: Mapping[TipoDia, float]


@dataclass(frozen=True)
class ContagemDias:
    ano: int
    mes: int
    municipio_ibge: str
    dias: Mapping[TipoDia, int]
    cobertura: CoberturaCalendario


@dataclass(frozen=True)
class ResultadoSintese:
    demanda_kw: float
    curvas_kw: Mapping[TipoDia, FloatArray]
    energia_reconstituida_kwh: float
    pico_kw: float
    tipos_dia_do_pico: frozenset[TipoDia]
    cobertura_calendario: CoberturaCalendario
