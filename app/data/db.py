from contextlib import contextmanager
import re
import threading
from pathlib import Path
import duckdb
import pyarrow.parquet as pq

ENTITIES = ("sub", "ctmt", "crvcrg", "ucbt", "ucmt")


def version_view(import_id, entity):
    if not re.fullmatch(r"[0-9a-f]{32}", import_id) or entity not in ENTITIES:
        raise ValueError("Identificador de versão/entidade inválido")
    return f"v_{import_id}_{entity}"


_travas = {}
_trava_global = threading.Lock()


def _trava_do_arquivo(path):
    """Uma trava por arquivo DuckDB.

    Abrir e fechar o mesmo arquivo em threads diferentes ao mesmo tempo corre o
    risco de "Unique file handle conflict" no cache de instancias do DuckDB; as
    consultas da interface (tabela, resumo, listas) passam uma de cada vez.
    """
    chave = str(Path(path).resolve())
    with _trava_global:
        return _travas.setdefault(chave, threading.RLock())


@contextmanager
def connection(path=None, token=None, memory_mb=256):
    trava = _trava_do_arquivo(path) if path else None
    if trava is not None:
        while not trava.acquire(timeout=0.2):
            if token:
                token.verificar()
    try:
        con = duckdb.connect(str(path) if path else ":memory:", config={"memory_limit": f"{memory_mb}MB", "threads": "2", "autoinstall_known_extensions": "false", "autoload_known_extensions": "false"})
        try:
            if token:
                token.verificar()
                token.associar(con.interrupt)
            yield con
        finally:
            if token:
                token.associar(None)
            con.close()
    finally:
        if trava is not None:
            trava.release()


def init_catalog(path):
    with connection(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS versions (import_id VARCHAR PRIMARY KEY, distribuidora VARCHAR, ano INTEGER, created_utc VARCHAR, directory VARCHAR, manifest VARCHAR)")
        con.execute("CREATE TABLE IF NOT EXISTS active (distribuidora VARCHAR, ano INTEGER, import_id VARCHAR, PRIMARY KEY(distribuidora, ano))")


def parquet_paths(directory: Path, entity: str):
    return [str(p) for p in sorted((directory / entity).rglob("*.parquet"))]


def register_version_view(con, directory, import_id, entity):
    files = parquet_paths(directory, entity)
    columns = pq.read_schema(files[0]).names
    relation = con.from_parquet(files, hive_partitioning=entity.startswith("uc"))
    # Excluir metadados de diretório (dist/ano/versao) do esquema público.
    relation.project(",".join('CAST(ctmt AS VARCHAR) AS ctmt' if c == 'ctmt' else '"' + c.replace('"', '""') + '"' for c in columns)).create_view(version_view(import_id, entity), replace=True)
