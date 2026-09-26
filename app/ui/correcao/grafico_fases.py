"""Grafico interativo das curvas do alimentador por fase, em QtCharts.

Segue o padrao de interacao do visualizador do TESTE_CORRECAO_CARGA, para quem
usa os dois apps nao estranhar:

    roda                zoom no tempo, ancorado no ponteiro
    Ctrl + roda         zoom vertical (tambem com o ponteiro sobre o eixo Y)
    arrastar            desloca a janela visivel
    clique              fixa o cursor de leitura; outro clique solta
    duplo clique        volta ao enquadramento completo
    clique na legenda   esconde ou mostra a serie

A diferenca e que aqui ha quatro series num grafico so -- as tres fases e o total
trifasico -- e a leitura do cursor mostra as quatro no mesmo instante.

No CurvaGD o grafico e em preto e branco: cada fase tem um tom de cinza e um
tracejado proprio (continua, traco, ponto), e o total trifasico sai num cinza
claro, com traco mais grosso, desenhado POR BAIXO das fases -- por cima, na visao
do ano inteiro, ele cobria as tres. O nome de cada serie aparece por escrito na
legenda e na leitura: a identidade nunca depende so do tom.

Portado da ferramenta CORRECAO_DEMANDA_BDGD (PyQt6) para PySide6.
"""

import math

import numpy as np
from PySide6.QtCharts import QCategoryAxis, QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QDate, QDateTime, QLocale, QPointF, QRectF, Qt, QTime
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import theme
from ..charts import aplicar_tracejado

# ------------------------------------------------------------------ aparencia

# Preto e branco: as fases se distinguem pelo tom e pelo tracejado da linha
CORES = {
    "superficie": theme.GRAFICO_FUNDO, "texto": theme.TEXTO, "texto_2": theme.GRAFICO_TEXTO,
    "apagado": theme.TEXTO_FRACO, "grade": theme.GRAFICO_GRADE, "base": theme.GRAFICO_EIXO,
    "fases": theme.CORES_SERIES[:3],
    "total": theme.CORES_SERIES[5],
}
ESPESSURA_FASE = 1.5
ESPESSURA_TOTAL = 2.0

# curvas que o painel sabe mostrar: (rotulo, prefixo das colunas da tabela)
CURVAS = (
    ("Liquida (carga - geracao)", "LIQ"),
    ("So carga", "CARGA"),
    ("Geracao", "GER"),
)
FASES = ("A", "B", "C")
NOMES = ("Fase A", "Fase B", "Fase C", "Total trifasico")

# ------------------------------------------------------------ eixo do tempo

MS_POR_HORA = 3600 * 1000
MS_POR_MES = 30.44 * 24 * MS_POR_HORA  # so para estimar quantas marcas um passo gera
MARCAS_ALVO = 7
# passos de marca, do mais fino ao mais grosso. Acima de uma semana passa a andar
# por mes de calendario: 30 dias corridos cairiam em 31/01, 01/03, 31/03...
PASSOS_DE_MARCA = (
    ("min", 15), ("min", 30), ("min", 60), ("min", 120), ("min", 180), ("min", 360),
    ("min", 720), ("min", 1440), ("min", 2880), ("min", 10080),
    ("mes", 1), ("mes", 2), ("mes", 3), ("mes", 6), ("mes", 12),
)
FORMATO_CURSOR = "ddd dd/MM/yyyy HH:mm"


def para_qdatetime(instante):
    """Timestamp do pandas -> QDateTime na mesma hora local.

    Montar data e hora explicitamente evita que o fuso da maquina desloque os
    rotulos, que e o que aconteceria partindo do epoch.
    """
    return QDateTime(QDate(instante.year, instante.month, instante.day),
                     QTime(instante.hour, instante.minute))


def escala_y(minimo, maximo, max_marcas=8):
    """(base, topo, marcas) com passo redondo e o zero caindo numa marca.

    A curva liquida cruza o zero, entao as duas pontas se alinham ao mesmo passo;
    uma curva so positiva comeca no zero, sem vao negativo vazio.
    """
    minimo, maximo = min(float(minimo), 0.0), max(float(maximo), 0.0)
    if maximo - minimo <= 0:
        return -1.0, 1.0, 3
    folga = (maximo - minimo) * 0.05
    alvo_min = minimo - folga if minimo < 0 else 0.0
    alvo_max = maximo + folga if maximo > 0 else 0.0
    expoente = math.floor(math.log10(alvo_max - alvo_min)) - 1
    for k in range(expoente, expoente + 6):
        for base_passo in (1, 2, 2.5, 5):
            passo = base_passo * 10.0 ** k
            base = math.floor(alvo_min / passo) * passo
            topo = math.ceil(alvo_max / passo) * passo
            marcas = round((topo - base) / passo) + 1
            if 3 <= marcas <= max_marcas:
                return round(base, 10), round(topo, 10), marcas
    return round(alvo_min, 10), round(alvo_max, 10), 5


def _passo_de_marca(span_ms):
    for unidade, quantidade in PASSOS_DE_MARCA:
        tamanho = quantidade * (60_000 if unidade == "min" else MS_POR_MES)
        if span_ms / tamanho <= MARCAS_ALVO:
            return unidade, quantidade
    return PASSOS_DE_MARCA[-1]


def _formato_de_marca(passo, span_ms):
    unidade, quantidade = passo
    dias = span_ms / (24 * MS_POR_HORA)
    if unidade == "mes":
        return "MMM/yy"
    if quantidade < 1440:
        return "dd/MM HH:mm" if dias >= 1 else "HH:mm"
    return "dd/MM"


def _proxima_marca(instante, passo):
    unidade, quantidade = passo
    if unidade == "mes":
        return instante.addMonths(quantidade)
    if quantidade % 1440 == 0:
        return instante.addDays(quantidade // 1440)
    return instante.addSecs(quantidade * 60)


def _primeira_marca(minimo_ms, passo):
    """Primeira marca alinhada em hora local, e nao no epoch."""
    unidade, quantidade = passo
    inicio = QDateTime.fromMSecsSinceEpoch(int(minimo_ms))
    meia_noite = QDateTime(inicio.date(), QTime(0, 0))
    if unidade == "mes":
        mes = inicio.date().month() - 1
        primeira = QDateTime(QDate(inicio.date().year(), mes - mes % quantidade + 1, 1),
                             QTime(0, 0))
    elif quantidade < 1440:
        minutos = inicio.time().hour() * 60 + inicio.time().minute()
        primeira = meia_noite.addSecs((minutos // quantidade) * quantidade * 60)
    else:
        primeira = meia_noite
    if primeira.toMSecsSinceEpoch() < minimo_ms:
        primeira = _proxima_marca(primeira, passo)
    return primeira


def marcas_de_tempo(minimo_ms, maximo_ms):
    """Marcas em horarios redondos da faixa visivel, como (posicao_ms, rotulo)."""
    passo = _passo_de_marca(maximo_ms - minimo_ms)
    formato = _formato_de_marca(passo, maximo_ms - minimo_ms)
    local = QLocale()
    atual = _primeira_marca(minimo_ms, passo)
    marcas, usados = [], set()
    while atual.toMSecsSinceEpoch() <= maximo_ms:
        rotulo = local.toString(atual, formato)
        # o QCategoryAxis identifica a marca pelo texto: rotulos repetidos ganham
        # espacos de largura zero, invisiveis na tela
        while rotulo in usados:
            rotulo += "​"
        usados.add(rotulo)
        marcas.append((float(atual.toMSecsSinceEpoch()), rotulo))
        atual = _proxima_marca(atual, passo)
    return marcas


def _encaixar(minimo, maximo, limite_min, limite_max):
    """Desloca a janela para dentro dos limites, preservando o tamanho."""
    tamanho = maximo - minimo
    if tamanho >= limite_max - limite_min:
        return limite_min, limite_max
    if minimo < limite_min:
        return limite_min, limite_min + tamanho
    if maximo > limite_max:
        return limite_max - tamanho, limite_max
    return minimo, maximo


# ---------------------------------------------------------------- a vista


class GraficoFases(QChartView):
    """As tres fases e o total num grafico so, com zoom, arrasto e cursor."""

    PASSO_ZOOM = 1.2
    BLOCOS_VISIVEIS_MINIMO = 8
    TOLERANCIA_CLIQUE = 4      # px; acima disso o gesto e arrasto, nao clique
    ALTURA_POR_MARCA = 28      # px minimos por rotulo do eixo Y

    def __init__(self, parent=None):
        self.grafico = QChart()
        super().__init__(self.grafico, parent)
        self.cores = CORES

        self.eixo_x = QCategoryAxis()
        self.eixo_x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        self.eixo_y = QValueAxis()
        self.eixo_y.setLabelFormat("%.0f")
        self.eixo_y.setTitleText("kW")
        self.grafico.addAxis(self.eixo_x, Qt.AlignmentFlag.AlignBottom)
        self.grafico.addAxis(self.eixo_y, Qt.AlignmentFlag.AlignLeft)

        cores = [QColor(c) for c in self.cores["fases"]] + [QColor(self.cores["total"])]
        espessuras = [ESPESSURA_FASE] * 3 + [ESPESSURA_TOTAL]
        self.cores_series = cores
        self.series = []
        for nome, cor, espessura in zip(NOMES, cores, espessuras):
            serie = QLineSeries()
            serie.setName(nome)
            caneta = QPen(cor, espessura)
            caneta.setCosmetic(True)
            serie.setPen(aplicar_tracejado(caneta, cor))
            self.series.append(serie)
        # o QtCharts desenha na ordem em que as series entram e nao tem z-order
        # por serie: o total entra primeiro para ficar por baixo das fases
        for serie in self.series[3:] + self.series[:3]:
            self.grafico.addSeries(serie)
            serie.attachAxis(self.eixo_x)
            serie.attachAxis(self.eixo_y)

        self._aplicar_tema()
        self.grafico.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grafico.legend().setMarkerShape(self.grafico.legend().MarkerShape.MarkerShapeFromSeries)
        for marcador in self.grafico.legend().markers():
            marcador.clicked.connect(self._alternar_serie)

        self.base_ms = self.passo_ms = None
        self.valores = None            # (n, 4): fases A, B, C e total
        self.limites_x = self.limites_y = None
        self.span_x_minimo = self.BLOCOS_VISIVEIS_MINIMO * 15 * 60 * 1000
        self.indice_cursor = None
        self.cursor_congelado = False
        self._ultimo_ponto = self._inicio_arrasto = None
        self._marcas = []

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _aplicar_tema(self):
        """Fundo, eixos e legenda nas cores de texto do tema, nunca na da serie."""
        c = self.cores
        self.grafico.setBackgroundBrush(QColor(c["superficie"]))
        self.grafico.setBackgroundRoundness(0)
        self.grafico.setDropShadowEnabled(False)
        self.grafico.legend().setLabelColor(QColor(c["texto_2"]))
        for eixo in (self.eixo_x, self.eixo_y):
            eixo.setLabelsColor(QColor(c["apagado"]))
            eixo.setGridLineColor(QColor(c["grade"]))
            eixo.setLinePenColor(QColor(c["base"]))
        self.eixo_y.setTitleBrush(QColor(c["apagado"]))

    # ------------------------------------------------------------- dados

    def definir(self, indice, valores, passo_minutos):
        """Troca as curvas. `valores` e (n, 4): fases A, B, C e o total."""
        self.valores = np.asarray(valores, dtype=float)
        self.base_ms = float(para_qdatetime(indice[0]).toMSecsSinceEpoch())
        self.passo_ms = passo_minutos * 60 * 1000
        self.span_x_minimo = self.BLOCOS_VISIVEIS_MINIMO * self.passo_ms
        # grade regular: os instantes saem por aritmetica, sem um QDateTime por ponto
        tempos = self.base_ms + self.passo_ms * np.arange(len(self.valores))
        for j, serie in enumerate(self.series):
            serie.replace([QPointF(t, v) for t, v in zip(tempos, self.valores[:, j])])

        self.limites_x = (float(tempos[0]), float(tempos[-1]))
        self.indice_cursor = None
        self.cursor_congelado = False
        self._ajustar_escala_y(forcar=True)
        self.enquadrar()

    def _visiveis(self):
        return [j for j, s in enumerate(self.series) if s.isVisible()]

    def _ajustar_escala_y(self, forcar=False):
        """Escala Y pelas series visiveis, com tantas marcas quanto couberem."""
        if self.valores is None:
            return
        colunas = self._visiveis() or list(range(len(self.series)))
        altura = self.grafico.plotArea().height()
        cabem = max(3, min(8, int(altura // self.ALTURA_POR_MARCA))) if altura > 0 else 6
        base, topo, marcas = escala_y(self.valores[:, colunas].min(),
                                      self.valores[:, colunas].max(), cabem)
        enquadrado = (self.eixo_y.min(), self.eixo_y.max()) == self.limites_y
        self.eixo_y.setTickCount(marcas)
        self.limites_y = (base, topo)
        if forcar or enquadrado:
            self.eixo_y.setRange(base, topo)

    def _alternar_serie(self):
        """Clique na legenda: esconde ou mostra a serie, e reescala o eixo Y."""
        marcador = self.sender()
        serie = marcador.series()
        serie.setVisible(not serie.isVisible())
        # o QtCharts esconde o marcador junto com a serie; ele precisa continuar
        # na legenda, apagado, para dar para ligar de novo
        marcador.setVisible(True)
        rotulo = QColor(self.cores["texto_2"] if serie.isVisible() else self.cores["apagado"])
        marcador.setLabelBrush(rotulo)
        self._ajustar_escala_y(forcar=True)
        self.viewport().update()

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._ajustar_escala_y()

    # ------------------------------------------------------------- janela

    def enquadrar(self):
        if self.limites_x is None:
            return
        self._aplicar_x(*self.limites_x)
        self.eixo_y.setRange(*self.limites_y)

    def _aplicar_x(self, minimo, maximo):
        minimo, maximo = _encaixar(minimo, maximo, *self.limites_x)
        marcas = marcas_de_tempo(minimo, maximo)
        if marcas != self._marcas:
            self._marcas = marcas
            for rotulo in self.eixo_x.categoriesLabels():
                self.eixo_x.remove(rotulo)
            self.eixo_x.setStartValue(minimo)
            for posicao, rotulo in marcas:
                self.eixo_x.append(rotulo, posicao)
        self.eixo_x.setRange(minimo, maximo)

    def _aplicar_y(self, minimo, maximo):
        self.eixo_y.setRange(*_encaixar(minimo, maximo, *self.limites_y))

    def _no_grafico(self, posicao):
        return self.grafico.mapFromScene(self.mapToScene(posicao.toPoint()))

    def _regiao(self, ponto):
        area = self.grafico.plotArea()
        if ponto.y() < area.top() or ponto.x() > area.right():
            return "fora"
        if ponto.x() < area.left():
            return "eixo_y"
        if ponto.y() > area.bottom():
            return "eixo_x"
        return "grafico"

    # --------------------------------------------------------- zoom e pan

    def wheelEvent(self, evento):
        graus = evento.angleDelta().y()
        ponto = self._no_grafico(evento.position())
        regiao = self._regiao(ponto)
        if regiao == "fora" or self.limites_x is None or not graus:
            evento.ignore()
            return
        fator = self.PASSO_ZOOM ** (-graus / 120.0)
        com_ctrl = bool(evento.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if regiao == "eixo_y" or (regiao == "grafico" and com_ctrl):
            self._zoom_y(fator, ponto)
        else:
            self._zoom_x(fator, ponto)
        evento.accept()

    def _zoom_x(self, fator, ponto):
        minimo, maximo = self.eixo_x.min(), self.eixo_x.max()
        ancora = min(max(self.grafico.mapToValue(ponto).x(), minimo), maximo)
        novo_min = ancora - (ancora - minimo) * fator
        novo_max = ancora + (maximo - ancora) * fator
        if novo_max - novo_min < self.span_x_minimo:
            metade = self.span_x_minimo / 2
            novo_min, novo_max = ancora - metade, ancora + metade
        self._aplicar_x(novo_min, novo_max)

    def _zoom_y(self, fator, ponto):
        minimo, maximo = self.eixo_y.min(), self.eixo_y.max()
        ancora = min(max(self.grafico.mapToValue(ponto).y(), minimo), maximo)
        novo_min = ancora - (ancora - minimo) * fator
        novo_max = ancora + (maximo - ancora) * fator
        span_minimo = (self.limites_y[1] - self.limites_y[0]) / 100.0
        if novo_max - novo_min < span_minimo:
            metade = span_minimo / 2
            novo_min, novo_max = ancora - metade, ancora + metade
        self._aplicar_y(novo_min, novo_max)

    def mousePressEvent(self, evento):
        ponto = self._no_grafico(evento.position())
        if (evento.button() == Qt.MouseButton.LeftButton and self.limites_x is not None
                and self._regiao(ponto) != "fora"):
            self._ultimo_ponto = self._inicio_arrasto = evento.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            evento.accept()
            return
        super().mousePressEvent(evento)       # a legenda recebe o clique por aqui

    def mouseMoveEvent(self, evento):
        if not self.cursor_congelado:
            self._mover_cursor(self._indice_em(self._no_grafico(evento.position())))
        if self._ultimo_ponto is None:
            super().mouseMoveEvent(evento)
            return
        antes = self.grafico.mapToValue(self._no_grafico(self._ultimo_ponto))
        agora = self.grafico.mapToValue(self._no_grafico(evento.position()))
        self._ultimo_ponto = evento.position()
        if antes.x() != agora.x():
            self._aplicar_x(self.eixo_x.min() + antes.x() - agora.x(),
                            self.eixo_x.max() + antes.x() - agora.x())
        if antes.y() != agora.y():
            self._aplicar_y(self.eixo_y.min() + antes.y() - agora.y(),
                            self.eixo_y.max() + antes.y() - agora.y())
        evento.accept()

    def mouseReleaseEvent(self, evento):
        if self._ultimo_ponto is not None and evento.button() == Qt.MouseButton.LeftButton:
            inicio, self._inicio_arrasto, self._ultimo_ponto = self._inicio_arrasto, None, None
            self.unsetCursor()
            if (evento.position() - inicio).manhattanLength() <= self.TOLERANCIA_CLIQUE:
                self.cursor_congelado = not self.cursor_congelado
                self._mover_cursor(self._indice_em(self._no_grafico(evento.position())),
                                   forcar=True)
            evento.accept()
            return
        super().mouseReleaseEvent(evento)

    def mouseDoubleClickEvent(self, evento):
        self.enquadrar()
        evento.accept()

    def leaveEvent(self, evento):
        if not self.cursor_congelado:
            self._mover_cursor(None)
        super().leaveEvent(evento)

    # ------------------------------------------------------------- cursor

    def _indice_em(self, ponto):
        """Indice do ponto real mais proximo: a grade e regular, e so aritmetica."""
        if self.valores is None or self._regiao(ponto) == "fora":
            return None
        indice = round((self.grafico.mapToValue(ponto).x() - self.base_ms) / self.passo_ms)
        return int(min(max(indice, 0), len(self.valores) - 1))

    def _mover_cursor(self, indice, forcar=False):
        if indice == self.indice_cursor and not forcar:
            return
        self.indice_cursor = indice
        self.viewport().update()

    def drawForeground(self, painter, retangulo):
        super().drawForeground(painter, retangulo)
        area = self.grafico.plotArea()
        self._desenhar_zero(painter, area)
        if self.indice_cursor is None or self.valores is None:
            return
        instante = self.base_ms + self.indice_cursor * self.passo_ms
        if not (self.eixo_x.min() <= instante <= self.eixo_x.max()):
            return
        x = self.grafico.mapToPosition(QPointF(instante, 0.0)).x()

        painter.save()
        painter.setClipRect(area)
        painter.setPen(QPen(QColor(self.cores["apagado"]), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        # pontos sobre cada serie visivel, com anel da cor do fundo para separar
        # os que caem perto uns dos outros
        anel = QPen(QColor(self.cores["superficie"]), 2.0)
        for j in self._visiveis():
            y = self.grafico.mapToPosition(QPointF(instante, self.valores[self.indice_cursor, j])).y()
            painter.setPen(anel)
            painter.setBrush(self.cores_series[j])
            painter.drawEllipse(QPointF(x, y), 4.0, 4.0)
        painter.restore()
        self._desenhar_leitura(painter, area, instante)

    def _desenhar_zero(self, painter, area):
        """Linha do zero: acima, o alimentador puxa da rede; abaixo, devolve."""
        if self.valores is None or self.eixo_y.min() > 0 or self.eixo_y.max() < 0:
            return
        y = self.grafico.mapToPosition(QPointF(self.eixo_x.min(), 0.0)).y()
        painter.save()
        painter.setClipRect(area)
        painter.setPen(QPen(QColor(self.cores["base"]), 1.4))
        painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.restore()

    def _desenhar_leitura(self, painter, area, instante):
        """Caixa no canto do plot: o instante e as quatro series, com o nome escrito."""
        quando = QLocale().toString(QDateTime.fromMSecsSinceEpoch(int(instante)), FORMATO_CURSOR)
        linhas = [(None, f"{quando[:1].upper()}{quando[1:]}")]
        # mesma ordem da legenda: o total, depois as fases
        for j in [k for k in (3, 0, 1, 2) if k in self._visiveis()]:
            valor = self.valores[self.indice_cursor, j]
            linhas.append((j, f"{NOMES[j]}   {valor:,.1f} kW".replace(",", " ")))

        painter.save()
        fonte = painter.font()
        fonte.setPointSizeF(max(fonte.pointSizeF() - 0.5, 7.5))
        painter.setFont(fonte)
        metricas = painter.fontMetrics()
        altura_linha = metricas.height() + 2
        amostra = 14
        largura = max(metricas.horizontalAdvance(t) for _, t in linhas) + amostra + 18
        caixa = QRectF(area.left() + 8, area.top() + 8, largura, altura_linha * len(linhas) + 8)
        fundo = QColor(self.cores["superficie"])
        fundo.setAlpha(235)
        painter.setBrush(fundo)
        painter.setPen(QPen(QColor(self.cores["base"]), 0.8))
        painter.drawRoundedRect(caixa, 3.0, 3.0)

        y = caixa.top() + 4
        for j, texto in linhas:
            x = caixa.left() + 8
            if j is not None:
                # a cor marca a serie ao lado; o texto fica na cor do texto
                painter.setPen(aplicar_tracejado(QPen(self.cores_series[j], 2.0), self.cores_series[j]))
                meio = y + altura_linha / 2
                painter.drawLine(QPointF(x, meio), QPointF(x + amostra - 4, meio))
                x += amostra
            painter.setPen(QColor(self.cores["texto"] if j is None else self.cores["texto_2"]))
            painter.drawText(QRectF(x, y, largura, altura_linha),
                             int(Qt.AlignmentFlag.AlignVCenter), texto)
            y += altura_linha
        painter.restore()


# ---------------------------------------------------------------- o painel


class PainelCurva(QWidget):
    """Grafico da curva do alimentador com o seletor de curva e a ajuda de uso."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabela = None
        self.passo_minutos = 15

        self.seletor = QComboBox()
        for rotulo, prefixo in CURVAS:
            self.seletor.addItem(rotulo, prefixo)
        self.seletor.currentIndexChanged.connect(self._redesenhar)
        enquadrar = QPushButton("Enquadrar tudo")

        self.vista = GraficoFases()
        enquadrar.clicked.connect(self.vista.enquadrar)

        ajuda = QLabel("Roda: zoom no tempo  |  Ctrl + roda: zoom vertical  |  arrastar: mover"
                       "  |  clique: fixa o cursor  |  duplo clique: enquadrar  |"
                       "  clique na legenda: esconde a serie")
        ajuda.setWordWrap(True)
        fonte = ajuda.font()
        fonte.setPointSizeF(max(fonte.pointSizeF() - 1, 7.0))
        ajuda.setFont(fonte)
        ajuda.setEnabled(False)       # cor apagada do tema, sem cor fixa

        topo = QHBoxLayout()
        topo.addWidget(QLabel("Curva:"))
        topo.addWidget(self.seletor)
        topo.addStretch(1)
        topo.addWidget(enquadrar)

        layout = QVBoxLayout(self)
        layout.addLayout(topo)
        layout.addWidget(self.vista, 1)
        layout.addWidget(ajuda)

    def definir(self, tabela, passo_minutos):
        self.tabela = tabela
        self.passo_minutos = passo_minutos
        self._redesenhar()

    def _redesenhar(self):
        if self.tabela is None:
            return
        prefixo = self.seletor.currentData()
        fases = self.tabela[[f"{prefixo}_{f}" for f in FASES]].to_numpy(dtype=float)
        valores = np.column_stack([fases, fases.sum(axis=1)])
        self.vista.definir(self.tabela.index, valores, self.passo_minutos)
