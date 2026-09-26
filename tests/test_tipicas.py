"""Classificacao detalhada dos dias e curvas tipicas por tipo de dia."""
from datetime import date

import numpy as np
import pytest

from app.core.errors import ErroBDGD
from app.domain.calendario import (CalendarioMunicipal, TIPO_DIA_DO_ROTULO,
                                   classificar_data, classificar_detalhado, rotular_dias)
from app.domain.tipicas import contar_rotulos, curvas_tipicas

MUNICIPIO = "5103403"
# Janeiro/2024: 01/01 cai numa segunda-feira e 25/01 numa quinta-feira.
CALENDARIO = CalendarioMunicipal({MUNICIPIO: frozenset({date(2024, 1, 1), date(2024, 1, 25)})})


def test_rotulos_separam_feriado_de_domingo():
    rotulos = rotular_dias(2024, 1, MUNICIPIO, CALENDARIO)
    assert len(rotulos) == 31
    assert rotulos[0] == "Feriado"      # 01/01, segunda-feira
    assert rotulos[1] == "Dia útil"     # 02/01
    assert rotulos[5] == "Sábado"       # 06/01
    assert rotulos[6] == "Domingo"      # 07/01
    assert rotulos[24] == "Feriado"     # 25/01, feriado municipal
    assert dict(contar_rotulos(rotulos)) == {"Dia útil": 21, "Sábado": 4, "Domingo": 4, "Feriado": 2}


def test_rotulo_detalhado_mantem_a_classificacao_do_modelo():
    for dia in range(1, 32):
        data = date(2024, 1, dia)
        rotulo = classificar_detalhado(data, MUNICIPIO, CALENDARIO)
        assert TIPO_DIA_DO_ROTULO[rotulo] == classificar_data(data, MUNICIPIO, CALENDARIO)


def test_mes_sem_feriado_tem_apenas_tres_grupos():
    rotulos = rotular_dias(2024, 3, MUNICIPIO, CalendarioMunicipal())
    assert set(rotulos) == {"Dia útil", "Sábado", "Domingo"}


def test_curvas_tipicas_resumem_cada_grupo_de_dias():
    rotulos = rotular_dias(2024, 1, MUNICIPIO, CALENDARIO)
    # Cada dia vale o proprio numero, o que torna as medias conferiveis a mao.
    serie = np.concatenate([np.full(96, float(d)) for d in range(1, 32)])
    tipicas = curvas_tipicas(serie, rotulos)
    assert list(tipicas) == ["Dia útil", "Sábado", "Domingo", "Feriado"]
    for rotulo, tipica in tipicas.items():
        dias = [d for d, r in enumerate(rotulos, 1) if r == rotulo]
        assert tipica.dias == len(dias)
        np.testing.assert_allclose(tipica.media_kw, np.mean(dias))
        np.testing.assert_allclose(tipica.minimo_kw, min(dias))
        np.testing.assert_allclose(tipica.maximo_kw, max(dias))
        assert tipica.energia_media_kwh == pytest.approx(24.0 * np.mean(dias))
        assert tipica.fator_carga == pytest.approx(1.0)  # curva plana


def test_fator_de_carga_de_uma_curva_com_forma():
    rotulos = rotular_dias(2024, 1, MUNICIPIO, CALENDARIO)
    hora = np.tile(np.arange(96) / 4.0, 31)
    tipica = curvas_tipicas(1.0 + 0.5 * np.sin((hora - 6) / 24 * 2 * np.pi), rotulos)["Dia útil"]
    assert 0 < tipica.fator_carga < 1
    assert tipica.pico_medio_kw > tipica.minimo_medio_kw
    assert tipica.media_kw.flags.writeable is False


@pytest.mark.parametrize("valores,rotulos,codigo", [
    (np.zeros(95), ("Dia útil",), "PERFIL_INVALIDO"),
    (np.zeros(96 * 3), ("Dia útil", "Sábado"), "CALENDARIO_INCOMPLETO"),
    (np.full(96, np.nan), ("Dia útil",), "PERFIL_INVALIDO"),
])
def test_curvas_tipicas_recusam_entradas_inconsistentes(valores, rotulos, codigo):
    with pytest.raises(ErroBDGD) as erro:
        curvas_tipicas(valores, rotulos)
    assert erro.value.codigo == codigo
