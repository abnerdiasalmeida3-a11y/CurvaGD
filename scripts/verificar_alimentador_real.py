"""Exemplo completo com um circuito real, sem alterar os tratamentos de trabalho."""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from app.services.application import ApplicationService
from app.data.repositories.uc import FiltrosUC
from app.ui.main_window import MainWindow, CurveResultDialog


class CatalogoDeExemplo:
    def __init__(self, original, workspace):
        self.original, self.workspace = original, workspace

    def __getattr__(self, nome):
        return getattr(self.original, nome)


def main():
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    real = ApplicationService(ROOT / "workspace", ROOT / "workspace/settings.toml")
    exemplo = ROOT / "workspace/verificacao_alimentador_real"
    service = ApplicationService(exemplo, exemplo / "settings.toml")
    service.catalog = CatalogoDeExemplo(real.catalog, exemplo)
    ident = real.versions()[0]["import_id"]
    estado = service.tratar_em_lote(ident, "13", "5221504", 0)
    assert estado["pendentes"] == 0, [(i["uc"]["id_uc"], i["motivo"]) for i in estado["ucs"] if not i["completo"]]
    resultado = service.curva_alimentador(ident, FiltrosUC(subestacao="13", alimentador="5221504"), 0)
    out = ROOT / "outputs/01a0cb63-0a2a-7663-9e82-fb2a345c540b"
    info = service.exportar_alimentador(resultado, out / f"Exemplo_Alimentador_5221504_{resultado['ano']}_15min.xlsx")
    print(json.dumps(info), flush=True)
    report = {**info, "subestacao": "13", "alimentador": "5221504", "ano": resultado["ano"],
              "bt": estado["bt"], "mt": estado["mt"], "pico_kw": resultado["pico_kw"],
              "energia_liquida_kwh": resultado["energia_reconstituida_kwh"], "tratamentos_em_workspace_de_exemplo": True,
              "influencias": resultado["influencias"]}
    (ROOT / "docs/verificacao_alimentador_real.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    def esperar(condicao):
        limite = time.monotonic() + 90
        while not condicao():
            if time.monotonic() > limite:
                raise RuntimeError("A interface não terminou de carregar")
            app.processEvents()
            time.sleep(.02)
    window = MainWindow(service)
    window.resize(1500, 1000)
    window.show()
    esperar(lambda: not window.workers and window.feeder_sub.count() > 1)
    window.navigation.setCurrentRow(2)
    window.feeder_sub.setCurrentIndex(window.feeder_sub.findData("13"))
    esperar(lambda: not window.workers)
    window.feeder_select.setCurrentIndex(window.feeder_select.findData("5221504"))
    esperar(lambda: not window.workers and window._feeder_state is not None)
    app.processEvents()
    window.grab().save(str(ROOT / "docs/alimentador_selecao.png"))
    dialog = CurveResultDialog(resultado, window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(ROOT / "docs/alimentador_curva.png"))
    dialog.chart_tabs.setCurrentIndex(dialog.chart_tabs.count() - 1)
    app.processEvents()
    dialog.grab().save(str(ROOT / "docs/alimentador_influencias.png"))
    dialog.close()
    window.close()


if __name__ == "__main__":
    main()
