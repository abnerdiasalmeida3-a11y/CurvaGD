"""Verifica o EXE unico em pasta isolada e com PATH contendo so System32."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data.repositories.catalogo import Catalogo
from app.data.repositories.uc import FiltrosUC, UCRepository
from tests.fixtures.generate import fixture


def main():
    original = ROOT.parent / "CurvaGD.exe"
    run = Path(tempfile.mkdtemp(prefix="onefile_"))
    aplicativo = run / "aplicativo"
    aplicativo.mkdir()
    exe = aplicativo / "CurvaGD.exe"
    shutil.copy2(original, exe)
    assert list(aplicativo.iterdir()) == [exe]
    temporarios = run / "temporarios"
    temporarios.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PYTHON", "QT_", "GDAL", "PROJ", "BDGD_"))}
    env["PATH"] = str(Path(env.get("SystemRoot", r"C:\Windows")) / "System32")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TEMP"] = env["TMP"] = str(temporarios)
    workspace = run / "workspace"
    fonte = fixture(run / "fonte", size=8)
    etapas = [
        ("calculos_e_recursos_embutidos", ["--self-test"]),
        ("interface_sem_base", ["--smoke-ui", "--workspace", str(workspace)]),
        ("importacao_csv", ["--workspace", str(workspace), "--import-source", str(fonte), "--year", "2024"]),
        ("interface_com_base", ["--smoke-ui", "--workspace", str(workspace)]),
    ]
    with original.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "executavel": str(original),
        "bytes": original.stat().st_size,
        "sha256": digest,
        "pasta_isolada": str(aplicativo),
        "path_somente_system32": True,
        "maquina_windows_limpa_testada": False,
        "testes": [],
        "aprovado": False,
    }
    try:
        for nome, args in etapas:
            inicio = time.perf_counter()
            resultado = subprocess.run([str(exe), *args], cwd=aplicativo, env=env,
                                       capture_output=True, text=True, errors="replace", timeout=180)
            report["testes"].append({"nome": nome, "exit_code": resultado.returncode,
                                     "segundos": round(time.perf_counter() - inicio, 2),
                                     "stderr": resultado.stderr[-3000:]})
            print(f"{nome}: exit={resultado.returncode}", flush=True)
            if resultado.returncode:
                raise RuntimeError(f"Falha em {nome}: {resultado.stderr}")
        catalogo = Catalogo(workspace)
        versoes = catalogo.list()
        assert len(versoes) == 1
        total = UCRepository(catalogo, versoes[0]["import_id"]).page(FiltrosUC())["total"]
        assert total == 11, total
        report["ucs_importadas"] = total
        report["aprovado"] = True
    finally:
        (ROOT / "docs" / "aceite_executavel_unico.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Executavel unico aprovado: calculos, recursos, interface e importacao.", flush=True)


if __name__ == "__main__":
    main()
