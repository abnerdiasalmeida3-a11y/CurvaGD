"""Resultado do balanco mensal de uma UC: carga, geracao e troca com a rede.

O calculo que preenche esta estrutura esta em `app.calculo.motor`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import FloatArray


@dataclass(frozen=True)
class ResultadoBalancoGD:
    demanda_carga_kw: float
    carga_kw: FloatArray
    geracao_kw: FloatArray
    curva_liquida_kw: FloatArray
    importacao_kw: FloatArray
    exportacao_kw: FloatArray
    autoconsumo_kw: FloatArray
    energia_importada_kwh: float
    energia_exportada_kwh: float
    energia_gerada_kwh: float
    energia_autoconsumida_kwh: float
    energia_carga_kwh: float
    residuo_calibracao_kwh: float
    residuo_balanco_carga_kwh: float
    residuo_balanco_geracao_kwh: float
    residuo_balanco_rede_kwh: float
    solucao_unica: bool
    intervalo_solucoes_kw: tuple[float, float]
    bracket_kw: tuple[float, float]
    iteracoes: int
    chamadas_funcao: int
    interpretacao_ene: str = "importada"
    energia_ene_kwh: float = 0.0

    @property
    def demanda_kw(self):
        return self.demanda_carga_kw

    @property
    def carga_sem_gd_kw(self):
        return self.carga_kw

    @property
    def geracao_gd_kw(self):
        return self.geracao_kw

    @property
    def residuo_importacao_kwh(self):
        return self.residuo_calibracao_kwh

    @property
    def residuo_balanco_kwh(self):
        return self.residuo_balanco_rede_kwh

    @property
    def identificacao_unica(self):
        return self.solucao_unica
