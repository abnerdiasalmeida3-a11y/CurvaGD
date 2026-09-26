"""GeoPackage minimo com as tabelas da BDGD que a Correcao de demanda le."""
import sqlite3

ENE = [f"ENE_{m:02d}" for m in range(1, 13)]
POT = [f"POT_{i:02d}" for i in range(1, 97)]
PREFIXO = "Teste_BDGD - "


def _tabela(con, nome, colunas, linhas):
    con.execute(f'CREATE TABLE "{PREFIXO}{nome}" ({", ".join(f"{c}" for c in colunas)})')
    con.executemany(f'INSERT INTO "{PREFIXO}{nome}" VALUES ({", ".join("?" * len(colunas))})', linhas)
    con.execute("INSERT INTO gpkg_contents (table_name) VALUES (?)", (PREFIXO + nome,))


def gerar(caminho):
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE gpkg_contents (table_name TEXT)")
    _tabela(con, "CTMT", ["COD_ID", "NOME", "SUB", "TEN_NOM"],
            [("F1", "Alimentador piloto", "S1", "13.8"), ("F2", "Alimentador vazio", "S1", "13.8")])
    perfis = {"DU": [1 + (i % 48) / 10 for i in range(96)], "SA": [2.0] * 96, "DO": [1.0] * 96}
    _tabela(con, "CRVCRG", ["COD_ID", "TIP_DIA", "GRU_TEN", "DESCR"] + POT,
            [("RES", tipo, "BT", "Residencial", *valores) for tipo, valores in perfis.items()])
    uc = ["COD_ID", "MUN", "CEG_GD", "TIP_CC", "GRU_TEN", "TEN_FORN", "FAS_CON", "CAR_INST"] + ENE
    linhas_bt = []
    for i in range(6):
        ceg = "GD.MT.001" if i == 0 else ("GD.MT.002" if i == 1 else "")
        fase = ["AN", "BN", "CN", "ABN", "ABCN", "AN"][i]
        linhas_bt.append((f"UC{i}", "5103403", ceg, "RES", "BT", "220", fase, 5.0,
                          *[300.0 + 10 * i] * 12, "T1" if i < 3 else "T3", "F1", "AT", "RE1"))
    # a mesma UC em duas linhas ativas (mudou de subclasse): energias somadas
    linhas_bt.append(("UC5", "5103403", "", "RES", "BT", "220", "AN", 5.0,
                      *[40.0] * 12, "T3", "F1", "AT", "RE2"))
    # inativa: fica de fora
    linhas_bt.append(("UC9", "5103403", "", "RES", "BT", "220", "AN", 5.0,
                      *[999.0] * 12, "T3", "F1", "DS", "RE1"))
    _tabela(con, "UCBT_tab", uc + ["UNI_TR_MT", "CTMT", "SIT_ATIV", "CLAS_SUB"], linhas_bt)
    _tabela(con, "UCMT_tab", uc + ["CTMT", "SIT_ATIV", "CLAS_SUB"],
            [("M1", "5103403", "", "RES", "MT", "13800", "ABC", 100.0, *[5000.0] * 12, "F1", "AT", "IN1")])
    ug = ["CEG_GD", "POT_INST", "FAS_CON"] + ENE + ["CTMT", "SIT_ATIV"]
    _tabela(con, "UGBT_tab", ug, [("GD.MT.001", 5.0, "A", *[200.0] * 12, "F1", "AT"),
                                  ("GD.MT.002", 3000.0, "B", *[100.0] * 12, "F1", "AT")])
    _tabela(con, "UGMT_tab", ug, [])
    _tabela(con, "UNTRMT", ["COD_ID", "FAS_CON_P", "CTMT", "SIT_ATIV"],
            [("T1", "B", "F1", "AT"), ("T3", "ABC", "F1", "AT")])
    con.commit()
    con.close()
    return caminho


def gerar_gdb(gpkg, destino):
    """A mesma BDGD minima como File Geodatabase (.gdb), pelo driver OpenFileGDB."""
    import pandas as pd
    import pyarrow as pa
    import pyogrio

    con = sqlite3.connect(gpkg)
    try:
        for (nome,) in con.execute("SELECT table_name FROM gpkg_contents").fetchall():
            quadro = pd.read_sql(f'SELECT * FROM "{nome}"', con)
            camada = nome.removeprefix(PREFIXO)
            tabela = pa.Table.from_pandas(quadro, preserve_index=False)
            if not len(quadro):
                # uma camada vazia ainda precisa das colunas com tipo definido
                tabela = pa.table({c: pa.array([], pa.float64() if c.startswith(("ENE_", "POT_")) else pa.string())
                                   for c in quadro.columns})
            pyogrio.write_arrow(tabela, destino, layer=camada, driver="OpenFileGDB",
                                geometry_type=None, geometry_name=None, append=destino.exists())
    finally:
        con.close()
    return destino
