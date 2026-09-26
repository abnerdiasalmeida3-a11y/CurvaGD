"""Aba Estatisticas: o perfil das cargas de cada alimentador, para escolher estudos.

Em cima, uma linha por alimentador, ordenavel por qualquer coluna -- clicar em
"Indust." poe no topo os mais industriais, em "Mono" os mais monofasicos. Embaixo,
o detalhe do alimentador selecionado: todas as classes e as ligacoes, com
quantidade e participacao. Duplo clique numa linha leva o alimentador para a aba
Demanda.

As porcentagens trazem uma barra discreta atras do numero, em cinza: e
magnitude, nao categoria, e a barra deixa a coluna ser lida de relance sem
competir com o texto.

Portado da ferramenta CORRECAO_DEMANDA_BDGD (PyQt6) para PySide6.
"""

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QSpinBox, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView,
    QVBoxLayout, QWidget,
)

from ...correcao import estatisticas

# barra em cinza medio: a interface do CurvaGD e em preto e branco
COR_BARRA = "#6E6E6E"
MINIMO_PADRAO = 100   # abaixo disso, 100% de uma classe costuma ser uma UC so
# largura unica das colunas de porcentagem, em todas as tabelas: e o que faz a
# mesma porcentagem ter o mesmo comprimento de barra em qualquer coluna
LARGURA_PCT = 72

COLUNAS_GERAL = (
    ("ALIMENTADOR", "Alimentador", "texto", "Codigo e nome do alimentador (CTMT)"),
    ("UCS", "UCs", "inteiro", "Unidades consumidoras ativas, BT e MT"),
    ("Residencial", "Resid.", "pct", "Participacao da classe residencial"),
    ("Comercial", "Comerc.", "pct", "Participacao da classe comercial, servicos e outras atividades"),
    ("Industrial", "Indust.", "pct", "Participacao da classe industrial"),
    ("Rural", "Rural", "pct", "Participacao da classe rural"),
    ("Outras", "Outras", "pct", "Poder publico, servico publico, iluminacao publica e demais"),
    ("Monofasica", "Mono", "pct", "Participacao das UCs de ligacao monofasica"),
    ("Bifasica", "Bi", "pct", "Participacao das UCs de ligacao bifasica"),
    ("Trifasica", "Tri", "pct", "Participacao das UCs de ligacao trifasica"),
    ("COM_GD", "Com GD", "pct", "UCs com geracao distribuida -- sempre pela quantidade de UCs"),
    ("PREDOMINANTE", "Predominante", "texto", "Classe com maior participacao"),
)
COLUNAS_CLASSE = (
    ("CLASSE", "Classe", "texto", ""),
    ("UCS", "UCs", "inteiro", ""),
    ("PCT_UCS", "% UCs", "pct", ""),
    ("ENERGIA_MWH", "MWh/ano", "real", ""),
    ("PCT_ENERGIA", "% energia", "pct", ""),
)
COLUNAS_LIGACAO = (("LIGACAO", "Ligacao", "texto", ""),) + COLUNAS_CLASSE[1:]


def _numero(valor, casas=0):
    return f"{valor:,.{casas}f}".replace(",", " ")


class ModeloQuadro(QAbstractTableModel):
    """DataFrame numa tabela, com formato por coluna e ordenacao pelo valor cru."""

    CRU = Qt.ItemDataRole.UserRole

    def __init__(self, colunas):
        super().__init__()
        self.colunas = colunas
        self.df = pd.DataFrame(columns=[c[0] for c in colunas])

    def trocar(self, df):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.endResetModel()

    def rowCount(self, pai=QModelIndex()):
        return 0 if pai.isValid() else len(self.df)

    def columnCount(self, pai=QModelIndex()):
        return 0 if pai.isValid() else len(self.colunas)

    def data(self, indice, papel=Qt.ItemDataRole.DisplayRole):
        if not indice.isValid():
            return None
        chave, _, formato, _ = self.colunas[indice.column()]
        valor = self.df.iat[indice.row(), self.df.columns.get_loc(chave)]
        if papel == self.CRU:
            return valor
        if papel == Qt.ItemDataRole.DisplayRole:
            if formato == "pct":
                return f"{valor:.1f}%"
            if formato == "inteiro":
                return _numero(valor)
            if formato == "real":
                return _numero(valor, 1)
            return str(valor)
        if papel == Qt.ItemDataRole.TextAlignmentRole and formato != "texto":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, secao, orientacao, papel=Qt.ItemDataRole.DisplayRole):
        if orientacao != Qt.Orientation.Horizontal:
            return None
        if papel == Qt.ItemDataRole.DisplayRole:
            return self.colunas[secao][1]
        if papel == Qt.ItemDataRole.ToolTipRole:
            return self.colunas[secao][3] or None
        return None

    def sort(self, coluna, ordem=Qt.SortOrder.AscendingOrder):
        if self.df.empty:
            return
        self.beginResetModel()
        self.df = self.df.sort_values(
            self.colunas[coluna][0], ascending=ordem == Qt.SortOrder.AscendingOrder,
            kind="stable", ignore_index=True)
        self.endResetModel()


class DelegadoBarra(QStyledItemDelegate):
    """Porcentagem com uma barra de fundo proporcional ao valor.

    A barra vai por baixo do numero, que fica na cor normal do texto: a cor so
    reforca a leitura, nunca e a unica pista.
    """

    def __init__(self, cor, parent=None):
        super().__init__(parent)
        self.cor = QColor(cor)
        self.cor.setAlpha(80)

    def paint(self, painter, opcao, indice):
        valor = indice.data(ModeloQuadro.CRU)
        if not isinstance(valor, (int, float, np.number)) or not np.isfinite(valor):
            return super().paint(painter, opcao, indice)

        opt = QStyleOptionViewItem(opcao)
        self.initStyleOption(opt, indice)
        estilo = opt.widget.style() if opt.widget else None
        texto = opt.text
        opt.text = ""
        if estilo:
            estilo.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        area = QRectF(opt.rect).adjusted(3, 4, -3, -4)
        largura = area.width() * max(0.0, min(float(valor), 100.0)) / 100.0
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.cor)
        painter.drawRoundedRect(QRectF(area.left(), area.top(), largura, area.height()), 2, 2)
        selecionado = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.setPen(opt.palette.color(
            opt.palette.ColorRole.HighlightedText if selecionado else opt.palette.ColorRole.Text))
        painter.drawText(QRectF(opt.rect).adjusted(4, 0, -6, 0),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), texto)
        painter.restore()


def _ajustar_larguras(vista, colunas):
    """Texto e inteiros no tamanho do conteudo; porcentagens todas iguais."""
    vista.resizeColumnsToContents()
    for j, (_, _, formato, _) in enumerate(colunas):
        if formato == "pct":
            vista.setColumnWidth(j, LARGURA_PCT)


def _tabela(colunas, cor, ordenavel=True):
    modelo = ModeloQuadro(colunas)
    vista = QTableView()
    vista.setModel(modelo)
    vista.setSortingEnabled(ordenavel)
    vista.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    vista.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    vista.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    vista.setAlternatingRowColors(True)
    vista.setShowGrid(False)
    vista.verticalHeader().setVisible(False)
    vista.verticalHeader().setDefaultSectionSize(24)
    cabecalho = vista.horizontalHeader()
    cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    delegado = DelegadoBarra(cor, vista)
    for j, (_, _, formato, _) in enumerate(colunas):
        if formato == "pct":
            vista.setItemDelegateForColumn(j, delegado)
    return vista, modelo


class PainelEstatisticas(QWidget):
    """Visao geral de todos os alimentadores e o detalhe do selecionado."""

    escolhido = Signal(str)   # CTMT, no duplo clique

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preparadas = None
        self.alimentadores = None
        self.sem_alimentador = 0
        cor = COR_BARRA

        self.medida = QComboBox()
        for chave, rotulo in estatisticas.MEDIDAS.items():
            self.medida.addItem(rotulo, chave)
        self.medida.setToolTip("Base das porcentagens: quantidade de UCs ou energia anual. "
                               "Poucas UCs industriais podem responder pela maior parte da energia.")
        self.minimo = QSpinBox()
        self.minimo.setRange(0, 100000)
        self.minimo.setSingleStep(50)
        self.minimo.setValue(MINIMO_PADRAO)
        self.minimo.setToolTip("Esconde alimentadores pequenos: com poucas UCs, uma unica "
                               "carga ja vira 100% de uma classe.")
        self.filtro = QLineEdit()
        self.filtro.setPlaceholderText("Filtrar alimentador")
        self.filtro.setClearButtonEnabled(True)
        self.contagem = QLabel("Abra a BDGD para ver o perfil dos alimentadores.")
        self.contagem.setObjectName("ajuda")

        topo = QHBoxLayout()
        topo.addWidget(QLabel("Participacao por:"))
        topo.addWidget(self.medida)
        topo.addSpacing(12)
        topo.addWidget(QLabel("Minimo de UCs:"))
        topo.addWidget(self.minimo)
        topo.addSpacing(12)
        topo.addWidget(self.filtro, 1)

        self.geral, self.modelo_geral = _tabela(COLUNAS_GERAL, cor)
        self.geral.setToolTip("Clique no cabecalho para ordenar. Duplo clique leva o "
                              "alimentador para a aba Demanda.")
        self.geral.doubleClicked.connect(self._ao_duplo_clique)
        self.geral.selectionModel().currentRowChanged.connect(self._mostrar_detalhe)

        self.titulo = QLabel("Selecione um alimentador.")
        self.titulo.setTextFormat(Qt.TextFormat.RichText)
        self.classes, self.modelo_classes = _tabela(COLUNAS_CLASSE, cor, ordenavel=False)
        self.ligacoes, self.modelo_ligacoes = _tabela(COLUNAS_LIGACAO, cor, ordenavel=False)
        caixas = QHBoxLayout()
        for titulo, vista in (("Classes", self.classes), ("Ligacao", self.ligacoes)):
            caixa = QGroupBox(titulo)
            QVBoxLayout(caixa).addWidget(vista)
            caixas.addWidget(caixa, 3 if titulo == "Classes" else 2)
        detalhe = QWidget()
        corpo = QVBoxLayout(detalhe)
        corpo.setContentsMargins(0, 6, 0, 0)
        corpo.addWidget(self.titulo)
        corpo.addLayout(caixas, 1)

        divisor = QSplitter(Qt.Orientation.Vertical)
        divisor.addWidget(self.geral)
        divisor.addWidget(detalhe)
        divisor.setStretchFactor(0, 3)
        divisor.setStretchFactor(1, 2)
        divisor.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.addLayout(topo)
        layout.addWidget(divisor, 1)
        layout.addWidget(self.contagem)

        self.medida.currentIndexChanged.connect(self._atualizar)
        self.minimo.valueChanged.connect(self._atualizar)
        self.filtro.textChanged.connect(self._atualizar)

    # --------------------------------------------------------------- dados

    def mensagem(self, texto):
        """Estado sem dados: esvazia as tabelas e explica o porque no rodape."""
        self.preparadas = None
        for modelo in (self.modelo_geral, self.modelo_classes, self.modelo_ligacoes):
            modelo.trocar(modelo.df.iloc[0:0])
        self.titulo.setText("Selecione um alimentador.")
        self.contagem.setText(texto)

    def definir(self, brutas, alimentadores):
        """Recebe a contagem de `bdgd.estatisticas` e os alimentadores da base."""
        sem = brutas["CTMT"] == ""
        self.sem_alimentador = int(brutas.loc[sem, "UCS"].sum())
        self.preparadas = estatisticas.preparar(brutas[~sem])
        self.alimentadores = alimentadores
        self._atualizar()
        if self.modelo_geral.rowCount():
            self.geral.selectRow(0)

    def _atualizar(self):
        if self.preparadas is None:
            return
        atual = self._ctmt_selecionado()
        geral = estatisticas.visao_geral(self.preparadas, self.alimentadores,
                                         self.medida.currentData())
        total = len(geral)
        geral = geral[geral["UCS"] >= self.minimo.value()]
        texto = self.filtro.text().strip().lower()
        if texto:
            geral = geral[geral["ALIMENTADOR"].str.lower().str.contains(texto, regex=False)]

        coluna = self.geral.horizontalHeader().sortIndicatorSection()
        ordem = self.geral.horizontalHeader().sortIndicatorOrder()
        self.modelo_geral.trocar(geral)
        if 0 <= coluna < len(COLUNAS_GERAL) and self.geral.isSortingEnabled():
            self.modelo_geral.sort(coluna, ordem)
        _ajustar_larguras(self.geral, COLUNAS_GERAL)
        self.geral.horizontalHeader().setStretchLastSection(True)

        rodape = f"{len(geral)} de {total} alimentadores"
        if self.sem_alimentador:
            rodape += (f"  |  {_numero(self.sem_alimentador)} UCs sem alimentador (CTMT vazio) "
                       "ficaram de fora")
        rodape += "  |  duplo clique leva o alimentador para a aba Demanda"
        self.contagem.setText(rodape)
        self._selecionar(atual)

    def _ctmt_selecionado(self):
        linha = self.geral.currentIndex().row()
        if linha < 0 or linha >= len(self.modelo_geral.df):
            return None
        return self.modelo_geral.df.at[linha, "CTMT"]

    def _selecionar(self, ctmt):
        """Mantem o mesmo alimentador selecionado depois de filtrar ou reordenar."""
        linhas = self.modelo_geral.df.index[self.modelo_geral.df["CTMT"] == ctmt]
        if len(linhas):
            self.geral.selectRow(int(linhas[0]))
        else:
            self._mostrar_detalhe()

    def _mostrar_detalhe(self, *_):
        ctmt = self._ctmt_selecionado()
        if ctmt is None or self.preparadas is None:
            self.titulo.setText("Selecione um alimentador.")
            self.modelo_classes.trocar(self.modelo_classes.df.iloc[0:0])
            self.modelo_ligacoes.trocar(self.modelo_ligacoes.df.iloc[0:0])
            return
        linha = self.modelo_geral.df[self.modelo_geral.df["CTMT"] == ctmt].iloc[0]
        classes, ligacoes = estatisticas.detalhe(self.preparadas, ctmt)
        self.modelo_classes.trocar(classes)
        self.modelo_ligacoes.trocar(ligacoes)
        _ajustar_larguras(self.classes, COLUNAS_CLASSE)
        _ajustar_larguras(self.ligacoes, COLUNAS_LIGACAO)
        self.titulo.setText(
            f"<b>{linha['ALIMENTADOR']}</b> &nbsp;&middot;&nbsp; {_numero(linha['UCS'])} UCs"
            f" &nbsp;&middot;&nbsp; {_numero(classes['ENERGIA_MWH'].sum())} MWh/ano"
            f" &nbsp;&middot;&nbsp; {linha['COM_GD']:.1f}% com GD"
        )

    def _ao_duplo_clique(self, indice):
        ctmt = self._ctmt_selecionado()
        if ctmt:
            self.escolhido.emit(str(ctmt))
