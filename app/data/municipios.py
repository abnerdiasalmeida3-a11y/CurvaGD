"""Nomes oficiais dos municípios para apresentar os códigos IBGE da BDGD."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def nomes_municipios():
    arquivo = Path(__file__).parents[1] / "config" / "municipios_ibge.json"
    return json.loads(arquivo.read_text(encoding="utf-8"))


def rotulo_municipio(codigo):
    nome = nomes_municipios().get(str(codigo))
    return f"{codigo} · {nome}" if nome else str(codigo)
