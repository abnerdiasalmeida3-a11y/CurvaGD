import json
import subprocess
import sys
from pathlib import Path
import pytest
from app.ingest.pipeline import import_bdgd
from app.data.repositories.catalogo import Catalogo
from app.data.repositories.uc import UCRepository, FiltrosUC
from app.core.jobs import Cancelamento
from app.core.errors import ErroBDGD


def test_import_reopen_and_filters(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work, batch_size=3)
    assert manifest["counts_output"]["ucbt"] == 8
    assert manifest["status"] == "READY"
    assert manifest["diagnostics"]["ENERGIA_INVALIDA"] == 6
    repo = UCRepository(Catalogo(work), manifest["import_id"])
    assert repo.page(FiltrosUC())["total"] == 11
    assert repo.page(FiltrosUC(status="NAO_CURVAVEL_ENERGIA"))["total"] == 2
    assert repo.page(FiltrosUC(status="CALENDARIO_PARCIAL"))["total"] == 11
    assert repo.page(FiltrosUC(busca="' OR 1=1 --"))["total"] == 0
    assert repo.page(FiltrosUC(codigo_gd="sim"))["total"] == 2
    assert repo.choices("municipio") == ["5103403"]
    assert repo.summary(FiltrosUC())["total_ucs"] == 11
    code = "from pathlib import Path; from app.data.repositories.catalogo import Catalogo; print(len(Catalogo(Path(__import__('sys').argv[1])).list()))"
    result = subprocess.run([sys.executable, "-c", code, str(work)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "1"
    assert not list((work / "staging").iterdir())


@pytest.mark.parametrize("stage", ["Perfilando", "Calculando", "Lendo", "Particionando", "Verificando", "Promovendo"])
def test_cancel_preserves_active(source, tmp_path, stage):
    work = tmp_path / "work"
    first = import_bdgd(source, work)
    token = Cancelamento()
    def progress(name, current, total):
        if name.startswith(stage):
            token.cancelar()
    with pytest.raises(ErroBDGD):
        import_bdgd(source, work, token=token, progress=progress, batch_size=3)
    assert Catalogo(work).list()[0]["import_id"] == first["import_id"]
    assert not list((work / "staging").iterdir())


def test_catalog_failure(source, tmp_path):
    work = tmp_path / "work"
    first = import_bdgd(source, work)
    with pytest.raises(ErroBDGD):
        import_bdgd(source, work, fail_catalog=True)
    catalog = Catalogo(work)
    assert catalog.list()[0]["import_id"] == first["import_id"]
    assert len(catalog.orphans()) == 1


def test_missing_column(source, tmp_path):
    path = source / "UCBT_tab.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("TIP_CC", "SEM_TIP"), encoding="utf-8")
    with pytest.raises(ErroBDGD, match="coluna obrigatória") as err:
        import_bdgd(source, tmp_path / "work")
    assert err.value.codigo == "ESQUEMA_INVALIDO"
