"""Os dados de entrada do CurvaGD: uma BDGD, a irradiancia da NASA e a base da ANEEL.

A tela inicial pede os tres arquivos uma unica vez; todos os modos (regioes,
mapa, unidades consumidoras e correcao de demanda) leem daqui. Nao ha historico:
importar uma pasta nova substitui a anterior, inclusive no disco.

Os arquivos que o programa usa ficam copiados no workspace:

- ``entrada/irradiancia_nasa.csv``: a copia do CSV da NASA escolhido pelo usuario;
- ``solar_aneel/equipamentos.parquet``: a base tecnica de GD da ANEEL reduzida as
  colunas de consulta -- montada a partir do arquivo escolhido pelo usuario.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import shutil
from uuid import uuid4

from ..calculo import gd as gd_calc
from ..calculo import irradiancia
from ..calculo.potencias import RAZAO_PADRAO
from ..core.errors import ErroBDGD
from ..core.jobs import sem_progresso

ARQUIVO = "projeto.json"
PASTA_ENTRADA = "entrada"
NASA_LOCAL = "irradiancia_nasa.csv"


@dataclass
class Projeto:
    bdgd: str = ""
    nasa: str = ""                # CSV escolhido pelo usuario (so para exibir)
    aneel: str = ""               # arquivo tecnico escolhido; vazio = reserva POT_INST
    distribuidora: str = "ENERGISA_MT"
    ano: int = 2024
    import_id: str = ""
    importado_em: str = ""
    aneel_fonte: str = ""         # descricao da base ANEEL que ficou no workspace
    aneel_aviso: str = ""
    performance_ratio: float = gd_calc.PERFORMANCE_RATIO_PADRAO
    cut_in: float = gd_calc.CUTIN_PCT_PADRAO
    razao_kw_kva: float = RAZAO_PADRAO

    @classmethod
    def carregar(cls, workspace):
        try:
            dados = json.loads((Path(workspace) / ARQUIVO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(dados, dict):
            return None
        conhecidos = {campo.name for campo in fields(cls)}
        projeto = cls(**{k: v for k, v in dados.items() if k in conhecidos})
        projeto.parametros_validos()
        return projeto

    def salvar(self, workspace):
        pasta = Path(workspace)
        pasta.mkdir(parents=True, exist_ok=True)
        temporario = pasta / f"projeto-{uuid4().hex}.tmp"
        temporario.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporario.replace(pasta / ARQUIVO)

    def parametros_validos(self):
        """Volta para o padrao qualquer parametro solar fora da faixa."""
        for nome, minimo, maximo, padrao in (
            ("performance_ratio", 0.01, 1.0, gd_calc.PERFORMANCE_RATIO_PADRAO),
            ("cut_in", 0.0, 50.0, gd_calc.CUTIN_PCT_PADRAO),
            ("razao_kw_kva", 0.1, 5.0, RAZAO_PADRAO),
        ):
            valor = getattr(self, nome)
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) \
                    or not minimo <= valor <= maximo:
                setattr(self, nome, padrao)

    def parametros_solares(self):
        return {"performance_ratio": float(self.performance_ratio), "cut_in_percentual": float(self.cut_in),
                "razao_kw_kva": float(self.razao_kw_kva)}


def nasa_local(workspace):
    return Path(workspace) / PASTA_ENTRADA / NASA_LOCAL


def aneel_local(workspace):
    return Path(workspace) / "solar_aneel" / "equipamentos.parquet"


def ano_sugerido(caminho_bdgd):
    """Ano-base pelo nome da BDGD (ex.: Energisa_MT_405_2024-12-31_...gdb)."""
    achado = re.search(r"(19|20)\d{2}(?=-12-31)", Path(str(caminho_bdgd)).name)
    return int(achado.group(0)) if achado else None


def validar_nasa(caminho, ano):
    """Le o CSV e confere que ele cobre o ano-base. Devolve o diagnostico."""
    if not caminho or not Path(caminho).is_file():
        raise ErroBDGD("IRRADIANCIA_INVALIDA", "Selecione o CSV horário de irradiância da NASA POWER.")
    try:
        serie, diagnostico = irradiancia.ler_csv_nasa(caminho)
        irradiancia.serie_ano(serie, ano)
    except (ValueError, KeyError) as exc:
        raise ErroBDGD("IRRADIANCIA_INVALIDA", f"CSV da NASA inválido para {ano}: {exc}") from exc
    return diagnostico


def copiar_nasa(caminho, workspace):
    destino = nasa_local(workspace)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f"nasa-{uuid4().hex}.tmp")
    shutil.copyfile(caminho, temporario)
    temporario.replace(destino)
    return destino


def preparar_indice_bdgd(caminho, workspace, *, token=None, progress=sem_progresso):
    """Monta o indice alimentador -> registros do .gdb usado pela Correcao de demanda."""
    from ..correcao import bdgd

    caminho = Path(caminho)
    if caminho.suffix.lower() not in (".gdb", ".gpkg"):
        return
    bdgd.definir_pasta_cache(Path(workspace) / "cache_bdgd")
    fonte = bdgd.abrir(caminho)

    def avisar(etapa, feito=0, total=0):
        if token:
            token.verificar()
        progress(f"Preparando a BDGD para a correção de demanda ({etapa})", feito, total)

    try:
        fonte.preparar(avisar)
    finally:
        fonte.fechar()


def remover_versoes_antigas(catalog, manter, workspace):
    """Apaga do catalogo e do disco toda importacao diferente de ``manter``."""
    removidas = []
    for versao in catalog.list():
        if versao["import_id"] == manter:
            continue
        try:
            catalog.remover(versao["import_id"])
            removidas.append(versao["import_id"])
        except Exception:
            logging.getLogger("bdgd").exception("Falha ao remover a importação %s", versao["import_id"])
    # Relatorios de importacoes antigas (inclusive as que nao chegaram ao
    # catalogo) e pastas de dados orfas tambem saem: so a base atual fica.
    for arquivo in (Path(workspace) / "reports").glob("*"):
        if arquivo.is_file() and manter not in arquivo.name:
            try:
                arquivo.unlink()
            except OSError:
                pass
    raiz = (Path(workspace) / "datasets").resolve()
    for pasta in catalog.orphans():
        pasta = Path(pasta).resolve()
        if raiz in pasta.parents and pasta.name.startswith("versao=") and manter not in pasta.name:
            shutil.rmtree(pasta, ignore_errors=True)
    if removidas:
        try:
            from ..data.repositories.tratamentos import TratamentosUC
            TratamentosUC(workspace).remover_versoes(removidas)
        except Exception:
            logging.getLogger("bdgd").exception("Falha ao limpar tratamentos antigos")
    return removidas


def agora():
    return datetime.now(timezone.utc).isoformat()
