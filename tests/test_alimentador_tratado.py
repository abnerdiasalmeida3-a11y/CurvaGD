from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from app.core.errors import ErroBDGD
from app.core.jobs import Cancelamento
from app.data.repositories.uc import FiltrosUC
from app.data.repositories.tratamentos import TratamentosUC
from app.ingest.pipeline import import_bdgd
from app.services.application import ApplicationService
from app.services.projeto import copiar_nasa
from app.config.recursos import irradiancia_padrao
from app.services.exportar_alimentador import CABECALHO, exportar
from tests.fixtures.generate import fixture


@pytest.fixture
def circuito(tmp_path):
    fonte = fixture(tmp_path / "fonte", size=2, errors=False)
    work = tmp_path / "work"
    manifest = import_bdgd(fonte, work)
    copiar_nasa(irradiancia_padrao(), work)
    service = ApplicationService(work, work / "settings.toml")
    return service, manifest["import_id"]


def geracao_esperada_kwh(mes, kw=8.0, kva=5.0):
    """Energia do modelo PVSystem com o CSV NASA do projeto, no mes (0 = ano)."""
    from app.calculo import curvas, motor
    from app.config.recursos import irradiancia_padrao
    geracao = motor.geracao_ano(motor.irradiancia_ano(irradiancia_padrao(), 2024), kw, kva)
    trecho = geracao if mes == 0 else geracao[curvas.fatias_mensais(2024)[mes - 1]]
    return float(trecho.sum() * .25)


def concluir(service, ident, mes=2):
    """Lote para todas as UCs; depois as GDs sao revisadas com potencias informadas."""
    estado = service.tratar_em_lote(ident, "S1", "F1", mes)
    assert estado["pendentes"] == 0
    for item in estado["ucs"]:
        uc = item["uc"]
        if str(uc.get("codigo_gd") or "").strip():
            service.curva_uc(ident, uc["entidade"], uc["linha_origem"], mes,
                             potencia_modulos_kwp=8, potencia_inversor_kw=5)
    return service.curva_alimentador(ident, FiltrosUC(subestacao="S1", alimentador="F1"), mes)


def test_bloqueia_ate_todas_bt_mt_e_meses_tratados(circuito):
    service, ident = circuito
    estado = service.tratamentos_alimentador(ident, "S1", "F1", 2)
    assert (estado["bt"], estado["mt"], estado["pendentes"]) == (2, 3, 5)
    with pytest.raises(ErroBDGD, match="bloqueada"):
        service.curva_alimentador(ident, FiltrosUC(subestacao="S1", alimentador="F1"), 2)
    resultado = concluir(service, ident)
    assert resultado["unidades_incluidas"] == 5
    assert service.tratamentos_alimentador(ident, "S1", "F1", 0)["pendentes"] == 5
    with pytest.raises(ErroBDGD, match="bloqueada"):
        service.curva_alimentador(ident, FiltrosUC(subestacao="S1", alimentador="F1"), 0)


def test_lote_usa_os_parametros_solares_da_interface(circuito, monkeypatch):
    from app.calculo import curvas, motor
    from app.services import alimentadores

    service, ident = circuito
    monkeypatch.setattr(alimentadores, "potencias_ug_por_ceg", lambda *_: {"GD1": 5.0})
    service.atualizar_parametros_solares(0.6, 8.0, 1.7)
    estado = service.tratar_em_lote(ident, "S1", "F1", 2)
    assert estado["pendentes"] == 0
    item = next(i for i in estado["ucs"] if i["uc"]["codigo_gd"] == "GD1")
    uc = item["uc"]
    store = TratamentosUC(service.workspace)
    parametros = store.parametros(ident, uc["entidade"], uc["linha_origem"])
    assert {k: parametros[k] for k in ("performance_ratio", "cut_in_percentual", "razao_kw_kva")} == {
        "performance_ratio": 0.6, "cut_in_percentual": 8.0, "razao_kw_kva": 1.7}
    _, arrays = store.ler(item["referencias"][2])
    ano = estado["ano"]
    geracao = motor.geracao_ano(motor.irradiancia_ano(irradiancia_padrao(), ano), 8.5, 5.0,
                               performance_ratio=0.6, cut_in=8.0)
    np.testing.assert_allclose(arrays["geracao"], geracao[curvas.fatias_mensais(ano)[1]])


def test_soma_coincidente_bt_mt_gd_e_influencia(circuito):
    service, ident = circuito
    resultado = concluir(service, ident)
    series = resultado["series"]
    total = series["Carga do alimentador (kW)"]
    np.testing.assert_allclose(total, series["BT - líquida (kW)"] + series["MT - líquida (kW)"])
    np.testing.assert_allclose(total, series["Carga bruta BT + MT (kW)"] - series["Geração BT + MT (kW)"])
    assert series["Geração BT + MT (kW)"].sum() * .25 == pytest.approx(2 * geracao_esperada_kwh(2))
    assert sum(i["contribuicao_pico_kw"] for i in resultado["influencias"]) == pytest.approx(resultado["pico_kw"])
    assert sum(i["participacao_pico"] for i in resultado["influencias"]) == pytest.approx(1.)
    influencia = service.influencia_uc(resultado, 0)["series"]
    np.testing.assert_allclose(influencia["Alimentador sem esta UC (kW)"] + influencia["UC selecionada - líquida (kW)"], total)
    assert all(i["carga_kwh"] >= 342 for i in resultado["influencias"])
    # Filtros de visualização nunca removem UCs da curva final.
    filtrado = service.curva_alimentador(ident, FiltrosUC(subestacao="S1", alimentador="F1", busca="inexistente", classe="inexistente", grupo_tensao="BT"), 2)
    assert filtrado["unidades_incluidas"] == 5
    np.testing.assert_array_equal(filtrado["series"]["Carga do alimentador (kW)"], total)


def test_tratamentos_persistem_e_snapshot_nao_muda_com_edicao(circuito):
    service, ident = circuito
    resultado = concluir(service, ident)
    item = resultado["estado"]["ucs"][0]
    antigo = service.influencia_uc(resultado, 0)["series"]["UC selecionada - líquida (kW)"].copy()
    uc = item["uc"]
    service.curva_uc(ident, uc["entidade"], uc["linha_origem"], 2,
        potencia_modulos_kwp=16, potencia_inversor_kw=10)
    reaberto = ApplicationService(service.workspace, service.settings_path)
    assert reaberto.parametros_uc(ident, uc["entidade"], uc["linha_origem"])["potencia_modulos_kwp"] == 16
    np.testing.assert_array_equal(reaberto.influencia_uc(resultado, 0)["series"]["UC selecionada - líquida (kW)"], antigo)
    novo = reaberto.curva_alimentador(ident, FiltrosUC(subestacao="S1", alimentador="F1"), 2)
    assert not np.array_equal(novo["series"]["Carga do alimentador (kW)"], resultado["series"]["Carga do alimentador (kW)"])
    assert TratamentosUC(service.workspace).referencias("outra-versao") == {}


def _valores(row):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    saida = {}
    for c in row.findall("s:c", ns):
        v = c.find("s:v", ns)
        texto = c.find("s:is/s:t", ns)
        saida[c.attrib["r"].rstrip("0123456789")] = float(v.text) if v is not None else (texto.text if texto is not None else None)
    return saida


def test_excel_modelo_sem_perder_ucs_e_limite_por_aba(circuito, tmp_path):
    service, ident = circuito
    resultado = concluir(service, ident)
    pontos = 29 * 96
    info = exportar(service.catalog, resultado, tmp_path / "saida.xlsx", limite_linhas=2*pontos+1)
    assert info["abas_dados"] == 3 and info["registros_ucs"] == 5 * pontos
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    energia, ids = 0., []
    with ZipFile(info["path"]) as zip:
        workbook = ET.fromstring(zip.read("xl/workbook.xml"))
        nomes = [n.attrib["name"] for n in workbook.findall("s:sheets/s:sheet", ns)]
        assert nomes == ["Dados_UCs", "Dados_UCs_02", "Dados_UCs_03", "Alimentador", "Influencia_UCs", "Legenda", "Feriados"]
        for indice, quantidade in [(1, 2), (2, 2), (3, 1)]:
            doc = ET.fromstring(zip.read(f"xl/worksheets/sheet{indice}.xml"))
            rows = doc.findall("s:sheetData/s:row", ns)
            assert len(rows) == 1 + quantidade * pontos
            assert tuple(_valores(rows[0]).values()) == CABECALHO
            for i, row in enumerate(rows[1:]):
                valores = _valores(row)
                assert valores["C"] in ("BT", "MT") and valores["F"] == "Fevereiro"
                assert valores["E"] == pytest.approx((i % 96) / 96)
                assert ("M" in valores) == (valores["N"] == 1)  # irradiância só nas UCs com GD
                assert valores["L"] == pytest.approx(max(valores["K"] - valores["J"], 0))
                energia += (valores["J"] - valores["K"]) * .25
                if i % pontos == 0:
                    ids.append((valores["B"], valores["C"]))
                else:
                    assert (valores["B"], valores["C"]) == ids[-1]
        assert len(set(ids)) == 5
    assert energia == pytest.approx(resultado["energia_reconstituida_kwh"], rel=1e-10)


def test_cancelar_exportacao_preserva_arquivo_anterior(circuito, tmp_path):
    service, ident = circuito
    resultado = concluir(service, ident)
    destino = tmp_path / "saida.xlsx"
    destino.write_bytes(b"anterior")
    token = Cancelamento()
    def cancelar(*_):
        token.cancelar()
    with pytest.raises(ErroBDGD):
        exportar(service.catalog, resultado, destino, token=token, progress=cancelar)
    assert destino.read_bytes() == b"anterior"
    assert list(tmp_path.glob(".saida-*.xlsx")) == []


def test_ano_completo_bissexto_e_todas_as_ucs(circuito):
    service, ident = circuito
    resultado = concluir(service, ident, 0)
    assert len(resultado["timestamps"]) == 366 * 96
    assert len(resultado["tipos_dia"]) == 366
    assert resultado["timestamps"][59 * 96].strftime("%d/%m") == "29/02"
    assert resultado["series"]["Geração BT + MT (kW)"].sum() * .25 == pytest.approx(2 * geracao_esperada_kwh(0))
    assert resultado["estado"]["pendentes"] == 0


def test_outro_alimentador_nao_entra_e_mesmo_id_em_bt_mt_nao_colide(tmp_path):
    import csv
    fonte = fixture(tmp_path / "fonte", size=2, errors=False)
    for nome, linha in [("SUB.csv", "S2,Segunda subestação\n"), ("CTMT.csv", "F2,S2,Segundo alimentador\n")]:
        with (fonte / nome).open("a", encoding="utf-8") as f:
            f.write(linha)
    path = fonte / "UCMT_tab.csv"
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        campos = reader.fieldnames
        rows = list(reader)
    rows[-1]["CTMT"] = "F2"
    rows[-1]["SUB"] = "S2"
    rows[0]["COD_ID"] = "ucbt-0000000"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(rows)
    work = tmp_path / "work"
    manifest = import_bdgd(fonte, work)
    copiar_nasa(irradiancia_padrao(), work)
    service = ApplicationService(work, work / "settings.toml")
    ident = manifest["import_id"]
    s1 = service.tratamentos_alimentador(ident, "S1", "F1", 2)
    s2 = service.tratamentos_alimentador(ident, "S2", "F2", 2)
    assert s1["total"] == 4 and s2["total"] == 1
    assert all(i["uc"]["alimentador"] == "F1" for i in s1["ucs"])
    with pytest.raises(ErroBDGD, match="Não há UCs"):
        service.tratamentos_alimentador(ident, "S1", "F2", 2)
    resultado = concluir(service, ident)
    assert resultado["unidades_incluidas"] == 4
    iguais = [i for i in resultado["estado"]["ucs"] if i["uc"]["id_uc"] == "ucbt-0000000"]
    assert len(iguais) == 2 and iguais[0]["referencias"] != iguais[1]["referencias"]


def test_tratamento_de_metodo_antigo_volta_a_ficar_pendente(circuito):
    import sqlite3
    service, ident = circuito
    concluir(service, ident)
    assert service.tratamentos_alimentador(ident, "S1", "F1", 2)["pendentes"] == 0
    store = TratamentosUC(service.workspace)
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE curvas SET parametros = json_remove(parametros, '$.metodo')")
    estado = service.tratamentos_alimentador(ident, "S1", "F1", 2)
    assert estado["pendentes"] == 5
    estado = service.tratar_em_lote(ident, "S1", "F1", 2)
    assert estado["pendentes"] == 0
