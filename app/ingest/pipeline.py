from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time
import uuid
import importlib.metadata
import pyarrow as pa
import pyarrow.parquet as pq
from .. import __version__
from ..core.errors import ErroBDGD
from ..core.jobs import Cancelamento, sem_progresso
from ..config.calendar_loader import CalendarLoader
from ..data.repositories.catalogo import Catalogo
from ..data.repositories.bdgd_complemento import preservar_geradores
from ..data.ingest_queries import finalize_ucs, verify_tables
from .mapping import Fonte, ENTITIES
from .validator import Validador, canonical_schema
from .normalizer import normalize_curves
from .profiler import DiagnosticSink, Profiler
from .writer import BatchWriter

RESOURCES = Path(__file__).parents[1] / "config"


def safe_remove_staging(path, workspace):
    root = (workspace / "staging").resolve()
    target = path.resolve()
    if target.parent != root or len(target.name) != 32:
        raise ValueError("Caminho de staging fora do diretório esperado")
    if target.exists():
        shutil.rmtree(target)


def unique_refs(table, field, sink, entity):
    rows = table.to_pylist()
    counts = Counter(r[field] for r in rows)
    for r in rows:
        if r[field] is not None and counts[r[field]] > 1:
            r["status"] = "REFERENCIA_ORFA"
            r["diagnosticos"] = "|".join(filter(None, (r["diagnosticos"], "CHAVE_DUPLICADA")))
            sink.emit("CHAVE_DUPLICADA", "ERRO", entity, r[field], field, r[field], "Chave ambígua; todas as linhas foram preservadas.", r["linha_origem"])
    return pa.Table.from_pylist(rows, schema=table.schema), {r[field]: r for r in rows if r[field] is not None and counts[r[field]] == 1}


def import_bdgd(source_path: Path, workspace: Path, dist="ENERGISA_MT", ano=2024,
                *, schema_path=None, calendar_path=None, batch_size=32768, memory_mb=256,
                token=None, progress=sem_progresso, validate_only=False, fail_catalog=False):
    token = token or Cancelamento()
    token.verificar()
    if not dist.strip() or not 1900 <= ano <= 2200:
        raise ErroBDGD("ESQUEMA_INVALIDO", "Distribuidora e ano-base são obrigatórios.")
    if not 1 <= batch_size <= 65536:
        raise ValueError("Lote fora do limite")
    workspace, source_path = workspace.resolve(), source_path.resolve()
    if workspace == source_path or source_path in workspace.parents:
        raise ErroBDGD("ESQUEMA_INVALIDO", "O armazenamento deve ficar fora da pasta da fonte.")
    for name in ("datasets", "staging", "reports", "logs", "exports"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    import_id = uuid.uuid4().hex
    stage = workspace / "staging" / import_id
    stage.mkdir()
    report_base = f"{dist.encode().hex()}_{ano}_{import_id}"
    report = workspace / "reports" / f"perfil_bdgd_{report_base}.md"
    validation_report = workspace / "reports" / f"validacao_bdgd_{report_base}.md"
    csv_path = workspace / "reports" / f"validacao_bdgd_{report_base}.csv"
    sink = DiagnosticSink(csv_path)
    start = time.perf_counter()
    promoted = None
    try:
        progress("Perfilando estrutura", 0, 0)
        schema_path = Path(schema_path or RESOURCES / "schemas" / "bdgd_2024.yaml")
        calendar_path = Path(calendar_path or RESOURCES / "calendars" / "municipios.yaml")
        source = Fonte(source_path, schema_path)
        profiler = Profiler(source)
        loader = CalendarLoader(calendar_path, ano)
        validator = Validador(sink, loader, ano)
        # Snapshot dos contratos para reprodução da versão, inclusive calendário.
        (stage / "contracts").mkdir()
        shutil.copyfile(schema_path, stage / "contracts" / "schema.yaml")
        shutil.copyfile(calendar_path, stage / "contracts" / "municipios.yaml")
        snapshots, fingerprints = {}, {}
        files = source.input_files()
        for i, path in enumerate(files):
            token.verificar()
            progress("Calculando SHA-256 da fonte", i, len(files))
            before = path.stat()
            snapshots[path] = (before.st_size, before.st_mtime_ns)
            sha = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    token.verificar()
                    sha.update(chunk)
            fingerprints[str(path.relative_to(source.path))] = sha.hexdigest()
        counts, schemas, global_stats = {}, {}, {}
        for entity in ENTITIES:
            token.verificar()
            info = source.entities[entity]
            fields = info["fields"]
            offset = 0
            table_list = []
            directory = stage / ("raw_" + entity if entity.startswith("uc") else entity)
            writer = BatchWriter(directory)
            for batch in source.batches(entity, batch_size):
                token.verificar()
                progress(f"Lendo e validando {entity}", offset, info["info"]["count"] or 0)
                table = validator.convert(entity, batch, fields, offset)
                offset += batch.num_rows
                if entity.startswith("uc"):
                    profiler.observe(entity, table)
                    writer.write(table)
                else:
                    table_list.append(table)
            counts[entity] = offset
            schemas[entity] = str(canonical_schema(entity, fields))
            if info["info"]["count"] is not None and info["info"]["count"] >= 0 and info["info"]["count"] != offset:
                raise ErroBDGD("FONTE_ILEGIVEL", f"Contagem de {entity} mudou durante a leitura.")
            if entity.startswith("uc"):
                if not writer.count:
                    writer.write(pa.Table.from_pylist([], schema=canonical_schema(entity, fields)))
                progress(f"Particionando {entity}", 0, 0)
                global_stats[entity] = finalize_ucs(directory, stage / entity, token, sink, entity, memory_mb)
                # Somente arquivos intermediários criados nesta importação.
                for part in directory.glob("*.parquet"):
                    part.unlink()
                directory.rmdir()
            else:
                table = pa.concat_tables(table_list) if table_list else pa.Table.from_pylist([], schema=canonical_schema(entity, fields))
                if entity == "sub":
                    table, refs = unique_refs(table, "id_subestacao", sink, entity)
                    validator.sub_ids = set(refs)
                elif entity == "ctmt":
                    table, refs = unique_refs(table, "id_alimentador", sink, entity)
                    validator.ctmt_sub = {key: row["subestacao"] for key, row in refs.items()}
                else:
                    table, validator.valid_types = normalize_curves(table, sink, profiler)
                profiler.observe(entity, table)
                writer.write(table)
        preservar_geradores(source.path, stage, token)
        token.verificar()
        progress("Verificando Parquet e contagens", 0, 0)
        verify_tables(stage, counts, token)
        for path, snapshot in snapshots.items():
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != snapshot:
                raise ErroBDGD("FONTE_ILEGIVEL", "A fonte foi alterada durante a importação.")
        text = profiler.markdown(dist, ano, sink, global_stats)
        report.write_text(text, encoding="utf-8")
        validation_report.write_text("# Validação BDGD\n\n" + "\n".join(f"- {code}: {count}" for code, count in sink.counts.items()) + f"\n\nDetalhes: {csv_path.name}\n\nTodos os registros foram preservados.\n", encoding="utf-8")
        parquet = []
        for path in stage.rglob("*.parquet"):
            metadata = pq.read_metadata(path)
            parquet.append({"path": path.relative_to(stage).as_posix(), "rows": metadata.num_rows, "bytes": path.stat().st_size})
        for entity in ENTITIES:
            schemas[entity] = str(pq.read_schema(next((stage / entity).rglob("*.parquet"))))
        manifest = {"import_id": import_id, "distribuidora": dist, "ano_base": ano, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "source": str(source.path), "sha256": fingerprints, "contract_version": source.schema["versao_contrato"], "app_version": __version__,
                    "holidays_version": importlib.metadata.version("holidays"), "contracts_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage / "contracts").iterdir()},
                    "counts_input": counts, "counts_output": counts.copy(), "diagnostics": dict(sink.counts), "severities": dict(sink.severities),
                    "diagnostics_by_entity": {e: dict(v) for e, v in sink.entities.items()}, "canonical_schema": schemas, "parquet": parquet,
                    "status": "READY", "duration_seconds": time.perf_counter() - start, "statistics": global_stats,
                    "profile_statistics": profiler.curves,
                    "reports": {"profile": str(report), "validation": str(validation_report), "csv": str(csv_path)}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        token.verificar()
        if validate_only:
            progress("Validação concluída", 1, 1)
            return manifest
        progress("Promovendo versão", 0, 0)
        token.verificar()
        destination = workspace / "datasets" / f"dist={dist.encode().hex()}" / f"ano={ano}" / f"versao={import_id}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        catalog = Catalogo(workspace)
        stage.rename(destination)
        promoted = destination
        # Região de commit indivisível: após início da promoção o resultado é concluído ou revertido.
        try:
            catalog.publish(manifest, destination, fail=fail_catalog)
        except Exception:
            (destination / "ORFA_RECUPERAVEL.txt").write_text("A promoção do catálogo falhou. Versão anterior preservada. Consulte docs/RECUPERACAO.md.\n", encoding="utf-8")
            raise
        progress("Importação concluída", 1, 1)
        logging.getLogger("bdgd").info("Importação %s READY: %s", import_id, counts)
        return manifest
    except Exception as exc:
        if not isinstance(exc, ErroBDGD) and token.solicitado:
            exc = ErroBDGD("IMPORTACAO_CANCELADA", "Operação cancelada; versão anterior preservada.")
        code = exc.codigo if isinstance(exc, ErroBDGD) else "FONTE_ILEGIVEL"
        sink.emit(code, "ERRO", "importacao", "", "", "", str(exc), detail=repr(exc))
        validation_report.write_text(f"# Importação não concluída\n\n{code}: {exc}\n\nVersão anterior preservada.\n", encoding="utf-8")
        logging.getLogger("bdgd").exception("Falha na importação %s; promovido=%s", import_id, promoted)
        if isinstance(exc, ErroBDGD):
            raise exc
        raise ErroBDGD(code, f"A importação não foi concluída: {exc}") from exc
    finally:
        sink.close()
        safe_remove_staging(stage, workspace)
