"""Cadastro público de equipamentos FV, com cópia local e consulta por CEG GD."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ..core.errors import ErroBDGD
from ..core.jobs import Cancelamento, sem_progresso
from ..data.repositories.gd import (EquipamentoGD, _chave, _numero, _resolver_colunas, buscar_equipamento_aneel,
                                    somar_potencias)


def _chave_texto(nome):
    return _chave(nome)

CATALOGO_URL = "https://dadosabertos.aneel.gov.br/api/3/action/package_show?id=relacao-de-empreendimentos-de-geracao-distribuida"
RECURSO = "empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet"
COLUNAS = ["CodGeracaoDistribuida", "MdaPotenciaModulos", "MdaPotenciaInversores", "DatConexao", "DatGeracaoConjuntoDados"]
MAX_BYTES = 1024 * 1024 * 1024


def _url_publica(url):
    partes = urlparse(url)
    if partes.scheme != "https" or partes.hostname != "dadosabertos.aneel.gov.br":
        raise ValueError("Endereço fora do portal oficial da ANEEL.")
    return url


def _abrir(url):
    response = urlopen(Request(_url_publica(url), headers={"User-Agent": "CurvaGD/0.7"}), timeout=30)
    try:
        _url_publica(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def preparar_base(origem, destino, *, metadados=None, token=None, progress=sem_progresso):
    """Reduz o arquivo público às colunas de consulta, mantendo valores sem conversão de escala.

    A amostra oficial (8 módulos de 250 W, potência 2,0) confirma kW, apesar
    do erro 'MW (quilowatt)' no dicionário. Não se multiplica a coluna por 1000.
    """
    token = token or Cancelamento()
    arquivo = pq.ParquetFile(origem)
    try:
        if not set(COLUNAS).issubset(arquivo.schema_arrow.names):
            raise ValueError("A base não contém as colunas técnicas fotovoltaicas esperadas.")
        schema = pa.schema([("codigo", pa.string()), ("modulos", pa.float64()),
                            ("inversores", pa.float64()), ("conexao", pa.string()), ("publicacao", pa.string())],
                           metadata={b"fonte": json.dumps(metadados or {}, ensure_ascii=False).encode("utf-8")})
        total = arquivo.metadata.num_rows
        if total == 0:
            raise ValueError("A base técnica está vazia.")
        atual = 0
        with pq.ParquetWriter(destino, schema, compression="zstd") as writer:
            for batch in arquivo.iter_batches(batch_size=65536, columns=COLUNAS):
                token.verificar()
                cols = {name: batch.column(name) for name in COLUNAS}
                # Códigos CEG usam caracteres ASCII; a mesma normalização é usada na consulta.
                codigo = pc.replace_substring_regex(pc.utf8_lower(pc.cast(cols[COLUNAS[0]], pa.string())), "[^a-z0-9]", "")
                tabela = pa.Table.from_arrays([codigo, pc.cast(cols[COLUNAS[1]], pa.float64()),
                    pc.cast(cols[COLUNAS[2]], pa.float64()), pc.cast(cols[COLUNAS[3]], pa.string()),
                    pc.cast(cols[COLUNAS[4]], pa.string())], schema=schema)
                writer.write_table(tabela.sort_by("codigo"))
                atual += batch.num_rows
                progress("Preparando consultas de equipamentos", atual, total)
        token.verificar()
    finally:
        arquivo.close()


def _numeros(serie):
    """Coluna de potencia como float, aceitando virgula decimal (como `gd._numero`)."""
    texto = serie.astype("string").str.strip().str.replace(" ", "", regex=False)
    ambos = texto.str.contains(",", regex=False) & texto.str.contains(".", regex=False)
    texto = texto.where(~ambos.fillna(False), texto.str.replace(".", "", regex=False))
    texto = texto.str.replace(",", ".", regex=False)
    valores = pd.to_numeric(texto, errors="coerce").astype("float64")
    return valores.where(np.isfinite(valores) & (valores > 0))


def _blocos_csv(fluxo):
    """DataFrames de texto de um CSV da ANEEL, com codificacao e separador detectados."""
    amostra = fluxo.read(65536)
    fluxo.seek(0)
    codificacao = "utf-8-sig"
    try:
        texto = amostra.decode(codificacao)
    except UnicodeDecodeError:
        codificacao, texto = "cp1252", amostra.decode("cp1252", errors="replace")
    try:
        separador = csv.Sniffer().sniff(texto, delimiters=",;\t|").delimiter
    except csv.Error:
        separador = ";"
    yield from pd.read_csv(TextIOWrapper(fluxo, encoding=codificacao, errors="replace"), sep=separador,
                           dtype=str, chunksize=200_000, low_memory=False)


def _blocos_parquet(fonte):
    arquivo = pq.ParquetFile(fonte)
    try:
        for batch in arquivo.iter_batches(batch_size=200_000):
            yield batch.to_pandas().astype("string")
    finally:
        arquivo.close()


def converter_arquivo_tecnico(origem, pasta, *, token=None, progress=sem_progresso):
    """Arquivo tecnico escolhido (CSV, ZIP ou Parquet) -> ``equipamentos.parquet``.

    Grava o mesmo formato da base baixada do portal (codigo normalizado, kW de
    modulos, kVA de inversores), entao UCs e Correcao consultam a mesma copia.
    """
    token = token or Cancelamento()
    origem, pasta = Path(origem), Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    preparado = pasta / f"consulta-{uuid4().hex}.parquet"
    schema = pa.schema([("codigo", pa.string()), ("modulos", pa.float64()), ("inversores", pa.float64()),
                        ("conexao", pa.string()), ("publicacao", pa.string())],
                       metadata={b"fonte": json.dumps({"arquivo": str(origem)}, ensure_ascii=False).encode("utf-8")})

    def blocos():
        sufixo = origem.suffix.casefold()
        if sufixo == ".parquet":
            yield from _blocos_parquet(origem)
        elif sufixo == ".zip":
            with zipfile.ZipFile(origem) as arquivo:
                nomes = [n for n in arquivo.namelist()
                         if n.casefold().endswith((".csv", ".parquet")) and not n.endswith("/")]
                if not nomes:
                    raise ErroBDGD("DADOS_GD_INCOMPLETOS", "O ZIP da ANEEL não contém CSV ou Parquet.")
                for nome in nomes:
                    with arquivo.open(nome) as fluxo:
                        if nome.casefold().endswith(".parquet"):
                            yield from _blocos_parquet(BytesIO(fluxo.read()))
                        else:
                            yield from _blocos_csv(BytesIO(fluxo.read()))
        else:
            with origem.open("rb") as fluxo:
                yield from _blocos_csv(fluxo)

    linhas = 0
    try:
        with pq.ParquetWriter(preparado, schema, compression="zstd") as writer:
            for quadro in blocos():
                token.verificar()
                colunas = _resolver_colunas(quadro.columns)
                mapa = {_chave_texto(c): c for c in quadro.columns}
                conexao = mapa.get("datconexao")
                publicacao = mapa.get("datgeracaoconjuntodados")
                codigo = (quadro[colunas["codigo"]].astype("string").fillna("")
                          .str.normalize("NFKD").str.casefold().str.replace(r"[^0-9a-z]", "", regex=True))
                tabela = pd.DataFrame({
                    "codigo": codigo.astype(object),
                    "modulos": _numeros(quadro[colunas["modulos"]]),
                    "inversores": _numeros(quadro[colunas["inversores"]]),
                    "conexao": (quadro[conexao].astype(object) if conexao else None),
                    "publicacao": (quadro[publicacao].astype(object) if publicacao else origem.name),
                })
                tabela = tabela[tabela["codigo"] != ""].sort_values("codigo", kind="stable")
                writer.write_table(pa.Table.from_pandas(tabela, schema=schema, preserve_index=False))
                linhas += len(tabela)
                progress("Preparando a base técnica da ANEEL", linhas, 0)
        if not linhas:
            raise ErroBDGD("DADOS_GD_INCOMPLETOS", "O arquivo técnico da ANEEL está vazio.")
        token.verificar()
        preparado.replace(pasta / "equipamentos.parquet")
    except ErroBDGD:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, zipfile.BadZipFile, pa.ArrowException) as exc:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"Não foi possível ler o arquivo técnico da ANEEL: {exc}") from exc
    finally:
        preparado.unlink(missing_ok=True)
    return linhas


def atualizar_base(pasta, *, token=None, progress=sem_progresso):
    token = token or Cancelamento()
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    identificador = uuid4().hex
    download = pasta / f"download-{identificador}.parquet"
    preparado = pasta / f"consulta-{identificador}.parquet"
    try:
        token.verificar()
        progress("Consultando catálogo público da ANEEL", 0, 0)
        with _abrir(CATALOGO_URL) as response:
            catalogo = json.loads(response.read(4 * 1024 * 1024))
        recurso = next(r for r in catalogo["result"]["resources"]
                       if r.get("name", "").lower() == RECURSO and r.get("format", "").upper() == "PARQUET")
        url = _url_publica(recurso["url"])
        with _abrir(url) as response, download.open("wb") as stream:
            total = int(response.headers.get("Content-Length", 0))
            if total > MAX_BYTES:
                raise ValueError("O arquivo excede o limite de download de 1 GB.")
            atual = 0
            while True:
                token.verificar()
                bloco = response.read(1024 * 1024)
                if not bloco:
                    break
                atual += len(bloco)
                if atual > MAX_BYTES:
                    raise ValueError("O arquivo excede o limite de download de 1 GB.")
                stream.write(bloco)
                progress("Baixando cadastro técnico da ANEEL", atual, total)
            if total and atual != total:
                raise ValueError("Download incompleto. A cópia anterior foi preservada.")
        preparar_base(download, preparado, metadados={"url": url, "recurso": recurso["id"],
                       "consulta_utc": datetime.now(timezone.utc).isoformat()}, token=token, progress=progress)
        token.verificar()
        preparado.replace(pasta / "equipamentos.parquet")
    except ErroBDGD:
        raise
    except Exception as exc:
        raise ErroBDGD("ANEEL_INDISPONIVEL", f"Não foi possível atualizar a base da ANEEL: {exc}") from exc
    finally:
        download.unlink(missing_ok=True)
        preparado.unlink(missing_ok=True)


def _somar_cache(caminho, codigos):
    """Soma, por CEG, as potencias de todas as linhas da copia local da ANEEL."""
    chaves = sorted({_chave(c) for c in codigos if _chave(c)})
    if not chaves:
        return {}, ""
    tabela = pq.read_table(caminho, filters=[("codigo", "in", chaves)])
    publicacao = ""
    somas = {}
    for r in tabela.to_pylist():
        kw, kva, n = somas.get(r["codigo"], (0.0, 0.0, 0))
        somas[r["codigo"]] = (kw + (_numero(r["modulos"]) or 0.0), kva + (_numero(r["inversores"]) or 0.0), n + 1)
        publicacao = max(publicacao, r["publicacao"] or "")
    return somas, publicacao or "não informada"


def _consultar(caminho, codigo):
    somas, publicacao = _somar_cache(caminho, [codigo])
    encontrado = somas.get(_chave(codigo))
    if encontrado is None:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"Dados não encontrados para esta UC (CEG GD {codigo}). Informe as potências manualmente ou selecione outro arquivo técnico.")
    modulos, inversores, linhas = encontrado
    if modulos <= 0 or inversores <= 0:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "A UC foi localizada, mas as potências cadastradas estão ausentes ou inválidas. Informe os valores manualmente.")
    return EquipamentoGD(codigo, modulos, inversores,
        f"ANEEL · publicação {publicacao} · CEG GD {codigo} · soma de {linhas} linha(s) · {caminho}", linhas)


def potencias_em_lote(workspace, codigos, *, caminho="", token=None, progress=sem_progresso):
    """Potências de vários CEG GD de uma vez: {chave: (kW, kVA, linhas)}, fonte e aviso.

    Com `caminho`, varre esse arquivo técnico uma única vez; sem ele, usa apenas
    a cópia local criada a partir do arquivo escolhido pelo usuário.
    """
    token = token or Cancelamento()
    codigos = [c for c in codigos if _chave(c)]
    if not codigos:
        return {}, "", ""
    if caminho:
        progress("Somando potências no arquivo técnico da ANEEL", 0, 0)
        return somar_potencias(Path(caminho), codigos), str(caminho), ""
    pasta = Path(workspace) / "solar_aneel"
    cache = pasta / "equipamentos.parquet"
    aviso = ""
    if not cache.is_file():
        return {}, "", "Base da ANEEL não informada. Potências buscadas no POT_INST das UGs."
    token.verificar()
    somas, publicacao = _somar_cache(cache, codigos)
    return somas, f"ANEEL · publicação {publicacao} · {cache}", aviso


def _preferencias(workspace):
    try:
        valor = json.loads((Path(workspace) / "solar_aneel" / "preferencias.json").read_text(encoding="utf-8"))
        return valor if isinstance(valor, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def carregar_preferencia(workspace, campo="arquivo_tecnico"):
    return str(_preferencias(workspace).get(campo, "") or "")


def salvar_preferencia(workspace, caminho, campo="arquivo_tecnico"):
    pasta = Path(workspace) / "solar_aneel"
    pasta.mkdir(parents=True, exist_ok=True)
    valores = {**_preferencias(workspace), campo: str(caminho)}
    temporario = pasta / f"preferencias-{uuid4().hex}.json"
    temporario.write_text(json.dumps(valores, ensure_ascii=False), encoding="utf-8")
    temporario.replace(pasta / "preferencias.json")


def buscar_potencias(workspace, codigo, *, caminho="", token=None, progress=sem_progresso):
    token = token or Cancelamento()
    token.verificar()
    if not _chave(codigo):
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "A UC não possui CEG GD para consulta automática.")
    if caminho:
        progress("Consultando arquivo técnico da ANEEL", 0, 0)
        equipamento = buscar_equipamento_aneel(Path(caminho), codigo)
        token.verificar()
        return {"equipamento": equipamento, "aviso": "", "arquivo": caminho}
    pasta = Path(workspace) / "solar_aneel"
    cache = pasta / "equipamentos.parquet"
    aviso = ""
    if not cache.is_file():
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "Selecione e importe o arquivo técnico da ANEEL em Dados de entrada.")
    token.verificar()
    progress("Localizando equipamentos da UC", 0, 0)
    try:
        equipamento = _consultar(cache, codigo)
    except ErroBDGD:
        raise
    except Exception as exc:
        raise ErroBDGD("DADOS_GD_INCOMPLETOS", "Cópia local inválida. Selecione e importe novamente o arquivo técnico da ANEEL.") from exc
    return {"equipamento": equipamento, "aviso": aviso, "arquivo": ""}
