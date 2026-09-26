import csv
from collections import Counter
from pathlib import Path
import pyarrow.compute as pc


class DiagnosticSink:
    def __init__(self, path: Path):
        self.stream = path.open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.writer(self.stream)
        self.writer.writerow(["codigo", "severidade", "entidade", "chave", "campo", "valor_original", "mensagem", "detalhe_tecnico", "linha"])
        self.counts = Counter()
        self.severities = Counter()
        self.entities = {}

    def emit(self, code, severity, entity, key, field, value, message, line=0, detail=""):
        self.counts[code] += 1
        self.severities[severity] += 1
        self.entities.setdefault(entity, Counter())[severity] += 1
        values = [code, severity, entity, key, field, str(value)[:180], message, detail, line]
        # Relatórios podem ser abertos em planilhas; não executar fórmulas dos dados.
        self.writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])

    def close(self):
        self.stream.close()


class Profiler:
    def __init__(self, source):
        self.source = source
        self.stats = {}
        self.curves = {}

    def observe(self, entity, table):
        s = self.stats.setdefault(entity, {"linhas": 0, "nulos": Counter(), "categorias": {}})
        s["linhas"] += table.num_rows
        for name in table.column_names:
            s["nulos"][name] += table[name].null_count
        for name in ("classe", "municipio", "tipo_curva", "grupo_tensao", "status"):
            if name not in table.column_names:
                continue
            counts = s["categorias"].setdefault(name, Counter())
            for item in pc.value_counts(table[name]).to_pylist():
                value = str(item["values"])
                label = value if value in counts or len(counts) < 1000 else "__OUTRAS_CATEGORIAS__"
                counts[label] += item["counts"]

    def markdown(self, dist, ano, sink, extra=None):
        out = [f"# Perfil BDGD — {dist} / {ano}", "", "Fonte inspecionada em lotes. Identificadores são tratados como texto.",
               "CSV: campos numéricos aceitam ponto ou vírgula decimal, sem inferência de milhares.",
               "", "## Inventário", ", ".join(self.source.inventory), "", "## Entidades"]
        for entity, s in self.stats.items():
            info = self.source.entities[entity]
            out += [f"\n### {entity} ({info['name']})", f"\nRegistros: {s['linhas']:,}", "", "| Campo físico | Tipo |", "|---|---|"]
            out += [f"| {c} | {t} |" for c, t in zip(info["info"]["columns"], info["info"]["types"])]
            out += ["", "Nulos canônicos: " + str(dict(s["nulos"])), "", "Categorias (até 1.000 por campo):"]
            out += [f"- {k}: {dict(v)}" for k, v in s["categorias"].items()]
        out += ["", "## Perfis típicos", str({k: v for k, v in self.curves.items() if k != 'por_tipologia'}), "", "| Tipologia | Dia | Base kW | Mín. pu | Máx. pu | Área pu·h | Status |", "|---|---|---:|---:|---:|---:|---|"]
        for tipologia, profiles in self.curves.get("por_tipologia", {}).items():
            for p in profiles:
                out.append(f"| {tipologia} | {p['dia']} | {p['base_kw']} | {p['min_pu']} | {p['max_pu']} | {p['area_pu_h']} | {p['status']} |")
        out += ["", "## Chaves e relações", str(extra or {}), "", "## Diagnósticos"]
        out += [f"- {k}: {v:,}" for k, v in sink.counts.items()]
        out += ["", "As linhas com problemas permanecem disponíveis. Calendário parcial não equivale a ausência de feriados."]
        return "\n".join(out) + "\n"
