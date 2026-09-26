from pathlib import Path
import yaml
from .readers import gdb_reader, csv_reader
from ..core.errors import ErroBDGD

ENTITIES = ("sub", "ctmt", "crvcrg", "ucbt", "ucmt")


class Fonte:
    def __init__(self, path: Path, schema: Path):
        self.path = path.resolve()
        if not self.path.is_dir():
            raise ErroBDGD("FONTE_ILEGIVEL", "Selecione uma pasta .gdb ou uma pasta com os CSVs.")
        try:
            self.schema = yaml.safe_load(schema.read_text(encoding="utf-8"))
            assert isinstance(self.schema, dict) and self.schema.get("versao_contrato")
            for entity in ENTITIES:
                spec = self.schema["entidades"][entity]
                assert isinstance(spec["fontes"], list) and spec["fontes"] and isinstance(spec["campos"], dict)
                for rule in spec["campos"].values():
                    assert isinstance(rule, dict) and (isinstance(rule.get("origem"), list) or ("origem_padrao" in rule and type(rule["cardinalidade"]) is int and rule["cardinalidade"] > 0))
        except (yaml.YAMLError, AssertionError, TypeError, KeyError) as exc:
            raise ErroBDGD("ESQUEMA_INVALIDO", "Contrato YAML inválido ou incompleto.") from exc
        self.gdb = self.path.suffix.lower() == ".gdb"
        available = gdb_reader.inventory(self.path) if self.gdb else {p.stem: p for p in self.path.glob("*.csv")}
        self.inventory = list(available)
        self.entities = {}
        for entity in ENTITIES:
            spec = self.schema["entidades"][entity]
            name = next((name for name in spec["fontes"] if name in available), None)
            if name is None:
                raise ErroBDGD("ESQUEMA_INVALIDO", f"Entidade obrigatória ausente: {entity}.")
            location = self.path if self.gdb else available[name]
            info = gdb_reader.inspect_layer(location, name) if self.gdb else csv_reader.inspect_csv(location)
            fields = {}
            for canonical, rule in spec["campos"].items():
                if "origem_padrao" in rule:
                    for index in range(1, rule["cardinalidade"] + 1):
                        physical = rule["origem_padrao"].format(mes=index, i=index)
                        if physical not in info["columns"] and rule.get("obrigatorio", True):
                            raise ErroBDGD("ESQUEMA_INVALIDO", f"{name}: coluna obrigatória ausente: {physical}.")
                        fields[f"{canonical}_{index:02d}"] = physical if physical in info["columns"] else None
                else:
                    physical = next((c for c in rule["origem"] if c in info["columns"]), None)
                    if physical is None and rule.get("obrigatorio", False):
                        raise ErroBDGD("ESQUEMA_INVALIDO", f"{name}: coluna obrigatória ausente: {canonical}.")
                    fields[canonical] = physical
            self.entities[entity] = {"name": name, "path": location, "info": info, "fields": fields}

    def batches(self, entity: str, size: int):
        e = self.entities[entity]
        columns = list(dict.fromkeys(p for p in e["fields"].values() if p))
        reader = gdb_reader.batches(e["path"], e["name"], columns, size) if self.gdb else csv_reader.batches(e["path"], e["info"], columns, size)
        yield from reader

    def input_files(self):
        return sorted(p for p in self.path.rglob("*") if p.is_file() and not p.name.endswith(".lock")) if self.gdb else sorted(self.path.glob("*.csv"))
