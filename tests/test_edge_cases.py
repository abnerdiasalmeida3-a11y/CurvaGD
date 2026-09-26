import csv
import hashlib
from pathlib import Path
import pyarrow as pa
import pytest
from app.ingest.pipeline import import_bdgd
from app.ingest.mapping import Fonte
from app.ingest.readers.csv_reader import inspect_csv, batches
from app.core.errors import ErroBDGD
from app.data.repositories.catalogo import Catalogo
from app.data.repositories.uc import UCRepository, FiltrosUC
from app.config.settings import Settings
from tests.fixtures.generate import fixture


def test_empty_entities_are_queryable(source, tmp_path):
    for filename in ("UCBT_tab.csv", "UCMT_tab.csv"):
        p = source / filename
        p.write_text(p.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    work = tmp_path / "work"
    m = import_bdgd(source, work)
    assert UCRepository(Catalogo(work), m["import_id"]).page(FiltrosUC())["total"] == 0


def test_duplicate_ids_not_removed_and_pages_stable(tmp_path):
    source = fixture(tmp_path / "csv", size=450, errors=False)
    path = source / "UCBT_tab.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[-1]["COD_ID"] = rows[0]["COD_ID"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    work = tmp_path / "work"
    m = import_bdgd(source, work, batch_size=31)
    repo = UCRepository(Catalogo(work), m["import_id"])
    ids = []
    for page in range(3):
        result = repo.page(FiltrosUC(pagina=page, ordenar="classe"))
        ids += [(r["entidade"], r["linha_origem"]) for r in result["linhas"]]
    assert len(ids) == len(set(ids)) == 453
    assert m["diagnostics"]["CHAVE_DUPLICADA"] == 1
    duplicate = repo.page(FiltrosUC(busca=rows[0]["COD_ID"]))
    assert duplicate["total"] == 2
    assert all("CHAVE_DUPLICADA" in r["diagnosticos"] for r in duplicate["linhas"])


def test_validation_only_does_not_publish_and_manifest_hashes(source, tmp_path):
    work = tmp_path / "work"
    m = import_bdgd(source, work, validate_only=True)
    assert not (work / "catalog.duckdb").exists()
    assert m["sha256"]["CTMT.csv"] == hashlib.sha256((source / "CTMT.csv").read_bytes()).hexdigest()
    assert not list((work / "staging").iterdir())
    assert Path(m["reports"]["profile"]).exists()


def test_utf8_boundary_and_csv_decimal(tmp_path):
    p = tmp_path / "dados.csv"
    # O primeiro byte de 'á' termina a amostra inicial de 64 KiB.
    p.write_bytes(b"name;value\n" + b"a" * (65535 - len(b"name;value\n")) + "á;1,25\n".encode())
    info = inspect_csv(p)
    assert info["encoding"] == "utf-8-sig"
    result = list(batches(p, info, ["name", "value"], 32))
    assert result[0].column(0)[0].as_py().endswith("á")


def test_malformed_schema(source, tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("entidades: []", encoding="utf-8")
    with pytest.raises(ErroBDGD) as exc:
        Fonte(source, p)
    assert exc.value.codigo == "ESQUEMA_INVALIDO"


def test_settings_roundtrip_and_invalid(tmp_path):
    path = tmp_path / "settings.toml"
    s = Settings(workspace=str(tmp_path / "dados com espaços"), batch_size=8000)
    s.save(path)
    assert Settings.load(path) == s
    with pytest.raises(ValueError):
        Settings(batch_size=999999)
