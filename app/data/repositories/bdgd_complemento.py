"""Leitura rastreavel de UGBT/UGMT e DEM; compativel com catalogos antigos."""
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio

from ...core.errors import ErroBDGD
from ...ingest.readers import csv_reader
from ...ingest.validator import number


def _camada(fonte, entidade):
    nomes = (entidade.upper() + "_tab", entidade.upper())
    if fonte.suffix.lower() == ".gdb":
        existentes = {str(n) for n, _ in pyogrio.list_layers(fonte)}
        return next((n for n in nomes if n in existentes), None)
    return next((p for p in fonte.glob("*.csv") if p.stem.upper() in {n.upper() for n in nomes}), None)


def _ler(fonte, entidade, campos, filtro=None):
    camada = _camada(fonte, entidade)
    if camada is None:
        return []
    if fonte.suffix.lower() == ".gdb":
        disponiveis = set(pyogrio.read_info(fonte, layer=camada)["fields"])
        colunas = [c for c in campos if c in disponiveis]
        where = None
        if filtro:
            campo, valor = filtro
            if campo not in disponiveis:
                return []
            where = campo + " = '" + str(valor).replace("'", "''") + "'"
        return pyogrio.read_arrow(fonte, layer=camada, columns=colunas,
                                 read_geometry=False, where=where)[1].to_pylist()
    info = csv_reader.inspect_csv(camada)
    colunas = [c for c in campos if c in info["columns"]]
    linhas = []
    for batch in csv_reader.batches(camada, info, colunas, 32768):
        linhas.extend(r for r in batch.to_pylist() if not filtro or str(r.get(filtro[0]) or "").strip() == str(filtro[1]).strip())
    return linhas


CAMPOS_UG = ["COD_ID", "CEG_GD", "PAC", "PN_CON", "POT_INST", "SIT_ATIV"] + [f"ENE_{m:02d}" for m in range(1, 13)]


def preservar_geradores(fonte, destino, token):
    """Snapshot opcional dentro da versao importada; nao modifica a fonte."""
    destino = Path(destino) / "complementos"
    destino.mkdir()
    for entidade in ("ugbt", "ugmt"):
        token.verificar()
        linhas = _ler(Path(fonte), entidade, CAMPOS_UG)
        # Strings preservam valores originais e mantem esquema estavel entre CSV/GDB.
        schema = pa.schema([(campo, pa.string()) for campo in CAMPOS_UG])
        table = pa.Table.from_pylist([{c: None if r.get(c) is None else str(r[c]) for c in CAMPOS_UG} for r in linhas], schema=schema)
        pq.write_table(table, destino / f"{entidade}.parquet", compression="zstd")


def buscar_complemento(directory, source, uc):
    entidade = "ugbt" if uc["entidade"] == "ucbt" else "ugmt"
    caminho = Path(directory) / "complementos" / f"{entidade}.parquet"
    fonte = Path(source) if source else None
    codigo = str(uc.get("codigo_gd") or "").strip()
    avisos = []
    demandas = {m: uc.get(f"demanda_mes_{m:02d}") for m in range(1, 13)}
    origem = str(caminho)
    linhas = []
    try:
        if caminho.is_file() and codigo:
            linhas = pq.read_table(caminho, filters=[("CEG_GD", "=", codigo)]).to_pylist()
        elif fonte and fonte.is_dir() and codigo:
            linhas = _ler(fonte, entidade, CAMPOS_UG, ("CEG_GD", codigo))
            origem = f"{fonte} · {entidade.upper()} · fonte original (catalogo anterior ao snapshot)"
        if all(v is None for v in demandas.values()) and fonte and fonte.is_dir():
            registros = _ler(fonte, uc["entidade"], ["COD_ID"] + [f"DEM_{m:02d}" for m in range(1, 13)], ("COD_ID", uc["id_uc"]))
            if len(registros) == 1:
                demandas = {m: number(registros[0].get(f"DEM_{m:02d}")) for m in range(1, 13)}
    except Exception as exc:
        avisos.append(f"Complemento BDGD indisponivel: {exc}")
    mesmos = [r for r in linhas if str(r.get("COD_ID")) == str(uc["id_uc"])]
    if mesmos:
        linhas = mesmos
    elif linhas:
        # CEG identifica empreendimento, mas nao garante o mesmo medidor.
        avisos.append("CEG GD localizado sem COD_ID coincidente; potencia e injecao nao foram associadas automaticamente.")
        linhas = []
    if len(linhas) > 1:
        avisos.append("Mais de uma UG corresponde a UC; associacao ambigua, sem soma automatica.")
    ug = linhas[0] if len(linhas) == 1 else {}
    return {"potencia_instalada_kw": number(ug.get("POT_INST")),
            "injecao_registrada_kwh": {m: number(ug.get(f"ENE_{m:02d}")) for m in range(1, 13)},
            "demanda_kw": demandas, "origem": origem if ug else "UG nao associada",
            "avisos": tuple(avisos), "ug_associada": bool(ug)}


def _somar_pot_inst(linhas, soma):
    for r in linhas:
        codigo = str(r.get("CEG_GD") or "").strip()
        situacao = str(r.get("SIT_ATIV") or "").strip().upper()
        if not codigo or (situacao and situacao != "AT"):
            continue
        valor = number(r.get("POT_INST"))
        if valor is not None and valor > 0:
            soma[codigo] = soma.get(codigo, 0.0) + float(valor)


def potencias_ug_por_ceg(directory, source):
    """POT_INST somado por CEG_GD nas UGBT e UGMT ativas, como a CORRECAO_DEMANDA_BDGD.

    Varias UGs podem dividir o mesmo CEG_GD; a potencia do conjunto e a soma.
    Usa o snapshot da importacao e, em catalogos antigos, a fonte original.
    """
    soma = {}
    fonte = Path(source) if source else None
    for entidade in ("ugbt", "ugmt"):
        caminho = Path(directory) / "complementos" / f"{entidade}.parquet"
        try:
            if caminho.is_file():
                linhas = pq.read_table(caminho, columns=["CEG_GD", "POT_INST", "SIT_ATIV"]).to_pylist()
            elif fonte and fonte.is_dir():
                linhas = _ler(fonte, entidade, ["CEG_GD", "POT_INST", "SIT_ATIV"])
            else:
                linhas = []
        except Exception:
            linhas = []
        _somar_pot_inst(linhas, soma)
    return soma
