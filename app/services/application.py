from dataclasses import replace
from pathlib import Path
import json
import shutil
from uuid import uuid4
from ..config.settings import Settings
from ..core.logging import setup_logging
from ..core.errors import ErroBDGD
from ..data.repositories.catalogo import Catalogo
from ..data.repositories.uc import UCRepository, FiltrosUC, opcoes_regiao, rotular
from . import projeto as projeto_mod
from .aneel_solar import converter_arquivo_tecnico
from ..ingest.pipeline import import_bdgd
from .curvas import gerar_curva_uc
from .network_map import opcoes_mapa, desenhar_rede, filtrar_alimentador
from ..data.repositories.tratamentos import TratamentosUC
from . import alimentadores

VERSAO_INDICE_REGIOES = 1


class ApplicationService:
    """Casos de uso do CurvaGD sobre a base importada (uma so: nao ha historico)."""

    def __init__(self, workspace: Path, settings_path: Path):
        self.settings_path = settings_path
        self.settings = Settings.load(settings_path)
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        setup_logging(self.workspace)
        self.catalog = Catalogo(self.workspace)
        self._choices_cache = {}
        self._regioes = None
        self._map_cache = None
        self.projeto = self._carregar_projeto()

    # ------------------------------------------------------------ projeto
    def _carregar_projeto(self):
        """O projeto salvo; sem ele, a importacao ativa do catalogo vira o projeto."""
        salvo = projeto_mod.Projeto.carregar(self.workspace)
        versoes = self.catalog.list()
        ids = {v["import_id"] for v in versoes}
        if salvo is not None and salvo.import_id in ids:
            return salvo
        ativa = next((v for v in versoes if v["ativa"]), None) or (versoes[0] if versoes else None)
        if ativa is None:
            projeto = salvo or projeto_mod.Projeto(distribuidora=self.settings.distribuidora, ano=self.settings.ano)
            projeto.import_id = ""
            return projeto
        manifest = self.catalog.manifest(ativa["import_id"]) or {}
        projeto = salvo or projeto_mod.Projeto()
        projeto.import_id = ativa["import_id"]
        projeto.bdgd = manifest.get("source") or projeto.bdgd
        projeto.distribuidora = manifest.get("distribuidora") or projeto.distribuidora
        projeto.ano = int(manifest.get("ano_base") or projeto.ano)
        projeto.importado_em = manifest.get("created_utc") or projeto.importado_em
        if projeto_mod.aneel_local(self.workspace).is_file() and not projeto.aneel_fonte:
            projeto.aneel_fonte = "Arquivo técnico da ANEEL importado anteriormente"
        try:
            projeto.salvar(self.workspace)
        except OSError:
            pass
        return projeto

    @property
    def import_id(self):
        return self.projeto.import_id

    @property
    def base_carregada(self):
        return bool(self.projeto.import_id)

    def nasa_path(self):
        """CSV da NASA usado por todos os modos: a copia feita na importacao."""
        local = projeto_mod.nasa_local(self.workspace)
        if local.is_file():
            return local
        return None

    def parametros_solares(self):
        return self.projeto.parametros_solares()

    def atualizar_parametros_solares(self, performance_ratio, cut_in, razao_kw_kva):
        self.projeto.performance_ratio = float(performance_ratio)
        self.projeto.cut_in = float(cut_in)
        self.projeto.razao_kw_kva = float(razao_kw_kva)
        self.projeto.parametros_validos()
        if self.base_carregada:
            self.projeto.salvar(self.workspace)
        return self.projeto

    def versions(self):
        return self.catalog.list()

    def importar(self, source, dist, ano, token, progress, validate_only=False):
        return import_bdgd(Path(source), self.workspace, dist, ano, calendar_path=self.settings.calendario or None,
                           batch_size=self.settings.batch_size, memory_mb=self.settings.memory_mb, token=token,
                           progress=progress, validate_only=validate_only)

    def importar_projeto(self, bdgd, nasa, aneel, dist, ano, token, progress, parametros=None):
        """Importa a BDGD e prepara NASA e ANEEL para todos os modos.

        Tudo o que pode falhar rapido e conferido antes da importacao da BDGD; a
        base anterior so e apagada depois que a nova ficou pronta.
        """
        token.verificar()
        bdgd, nasa, aneel = str(bdgd or "").strip(), str(nasa or "").strip(), str(aneel or "").strip()
        if not bdgd or not Path(bdgd).exists():
            raise ErroBDGD("FONTE_ILEGIVEL", "Selecione a pasta .gdb da BDGD.")
        if not str(dist or "").strip():
            raise ErroBDGD("ESQUEMA_INVALIDO", "Informe a distribuidora.")
        ano = int(ano)
        progress("Conferindo a irradiância da NASA", 0, 0)
        projeto_mod.validar_nasa(nasa, ano)
        if aneel and not Path(aneel).is_file():
            raise ErroBDGD("DADOS_GD_INCOMPLETOS", f"Arquivo da ANEEL não encontrado: {aneel}")
        avisos = []
        # ANEEL e NASA vao para uma pasta provisoria e so entram no lugar com a BDGD pronta.
        provisoria = self.workspace / projeto_mod.PASTA_ENTRADA / f"nova-{uuid4().hex}"
        provisoria.mkdir(parents=True, exist_ok=True)
        try:
            nasa_nova = provisoria / projeto_mod.NASA_LOCAL
            shutil.copyfile(nasa, nasa_nova)
            aneel_novo = None
            if aneel:
                converter_arquivo_tecnico(Path(aneel), provisoria / "solar_aneel", token=token, progress=progress)
                aneel_novo, fonte_aneel, aviso_aneel = provisoria / "solar_aneel" / "equipamentos.parquet", f"Arquivo escolhido: {aneel}", ""
            else:
                fonte_aneel = ""
                aviso_aneel = ("Base técnica da ANEEL não informada. As GDs usam o POT_INST das UGs; "
                               "selecione o arquivo técnico e importe novamente para usar as potências cadastradas.")
            if aviso_aneel:
                avisos.append(aviso_aneel)
            manifest = self.importar(bdgd, dist.strip(), ano, token, progress)
            destino_nasa = projeto_mod.nasa_local(self.workspace)
            destino_nasa.parent.mkdir(parents=True, exist_ok=True)
            nasa_nova.replace(destino_nasa)
            if aneel_novo is not None:
                destino_aneel = projeto_mod.aneel_local(self.workspace)
                destino_aneel.parent.mkdir(parents=True, exist_ok=True)
                aneel_novo.replace(destino_aneel)
            else:
                projeto_mod.aneel_local(self.workspace).unlink(missing_ok=True)
        finally:
            shutil.rmtree(provisoria, ignore_errors=True)
        novo = projeto_mod.Projeto(
            bdgd=bdgd, nasa=nasa, aneel=aneel, distribuidora=dist.strip(), ano=ano,
            import_id=manifest["import_id"], importado_em=projeto_mod.agora(),
            aneel_fonte=fonte_aneel, aneel_aviso=aviso_aneel,
            performance_ratio=(parametros or {}).get("performance_ratio", self.projeto.performance_ratio),
            cut_in=(parametros or {}).get("cut_in", self.projeto.cut_in),
            razao_kw_kva=(parametros or {}).get("razao_kw_kva", self.projeto.razao_kw_kva))
        novo.parametros_validos()
        novo.salvar(self.workspace)
        self.projeto = novo
        self._limpar_caches()
        removidas = projeto_mod.remover_versoes_antigas(self.catalog, novo.import_id, self.workspace)
        # Daqui em diante a base nova ja esta valendo: um cancelamento ou erro so
        # adia o indice da correcao, que e montado de novo ao abrir aquela tela.
        try:
            projeto_mod.preparar_indice_bdgd(bdgd, self.workspace, token=token, progress=progress)
        except Exception as exc:
            avisos.append(f"A BDGD foi importada, mas o índice da correção de demanda não foi montado agora "
                          f"({exc}); ele será montado ao abrir a correção.")
        progress("Calculando municípios, subestações e alimentadores", 0, 0)
        self.indice_regioes()
        progress("Importação concluída", 1, 1)
        return {"manifest": manifest, "projeto": novo, "removidas": removidas, "avisos": avisos}

    def _limpar_caches(self):
        from ..calculo import motor
        self._choices_cache.clear()
        self._regioes = None
        self._map_cache = None
        motor.limpar_caches()

    def repository(self, import_id=None):
        import_id = import_id or self.import_id
        if not import_id:
            raise ErroBDGD("ESQUEMA_INVALIDO", "Importe os dados primeiro.")
        return UCRepository(self.catalog, import_id)

    # ------------------------------------------------------------ regioes
    def indice_regioes(self, token=None, import_id=None):
        """Municipios, subestacoes e alimentadores ativos (em memoria e no disco)."""
        import_id = import_id or self.import_id
        if self._regioes is not None and self._regioes[0] == import_id:
            return self._regioes[1]
        repo = self.repository(import_id)
        arquivo = repo.directory / "regioes.json"
        indice = None
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if dados.get("versao") == VERSAO_INDICE_REGIOES:
                indice = dados["indice"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            indice = None
        if indice is None:
            indice = repo.indice_regioes(token)
            try:
                arquivo.write_text(json.dumps({"versao": VERSAO_INDICE_REGIOES, "indice": indice}, ensure_ascii=False),
                                   encoding="utf-8")
            except OSError:
                pass
        self._regioes = (import_id, indice)
        return indice

    def opcoes_regiao(self, kind, filters=FiltrosUC(), token=None):
        indice = self.indice_regioes(token)
        return rotular(indice, kind, opcoes_regiao(indice, kind, filters))

    def regions(self, import_id, filters, token):
        import_id = import_id or self.import_id
        repo = self.repository(import_id)
        indice = self.indice_regioes(token, import_id)

        def choices(kind):
            token.verificar()
            key = (import_id, kind)
            if key not in self._choices_cache:
                self._choices_cache[key] = repo.named_choices(kind, FiltrosUC(), token)
            return self._choices_cache[key]

        return {"municipios": rotular(indice, "municipio", opcoes_regiao(indice, "municipio")),
                "subestacoes": rotular(indice, "subestacao", opcoes_regiao(indice, "subestacao",
                                                                             FiltrosUC(municipio=filters.municipio))),
                "alimentadores": rotular(indice, "alimentador", opcoes_regiao(indice, "alimentador", FiltrosUC(
                    municipio=filters.municipio, subestacao=filters.subestacao))),
                "classes": choices("classe"), "grupos": choices("grupo_tensao")}

    def page(self, import_id, filters, token):
        return self.repository(import_id).page(filters, token)

    def summary(self, import_id, filters, token):
        return self.repository(import_id).summary(filters, token)

    def detail(self, import_id, entity, line, token):
        return self.repository(import_id).detail(entity, line, token)

    def curva_uc(self, import_id, entity, line, month, token=None, **parameters):
        """Curvas da UC com a NASA e os parametros solares da tela inicial."""
        import_id = import_id or self.import_id
        parameters = {"irradiancia_path": str(self.nasa_path() or ""), "dados_aneel_path": "",
                      **self.parametros_solares(), **parameters}
        resultado = gerar_curva_uc(self.catalog, import_id, entity, line, month, token=token, **parameters)
        TratamentosUC(self.workspace).salvar(import_id, resultado, parameters, token)
        resultado["tratamento_salvo"] = True
        return resultado

    def curva_alimentador(self, import_id, filters, month, token=None, progress=None):
        return alimentadores.gerar(self.catalog, import_id, filters.subestacao, filters.alimentador, month,
                                  token, **({"progress": progress} if progress else {}))

    def parametros_uc(self, import_id, entity, line):
        return TratamentosUC(self.workspace).parametros(import_id, entity, line)

    def tratamentos_alimentador(self, import_id, subestacao, alimentador, mes=0, token=None):
        return alimentadores.verificar(self.catalog, import_id, subestacao, alimentador, mes, token)

    def tratar_em_lote(self, import_id, subestacao, alimentador, mes, token=None, progress=None):
        return alimentadores.tratar_em_lote(self.catalog, import_id, subestacao, alimentador, mes, token,
                                         **self.parametros_solares(),
                                         **({"progress": progress} if progress else {}))

    def influencia_uc(self, resultado, indice, token=None):
        return alimentadores.contribuicao(self.catalog, resultado, indice, token)

    def exportar_alimentador(self, resultado, caminho, token=None, progress=None):
        from .exportar_alimentador import exportar
        return exportar(self.catalog, resultado, caminho, token=token,
                        **({"progress": progress} if progress else {}))

    def report(self, import_id):
        manifest = self.catalog.manifest(import_id)
        if not manifest:
            return "Nenhuma importação selecionada."
        path = Path(manifest["reports"]["validation"])
        return path.read_text(encoding="utf-8") + "\n\n" + json.dumps(manifest["reports"], indent=2, ensure_ascii=False)

    def map_source(self, import_id=None):
        if not import_id or import_id == self.import_id:
            return self.projeto.bdgd
        manifest = self.catalog.manifest(import_id) if import_id else None
        return manifest.get("source", "") if manifest else ""

    def map_options(self, path, token=None):
        return opcoes_mapa(path, token)

    def network_map(self, path, subestacao, alimentador="", token=None):
        key = (str(Path(path).expanduser().resolve()), str(subestacao))
        if token:
            token.verificar()
        cached = self._map_cache
        if cached is not None and cached[0] == key:
            full = cached[1]
        else:
            full = desenhar_rede(path, subestacao, token=token)
            if token:
                token.verificar()
            self._map_cache = (key, full)
        return filtrar_alimentador(full, alimentador)


# Casos de uso públicos, sem dependência de Qt.
PerfilarFonteBDGD = lambda service, *args, **kwargs: service.importar(*args, **kwargs, validate_only=True)
ValidarFonteBDGD = PerfilarFonteBDGD
ImportarBDGD = lambda service, *args, **kwargs: service.importar(*args, **kwargs)
ListarMunicipios = lambda repo, token=None: repo.choices("municipio", token=token)
ListarSubestacoes = lambda repo, filtros=FiltrosUC(), token=None: repo.choices("subestacao", filtros, token)
ListarAlimentadores = lambda repo, filtros=FiltrosUC(), token=None: repo.choices("alimentador", filtros, token)
ResumirAlimentador = lambda repo, filtros, token=None: repo.summary(filtros, token)
ListarUnidadesConsumidoras = lambda repo, filtros, token=None: repo.page(filtros, token)


def DiagnosticarCurvabilidade(uc, meses=tuple(range(1, 13))):
    if any(type(m) is not int or m not in range(1, 13) for m in meses) or not meses:
        raise ValueError("Selecione meses entre 1 e 12")
    flags = set(uc["status"].split("|")) - {"CURVAVEL", "NAO_CURVAVEL_ENERGIA"}
    if set(meses).intersection(uc["meses_invalidos"]):
        flags.add("NAO_CURVAVEL_ENERGIA")
    return sorted(flags) or ["CURVAVEL"]
