"""Consulta sob demanda do traçado da rede na File Geodatabase original."""

from collections import defaultdict
from math import cos, hypot, radians
from pathlib import Path
import struct

import pyogrio


def _geometria_linhas(wkb):
    """Lê LineString/MultiLineString WKB sem dependência de Shapely."""
    if not wkb:
        return []
    data = memoryview(wkb)

    def geometry(offset):
        endian = "<" if data[offset] == 1 else ">"
        kind = struct.unpack_from(endian + "I", data, offset + 1)[0]
        offset += 5
        dimensions = 2 + bool(kind & 0x80000000) + bool(kind & 0x40000000)
        if kind & 0x20000000:
            offset += 4  # SRID no EWKB
        plain = kind & 0x0FFFFFFF
        if plain >= 1000:  # ISO WKB com Z/M
            variant = plain // 1000
            dimensions = 2 + (variant in (1, 3)) + (variant in (2, 3))
            plain %= 1000
        if plain == 2:
            count = struct.unpack_from(endian + "I", data, offset)[0]
            offset += 4
            points = []
            for _ in range(count):
                x, y = struct.unpack_from(endian + "dd", data, offset)
                points.append((x, y))
                offset += dimensions * 8
            return [points], offset
        if plain == 5:
            count = struct.unpack_from(endian + "I", data, offset)[0]
            offset += 4
            lines = []
            for _ in range(count):
                part, offset = geometry(offset)
                lines.extend(part)
            return lines, offset
        return [], offset

    try:
        return [line for line in geometry(0)[0] if len(line) > 1]
    except (IndexError, struct.error, ValueError):
        return []


def _comprimento_km(points):
    # Aproximação local adequada à extensão de um trecho da rede.
    return sum(hypot((b[0] - a[0]) * 111.32 * cos(radians((a[1] + b[1]) / 2)),
                     (b[1] - a[1]) * 110.57)
               for a, b in zip(points, points[1:]))


def _geometria_ponto(wkb):
    if not wkb:
        return None
    try:
        endian = "<" if wkb[0] == 1 else ">"
        kind = struct.unpack_from(endian + "I", wkb, 1)[0]
        offset = 9 if kind & 0x20000000 else 5
        if (kind & 0x0FFFFFFF) % 1000 != 1:
            return None
        return struct.unpack_from(endian + "dd", wkb, offset)
    except (IndexError, struct.error):
        return None


def _fonte(path):
    source = Path(path).expanduser()
    if not source.is_dir() or source.suffix.lower() != ".gdb":
        raise ValueError("Selecione a pasta .gdb original para desenhar a rede.")
    return source


def opcoes_mapa(path, token=None):
    source = _fonte(path)
    names = {name for name, _ in pyogrio.list_layers(source)}
    if not {"SUB", "CTMT", "SSDMT", "SSDBT"}.issubset(names):
        raise ValueError("A geodatabase não contém SUB, CTMT, SSDMT e SSDBT.")
    substations = pyogrio.read_arrow(source, layer="SUB", columns=["COD_ID", "NOME"],
                                     read_geometry=False)[1].to_pylist()
    feeders = pyogrio.read_arrow(source, layer="CTMT", columns=["COD_ID", "NOME", "SUB"],
                                read_geometry=False)[1].to_pylist()
    if token:
        token.verificar()
    subs = sorted({str(r["COD_ID"]): str(r.get("NOME") or "") for r in substations
                   if r.get("COD_ID") is not None}.items(), key=lambda item: item[1] or item[0])
    grouped = defaultdict(list)
    for row in feeders:
        if row.get("SUB") is not None and row.get("COD_ID") is not None:
            grouped[str(row["SUB"])].append((str(row["COD_ID"]), str(row.get("NOME") or "")))
    for values in grouped.values():
        values.sort(key=lambda item: item[0])
    return {"subestacoes": subs, "alimentadores": dict(grouped)}


def desenhar_rede(path, subestacao, alimentador="", token=None):
    source = _fonte(path)
    if not subestacao:
        raise ValueError("Selecione uma subestação.")
    where = "SUB = '" + str(subestacao).replace("'", "''") + "'"
    if alimentador:
        where += " AND CTMT = '" + str(alimentador).replace("'", "''") + "'"
    segments = {"SSDMT": [], "SSDBT": []}
    segment_feeders = {"SSDMT": [], "SSDBT": []}
    summary = defaultdict(lambda: {"mt": 0, "bt": 0, "mt_km": 0.0, "bt_km": 0.0, "trafos": 0})
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for layer, key in (("SSDMT", "mt"), ("SSDBT", "bt")):
        with pyogrio.open_arrow(source, layer=layer, columns=["CTMT", "SUB"],
                                where=where, batch_size=16384, use_pyarrow=True) as (_, reader):
            for batch in reader:
                if token:
                    token.verificar()
                geometry_field = next((field.name for field in batch.schema
                                       if (field.metadata or {}).get(b"ARROW:extension:name") == b"geoarrow.wkb"), None)
                if geometry_field is None:
                    raise ValueError(f"A camada {layer} não possui geometria de linha.")
                for row in batch.to_pylist():
                    feeder = str(row.get("CTMT") or "")
                    lines = _geometria_linhas(row.get(geometry_field))
                    if not lines:
                        continue
                    summary[feeder][key] += 1
                    for points in lines:
                        segments[layer].append(points)
                        segment_feeders[layer].append(feeder)
                        summary[feeder][key + "_km"] += _comprimento_km(points)
                        for x, y in points:
                            bounds[0] = min(bounds[0], x)
                            bounds[1] = min(bounds[1], y)
                            bounds[2] = max(bounds[2], x)
                            bounds[3] = max(bounds[3], y)
    transformers = []
    if "UNTRMT" in {name for name, _ in pyogrio.list_layers(source)}:
        with pyogrio.open_arrow(source, layer="UNTRMT", columns=["CTMT", "SUB"],
                                where=where, batch_size=16384, use_pyarrow=True) as (_, reader):
            for batch in reader:
                if token:
                    token.verificar()
                geometry_field = next((field.name for field in batch.schema
                                       if (field.metadata or {}).get(b"ARROW:extension:name") == b"geoarrow.wkb"), None)
                for row in batch.to_pylist():
                    point = _geometria_ponto(row.get(geometry_field)) if geometry_field else None
                    if point:
                        feeder = str(row.get("CTMT") or "")
                        transformers.append((feeder, *point))
                        summary[feeder]["trafos"] += 1
    if bounds[0] == float("inf"):
        bounds = None
    return {"subestacao": subestacao, "alimentador": alimentador,
            "linhas_mt": segments["SSDMT"], "linhas_bt": segments["SSDBT"],
            "codigos_mt": segment_feeders["SSDMT"], "codigos_bt": segment_feeders["SSDBT"],
            "trafos": transformers, "limites": bounds, "alimentadores": dict(sorted(summary.items()))}


def filtrar_alimentador(rede, codigo):
    if not codigo:
        return rede
    result = {"subestacao": rede["subestacao"], "alimentador": codigo,
              "alimentadores": {codigo: rede["alimentadores"][codigo]} if codigo in rede["alimentadores"] else {}}
    result["trafos"] = [item for item in rede["trafos"] if item[0] == codigo]
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for key in ("mt", "bt"):
        lines = [points for feeder, points in zip(rede["codigos_" + key], rede["linhas_" + key])
                 if feeder == codigo]
        result["linhas_" + key] = lines
        for points in lines:
            for x, y in points:
                bounds[0] = min(bounds[0], x)
                bounds[1] = min(bounds[1], y)
                bounds[2] = max(bounds[2], x)
                bounds[3] = max(bounds[3], y)
    result["limites"] = None if bounds[0] == float("inf") else bounds
    return result
