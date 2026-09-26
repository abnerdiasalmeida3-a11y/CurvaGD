import numpy as np
from ...domain.models import ComportamentosTipicos
from ...domain.enums import TipoDia
from ..db import connection, version_view


def get_curve(catalog, import_id, tipologia):
    with connection(catalog.path) as con:
        view = version_view(import_id, "crvcrg")
        rows = con.execute(f"SELECT tipo_dia,potencia_pu,base_normalizacao_kw,area_pu_h FROM {view} WHERE tipo_curva=? AND status='VALIDO'", [tipologia]).fetchall()
    if len(rows) != 3:
        return None
    return ComportamentosTipicos(tipologia, {TipoDia(r[0]): np.array(r[1], dtype=np.float64) for r in rows}, rows[0][2], {TipoDia(r[0]): r[3] for r in rows})
