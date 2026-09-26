"""Potencias das GDs, da base tecnica de fotovoltaica da ANEEL.

A BDGD diz que a UC tem geracao e da o codigo CEG_GD, mas nao o tamanho do
sistema. Quem tem isso e o cadastro da ANEEL, que separa a potencia dos modulos
(Pmpp, o lado CC) da potencia dos inversores (o kVA) -- os dois numeros que o
modelo do PVSystem pede.

O arquivo tem quase 4 milhoes de linhas e meio giga; varrer em blocos filtrando
pelos CEG_GD do alimentador custa uns 6 segundos e nao carrega nada disso na
memoria.
"""

import numpy as np
import pandas as pd

COLUNA_CODIGO = "CodGeracaoDistribuida"
COLUNA_MODULOS = "MdaPotenciaModulos"
COLUNA_INVERSORES = "MdaPotenciaInversores"
COLUNAS = [COLUNA_CODIGO, COLUNA_MODULOS, COLUNA_INVERSORES]
BLOCO = 500_000

# Razao kW de modulos por kVA de inversor usada quando so ha a potencia da UGBT.
# Mediana de 1,276 nas 149 GDs do alimentador 764500; sobredimensionar o arranjo
# em ~25% e a pratica corrente.
RAZAO_PADRAO = 1.25

STATUS_SEM_GD = "SEM_GD"
STATUS_ANEEL = "COM_GD"
STATUS_UGBT = "GD_POT_UGBT"
STATUS_SEM_POTENCIA = "GD_SEM_POTENCIA"

# Teto legal da potencia de uma GD por nivel de tensao, em kW: microgeracao ate
# 75 kW (ligada em BT), minigeracao ate 5 MW (MT). Serve de trava para o
# POT_INST da UG, que vem em kW em 89% dos casos mas em W em 1,4% -- conferido
# contra a ANEEL em 158 mil GDs. Sem a trava, uma UC monofasica de BT chegou a
# ser simulada com 89 mil kVA.
TETO_KW = {"UCBT": 75.0, "UCMT": 5000.0}


def _numero(serie):
    """Coluna numerica da ANEEL: virada decimal com virgula."""
    return pd.to_numeric(serie.str.replace(",", ".", regex=False), errors="coerce")


def potencias(caminhos, cegs, progresso=None):
    """Varre o(s) CSV(s) da ANEEL e agrega as potencias dos codigos pedidos.

    Soma as potencias por codigo em vez de ficar com a ultima linha: um mesmo
    CEG_GD aparece varias vezes quando ha mais de um arranjo ou uma ampliacao,
    e manter so a ultima subestimaria o sistema.
    """
    if isinstance(caminhos, (str, bytes)) or not hasattr(caminhos, "__iter__"):
        caminhos = [caminhos]
    cegs = {str(c).strip() for c in cegs if str(c).strip()}
    if not cegs:
        return pd.DataFrame(columns=["POT_MODULOS", "POT_INVERSOR", "N_LINHAS"])

    partes = []
    lidas = 0
    for caminho in caminhos:
        try:
            blocos = pd.read_csv(caminho, encoding="latin-1", sep=";", dtype=str,
                                 usecols=COLUNAS, chunksize=BLOCO)
        except ValueError as erro:
            raise ValueError(
                f"O arquivo {caminho} nao parece ser a base tecnica de GD fotovoltaica "
                f"da ANEEL: {erro}"
            ) from erro

        for bloco in blocos:
            lidas += len(bloco)
            codigo = bloco[COLUNA_CODIGO].astype(str).str.strip()
            achados = bloco[codigo.isin(cegs)].copy()
            if not achados.empty:
                achados[COLUNA_CODIGO] = codigo[achados.index]
                partes.append(achados)
            if progresso is not None:
                progresso(lidas)

    if not partes:
        return pd.DataFrame(columns=["POT_MODULOS", "POT_INVERSOR", "N_LINHAS"])

    dados = pd.concat(partes, ignore_index=True)
    dados[COLUNA_MODULOS] = _numero(dados[COLUNA_MODULOS])
    dados[COLUNA_INVERSORES] = _numero(dados[COLUNA_INVERSORES])
    return dados.groupby(COLUNA_CODIGO).agg(
        POT_MODULOS=(COLUNA_MODULOS, "sum"),
        POT_INVERSOR=(COLUNA_INVERSORES, "sum"),
        N_LINHAS=(COLUNA_CODIGO, "size"),
    )


def _corrigir_unidade(pot_inst, tabelas):
    """POT_INST em kW, com a trava do teto legal de cada nivel de tensao.

    Acima do teto, se o valor dividido por mil couber, o registro estava em W e e
    convertido; se nem assim couber, e inverossimil e vira zero -- a UC passa a
    GD_SEM_POTENCIA em vez de ser simulada com uma usina que nao existe.
    """
    teto = np.array([TETO_KW.get(t, TETO_KW["UCMT"]) for t in tabelas])
    em_watt = (pot_inst > teto) & (pot_inst / 1000.0 <= teto)
    inverossimil = (pot_inst > teto) & ~em_watt
    return np.where(em_watt, pot_inst / 1000.0, np.where(inverossimil, 0.0, pot_inst))


def potencias_por_uc(ucs, base_aneel, pot_ugs=None, razao_padrao=RAZAO_PADRAO):
    """Resolve (kW de modulos, kVA de inversor, status) para cada UC.

    Ordem das fontes: ANEEL primeiro, potencia da UG da propria BDGD depois. A
    UG traz um numero so, entao o kVA vem dela e o kW sai da razao padrao -- e a
    linha fica marcada, para nao passar por medida quando nao e. A potencia da UG
    passa antes pela trava de unidade de `_corrigir_unidade`.
    """
    ceg = ucs["CEG_GD"].astype(str).str.strip()
    tem_ceg = ceg != ""

    if len(base_aneel):
        casado = base_aneel.reindex(ceg)
        kw = pd.to_numeric(casado["POT_MODULOS"], errors="coerce").to_numpy()
        kva = pd.to_numeric(casado["POT_INVERSOR"], errors="coerce").to_numpy()
    else:
        kw = np.full(len(ucs), np.nan)
        kva = np.full(len(ucs), np.nan)

    kw = np.nan_to_num(kw, nan=0.0)
    kva = np.nan_to_num(kva, nan=0.0)
    da_aneel = tem_ceg.to_numpy() & (kw > 0) & (kva > 0)

    if pot_ugs is not None and len(pot_ugs):
        reserva = pd.to_numeric(pot_ugs.reindex(ceg), errors="coerce").to_numpy()
        reserva = np.nan_to_num(reserva, nan=0.0)
    else:
        reserva = np.zeros(len(ucs))
    reserva = _corrigir_unidade(reserva, ucs["TABELA"].to_numpy())
    da_ugbt = tem_ceg.to_numpy() & ~da_aneel & (reserva > 0)

    kw = np.where(da_aneel, kw, np.where(da_ugbt, reserva * razao_padrao, 0.0))
    kva = np.where(da_aneel, kva, np.where(da_ugbt, reserva, 0.0))

    status = np.where(
        da_aneel, STATUS_ANEEL,
        np.where(da_ugbt, STATUS_UGBT,
                 np.where(tem_ceg.to_numpy(), STATUS_SEM_POTENCIA, STATUS_SEM_GD)),
    )
    return pd.DataFrame(
        {"POT_MODULOS": kw, "POT_INVERSOR": kva, "STATUS": status}, index=ucs.index
    )


def potencias_copia_local(workspace, cegs, progresso=None):
    """Mesmo resultado de `potencias`, lido da copia local da base da ANEEL.

    E a base que o CurvaGD ja baixa para as curvas das UCs
    (workspace/solar_aneel/equipamentos.parquet): mesma soma por CEG_GD, sem
    precisar do CSV de meio giga. Baixa a base se ela ainda nao existir.
    """
    from ..services.aneel_solar import potencias_em_lote
    from ..data.repositories.gd import _chave

    cegs = sorted({str(c).strip() for c in cegs if str(c).strip()})
    if not cegs:
        return pd.DataFrame(columns=["POT_MODULOS", "POT_INVERSOR", "N_LINHAS"])
    avisar = (lambda etapa, atual, total: progresso(atual)) if progresso is not None else None
    somas, _, aviso = potencias_em_lote(workspace, cegs, **({"progress": avisar} if avisar else {}))
    if not somas and aviso:
        raise ValueError(aviso)
    linhas = {c: somas[_chave(c)] for c in cegs if _chave(c) in somas}
    return pd.DataFrame.from_dict(
        linhas, orient="index", columns=["POT_MODULOS", "POT_INVERSOR", "N_LINHAS"]
    ).rename_axis("CodGeracaoDistribuida")
