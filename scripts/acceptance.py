"""Medições reproduzíveis; sem importar toda a distribuidora em memória."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
import os
import psutil
import winreg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingest.pipeline import import_bdgd
from app.core.logging import setup_logging
from app.data.repositories.catalogo import Catalogo
from app.data.repositories.uc import UCRepository, FiltrosUC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["real", "benchmark"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--reuse-latest", action="store_true", help="Mede a versão READY mais recente sem reimportar")
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    setup_logging(args.workspace)
    process = psutil.Process()
    peak = [0]
    done = threading.Event()
    def sample():
        while not done.wait(0.1):
            peak[0] = max(peak[0], process.memory_info().rss)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    start = time.perf_counter()
    source = args.source
    if args.reuse_latest:
        catalog = Catalogo(args.workspace)
        versions = catalog.list()
        if not versions:
            raise SystemExit("Nenhuma versão registrada no workspace")
        manifest = catalog.manifest(versions[0]["import_id"])
        source = Path(manifest["source"])
        import_seconds = manifest["duration_seconds"]
    elif args.mode == "benchmark":
        from tests.fixtures.generate import fixture
        source = fixture(args.workspace / "synthetic_source", 100000, errors=False)
    last = [None, 0.0]
    def progress(stage, current, total):
        if stage != last[0] or time.monotonic() - last[1] > 5:
            print(f"{stage}: {current}/{total}", flush=True)
            last[:] = [stage, time.monotonic()]
    heartbeat_count = [0]
    if not args.reuse_latest and args.mode == "benchmark":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QEventLoop, QTimer
        from app.ui.jobs import Worker
        from app.ui.main_window import MainWindow
        from app.services.application import ApplicationService
        qapp = QApplication.instance() or QApplication([])
        service = ApplicationService(args.workspace, args.workspace / "settings.toml")
        window = MainWindow(service)
        loop = QEventLoop()
        outcome, errors = [], []
        worker = Worker(lambda token, emit: import_bdgd(source, args.workspace, token=token, progress=emit))
        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(lambda: heartbeat_count.__setitem__(0, heartbeat_count[0] + 1))
        worker.progress.connect(progress)
        worker.result.connect(outcome.append)
        worker.error.connect(errors.append)
        worker.finished.connect(loop.quit)
        timer.start()
        worker.start()
        loop.exec()
        timer.stop()
        worker.wait()
        if errors:
            raise RuntimeError(errors[0])
        manifest = outcome[0]
        window.close()
        while window.workers:
            qapp.processEvents()
    elif not args.reuse_latest:
        manifest = import_bdgd(source, args.workspace, progress=progress)
    if not args.reuse_latest:
        import_seconds = time.perf_counter() - start
    print(f"Importação READY em {import_seconds:.2f}s", flush=True)
    catalog = Catalogo(args.workspace)
    repo = UCRepository(catalog, manifest["import_id"])
    if args.mode == "real":
        assert manifest["counts_output"] == {"sub": 189, "ctmt": 673, "crvcrg": 198, "ucbt": 2115563, "ucmt": 13483}
        assert manifest["statistics"]["ucbt"]["tipologias_usadas"] == 51
    feeder = repo.choices("alimentador")[0]
    filters = FiltrosUC(alimentador=feeder)
    repo.page(filters)
    timings = {}
    for name, f in [("abertura", filters), ("filtro", replace(filters, classe="RE1")), ("ordenacao", replace(filters, ordenar="energia_anual_kwh", descendente=True)), ("pagina", replace(filters, pagina=1))]:
        times = []
        for _ in range(10):
            begin = time.perf_counter()
            repo.page(f)
            times.append(time.perf_counter() - begin)
        timings[name] = {"median_seconds": statistics.median(times), "p95_seconds": sorted(times)[-1], "samples_seconds": times}
        print(f"{name}: p95={max(times):.3f}s", flush=True)
    done.set()
    sampler.join()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu_name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        cpu_name = platform.processor()
    previous_path = ROOT / "docs" / f"aceite_{args.mode}.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if args.reuse_latest and previous_path.exists() else {}
    measured_peak = previous.get("peak_rss_bytes", peak[0]) if args.reuse_latest else peak[0]
    result = {"mode": args.mode, "import_id": manifest["import_id"], "windows": platform.platform(), "cpu": cpu_name,
              "logical_cpus": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total, "disk": str(args.workspace.resolve().anchor),
              "source_bytes": sum(p.stat().st_size for p in source.rglob("*") if p.is_file()), "import_seconds": import_seconds,
              "peak_rss_bytes": measured_peak, "counts": manifest["counts_output"], "queries": timings,
              "qt_heartbeat_ticks_during_import": heartbeat_count[0],
              "memory_pass": measured_peak < 1_000_000_000, "latency_pass": all(t["p95_seconds"] < 1 for t in timings.values())}
    out = ROOT / "docs" / f"aceite_{args.mode}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
