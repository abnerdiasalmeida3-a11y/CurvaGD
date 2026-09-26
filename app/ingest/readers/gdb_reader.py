from pathlib import Path
import pyogrio
from ...core.errors import ErroBDGD


def inventory(path: Path):
    try:
        return {str(name): None for name, _ in pyogrio.list_layers(path)}
    except Exception as exc:
        raise ErroBDGD("FONTE_ILEGIVEL", "Não foi possível abrir a File Geodatabase.") from exc


def inspect_layer(path: Path, layer: str):
    info = pyogrio.read_info(path, layer=layer)
    return {"columns": list(info["fields"]), "types": [str(x) for x in info["dtypes"]], "count": int(info["features"])}


def batches(path: Path, layer: str, columns: list[str], batch_size: int):
    try:
        with pyogrio.open_arrow(path, layer=layer, columns=columns, read_geometry=False,
                                batch_size=batch_size, use_pyarrow=True) as (_, reader):
            yield from reader
    except Exception as exc:
        raise ErroBDGD("FONTE_ILEGIVEL", f"Não foi possível ler a camada {layer}: {exc}") from exc
