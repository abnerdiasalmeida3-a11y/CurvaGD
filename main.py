import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="CurvaGD: curvas de carga e geração distribuída das UCs da BDGD")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--import-source", type=Path)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--distributor", default="ENERGISA_MT")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-ui", action="store_true")
    parser.add_argument("--inspect-catalog", action="store_true")
    args = parser.parse_args()
    if args.inspect_catalog:
        from app.data.repositories.catalogo import Catalogo
        from app.data.repositories.uc import UCRepository, FiltrosUC
        if not args.workspace:
            parser.error("--workspace obrigatório para consultar o catálogo")
        catalog = Catalogo(args.workspace)
        versions = catalog.list()
        if not versions:
            print(json.dumps({"versions": 0, "total_ucs": 0}))
        else:
            page = UCRepository(catalog, versions[0]["import_id"]).page(FiltrosUC())
            print(json.dumps({"versions": len(versions), "total_ucs": page["total"], "page_rows": len(page["linhas"])}))
        return 0
    if args.import_source:
        from app.ingest.pipeline import import_bdgd
        from app.core.logging import setup_logging
        if not args.workspace:
            parser.error("--workspace obrigatório para importação via linha de comando")
        setup_logging(args.workspace)
        manifest = import_bdgd(args.import_source, args.workspace, args.distributor, args.year, validate_only=args.validate_only,
                               progress=lambda stage, current, total: print(f"{stage}: {current}/{total}", flush=True))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.self_test:
        from app.domain.models import PerfisBrutos
        from app.domain.enums import TipoDia
        from app.domain.sintese import normalizar_perfis
        from app.calculo import curvas, motor, gd
        from PySide6.QtCharts import QChart
        import numpy as np
        import pandas as pd
        import pyogrio
        import holidays
        import duckdb
        perfis = normalizar_perfis(PerfisBrutos("T", {d: np.full(96, q, dtype=np.float64) for d, q in zip(TipoDia, (2, 4, 1))}))
        curva = curvas.curva_ano(perfis, 2024)
        fatia = curvas.fatias_mensais(2024)[3]
        sem_gd = motor.balanco_mes(curva[fatia], None, 342.0)
        assert abs(sem_gd.energia_importada_kwh - 342) < 1e-9
        assert "OpenFileGDB" in pyogrio.list_drivers()
        assert holidays.country_holidays("BR", subdiv="MT", years=[2024])
        indice = pd.date_range("2024-01-01", "2025-01-01", freq="15min", inclusive="left")
        hora = indice.hour.to_numpy() + indice.minute.to_numpy() / 60.0
        irrad = pd.Series(0.8 * np.maximum(np.sin((hora - 6.0) * np.pi / 12.0), 0.0), index=indice)
        geracao = gd.geracao_por_kva(6.0 / 5.0, irrad, 15).to_numpy() * 5.0
        fatia = curvas.fatias_mensais(2024)[0]
        com_gd = motor.balanco_mes(curva[fatia], geracao[fatia], 234.0)
        assert len(geracao) == 366 * 96 and com_gd.energia_gerada_kwh > 0
        assert abs(com_gd.residuo_calibracao_kwh) < 1e-7 and abs(com_gd.residuo_balanco_rede_kwh) < 1e-9
        assert QChart is not None
        print(json.dumps({"status": "OK", "energia_kwh": sem_gd.energia_importada_kwh,
                          "energia_solar_jan_kwh": com_gd.energia_gerada_kwh,
                          "dem_max_jan_kw": com_gd.demanda_kw,
                          "gdal": pyogrio.__gdal_version_string__, "duckdb": duckdb.__version__}))
        return 0
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("CurvaGD")
    app.setApplicationName("CurvaGD")
    app.setApplicationDisplayName("CurvaGD")
    app.setFont(QFont("Segoe UI", 10))
    try:
        from app.config.paths import user_workspace, user_config
        from app.config.settings import Settings
        from app.services.application import ApplicationService
        from app.ui.main_window import MainWindow
        config = args.workspace / "settings.toml" if args.workspace else user_config()
        settings = Settings.load(config)
        if args.workspace:
            workspace = args.workspace
        elif getattr(sys, "frozen", False):
            workspace = user_workspace()
        else:
            workspace = Path(settings.workspace) if settings.workspace else user_workspace()
        service = ApplicationService(workspace, config)
        window = MainWindow(service)
    except Exception as exc:
        # O executavel nao tem console: sem esta janela, um erro na abertura so
        # fecharia o programa sem explicacao.
        import traceback
        detalhe = traceback.format_exc()
        try:
            import logging
            logging.getLogger("bdgd").error("Falha ao abrir o CurvaGD\n%s", detalhe)
        except Exception:
            pass
        caixa = QMessageBox(QMessageBox.Icon.Critical, "CurvaGD", f"O CurvaGD não conseguiu abrir.\n\n{exc}")
        caixa.setDetailedText(detalhe)
        caixa.exec()
        return 1
    window.show()
    if args.smoke_ui:
        QTimer.singleShot(3000, window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
