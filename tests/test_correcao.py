"""Pagina Correcao de demanda: os modulos da CORRECAO_DEMANDA_BDGD dentro do CurvaGD."""
from types import SimpleNamespace

import numpy as np
import pytest

from app.calculo import irradiancia
from app.config.recursos import irradiancia_padrao
from app.correcao import alimentador, aneel, bdgd, demanda, estatisticas
from tests.fixtures.gpkg import gerar, gerar_gdb


@pytest.fixture
def base(tmp_path):
    bdgd.definir_pasta_cache(tmp_path / "cache_bdgd")
    gpkg = gerar(tmp_path / "bdgd.gpkg")
    tecnico = tmp_path / "empreendimento-gd-teste.csv"
    tecnico.write_text("CodGeracaoDistribuida;MdaPotenciaModulos;MdaPotenciaInversores\n"
                       "GD.MT.001;4,00;3,00\nGD.MT.001;2,50;2,00\nGD.OUTRA;9;9\n", encoding="latin-1")
    return gpkg, tecnico


def test_calculo_do_alimentador_pela_bdgd(base):
    gpkg, tecnico = base
    fonte = bdgd.abrir(gpkg)
    ucs, resumo = fonte.unidades("F1")
    # 6 UCs de BT ativas (UC5 em duas linhas, somadas; UC9 inativa fora) e 1 de MT
    assert (resumo["bt"], resumo["mt"], resumo["repetidos"]) == (6, 1, 1)
    assert float(ucs.loc[ucs["COD_ID"] == "UC5", "ENE_01"].iloc[0]) == 350.0 + 40.0
    potencias = aneel.potencias_por_uc(ucs, aneel.potencias(tecnico, ucs["CEG_GD"]), fonte.geradoras("F1"))
    status = dict(zip(ucs["COD_ID"], potencias["STATUS"]))
    assert status["UC0"] == aneel.STATUS_ANEEL and status["UC1"] == aneel.STATUS_UGBT
    assert potencias.loc[ucs["COD_ID"] == "UC0", "POT_MODULOS"].iloc[0] == pytest.approx(6.5)
    # POT_INST de 3000 numa UC de BT estava em W: vira 3 kVA
    assert potencias.loc[ucs["COD_ID"] == "UC1", "POT_INVERSOR"].iloc[0] == pytest.approx(3.0)

    serie, _ = irradiancia.ler_csv_nasa(irradiancia_padrao())
    irrad = irradiancia.reamostrar(irradiancia.serie_ano(serie, 2024), 15, "pchip", passo_origem=60)
    resultado, _, insumos = demanda.calcular(ucs, potencias, fonte.banco_de_curvas(), irrad, 2024,
                                             diagnostico=True, devolver_insumos=True)
    lida = resultado[demanda.COLUNAS_ENE].to_numpy()
    conferida = resultado[demanda.COLUNAS_IMPORTADA].to_numpy()
    np.testing.assert_allclose(conferida, lida, rtol=1e-9)

    tabela, indicadores, relatorio = alimentador.sintetizar(fonte, "F1", ucs, resultado, insumos)
    liquida = tabela[[f"LIQ_{f}" for f in alimentador.FASES]].sum(axis=1)
    np.testing.assert_allclose(liquida, tabela["LIQ_TOTAL"])
    assert relatorio["bt_primario_mono_bi"] == 3  # UCs no trafo T1, de primario so em B
    assert set(indicadores.index) == {"LIQUIDA", "CARGA"}

    brutas = fonte.estatisticas()
    geral = estatisticas.visao_geral(estatisticas.preparar(brutas[brutas["CTMT"] != ""]), fonte.alimentadores())
    assert int(geral.loc[geral["CTMT"] == "F1", "UCS"].iloc[0]) == 7
    fonte.fechar()


def _servico_da_pagina(tmp_path, gpkg, tecnico):
    """O minimo do ApplicationService que a pagina usa: projeto, NASA e ANEEL."""
    from app.services.aneel_solar import converter_arquivo_tecnico
    from app.services.projeto import Projeto
    work = tmp_path / "work"
    converter_arquivo_tecnico(tecnico, work / "solar_aneel")
    projeto = Projeto(bdgd=str(gpkg), ano=2024, import_id="0" * 32, aneel_fonte="teste")
    return SimpleNamespace(workspace=work, projeto=projeto, base_carregada=True,
                           nasa_path=irradiancia_padrao, parametros_solares=projeto.parametros_solares)


@pytest.mark.parametrize("formato", ["gpkg", "gdb"])
def test_pagina_usa_os_dados_de_entrada_e_a_regiao(qtbot, base, tmp_path, monkeypatch, formato):
    import sys
    from app.ui.correcao.pagina import PaginaCorrecao
    gpkg, tecnico = base
    if formato == "gdb":
        gpkg = gerar_gdb(gpkg, tmp_path / "bdgd.gdb")
        monkeypatch.setitem(sys.modules, "geopandas", None)
    pagina = PaginaCorrecao(_servico_da_pagina(tmp_path, gpkg, tecnico))
    qtbot.addWidget(pagina)
    pagina.definir_projeto()
    # sem campos de arquivo nem opcoes de passo/motor: tudo vem da tela inicial
    for antigo in ("campo_gpkg", "campo_nasa", "campo_aneel", "seletor_passo", "seletor_motor", "seletor_ano"):
        assert not hasattr(pagina, antigo)
    pagina.definir_regiao("Município: Cuiabá", [("F1 · Alimentador piloto", "F1")], "F1")
    pagina.show()
    # a regiao ja tem alimentador: as UCs dele entram ao abrir a tela
    qtbot.waitUntil(lambda: not pagina.ocupada() and pagina.ucs is not None, timeout=30000)
    assert pagina.seletor_ctmt.count() == 1 and pagina.ctmt_carregado == "F1"
    assert "7 UCs" in pagina.resumo_ucs.text() and "1 com GD na ANEEL" in pagina.resumo_ucs.text()
    assert "15 min" in pagina.rotulo_dados.text() and "PVSystem" in pagina.rotulo_dados.text()

    pagina.marcar_diagnostico.setChecked(True)
    pagina.calcular()
    qtbot.waitUntil(lambda: not pagina.ocupada() and pagina.resultado is not None, timeout=30000)
    assert pagina.modelo.rowCount() == 7 and pagina.passo_calculado == 15
    assert "DEM_MAX_12" in pagina.resultado.columns
    np.testing.assert_allclose(pagina.resultado[demanda.COLUNAS_IMPORTADA].to_numpy(),
                               pagina.resultado[demanda.COLUNAS_ENE].to_numpy(), rtol=1e-9)

    pagina.gerar_curva()
    qtbot.waitUntil(lambda: not pagina.ocupada() and pagina.sintese is not None, timeout=30000)
    assert pagina._dialogo_curva.isVisible()
    assert "Desequilibrio P95" in pagina._texto_curva.toHtml()
    pagina._dialogo_curva.close()

    pagina.abas.setCurrentWidget(pagina.painel_estatisticas)
    qtbot.waitUntil(lambda: not pagina.ocupada() and pagina.estatisticas_brutas is not None, timeout=30000)
    # so o alimentador da regiao entra nas estatisticas
    assert set(pagina._estatisticas_da_regiao()["CTMT"]) == {"F1"}
    pagina.definir_regiao("Todos", [("F1", "F1"), ("F2", "F2")], "")
    assert pagina.seletor_ctmt.count() == 2
    pagina.fechar()


def test_resultado_igual_ao_das_unidades_consumidoras(base):
    """Correcao e UCs usam a mesma irradiancia reamostrada e a mesma geracao NumPy."""
    from app.calculo import motor
    gpkg, _ = base
    serie, _ = irradiancia.ler_csv_nasa(irradiancia_padrao())
    irrad = irradiancia.reamostrar(irradiancia.serie_ano(serie, 2024), 15, "pchip", passo_origem=60)
    da_uc = motor.irradiancia_ano(irradiancia_padrao(), 2024)
    np.testing.assert_allclose(irrad.reindex(da_uc.serie.index, fill_value=0.0).to_numpy(), da_uc.serie.to_numpy())
    from app.calculo import gd
    correcao = gd.geracao_por_kva(1.3, irrad, 15).to_numpy()
    ucs = motor.geracao_por_kva_ano(da_uc, 1.3, gd.PERFORMANCE_RATIO_PADRAO, gd.CUTIN_PCT_PADRAO)
    np.testing.assert_allclose(correcao[:len(ucs)], ucs)


def test_gdb_sem_geopandas_le_igual_ao_geopackage(base, tmp_path, monkeypatch):
    """O executavel nao leva o geopandas: a leitura do .gdb nao pode depender dele."""
    import sys
    gpkg, tecnico = base
    gdb = gerar_gdb(gpkg, tmp_path / "bdgd.gdb")
    monkeypatch.setitem(sys.modules, "geopandas", None)   # qualquer import do geopandas falha
    pelo_gdb, pelo_gpkg = bdgd.abrir(gdb), bdgd.abrir(gpkg)
    assert isinstance(pelo_gdb, bdgd.FonteFileGDB)
    pelo_gdb.preparar()
    ucs_gdb, resumo_gdb = pelo_gdb.unidades("F1")
    ucs_gpkg, resumo_gpkg = pelo_gpkg.unidades("F1")
    assert resumo_gdb == resumo_gpkg
    ordem = lambda df: df.sort_values(["TABELA", "COD_ID"]).reset_index(drop=True)
    np.testing.assert_allclose(ordem(ucs_gdb)[demanda.COLUNAS_ENE].to_numpy(dtype=float),
                               ordem(ucs_gpkg)[demanda.COLUNAS_ENE].to_numpy(dtype=float))
    assert list(ordem(ucs_gdb)["COD_ID"]) == list(ordem(ucs_gpkg)["COD_ID"])
    assert sorted(pelo_gdb.alimentadores()["COD_ID"]) == ["F1", "F2"]
    assert set(pelo_gdb.banco_de_curvas()) == set(pelo_gpkg.banco_de_curvas())
    potencias = aneel.potencias_por_uc(ucs_gdb, aneel.potencias(tecnico, ucs_gdb["CEG_GD"]), pelo_gdb.geradoras("F1"))
    assert set(potencias["STATUS"]) >= {aneel.STATUS_ANEEL, aneel.STATUS_UGBT}
    brutas = pelo_gdb.estatisticas()
    assert int(brutas.loc[brutas["CTMT"] == "F1", "UCS"].sum()) == 7
    # o indice por alimentador fica no cache e e reaproveitado
    assert list(bdgd.PASTA_CACHE.glob("bdgd-*.npz"))
    outro = bdgd.abrir(gdb)
    outro.preparar()
    assert len(outro.unidades("F1")[0]) == len(ucs_gdb)
