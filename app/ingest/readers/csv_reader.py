import csv
import codecs
from pathlib import Path
import pyarrow as pa
import pyarrow.csv as pacsv
from ...core.errors import ErroBDGD


def inspect_csv(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(65536)
    encoding = "utf-8-sig"
    try:
        sample = codecs.getincrementaldecoder(encoding)().decode(raw, final=False)
    except UnicodeDecodeError:
        encoding, sample = "cp1252", raw.decode("cp1252")
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        try:
            delimiter = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=",;\t|").delimiter
        except (csv.Error, IndexError):
            delimiter = ","
    names = next(csv.reader(sample.splitlines(), delimiter=delimiter), [])
    if not names or len(set(names)) != len(names):
        raise ErroBDGD("ESQUEMA_INVALIDO", f"Cabeçalho vazio ou duplicado: {path.name}")
    return {"columns": names, "types": ["texto"] * len(names), "count": None, "encoding": encoding, "delimiter": delimiter}


def batches(path: Path, info: dict, columns: list[str], batch_size: int):
    try:
        with pacsv.open_csv(path, read_options=pacsv.ReadOptions(encoding=info["encoding"], block_size=2 * 1024 * 1024),
                             parse_options=pacsv.ParseOptions(delimiter=info["delimiter"]),
                             convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in info["columns"]},
                                                                  include_columns=columns, strings_can_be_null=True,
                                                                  null_values=["", "NULL", "null"])) as reader:
            for batch in reader:
                for start in range(0, batch.num_rows, batch_size):
                    yield batch.slice(start, batch_size)
    except (pa.ArrowInvalid, pa.ArrowKeyError, OSError, UnicodeError) as exc:
        raise ErroBDGD("FONTE_ILEGIVEL", f"Não foi possível ler {path.name}: {exc}") from exc
