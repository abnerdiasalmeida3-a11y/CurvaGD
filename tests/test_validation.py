from dataclasses import replace
from datetime import date
import json
import socket
from pathlib import Path
import numpy as np
import pyarrow as pa
import pytest
import yaml
from app.ingest.mapping import Fonte
from app.ingest.pipeline import RESOURCES, import_bdgd
from app.ingest.validator import Validador, number, text_value
from app.ingest.profiler import DiagnosticSink, Profiler
from app.ingest.normalizer import normalize_curves
from app.config.calendar_loader import CalendarLoader
from app.domain.enums import CoberturaCalendario
from app.core.errors import ErroBDGD
from app.data.repositories.catalogo import Catalogo
from app.data.repositories.uc import UCRepository, FiltrosUC
from app.data.repositories.curva import get_curve
from app.services.application import DiagnosticarCurvabilidade
from app.domain.calendario import contar_tipos_dia
from app.domain.sintese import sintetizar_mes


@pytest.mark.parametrize("value, expected", [(None, None), (True, None), ("erro", None), ("nan", None), ("inf", None), ("1.234,56", None), ("1,25", 1.25), ("-3", -3), (0, 0)])
def test_numbers(value, expected):
    assert number(value) == expected


def test_validator_edges(source, tmp_path):
    fonte = Fonte(source, RESOURCES / "schemas/bdgd_2024.yaml")
    loader = CalendarLoader(RESOURCES / "calendars/municipios.yaml", 2024)
    sink = DiagnosticSink(tmp_path / "diag.csv")
    val = Validador(sink, loader, 2024, ["S1"], {"F1": "S1"}, ["T1"])
    batch = next(fonte.batches("ucbt", 32))
    rows = batch.to_pylist()
    rows[0].update(COD_ID="", MUN="x", SUB="ERRADA", CLAS_SUB="", GRU_TEN="X", SIT_ATIV="X", CEG_GD="=1+1")
    result = val.convert("ucbt", pa.RecordBatch.from_pylist(rows, schema=batch.schema), fonte.entities["ucbt"]["fields"], 0)
    row = result.to_pylist()[0]
    assert "REFERENCIA_ORFA" in row["status"]
    assert "CALENDARIO_PARCIAL" in row["status"]
    assert sink.counts["DOMINIO_INVALIDO"] == 2
    assert text_value("   ") is None and text_value(" a ") == " a "
    sink.close()


def test_normalization_invalid_and_duplicate(source, tmp_path):
    fonte = Fonte(source, RESOURCES / "schemas/bdgd_2024.yaml")
    sink = DiagnosticSink(tmp_path / "diag.csv")
    val = Validador(sink, CalendarLoader(RESOURCES / "calendars/municipios.yaml", 2024), 2024)
    batch = next(fonte.batches("crvcrg", 32))
    table = val.convert("crvcrg", batch, fonte.entities["crvcrg"]["fields"], 0)
    bad, valid = normalize_curves(pa.concat_tables([table, table.slice(0, 1)]), sink, Profiler(fonte))
    assert not valid and bad.num_rows == 4
    rows = table.to_pylist()
    rows[0]["potencia_01"] = None
    bad, valid = normalize_curves(pa.Table.from_pylist(rows, schema=table.schema), sink, Profiler(fonte))
    assert not valid
    assert sink.counts["PERFIL_INVALIDO"]
    sink.close()


def test_calendar_loader(tmp_path):
    path = tmp_path / "calendar.yaml"
    entry = {"uf": "MT", "cobertura": {"inicio": "2024-01-01", "fim": "2024-12-31", "declarada_completa": True},
             "feriados": [{"data": "2024-02-03", "fonte": "fixture"}],
             "ajustes": [{"data": "2024-01-01", "fonte": "fixture", "acao": "excluir", "justificativa": "teste"}]}
    path.write_text(yaml.safe_dump({"municipios": {"5103403": entry}}), encoding="utf-8")
    loader = CalendarLoader(path, 2024)
    cal = loader.for_municipio("5103403")
    assert date(2024, 2, 3) in cal.feriados["5103403"]
    assert date(2024, 1, 1) not in cal.feriados["5103403"]
    assert cal is loader.for_municipio("5103403")
    assert not loader.for_municipio("x").feriados
    assert cal.cobertura("5103403", date(2024, 1, 1), date(2024, 12, 31)) == CoberturaCalendario.COMPLETA
    for change in [{"uf": "SP"}, {"feriados": [{"data": "2024-02-03"}]}, {"cobertura": {"inicio": "inválido"}}, {"ajustes": [{"data": "2024-02-03", "fonte": "x"}]}]:
        path.write_text(yaml.safe_dump({"municipios": {"5103403": entry | change}}), encoding="utf-8")
        with pytest.raises(ErroBDGD):
            CalendarLoader(path, 2024)


def test_gd_is_metadata_and_partial_survives(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    catalog = Catalogo(work)
    repo = UCRepository(catalog, manifest["import_id"])
    row = repo.page(FiltrosUC())["linhas"][0]
    curve = get_curve(catalog, manifest["import_id"], "T1")
    cal = CalendarLoader(RESOURCES / "calendars/municipios.yaml", 2024).for_municipio("5103403")
    days = contar_tipos_dia(2024, 1, "5103403", cal)
    results = []
    for gd in [None, "", "GD_ALTERADA"]:
        changed = row | {"codigo_gd": gd}
        assert DiagnosticarCurvabilidade(changed) == DiagnosticarCurvabilidade(row)
        results.append(sintetizar_mes(curve, changed["energia_mes_01"], days))
    assert all(r.cobertura_calendario == CoberturaCalendario.PARCIAL for r in results)
    assert len({r.demanda_kw for r in results}) == 1
    invalid = row | {"status": "NAO_CURVAVEL_ENERGIA|CALENDARIO_PARCIAL", "meses_invalidos": [2]}
    assert DiagnosticarCurvabilidade(invalid, [1]) == ["CALENDARIO_PARCIAL"]
    assert "NAO_CURVAVEL_ENERGIA" in DiagnosticarCurvabilidade(invalid, [2])


def test_no_network_during_import_and_queries(source, tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Acesso de rede proibido no teste offline")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    work = tmp_path / "work"
    m = import_bdgd(source, work)
    assert UCRepository(Catalogo(work), m["import_id"]).page(FiltrosUC())["total"] == 11


def test_gdb_csv_equivalence(source, tmp_path):
    import pyogrio
    import pyarrow.csv as csv
    from app.data.db import connection, version_view
    gdb = tmp_path / "fixture.gdb"
    for path in source.glob("*.csv"):
        table = csv.read_csv(path, convert_options=csv.ConvertOptions(column_types={k: pa.string() for k in path.read_text(encoding="utf-8").splitlines()[0].split(",")}))
        pyogrio.write_arrow(table, gdb, layer=path.stem, driver="OpenFileGDB")
    m1 = import_bdgd(source, tmp_path / "csv_work")
    m2 = import_bdgd(gdb, tmp_path / "gdb_work")
    for entity in ("sub", "ctmt", "crvcrg", "ucbt", "ucmt"):
        tables = []
        for folder, manifest in [("csv_work", m1), ("gdb_work", m2)]:
            with connection(tmp_path / folder / "catalog.duckdb") as con:
                tables.append(con.sql(f"SELECT * FROM {version_view(manifest['import_id'], entity)} ORDER BY linha_origem").to_arrow_table())
        assert tables[0].equals(tables[1])
