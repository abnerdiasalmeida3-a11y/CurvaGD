from datetime import date
from pathlib import Path
from threading import Event

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.errors import ErroBDGD
from app.core.jobs import Cancelamento
from app.services import aneel_solar as aneel


def base_oficial(tmp_path, linhas=None):
    linhas = linhas or [("GD.TESTE.1", 6.6, 5.0), ("GD.TESTE.2", 9.9, 8.0)]
    arquivo = tmp_path / "oficial.parquet"
    pq.write_table(pa.table({
        "CodGeracaoDistribuida": [r[0] for r in linhas],
        "MdaPotenciaModulos": [r[1] for r in linhas],
        "MdaPotenciaInversores": [r[2] for r in linhas],
        "DatConexao": [date(2024, 1, 1)] * len(linhas),
        "DatGeracaoConjuntoDados": [date(2026, 9, 22)] * len(linhas),
    }), arquivo)
    pasta = tmp_path / "solar_aneel"
    pasta.mkdir(exist_ok=True)
    aneel.preparar_base(arquivo, pasta / "equipamentos.parquet")
    return pasta


def test_base_local_separa_ucs_e_funciona_sem_rede(tmp_path, monkeypatch):
    base_oficial(tmp_path)
    monkeypatch.setattr(aneel, "_abrir", lambda *_: pytest.fail("Não deve acessar a rede com cache recente"))
    for codigo, dc, ac in [("gd.teste.1", 6.6, 5), ("GD.TESTE.2", 9.9, 8)]:
        resultado = aneel.buscar_potencias(tmp_path, codigo)
        equipamento = resultado["equipamento"]
        assert equipamento.potencia_modulos_kwp == dc
        assert equipamento.potencia_inversor_kw == ac
        assert "2026-09-22" in equipamento.fonte
    with pytest.raises(ErroBDGD, match="não encontrados"):
        aneel.buscar_potencias(tmp_path, "GD.DESCONHECIDA")


def test_linhas_do_mesmo_ceg_sao_somadas(tmp_path):
    base_oficial(tmp_path, [("GD.1", 6., 5.), ("GD.1", 7., 5.), ("GD.2", 1., 1.)])
    equipamento = aneel.buscar_potencias(tmp_path, "GD.1")["equipamento"]
    assert (equipamento.potencia_modulos_kwp, equipamento.potencia_inversor_kw) == (13., 10.)
    assert "soma de 2 linha(s)" in equipamento.fonte
    somas, _, _ = aneel.potencias_em_lote(tmp_path, ["GD.1", "gd.2", "GD.X"])
    assert somas == {"gd1": (13., 10., 2), "gd2": (1., 1., 1)}


@pytest.mark.parametrize("linhas,trecho", [
    ([("GD.1", -1., 5.)], "inválidas"),
    ([("GD.1", float('nan'), 5.)], "inválidas"),
])
def test_cadastro_ambiguo_ou_invalido_nao_preenche(tmp_path, linhas, trecho):
    base_oficial(tmp_path, linhas)
    with pytest.raises(ErroBDGD, match=trecho):
        aneel.buscar_potencias(tmp_path, "GD.1")


def test_ausencia_de_base_nao_dispara_download(tmp_path, monkeypatch):
    pasta = base_oficial(tmp_path)
    anterior = (pasta / "equipamentos.parquet").read_bytes()
    def offline(*args):
        raise OSError("Sem conexão")
    monkeypatch.setattr(aneel, "_abrir", offline)
    resultado = aneel.buscar_potencias(tmp_path, "GD.TESTE.1")
    assert resultado["aviso"] == ""
    assert (pasta / "equipamentos.parquet").read_bytes() == anterior
    with pytest.raises(ErroBDGD, match="Selecione e importe"):
        aneel.buscar_potencias(tmp_path / "vazio", "GD.TESTE.1")
    assert aneel.potencias_em_lote(tmp_path / "vazio", ["GD.TESTE.1"])[0] == {}


def test_download_prepara_e_substitui_cache_atomicamente(tmp_path, monkeypatch):
    import io
    import json
    base_oficial(tmp_path)
    conteudo = (tmp_path / "oficial.parquet").read_bytes()
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(conteudo))}
    def abrir(url):
        if url == aneel.CATALOGO_URL:
            return Response(json.dumps({"result": {"resources": [{"name": aneel.RECURSO,
                "format": "PARQUET", "id": "teste", "url": "https://dadosabertos.aneel.gov.br/oficial.parquet"}]}}).encode())
        return Response(conteudo)
    monkeypatch.setattr(aneel, "_abrir", abrir)
    aneel.atualizar_base(tmp_path / "solar_aneel")
    resultado = aneel.buscar_potencias(tmp_path, "GD.TESTE.2")
    assert resultado["equipamento"].potencia_modulos_kwp == 9.9
    assert sorted(p.name for p in (tmp_path / "solar_aneel").iterdir()) == ["equipamentos.parquet"]
    # Resposta truncada não substitui uma base válida.
    Response.headers = {"Content-Length": str(len(conteudo) + 100)}
    with pytest.raises(ErroBDGD, match="Download incompleto"):
        aneel.atualizar_base(tmp_path / "solar_aneel")
    assert aneel.buscar_potencias(tmp_path, "GD.TESTE.2")["equipamento"].potencia_modulos_kwp == 9.9


def test_cancelamento_nao_vira_fallback_silencioso(tmp_path):
    base_oficial(tmp_path)
    token = Cancelamento()
    token.cancelar()
    with pytest.raises(ErroBDGD, match="cancelada"):
        aneel.buscar_potencias(tmp_path, "GD.TESTE.1", token=token)


def test_tela_abre_preenchida_por_uc_e_preserva_edicao(qtbot, tmp_path):
    from app.ui.parameters import CurveParametersDialog
    base_oficial(tmp_path)
    for codigo, dc, ac in [("GD.TESTE.1", 6.6, 5.), ("GD.TESTE.2", 9.9, 8.)]:
        dialog = CurveParametersDialog(tem_gd=True, codigo_gd=codigo, workspace=tmp_path)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitUntil(lambda: dialog._loaded_powers is not None and dialog._equipment_worker is None, timeout=10000)
        assert dialog.modules.value() == dc
        assert dialog.inverter.value() == ac
        assert dialog.generate_button.isEnabled()
        dialog.modules.setValue(10.123)
        valores = dialog.values()
        assert valores["potencia_modulos_kwp"] == 10.123
        assert "editadas pelo usuário" in valores["referencia_potencias"]
        dialog.close()


def test_tela_sem_cadastro_permite_manual(qtbot, tmp_path):
    from app.ui.parameters import CurveParametersDialog
    base_oficial(tmp_path)
    dialog = CurveParametersDialog(tem_gd=True, codigo_gd="GD.AUSENTE", workspace=tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: "não encontrados" in dialog.power_status.text() and dialog._equipment_worker is None, timeout=10000)
    assert dialog.modules.value() == dialog.inverter.value() == 0
    dialog.modules.setValue(7)
    dialog.accept()
    assert dialog.isVisible() and "duas potências" in dialog.error.text()
    dialog.inverter.setValue(5)
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_fechar_durante_consulta_cancela_sem_destruir_thread(qtbot, tmp_path, monkeypatch):
    from app.ui import parameters
    iniciou = Event()
    def demorado(*args, token, **kwargs):
        iniciou.set()
        while not token._event.wait(.01):
            pass
        token.verificar()
    monkeypatch.setattr(parameters, "buscar_potencias", demorado)
    dialog = parameters.CurveParametersDialog(tem_gd=True, codigo_gd="GD.1", workspace=tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(iniciou.is_set, timeout=5000)
    assert not dialog.generate_button.isEnabled()
    dialog.reject()
    qtbot.waitUntil(lambda: dialog._equipment_worker is None and not dialog.isVisible(), timeout=5000)


@pytest.mark.parametrize("formato", ["csv", "zip", "parquet"])
def test_arquivo_escolhido_vira_copia_local_usada_pelo_dialogo(qtbot, tmp_path, formato):
    import zipfile
    from app.ui.parameters import CurveParametersDialog
    texto = ("DatGeracaoConjuntoDados;CodGeracaoDistribuida;MdaPotenciaModulos;MdaPotenciaInversores\n"
             "2026-09-01;GD.1;6,6;5\n2026-09-01;GD.2;13,2;10\n2026-09-01;GD.2;1.000,5;2\n")
    arquivo = tmp_path / f"tecnico.{formato}"
    if formato == "csv":
        arquivo.write_text(texto, encoding="cp1252")
    elif formato == "zip":
        with zipfile.ZipFile(arquivo, "w") as z:
            z.writestr("tecnico.csv", texto.encode("cp1252"))
    else:
        pq.write_table(pa.table({"CodGeracaoDistribuida": ["GD.1", "GD.2", "GD.2"],
                                 "MdaPotenciaModulos": ["6,6", "13,2", "1.000,5"],
                                 "MdaPotenciaInversores": ["5", "10", "2"]}), arquivo)
    assert aneel.converter_arquivo_tecnico(arquivo, tmp_path / "solar_aneel") == 3
    dialog = CurveParametersDialog(tem_gd=True, codigo_gd="GD.2", workspace=tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog._loaded_powers is not None and dialog._equipment_worker is None, timeout=10000)
    assert dialog.modules.value() == pytest.approx(1013.7)
    assert dialog.inverter.value() == 12
    assert not hasattr(dialog, "aneel") and not hasattr(dialog, "irradiance")
    dialog.close()


def test_arquivo_sem_colunas_da_aneel_e_recusado(tmp_path):
    arquivo = tmp_path / "errado.csv"
    arquivo.write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(ErroBDGD, match="CodGeracaoDistribuida"):
        aneel.converter_arquivo_tecnico(arquivo, tmp_path / "solar_aneel")
    assert not (tmp_path / "solar_aneel" / "equipamentos.parquet").exists()


def test_calculo_respeita_edicao_mesmo_com_arquivo_aneel(source, tmp_path):
    from app.config.recursos import irradiancia_padrao
    from app.ingest.pipeline import import_bdgd
    from app.services.application import ApplicationService
    from app.data.repositories.uc import FiltrosUC
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    uc = service.page(manifest["import_id"], FiltrosUC(busca="ucbt-0000000"), None)["linhas"][0]
    referencia = "Potências editadas pelo usuário; cadastro consultado: ANEEL"
    # Caminho inexistente prova que o cálculo não relê o cadastro para substituir a edição.
    for mes in [2, 0]:
        resultado = service.curva_uc(manifest["import_id"], uc["entidade"], uc["linha_origem"], mes,
                                    irradiancia_path=str(irradiancia_padrao()),
                                    dados_aneel_path=str(tmp_path / "nao-reler.csv"), potencia_modulos_kwp=8.,
                                    potencia_inversor_kw=5., referencia_potencias=referencia)
        assert resultado["fonte_equipamento"] == referencia
        for parte in resultado.get("resultados_mensais", [resultado]):
            assert parte["potencias_gd"]["modulos_kw"] == 8. and parte["potencias_gd"]["inversor_kva"] == 5.
            assert parte["balanco"].energia_gerada_kwh > 0


def test_sem_cadastro_usa_pot_inst_da_ug_ou_trata_sem_geracao(source, tmp_path):
    from app.ingest.pipeline import import_bdgd
    from app.services.application import ApplicationService
    from app.data.repositories.uc import FiltrosUC
    from app.calculo import potencias
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    uc = service.page(manifest["import_id"], FiltrosUC(busca="ucbt-0000000"), None)["linhas"][0]
    resultado = service.curva_uc(manifest["import_id"], uc["entidade"], uc["linha_origem"], 2)
    assert resultado["potencias_gd"]["status"] in (potencias.STATUS_UGBT, potencias.STATUS_SEM_POTENCIA)
    assert resultado["balanco"].energia_importada_kwh == pytest.approx(uc["energia_mes_02"])
