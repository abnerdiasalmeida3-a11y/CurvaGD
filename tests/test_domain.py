import calendar
from dataclasses import replace
from datetime import date
import math
import numpy as np
import pytest
from app.domain.models import PerfisBrutos, ComportamentosTipicos, ContagemDias
from app.domain.enums import TipoDia as D, CoberturaCalendario as C
from app.domain.sintese import normalizar_perfis, validar_perfis_brutos, calcular_demanda_mes, sintetizar_mes
from app.domain.calendario import CalendarioMunicipal, contar_tipos_dia, classificar_data
from app.core.errors import ErroBDGD


def raw(values=(2., 4., 1.)):
    return PerfisBrutos("T1", {d: np.full(96, q, dtype=np.float64) for d, q in zip(D, values)})


def days(coverage=C.COMPLETA):
    return ContagemDias(2024, 4, "5103403", dict(zip(D, (20, 5, 5))), coverage)


def test_example_and_joint_peak():
    p = normalizar_perfis(raw())
    assert p.base_normalizacao_kw == 4
    assert p.areas_pu_h == {D.DU: 12, D.SA: 24, D.DO: 6}
    assert max(p.potencia_pu[D.DU]) == .5
    assert max(p.potencia_pu[D.SA]) == 1
    assert max(p.potencia_pu[D.DO]) == .25
    out = sintetizar_mes(p, 342., days())
    assert out.demanda_kw == pytest.approx(342 / 390)
    assert out.energia_reconstituida_kwh == pytest.approx(342, abs=1e-9)
    assert out.pico_kw == out.demanda_kw
    assert out.tipos_dia_do_pico == {D.SA}
    assert not p.potencia_pu[D.DU].flags.writeable


@pytest.mark.parametrize("scale", [.001, 17., 1e6])
def test_scaling_and_ratios(scale):
    rng = np.random.default_rng(9)
    source = PerfisBrutos("T", {d: rng.uniform(.1, 200, 96).astype(np.float64) for d in D})
    p = normalizar_perfis(source)
    q = normalizar_perfis(PerfisBrutos("T", {d: v * scale for d, v in source.potencia_kw.items()}))
    x, y = sintetizar_mes(p, 19283.23, days()), sintetizar_mes(q, 19283.23, days())
    for d in D:
        np.testing.assert_allclose(x.curvas_kw[d], y.curvas_kw[d], rtol=1e-12)
        np.testing.assert_allclose(p.potencia_pu[d] / p.potencia_pu[D.DU][0], source.potencia_kw[d] / source.potencia_kw[D.DU][0])


@pytest.mark.parametrize("energy", [0., 1e-8, 342., 1e9])
def test_energy_every_month(energy):
    p = normalizar_perfis(raw((2, 1, 5)))
    for month in range(1, 13):
        c = contar_tipos_dia(2024, month, "5103403", CalendarioMunicipal())
        r = sintetizar_mes(p, energy, c)
        assert abs(r.energia_reconstituida_kwh - energy) <= 1e-9 + 1e-12 * abs(energy)
        assert r.cobertura_calendario == C.PARCIAL
        if energy == 0:
            assert r.demanda_kw == 0 and r.tipos_dia_do_pico == set(D)


@pytest.mark.parametrize("bad", [-1, None, np.nan, np.inf, "2", True])
def test_bad_energy(bad):
    with pytest.raises(ErroBDGD) as e:
        sintetizar_mes(normalizar_perfis(raw()), bad, days())
    assert e.value.codigo == "ENERGIA_INVALIDA"


@pytest.mark.parametrize("bad", [np.full(95, 1.), np.full(97, 1.), np.full((96, 1), 1.), np.ones(96, dtype=np.int32), [1.] * 96, np.full(96, -1.), np.full(96, np.nan), np.full(96, np.inf)])
def test_bad_profiles(bad):
    r = raw()
    r.potencia_kw[D.SA] = bad
    with pytest.raises(ErroBDGD) as e:
        normalizar_perfis(r)
    assert e.value.codigo == "PERFIL_INVALIDO"


def test_missing_base_and_zero_profiles():
    with pytest.raises(ErroBDGD, match="DU, SA e DO"):
        normalizar_perfis(PerfisBrutos("T", {}))
    with pytest.raises(ErroBDGD) as e:
        normalizar_perfis(raw((0, 0, 0)))
    assert e.value.codigo == "BASE_NORMALIZACAO_INVALIDA"
    with pytest.raises(ErroBDGD) as e:
        normalizar_perfis(raw((np.inf, 2, 1)))
    assert "BASE_NORMALIZACAO_INVALIDA" in e.value.codigos
    with pytest.warns(UserWarning, match="PERFIL_ZERO"):
        p = normalizar_perfis(raw((1, 0, 0)))
    with pytest.warns(UserWarning):
        assert sintetizar_mes(p, 100., days()).energia_reconstituida_kwh == pytest.approx(100)


def test_calendar():
    sat = date(2024, 2, 3)
    cal = CalendarioMunicipal({"5103403": frozenset([sat])}, {"5103403": (date(2024, 1, 1), date(2024, 12, 31), True)})
    counts = contar_tipos_dia(2024, 2, "5103403", cal)
    assert sum(counts.dias.values()) == 29
    assert counts.dias[D.SA] == 3
    assert counts.dias[D.DO] == 5
    assert counts.cobertura == C.COMPLETA
    assert classificar_data(sat, "5103403", cal) == D.DO
    assert classificar_data(date(2024, 2, 4), "5103403", cal) == D.DO
    assert contar_tipos_dia(2024, 2, "5107909", cal).cobertura == C.PARCIAL
    for y, m, mun in [(2024, 13, "5103403"), (0, 1, "5103403"), (2024, 1, "x")]:
        with pytest.raises(ErroBDGD):
            contar_tipos_dia(y, m, mun, cal)


@pytest.mark.parametrize("c", [replace(days(), mes=0), replace(days(), dias={D.DU: 30}), replace(days(), dias={D.DU: 29, D.SA: -1, D.DO: 2}), replace(days(), municipio_ibge="x"), None])
def test_invalid_counts(c):
    with pytest.raises(ErroBDGD):
        calcular_demanda_mes(1., {D.DU: 12., D.SA: 24., D.DO: 6.}, c)


@pytest.mark.parametrize("areas", [{}, dict.fromkeys(D, 0.), dict.fromkeys(D, -1.), dict.fromkeys(D, np.inf)])
def test_invalid_areas(areas):
    with pytest.raises(ErroBDGD):
        calcular_demanda_mes(1., areas, days())


def test_public_objects_cannot_bypass_validation():
    p = normalizar_perfis(raw())
    for corrupt in [None, replace(p, base_normalizacao_kw=0), replace(p, potencia_pu={d: v * 2 for d, v in p.potencia_pu.items()}), replace(p, areas_pu_h=dict.fromkeys(D, 1.))]:
        with pytest.raises(ErroBDGD):
            sintetizar_mes(corrupt, 1., days())


def test_aggregation_does_not_equalize_peaks():
    a = sintetizar_mes(normalizar_perfis(raw((5, 2, 1))), 100., days())
    b = sintetizar_mes(normalizar_perfis(raw((1, 4, 2))), 200., days())
    peak = [max(a.curvas_kw[d] + b.curvas_kw[d]) for d in D]
    assert len(set(peak)) == 3
    energy = sum(days().dias[d] * .25 * np.sum(a.curvas_kw[d] + b.curvas_kw[d]) for d in D)
    assert energy == pytest.approx(300.)
