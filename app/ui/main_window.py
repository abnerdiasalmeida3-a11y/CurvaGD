import json
import csv
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QStackedWidget, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog,
    QProgressBar, QTextBrowser, QTableView, QTableWidget, QTableWidgetItem, QFormLayout, QDialog,
    QDialogButtonBox, QHeaderView, QGroupBox, QTabWidget, QStyle, QToolButton)
from . import theme
from .parameters import CurveParametersDialog
from .correcao.pagina import PaginaCorrecao
from .charts import (construir_grafico, CurvaChartView, PainelCurvasTipicas,
                     SeletorDeSeries)
from .jobs import Worker
from .network_map import NetworkMap
from .models.uc_table_model import UCTableModel, COLUMNS, STATUS_LABELS
from ..calculo import gd as gd_calc
from ..calculo.potencias import RAZAO_PADRAO
from ..data.repositories.uc import FiltrosUC, SORTABLE
from ..services.projeto import ano_sugerido

APP_NOME = "CurvaGD"
PAGINAS = ("Dados de entrada", "Regiões", "Mapa da rede", "Unidades consumidoras", "Correção de demanda")
PAGINA_DADOS, PAGINA_REGIOES, PAGINA_MAPA, PAGINA_UCS, PAGINA_CORRECAO = range(len(PAGINAS))
LARGURA_MENU = 220


def formatar_numero(valor, casas=3):
    return f"{valor:,.{casas}f}".replace(",", "#").replace(".", ",").replace("#", ".")


class MainWindow(QMainWindow):
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.jobs = {}
        self.workers = set()
        self.import_id = ""
        self.filters = FiltrosUC()
        self.total = 0
        self.closing = False
        self._regioes_prontas = False
        self._rotulos = {"municipio": {}, "subestacao": {}, "alimentador": {}}
        self.setWindowTitle(APP_NOME)
        self.resize(1320, 860)
        self.setMinimumSize(980, 660)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 16, 12)
        layout.setSpacing(10)
        content = QHBoxLayout()
        content.setSpacing(10)
        # Trilho fino com o botao do menu: e o que sobra quando o menu e recolhido.
        trilho = QVBoxLayout()
        trilho.setContentsMargins(0, 0, 0, 0)
        self.menu_toggle = QToolButton()
        self.menu_toggle.setObjectName("alternarMenu")
        self.menu_toggle.setText("☰")
        self.menu_toggle.setCheckable(True)
        self.menu_toggle.setChecked(True)
        self.menu_toggle.setToolTip("Mostrar ou ocultar o menu lateral (Ctrl+B)")
        self.menu_toggle.setAccessibleName("Mostrar ou ocultar o menu lateral")
        self.menu_toggle.toggled.connect(self.set_menu_visible)
        trilho.addWidget(self.menu_toggle)
        trilho.addStretch()
        content.addLayout(trilho)
        self.navigation = QListWidget()
        self.navigation.setObjectName("nav")
        self.navigation.addItems(list(PAGINAS))
        self.navigation.setFixedWidth(LARGURA_MENU)
        self.pages = QStackedWidget()
        self.pages.setObjectName("conteudo")
        content.addWidget(self.navigation)
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
        self.notice = QLabel("Pronto.")
        self.notice.setObjectName("rodape")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self._import_page()
        self._regions_page()
        self._map_page()
        self._uc_page()
        self.correcao = PaginaCorrecao(self.service)
        self.pages.addWidget(self.correcao)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(PAGINA_DADOS)
        self.atalho_menu = QShortcut(QKeySequence("Ctrl+B"), self)
        self.atalho_menu.activated.connect(self.menu_toggle.toggle)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self.request_page)
        self.search.textChanged.connect(self.search_changed)
        self.setStyleSheet(theme.QSS)
        self.aplicar_projeto()

    # ------------------------------------------------------------ estrutura
    def set_menu_visible(self, visivel):
        self.navigation.setVisible(visivel)
        self.menu_toggle.setToolTip(("Ocultar" if visivel else "Mostrar") + " o menu lateral (Ctrl+B)")

    def set_pages_enabled(self, habilitadas):
        for linha in range(1, self.navigation.count()):
            item = self.navigation.item(linha)
            flags = item.flags()
            item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled if habilitadas else flags & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip("" if habilitadas else "Importe os dados em ‘Dados de entrada’ para liberar esta tela.")
        if not habilitadas:
            self.navigation.setCurrentRow(PAGINA_DADOS)

    def page_layout(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(11)
        label = QLabel(title)
        label.setObjectName("tituloPagina")
        layout.addWidget(label)
        self.pages.addWidget(widget)
        return layout

    def launch(self, key, action, success):
        if key in self.jobs:
            self.jobs[key].token.cancelar()
        worker = Worker(action, self)
        self.jobs[key] = worker
        self.workers.add(worker)
        worker.result.connect(lambda result: success(result) if self.jobs.get(key) is worker and not self.closing else None)
        worker.error.connect(lambda text: self.show_error(key, worker, text))
        worker.progress.connect(self.show_progress if key == "import" else lambda *_: None)
        worker.finished.connect(lambda: self.job_finished(key, worker))
        worker.start()

    def show_error(self, key, worker, text):
        if self.jobs.get(key) is worker:
            self.notice.setText(text)
            if key == "import":
                self.import_log.setPlainText("A importação não foi concluída.\n\n" + text +
                                             ("\n\nA base anterior continua carregada." if self.service.base_carregada else ""))
            if key == "network_map":
                self.map_info.setText(text)

    def job_finished(self, key, worker):
        self.workers.discard(worker)
        if self.jobs.get(key) is worker:
            self.jobs.pop(key, None)
            if key == "import":
                self._import_busy(False)
                self.progress_bar.setRange(0, 1)
                self.progress_bar.setValue(1)
        worker.deleteLater()
        if self.closing and not self.workers:
            self.close()

    # ------------------------------------------------------------ dados de entrada
    def _import_page(self):
        layout = self.page_layout("Dados de entrada")
        text = QLabel("Escolha a BDGD e o CSV da NASA para o ano analisado. A base técnica da ANEEL é opcional; "
                      "sem ela, a potência da GD vem das UGs quando disponível. Importar uma pasta nova substitui a anterior.")
        text.setObjectName("ajuda")
        text.setWordWrap(True)
        layout.addWidget(text)
        arquivos = QGroupBox("Arquivos")
        form = QFormLayout(arquivos)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.source = QLineEdit()
        self.source.setPlaceholderText("Pasta .gdb da BDGD (ex.: Energisa_MT_405_2024-12-31_….gdb)")
        self.source.editingFinished.connect(self.sugerir_ano)
        form.addRow("BDGD (.gdb)", self._path_row(self.source, self.choose_bdgd, "Escolher pasta…"))
        self.nasa_field = QLineEdit()
        self.nasa_field.setPlaceholderText("CSV horário da NASA POWER (ALLSKY_SFC_SW_DWN)")
        form.addRow("Irradiância NASA", self._path_row(self.nasa_field, self.choose_nasa, "Escolher CSV…"))
        self.aneel_field = QLineEdit()
        self.aneel_field.setPlaceholderText("Escolha o CSV, ZIP ou Parquet da ANEEL; vazio usa POT_INST das UGs")
        form.addRow("Base técnica ANEEL", self._path_row(self.aneel_field, self.choose_aneel, "Escolher arquivo…"))
        self.dist = QLineEdit(self.service.projeto.distribuidora)
        self.year = QSpinBox()
        self.year.setRange(1900, 2200)
        self.year.setValue(self.service.projeto.ano)
        form.addRow("Distribuidora", self.dist)
        form.addRow("Ano-base", self.year)
        layout.addWidget(arquivos)
        solar = QGroupBox("Simulação solar · modelo PVSystem, passo de 15 min")
        linha = QHBoxLayout(solar)
        self.pr_field = self._number_box(0.01, 1.0, gd_calc.PERFORMANCE_RATIO_PADRAO, 2, "", 0.05)
        self.pr_field.setToolTip("Irradiância-base. 1,0 é o fisicamente correto: as perdas já entram pela "
                                 "curva de temperatura e pela eficiência do inversor.")
        self.cutin_field = self._number_box(0.0, 50.0, gd_calc.CUTIN_PCT_PADRAO, 1, " %", 0.5)
        self.cutin_field.setToolTip("Potência DC mínima para o inversor ligar, em % do kVA. Datasheets: 1% a 5%.")
        self.ratio_field = self._number_box(0.1, 5.0, RAZAO_PADRAO, 2, "", 0.05)
        self.ratio_field.setToolTip("kW de módulos por kVA de inversor, usado quando a GD não está na ANEEL "
                                    "e a potência vem do POT_INST da UGBT/UGMT.")
        for rotulo, campo in (("Irradiância-base", self.pr_field), ("Cut-in do inversor", self.cutin_field),
                              ("kW/kVA de reserva", self.ratio_field)):
            linha.addWidget(QLabel(rotulo))
            linha.addWidget(campo)
            linha.addSpacing(12)
            campo.valueChanged.connect(self.solar_changed)
        linha.addStretch()
        layout.addWidget(solar)
        buttons = QHBoxLayout()
        self.import_button = QPushButton("Importar dados")
        self.import_button.setObjectName("primario")
        self.cancel_import = QPushButton("Cancelar")
        self.cancel_import.setEnabled(False)
        self.report_button = QPushButton("Ver relatório de validação")
        self.import_button.clicked.connect(self.start_import)
        self.cancel_import.clicked.connect(lambda: self.cancel_job("import"))
        self.report_button.clicked.connect(self.show_report)
        for b in (self.import_button, self.cancel_import):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.report_button)
        layout.addLayout(buttons)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.import_log = QTextBrowser()
        layout.addWidget(self.import_log, 1)

    def _path_row(self, field, action, texto):
        widget = QWidget()
        linha = QHBoxLayout(widget)
        linha.setContentsMargins(0, 0, 0, 0)
        botao = QPushButton(texto)
        botao.clicked.connect(action)
        linha.addWidget(field, 1)
        linha.addWidget(botao)
        return widget

    @staticmethod
    def _number_box(minimum, maximum, value, decimals, suffix, step):
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setValue(value)
        box.setSuffix(suffix)
        box.setSingleStep(step)
        box.setMinimumWidth(96)
        return box

    def _pasta_inicial(self, field):
        texto = field.text().strip()
        return str(Path(texto).parent) if texto else str(Path.home())

    def choose_bdgd(self):
        folder = QFileDialog.getExistingDirectory(self, "Pasta .gdb da BDGD", self._pasta_inicial(self.source))
        if folder:
            self.source.setText(folder)
            self.sugerir_ano()

    def choose_nasa(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSV horário da NASA POWER", self._pasta_inicial(self.nasa_field),
                                              "CSV (*.csv);;Todos (*)")
        if path:
            self.nasa_field.setText(path)

    def choose_aneel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Base técnica de GD fotovoltaica da ANEEL",
                                              self._pasta_inicial(self.aneel_field),
                                              "Dados (*.csv *.zip *.parquet);;Todos (*)")
        if path:
            self.aneel_field.setText(path)

    def sugerir_ano(self):
        ano = ano_sugerido(self.source.text().strip())
        if ano:
            self.year.setValue(ano)

    def solar_changed(self):
        if getattr(self, "_carregando_projeto", False):
            return
        try:
            self.service.atualizar_parametros_solares(self.pr_field.value(), self.cutin_field.value(),
                                                     self.ratio_field.value())
        except OSError as exc:
            self.notice.setText(f"Não foi possível salvar os parâmetros solares: {exc}")
            return
        self.correcao.definir_parametros(self.service.parametros_solares())
        self.notice.setText("Parâmetros da simulação solar atualizados para todos os modos.")

    def _import_busy(self, ocupado):
        for widget in (self.import_button, self.source, self.nasa_field, self.aneel_field, self.dist, self.year):
            widget.setEnabled(not ocupado)
        self.cancel_import.setEnabled(ocupado)

    def start_import(self):
        bdgd, nasa = self.source.text().strip(), self.nasa_field.text().strip()
        if not bdgd or not nasa or not self.dist.text().strip():
            self.notice.setText("Informe a pasta .gdb da BDGD, o CSV da NASA e a distribuidora.")
            return
        if "import" in self.jobs:
            return
        self._import_busy(True)
        self.progress_bar.setRange(0, 0)
        self.import_log.setPlainText("Importando os dados…")
        aneel, dist, year = self.aneel_field.text().strip(), self.dist.text().strip(), self.year.value()
        parametros = {"performance_ratio": self.pr_field.value(), "cut_in": self.cutin_field.value(),
                      "razao_kw_kva": self.ratio_field.value()}
        self.launch("import", lambda token, progress: self.service.importar_projeto(
            bdgd, nasa, aneel, dist, year, token, progress, parametros), self.import_done)

    def show_progress(self, name, current, total):
        self.notice.setText(f"{name} · {current:,}" + (f" / {total:,}" if total else ""))
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.cancel_import.setEnabled(name not in ("Promovendo versão", "Importação concluída"))

    def import_done(self, resultado):
        self.aplicar_projeto()
        manifest = resultado["manifest"]
        linhas = ["Importação concluída. Todas as telas estão liberadas.", ""]
        linhas += self._resumo_projeto()
        linhas += ["", "Registros importados:"]
        linhas += [f"  {entidade.upper()}: {quantidade:,}".replace(",", ".")
                   for entidade, quantidade in manifest["counts_output"].items()]
        if resultado["removidas"]:
            linhas += ["", f"Importação anterior apagada do disco ({len(resultado['removidas'])})."]
        if resultado["avisos"]:
            linhas += ["", "Avisos:", *[f"  • {aviso}" for aviso in resultado["avisos"]]]
        self.import_log.setPlainText("\n".join(linhas))
        self.notice.setText("Dados importados. Escolha a região em ‘Regiões’.")

    def cancel_job(self, key):
        if key in self.jobs:
            self.jobs[key].token.cancelar()
            self.notice.setText("Cancelamento solicitado. Aguardando fechamento da operação…")

    def show_report(self):
        if not self.service.base_carregada:
            self.notice.setText("Importe os dados primeiro.")
            return
        self.launch("report", lambda *_: self.service.report(self.service.import_id), self.import_log.setPlainText)

    def _resumo_projeto(self):
        p = self.service.projeto
        nasa = p.nasa or "não informado"
        aneel = p.aneel_fonte or p.aneel or "não informada — GDs pelo POT_INST das UGs"
        return [f"BDGD: {p.bdgd}", f"Irradiância NASA: {nasa}", f"ANEEL: {aneel}",
                f"Distribuidora: {p.distribuidora} · Ano-base: {p.ano}",
                f"Importado em: {p.importado_em[:19].replace('T', ' ')} (UTC)" if p.importado_em else ""]

    def aplicar_projeto(self):
        """Carrega a base atual em todas as telas (na abertura e depois de importar)."""
        p = self.service.projeto
        self._carregando_projeto = True
        try:
            if p.bdgd:
                self.source.setText(p.bdgd)
            nasa_original = p.nasa if p.nasa and Path(p.nasa).is_file() else ""
            self.nasa_field.setText(self.nasa_field.text() or nasa_original)
            self.aneel_field.setText(p.aneel if p.aneel and Path(p.aneel).is_file() else self.aneel_field.text())
            self.dist.setText(p.distribuidora)
            self.year.setValue(p.ano)
            self.pr_field.setValue(p.performance_ratio)
            self.cutin_field.setValue(p.cut_in)
            self.ratio_field.setValue(p.razao_kw_kva)
        finally:
            self._carregando_projeto = False
        for key in list(self.jobs):
            if key != "import":
                self.jobs.pop(key).token.cancelar()
        self.import_id = self.service.import_id
        self.filters = FiltrosUC()
        self._regioes_prontas = False
        for widget in (self.search, self.gd_filter, self.status_filter, self.class_filter, self.group_filter):
            widget.blockSignals(True)
            if isinstance(widget, QLineEdit):
                widget.clear()
            else:
                widget.setCurrentIndex(0)
            widget.blockSignals(False)
        for box in (self.municipio, self.subestacao, self.alimentador):
            self.fill_choices(box, [], "")
        self.model.set_rows([])
        self.total = 0
        self.network_map.clear()
        self.map_table.setRowCount(0)
        self.correcao.definir_projeto()
        base = self.service.base_carregada
        self.set_pages_enabled(base)
        self.report_button.setEnabled(base)
        if not base:
            self.import_log.setPlainText("Nenhuma base carregada.\n\nEscolha a pasta .gdb da BDGD, confira o CSV da NASA "
                                         "e a base da ANEEL e clique em Importar dados.\n\n"
                                         "Feriados municipais sem cobertura documentada serão indicados como calendário parcial.")
            self.page_info.setText("Nenhuma base importada")
            self.setWindowTitle(APP_NOME)
            self.notice.setText("Importe os dados para começar.")
            return
        self.setWindowTitle(f"{APP_NOME} · {Path(p.bdgd).name or p.distribuidora} · {p.ano}")
        if "import" not in self.jobs:
            self.import_log.setPlainText("\n".join(["Base carregada.", "", *self._resumo_projeto()] +
                                                   (["", "Aviso: " + p.aneel_aviso] if p.aneel_aviso else [])))
        self.notice.setText("Carregando municípios, subestações e alimentadores…")
        self.launch("regions_index", lambda token, _: self.service.indice_regioes(token), self._indice_pronto)

    def _indice_pronto(self, _indice):
        self._regioes_prontas = True
        self.refresh_region_boxes()
        self.aplicar_regiao()
        self.launch("classes", lambda token, _: self.service.regions(self.import_id, FiltrosUC(), token), self.set_classes)

    def set_classes(self, result):
        self.fill_choices(self.class_filter, result["classes"], self.filters.classe)
        self.fill_choices(self.group_filter, result["grupos"], self.filters.grupo_tensao)

    # ------------------------------------------------------------ regioes
    def _regions_page(self):
        layout = self.page_layout("Regiões")
        ajuda = QLabel("Escolha o município, a subestação e o alimentador. A seleção vale para o mapa da rede, "
                       "as unidades consumidoras e a correção de demanda.\nAs subestações são as do município "
                       "(onde está a maior parte das suas UCs); os alimentadores são os ativos da subestação.")
        ajuda.setObjectName("ajuda")
        ajuda.setWordWrap(True)
        layout.addWidget(ajuda)
        form = QFormLayout()
        self.municipio, self.subestacao, self.alimentador = QComboBox(), QComboBox(), QComboBox()
        for label, box in (("Município", self.municipio), ("Subestação", self.subestacao), ("Alimentador", self.alimentador)):
            box.addItem("Todos", "")
            box.setMinimumWidth(420)
            box.setMaxVisibleItems(25)
            form.addRow(label, box)
        layout.addLayout(form)
        self.municipio.currentIndexChanged.connect(lambda: self.region_changed("municipio"))
        self.subestacao.currentIndexChanged.connect(lambda: self.region_changed("subestacao"))
        self.alimentador.currentIndexChanged.connect(lambda: self.region_changed("alimentador"))
        atalhos = QHBoxLayout()
        for texto, pagina in (("Ver unidades consumidoras", PAGINA_UCS), ("Abrir no mapa da rede", PAGINA_MAPA),
                              ("Correção de demanda", PAGINA_CORRECAO)):
            botao = QPushButton(texto)
            botao.clicked.connect(lambda _=False, destino=pagina: self.navigation.setCurrentRow(destino))
            atalhos.addWidget(botao)
        atalhos.addStretch()
        layout.addLayout(atalhos)
        self.summary_view = QTextBrowser()
        layout.addWidget(self.summary_view, 1)

    def fill_choices(self, box, values, selected=""):
        box.blockSignals(True)
        box.clear()
        label = ("Todas as classes" if box is self.class_filter else "Todos os grupos" if box is self.group_filter
                 else "Todas" if box is self.subestacao else "Todos")
        box.addItem(label, "")
        for value in values:
            display, code = value if isinstance(value, tuple) else (value, value)
            box.addItem(display, code)
        index = box.findData(selected)
        box.setCurrentIndex(max(0, index))
        box.blockSignals(False)

    def refresh_region_boxes(self):
        """Refaz as tres listas a partir do indice (instantaneo, sem consultar a base)."""
        if not self._regioes_prontas:
            return
        f = self.filters
        municipios = self.service.opcoes_regiao("municipio")
        subs = self.service.opcoes_regiao("subestacao", FiltrosUC(municipio=f.municipio))
        alimentadores = self.service.opcoes_regiao("alimentador", FiltrosUC(municipio=f.municipio, subestacao=f.subestacao))
        for kind, valores in (("municipio", municipios), ("subestacao", subs), ("alimentador", alimentadores)):
            self._rotulos[kind] = {codigo: rotulo for rotulo, codigo in valores}
        self.fill_choices(self.municipio, municipios, f.municipio)
        self.fill_choices(self.subestacao, subs, f.subestacao)
        self.fill_choices(self.alimentador, alimentadores, f.alimentador)

    def region_changed(self, name):
        if not self.import_id or not self._regioes_prontas:
            return
        changes = {name: getattr(self, name).currentData() or "", "pagina": 0}
        if name == "municipio":
            changes.update(subestacao="", alimentador="")
        elif name == "subestacao":
            changes.update(alimentador="")
        self.filters = replace(self.filters, **changes)
        self.refresh_region_boxes()
        self.aplicar_regiao()

    def rotulo_regiao(self, kind, codigo):
        return self._rotulos[kind].get(codigo, codigo) if codigo else ""

    def descricao_regiao(self):
        f = self.filters
        partes = [f"Município: {self.rotulo_regiao('municipio', f.municipio) or 'Todos'}",
                  f"Subestação: {self.rotulo_regiao('subestacao', f.subestacao) or 'Todas'}",
                  f"Alimentador: {self.rotulo_regiao('alimentador', f.alimentador) or 'Todos'}"]
        return " · ".join(partes)

    def aplicar_regiao(self):
        """Leva a regiao escolhida para as outras telas."""
        self.selection_label.setText(self.descricao_regiao())
        self.request_page()
        self.request_summary()
        self.refresh_map_boxes()
        f = self.filters
        alimentadores = self.service.opcoes_regiao(
            "alimentador", FiltrosUC(municipio=f.municipio, subestacao=f.subestacao))
        self.correcao.definir_regiao(self.descricao_regiao(), alimentadores, f.alimentador)

    # ------------------------------------------------------------ mapa
    def _map_page(self):
        layout = self.page_layout("Mapa da rede")
        help_label = QLabel("Mostra os trechos de média e baixa tensão da subestação escolhida em Regiões, lidos da BDGD "
                            "importada. Escolha um alimentador para destacar só o seu percurso. "
                            "A roda amplia; arraste para deslocar; dê duplo clique para enquadrar tudo.")
        help_label.setObjectName("ajuda")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Subestação"))
        self.map_sub = QComboBox()
        self.map_sub.setMinimumWidth(250)
        self.map_sub.currentIndexChanged.connect(self.map_sub_changed)
        controls.addWidget(self.map_sub, 2)
        controls.addWidget(QLabel("Alimentador"))
        self.map_feeder = QComboBox()
        self.map_feeder.currentIndexChanged.connect(self.map_selection_changed)
        controls.addWidget(self.map_feeder, 1)
        self.map_mt = QCheckBox("Média tensão")
        self.map_bt = QCheckBox("Baixa tensão")
        self.map_trafos = QCheckBox("Transformadores")
        for caixa in (self.map_mt, self.map_bt, self.map_trafos):
            caixa.setChecked(True)
            caixa.toggled.connect(self.set_map_layers)
            controls.addWidget(caixa)
        layout.addLayout(controls)
        actions = QHBoxLayout()
        draw = QPushButton("Desenhar rede")
        draw.setObjectName("primario")
        draw.clicked.connect(self.request_network_map)
        actions.addWidget(draw)
        fit = QPushButton("Enquadrar tudo")
        fit.clicked.connect(lambda: self.network_map.fit_all())
        actions.addWidget(fit)
        actions.addStretch()
        layout.addLayout(actions)
        body = QHBoxLayout()
        self.network_map = NetworkMap()
        body.addWidget(self.network_map, 3)
        self.map_table = QTableWidget(0, 7)
        self.map_table.setHorizontalHeaderLabels(["Alimentador", "Nome", "Trechos MT", "MT (km)", "Trechos BT", "BT (km)", "Trafos"])
        self.map_table.horizontalHeader().setStretchLastSection(True)
        self.map_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.map_table.cellDoubleClicked.connect(self.map_table_selected)
        body.addWidget(self.map_table, 2)
        layout.addLayout(body, 1)
        self.map_info = QLabel("Escolha a subestação em Regiões ou aqui e clique em Desenhar rede.")
        self.map_info.setWordWrap(True)
        layout.addWidget(self.map_info)

    def refresh_map_boxes(self):
        f = self.filters
        subs = self.service.opcoes_regiao("subestacao", FiltrosUC(municipio=f.municipio))
        self.map_sub.blockSignals(True)
        self.map_sub.clear()
        for rotulo, codigo in subs:
            self.map_sub.addItem(rotulo, codigo)
        indice = self.map_sub.findData(f.subestacao)
        self.map_sub.setCurrentIndex(indice if indice >= 0 else (0 if subs else -1))
        self.map_sub.blockSignals(False)
        self.map_sub_changed()

    def map_sub_changed(self):
        self.map_selection_changed()
        code = self.map_sub.currentData() or ""
        self.map_feeder.blockSignals(True)
        self.map_feeder.clear()
        self.map_feeder.addItem("Todos os alimentadores", "")
        if code and self._regioes_prontas:
            for rotulo, feeder in self.service.opcoes_regiao("alimentador", FiltrosUC(subestacao=code)):
                self.map_feeder.addItem(rotulo, feeder)
        index = self.map_feeder.findData(self.filters.alimentador)
        self.map_feeder.setCurrentIndex(index if index >= 0 else 0)
        self.map_feeder.blockSignals(False)

    def map_selection_changed(self):
        if "network_map" in self.jobs:
            self.jobs.pop("network_map").token.cancelar()
        self.network_map.clear()
        self.map_table.setRowCount(0)
        self.map_info.setText("Clique em Desenhar rede para aplicar a seleção.")

    def request_network_map(self):
        path, sub, feeder = self.service.map_source(), self.map_sub.currentData(), self.map_feeder.currentData() or ""
        if not path or not Path(path).is_dir() or Path(path).suffix.lower() != ".gdb":
            self.map_info.setText("O mapa precisa da pasta .gdb da BDGD importada. Confira o caminho em ‘Dados de entrada’.")
            return
        if not sub:
            self.map_info.setText("Selecione uma subestação.")
            return
        self.map_info.setText("Lendo os trechos da rede…")
        self.launch("network_map", lambda token, _: self.service.network_map(path, sub, feeder, token), self.set_network_map)

    def set_network_map(self, result):
        self.network_map.set_data(result)
        nomes = self.service.indice_regioes()["nomes_alimentadores"]
        rows = result["alimentadores"]
        self.map_table.setRowCount(len(rows))
        for index, (code, values) in enumerate(rows.items()):
            fields = (code, nomes.get(code, ""), str(values["mt"]), formatar_numero(values["mt_km"], 2),
                      str(values["bt"]), formatar_numero(values["bt_km"], 2), str(values["trafos"]))
            for column, value in enumerate(fields):
                self.map_table.setItem(index, column, QTableWidgetItem(value))
        mt = sum(v["mt"] for v in rows.values())
        bt = sum(v["bt"] for v in rows.values())
        trafos = sum(v["trafos"] for v in rows.values())
        sub_label = self.map_sub.currentText() or result["subestacao"]
        self.map_info.setText(f"Subestação {sub_label} · {len(rows)} alimentadores · "
                              f"{formatar_numero(mt, 0)} trechos MT · {formatar_numero(bt, 0)} trechos BT · "
                              f"{formatar_numero(trafos, 0)} transformadores")

    def map_table_selected(self, row, column):
        code = self.map_table.item(row, 0).text()
        index = self.map_feeder.findData(code)
        if index >= 0:
            self.map_feeder.setCurrentIndex(index)
            self.request_network_map()

    def set_map_layers(self):
        self.network_map.show_mt = self.map_mt.isChecked()
        self.network_map.show_bt = self.map_bt.isChecked()
        self.network_map.show_trafos = self.map_trafos.isChecked()
        self.network_map.update()

    # ------------------------------------------------------------ unidades consumidoras
    def _uc_page(self):
        layout = self.page_layout("Unidades consumidoras")
        self.selection_label = QLabel("Município: Todos · Subestação: Todas · Alimentador: Todos")
        self.selection_label.setObjectName("ajuda")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar identificador da UC…")
        row.addWidget(self.search, 2)
        self.class_filter, self.group_filter, self.gd_filter, self.status_filter = [QComboBox() for _ in range(4)]
        for combo, name in ((self.class_filter, "Classes"), (self.group_filter, "Grupos"), (self.gd_filter, "GD"), (self.status_filter, "Status")):
            combo.addItem(f"Todos · {name}", "")
            row.addWidget(combo)
            combo.currentIndexChanged.connect(self.table_filter_changed)
        self.gd_filter.addItem("Com CEG GD", "sim")
        self.gd_filter.addItem("Sem CEG GD", "nao")
        for code in ("CURVAVEL", "NAO_CURVAVEL_TIPOLOGIA", "NAO_CURVAVEL_ENERGIA", "CALENDARIO_PARCIAL", "REFERENCIA_ORFA"):
            self.status_filter.addItem(STATUS_LABELS[code], code)
        layout.addLayout(row)
        self.model = UCTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setMinimumSectionSize(70)
        for column, width in enumerate((176, 122, 108, 72, 68, 152, 90, 200)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_changed)
        self.table.doubleClicked.connect(self.show_detail)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.previous, self.next = QPushButton("← Anterior"), QPushButton("Próxima →")
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.page_info = QLabel("Nenhuma base importada")
        cancel = QPushButton("Cancelar consulta")
        cancel.clicked.connect(lambda: self.cancel_job("page"))
        row.addWidget(self.previous)
        row.addWidget(self.page_info, 1)
        row.addWidget(self.next)
        row.addWidget(cancel)
        layout.addLayout(row)
        actions = QHBoxLayout()
        detail = QPushButton("Ver 12 meses e total da UC")
        detail.clicked.connect(self.show_selected_detail)
        self.uc_curve_button = QPushButton("Gerar curvas da UC selecionada")
        self.uc_curve_button.setObjectName("primario")
        self.uc_curve_button.clicked.connect(self.start_uc_curve)
        actions.addWidget(detail)
        actions.addWidget(self.uc_curve_button)
        actions.addStretch()
        layout.addLayout(actions)
        nota = QLabel("Só UCs ativas (SIT_ATIV = AT), como na correção de demanda. Selecione uma linha para ver os "
                      "12 meses ou gerar carga, GD e curva líquida.")
        nota.setObjectName("ajuda")
        nota.setWordWrap(True)
        layout.addWidget(nota)

    def table_filter_changed(self):
        if not hasattr(self, "status_filter"):
            return
        self.filters = replace(self.filters, classe=self.class_filter.currentData() or "", grupo_tensao=self.group_filter.currentData() or "", codigo_gd=self.gd_filter.currentData() or "", status=self.status_filter.currentData() or "", pagina=0)
        if hasattr(self, "model"):
            self.request_page()

    def search_changed(self):
        self.filters = replace(self.filters, busca=self.search.text(), pagina=0)
        if "page" in self.jobs:
            self.jobs["page"].token.cancelar()
        self.debounce.start()

    def sort_changed(self, column):
        field = COLUMNS[column][0]
        if field in SORTABLE:
            descending = not self.filters.descendente if self.filters.ordenar == field else False
            self.filters = replace(self.filters, ordenar=field, descendente=descending, pagina=0)
            self.table.horizontalHeader().setSortIndicator(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
            self.table.horizontalHeader().setSortIndicatorShown(True)
            self.request_page()

    def change_page(self, step):
        page = self.filters.pagina + step
        if page >= 0 and page * 200 < self.total:
            self.filters = replace(self.filters, pagina=page)
            self.request_page()

    def request_page(self):
        if not self.import_id:
            return
        import_id, filters = self.import_id, self.filters
        self.notice.setText("Consultando UCs…")
        self.launch("page", lambda token, _: self.service.page(import_id, filters, token), self.set_page)

    def set_page(self, result):
        self.model.set_rows(result["linhas"])
        self.total = result["total"]
        self.page_info.setText(f"Página {result['pagina'] + 1} · {self.total:,} UCs · até 200 registros por página".replace(",", "."))
        self.previous.setEnabled(result["pagina"] > 0)
        self.next.setEnabled((result["pagina"] + 1) * 200 < self.total)
        self.notice.setText("Consulta concluída." if self.total else "Nenhuma UC corresponde aos filtros selecionados.")

    def request_summary(self):
        if not self.import_id:
            return
        import_id = self.import_id
        filters = FiltrosUC(municipio=self.filters.municipio, subestacao=self.filters.subestacao, alimentador=self.filters.alimentador)
        self.summary_view.setPlainText("Calculando o resumo da região…")
        self.launch("summary", lambda token, _: self.service.summary(import_id, filters, token), self.set_summary)

    def set_summary(self, summary):
        energia = summary["energia_anual_informada_kwh"]
        text = [self.descricao_regiao(), "",
                f"Unidades consumidoras ativas: {summary['total_ucs']:,}".replace(",", "."),
                f"Com CEG GD preenchido: {summary['com_gd']:,}".replace(",", "."),
                f"Energia anual informada: {formatar_numero(energia, 0) if energia is not None else '-'} kWh",
                f"UCs com energia incompleta ou inválida: {summary['ucs_energia_incompleta_ou_invalida']:,}".replace(",", "."),
                "", "UCs por classe:"]
        text += [f"  {k or 'Ausente'}: {v:,}".replace(",", ".") for k, v in summary["classes"].items()]
        text += ["", "Curvabilidade (uma UC pode ter mais de um motivo):"]
        text += [f"  {STATUS_LABELS.get(k, k)}: {v:,}".replace(",", ".") for k, v in summary["status"].items()]
        self.summary_view.setPlainText("\n".join(text))

    def selected_uc(self):
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            self.notice.setText("Selecione uma UC na tabela.")
            return None
        return self.model.rows[indexes[0].row()]

    def show_selected_detail(self):
        row = self.selected_uc()
        if row:
            import_id = self.import_id
            self.launch("detail", lambda token, _: self.service.detail(import_id, row["entidade"], row["linha_origem"], token), self.detail_dialog)

    def start_uc_curve(self):
        row = self.selected_uc()
        if not row:
            return
        self._start_uc_curve(row)

    def _start_uc_curve(self, row):
        dialog = CurveParametersDialog(self, tem_gd=bool(str(row.get("codigo_gd") or "").strip()),
                                       codigo_gd=row.get("codigo_gd"), workspace=self.service.workspace,
                                       parametros_salvos=self.service.parametros_uc(self.import_id, row["entidade"], row["linha_origem"]),
                                       parametros_solares=self.service.parametros_solares())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        parametros = dialog.values()
        import_id = self.import_id
        self.notice.setText("Calculando as curvas da UC…")
        self.launch(
            "curve_uc",
            lambda token, _: self.service.curva_uc(
                import_id,
                row["entidade"],
                row["linha_origem"],
                parametros.pop("mes"),
                token,
                **parametros,
            ),
            self.curve_dialog,
        )

    def curve_dialog(self, result):
        self.notice.setText("Curvas da UC calculadas.")
        dialog = CurveResultDialog(result, self)
        dialog.exec()

    def show_detail(self, index):
        if index.isValid():
            row = self.model.rows[index.row()]
            import_id = self.import_id
            self.launch("detail", lambda token, _: self.service.detail(import_id, row["entidade"], row["linha_origem"], token), self.detail_dialog)

    def detail_dialog(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle("Dados mensais da UC")
        dialog.resize(780, 700)
        dialog.setStyleSheet(theme.QSS)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel(f"UC: {row.get('id_uc') or '-'}   ·   CEG GD: {row.get('codigo_gd') or 'sem GD'}"))
        table = QTableWidget(13, 2)
        table.setHorizontalHeaderLabels(["Período", "Energia (kWh)"])
        total = 0.0
        validos = 0
        for mes in range(1, 13):
            valor = row.get(f"energia_mes_{mes:02d}")
            valido = isinstance(valor, (int, float)) and valor >= 0
            table.setItem(mes - 1, 0, QTableWidgetItem(f"{mes:02d}"))
            table.setItem(mes - 1, 1, QTableWidgetItem(formatar_numero(valor) if valido else "ausente ou inválida"))
            if valido:
                total += valor
                validos += 1
        table.setItem(12, 0, QTableWidgetItem("TOTAL DOS MESES INFORMADOS"))
        table.setItem(12, 1, QTableWidgetItem(formatar_numero(total)))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(table)
        text = QTextBrowser()
        extras = {k: v for k, v in row.items() if not k.startswith("energia_mes_")}
        text.setPlainText(f"Meses válidos: {validos}/12\n\n" + json.dumps(extras, ensure_ascii=False, indent=2, default=str))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        if self.correcao.ocupada():
            self.notice.setText("Aguarde o cálculo da correção de demanda terminar antes de fechar.")
            event.ignore()
            return
        if self.workers:
            self.closing = True
            for worker in self.workers:
                worker.token.cancelar()
            self.notice.setText("Fechando operações em andamento…")
            event.ignore()
        else:
            self.correcao.fechar()
            event.accept()


MESES = (
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
)


# Nome historico mantido para compatibilidade com scripts e testes antigos.
ZoomChartView = CurvaChartView


class CurveResultDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = result
        self.setWindowTitle(result["titulo"])
        self.resize(1300, 900)
        self.setMinimumSize(980, 680)
        self.setStyleSheet(theme.QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(11)
        title = QLabel(result["titulo"])
        title.setObjectName("tituloPagina")
        layout.addWidget(title)
        self.chart_tabs = QTabWidget()
        self.chart_tabs.setUsesScrollButtons(True)
        self.chart_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.chart_tabs.tabBar().setExpanding(False)
        self.chart_views = []
        self._cores_curvas = {nome: theme.CORES_SERIES[i % len(theme.CORES_SERIES)]
                              for i, nome in enumerate(result["series"])}
        if result.get("irradiancia"):
            self._cores_curvas.update({nome: theme.CORES_SERIES[i % len(theme.CORES_SERIES)]
                                      for i, nome in enumerate(result["irradiancia"]["series"])})
        for nome, vetor in result["series"].items():
            view = self._create_chart({nome: vetor}, nome)
            self.chart_tabs.addTab(view, nome.removesuffix(" (kW)"))
            self.chart_views.append(view)
        if result.get("irradiancia"):
            self._add_irradiance_tab()
        if len(result["series"]) > 1:
            view = self._create_chart(result["series"], "Curvas reunidas")
            self.chart_tabs.addTab(self._pagina_com_seletor(view), "Todas juntas")
            self.chart_views.append(view)
        self.typical_panel = None
        if result.get("tipos_dia"):
            self.typical_panel = PainelCurvasTipicas(result)
            self.chart_tabs.addTab(self.typical_panel, "Curvas típicas")
        self._feeder_worker = None
        self._pending_close = None
        self._service = getattr(parent, "service", None)
        if result.get("agregacao_individual"):
            self._add_influences_tab()
        layout.addWidget(self.chart_tabs, 1)
        status_row = QHBoxLayout()
        self.periodo_visivel = QLabel()
        self.periodo_visivel.setObjectName("ajuda")
        self.periodo_visivel.setWordWrap(True)
        status_row.addWidget(self.periodo_visivel, 1)
        self.escala_automatica = QCheckBox("Ajustar altura automaticamente")
        self.escala_automatica.setChecked(True)
        self.escala_automatica.setToolTip(
            "Ajusta o eixo vertical às curvas e ao período visíveis, mantendo o zero. "
            "Desmarque para manter a mesma escala ao navegar."
        )
        self.escala_automatica.toggled.connect(self._alterar_escala)
        status_row.addWidget(self.escala_automatica)
        layout.addLayout(status_row)
        for view in self.chart_views:
            view.visualizacao_alterada.connect(self._atualizar_estado_grafico)
        if self.typical_panel is not None:
            self.typical_panel.grafico_alterado.connect(self._conectar_tipicas)
            self._conectar_tipicas()
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        hint = QLabel(
            "Mouse: ler valores · roda: zoom · arraste: selecionar período · "
            "linha tracejada: referência zero"
        )
        hint.setObjectName("ajuda")
        hint.setWordWrap(True)
        hint.setToolTip("Ctrl+roda: zoom vertical · Shift+arraste ou botão do meio: deslocar · "
                        "setas: navegar · +/−: zoom · duplo clique ou 0: restaurar")
        zoom_row.addWidget(hint, 1)
        anterior = QPushButton()
        anterior.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowLeft))
        anterior.setToolTip("Deslocar para trás no tempo (seta esquerda)")
        anterior.setAccessibleName("Período anterior")
        seguinte = QPushButton()
        seguinte.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        seguinte.setToolTip("Deslocar para frente no tempo (seta direita)")
        seguinte.setAccessibleName("Próximo período")
        zoom_in = QPushButton("Zoom +")
        zoom_out = QPushButton("Zoom −")
        zoom_reset = QPushButton("Restaurar")
        zoom_reset.setObjectName("primario")
        for botao in (anterior, seguinte):
            botao.setFixedWidth(38)
        anterior.clicked.connect(lambda: self._navegar("deslocar", -0.25))
        seguinte.clicked.connect(lambda: self._navegar("deslocar", 0.25))
        zoom_in.clicked.connect(lambda: self._navegar("aplicar_zoom", 1.4))
        zoom_out.clicked.connect(lambda: self._navegar("aplicar_zoom", 1 / 1.4))
        zoom_reset.clicked.connect(lambda: self._navegar("restaurar"))
        for botao in (anterior, seguinte, zoom_out, zoom_in, zoom_reset):
            zoom_row.addWidget(botao)
        layout.addLayout(zoom_row)

        self.summary = QTextBrowser()
        self.summary.setMinimumHeight(108)
        self.summary.setMaximumHeight(210)
        self.summary.setPlainText(self._summary())
        layout.addWidget(self.summary)
        # Na aba de curvas tipicas o resumo do periodo da lugar ao quadro
        # comparativo do proprio painel, liberando altura para o grafico.
        self.chart_tabs.currentChanged.connect(self._aba_mudou)
        self._aba_mudou()
        row = QHBoxLayout()
        export = QPushButton("Exportar curvas em CSV…")
        export.setObjectName("primario")
        export.clicked.connect(self.export_csv)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        close.rejected.connect(self.reject)
        row.addWidget(export)
        if result.get("agregacao_individual"):
            self.export_xlsx_button = QPushButton("Exportar Excel · modelo das UCs…")
            self.export_xlsx_button.setEnabled(self._service is not None)
            self.export_xlsx_button.clicked.connect(self.export_feeder_xlsx)
            row.addWidget(self.export_xlsx_button)
        if result.get("irradiancia"):
            export_solar = QPushButton("Exportar irradiância da UC em CSV…")
            export_solar.clicked.connect(self.export_irradiance_csv)
            row.addWidget(export_solar)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)

    def _add_influences_tab(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        info = QLabel("Todas as UCs de BT e MT do alimentador. A contribuição é medida no mesmo instante do pico do circuito. "
                      "Selecione uma UC para comparar o alimentador com e sem ela.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.influence_table = QTableWidget(len(self.result["influencias"]), 8)
        self.influence_table.setHorizontalHeaderLabels(["UC", "BT / MT", "Carga (kWh)", "GD (kWh)", "Líquida (kWh)",
            "No pico (kW)", "Participação no pico", "Cenário ENE"])
        self.influence_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.influence_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.influence_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.influence_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for i, uc in enumerate(self.result["influencias"]):
            participacao = "Indefinida (pico zero)" if uc["participacao_pico"] is None else formatar_numero(uc["participacao_pico"] * 100, 2) + "%"
            valores = [uc["uc_id"], uc["tipo"], *[formatar_numero(uc[k]) for k in
                ("carga_kwh", "geracao_kwh", "liquida_kwh", "contribuicao_pico_kw")], participacao, uc["cenarios"]]
            for j, valor in enumerate(valores):
                self.influence_table.setItem(i, j, QTableWidgetItem(str(valor)))
        layout.addWidget(self.influence_table, 1)
        self.influence_button = QPushButton("Ver influência da UC selecionada")
        self.influence_button.setEnabled(self._service is not None)
        self.influence_button.clicked.connect(self.show_uc_influence)
        layout.addWidget(self.influence_button)
        self.chart_tabs.addTab(pagina, "Influência das UCs")

    def _run_feeder_job(self, action, success):
        if self._feeder_worker is not None:
            return
        self.export_xlsx_button.setEnabled(False)
        self.influence_button.setEnabled(False)
        worker = Worker(action, self)
        self._feeder_worker = worker
        worker.result.connect(lambda value: success(value) if self._pending_close is None else None)
        worker.error.connect(lambda text: self.summary.setPlainText(text) if self._pending_close is None else None)
        worker.progress.connect(lambda etapa, atual, total: self.summary.setPlainText(f"{etapa} · {atual}/{total}"))
        worker.finished.connect(self._feeder_job_finished)
        worker.start()

    def _feeder_job_finished(self):
        worker = self._feeder_worker
        self._feeder_worker = None
        worker.deleteLater()
        self.export_xlsx_button.setEnabled(True)
        self.influence_button.setEnabled(True)
        if self._pending_close is not None:
            super().done(self._pending_close)

    def done(self, result):
        if getattr(self, "_feeder_worker", None) is not None:
            self._pending_close = result
            self._feeder_worker.token.cancelar()
            self.summary.setPlainText("Cancelando a operação antes de fechar…")
            return
        super().done(result)

    def export_feeder_xlsx(self):
        periodo = "jan_dez" if self.result["mes"] == 0 else f"mes_{self.result['mes']:02d}"
        nome = f"Alimentador_{self.result['estado']['alimentador']}_{self.result['ano']}_{periodo}_15min.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar todas as UCs tratadas", nome, "Planilha Excel (*.xlsx)")
        if path:
            self._run_feeder_job(lambda token, progress: self._service.exportar_alimentador(self.result, path, token, progress),
                lambda r: self.summary.setPlainText(f"Excel salvo: {r['path']}\n{r['ucs']:,} UCs · {r['registros_ucs']:,} registros · {r['abas_dados']} aba(s) de dados."))

    def show_uc_influence(self):
        indice = self.influence_table.currentRow()
        if indice < 0:
            self.summary.setPlainText("Selecione uma UC na tabela de influência.")
            return
        self._run_feeder_job(lambda token, _: self._service.influencia_uc(self.result, indice, token), self._show_influence_chart)

    def _show_influence_chart(self, result):
        dialog = QDialog(self)
        dialog.setWindowTitle(result["titulo"])
        dialog.resize(1100, 720)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(result["titulo"]))
        view = construir_grafico(result["series"], self.result["timestamps"], result["titulo"])
        seletor = SeletorDeSeries(view.series_disponiveis())
        seletor.alterado.connect(view.definir_series_visiveis)
        layout.addWidget(seletor)
        layout.addWidget(view, 1)
        layout.addWidget(QLabel("Potência em kW · roda para zoom · comparação no mesmo intervalo de 15 minutos."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        # Show mantém o processamento do sinal finished da consulta independente do diálogo.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _aba_mudou(self, *_):
        self.summary.setVisible(not isinstance(self.chart_tabs.currentWidget(), PainelCurvasTipicas))
        self._atualizar_estado_grafico()

    def _conectar_tipicas(self):
        view = self.typical_panel.view
        if view is not None:
            view.visualizacao_alterada.connect(self._atualizar_estado_grafico)
        self._atualizar_estado_grafico()

    def _atualizar_estado_grafico(self):
        view = self._current_view()
        if view is not None:
            self.periodo_visivel.setText(view.descricao_janela())
            self.escala_automatica.blockSignals(True)
            self.escala_automatica.setChecked(view._y_automatico)
            self.escala_automatica.blockSignals(False)

    def _alterar_escala(self, ativa):
        view = self._current_view()
        if view is not None:
            view.definir_escala_automatica(ativa)

    def _pagina_com_seletor(self, view):
        """Envolve o grafico numa pagina com as caixas de selecao das curvas."""
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(12, 10, 12, 6)
        layout.setSpacing(8)
        seletor = SeletorDeSeries(view.series_disponiveis())
        seletor.alterado.connect(view.definir_series_visiveis)
        layout.addWidget(seletor)
        layout.addWidget(view, 1)
        return pagina

    def _current_view(self):
        widget = self.chart_tabs.currentWidget()
        if isinstance(widget, PainelCurvasTipicas):
            return widget.view
        indice = self.chart_tabs.currentIndex()
        return self.chart_views[indice] if 0 <= indice < len(self.chart_views) else None

    def _current_chart(self):
        view = self._current_view()
        return view.chart() if view is not None else None

    def _navegar(self, metodo, *args):
        view = self._current_view()
        if view is not None:
            getattr(view, metodo)(*args)
            view.setFocus()

    def _add_irradiance_tab(self):
        solar = self.result["irradiancia"]
        page = QWidget()
        layout = QVBoxLayout(page)
        coordenadas = (f"{solar['latitude']:.4f}, {solar['longitude']:.4f}"
                       if solar.get("latitude") is not None and solar.get("longitude") is not None else "não informadas")
        info = QLabel(
            f"UC: {self.result['uc']['id_uc']} · CEG GD: {self.result['codigo_gd']}\n"
            f"Fonte: {solar['arquivo']}\n"
            f"Coordenadas do arquivo NASA: {coordenadas}. "
            "Irradiância horizontal da NASA (ALLSKY_SFC_SW_DWN), reamostrada de 1 h para 15 min por PCHIP, "
            "conservando a energia de cada hora. É a irradiância que entra no modelo PVSystem vetorizado."
        )
        info.setObjectName("ajuda")
        info.setWordWrap(True)
        layout.addWidget(info)
        display_tabs = QTabWidget()
        display_tabs.setUsesScrollButtons(False)
        view = self._create_chart(solar["series"], "Irradiância solar da UC", "Irradiância (W/m²)")
        display_tabs.addTab(view, "Gráfico")
        timestamps = self.result["timestamps"]
        table = QTableWidget(len(timestamps), 1 + len(solar["series"]))
        table.setHorizontalHeaderLabels(["Data e hora (com fuso)", *solar["series"]])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for index, timestamp in enumerate(timestamps):
            table.setItem(index, 0, QTableWidgetItem(timestamp.isoformat(sep=" ")))
            for column, values in enumerate(solar["series"].values(), 1):
                table.setItem(index, column, QTableWidgetItem(formatar_numero(float(values[index]), 3)))
        display_tabs.addTab(table, "Dados por intervalo")
        layout.addWidget(display_tabs, 1)
        self.chart_tabs.addTab(page, "Irradiância solar")
        self.chart_views.append(view)

    def _create_chart(self, series_map, title, y_title="Potência (kW)"):
        cores = [self._cores_curvas[nome] for nome in series_map]
        return construir_grafico(series_map, self.result["timestamps"], title, y_title, cores=cores)

    def _summary(self):
        if self.result.get("agregacao_individual"):
            s = self.result["estado"]
            return (f"Subestação {s['subestacao']} · Alimentador {s['alimentador']} · {s['bt']} UCs BT + {s['mt']} UCs MT\n"
                    f"Todas as {s['total']} UCs tratadas no período. Pico coincidente: {formatar_numero(self.result['pico_kw'])} kW "
                    f"em {self.result['timestamps'][self.result['pico_indice']]:%d/%m/%Y %H:%M}.\n"
                    "Compare BT, MT, carga bruta e geração nas abas; consulte cada UC em Influência das UCs.\n" +
                    "\n".join(self.result["hipoteses"]))
        lines = []
        if self.result.get("resultados_mensais"):
            lines.extend(self._annual_summary())
        elif self.result["tipo"] == "uc":
            balance = self.result["balanco"]
            lines.extend([
                f"CEG GD: {self.result['codigo_gd'] or 'sem GD'}",
                f"Energia importada reconstruída: {formatar_numero(balance.energia_importada_kwh, 6)} kWh",
                f"Energia gerada: {formatar_numero(balance.energia_gerada_kwh, 6)} kWh",
                f"Energia exportada estimada: {formatar_numero(balance.energia_exportada_kwh, 6)} kWh",
                f"Energia da carga sem GD: {formatar_numero(balance.energia_carga_kwh, 6)} kWh",
                f"Balanço: E_carga = E_importada + E_gerada - E_exportada · resíduo {balance.residuo_balanco_kwh:.3e} kWh",
                f"Demanda máxima do mês (DEM_MAX): {formatar_numero(balance.demanda_kw, 6)} kW",
                *self._linhas_gd(),
                "",
                "Energia mensal informada da UC:",
            ])
            for mes, valor in enumerate(self.result["energias_mensais_kwh"], 1):
                valido = isinstance(valor, (int, float)) and valor >= 0
                lines.append(f"  {mes:02d}: {formatar_numero(valor) + ' kWh' if valido else 'ausente ou inválida'}")
            lines.append(f"  TOTAL: {formatar_numero(self.result['energia_anual_kwh'])} kWh")
        else:
            lines.extend([
                f"UCs incluídas: {self.result['unidades_incluidas']:,}",
                f"Energia mensal incluída: {formatar_numero(self.result['energia_incluida_kwh'])} kWh",
                f"Energia reconstruída pela curva: {formatar_numero(self.result['energia_reconstituida_kwh'])} kWh",
                f"UCs omitidas por energia inválida no mês: {self.result['unidades_energia_invalida']:,}",
                f"Grupos omitidos por tipologia/município inválido: {len(self.result['grupos_omitidos'])}",
            ])
        if self.result.get("hipoteses"):
            lines.extend(["", "Hipóteses:", *[f"  • {item}" for item in self.result["hipoteses"]]])
        return "\n".join(lines)

    def _annual_summary(self):
        meses = self.result["resultados_mensais"]
        lines = ["Período: janeiro a dezembro · 12 meses completos"]
        if self.result["tipo"] == "uc":
            lines.append(f"CEG GD: {self.result['codigo_gd'] or 'sem GD'}")
            for label, campo in (
                ("Energia importada", "energia_importada_kwh"),
                ("Energia gerada", "energia_gerada_kwh"),
                ("Energia exportada", "energia_exportada_kwh"),
                ("Energia da carga sem GD", "energia_carga_kwh"),
            ):
                total = sum(getattr(parte["balanco"], campo) for parte in meses)
                lines.append(f"{label} no ano: {formatar_numero(total, 6)} kWh")
            lines.extend([*self._linhas_gd(),
                          "DEM_MAX calculada separadamente para cada mês:"])
            for parte in meses:
                balance = parte["balanco"]
                lines.append(
                    f"  DEM_MAX_{parte['mes']:02d}: {formatar_numero(balance.demanda_kw, 6)} kW · "
                    f"importada {formatar_numero(balance.energia_importada_kwh, 6)} kWh · "
                    f"gerada {formatar_numero(balance.energia_gerada_kwh, 6)} kWh · "
                    f"resíduo do balanço {balance.residuo_balanco_kwh:.3e} kWh"
                )
        else:
            lines.extend([
                f"Energia anual incluída: {formatar_numero(self.result['energia_incluida_kwh'])} kWh",
                f"Energia anual reconstruída: {formatar_numero(self.result['energia_reconstituida_kwh'])} kWh",
                "Cobertura por mês:",
            ])
            for parte in meses:
                lines.append(
                    f"  {parte['mes']:02d}: {parte['unidades_incluidas']:,} UCs incluídas · "
                    f"{parte['unidades_energia_invalida']:,} com energia inválida · "
                    f"{len(parte['grupos_omitidos'])} grupos omitidos"
                )
        return lines

    def _linhas_gd(self):
        linhas = [f"Fonte dos equipamentos: {self.result['fonte_equipamento']}"]
        potencias = self.result.get("potencias_gd")
        if potencias and potencias["inversor_kva"] > 0:
            linhas.append(f"GD simulada: {formatar_numero(potencias['modulos_kw'], 3)} kW de módulos · "
                          f"{formatar_numero(potencias['inversor_kva'], 3)} kVA de inversor · "
                          f"irradiância-base {formatar_numero(potencias['performance_ratio'], 2)} · "
                          f"cut-in {formatar_numero(potencias['cut_in_percentual'], 1)}%")
        return linhas

    def export_csv(self):
        suggested = "curva_" + "_".join(self.result["titulo"].replace("/", "-").split()) + ".csv"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar curvas", suggested, "CSV (*.csv)")
        if not path:
            return
        series_map = dict(self.result["series"])
        if self.result.get("irradiancia"):
            series_map.update(self.result["irradiancia"]["series"])
        self._write_csv(path, series_map)

    def export_irradiance_csv(self):
        suggested = "irradiancia_" + "_".join(self.result["titulo"].replace("/", "-").split()) + ".csv"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar irradiância da UC", suggested, "CSV (*.csv)")
        if path:
            self._write_csv(path, self.result["irradiancia"]["series"])

    def _write_csv(self, path, series_map):
        series = list(series_map.items())
        solar = self.result.get("irradiancia")
        identity_headers = ["id_uc", "entidade", "linha_origem", "codigo_gd", "arquivo_nasa", "latitude_nasa", "longitude_nasa"] if solar else []
        identity = [self.result["uc"]["id_uc"], self.result["uc"]["entidade"], self.result["uc"]["linha_origem"],
                    self.result["codigo_gd"], solar["arquivo"], solar["latitude"], solar["longitude"]] if solar else []
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow([*identity_headers, "data_hora", *[nome for nome, _ in series]])
            for index, timestamp in enumerate(self.result["timestamps"]):
                writer.writerow([*identity, timestamp.isoformat(), *[f"{float(vetor[index]):.9f}".replace(".", ",") for _, vetor in series]])
