"""Tratamentos imutáveis por versão da BDGD, registro de UC e mês.

Cada tratamento registra o método de cálculo. Só contam como tratados os meses
calculados pelo método atual (CORRECAO_DEMANDA_BDGD): tratamentos antigos ficam
guardados, mas a UC volta a aparecer como pendente até ser recalculada, para a
curva do alimentador nunca misturar dois métodos.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import numpy as np

from ...core.errors import ErroBDGD

METODO_ATUAL = "CORRECAO_DEMANDA_BDGD"

SERIES = {
    "carga": "Carga sem GD (kW)", "geracao": "Geração da GD (kW)",
    "liquida": "Curva unificada - carga menos GD (kW)",
    "exportado": "Exportação para a rede (kW)",
}


class TratamentosUC:
    def __init__(self, workspace):
        self.path = Path(workspace) / "tratamentos_ucs.sqlite3"
        with self._con() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS curvas (
                    id TEXT PRIMARY KEY, import_id TEXT NOT NULL, entidade TEXT NOT NULL,
                    linha INTEGER NOT NULL, mes INTEGER NOT NULL, criado TEXT NOT NULL,
                    parametros TEXT NOT NULL, metadados TEXT NOT NULL, dados BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS atuais (
                    import_id TEXT, entidade TEXT, linha INTEGER, mes INTEGER, curva_id TEXT NOT NULL,
                    PRIMARY KEY(import_id, entidade, linha, mes));
                CREATE INDEX IF NOT EXISTS idx_curvas_uc ON curvas(import_id, entidade, linha, criado);
            """)

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.path, timeout=30)
        try:
            with con:
                yield con
        finally:
            con.close()

    def salvar(self, import_id, resultado, parametros, token=None):
        partes = resultado.get("resultados_mensais") or (resultado,)
        criado = datetime.now(timezone.utc).isoformat()
        opcoes = json.dumps({**parametros, "mes": resultado["mes"], "metodo": METODO_ATUAL},
                            ensure_ascii=False, allow_nan=False)
        with self._con() as con:
            for parte in partes:
                if token:
                    token.verificar()
                uc = parte["uc"]
                arrays = {k: np.asarray(parte["series"][v], dtype=np.float64) for k, v in SERIES.items()}
                self._validar(arrays, len(parte["timestamps"]))
                solar = parte.get("irradiancia")
                arrays["ghi"] = np.asarray(solar["series"]["GHI - horizontal (W/m²)"], dtype=np.float64) if solar else np.full(len(arrays["carga"]), np.nan)
                buffer = BytesIO()
                np.savez_compressed(buffer, **arrays)
                meta = {"uc_id": uc["id_uc"], "ano": parte["ano"], "mes": parte["mes"],
                        "entidade": uc["entidade"], "linha_origem": uc["linha_origem"],
                        "inicio": parte["timestamps"][0].isoformat(), "fim": parte["timestamps"][-1].isoformat(),
                        "pontos": len(arrays["carga"]), "tipos_dia": parte["tipos_dia"],
                        "cenario": parte.get("cenario_principal", "importada"),
                        "fonte_equipamento": parte.get("fonte_equipamento", "sem GD"),
                        "fonte_irradiancia": solar.get("arquivo") if solar else None,
                        "hipoteses": parte.get("hipoteses", ())}
                ident = uuid4().hex
                con.execute("INSERT INTO curvas VALUES (?,?,?,?,?,?,?,?,?)", (
                    ident, import_id, uc["entidade"], uc["linha_origem"], parte["mes"], criado,
                    opcoes, json.dumps(meta, ensure_ascii=False), buffer.getvalue()))
                con.execute("INSERT INTO atuais VALUES (?,?,?,?,?) ON CONFLICT(import_id,entidade,linha,mes) DO UPDATE SET curva_id=excluded.curva_id",
                            (import_id, uc["entidade"], uc["linha_origem"], parte["mes"], ident))
            if token:
                token.verificar()

    @staticmethod
    def _validar(arrays, pontos):
        if any(a.shape != (pontos,) or not np.all(np.isfinite(a)) for a in arrays.values()):
            raise ErroBDGD("TRATAMENTO_INVALIDO", "O tratamento possui intervalos ausentes ou valores inválidos.")
        if any(np.any(arrays[k] < -1e-9) for k in ("carga", "geracao", "exportado")):
            raise ErroBDGD("TRATAMENTO_INVALIDO", "Carga, geração e exportação não podem ser negativas.")
        if not np.allclose(arrays["liquida"], arrays["carga"] - arrays["geracao"], rtol=1e-9, atol=1e-8):
            raise ErroBDGD("TRATAMENTO_INVALIDO", "A curva líquida não fecha com carga menos geração.")
        if not np.allclose(arrays["exportado"], np.maximum(-arrays["liquida"], 0), rtol=1e-9, atol=1e-8):
            raise ErroBDGD("TRATAMENTO_INVALIDO", "A exportação da UC não fecha com sua curva líquida.")

    def referencias(self, import_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT a.entidade, a.linha, a.mes, a.curva_id FROM atuais a JOIN curvas c ON c.id = a.curva_id "
                "WHERE a.import_id=? AND json_extract(c.parametros, '$.metodo') = ?",
                (import_id, METODO_ATUAL)).fetchall()
        saida = {}
        for entidade, linha, mes, ident in rows:
            saida.setdefault((entidade, linha), {})[mes] = ident
        return saida

    def ler(self, ident):
        with self._con() as con:
            row = con.execute("SELECT metadados,dados FROM curvas WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ErroBDGD("TRATAMENTO_INVALIDO", "O tratamento salvo não foi encontrado. Trate novamente a UC.")
        try:
            meta = json.loads(row[0])
            with np.load(BytesIO(row[1]), allow_pickle=False) as dados:
                arrays = {k: dados[k] for k in (*SERIES, "ghi")}
            self._validar({k: arrays[k] for k in SERIES}, meta["pontos"])
            if arrays["ghi"].shape != (meta["pontos"],) or len(meta["tipos_dia"]) * 96 != meta["pontos"]:
                raise ValueError("Quantidade de intervalos incompatível")
            if np.any(np.isinf(arrays["ghi"])) or np.any(arrays["ghi"] < 0):
                raise ValueError("Irradiância inválida")
        except (ValueError, KeyError, OSError) as exc:
            raise ErroBDGD("TRATAMENTO_INVALIDO", "O tratamento salvo está inválido. Trate novamente a UC.") from exc
        return meta, arrays

    def remover_versoes(self, import_ids):
        """Apaga os tratamentos de importacoes que sairam do catalogo."""
        ids = [str(i) for i in import_ids]
        if not ids:
            return
        marcas = ",".join("?" * len(ids))
        with self._con() as con:
            con.execute(f"DELETE FROM atuais WHERE import_id IN ({marcas})", ids)
            con.execute(f"DELETE FROM curvas WHERE import_id IN ({marcas})", ids)
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("VACUUM")
        finally:
            con.close()

    def parametros(self, import_id, entidade, linha):
        with self._con() as con:
            row = con.execute("SELECT parametros FROM curvas WHERE import_id=? AND entidade=? AND linha=? ORDER BY criado DESC, rowid DESC LIMIT 1",
                              (import_id, entidade, linha)).fetchone()
        if row is None:
            return None
        parametros = json.loads(row[0])
        for campo in ("produtividades_mensais", "injecoes_mensais", "demandas_mensais"):
            if parametros.get(campo):
                parametros[campo] = {int(k): v for k, v in parametros[campo].items()}
        return parametros
