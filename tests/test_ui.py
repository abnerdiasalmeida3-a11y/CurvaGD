from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtCharts import QChartView
from app.domain.calendario import CalendarioMunicipal, rotular_dias
from app.services.application import ApplicationService
from app.ui.charts import PainelCurvasTipicas, SeletorDeSeries
from app.ui.main_window import MainWindow, CurveResultDialog
from app.ui.main_window import CurveParametersDialog
from app.ui.models.uc_table_model import UCTableModel


@pytest.mark.parametrize("tem_gd,somente_mes", [(False, False), (True, False), (False, True)])
def test_parametros_permitem_ano_completo_e_meses_individuais(qtbot, tem_gd, somente_mes):
    dialog = CurveParametersDialog(tem_gd=tem_gd, somente_mes=somente_mes)
    qtbot.addWidget(dialog)
    assert dialog.month.count() == 13
    assert dialog.values()["mes"] == 0
    assert "12 meses" in dialog.month.currentText()
    for mes in range(1, 13):
        dialog.month.setCurrentIndex(dialog.month.findData(mes))
        assert dialog.values()["mes"] == mes


def _janela_importada(qtbot, source, tmp_path):
    from app.config.recursos import irradiancia_padrao
    work = tmp_path / "work"
    service = ApplicationService(work, work / "settings.toml")
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    # antes de importar so a tela de dados de entrada fica liberada
    assert window.windowTitle() == "CurvaGD"
    assert all(not window.navigation.item(i).flags() & Qt.ItemFlag.ItemIsEnabled for i in range(1, 5))
    window.source.setText(str(source))
    window.nasa_field.setText(str(irradiancia_padrao()))
    window.start_import()
    qtbot.waitUntil(lambda: service.base_carregada and window._regioes_prontas and not window.workers, timeout=60000)
    return service, window


def test_ui_navigation_filters_and_pagination(qtbot, source, tmp_path):
    # O plugin offscreen do Qt não enumera fontes do Windows automaticamente.
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QApplication.instance().setFont(QFont("Segoe UI", 10))
    service, window = _janela_importada(qtbot, source, tmp_path)
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())] == [
        "Dados de entrada", "Regiões", "Mapa da rede", "Unidades consumidoras", "Correção de demanda"]
    assert all(window.navigation.item(i).flags() & Qt.ItemFlag.ItemIsEnabled for i in range(5))
    assert "Importação concluída" in window.import_log.toPlainText()
    for page in range(5):
        window.navigation.setCurrentRow(page)
        assert window.pages.currentIndex() == page
    window.navigation.setCurrentRow(3)
    # 11 linhas na fonte: as 11 estao ativas (SIT_ATIV = AT)
    qtbot.waitUntil(lambda: window.total == 11 and not window.workers, timeout=15000)
    window.search.setText("ucbt-0000000")
    qtbot.waitUntil(lambda: window.total == 1 and not window.workers, timeout=15000)
    assert window.model.rowCount() == 1
    window.search.setText("inexistente")
    qtbot.waitUntil(lambda: window.total == 0 and not window.workers, timeout=15000)
    assert window.model.rowCount() == 0
    window.search.clear()
    qtbot.waitUntil(lambda: window.total == 11 and not window.workers, timeout=15000)
    window.gd_filter.setCurrentIndex(1)
    qtbot.waitUntil(lambda: window.total == 2 and not window.workers, timeout=15000)
    window.gd_filter.setCurrentIndex(0)
    qtbot.waitUntil(lambda: window.total == 11 and not window.workers, timeout=15000)
    # a regiao escolhida vale para a tabela: o alimentador F1 tem 9 UCs (2 estao no ORFA)
    window.municipio.setCurrentIndex(window.municipio.findData("5103403"))
    assert window.subestacao.findData("S1") >= 0
    window.subestacao.setCurrentIndex(window.subestacao.findData("S1"))
    assert [window.alimentador.itemData(i) for i in range(window.alimentador.count())] == ["", "F1"]
    window.alimentador.setCurrentIndex(window.alimentador.findData("F1"))
    qtbot.waitUntil(lambda: window.total == 9 and not window.workers, timeout=15000)
    assert "Alimentador piloto" in window.selection_label.text()
    assert window.map_sub.currentData() == "S1" and window.map_feeder.currentData() == "F1"
    assert window.correcao.seletor_ctmt.currentData() == "F1"
    # o menu lateral pode ser recolhido e reaberto
    window.menu_toggle.setChecked(False)
    assert not window.navigation.isVisible()
    window.menu_toggle.setChecked(True)
    assert window.navigation.isVisible()
    window.grab().save(str(tmp_path / "interface_teste.png"))
    window.close()


def test_import_pede_os_dados_de_entrada(qtbot, tmp_path):
    service = ApplicationService(tmp_path, tmp_path / "settings.toml")
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.nasa_field.clear()
    window.start_import()
    assert "BDGD" in window.notice.text() and "NASA" in window.notice.text()
    assert "import" not in window.jobs
    window.source.setText(str(tmp_path / "nao-existe.gdb"))
    window.nasa_field.setText(str(tmp_path / "nao-existe.csv"))
    window.start_import()
    qtbot.waitUntil(lambda: not window.workers, timeout=15000)
    assert "não foi concluída" in window.import_log.toPlainText()
    assert not service.base_carregada
    window.close()


def test_janela_reabre_a_ultima_importacao(qtbot, source, tmp_path):
    service, window = _janela_importada(qtbot, source, tmp_path)
    window.close()
    reaberto = ApplicationService(service.workspace, service.workspace / "settings.toml")
    nova = MainWindow(reaberto)
    qtbot.addWidget(nova)
    qtbot.waitUntil(lambda: nova._regioes_prontas and not nova.workers, timeout=30000)
    assert nova.source.text() == str(source)
    assert "Base carregada" in nova.import_log.toPlainText()
    assert nova.navigation.item(1).flags() & Qt.ItemFlag.ItemIsEnabled
    nova.close()


def test_model_cache_is_bounded(qtbot):
    model = UCTableModel()
    model.set_rows([{"id_uc": str(i)} for i in range(200)])
    assert model.rowCount() == 200
    with pytest.raises(ValueError):
        model.set_rows([{}] * 201)


def test_dialogo_de_curva_renderiza_serie_temporal(qtbot):
    inicio = datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-4)))
    resultado = {
        "tipo": "alimentador",
        "titulo": "Alimentador F1 - 01/2024",
        "timestamps": tuple(inicio + timedelta(minutes=15 * i) for i in range(8)),
        "series": {"Carga do alimentador (kW)": np.arange(8, dtype=float)},
        "unidades_incluidas": 2,
        "energia_incluida_kwh": 7.0,
        "energia_reconstituida_kwh": 7.0,
        "unidades_energia_invalida": 0,
        "grupos_omitidos": (),
        "hipoteses": (),
    }
    dialog = CurveResultDialog(resultado)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowTitle() == resultado["titulo"]
    chart = dialog.findChild(QChartView)
    assert chart is not None and len(chart.chart().series()) == 1
    assert dialog.chart_tabs.count() == 1


@pytest.mark.parametrize("com_solar", [False, True])
def test_dialogo_mostra_curvas_separadas_e_juntas(qtbot, tmp_path, monkeypatch, com_solar):
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QApplication.instance().setFont(QFont("Segoe UI", 10))
    inicio = datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-4)))
    timestamps = tuple(inicio + timedelta(minutes=15 * i) for i in range(8))
    resultado = {
        "tipo": "alimentador",
        "titulo": "Curvas da UC",
        "timestamps": timestamps,
        "series": {
            "Carga sem GD (kW)": np.arange(8, dtype=float),
            "Geração da GD (kW)": np.arange(8, dtype=float) / 2,
            "Curva unificada (kW)": np.arange(8, dtype=float) / 2,
        },
        "unidades_incluidas": 1,
        "energia_incluida_kwh": 7.0,
        "energia_reconstituida_kwh": 7.0,
        "unidades_energia_invalida": 0,
        "grupos_omitidos": (),
        "hipoteses": (),
    }
    if com_solar:
        resultado.update(
            uc={"id_uc": "UC-SOLAR", "entidade": "ucbt", "linha_origem": 3},
            codigo_gd="GD1",
            irradiancia={
                "series": {"GHI - horizontal (W/m²)": np.arange(8) * 100.0,
                           "POA - plano dos módulos (W/m²)": np.arange(8) * 110.0},
                "arquivo": "nasa.csv", "latitude": -15.5929, "longitude": -56.0925,
            },
        )
    dialog = CurveResultDialog(resultado)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.chart_tabs.count() == (5 if com_solar else 4)
    assert dialog.chart_tabs.tabText(dialog.chart_tabs.count() - 1) == "Todas juntas"
    assert [len(view.chart().series()) for view in dialog.chart_views] == ([1, 1, 1, 2, 3] if com_solar else [1, 1, 1, 3])
    if com_solar:
        import csv
        from PySide6.QtWidgets import QFileDialog, QTableWidget
        assert dialog.chart_tabs.tabText(3) == "Irradiância solar"
        dialog.chart_tabs.setCurrentIndex(3)
        assert dialog._current_chart().axes(Qt.Orientation.Vertical)[0].titleText() == "Irradiância (W/m²)"
        table = dialog.chart_tabs.widget(3).findChild(QTableWidget)
        assert table.rowCount() == 8
        assert table.item(0, 0).text() == timestamps[0].isoformat(sep=" ")
        path = tmp_path / "irradiancia.csv"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "CSV (*.csv)"))
        dialog.export_irradiance_csv()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
        assert len(rows) == 8
        assert all(row["id_uc"] == "UC-SOLAR" and row["codigo_gd"] == "GD1" for row in rows)
        assert rows[-1]["GHI - horizontal (W/m²)"] == "700,000000000"
        assert "Carga sem GD (kW)" not in rows[0]
        dialog.export_csv()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
        assert "Carga sem GD (kW)" in rows[0] and "GHI - horizontal (W/m²)" in rows[0]
        dialog.grab().save(str(tmp_path / "irradiancia_ui.png"))
    dialog.chart_tabs.setCurrentIndex(dialog.chart_tabs.count() - 1)
    before = dialog._current_chart().axes(Qt.Orientation.Horizontal)[0].max()
    dialog._current_chart().zoom(2.0)
    after = dialog._current_chart().axes(Qt.Orientation.Horizontal)[0].max()
    assert after < before


def _resultado_de_um_mes(com_tipos_dia=True):
    """Resultado sintetico de janeiro/2024 com forma diferente por tipo de dia."""
    fuso = timezone(timedelta(hours=-4))
    inicio = datetime(2024, 1, 1, tzinfo=fuso)
    total = 31 * 96
    timestamps = tuple(inicio + timedelta(minutes=15 * i) for i in range(total))
    municipio = "5103403"
    calendario = CalendarioMunicipal({municipio: frozenset({date(2024, 1, 1), date(2024, 1, 25)})})
    tipos = rotular_dias(2024, 1, municipio, calendario)
    hora = (np.arange(total) % 96) / 4.0
    ganho = {"Dia útil": 1.0, "Sábado": 0.8, "Domingo": 0.6, "Feriado": 0.5}
    carga = np.concatenate([
        ganho[rotulo] * (1.0 + 0.5 * np.sin((hora[dia * 96:(dia + 1) * 96] - 6) / 24 * 2 * np.pi))
        for dia, rotulo in enumerate(tipos)
    ])
    geracao = np.clip(np.sin((hora - 6) / 12 * np.pi), 0, None) * 3.0
    resultado = {
        "tipo": "alimentador",
        "titulo": "UC ucbt-0000000 - 01/2024",
        "ano": 2024,
        "mes": 1,
        "timestamps": timestamps,
        "series": {
            "Carga sem GD (kW)": carga,
            "Geração da GD (kW)": geracao,
            "Curva unificada - carga menos GD (kW)": carga - geracao,
        },
        "unidades_incluidas": 1,
        "energia_incluida_kwh": 342.0,
        "energia_reconstituida_kwh": 342.0,
        "unidades_energia_invalida": 0,
        "grupos_omitidos": (),
        "hipoteses": (),
    }
    if com_tipos_dia:
        resultado["tipos_dia"] = tipos
        resultado["municipio_calendario"] = municipio
    return resultado


def test_aba_de_curvas_tipicas_compara_os_tipos_de_dia(qtbot):
    dialog = CurveResultDialog(_resultado_de_um_mes())
    qtbot.addWidget(dialog)
    dialog.show()
    abas = [dialog.chart_tabs.tabText(i) for i in range(dialog.chart_tabs.count())]
    assert abas[-1] == "Curvas típicas"
    painel = dialog.chart_tabs.widget(dialog.chart_tabs.count() - 1)
    assert isinstance(painel, PainelCurvasTipicas)
    assert list(painel.caixas) == ["Dia útil", "Sábado", "Domingo", "Feriado"]
    assert painel.caixas["Dia útil"].text() == "Dia útil (21)"
    assert painel.caixas["Feriado"].text() == "Feriado (2)"
    assert [painel.combo.itemData(i) for i in range(painel.combo.count())] == list(_resultado_de_um_mes()["series"])

    # Uma curva por tipo de dia marcado, com 96 pontos (um dia) e a faixa min-max.
    assert painel.resumo.rowCount() == 4
    assert len(painel.view.series_disponiveis()) == 4
    linhas = [serie for serie in painel.view.chart().series() if hasattr(serie, "count")]
    assert len(linhas) == 4 and all(serie.count() == 96 for serie in linhas)
    assert len(painel.instantes) == 96
    dialog.chart_tabs.setCurrentIndex(dialog.chart_tabs.count() - 1)
    assert dialog._current_view() is painel.view
    assert not dialog.summary.isVisible()   # o quadro comparativo substitui o resumo

    # Desmarcar um tipo de dia remove a curva e a linha do quadro.
    painel.caixas["Feriado"].setChecked(False)
    assert painel.resumo.rowCount() == 3
    assert len(painel.view.series_disponiveis()) == 3
    assert dialog._current_view() is painel.view

    # Sem a faixa o grafico fica so com as linhas medias.
    com_faixa = len(painel.view.chart().series())
    painel.faixa.setChecked(False)
    assert len(painel.view.chart().series()) == 3 < com_faixa

    # A curva de potencia e a de irradiancia usam titulos de eixo diferentes.
    painel.combo.setCurrentIndex(1)
    assert painel.view.chart().axes(Qt.Orientation.Vertical)[0].titleText() == "Potência (kW)"

    # Dia util consome mais que domingo neste resultado sintetico.
    tipicas = painel.tipicas["Carga sem GD (kW)"]
    assert tipicas["Dia útil"].energia_media_kwh > tipicas["Domingo"].energia_media_kwh
    assert tipicas["Domingo"].dias == 4 and tipicas["Feriado"].dias == 2


def test_resultado_sem_calendario_nao_ganha_aba_de_tipicas(qtbot):
    dialog = CurveResultDialog(_resultado_de_um_mes(com_tipos_dia=False))
    qtbot.addWidget(dialog)
    assert "Curvas típicas" not in [dialog.chart_tabs.tabText(i) for i in range(dialog.chart_tabs.count())]
    assert dialog.typical_panel is None


def test_seletor_de_curvas_e_navegacao_no_tempo(qtbot):
    dialog = CurveResultDialog(_resultado_de_um_mes())
    qtbot.addWidget(dialog)
    dialog.show()
    indice = [dialog.chart_tabs.tabText(i) for i in range(dialog.chart_tabs.count())].index("Todas juntas")
    dialog.chart_tabs.setCurrentIndex(indice)
    vista = dialog._current_view()
    seletor = dialog.chart_tabs.widget(indice).findChild(SeletorDeSeries)
    assert sorted(seletor.selecionadas()) == sorted(_resultado_de_um_mes()["series"])

    eixo_y = vista.chart().axes(Qt.Orientation.Vertical)[0]
    assert eixo_y.min() < 0  # a curva liquida fica negativa ao meio-dia
    seletor.caixas["Geração da GD (kW)"].setChecked(False)
    seletor.caixas["Curva unificada - carga menos GD (kW)"].setChecked(False)
    assert seletor.selecionadas() == ["Carga sem GD (kW)"]
    assert eixo_y.min() == 0.0  # so a carga: o eixo se reenquadra e o zero continua marcado

    # A ultima curva nao pode ser desligada.
    seletor.caixas["Carga sem GD (kW)"].setChecked(False)
    assert seletor.selecionadas() == ["Carga sem GD (kW)"]

    cheia = vista.janela()
    vista.aplicar_zoom(6.0)
    ampliada = vista.janela()
    assert ampliada[1] - ampliada[0] < (cheia[1] - cheia[0]) / 3
    vista.deslocar(-100)
    assert vista.janela()[0] >= cheia[0] - 1        # nao passa do inicio dos dados
    vista.deslocar(100)
    assert vista.janela()[1] <= cheia[1] + 1        # nem do fim
    vista.restaurar()
    assert vista.janela() == pytest.approx(cheia, abs=1000)

    eixo_x = vista.chart().axes(Qt.Orientation.Horizontal)[0]
    dialog._navegar("aplicar_zoom", 4.0)
    assert eixo_x.max().toMSecsSinceEpoch() - eixo_x.min().toMSecsSinceEpoch() < cheia[1] - cheia[0]
    dialog._navegar("restaurar")
    assert vista.janela() == pytest.approx(cheia, abs=1000)
