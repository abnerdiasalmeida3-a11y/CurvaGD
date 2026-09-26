"""Gera imagens de revisao dos graficos com dados sinteticos, sem abrir janelas."""
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from app.ui.main_window import CurveResultDialog
from tests.test_ui import _resultado_de_um_mes


def main():
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    dialog = CurveResultDialog(_resultado_de_um_mes())
    dialog.show()
    for nome, tamanho, aba, zoom in [
        ("graficos_comparacao", (1300, 900), dialog.chart_tabs.count() - 2, 16),
        ("graficos_compacto", (980, 680), 0, 1),
        ("graficos_tipicos", (1300, 900), dialog.chart_tabs.count() - 1, 1),
    ]:
        dialog.resize(*tamanho)
        dialog.chart_tabs.setCurrentIndex(aba)
        view = dialog._current_view()
        if zoom != 1:
            view.aplicar_zoom(zoom)
        for _ in range(5):
            app.processEvents()
        if zoom != 1:
            view._cursor = view.chart().plotArea().center()
        destino = ROOT / "docs" / f"{nome}.png"
        dialog.grab().save(str(destino))
        print(destino, flush=True)
    dialog.close()


if __name__ == "__main__":
    main()
