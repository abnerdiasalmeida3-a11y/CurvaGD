"""Leitura e reamostragem da serie horaria de irradiancia do CSV da NASA POWER.

Copia fiel do `irradiancia.py` da ferramenta CORRECAO_DEMANDA_BDGD -- o mesmo
arquivo, para as duas ferramentas chegarem ao mesmo numero.

Modulo sem dependencia de Qt: e a mesma conversao usada no processo atual
(Wh/m2 -> kW/m2 medio na hora), mas validando o que o metodo antigo assumia
em silencio (cabecalho fixo, dado faltante virando zero, lacunas de horario).
"""

import re

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

COLUNA_VALOR = "ALLSKY_SFC_SW_DWN"
COLUNAS_DATA = ["YEAR", "MO", "DY", "HR"]
MARCA_FIM_CABECALHO = "-END HEADER-"
PASSO_HORARIO = 60
METODOS = ("pchip", "escada")


def _linhas_de_cabecalho(caminho):
    """Numero de linhas a pular ate a linha de colunas do CSV.

    Procura a propria linha de colunas; se nao achar, cai para a marca de fim
    de cabecalho da NASA e, por ultimo, para zero -- assim um arquivo que nao
    e da NASA falha na checagem de colunas, com mensagem legivel.
    """
    fim_cabecalho = None
    with open(caminho, "r", encoding="utf-8", errors="replace") as arq:
        for numero, linha in enumerate(arq, start=1):
            texto = linha.strip()
            if texto.upper().startswith(",".join(COLUNAS_DATA)):
                return numero - 1
            if texto == MARCA_FIM_CABECALHO:
                fim_cabecalho = numero
            if numero > 50:
                break
    return fim_cabecalho if fim_cabecalho is not None else 0


def _coordenadas(caminho):
    """Le latitude/longitude da linha 'Location:' do cabecalho. Devolve (None, None) se nao achar."""
    with open(caminho, "r", encoding="utf-8", errors="replace") as arq:
        for numero, linha in enumerate(arq, start=1):
            if linha.strip() == MARCA_FIM_CABECALHO or numero > 50:
                break
            if "Latitude" in linha and "Longitude" in linha:
                achados = re.findall(r"[-+]?\d+\.?\d*", linha)
                if len(achados) >= 2:
                    return float(achados[0]), float(achados[1])
    return None, None


def ler_csv_nasa(caminho):
    """Serie horaria em kW/m2 indexada por datetime, mais um dicionario de diagnostico.

    Nao filtra por ano: devolve tudo que houver no arquivo. Levanta ValueError
    quando o arquivo nao tem o formato esperado.
    """
    pulos = _linhas_de_cabecalho(caminho)
    try:
        df = pd.read_csv(caminho, sep=",", dtype=str, skiprows=pulos)
    except Exception as erro:
        raise ValueError(f"Nao foi possivel ler o CSV: {erro}") from erro

    faltando = [c for c in COLUNAS_DATA + [COLUNA_VALOR] if c not in df.columns]
    if faltando:
        raise ValueError(
            "O arquivo nao parece ser um CSV horario da NASA POWER. "
            f"Colunas ausentes: {', '.join(faltando)}."
        )

    for coluna in COLUNAS_DATA:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    valores = pd.to_numeric(df[COLUNA_VALOR], errors="coerce")

    invalidas = df[COLUNAS_DATA].isna().any(axis=1)
    if invalidas.all():
        raise ValueError("Nenhuma linha com data/hora valida foi encontrada no arquivo.")
    df = df[~invalidas]
    valores = valores[~invalidas]

    indice = pd.to_datetime(
        dict(
            year=df["YEAR"].astype(int),
            month=df["MO"].astype(int),
            day=df["DY"].astype(int),
            hour=df["HR"].astype(int),
        )
    )
    serie = pd.Series(valores.values, index=indice).sort_index()

    duplicados = int(serie.index.duplicated().sum())
    if duplicados:
        serie = serie[~serie.index.duplicated(keep="first")]

    # -999 marca dado ausente no arquivo da NASA; conta antes de zerar
    ausentes = serie.isna() | (serie < 0)
    faltantes = int(ausentes.sum())
    serie = serie.where(~ausentes, 0.0)

    # Wh/m2 acumulado na hora -> kW/m2 medio na hora
    serie = (serie / 1000.0).round(5)

    horas_esperadas = pd.date_range(serie.index[0], serie.index[-1], freq="h")
    lacunas = int(len(horas_esperadas.difference(serie.index)))

    latitude, longitude = _coordenadas(caminho)
    diagnostico = {
        "caminho": str(caminho),
        "inicio": serie.index[0],
        "fim": serie.index[-1],
        "n_horas": int(len(serie)),
        "lacunas": lacunas,
        "faltantes": faltantes,
        "duplicados": duplicados,
        "lat": latitude,
        "lon": longitude,
    }
    return serie, diagnostico


def serie_ano(serie, ano):
    """Recorta um ano e completa as horas ausentes com zero para o modelo PVSystem."""
    do_ano = serie[serie.index.year == int(ano)]
    if do_ano.empty:
        raise ValueError(f"O arquivo nao tem dados do ano {ano}.")
    indice = pd.date_range(f"{ano}-01-01 00:00", f"{ano}-12-31 23:00", freq="h")
    return do_ano.reindex(indice, fill_value=0.0)


def recortar(serie, inicio, fim, ancorar=False):
    """Blocos da serie no periodo [inicio, fim): o fim e EXCLUSIVO.

    Um carimbo de tempo rotula o INICIO de um bloco, nao um instante solto: o
    valor de 01/02 00:00 e a potencia media de [01/02 00:00, 01/02 01:00). Entao
    pedir "ate 01/02 00:00" e pedir janeiro fechado, e o bloco rotulado
    01/02 00:00 fica de fora -- ele ja e fevereiro. O `.loc[inicio:fim]` do
    pandas inclui as duas pontas e entregaria uma hora inteira a mais.

    Com o fim exclusivo, o numero de blocos e exatamente (fim - inicio)/passo,
    periodos vizinhos se encaixam sem repetir bloco na virada do mes, e a energia
    do recorte nunca conta potencia de fora da janela pedida.

    O corte olha o rotulo do bloco, o que atende as duas formas naturais de
    escrever a mesma coisa: "ate 01/02 00:00" e "ate 31/01 23:59" devolvem os
    mesmos blocos de janeiro.

    `ancorar` recua o inicio para o bloco que o contem, em vez de comecar no
    primeiro rotulo posterior. E o que a origem de uma reamostragem precisa: um
    "de 00:30" numa serie horaria tem de trazer o bloco das 00:00, senao a
    primeira meia hora do recorte fino se perde.
    """
    inicio = pd.Timestamp(inicio)
    fim = pd.Timestamp(fim)
    if inicio >= fim:
        raise ValueError("O inicio do periodo precisa ser anterior ao fim.")

    if ancorar:
        anteriores = serie.index[serie.index <= inicio]
        if len(anteriores):
            inicio = anteriores[-1]

    recorte = serie[(serie.index >= inicio) & (serie.index < fim)]
    if recorte.empty:
        raise ValueError("O intervalo escolhido nao cobre nenhum bloco da serie.")
    return recorte


def energia(serie, passo_minutos=PASSO_HORARIO):
    """Energia acumulada da serie em kWh/m2: os valores sao potencia, entao pesam pelo passo.

    Usar isto em vez de somar direto evita comparar resolucoes diferentes pela
    soma aritmetica, que muda com o numero de blocos ainda que a energia nao mude.
    """
    return float(serie.sum()) * (passo_minutos / 60.0)


def reamostrar(serie, passo_minutos=15, metodo="pchip", passo_origem=PASSO_HORARIO):
    """Muda a resolucao da serie preservando a energia de cada bloco de origem.

    A serie de entrada e de potencia media por bloco, com rotulo no inicio do
    bloco; a saida segue a mesma convencao. `passo_origem` e o passo da entrada
    (60 para a serie horaria da NASA, 15 para as curvas de carga da BDGD).

    Refinando (passo menor que a origem):
      metodo="pchip"  interpola a energia acumulada com spline monotona e deriva:
                      cada bloco de origem conserva a propria energia, nenhum
                      bloco fica negativo e bloco nulo continua nulo.
      metodo="escada" repete o valor nos sub-blocos, sem suavizar.

    Agregando (passo maior que a origem): media dos blocos, que e o correto
    para potencia media e tambem conserva a energia.
    """
    if metodo not in METODOS:
        raise ValueError(f"Metodo desconhecido: {metodo}. Use um de {METODOS}.")
    if passo_minutos < 1 or passo_origem < 1:
        raise ValueError("Os passos em minutos precisam ser positivos.")
    if passo_minutos == passo_origem:
        return serie
    if serie.empty:
        raise ValueError("A serie esta vazia.")

    # o metodo pressupoe blocos contiguos; lacunas entram como zero, como no serie_ano
    grade = pd.date_range(serie.index[0], serie.index[-1], freq=f"{passo_origem}min")
    base = serie.reindex(grade, fill_value=0.0)

    if passo_minutos > passo_origem:
        if passo_minutos % passo_origem:
            raise ValueError(
                f"Para agregar, o passo ({passo_minutos} min) precisa ser multiplo "
                f"do passo de origem ({passo_origem} min)."
            )
        return base.resample(f"{passo_minutos}min").mean().dropna()

    if passo_origem % passo_minutos:
        raise ValueError(
            f"Para refinar, o passo de origem ({passo_origem} min) precisa ser "
            f"multiplo do passo ({passo_minutos} min)."
        )

    valores = base.to_numpy(dtype=float)
    n = len(valores)
    blocos = passo_origem // passo_minutos

    if metodo == "escada":
        finos = np.repeat(valores, blocos)
    else:
        # F(t) = energia acumulada nas bordas dos blocos de origem, em unidades de
        # bloco; PCHIP e monotona, logo dF >= 0. A divisao por `blocos` converte a
        # energia de volta em potencia media do sub-bloco.
        acumulada = np.concatenate([[0.0], np.cumsum(valores)])
        curva = PchipInterpolator(np.arange(n + 1, dtype=float), acumulada)
        bordas = np.linspace(0.0, float(n), n * blocos + 1)
        finos = np.diff(curva(bordas)) * blocos

    indice = pd.date_range(grade[0], periods=n * blocos, freq=f"{passo_minutos}min")
    return pd.Series(finos, index=indice)
