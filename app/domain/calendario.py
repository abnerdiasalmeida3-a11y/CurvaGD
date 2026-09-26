"""Calendário puro: conjuntos de feriados são fornecidos pela infraestrutura."""
import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping
from .enums import TipoDia, CoberturaCalendario
from .models import ContagemDias
from ..core.errors import ErroBDGD


@dataclass(frozen=True)
class CalendarioMunicipal:
    feriados: Mapping[str, frozenset[date]] = field(default_factory=dict)
    coberturas: Mapping[str, tuple[date, date, bool]] = field(default_factory=dict)

    def cobertura(self, municipio: str, inicio: date, fim: date) -> CoberturaCalendario:
        c = self.coberturas.get(municipio)
        return CoberturaCalendario.COMPLETA if c and c[2] and c[0] <= inicio and c[1] >= fim else CoberturaCalendario.PARCIAL


def classificar_data(data: date, municipio_ibge: str, calendario: CalendarioMunicipal) -> TipoDia:
    if data in calendario.feriados.get(municipio_ibge, ()) or data.weekday() == 6:
        return TipoDia.DO
    return TipoDia.SA if data.weekday() == 5 else TipoDia.DU


# Leitura humana dos tipos de dia. O modelo da BDGD tem tres comportamentos
# (DU, SA, DO) e trata feriado como domingo; aqui o feriado e destacado para
# que a analise mostre quantos dias de cada natureza compoem o mes.
ROTULOS_DIA = ("Dia útil", "Sábado", "Domingo", "Feriado")
DIA_UTIL, SABADO, DOMINGO, FERIADO = ROTULOS_DIA

TIPO_DIA_DO_ROTULO = {
    DIA_UTIL: TipoDia.DU,
    SABADO: TipoDia.SA,
    DOMINGO: TipoDia.DO,
    FERIADO: TipoDia.DO,
}


def classificar_detalhado(data: date, municipio_ibge: str, calendario: CalendarioMunicipal) -> str:
    """Mesma precedencia de classificar_data, separando feriado de domingo."""
    if data in calendario.feriados.get(municipio_ibge, ()):
        return FERIADO
    if data.weekday() == 6:
        return DOMINGO
    return SABADO if data.weekday() == 5 else DIA_UTIL


def rotular_dias(ano: int, mes: int, municipio_ibge: str, calendario: CalendarioMunicipal) -> tuple[str, ...]:
    """Um rotulo por dia do mes, na ordem cronologica."""
    try:
        n = calendar.monthrange(ano, mes)[1]
    except (ValueError, TypeError) as exc:
        raise ErroBDGD("CALENDARIO_INCOMPLETO", "Ano ou mês inválido.") from exc
    return tuple(classificar_detalhado(date(ano, mes, dia), municipio_ibge, calendario) for dia in range(1, n + 1))


def contar_tipos_dia(ano: int, mes: int, municipio_ibge: str, calendario: CalendarioMunicipal) -> ContagemDias:
    try:
        n = calendar.monthrange(ano, mes)[1]
        inicio, fim = date(ano, mes, 1), date(ano, mes, n)
    except (ValueError, TypeError) as exc:
        raise ErroBDGD("CALENDARIO_INCOMPLETO", "Ano ou mês inválido.") from exc
    if not isinstance(municipio_ibge, str) or len(municipio_ibge) != 7 or not municipio_ibge.isdigit():
        raise ErroBDGD("CALENDARIO_INCOMPLETO", "Código municipal precisa de sete dígitos.")
    dias = {d: 0 for d in TipoDia}
    for i in range(1, n + 1):
        dias[classificar_data(date(ano, mes, i), municipio_ibge, calendario)] += 1
    return ContagemDias(ano, mes, municipio_ibge, dias, calendario.cobertura(municipio_ibge, inicio, fim))
