"""Regressoes de navegacao, escala e leitura dos graficos."""
from datetime import datetime, timedelta

import numpy as np
import pytest
from PySide6.QtCore import Qt

from app.ui.charts import construir_grafico, escala_com_zero
from app.ui.main_window import CurveResultDialog
from tests.test_ui import _resultado_de_um_mes


@pytest.mark.parametrize("valores", [[0], [2, 10], [-10, -2], [-4, 8], [0.001, 0.004]])
def test_escala_inclui_dados_e_zero_em_divisao_exata(valores):
    inferior, superior, passo, _ = escala_com_zero(valores)
    assert inferior <= min(0, *valores)
    assert superior >= max(0, *valores)
    assert superior > inferior
    assert -inferior / passo == pytest.approx(round(-inferior / passo))


@pytest.mark.parametrize("valores", [[2, 10], [-10, -2], [-4, 8]])
def test_zoom_vertical_responde_a_cada_passo_e_mantem_zero(qtbot, valores):
    instantes = [datetime(2024, 1, 1) + timedelta(minutes=15 * i) for i in range(96)]
    view = construir_grafico({"Carga": np.linspace(*valores, 96)}, instantes, "Carga")
    qtbot.addWidget(view)
    eixo = view.chart().axes(Qt.Orientation.Vertical)[0]
    inicial = (eixo.min(), eixo.max())
    for _ in range(4):
        amplitude = eixo.max() - eixo.min()
        view.aplicar_zoom_vertical(1.25)
        assert eixo.max() - eixo.min() == pytest.approx(amplitude / 1.25)
        assert eixo.min() <= 0 <= eixo.max()
    view.restaurar()
    assert (eixo.min(), eixo.max()) == pytest.approx(inicial)


def test_cores_e_estado_acompanham_aba_e_navegacao(qtbot):
    dialog = CurveResultDialog(_resultado_de_um_mes())
    qtbot.addWidget(dialog)
    dialog.show()
    juntos = dialog.chart_views[-1]
    for individual, leitura in zip(dialog.chart_views, juntos._leituras):
        assert individual.chart().series()[0].pen().color() == leitura["cor"]
    dialog.chart_tabs.setCurrentIndex(dialog.chart_tabs.count() - 2)
    antes = dialog.periodo_visivel.text()
    dialog._navegar("aplicar_zoom", 6)
    assert dialog.periodo_visivel.text() != antes
    dialog._navegar("aplicar_zoom_vertical", 1.25)
    assert not dialog.escala_automatica.isChecked()
    eixo = juntos.chart().axes(Qt.Orientation.Vertical)[0]
    escala = (eixo.min(), eixo.max())
    dialog._navegar("deslocar", 0.25)
    assert (eixo.min(), eixo.max()) == escala
    dialog.escala_automatica.setChecked(True)
    assert juntos._y_automatico
    dialog._navegar("restaurar")
    assert dialog.periodo_visivel.text() == antes


def test_tipicas_preservam_periodo_e_ultima_selecao(qtbot):
    dialog = CurveResultDialog(_resultado_de_um_mes())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.chart_tabs.setCurrentIndex(dialog.chart_tabs.count() - 1)
    painel = dialog.typical_panel
    painel.view.aplicar_zoom(3)
    janela = painel.view.janela()
    painel.faixa.setChecked(False)
    assert painel.view.janela() == janela
    for caixa in painel.caixas.values():
        caixa.setChecked(False)
    assert sum(caixa.isChecked() for caixa in painel.caixas.values()) == 1
    assert painel.resumo.rowCount() == 1
    painel.view.aplicar_zoom_vertical(1.25)
    assert not dialog.escala_automatica.isChecked()


def test_rotulos_se_adaptam_ao_espaco_e_tooltip_exibe_unidade(qtbot, monkeypatch):
    inicio = datetime(2024, 1, 1)
    instantes = [inicio + timedelta(minutes=15 * i) for i in range(96 * 7)]
    view = construir_grafico({"GHI": np.ones(len(instantes))}, instantes,
                             "Irradiância", "Irradiância (W/m²)")
    qtbot.addWidget(view)
    view.resize(1250, 500)
    view.show()
    qtbot.wait(50)
    eixo = view.chart().axes(Qt.Orientation.Horizontal)[0]
    largo = eixo.tickCount()
    view.resize(540, 400)
    qtbot.wait(50)
    assert 2 <= eixo.tickCount() < largo
    capturado = []
    monkeypatch.setattr(view, "_pintar_balao", lambda painter, area, x, linhas, cores: capturado.extend(linhas))
    view._cursor = view.chart().plotArea().center()
    view.grab()
    assert any("1,00 W/m²" in linha for linha in capturado)
    assert view._unidade == "W/m²"
