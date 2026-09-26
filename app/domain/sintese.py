"""Potência intervalar de 15 minutos; nenhuma dependência de I/O."""
import calendar
import math
import warnings
from types import MappingProxyType
import numpy as np
from .enums import TipoDia, CoberturaCalendario
from .models import PerfisBrutos, ComportamentosTipicos, ContagemDias, ResultadoSintese
from ..core.errors import ErroBDGD

DT = 0.25
DIAS = frozenset(TipoDia)


def validar_perfis_brutos(perfis: PerfisBrutos) -> None:
    if not isinstance(perfis, PerfisBrutos) or set(perfis.potencia_kw) != DIAS:
        raise ErroBDGD("TIPOLOGIA_INCOMPLETA", "A tipologia precisa de DU, SA e DO.")
    vetores = list(perfis.potencia_kw.values())
    if any(not isinstance(v, np.ndarray) or v.shape != (96,) or v.dtype != np.float64 for v in vetores):
        raise ErroBDGD("PERFIL_INVALIDO", "Cada perfil precisa de 96 valores float64.")
    pontos = np.concatenate(vetores)
    base = float(np.max(pontos))
    if not np.all(np.isfinite(pontos)) or np.any(pontos < 0):
        extra = ("BASE_NORMALIZACAO_INVALIDA",) if not np.isfinite(base) or base <= 0 else ()
        raise ErroBDGD("PERFIL_INVALIDO", "Perfil com valores negativos ou não finitos.", codigos=extra)
    if base <= 0:
        raise ErroBDGD("BASE_NORMALIZACAO_INVALIDA", "A tipologia inteira tem potência zero.")
    for dia in (TipoDia.SA, TipoDia.DO):
        if not np.any(perfis.potencia_kw[dia]):
            warnings.warn(f"PERFIL_ZERO: {perfis.tipologia}/{dia}", UserWarning, stacklevel=2)


def normalizar_perfis(perfis_brutos: PerfisBrutos) -> ComportamentosTipicos:
    validar_perfis_brutos(perfis_brutos)
    base = max(float(np.max(v)) for v in perfis_brutos.potencia_kw.values())
    curvas, areas = {}, {}
    for dia in TipoDia:
        vetor = perfis_brutos.potencia_kw[dia] / base
        vetor.setflags(write=False)
        curvas[dia] = vetor
        areas[dia] = DT * math.fsum(vetor)
    return ComportamentosTipicos(perfis_brutos.tipologia, MappingProxyType(curvas), base, MappingProxyType(areas))


def calcular_demanda_mes(energia_kwh: float, areas_pu_h, contagem: ContagemDias) -> float:
    if isinstance(energia_kwh, (bool, str)) or not isinstance(energia_kwh, (int, float, np.floating)) or not math.isfinite(energia_kwh) or energia_kwh < 0:
        raise ErroBDGD("ENERGIA_INVALIDA", "Informe energia mensal finita e não negativa.")
    try:
        n = calendar.monthrange(contagem.ano, contagem.mes)[1]
        valido = (set(contagem.dias) == DIAS and isinstance(contagem.cobertura, CoberturaCalendario)
                  and len(contagem.municipio_ibge) == 7 and contagem.municipio_ibge.isdigit()
                  and all(type(x) is int and x >= 0 for x in contagem.dias.values())
                  and sum(contagem.dias.values()) == n)
    except (ValueError, TypeError, AttributeError):
        valido = False
    if not valido:
        raise ErroBDGD("CALENDARIO_INCOMPLETO", "Contagem de dias inválida para o mês.")
    if set(areas_pu_h) != DIAS or any(not isinstance(a, (int, float, np.floating)) or not math.isfinite(a) or a < 0 for a in areas_pu_h.values()):
        raise ErroBDGD("PERFIL_INVALIDO", "Áreas dos comportamentos inválidas.")
    h = math.fsum(contagem.dias[d] * areas_pu_h[d] for d in TipoDia)
    if not math.isfinite(h) or h <= 0:
        raise ErroBDGD("PERFIL_INVALIDO", "Integral mensal do comportamento não positiva.")
    demanda = float(energia_kwh / h)
    if not math.isfinite(demanda):
        raise ErroBDGD("ENERGIA_INVALIDA", "Energia fora da faixa numérica suportada.")
    return demanda


def sintetizar_mes(comportamentos: ComportamentosTipicos, energia_kwh: float, contagem: ContagemDias) -> ResultadoSintese:
    # Objetos públicos também podem ser construídos pelo consumidor: validar invariantes.
    if not isinstance(comportamentos, ComportamentosTipicos):
        raise ErroBDGD("PERFIL_INVALIDO", "Comportamentos inválidos.")
    validar_perfis_brutos(PerfisBrutos(comportamentos.tipologia, comportamentos.potencia_pu))
    base = comportamentos.base_normalizacao_kw
    if not isinstance(base, (int, float)) or not math.isfinite(base) or base <= 0:
        raise ErroBDGD("BASE_NORMALIZACAO_INVALIDA", "Base de normalização inválida.")
    pico_pu = max(float(np.max(v)) for v in comportamentos.potencia_pu.values())
    if not math.isclose(pico_pu, 1.0, rel_tol=1e-12, abs_tol=1e-15):
        raise ErroBDGD("PERFIL_INVALIDO", "O máximo conjunto deve ser 1 pu.")
    areas = {d: DT * math.fsum(comportamentos.potencia_pu[d]) for d in TipoDia}
    if set(comportamentos.areas_pu_h) != DIAS or any(not math.isclose(areas[d], comportamentos.areas_pu_h[d], rel_tol=1e-12, abs_tol=1e-15) for d in TipoDia):
        raise ErroBDGD("PERFIL_INVALIDO", "Áreas não correspondem aos comportamentos.")
    demanda = calcular_demanda_mes(energia_kwh, areas, contagem)
    curvas = {d: demanda * comportamentos.potencia_pu[d] for d in TipoDia}
    for v in curvas.values():
        v.setflags(write=False)
    energia = math.fsum(contagem.dias[d] * DT * math.fsum(curvas[d]) for d in TipoDia)
    if not math.isfinite(energia) or abs(energia - energia_kwh) > 1e-9 + 1e-12 * abs(energia_kwh):
        raise ErroBDGD("ENERGIA_INVALIDA", "A síntese não conservou a energia mensal.")
    pico = max(float(np.max(v)) for v in curvas.values())
    picos = frozenset(d for d in TipoDia if math.isclose(float(np.max(curvas[d])), pico, rel_tol=1e-12, abs_tol=1e-15))
    return ResultadoSintese(demanda, MappingProxyType(curvas), energia, pico, picos, contagem.cobertura)
