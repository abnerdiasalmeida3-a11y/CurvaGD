"""Leitura local dos dados tecnicos de micro e minigeracao da ANEEL.

As potencias de um CEG_GD sao a SOMA de todas as suas linhas na base tecnica,
como na CORRECAO_DEMANDA_BDGD: o mesmo codigo aparece varias vezes quando ha
mais de um arranjo ou uma ampliacao.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
import math
import unicodedata
import zipfile

import pandas as pd

from ...core.errors import ErroBDGD


@dataclass(frozen=True)
class EquipamentoGD:
    codigo_gd: str
    potencia_modulos_kwp: float
    potencia_inversor_kw: float
    fonte: str
    linhas: int = 1


def _chave(texto) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(texto or "")).casefold()
        if char.isalnum()
    )


def _numero(valor) -> float | None:
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return None
    texto = str(valor).strip().replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero if math.isfinite(numero) and numero > 0 else None


def _resolver_colunas(colunas) -> dict[str, str]:
    mapa = {_chave(c): str(c) for c in colunas}
    aliases = {
        "codigo": ("codgeracaodistribuida", "ceggd", "codigogeracaodistribuida"),
        "modulos": ("mdapotenciamodulos", "potenciamodulos", "potenciamodulokw"),
        "inversores": ("mdapotenciainversores", "potenciainversores", "potenciainversorkw"),
    }
    saida = {}
    for destino, nomes in aliases.items():
        origem = next((mapa[n] for n in nomes if n in mapa), None)
        if origem:
            saida[destino] = origem
    if any(c not in saida for c in ("codigo", "modulos", "inversores")):
        raise ErroBDGD(
            "DADOS_GD_INCOMPLETOS",
            "O arquivo tecnico precisa de CodGeracaoDistribuida, MdaPotenciaModulos e MdaPotenciaInversores.",
        )
    return saida


class _Soma:
    """Acumula kW de modulos, kVA de inversores e numero de linhas por CEG."""

    def __init__(self, codigos):
        self.procurados = {_chave(c) for c in codigos if _chave(c)}
        self.valores = {}

    def adicionar(self, quadro, colunas):
        chaves = quadro[colunas["codigo"]].map(_chave)
        achados = quadro[chaves.isin(self.procurados)]
        for chave, modulos, inversores in zip(chaves[achados.index], achados[colunas["modulos"]],
                                              achados[colunas["inversores"]]):
            kw, kva, n = self.valores.get(chave, (0.0, 0.0, 0))
            self.valores[chave] = (kw + (_numero(modulos) or 0.0), kva + (_numero(inversores) or 0.0), n + 1)


def _ler_csv_fluxo(fluxo, soma):
    amostra = fluxo.read(65536)
    fluxo.seek(0)
    if isinstance(amostra, bytes):
        codificacao = "utf-8-sig"
        try:
            texto = amostra.decode(codificacao)
        except UnicodeDecodeError:
            codificacao, texto = "cp1252", amostra.decode("cp1252")
        fluxo = TextIOWrapper(fluxo, encoding=codificacao, errors="strict")
    else:
        texto = amostra
    try:
        separador = csv.Sniffer().sniff(texto, delimiters=",;\t|").delimiter
    except csv.Error:
        separador = ";"
    colunas = None
    for parte in pd.read_csv(fluxo, sep=separador, dtype=str, chunksize=200_000, low_memory=False):
        colunas = colunas or _resolver_colunas(parte.columns)
        soma.adicionar(parte, colunas)
    if colunas is None:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "O arquivo tecnico da ANEEL esta vazio.")


def _ler_parquet(fonte, soma):
    quadro = pd.read_parquet(fonte)
    soma.adicionar(quadro, _resolver_colunas(quadro.columns))


def somar_potencias(caminho: Path, codigos) -> dict[str, tuple[float, float, int]]:
    """Varre o arquivo uma vez e soma as potencias de cada CEG pedido.

    Devolve {chave normalizada do CEG: (kW modulos, kVA inversores, linhas)}.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "Informe um arquivo tecnico da ANEEL existente.")
    soma = _Soma(codigos)
    if not soma.procurados:
        return {}
    try:
        if caminho.suffix.casefold() == ".parquet":
            _ler_parquet(caminho, soma)
        elif caminho.suffix.casefold() == ".zip":
            with zipfile.ZipFile(caminho) as arquivo:
                nomes = [n for n in arquivo.namelist() if n.casefold().endswith((".csv", ".parquet")) and not n.endswith("/")]
                if not nomes:
                    raise ErroBDGD("DADOS_GD_INCOMPLETOS", "O ZIP nao contem CSV ou Parquet.")
                for nome in nomes:
                    with arquivo.open(nome) as fluxo:
                        if nome.casefold().endswith(".parquet"):
                            _ler_parquet(BytesIO(fluxo.read()), soma)
                        else:
                            _ler_csv_fluxo(fluxo, soma)
        else:
            with caminho.open("rb") as fluxo:
                _ler_csv_fluxo(fluxo, soma)
    except ErroBDGD:
        raise
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"Nao foi possivel ler o arquivo tecnico: {exc}") from exc
    return soma.valores


def buscar_equipamento_aneel(caminho: Path, codigo_gd: str) -> EquipamentoGD:
    if not Path(caminho).is_file() or not str(codigo_gd).strip():
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "Informe o arquivo tecnico ANEEL e um CEG GD.")
    encontrado = somar_potencias(caminho, [codigo_gd]).get(_chave(codigo_gd))
    if encontrado is None:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"CEG GD {codigo_gd} nao encontrado no arquivo tecnico.")
    modulos, inversores, linhas = encontrado
    if modulos <= 0 or inversores <= 0:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "A GD foi encontrada, mas suas potencias de modulos/inversores sao invalidas.")
    fonte = f"{caminho} · soma de {linhas} linha(s) do CEG GD"
    return EquipamentoGD(str(codigo_gd).strip(), modulos, inversores, fonte, linhas)
