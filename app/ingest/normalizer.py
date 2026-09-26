from collections import defaultdict
import warnings
import numpy as np
import pyarrow as pa
from ..domain.models import PerfisBrutos
from ..domain.enums import TipoDia
from ..domain.sintese import normalizar_perfis
from ..core.errors import ErroBDGD


def normalize_curves(table, sink, profiler):
    rows = table.to_pylist()
    groups = defaultdict(list)
    for row in rows:
        groups[row["tipo_curva"]].append(row)
    valid = set()
    peaks = []
    for tipologia, group in groups.items():
        try:
            if not tipologia or len(group) != 3 or {r["tipo_dia"] for r in group} != {d.value for d in TipoDia}:
                raise ErroBDGD("TIPOLOGIA_INCOMPLETA", "Exigido exatamente um perfil DU, SA e DO.")
            raw = {TipoDia(r["tipo_dia"]): np.array([r[f"potencia_{i:02d}"] for i in range(1, 97)], dtype=np.float64) for r in group}
            with warnings.catch_warnings(record=True) as notices:
                normalized = normalizar_perfis(PerfisBrutos(tipologia, raw))
            for notice in notices:
                sink.emit("PERFIL_ZERO", "AVISO", "crvcrg", tipologia, "potencia", "0", str(notice.message))
            valid.add(tipologia)
            for row in group:
                d = TipoDia(row["tipo_dia"])
                row.update(base_normalizacao_kw=normalized.base_normalizacao_kw, potencia_pu=normalized.potencia_pu[d].tolist(), area_pu_h=normalized.areas_pu_h[d])
                peaks.append(float(np.max(raw[d])))
        except ErroBDGD as exc:
            for row in group:
                row["status"] = "NAO_CURVAVEL_TIPOLOGIA"
                row["diagnosticos"] = "|".join(exc.codigos)
                for code in exc.codigos:
                    sink.emit(code, "ERRO", "crvcrg", tipologia or "", "potencia", "perfil preservado", str(exc), row["linha_origem"])
    profiler.curves = {"registros": len(rows), "tipologias": len(groups), "tipologias_validas": len(valid),
                       "menor_pico_bruto_kw": min(peaks, default=None), "maior_pico_bruto_kw": max(peaks, default=None),
                       "por_tipologia": {str(t): [{"dia": r["tipo_dia"], "base_kw": r["base_normalizacao_kw"], "area_pu_h": r["area_pu_h"], "min_pu": min(r["potencia_pu"]) if r["potencia_pu"] else None, "max_pu": max(r["potencia_pu"]) if r["potencia_pu"] else None, "status": r["status"]} for r in g] for t, g in groups.items()}}
    return pa.Table.from_pylist(rows, schema=table.schema), valid
