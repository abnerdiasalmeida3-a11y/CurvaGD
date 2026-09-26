"""Verificação opcional do preenchimento com a BDGD e o cache públicos locais."""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from app.services.application import ApplicationService
from app.services.aneel_solar import buscar_potencias
from app.data.repositories.uc import FiltrosUC
from app.ui.parameters import CurveParametersDialog


def main():
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    workspace = ROOT / "workspace"
    service = ApplicationService(workspace, workspace / "settings.toml")
    versao = service.versions()[0]
    linhas = service.page(versao["import_id"], FiltrosUC(codigo_gd="sim"), None)["linhas"][:12]
    resumo = {"consultadas": len(linhas), "encontradas": 0, "ausentes": 0}
    escolhida = None
    for uc in linhas:
        try:
            buscar_potencias(workspace, uc["codigo_gd"])
            resumo["encontradas"] += 1
            escolhida = escolhida or uc
        except Exception:
            resumo["ausentes"] += 1
    assert escolhida, "Nenhuma UC da amostra foi encontrada no cadastro técnico"
    dialog = CurveParametersDialog(tem_gd=True, codigo_gd=escolhida["codigo_gd"], workspace=workspace)
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        app.processEvents()
        if dialog._loaded_powers is not None and dialog._equipment_worker is None:
            break
        time.sleep(.02)
    assert dialog._loaded_powers is not None
    resumo["potencia_modulos_kwp"] = dialog.modules.value()
    resumo["potencia_inversor_kw"] = dialog.inverter.value()
    resumo["preenchimento_tela"] = True
    dialog.resize(880, 950)
    app.processEvents()
    dialog.grab().save(str(ROOT / "docs/automacao_potencias_aneel.png"))
    dialog.close()
    (ROOT / "docs/automacao_potencias_verificacao.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False))


if __name__ == "__main__":
    main()
