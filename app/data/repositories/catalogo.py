import json
from pathlib import Path
import shutil
from ..db import connection, init_catalog, ENTITIES, parquet_paths, version_view, register_version_view


class Catalogo:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = workspace / "catalog.duckdb"
        init_catalog(self.path)
        self._relink_local_datasets()

    def _relink_local_datasets(self):
        """Reaponta views quando um workspace copiado traz caminhos absolutos antigos."""
        with connection(self.path) as con:
            versions = con.execute("SELECT import_id, directory FROM versions").fetchall()
            changes = []
            for import_id, directory in versions:
                candidates = list((self.workspace / "datasets").glob(
                    f"dist=*/ano=*/versao={import_id}"))
                if len(candidates) == 1 and candidates[0].resolve() != Path(directory).resolve():
                    if all(parquet_paths(candidates[0], entity) for entity in ENTITIES):
                        changes.append((import_id, candidates[0].resolve()))
            if not changes:
                return
            con.execute("BEGIN TRANSACTION")
            try:
                for import_id, directory in changes:
                    for entity in ENTITIES:
                        register_version_view(con, directory, import_id, entity)
                    con.execute("UPDATE versions SET directory=? WHERE import_id=?",
                                [str(directory), import_id])
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def publish(self, manifest, directory: Path, fail=False):
        with connection(self.path) as con:
            con.execute("BEGIN TRANSACTION")
            try:
                for entity in ENTITIES:
                    register_version_view(con, directory, manifest["import_id"], entity)
                con.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)", [manifest["import_id"], manifest["distribuidora"], manifest["ano_base"], manifest["created_utc"], str(directory), json.dumps(manifest, ensure_ascii=False)])
                if fail:
                    raise RuntimeError("Falha injetada no catálogo")
                con.execute("INSERT OR REPLACE INTO active VALUES (?, ?, ?)", [manifest["distribuidora"], manifest["ano_base"], manifest["import_id"]])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def list(self):
        with connection(self.path) as con:
            cur = con.execute("SELECT v.import_id, v.distribuidora, v.ano, v.created_utc, a.import_id IS NOT NULL AS ativa, v.directory FROM versions v LEFT JOIN active a ON v.import_id=a.import_id ORDER BY v.created_utc DESC")
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def manifest(self, import_id):
        with connection(self.path) as con:
            row = con.execute("SELECT manifest FROM versions WHERE import_id=?", [import_id]).fetchone()
            return json.loads(row[0]) if row else None

    def remover(self, import_id):
        """Tira a importacao do catalogo e apaga a sua pasta de dados.

        So apaga pastas dentro de ``workspace/datasets``: um catalogo com caminho
        estranho nunca leva o programa a apagar nada fora do seu armazenamento.
        """
        with connection(self.path) as con:
            row = con.execute("SELECT directory FROM versions WHERE import_id=?", [import_id]).fetchone()
            if row is None:
                return
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute("DELETE FROM active WHERE import_id=?", [import_id])
                con.execute("DELETE FROM versions WHERE import_id=?", [import_id])
                for entity in ENTITIES:
                    con.execute(f"DROP VIEW IF EXISTS {version_view(import_id, entity)}")
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        raiz = (self.workspace / "datasets").resolve()
        candidatos = [Path(row[0])] + list((self.workspace / "datasets").glob(f"dist=*/ano=*/versao={import_id}"))
        for pasta in candidatos:
            try:
                pasta = pasta.resolve()
            except OSError:
                continue
            if pasta.is_dir() and raiz in pasta.parents and pasta.name == f"versao={import_id}":
                shutil.rmtree(pasta, ignore_errors=True)

    def orphans(self):
        known = {v["import_id"] for v in self.list()}
        return [str(p.parent) for p in (self.workspace / "datasets").rglob("manifest.json") if p.parent.name.removeprefix("versao=") not in known]

    def rebuild_views(self):
        """Regenera apenas views sobre as versões registradas, sem consultar a fonte."""
        versions = self.list()
        with connection(self.path) as con:
            con.execute("BEGIN TRANSACTION")
            try:
                for version in versions:
                    for entity in ENTITIES:
                        register_version_view(con, Path(version["directory"]), version["import_id"], entity)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
