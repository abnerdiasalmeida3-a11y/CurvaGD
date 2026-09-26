"""Perfil das cargas de cada alimentador: classes de consumo e numero de fases.

Parte da contagem que `bdgd.estatisticas` tira da base inteira -- UCs ativas por
alimentador, subclasse e fases -- e resume em duas visoes:

  visao_geral   uma linha por alimentador, com a participacao das classes
                principais, das ligacoes mono/bi/trifasicas e das UCs com GD,
                para ordenar e achar perfis ("mais industrial", "mais rural");
  detalhe       as classes todas e as fases de um alimentador, com quantidade e
                participacao.

A participacao pode ser pela quantidade de UCs ou pela energia anual. As duas
contam historias diferentes: um alimentador com 3% de UCs industriais pode ter
60% da energia nelas.

A classe sai do prefixo da subclasse (CLAS_SUB), pelo dominio do Modulo 10 do
PRODIST: RE residencial, CO comercial (servicos e outras atividades), IN
industrial, RU rural, PP poder publico, SP servico publico, IP iluminacao
publica. CPR (consumo proprio), CSPS (concessionaria ou permissionaria) e o que
vier sem subclasse caem em "Outras".
"""

import pandas as pd

CLASSES = (
    ("RE", "Residencial"),
    ("CO", "Comercial"),
    ("IN", "Industrial"),
    ("RU", "Rural"),
    ("PP", "Poder publico"),
    ("SP", "Servico publico"),
    ("IP", "Iluminacao publica"),
)
OUTRAS = "Outras"
ORDEM_CLASSES = [nome for _, nome in CLASSES] + [OUTRAS]
# a visao geral mostra as quatro que definem perfil; o resto se junta em "Outras"
PRINCIPAIS = ("Residencial", "Comercial", "Industrial", "Rural")

FASES = ("Monofasica", "Bifasica", "Trifasica")
SEM_FASE = "Sem informacao"
ORDEM_FASES = list(FASES) + [SEM_FASE]

MEDIDAS = {"UCS": "Quantidade de UCs", "ENERGIA_KWH": "Energia anual"}


def classe(clas_sub):
    """Classe de consumo a partir da subclasse da BDGD."""
    codigo = str(clas_sub).strip().upper()
    for prefixo, nome in CLASSES:
        if codigo.startswith(prefixo):
            return nome
    return OUTRAS


def ligacao(fas_con):
    """Mono, bi ou trifasica, pelo numero de fases no FAS_CON; o neutro nao conta.

    E a ligacao da propria UC. Numa UC de BT as letras sao do secundario do
    trafo: servem para dizer quantas fases a UC tem, nao em qual fase do
    alimentador ela esta.
    """
    n = sum(f in str(fas_con).upper() for f in "ABC")
    return FASES[n - 1] if n else SEM_FASE


def preparar(brutas):
    """Acrescenta CLASSE e LIGACAO a contagem bruta, e agrupa por elas."""
    df = brutas.copy()
    df["CLASSE"] = df["CLAS_SUB"].map(classe)
    df["LIGACAO"] = df["FAS_CON"].map(ligacao)
    return df.groupby(["CTMT", "TABELA", "CLASSE", "LIGACAO"], as_index=False)[
        ["UCS", "COM_GD", "ENERGIA_KWH"]].sum()


def _participacao(df, coluna, ordem, medida):
    """% de cada categoria por alimentador, na medida pedida (colunas em `ordem`)."""
    tabela = df.pivot_table(index="CTMT", columns=coluna, values=medida,
                            aggfunc="sum", fill_value=0.0)
    tabela = tabela.reindex(columns=ordem, fill_value=0.0)
    total = tabela.sum(axis=1).replace(0, float("nan"))
    return tabela.div(total, axis=0).mul(100).fillna(0.0)


def visao_geral(preparadas, alimentadores, medida="UCS"):
    """Uma linha por alimentador, pronta para ordenar por perfil.

    `alimentadores` vem de `fonte.alimentadores()` (COD_ID, NOME). Alimentadores
    sem UC ativa ficam de fora -- nao ha perfil a mostrar.
    """
    classes = _participacao(preparadas, "CLASSE", ORDEM_CLASSES, medida)
    fases = _participacao(preparadas, "LIGACAO", ORDEM_FASES, medida)
    totais = preparadas.groupby("CTMT")[["UCS", "COM_GD", "ENERGIA_KWH"]].sum()

    geral = pd.DataFrame(index=totais.index)
    nomes = alimentadores.set_index("COD_ID")["NOME"]
    geral["ALIMENTADOR"] = [f"{c} - {nomes.get(c, '')}".rstrip(" -") for c in geral.index]
    geral["UCS"] = totais["UCS"].astype(int)
    geral["ENERGIA_MWH"] = totais["ENERGIA_KWH"] / 1e3
    for nome in PRINCIPAIS:
        geral[nome] = classes[nome]
    geral[OUTRAS] = classes.drop(columns=list(PRINCIPAIS)).sum(axis=1)
    for nome in FASES:
        geral[nome] = fases[nome]
    geral["COM_GD"] = 100.0 * totais["COM_GD"] / totais["UCS"]

    # a classe dominante e quanto ela pesa: e o "perfil" que se le de relance
    dominante = classes.idxmax(axis=1)
    geral["PREDOMINANTE"] = [f"{c} {classes.loc[i, c]:.0f}%" for i, c in dominante.items()]
    return geral.reset_index().sort_values("ALIMENTADOR", ignore_index=True)


def detalhe(preparadas, ctmt):
    """Classes e ligacoes de um alimentador, com quantidade e participacao.

    Devolve (classes, ligacoes), cada uma com UCS, % das UCs, energia em MWh e
    % da energia. Categorias sem nenhuma UC nao aparecem.
    """
    df = preparadas[preparadas["CTMT"] == str(ctmt)]

    def tabela(coluna, ordem):
        t = df.groupby(coluna)[["UCS", "ENERGIA_KWH"]].sum().reindex(ordem).dropna()
        t = t[t["UCS"] > 0]
        t["PCT_UCS"] = 100.0 * t["UCS"] / t["UCS"].sum()
        t["ENERGIA_MWH"] = t["ENERGIA_KWH"] / 1e3
        energia = t["ENERGIA_KWH"].sum()
        t["PCT_ENERGIA"] = 100.0 * t["ENERGIA_KWH"] / energia if energia else 0.0
        t["UCS"] = t["UCS"].astype(int)
        return t[["UCS", "PCT_UCS", "ENERGIA_MWH", "PCT_ENERGIA"]].rename_axis(coluna).reset_index()

    return tabela("CLASSE", ORDEM_CLASSES), tabela("LIGACAO", ORDEM_FASES)
