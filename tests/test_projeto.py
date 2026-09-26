"""Dados de entrada unicos (sem historico) e o filtro de regioes."""
import csv

import pytest

from app.config.recursos import irradiancia_padrao
from app.config.settings import Settings
from app.core.errors import ErroBDGD
from app.core.jobs import Cancelamento
from app.data.repositories.uc import FiltrosUC
from app.ingest.pipeline import import_bdgd
from app.services.application import ApplicationService
from app.services.projeto import Projeto, ano_sugerido
from tests.fixtures.generate import fixture

VARZEA_GRANDE = "5108402"
CUIABA = "5103403"


def _importar(service, fonte, **extra):
    return service.importar_projeto(fonte, irradiancia_padrao(), extra.pop("aneel", ""), "ENERGISA_MT", 2024,
                                    Cancelamento(), lambda *_: None, **extra)


def _acrescentar(fonte, nome, linhas):
    caminho = fonte / nome
    with caminho.open(encoding="utf-8", newline="") as stream:
        campos = csv.DictReader(stream).fieldnames
    with caminho.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=campos)
        for linha in linhas:
            writer.writerow({campo: linha.get(campo, "") for campo in campos})


def _uc(codigo, ctmt, sub, mun, situacao="AT"):
    return {"COD_ID": codigo, "CTMT": ctmt, "MUN": mun, "SUB": sub, "CLAS_SUB": "RE1", "TIP_CC": "T1",
            "GRU_TEN": "BT", "SIT_ATIV": situacao, **{f"ENE_{m:02d}": 100 for m in range(1, 13)}}


@pytest.fixture
def duas_cidades(tmp_path):
    """S1 em Cuiaba; S2 em Varzea Grande, com uma UC de Cuiaba; S3 so com UC desativada."""
    fonte = fixture(tmp_path / "fonte", size=20, errors=False)
    _acrescentar(fonte, "SUB.csv", [{"COD_ID": "S2", "NOME": "SE Várzea"}, {"COD_ID": "S3", "NOME": "SE Desligada"}])
    _acrescentar(fonte, "CTMT.csv", [{"COD_ID": "F2", "SUB": "S2", "NOME": "VG-01"},
                                     {"COD_ID": "F3", "SUB": "S3", "NOME": "DS-01"},
                                     {"COD_ID": "F4", "SUB": "S1", "NOME": "Reserva"}])
    _acrescentar(fonte, "UCBT_tab.csv", [_uc(f"vg-{i}", "F2", "S2", VARZEA_GRANDE) for i in range(10)]
                 + [_uc("cuiaba-no-vg", "F2", "S2", CUIABA), _uc("desligada", "F3", "S3", CUIABA, "DS")])
    return fonte


def test_subestacoes_do_municipio_e_alimentadores_ativos(duas_cidades, tmp_path):
    work = tmp_path / "work"
    import_bdgd(duas_cidades, work)
    service = ApplicationService(work, work / "settings.toml")
    codigos = lambda kind, **f: [c for _, c in service.opcoes_regiao(kind, FiltrosUC(**f))]
    assert codigos("municipio") == [CUIABA, VARZEA_GRANDE]
    # a UC de Cuiaba ligada em S2 nao traz a subestacao de Varzea Grande para Cuiaba
    assert codigos("subestacao", municipio=CUIABA) == ["S1"]
    assert codigos("subestacao", municipio=VARZEA_GRANDE) == ["S2"]
    assert sorted(codigos("subestacao")) == ["S1", "S2"]  # S3 nao tem UC ativa
    # F4 (sem UC) e F3 (so UC desativada) nao sao alimentadores ativos
    assert codigos("alimentador", subestacao="S1") == ["F1"]
    assert codigos("alimentador", municipio=CUIABA) == ["F1"]
    assert service.opcoes_regiao("subestacao", FiltrosUC(municipio=VARZEA_GRANDE)) == [("SE Várzea · S2", "S2")]
    # a tabela vale pelo nivel mais especifico: o alimentador inteiro, inclusive a UC de Cuiaba
    assert service.page(None, FiltrosUC(municipio=VARZEA_GRANDE), None)["total"] == 10
    assert service.page(None, FiltrosUC(municipio=VARZEA_GRANDE, subestacao="S2"), None)["total"] == 11
    assert service.page(None, FiltrosUC(municipio=CUIABA, subestacao="S1", alimentador="F1"), None)["total"] == 23
    # so UCs ativas, como na correcao de demanda
    assert service.page(None, FiltrosUC(busca="desligada"), None)["total"] == 0


def test_nova_importacao_apaga_a_anterior(source, tmp_path):
    from app.data.repositories.tratamentos import TratamentosUC
    work = tmp_path / "work"
    service = ApplicationService(work, work / "settings.toml")
    assert not service.base_carregada
    primeira = _importar(service, source)["manifest"]["import_id"]
    uc = service.page(None, FiltrosUC(busca="ucbt-0000003"), None)["linhas"][0]
    service.curva_uc(None, uc["entidade"], uc["linha_origem"], 2)
    assert TratamentosUC(work).referencias(primeira)
    pasta_antiga = next((work / "datasets").rglob(f"versao={primeira}"))
    # sobras de importacoes que nem chegaram ao catalogo tambem saem
    (work / "reports" / "validacao_bdgd_x_2024_abc.csv").write_text("x", encoding="utf-8")
    orfa = pasta_antiga.parent / ("versao=" + "f" * 32)
    orfa.mkdir()
    (orfa / "manifest.json").write_text("{}", encoding="utf-8")
    resultado = _importar(service, source, parametros={"performance_ratio": 0.9, "cut_in": 1.0, "razao_kw_kva": 1.3})
    segunda = resultado["manifest"]["import_id"]
    assert resultado["removidas"] == [primeira]
    assert [v["import_id"] for v in service.versions()] == [segunda]
    assert not pasta_antiga.exists()
    assert not list((work / "reports").glob(f"*_{primeira}.*"))
    assert all(segunda in arquivo.name for arquivo in (work / "reports").iterdir())
    assert not orfa.exists()
    assert not TratamentosUC(work).referencias(primeira)
    assert service.parametros_solares() == {"performance_ratio": 0.9, "cut_in_percentual": 1.0, "razao_kw_kva": 1.3}
    reaberto = ApplicationService(work, work / "settings.toml")
    assert reaberto.import_id == segunda and reaberto.projeto.bdgd == str(source)
    assert reaberto.nasa_path() == work / "entrada" / "irradiancia_nasa.csv"


def test_importacao_invalida_preserva_a_base_atual(source, tmp_path):
    work = tmp_path / "work"
    service = ApplicationService(work, work / "settings.toml")
    atual = _importar(service, source)["manifest"]["import_id"]
    with pytest.raises(ErroBDGD, match="NASA"):
        service.importar_projeto(source, tmp_path / "nao-existe.csv", "", "ENERGISA_MT", 2024, Cancelamento(),
                                 lambda *_: None)
    with pytest.raises(ErroBDGD, match="2023"):
        service.importar_projeto(source, irradiancia_padrao(), "", "ENERGISA_MT", 2023, Cancelamento(),
                                 lambda *_: None)
    assert service.import_id == atual and [v["import_id"] for v in service.versions()] == [atual]
    assert not list((work / "entrada").glob("nova-*"))


def test_catalogo_antigo_sem_projeto_vira_projeto(source, tmp_path):
    work = tmp_path / "work"
    manifest = import_bdgd(source, work)
    service = ApplicationService(work, work / "settings.toml")
    assert service.import_id == manifest["import_id"]
    assert service.projeto.bdgd == manifest["source"]
    assert Projeto.carregar(work).import_id == manifest["import_id"]
    assert service.nasa_path() is None


def test_settings_corrompido_nao_impede_abrir(tmp_path):
    arquivo = tmp_path / "settings.toml"
    arquivo.write_text('workspace = "C:\\Users\\ABNER\\Versão 1.4"\nbatch_size = 5000\n', encoding="utf-8")
    assert Settings.load(arquivo) == Settings()
    ApplicationService(tmp_path, arquivo)


def test_ano_sugerido_pelo_nome_da_bdgd():
    assert ano_sugerido(r"C:\dados\Energisa_MT_405_2024-12-31_V11_20251003-1011.gdb") == 2024
    assert ano_sugerido("pasta_sem_data") is None
