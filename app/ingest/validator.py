"""Validação por registro, independente dos nomes físicos da BDGD."""
import math
import re
from datetime import date
import pyarrow as pa
from ..domain.enums import CoberturaCalendario


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        # Decimal explícito; separadores de milhar ambíguos não são inferidos.
        result = float(value.replace(",", ".") if isinstance(value, str) else value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def text_value(value):
    return None if value is None or not str(value).strip() else str(value)


def canonical_schema(entity, fields):
    fields_out = [pa.field("linha_origem", pa.int64())]
    for field in fields:
        dtype = pa.float64() if field.startswith(("energia_mes_", "potencia_", "demanda_mes_")) else pa.string()
        fields_out.append(pa.field(field, dtype))
    fields_out += [pa.field("status", pa.string()), pa.field("diagnosticos", pa.string())]
    if entity in ("ucbt", "ucmt"):
        fields_out += [pa.field("meses_invalidos", pa.list_(pa.int8())), pa.field("entidade", pa.string())]
    if entity == "crvcrg":
        fields_out += [pa.field("potencia_original", pa.list_(pa.string(), 96)), pa.field("base_normalizacao_kw", pa.float64()), pa.field("potencia_pu", pa.list_(pa.float64(), 96)), pa.field("area_pu_h", pa.float64())]
    return pa.schema(fields_out)


class Validador:
    def __init__(self, sink, calendar_loader, ano, sub_ids=(), ctmt_sub=None, valid_types=()):
        self.sink = sink
        self.calendar_loader, self.ano = calendar_loader, ano
        self.sub_ids = set(sub_ids)
        self.ctmt_sub = ctmt_sub or {}
        self.valid_types = set(valid_types)
        self._coverage = {}

    def convert(self, entity, batch, fields, start):
        raw = {canonical: batch.column(batch.schema.get_field_index(physical)).to_pylist() if physical else [None] * batch.num_rows
               for canonical, physical in fields.items()}
        result = {f.name: [] for f in canonical_schema(entity, fields)}
        for i in range(batch.num_rows):
            row = {key: number(values[i]) if key.startswith(("energia_mes_", "potencia_", "demanda_mes_")) else text_value(values[i]) for key, values in raw.items()}
            row["linha_origem"] = start + i + 1
            flags, codes = set(), set()
            key = row.get("id_uc", row.get("id_alimentador", row.get("id_subestacao", row.get("tipo_curva")))) or ""

            def issue(code, field, message, severity="ERRO", status=None):
                codes.add(code)
                if status:
                    flags.add(status)
                original = raw[field][i] if field in raw else ""
                self.sink.emit(code, severity, entity, key, field, original, message, row["linha_origem"])

            id_field = {"sub": "id_subestacao", "ctmt": "id_alimentador", "crvcrg": "tipo_curva", "ucbt": "id_uc", "ucmt": "id_uc"}[entity]
            if row[id_field] is None:
                issue("CAMPO_INVALIDO", id_field, "Identificador obrigatório ausente.", status="REFERENCIA_ORFA")
            if entity == "ctmt" and row["subestacao"] not in self.sub_ids:
                issue("REFERENCIA_ORFA", "subestacao", "Subestação não encontrada ou ambígua.", status="REFERENCIA_ORFA")
            if entity in ("ucbt", "ucmt"):
                feeder = row["alimentador"]
                if feeder not in self.ctmt_sub:
                    issue("REFERENCIA_ORFA", "alimentador", "Alimentador não encontrado ou ambíguo.", status="REFERENCIA_ORFA")
                else:
                    expected = self.ctmt_sub[feeder]
                    if expected not in self.sub_ids or (row["subestacao"] is not None and row["subestacao"] != expected):
                        issue("REFERENCIA_ORFA", "subestacao", "Subestação ausente ou incompatível com o alimentador.", status="REFERENCIA_ORFA")
                if row["tipo_curva"] not in self.valid_types:
                    issue("TIPOLOGIA_INCOMPLETA", "tipo_curva", "Tipologia ausente, desconhecida ou inválida.", status="NAO_CURVAVEL_TIPOLOGIA")
                invalid_months = []
                for month in range(1, 13):
                    field = f"energia_mes_{month:02d}"
                    if row[field] is None or row[field] < 0:
                        invalid_months.append(month)
                        issue("ENERGIA_INVALIDA", field, "Energia ausente, não finita ou negativa.", status="NAO_CURVAVEL_ENERGIA")
                municipio = row["municipio"] or ""
                if not re.fullmatch(r"\d{7}", municipio) or municipio[:2] not in self.calendar_loader._uf_codes:
                    issue("CALENDARIO_INCOMPLETO", "municipio", "Código IBGE inválido.", status="CALENDARIO_PARCIAL")
                else:
                    if municipio not in self._coverage:
                        cal = self.calendar_loader.for_municipio(municipio)
                        self._coverage[municipio] = cal.cobertura(municipio, date(self.ano, 1, 1), date(self.ano, 12, 31))
                    if self._coverage[municipio] == CoberturaCalendario.PARCIAL:
                        issue("CALENDARIO_INCOMPLETO", "municipio", "Cobertura municipal não declarada completa.", "AVISO", "CALENDARIO_PARCIAL")
                if row["classe"] is None:
                    issue("CAMPO_INVALIDO", "classe", "Classe de consumo ausente.")
                for field, domain in (("grupo_tensao", {"AT", "MT", "BT"}), ("situacao", {"AT", "DS", "NE"})):
                    if row.get(field) and row[field] not in domain:
                        issue("DOMINIO_INVALIDO", field, "Valor fora do domínio do contrato.")
                row["meses_invalidos"], row["entidade"] = invalid_months, entity
            if entity == "crvcrg":
                row["potencia_original"] = [None if raw[f"potencia_{j:02d}"][i] is None else str(raw[f"potencia_{j:02d}"][i]) for j in range(1, 97)]
                for j in range(1, 97):
                    field = f"potencia_{j:02d}"
                    if row[field] is None or row[field] < 0:
                        issue("PERFIL_INVALIDO", field, "Potência original ausente, não finita ou negativa.")
                row.update(base_normalizacao_kw=None, potencia_pu=None, area_pu_h=None)
            row["status"] = "|".join(sorted(flags)) if flags else ("CURVAVEL" if entity.startswith("uc") else "VALIDO")
            row["diagnosticos"] = "|".join(sorted(codes))
            for field in result:
                result[field].append(row[field])
        return pa.Table.from_pydict(result, schema=canonical_schema(entity, fields))
