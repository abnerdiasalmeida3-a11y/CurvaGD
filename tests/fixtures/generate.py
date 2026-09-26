import csv
from pathlib import Path
import yaml


def fixture(path: Path, size=8, errors=True):
    path.mkdir(parents=True, exist_ok=True)
    schema = yaml.safe_load((Path(__file__).parents[2] / "app/config/schemas/bdgd_2024.yaml").read_text(encoding="utf-8"))
    for entity, spec in schema["entidades"].items():
        name = spec["fontes"][0]
        fields = []
        for field, rule in spec["campos"].items():
            if "origem_padrao" in rule:
                fields += [rule["origem_padrao"].format(mes=i, i=i) for i in range(1, rule["cardinalidade"] + 1)]
            else:
                fields.append(rule["origem"][0])
        with (path / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            if entity == "sub":
                rows = [{"COD_ID": "S1", "NOME": "Subestação piloto"}]
            elif entity == "ctmt":
                rows = [{"COD_ID": "F1", "SUB": "S1", "NOME": "Alimentador piloto"}]
            elif entity == "crvcrg":
                rows = [{"COD_ID": "T1", "TIP_DIA": d, "GRU_TEN": "BT", **{f"POT_{i:02d}": p for i in range(1, 97)}} for d, p in [("DU", 2), ("SA", 4), ("DO", 1)]]
            else:
                def consumers():
                    for i in range(size if entity == "ucbt" else 3):
                        row = {"COD_ID": f"{entity}-{i:07d}", "CTMT": "F1", "MUN": "5103403", "SUB": "S1", "CLAS_SUB": ["RE1", "CO1", "IN1"][i % 3], "TIP_CC": "T1", "CEG_GD": "GD1" if i % 10 == 0 else "", "GRU_TEN": "BT" if entity == "ucbt" else "MT", "SIT_ATIV": "AT", **{f"ENE_{m:02d}": 342 + i % 100 for m in range(1, 13)}}
                        if errors and i == 1:
                            row["ENE_02"], row["ENE_03"], row["ENE_04"] = "", -1, "inválida"
                        if errors and i == 2:
                            row["CTMT"], row["TIP_CC"] = "ORFA", "DESCONHECIDA"
                        yield row
                rows = consumers()
            writer.writerows(rows)
    return path
