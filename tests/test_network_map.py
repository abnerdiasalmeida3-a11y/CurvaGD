import struct
import shutil

from app.data.repositories.uc import UCRepository
from app.data.repositories.catalogo import Catalogo
from app.ingest.pipeline import import_bdgd
from app.services.network_map import _geometria_linhas, filtrar_alimentador


def test_opcoes_de_regiao_mostram_codigo_e_nome(source, tmp_path):
    workspace = tmp_path / "work"
    manifest = import_bdgd(source, workspace)
    repo = UCRepository(Catalogo(workspace), manifest["import_id"])
    assert ("Cuiabá · 5103403", "5103403") in repo.named_choices("municipio")
    assert ("Subestação piloto · S1", "S1") in repo.named_choices("subestacao")
    assert ("F1 · Alimentador piloto", "F1") in repo.named_choices("alimentador")


def test_rede_filtra_alimentador_sem_perder_codigo():
    line = struct.pack("<BII4d", 1, 2, 2, -56.0, -15.0, -55.9, -15.1)
    multi = struct.pack("<BII", 1, 5, 1) + line
    assert _geometria_linhas(multi) == [[(-56.0, -15.0), (-55.9, -15.1)]]
    full = {"subestacao": "10", "alimentador": "", "limites": [-56.0, -15.1, -55.8, -15.0],
            "linhas_mt": [[(-56.0, -15.0), (-55.9, -15.1)]],
            "linhas_bt": [[(-55.9, -15.1), (-55.8, -15.0)]],
            "codigos_mt": ["F1"], "codigos_bt": ["F2"],
            "trafos": [("F1", -56.0, -15.0)],
            "alimentadores": {"F1": {"mt": 1, "bt": 0, "mt_km": 12.0, "bt_km": 0.0, "trafos": 1},
                                "F2": {"mt": 0, "bt": 1, "mt_km": 0.0, "bt_km": 12.0, "trafos": 0}}}
    selected = filtrar_alimentador(full, "F1")
    assert selected["linhas_mt"] == full["linhas_mt"]
    assert selected["linhas_bt"] == []
    assert list(selected["alimentadores"]) == ["F1"]
    assert selected["trafos"] == full["trafos"]


def test_catalogo_reencontra_dataset_apos_mover_workspace(source, tmp_path):
    original = tmp_path / "original"
    manifest = import_bdgd(source, original)
    moved = tmp_path / "movido"
    shutil.move(str(original), str(moved))
    catalog = Catalogo(moved)
    versions = catalog.list()
    assert versions[0]["directory"].startswith(str(moved))
    repo = UCRepository(catalog, manifest["import_id"])
    assert ("F1 · Alimentador piloto", "F1") in repo.named_choices("alimentador")
