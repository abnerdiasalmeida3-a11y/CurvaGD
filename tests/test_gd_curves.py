"""Calculo da carga e das curvas das UCs pelo metodo da CORRECAO_DEMANDA_BDGD."""
import calendar
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.calculo import curvas, gd, irradiancia, motor, potencias
from app.data.repositories.gd import buscar_equipamento_aneel
from app.data.repositories.uc import FiltrosUC
from app.domain.calendario import ROTULOS_DIA, TIPO_DIA_DO_ROTULO
from app.domain.enums import TipoDia
from app.domain.models import ComportamentosTipicos
from app.domain.tipicas import contar_rotulos, curvas_tipicas
from app.ingest.pipeline import import_bdgd
from app.services.application import ApplicationService


def _comportamento(du=0.25, sa=0.5, do=1.0):
    perfis = {TipoDia.DU: np.full(96, du), TipoDia.SA: np.full(96, sa), TipoDia.DO: np.full(96, do)}
    return ComportamentosTipicos("T", perfis, 1.0, {d: 24 * perfis[d][0] for d in TipoDia})


def _nasa_csv(path: Path, ano_completo=False):
    linhas = [
        "-BEGIN HEADER-",
        "Location: Latitude  -15.5929   Longitude -56.0925",
        "Elevation from MERRA-2: Average = 245.83 meters",
        "-END HEADER-",
        "YEAR,MO,DY,HR,ALLSKY_SFC_SW_DWN",
    ]
    for mes in (range(1, 13) if ano_completo else [2]):
        for dia in range(1, calendar.monthrange(2024, mes)[1] + 1):
            for hora in range(24):
                ghi = max(0.0, 800.0 * np.sin(np.pi * (hora - 5) / 13)) if 5 <= hora <= 18 else 0.0
                linhas.append(f"2024,{mes},{dia},{hora},{ghi:.6f}")
    path.write_text("\n".join(linhas), encoding="utf-8")


def test_curva_do_ano_segue_o_dia_da_semana_sem_feriado():
    ano = curvas.curva_ano(_comportamento(), 2024)
    assert ano.shape == (366 * 96,)
    assert ano.max() == 1.0
    assert ano[0] == 0.25  # 01/01/2024, segunda-feira: feriado segue o dia da semana
    assert ano[5 * 96] == 0.5  # sabado
    assert ano[6 * 96] == 1.0  # domingo
    assert curvas.rotulos_mes(2024, 1)[:7] == ("Dia útil",) * 5 + ("Sábado", "Domingo")
    fatias = curvas.fatias_mensais(2024)
    assert [f.stop - f.start for f in fatias] == [calendar.monthrange(2024, m)[1] * 96 for m in range(1, 13)]


def test_sem_gd_a_demanda_fecha_direto_e_conserva_energia():
    carga = np.array([0.5, 1.0, 0.25, 0.75])
    balanco = motor.balanco_mes(carga, None, 5.0)
    assert balanco.demanda_kw == pytest.approx(5.0 / (0.25 * carga.sum()))
    assert balanco.energia_importada_kwh == pytest.approx(5.0)
    assert motor.balanco_mes(carga, None, 0.0).demanda_kw == 0.0
    assert motor.balanco_mes(carga, None, -3.0).demanda_kw == 0.0


def test_inversao_exata_bate_com_a_bissecao():
    rng = np.random.default_rng(7)
    carga = rng.uniform(0.1, 1.0, 2976)
    geracao = np.maximum(0.0, rng.normal(0.5, 0.8, 2976))
    for energia in (1.0, 50.0, 400.0, 2000.0):
        exata = gd.demanda_maxima_exata(carga, geracao, energia, 15)
        assert gd.energia_importada(exata, carga, geracao, 15) == pytest.approx(energia, rel=1e-10)
        balanco = motor.balanco_mes(carga, geracao, energia)
        assert balanco.energia_importada_kwh == pytest.approx(energia, rel=1e-10)
        assert abs(balanco.residuo_balanco_rede_kwh) < 1e-8


def test_modelo_pvsystem_limita_no_inversor_e_respeita_cut_in(tmp_path):
    nasa = tmp_path / "nasa.csv"
    _nasa_csv(nasa, ano_completo=True)
    irrad = motor.irradiancia_ano(nasa, 2024)
    assert len(irrad.serie) == 366 * 96
    # PCHIP sobre a energia acumulada: a energia de cada hora e preservada
    serie, _ = irradiancia.ler_csv_nasa(nasa)
    assert irradiancia.energia(irrad.serie, 15) == pytest.approx(irradiancia.energia(serie, 60), rel=1e-9)
    geracao = motor.geracao_ano(irrad, 8.0, 5.0)
    assert geracao.max() <= 5.0 + 1e-12
    assert np.all(geracao[irrad.serie.to_numpy() * 8.0 * 1.2 < 0.02 * 5.0] == 0.0)
    # a potencia por kVA so depende da razao kW/kVA
    np.testing.assert_allclose(motor.geracao_ano(irrad, 16.0, 10.0), 2 * geracao)


def test_potencias_somam_linhas_da_aneel_e_reserva_na_ug(tmp_path):
    caminho = tmp_path / "gd.csv"
    caminho.write_text(
        "CodGeracaoDistribuida;MdaPotenciaModulos;MdaPotenciaInversores;DatConexao\n"
        "GD.OUTRA;3,2;3,0;01/01/2024\n"
        "GD.TESTE;6,60;5,00;02/01/2024\n"
        "GD.TESTE;2,00;1,50;03/01/2024\n",
        encoding="utf-8",
    )
    equipamento = buscar_equipamento_aneel(caminho, "gd.teste")
    assert equipamento.potencia_modulos_kwp == pytest.approx(8.6)
    assert equipamento.potencia_inversor_kw == pytest.approx(6.5)
    assert potencias.resolver("GD", "ucbt", pot_ug_kw=10.0) == (12.5, 10.0, potencias.STATUS_UGBT)
    # POT_INST em W acima do teto de BT e convertido; inverossimil vira sem potencia
    assert potencias.resolver("GD", "ucbt", pot_ug_kw=5000.0)[1] == 5.0
    assert potencias.resolver("GD", "ucbt", pot_ug_kw=9e7)[2] == potencias.STATUS_SEM_POTENCIA
    assert potencias.resolver("", "ucbt", pot_ug_kw=10.0)[2] == potencias.STATUS_SEM_GD


def test_servico_gera_uc_sem_gd_com_energia_exata(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    import_id = manifest["import_id"]
    uc = service.page(import_id, FiltrosUC(busca="ucbt-0000001"), None)["linhas"][0]
    resultado = service.curva_uc(import_id, uc["entidade"], uc["linha_origem"], 1)
    assert resultado["balanco"].energia_carga_kwh == pytest.approx(343.0)
    assert resultado["balanco"].energia_importada_kwh == pytest.approx(343.0)
    np.testing.assert_array_equal(resultado["series"]["Geração da GD (kW)"], 0.0)
    assert resultado["irradiancia"] is None
    carga = np.asarray(resultado["series"]["Carga sem GD (kW)"])
    assert carga.max() == pytest.approx(resultado["demanda_max_kw"])
    tipos = resultado["tipos_dia"]
    assert len(tipos) == 31 and set(tipos) <= set(ROTULOS_DIA)
    carga = carga.reshape(31, 96)
    for i, tipo_i in enumerate(tipos):
        for j, tipo_j in enumerate(tipos):
            if TIPO_DIA_DO_ROTULO[tipo_i] == TIPO_DIA_DO_ROTULO[tipo_j]:
                np.testing.assert_allclose(carga[i], carga[j])
    tipicas = curvas_tipicas(resultado["series"]["Carga sem GD (kW)"], tipos)
    assert dict(contar_rotulos(tipos)) == {r: t.dias for r, t in tipicas.items()}


def test_uc_com_gd_usa_pvsystem_e_reproduz_ene(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    nasa = tmp_path / "nasa.csv"
    _nasa_csv(nasa, ano_completo=True)
    uc = service.page(manifest["import_id"], FiltrosUC(busca="ucbt-0000000"), None)["linhas"][0]
    result = service.curva_uc(manifest["import_id"], uc["entidade"], uc["linha_origem"], 2,
                              irradiancia_path=str(nasa), potencia_modulos_kwp=8.0, potencia_inversor_kw=5.0)
    irrad = motor.irradiancia_ano(nasa, 2024)
    fatia = curvas.fatias_mensais(2024)[1]
    esperado = motor.geracao_ano(irrad, 8.0, 5.0)[fatia]
    np.testing.assert_array_equal(result["series"]["Geração da GD (kW)"], esperado)
    assert result["balanco"].energia_importada_kwh == pytest.approx(uc["energia_mes_02"])
    assert result["potencias_gd"]["status"] == potencias.STATUS_INFORMADA
    ghi = result["irradiancia"]["series"]["GHI - horizontal (W/m²)"]
    np.testing.assert_allclose(ghi, irrad.serie.to_numpy()[fatia] * 1000)
    assert result["irradiancia"]["latitude"] == -15.5929
    assert len(ghi) == len(result["timestamps"]) == 29 * 96


def test_ano_completo_preserva_balancos_irradiancia_e_exportacao(qtbot, source, tmp_path):
    import csv
    from datetime import timedelta
    from app.ui.main_window import CurveResultDialog
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    nasa = tmp_path / "nasa_ano.csv"
    _nasa_csv(nasa, ano_completo=True)
    uc = service.page(manifest["import_id"], FiltrosUC(busca="ucbt-0000000"), None)["linhas"][0]
    result = service.curva_uc(
        manifest["import_id"], uc["entidade"], uc["linha_origem"], 0,
        irradiancia_path=str(nasa), potencia_modulos_kwp=8.0, potencia_inversor_kw=5.0,
    )
    assert result["mes"] == 0 and "Ano completo 2024" in result["titulo"]
    assert len(result["timestamps"]) == 366 * 96
    assert len(result["tipos_dia"]) == 366
    assert all(b - a == timedelta(minutes=15) for a, b in zip(result["timestamps"], result["timestamps"][1:]))
    assert len(result["resultados_mensais"]) == 12 and result["balanco"] is None
    assert len(result["demandas_max_kw"]) == 12
    offset = 0
    for mes, parte in enumerate(result["resultados_mensais"], 1):
        count = len(parte["timestamps"])
        assert parte["balanco"].energia_importada_kwh == pytest.approx(uc[f"energia_mes_{mes:02d}"])
        for nome in result["series"]:
            np.testing.assert_array_equal(result["series"][nome][offset:offset + count], parte["series"][nome])
        offset += count
    dialog = CurveResultDialog(result)
    qtbot.addWidget(dialog)
    assert "12 meses completos" in dialog._summary()
    assert "DEM_MAX_12" in dialog._summary()
    path = tmp_path / "ano.csv"
    dialog._write_csv(path, {**result["series"], **result["irradiancia"]["series"]})
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert len(rows) == 366 * 96
    assert all(row["id_uc"] == uc["id_uc"] for row in rows)


def test_ene_invalido_vira_demanda_zero(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    uc = service.page(manifest["import_id"], FiltrosUC(busca="ucbt-0000001"), None)["linhas"][0]
    result = service.curva_uc(manifest["import_id"], uc["entidade"], uc["linha_origem"], 0)
    for mes in (2, 3, 4):
        assert result["resultados_mensais"][mes - 1]["demanda_max_kw"] == 0.0
    assert any("ENE ausente" in h for h in result["hipoteses"])


def test_uc_do_alimentador_como_na_correcao_so_ativas_e_linhas_somadas():
    from app.data.repositories.uc import consolidar_ativas
    base = {"entidade": "ucbt", "tipo_curva": "T1", **{f"energia_mes_{m:02d}": 0.0 for m in range(1, 13)}}
    linhas = [
        {**base, "id_uc": "A", "linha_origem": 2, "situacao": "AT", "energia_mes_01": 10.0},
        {**base, "id_uc": "A", "linha_origem": 1, "situacao": "AT", "energia_mes_08": 7.0, "energia_mes_01": None},
        {**base, "id_uc": "A", "linha_origem": 3, "situacao": "DS", "energia_mes_01": 99.0},
        {**base, "id_uc": "A", "entidade": "ucmt", "linha_origem": 4, "situacao": "AT", "energia_mes_01": 5.0},
        {**base, "id_uc": "B", "linha_origem": 5, "situacao": "DS", "energia_mes_01": 1.0},
    ]
    saida = consolidar_ativas(linhas)
    assert [(r["entidade"], r["id_uc"], r["linha_origem"]) for r in saida] == [("ucbt", "A", 1), ("ucmt", "A", 4)]
    assert saida[0]["energia_mes_01"] == 10.0 and saida[0]["energia_mes_08"] == 7.0
    assert saida[0]["linhas_consolidadas"] == [1, 2]
