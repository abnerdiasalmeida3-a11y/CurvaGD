"""SQL de validação global e materialização, fora da interface e do domínio."""
from pathlib import Path
import pyarrow.parquet as pq
from .db import connection


def finalize_ucs(raw: Path, output: Path, token, sink, entity, memory_mb):
    files = [str(p) for p in sorted(raw.glob("*.parquet"))]
    output.mkdir(parents=True, exist_ok=True)
    with connection(token=token, memory_mb=memory_mb) as con:
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET threads=1")
        con.from_parquet(files, hive_partitioning=False).create_view("raw_uc")
        con.execute("CREATE TEMP TABLE duplicate_ids AS SELECT id_uc, count(*) AS n FROM raw_uc WHERE id_uc IS NOT NULL GROUP BY id_uc HAVING count(*)>1")
        duplicates = con.execute("SELECT * FROM duplicate_ids").fetchall()
        for key, count in duplicates:
            sink.emit("CHAVE_DUPLICADA", "ERRO", entity, key, "id_uc", key, f"{count} registros preservados; identidade desambiguada pela linha de origem.")
        # Manter todas as linhas e anexar diagnóstico em cada duplicata.
        con.execute("""CREATE TEMP VIEW checked AS
            SELECT r.* EXCLUDE (diagnosticos),
              CASE WHEN n>1 AND id_uc IS NOT NULL THEN concat_ws('|', nullif(diagnosticos,''), 'CHAVE_DUPLICADA') ELSE diagnosticos END diagnosticos,
              CASE WHEN alimentador IS NULL OR trim(alimentador)='' THEN '__SEM_ALIMENTADOR__' ELSE hex(encode(alimentador)) END ctmt
            FROM raw_uc r LEFT JOIN duplicate_ids USING (id_uc)""")
        token.verificar()
        # Um escritor por vez: não manter buffers Parquet de centenas de partições.
        reader = con.sql("SELECT * FROM checked ORDER BY ctmt, id_uc, linha_origem").to_arrow_reader(8192)
        writer, current_partition = None, None
        try:
            for batch in reader:
                token.verificar()
                partitions = batch.column(batch.schema.get_field_index("ctmt")).to_pylist()
                start = 0
                while start < batch.num_rows:
                    partition = partitions[start]
                    stop = start + 1
                    while stop < batch.num_rows and partitions[stop] == partition:
                        stop += 1
                    if partition != current_partition:
                        if writer:
                            writer.close()
                        folder = output / f"ctmt={partition}"
                        folder.mkdir(exist_ok=True)
                        writer = pq.ParquetWriter(folder / "part-000000.parquet", batch.schema, compression="zstd")
                        current_partition = partition
                    writer.write_batch(batch.slice(start, stop - start))
                    start = stop
        finally:
            if writer:
                writer.close()
        # Dataset vazio também precisa de esquema materializado.
        if not any(output.rglob("*.parquet")):
            con.sql("SELECT * FROM checked LIMIT 0").write_parquet(str(output / "part-000000.parquet"))
        result = con.execute("SELECT count(*), count(DISTINCT id_uc), count(*) FILTER (WHERE tipo_curva IS NULL), count(DISTINCT tipo_curva) FROM raw_uc").fetchone()
        return {"linhas": result[0], "ids_distintos": result[1], "tipologia_nula": result[2], "tipologias_usadas": result[3], "chaves_duplicadas": len(duplicates)}


def verify_tables(directory, counts, token):
    from .db import ENTITIES, parquet_paths
    with connection(token=token) as con:
        for entity in ENTITIES:
            relation = con.from_parquet(parquet_paths(directory, entity), hive_partitioning=False)
            actual = relation.count("*").fetchone()[0]
            if actual != counts[entity]:
                raise ValueError(f"Contagem divergente: {entity}: {actual} != {counts[entity]}")
            token.verificar()
