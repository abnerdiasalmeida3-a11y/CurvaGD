"""Banco de curvas de carga da BDGD (tabela CRVCRG).

Cada codigo tem tres perfis de 96 pontos de 15 min -- DU (dia util), SA
(sabado) e DO (domingo). A curva de um periodo sai encaixando o perfil certo
em cada dia do calendario.

Diferenca importante para o `curvas.py` do TESTE_CORRECAO_CARGA: la a serie era
normalizada pelo maximo do proprio recorte; aqui a base e o maximo do ANO
inteiro no passo de trabalho, a mesma para qualquer recorte. E o que sustenta a
promessa desta ferramenta -- montar a curva de duas semanas de janeiro e
multiplicar por DEM_MAX_01 tem de dar a mesma potencia em kW que a curva do mes
inteiro. Com base movel, um recorte so de domingo colocaria o pico de domingo em
1,0 pu e inflaria a carga em sete vezes num IND-Tipo4.

No passo nativo de 15 min todo dia util e identico, entao o maximo do ano e o
maximo de qualquer mes: DEM_MAX_mm continua sendo, literalmente, o pico do mes.
"""

import numpy as np
import pandas as pd

from ..calculo import irradiancia

COLUNA_CODIGO = "COD_ID"
COLUNA_TIPO = "TIP_DIA"
COLUNA_GRUPO = "GRU_TEN"
COLUNA_DESCRICAO = "DESCR"
COLUNAS_POTENCIA = [f"POT_{i:02d}" for i in range(1, 97)]

TIPOS_DIA = ("DU", "SA", "DO")
ROTULOS_TIPO = {"DU": "Dia util", "SA": "Sabado", "DO": "Domingo"}
PASSO_NATIVO = 15  # minutos entre os 96 pontos de cada perfil
PONTOS_POR_DIA = 96
TABELA = "CRVCRG"


def banco_do_df(df):
    """Monta {codigo: {"DU": array, "SA": array, "DO": array, ...}} a partir da tabela."""
    faltando = [c for c in [COLUNA_CODIGO, COLUNA_TIPO] + COLUNAS_POTENCIA if c not in df.columns]
    if faltando:
        raise ValueError(
            "A tabela nao parece ser a CRVCRG da BDGD. "
            f"Colunas ausentes: {', '.join(faltando[:5])}"
            f"{'...' if len(faltando) > 5 else ''}."
        )

    banco = {}
    for codigo, grupo in df.groupby(COLUNA_CODIGO):
        registro = {
            "descricao": str(grupo[COLUNA_DESCRICAO].iloc[0]) if COLUNA_DESCRICAO in grupo else "",
            "grupo": str(grupo[COLUNA_GRUPO].iloc[0]) if COLUNA_GRUPO in grupo else "",
        }
        for tipo, linhas in grupo.groupby(COLUNA_TIPO):
            valores = pd.to_numeric(linhas[COLUNAS_POTENCIA].iloc[0], errors="coerce")
            registro[str(tipo).strip().upper()] = valores.to_numpy(dtype=float)

        ausentes = [t for t in TIPOS_DIA if t not in registro]
        if ausentes:
            raise ValueError(f"A curva {codigo} nao tem o(s) perfil(is) {', '.join(ausentes)}.")
        banco[str(codigo).strip()] = registro

    if not banco:
        raise ValueError("Nenhuma curva encontrada.")
    return banco


def ler_crvcrg(caminho):
    """Le a CRVCRG de um CSV solto."""
    try:
        df = pd.read_csv(caminho, dtype={COLUNA_CODIGO: str, COLUNA_TIPO: str})
    except Exception as erro:
        raise ValueError(f"Nao foi possivel ler o CSV de curvas: {erro}") from erro
    return banco_do_df(df)


def banco_do_gpkg(con, tabelas):
    """Le a CRVCRG direto do GeoPackage da BDGD. 198 linhas, instantaneo."""
    colunas = ", ".join([COLUNA_CODIGO, COLUNA_TIPO, COLUNA_GRUPO, COLUNA_DESCRICAO]
                        + COLUNAS_POTENCIA)
    df = pd.read_sql(f'SELECT {colunas} FROM "{tabelas[TABELA]}"', con)
    return banco_do_df(df)


def codigos(banco):
    """Codigos principais, sem as variacoes de tipo de dia."""
    return sorted(banco)


def rotulo(banco, codigo):
    """Texto do seletor: codigo e descricao, quando houver."""
    descricao = banco.get(codigo, {}).get("descricao", "")
    return f"{codigo} - {descricao}" if descricao else codigo


def perfil(banco, codigo, tipo_dia, normalizar=False, dia=None):
    """Os 96 pontos originais de um tipo de dia, indexados por horario.

    O indice usa uma data qualquer (so o horario importa aqui); `normalizar`
    divide pelo maximo deste perfil, para comparar formatos lado a lado. Nao e
    a normalizacao do calculo -- essa e a de `curva_ano`.
    """
    if codigo not in banco:
        raise ValueError(f"Curva desconhecida: {codigo}.")
    tipo = tipo_dia.strip().upper()
    if tipo not in TIPOS_DIA:
        raise ValueError(f"Tipo de dia desconhecido: {tipo_dia}. Use um de {TIPOS_DIA}.")

    valores = np.asarray(banco[codigo][tipo], dtype=float)
    if normalizar:
        maximo = valores.max()
        valores = valores / maximo if maximo > 0 else valores

    inicio = pd.Timestamp(dia) if dia is not None else pd.Timestamp("2024-01-01")
    indice = pd.date_range(inicio.normalize(), periods=PONTOS_POR_DIA, freq=f"{PASSO_NATIVO}min")
    return pd.Series(valores, index=indice)


def _tipo_do_dia(data):
    """Dia da semana -> tipo de perfil. O banco nao traz feriado, entao ele cai em DU."""
    dia = data.weekday()
    if dia == 5:
        return "SA"
    if dia == 6:
        return "DO"
    return "DU"


def curva_ano(banco, codigo, ano, passo_minutos=PASSO_NATIVO):
    """Serie do ano inteiro em pu, e a base usada para normalizar.

    Reamostra o ano de uma vez, e nao dia a dia: o PCHIP do `irradiancia`
    interpola sobre a energia acumulada e precisa dos blocos vizinhos para nao
    inventar degrau na virada do dia.
    """
    if codigo not in banco:
        raise ValueError(f"Curva desconhecida: {codigo}.")

    dias = pd.date_range(f"{ano}-01-01", f"{ano}-12-31", freq="D")
    valores = np.concatenate([banco[codigo][_tipo_do_dia(dia)] for dia in dias])
    indice = pd.date_range(dias[0], periods=len(valores), freq=f"{PASSO_NATIVO}min")
    serie = pd.Series(valores, index=indice)

    if passo_minutos != PASSO_NATIVO:
        serie = irradiancia.reamostrar(serie, passo_minutos, passo_origem=PASSO_NATIVO)

    base = float(serie.max())
    return (serie / base if base > 0 else serie), base


def curva_periodo(banco, codigo, inicio, fim, passo_minutos=PASSO_NATIVO):
    """Curva em pu do periodo [inicio, fim), na mesma base do ano.

    O fim e exclusivo, como em `irradiancia.recortar`: 01/01 a 01/02 e janeiro
    fechado, e nao janeiro mais o primeiro bloco de fevereiro.

    Monta o ano e recorta, em vez de montar so os dias pedidos: assim os pontos
    sao byte a byte os mesmos que o motor de demanda usou, e o recorte exportado
    multiplicado por DEM_MAX reproduz exatamente a carga daquele mes.
    """
    inicio = pd.Timestamp(inicio)
    fim = pd.Timestamp(fim)
    if inicio >= fim:
        raise ValueError("O inicio do periodo precisa ser anterior ao fim.")
    # o fim exclusivo pode cair na virada do ano -- e assim que se pede dezembro
    # fechado, de 01/12 a 01/01 do ano seguinte
    if fim > pd.Timestamp(f"{inicio.year + 1}-01-01"):
        raise ValueError("O recorte precisa ficar dentro de um ano so.")

    serie, _ = curva_ano(banco, codigo, inicio.year, passo_minutos)
    return irradiancia.recortar(serie, inicio, fim)
