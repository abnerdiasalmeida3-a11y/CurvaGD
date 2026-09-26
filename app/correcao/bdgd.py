"""Leitura das tabelas da BDGD, em GeoPackage (.gpkg) ou File Geodatabase (.gdb).

As tabelas que interessam aqui -- UCBT_tab, UCMT_tab, UGBT_tab, UGMT_tab, CTMT e
CRVCRG -- nao tem geometria, e o acesso e sempre o mesmo: filtrar por alimentador
e trazer um punhado de colunas. O que muda e o caminho ate elas.

`FonteGeoPackage` le pelo sqlite3, porque um GeoPackage e um SQLite: um filtro
por CTMT nos 2,1 milhoes de registros da UCBT custa menos de um segundo mesmo sem
indice.

`FonteFileGDB` le pelo driver OpenFileGDB do GDAL, via pyogrio. Ali o filtro sai
caro: o .gdb nao tem indice de atributo em nenhuma tabela de dados, entao cada
consulta por CTMT vira varredura sequencial de 40 s. A saida esta no .gdbtablx,
que mapeia FID para deslocamento no arquivo -- ler por FID e quase de graca
(3 mil linhas espalhadas em 0,09 s). Uma varredura unica de CTMT + SIT_ATIV monta
o indice {CTMT: FIDs ativos}, guardado em disco, e dai em diante cada alimentador
sai por FID mais rapido que pelo proprio GeoPackage.

Nenhuma entrada e alterada: o GeoPackage e aberto somente para leitura e o .gdb
so e lido.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from . import curvas

COLUNAS_ENE = [f"ENE_{i:02d}" for i in range(1, 13)]
COLUNAS_UC = ["COD_ID", "MUN", "CEG_GD", "TIP_CC", "GRU_TEN", "TEN_FORN",
              "FAS_CON", "CAR_INST"] + COLUNAS_ENE
COLUNAS_TEXTO_UC = ["COD_ID", "MUN", "CEG_GD", "TIP_CC", "GRU_TEN", "TEN_FORN", "FAS_CON"]
COLUNAS_NUMERO_UC = ["CAR_INST"] + COLUNAS_ENE
COLUNAS_UG = ["CEG_GD", "POT_INST"]
COLUNAS_UG_FASE = ["CEG_GD", "FAS_CON"] + COLUNAS_ENE
COLUNAS_TRAFO = ["COD_ID", "FAS_CON_P"]
# so a UCBT tem trafo MT/BT; e por ele que a carga de BT chega a uma fase do
# alimentador (ver alimentador.py)
COLUNA_TRAFO_UC = "UNI_TR_MT"
COLUNAS_CTMT = ["COD_ID", "NOME", "SUB", "TEN_NOM"]

TABELAS_UC = {"UCBT": "UCBT_tab", "UCMT": "UCMT_tab"}
TABELAS_UG = {"UGBT": "UGBT_tab", "UGMT": "UGMT_tab"}
TABELA_TRAFO = "UNTRMT"
TABELAS_OBRIGATORIAS = ["CTMT", curvas.TABELA] + list(TABELAS_UC.values())
SITUACAO_ATIVA = "AT"
COLUNA_FILTRO = "CTMT"
COLUNA_SITUACAO = "SIT_ATIV"

# Dentro do CurvaGD o cache fica no workspace (o executavel roda de uma
# pasta temporaria); `definir_pasta_cache` e chamado pela pagina ao abrir.
PASTA_CACHE = Path.home() / ".curvagd" / "cache_bdgd"


def definir_pasta_cache(pasta):
    global PASTA_CACHE
    PASTA_CACHE = Path(pasta)
# muda quando o conjunto de tabelas indexadas do .gdb muda, para um indice
# antigo -- sem a UNTRMT -- nao ser reaproveitado
VERSAO_INDICE = 2
VERSAO_ESTATISTICAS = 2
COLUNAS_ESTATISTICAS = ["TABELA", "CTMT", "CLAS_SUB", "FAS_CON", "UCS", "COM_GD",
                        "ENERGIA_KWH"]


def abrir(caminho):
    """Abre a BDGD, escolhendo a fonte pelo formato.

    Um File Geodatabase e uma PASTA cheia de arquivos .gdbtable; um GeoPackage e
    um arquivo unico. E a diferenca que decide o backend.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        raise ValueError(f"Caminho nao encontrado: {caminho}")
    if caminho.is_dir() or caminho.suffix.lower() == ".gdb":
        return FonteFileGDB(caminho)
    return FonteGeoPackage(caminho)


# ============================================================ limpezas comuns
# As duas fontes passam por aqui, para os caminhos nao divergirem no detalhe.


def _texto(serie):
    """Coluna de texto normalizada, venha ela como str do SQLite ou como numero
    do GDAL: 'string' converte sem transformar ausente em 'nan'."""
    return serie.astype("string").fillna("").astype(str).str.strip()


def _consolidar(df):
    """Uma linha por COD_ID: energia somada, resto do primeiro registro.

    A mesma UC aparece mais de uma vez quando muda de situacao no meio do ano --
    cada linha traz os meses da sua fase e zero nos demais, entao somar as
    energias reconstroi o ano. Sem isso a UC entraria duas vezes na tabela de
    saida, com meio ano de demanda cada.
    """
    repetidos = int(df["COD_ID"].duplicated().sum())
    if repetidos:
        agregacao = {c: "sum" for c in COLUNAS_ENE}
        agregacao.update({c: "first" for c in df.columns if c not in COLUNAS_ENE + ["COD_ID"]})
        df = df.groupby("COD_ID", as_index=False, sort=False).agg(agregacao)
    return df, repetidos


def _arrumar_ucs(partes):
    """Normaliza tipos, consolida cada tabela e monta o resumo da interface.

    A consolidacao e POR TABELA, e nunca sobre a juncao das duas. Na base da
    Energisa MT, 3.197 COD_ID ativos aparecem ao mesmo tempo na UCBT e na UCMT --
    em 523 dos 673 alimentadores. Nao e erro de extracao: e a mesma UC com dois
    pontos de conexao (o PAC muda, um termina em BT e o outro em MT), cada lado
    com a sua propria curva de carga (os 3.197 tem TIP_CC diferente entre os
    lados) e a sua propria energia.

    Juntar os dois por COD_ID somaria energias que pertencem a curvas diferentes.
    Em 3.061 casos a energia esta toda no lado BT e o estrago seria so perder a
    linha de MT; nos outros 135, os dois lados tem energia e a demanda da UC
    sairia inflada. Consolidando por tabela, a UC continua sendo duas linhas --
    que e o que ela e na BDGD -- e cada uma recebe a demanda da sua curva.
    """
    arrumadas, repetidos = [], 0
    for df in partes:
        if COLUNA_TRAFO_UC not in df.columns:
            df[COLUNA_TRAFO_UC] = ""       # UCMT: ligada direto na MT
        df[COLUNA_TRAFO_UC] = _texto(df[COLUNA_TRAFO_UC])
        for coluna in COLUNAS_TEXTO_UC:
            df[coluna] = _texto(df[coluna])
        for coluna in COLUNAS_NUMERO_UC:
            df[coluna] = pd.to_numeric(df[coluna], errors="coerce").fillna(0.0)
        df, repetidos_tabela = _consolidar(df)
        repetidos += repetidos_tabela
        arrumadas.append(df)

    ucs = pd.concat(arrumadas, ignore_index=True)
    ucs = ucs[["COD_ID", "TABELA"] + [c for c in COLUNAS_UC if c != "COD_ID"]
              + [COLUNA_TRAFO_UC]]

    resumo = {
        "total": len(ucs),
        "bt": int((ucs["TABELA"] == "UCBT").sum()),
        "mt": int((ucs["TABELA"] == "UCMT").sum()),
        "com_ceg": int((ucs["CEG_GD"] != "").sum()),
        "repetidos": repetidos,
        # cada tabela ja esta sem repetidos, entao o que sobra e o cruzamento
        "nos_dois": int(ucs["COD_ID"].duplicated().sum()),
    }
    return ucs, resumo


def _arrumar_ugs(partes):
    """Potencia instalada por CEG_GD, somando as UGs que dividem o mesmo codigo."""
    if not partes:
        return pd.Series(dtype=float, name="POT_INST")

    ugs = pd.concat(partes, ignore_index=True)
    ugs["CEG_GD"] = _texto(ugs["CEG_GD"])
    ugs["POT_INST"] = pd.to_numeric(ugs["POT_INST"], errors="coerce").fillna(0.0)
    ugs = ugs[ugs["CEG_GD"] != ""]
    return ugs.groupby("CEG_GD")["POT_INST"].sum()


def _arrumar_alimentadores(df):
    """Circuitos de media tensao ordenados por nome, tudo como texto."""
    for coluna in COLUNAS_CTMT:
        df[coluna] = _texto(df[coluna])
    return df[COLUNAS_CTMT].sort_values("NOME", kind="stable", ignore_index=True)


# ============================================================ consultas comuns


class _Consultas:
    """O que as duas fontes fazem do mesmo jeito, sobre a primitiva `_consultar`.

    Cada fonte so precisa saber trazer as linhas ATIVAS de uma tabela num
    alimentador (`_consultar`); o resto e escrito uma vez so, e por isso os
    dois formatos nao conseguem divergir no detalhe.
    """

    def unidades(self, ctmt):
        partes = []
        for origem, tabela in TABELAS_UC.items():
            colunas = COLUNAS_UC + ([COLUNA_TRAFO_UC] if origem == "UCBT" else [])
            df = self._consultar(tabela, colunas, ctmt)
            df.insert(1, "TABELA", origem)
            partes.append(df)
        return _arrumar_ucs(partes)

    def geradoras(self, ctmt):
        partes = [self._consultar(t, COLUNAS_UG, ctmt)
                  for t in TABELAS_UG.values() if t in self.tabelas]
        return _arrumar_ugs(partes)

    def trafos(self, ctmt):
        """Fases do primario de cada trafo MT/BT ativo do alimentador."""
        if TABELA_TRAFO not in self.tabelas:
            return pd.DataFrame(columns=COLUNAS_TRAFO)
        df = self._consultar(TABELA_TRAFO, COLUNAS_TRAFO, ctmt)
        for coluna in COLUNAS_TRAFO:
            df[coluna] = _texto(df[coluna])
        return df.drop_duplicates("COD_ID").reset_index(drop=True)

    def estatisticas(self, progresso=None):
        """Contagem de UCs ativas por alimentador, subclasse e fases, da base inteira.

        Uma linha por (tabela, CTMT, CLAS_SUB, FAS_CON), com o numero de UCs
        distintas, quantas tem GD e a energia anual somada. E so contagem --
        nenhuma demanda e calculada --, entao cobre os 673 alimentadores de uma
        vez: e o que permite comparar perfis lado a lado.

        A mesma consulta SQL roda nas duas fontes (no .gdb pelo dialeto SQLite do
        GDAL), e o resultado vai para o cache: 7 s no .gpkg e 60 s no .gdb na
        primeira vez, instantaneo dali em diante.
        """
        arquivo = PASTA_CACHE / (f"{self.caminho.stem}-{self._assinatura()}"
                                 f"-estatisticas-v{VERSAO_ESTATISTICAS}.csv")
        if arquivo.exists():
            return pd.read_csv(arquivo, dtype={"CTMT": str, "CLAS_SUB": str, "FAS_CON": str},
                               keep_default_na=False)

        energia = " + ".join(f"COALESCE({c}, 0)" for c in COLUNAS_ENE)
        partes = []
        for n, (origem, tabela) in enumerate(TABELAS_UC.items()):
            if progresso is not None:
                progresso("contando as UCs da base", n, len(TABELAS_UC))
            # primeiro uma linha por UC em cada alimentador, como na aba Demanda:
            # quem muda de subclasse no meio do ano (RE1 -> RE2, por exemplo) tem
            # uma linha ativa para cada fase do ano, e contaria duas vezes. A
            # energia soma as duas; a subclasse e as fases ficam com MIN, que e
            # deterministico nas duas fontes
            consulta = (
                f"SELECT CTMT, CLAS_SUB, FAS_CON, COUNT(*) AS UCS, "
                f"SUM(TEM_GD) AS COM_GD, SUM(ENERGIA) AS ENERGIA_KWH FROM ("
                f"SELECT {COLUNA_FILTRO} AS CTMT, MIN(CLAS_SUB) AS CLAS_SUB, "
                f"MIN(FAS_CON) AS FAS_CON, "
                f"MAX(CASE WHEN TRIM(COALESCE(CEG_GD, '')) <> '' THEN 1 ELSE 0 END) AS TEM_GD, "
                f"SUM({energia}) AS ENERGIA "
                f"FROM \"{self.tabelas[tabela]}\" "
                f"WHERE {COLUNA_SITUACAO} = '{SITUACAO_ATIVA}' "
                f"GROUP BY COD_ID, {COLUNA_FILTRO}"
                f") GROUP BY CTMT, CLAS_SUB, FAS_CON"
            )
            df = self._agregar(consulta)
            df.insert(0, "TABELA", origem)
            partes.append(df)
        brutas = pd.concat(partes, ignore_index=True)
        for coluna in ("CTMT", "CLAS_SUB", "FAS_CON"):
            brutas[coluna] = _texto(brutas[coluna])
        brutas = brutas[COLUNAS_ESTATISTICAS]

        arquivo.parent.mkdir(parents=True, exist_ok=True)
        brutas.to_csv(arquivo, index=False)
        return brutas

    def geradoras_fases(self, ctmt):
        """Por CEG_GD: letras de fase das UGs (uniao) e energia injetada no ano.

        Um mesmo CEG_GD pode ter varias UGs, em fases diferentes; a geracao do
        conjunto sai pela uniao delas.
        """
        partes = [self._consultar(t, COLUNAS_UG_FASE, ctmt)
                  for t in TABELAS_UG.values() if t in self.tabelas]
        if not partes:
            return pd.DataFrame(columns=["FASES", "ENE_INJ"])
        ugs = pd.concat(partes, ignore_index=True)
        ugs["CEG_GD"] = _texto(ugs["CEG_GD"])
        ugs = ugs[ugs["CEG_GD"] != ""]
        ugs["ENE_INJ"] = ugs[COLUNAS_ENE].apply(
            pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1)
        ugs["FAS_CON"] = _texto(ugs["FAS_CON"])
        return ugs.groupby("CEG_GD").agg(
            FASES=("FAS_CON", lambda s: "".join(f for f in "ABC" if f in "".join(s))),
            ENE_INJ=("ENE_INJ", "sum"),
        )


# ============================================================ GeoPackage


class FonteGeoPackage(_Consultas):
    """BDGD num GeoPackage: SQLite lido direto, sem GDAL no caminho."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        # check_same_thread=False porque a interface le a base numa QThread; os
        # acessos sao serializados (um trabalho de cada vez) e o arquivo e read-only
        self.con = sqlite3.connect(
            f"file:{self.caminho}?mode=ro", uri=True, check_same_thread=False
        )
        try:
            nomes = [linha[0] for linha in self.con.execute(
                "SELECT table_name FROM gpkg_contents")]
        except sqlite3.DatabaseError as erro:
            self.con.close()
            raise ValueError(f"O arquivo nao parece ser um GeoPackage: {erro}") from erro

        # os nomes vem prefixados pelo nome da extracao e separados por travessao
        # ("Energisa_MT_405_... - UCBT_tab"), e o prefixo muda a cada versao
        self.tabelas = {nome.split(" ")[-1]: nome for nome in nomes}
        faltando = [n for n in TABELAS_OBRIGATORIAS if n not in self.tabelas]
        if faltando:
            self.con.close()
            raise ValueError(
                f"O GeoPackage nao tem a(s) tabela(s) {', '.join(faltando)}. "
                "Ele e mesmo uma BDGD?"
            )

    def preparar(self, progresso=None):
        """Nada a preparar: o SQLite ja filtra por CTMT em menos de um segundo."""

    def alimentadores(self):
        df = pd.read_sql(
            f'SELECT {", ".join(COLUNAS_CTMT)} FROM "{self.tabelas["CTMT"]}"', self.con,
            dtype=str,
        )
        return _arrumar_alimentadores(df)

    def _assinatura(self):
        """Tamanho e data do arquivo, para invalidar o cache das estatisticas."""
        estado = self.caminho.stat()
        return f"{estado.st_size}-{int(estado.st_mtime)}"

    def _agregar(self, consulta):
        return pd.read_sql(consulta, self.con)

    def _consultar(self, tabela, colunas, ctmt):
        return pd.read_sql(
            f'SELECT {", ".join(colunas)} FROM "{self.tabelas[tabela]}" '
            f"WHERE {COLUNA_FILTRO} = ? AND {COLUNA_SITUACAO} = ?",
            self.con, params=(str(ctmt), SITUACAO_ATIVA),
        )

    def banco_de_curvas(self):
        return curvas.banco_do_gpkg(self.con, self.tabelas)

    def fechar(self):
        self.con.close()


# ============================================================ File Geodatabase


class FonteFileGDB(_Consultas):
    """BDGD num File Geodatabase, pelo driver OpenFileGDB do GDAL.

    O `pyogrio` e importado sob demanda: quem trabalha so com GeoPackage nao
    precisa ter GDAL instalado. A leitura e pelo Arrow, sem geopandas.
    """

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self._indice = None
        pyogrio = self._pyogrio()

        try:
            camadas = {nome for nome, _ in pyogrio.list_layers(self.caminho)}
        except Exception as erro:
            raise ValueError(
                f"Nao foi possivel abrir o File Geodatabase: {erro}"
            ) from erro

        self.tabelas = {nome: nome for nome in camadas}
        faltando = [n for n in TABELAS_OBRIGATORIAS if n not in self.tabelas]
        if faltando:
            raise ValueError(
                f"O File Geodatabase nao tem a(s) tabela(s) {', '.join(faltando)}. "
                "Ele e mesmo uma BDGD?"
            )

    @staticmethod
    def _pyogrio():
        try:
            import pyogrio
        except ImportError as erro:
            raise ValueError(
                "Ler um File Geodatabase (.gdb) precisa do pyogrio: pip install pyogrio"
            ) from erro
        return pyogrio

    def _quadro(self, **opcoes):
        """Le pelo `read_arrow` e converte para pandas.

        Nao usa `pyogrio.read_dataframe`, que exige o geopandas mesmo sem
        geometria -- e o geopandas nao vai no executavel. As tabelas da BDGD lidas
        aqui nao tem geometria; se alguma tiver, a coluna e descartada.
        """
        meta, tabela = self._pyogrio().read_arrow(self.caminho, read_geometry=False, **opcoes)
        df = tabela.to_pandas()
        geometria = meta.get("geometry_name")
        if geometria and geometria in df.columns:
            df = df.drop(columns=[geometria])
        return df, meta

    # ---------------------------------------------------------------- indice

    def _assinatura(self):
        """Tamanho total e data mais recente da pasta, para invalidar o indice."""
        total, recente = 0, 0.0
        for arquivo in self.caminho.iterdir():
            if arquivo.is_file():
                estado = arquivo.stat()
                total += estado.st_size
                recente = max(recente, estado.st_mtime)
        return f"{total}-{int(recente)}"

    def _arquivo_indice(self):
        return PASTA_CACHE / (f"{self.caminho.stem}-{self._assinatura()}"
                              f"-v{VERSAO_INDICE}.npz")

    def preparar(self, progresso=None):
        """Garante o indice CTMT -> FIDs, lendo do cache ou montando na hora.

        A montagem le so CTMT e SIT_ATIV das quatro tabelas -- cerca de 25 s --
        e vale para a vida do arquivo. Chamadas seguintes nao custam nada.
        """
        if self._indice is not None:
            return

        arquivo = self._arquivo_indice()
        if arquivo.exists():
            with np.load(arquivo, allow_pickle=False) as dados:
                self._indice = {
                    tabela: (dados[f"{tabela}|codigos"], dados[f"{tabela}|inicios"],
                             dados[f"{tabela}|fids"])
                    for tabela in self._tabelas_indexadas()
                }
            return

        self._indice = {}
        tabelas = self._tabelas_indexadas()
        for feito, tabela in enumerate(tabelas):
            if progresso is not None:
                progresso("indexando o .gdb", feito, len(tabelas))
            self._indice[tabela] = self._indexar(tabela)
        if progresso is not None:
            progresso("indexando o .gdb", len(tabelas), len(tabelas))

        arquivo.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(arquivo, **{
            f"{tabela}|{parte}": valor
            for tabela, partes in self._indice.items()
            for parte, valor in zip(("codigos", "inicios", "fids"), partes)
        })

    def _tabelas_indexadas(self):
        return [t for t in list(TABELAS_UC.values()) + list(TABELAS_UG.values())
                + [TABELA_TRAFO]
                if t in self.tabelas]

    def _indexar(self, tabela):
        """Varre CTMT + SIT_ATIV e devolve o indice no formato CSR.

        `codigos` sao os CTMT ordenados, `inicios` os deslocamentos de cada um em
        `fids`, e `fids` os identificadores das linhas ATIVAS. Guardar so as
        ativas ja aplica o filtro de situacao que as consultas fariam depois.
        """
        df, meta = self._quadro(layer=tabela, columns=[COLUNA_FILTRO, COLUNA_SITUACAO], return_fids=True)
        coluna_fid = meta.get("fid_column") or "OGC_FID"
        if coluna_fid not in df.columns:
            coluna_fid = next(c for c in df.columns if c not in (COLUNA_FILTRO, COLUNA_SITUACAO))
        ativos = (_texto(df[COLUNA_SITUACAO]) == SITUACAO_ATIVA).to_numpy()
        chaves = _texto(df[COLUNA_FILTRO]).to_numpy()[ativos]
        fids = df[coluna_fid].to_numpy(dtype=np.int64)[ativos]

        ordem = np.argsort(chaves, kind="stable")
        chaves, fids = chaves[ordem], fids[ordem]
        codigos, inicios = np.unique(chaves, return_index=True)
        return (codigos.astype(str), np.append(inicios, len(chaves)), fids)

    def _fids(self, tabela, ctmt):
        """FIDs das linhas ativas do alimentador, por busca binaria no indice."""
        codigos, inicios, fids = self._indice[tabela]
        alvo = str(ctmt).strip()
        posicao = int(np.searchsorted(codigos, alvo))
        if posicao >= len(codigos) or codigos[posicao] != alvo:
            return fids[:0]
        return fids[inicios[posicao]:inicios[posicao + 1]]

    # ---------------------------------------------------------------- leitura

    def _ler(self, tabela, colunas=None, fids=None):
        """Le uma camada inteira, ou so as linhas dos FIDs pedidos.

        O GDAL devolve as colunas na ordem da camada, nao na ordem pedida, entao
        o reindex no fim importa. E cuidado ao trocar isto por um filtro `where`:
        no pyogrio, um campo usado em `where` precisa estar em `columns`, senao a
        consulta devolve zero linhas em silencio.
        """
        if fids is not None and len(fids) == 0:
            return pd.DataFrame(columns=list(colunas or []))

        extra = {} if fids is None else {"fids": np.asarray(fids, dtype=np.int64)}
        if colunas is not None:
            extra["columns"] = list(colunas)
        df, _ = self._quadro(layer=tabela, **extra)
        return df if colunas is None else df.reindex(columns=list(colunas))

    def alimentadores(self):
        return _arrumar_alimentadores(self._ler("CTMT", COLUNAS_CTMT))

    def _agregar(self, consulta):
        """Roda SQL de SQLite sobre as camadas do .gdb.

        O GDAL expoe cada camada como tabela virtual; o GROUP BY acontece la
        dentro, sem trazer os 2,1 milhoes de registros da UCBT para o pandas.
        """
        df, _ = self._quadro(sql=consulta, sql_dialect="SQLITE")
        return df

    def _consultar(self, tabela, colunas, ctmt):
        self.preparar()
        return self._ler(tabela, colunas, self._fids(tabela, ctmt))

    def banco_de_curvas(self):
        return curvas.banco_do_df(self._ler(curvas.TABELA, None))

    def fechar(self):
        """Nada a fechar: o GDAL abre e fecha o arquivo a cada leitura."""
