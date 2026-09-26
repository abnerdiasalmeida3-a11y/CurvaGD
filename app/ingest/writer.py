from pathlib import Path
import pyarrow.parquet as pq


class BatchWriter:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def write(self, table):
        pq.write_table(table, self.directory / f"part-{self.count:06d}.parquet", compression="zstd", row_group_size=32768)
        self.count += 1
