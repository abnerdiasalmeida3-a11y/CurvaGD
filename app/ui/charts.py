"""Construcao e apresentacao dos graficos de curva.

Responsabilidades:
  * escala vertical com passos redondos que sempre inclui e marca o zero;
  * escala temporal com divisoes e formato coerentes com o periodo exibido;
  * paleta categorica consistente e tracos com espessura legivel;
  * leitura interativa: cursor vertical, marcadores e balao com os valores;
  * navegacao: zoom no tempo pela roda, arrasto, selecao de janela e teclado;
  * selecao de quais curvas aparecem e comparacao de curvas tipicas por
    tipo de dia (dia util, sabado, domingo e feriado).
"""

import math
from bisect import bisect_left
from datetime import timedelta

import numpy as np
from PySide6.QtCharts import (QAreaSeries, QChart, QChartView, QDateTimeAxis,
                              QLineSeries, QValueAxis)
from PySide6.QtCore import (QDateTime, QLocale, QMargins, QPointF, QRectF, Qt,
                            Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                               QLabel, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme
from ..core.errors import ErroBDGD
from ..domain.tipicas import curvas_tipicas

PASSOS_REDONDOS = (1.0, 2.0, 2.5, 5.0, 10.0)
PASSOS_TEMPO_MIN = (5, 10, 15, 30, 60, 120, 180, 240, 360, 720, 1440)
LOCALE_BR = QLocale(QLocale.Language.Portuguese, QLocale.Country.Brazil)


# --------------------------------------------------------------------------
# Escalas
# --------------------------------------------------------------------------
def formatar_br(valor, casas=2):
    return f"{valor:,.{casas}f}".replace(",", "#").replace(".", ",").replace("#", ".")


def passo_redondo(bruto):
    """Menor passo 'redondo' (1, 2, 2.5, 5 x 10^k) nao menor que o passo bruto."""
    if not math.isfinite(bruto) or bruto <= 0:
        return 1.0
    base = 10.0 ** math.floor(math.log10(bruto))
    for passo in PASSOS_REDONDOS:
        if passo * base >= bruto * (1 - 1e-9):
            return passo * base
    return 10.0 * base


def escala_com_zero(valores, divisoes_alvo=8):
    """Limites e passo do eixo vertical, garantindo que o zero seja um tique.

    Retorna (inferior, superior, passo, casas_decimais).
    """
    finitos = [float(v) for v in valores if math.isfinite(float(v))]
    minimo = min([0.0, *finitos]) if finitos else 0.0
    maximo = max([0.0, *finitos]) if finitos else 1.0
    if maximo - minimo < 1e-12:
        maximo = minimo + 1.0
    folga = (maximo - minimo) * 0.06
    alvo_min = minimo - (folga if minimo < 0 else 0.0)
    alvo_max = maximo + (folga if maximo > 0 else 0.0)
    passo = passo_redondo((alvo_max - alvo_min) / max(divisoes_alvo, 2))
    inferior = math.floor(alvo_min / passo + 1e-9) * passo
    superior = math.ceil(alvo_max / passo - 1e-9) * passo
    if superior - inferior < passo:
        superior = inferior + passo
    if abs(inferior) < passo * 1e-9:
        inferior = 0.0
    if abs(superior) < passo * 1e-9:
        superior = 0.0
    if passo >= 10:
        casas = 0
    elif passo >= 1:
        casas = 1
    elif passo >= 0.1:
        casas = 2
    else:
        casas = max(2, min(6, int(math.ceil(-math.log10(passo))) + 1))
    return inferior, superior, passo, casas


def formato_do_periodo(duracao_ms, modo_horario=False):
    """Formato e numero de divisoes do eixo X para a janela visivel."""
    if modo_horario:
        return "HH:mm", 9
    dias = duracao_ms / 86_400_000.0
    if dias > 60:
        return "dd/MM/yy", 13
    if dias > 10:
        return "dd/MM", 11
    if dias > 1:
        return "dd/MM HH:mm", 9
    return "HH:mm", 9


def escala_temporal(xs, divisoes_alvo=8, modo_horario=False):
    """Limites, divisoes e formato do eixo temporal (valores em ms de epoca).

    Recortes de ate tres dias sao encaixados em horas redondas, para que os
    rotulos caiam em 00:00, 03:00, 06:00... em vez de instantes arbitrarios.
    Acima disso o rotulo mostra apenas a data e o ajuste nao e perceptivel.
    """
    inicio, fim = float(xs[0]), float(xs[-1])
    duracao = max(fim - inicio, 60_000.0)
    formato, divisoes = formato_do_periodo(duracao, modo_horario)
    if duracao > 3 * 86_400_000.0:
        return inicio, fim, divisoes, formato
    intervalo = min((minutos * 60_000 for minutos in PASSOS_TEMPO_MIN),
                    key=lambda passo: abs(duracao / passo - divisoes_alvo))
    deslocamento = QDateTime.fromMSecsSinceEpoch(int(inicio)).offsetFromUtc() * 1000
    minimo = math.floor((inicio + deslocamento) / intervalo) * intervalo - deslocamento
    maximo = math.ceil((fim + deslocamento) / intervalo) * intervalo - deslocamento
    divisoes = max(2, min(int(round((maximo - minimo) / intervalo)) + 1, 16))
    return minimo, maximo, divisoes, formato


def titulo_temporal(inicio_ms, modo_horario=False):
    """Rotulo do eixo X com o fuso efetivamente usado na exibicao."""
    if modo_horario:
        return "Hora do dia"
    segundos = QDateTime.fromMSecsSinceEpoch(int(inicio_ms)).offsetFromUtc()
    sinal = "+" if segundos >= 0 else "−"
    horas, minutos = divmod(abs(segundos) // 60, 60)
    sufixo = f"{horas:02d}" + (f":{minutos:02d}" if minutos else "")
    return f"Data e hora (UTC{sinal}{sufixo})"


def _fonte(tamanho, negrito=False):
    fonte = QFont(theme.FAMILIA_FONTE.split(",")[0].strip())
    fonte.setPointSizeF(tamanho)
    fonte.setBold(negrito)
    return fonte


def cor_da_serie(indice):
    return QColor(theme.CORES_SERIES[indice % len(theme.CORES_SERIES)])


_TRACEJADOS = {
    "traco": [6.0, 3.5],
    "ponto": [1.2, 2.6],
    "traco_ponto": [6.0, 2.6, 1.2, 2.6],
    "traco_ponto_ponto": [6.0, 2.4, 1.2, 2.4, 1.2, 2.4],
}


def aplicar_tracejado(caneta, cor):
    """Tracejado da curva pela sua cor: no preto e branco o tom sozinho nao basta."""
    estilo = theme.ESTILOS_LINHA.get(QColor(cor).name().upper(), "continua")
    padrao = _TRACEJADOS.get(estilo)
    if padrao:
        caneta.setStyle(Qt.PenStyle.CustomDashLine)
        caneta.setDashPattern(padrao)
    return caneta


# --------------------------------------------------------------------------
# Construcao do grafico
# --------------------------------------------------------------------------
def construir_grafico(series_map, timestamps, titulo, titulo_y="Potência (kW)",
                      *, cores=None, faixas=None, modo_horario=False):
    """Monta o QChart e o QChartView interativo de um conjunto de series.

    cores        sequencia de cores alinhada a series_map (opcional).
    faixas       {nome: (vetor_minimo, vetor_maximo)} desenhado como area
                 translucida sob a linha, para mostrar dispersao.
    modo_horario rotula o eixo X so pela hora, para curvas de um dia tipico.
    """
    chart = QChart()
    chart.setTitle(titulo)
    chart.setTitleFont(_fonte(11.5, True))
    chart.setTitleBrush(QBrush(QColor(theme.GRAFICO_TITULO)))
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setBackgroundBrush(QBrush(QColor(theme.GRAFICO_FUNDO)))
    chart.setBackgroundPen(QPen(Qt.PenStyle.NoPen))
    chart.setPlotAreaBackgroundBrush(QBrush(QColor(theme.GRAFICO_AREA)))
    chart.setPlotAreaBackgroundVisible(True)
    chart.setMargins(QMargins(10, 8, 16, 8))
    chart.setLocale(LOCALE_BR)
    chart.setLocalizeNumbers(True)

    legenda = chart.legend()
    legenda.setVisible(True)
    legenda.setAlignment(Qt.AlignmentFlag.AlignBottom)
    legenda.setFont(_fonte(9.5))
    legenda.setLabelColor(QColor(theme.GRAFICO_TEXTO))
    legenda.setMarkerShape(legenda.MarkerShape.MarkerShapeFromSeries)
    legenda.setBorderColor(QColor(Qt.GlobalColor.transparent))

    xs = [instante.timestamp() * 1000.0 for instante in timestamps]
    faixas = dict(faixas or {})
    leituras = []
    valores = []
    areas_ocultas = []
    # QAreaSeries nao assume a posse das linhas de contorno: sem manter uma
    # referencia viva a elas o coletor de lixo derruba o processo.
    retidos = []

    # As faixas entram primeiro para ficarem atras das linhas.
    for indice, nome in enumerate(series_map):
        if nome not in faixas:
            continue
        cor = QColor(cores[indice]) if cores else cor_da_serie(indice)
        inferior_vetor = np.asarray(faixas[nome][0], dtype=np.float64)
        superior_vetor = np.asarray(faixas[nome][1], dtype=np.float64)
        inferior = QLineSeries()
        inferior.replace([QPointF(x, float(y)) for x, y in zip(xs, inferior_vetor)])
        superior = QLineSeries()
        superior.replace([QPointF(x, float(y)) for x, y in zip(xs, superior_vetor)])
        area = QAreaSeries(superior, inferior)
        inferior.setParent(area)
        superior.setParent(area)
        retidos.extend((inferior, superior))
        area.setName(f"{nome} · faixa mín–máx")
        pincel = QColor(cor)
        pincel.setAlpha(theme.FAIXA_OPACIDADE)
        area.setBrush(QBrush(pincel))
        area.setPen(QPen(Qt.PenStyle.NoPen))
        chart.addSeries(area)
        areas_ocultas.append(area)
        faixas[nome] = (inferior_vetor, superior_vetor, area)
        valores.extend((float(inferior_vetor.min()), float(superior_vetor.max())))

    serie_ref = None
    for indice, (nome, vetor) in enumerate(series_map.items()):
        cor = QColor(cores[indice]) if cores else cor_da_serie(indice)
        serie = QLineSeries()
        serie.setName(nome)
        caneta = QPen(cor)
        caneta.setWidthF(1.9)
        caneta.setCosmetic(True)
        caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        caneta.setCapStyle(Qt.PenCapStyle.FlatCap)
        aplicar_tracejado(caneta, cor)
        serie.setPen(caneta)
        ys = np.asarray(vetor, dtype=np.float64)
        serie.replace([QPointF(x, float(y)) for x, y in zip(xs, ys)])
        chart.addSeries(serie)
        serie_ref = serie_ref or serie
        entrada = faixas.get(nome)
        leituras.append({
            "nome": nome,
            "cor": cor,
            "ys": ys,
            "serie": serie,
            "faixa": (entrada[0], entrada[1]) if entrada else None,
            "area": entrada[2] if entrada else None,
        })
        valores.extend((float(ys.min()), float(ys.max())))

    x_min, x_max, divisoes, formato = escala_temporal(xs, modo_horario=modo_horario)
    eixo_x = QDateTimeAxis()
    eixo_x.setFormat(formato)
    eixo_x.setTitleText(titulo_temporal(xs[0], modo_horario))
    eixo_x.setTitleFont(_fonte(9.5, True))
    eixo_x.setTitleBrush(QBrush(QColor(theme.GRAFICO_TEXTO)))
    eixo_x.setTickCount(divisoes)
    eixo_x.setLabelsFont(_fonte(9))
    eixo_x.setLabelsColor(QColor(theme.GRAFICO_TEXTO))
    eixo_x.setGridLineColor(QColor(theme.GRAFICO_GRADE))
    eixo_x.setLinePenColor(QColor(theme.GRAFICO_EIXO))
    eixo_x.setRange(QDateTime.fromMSecsSinceEpoch(int(x_min)), QDateTime.fromMSecsSinceEpoch(int(x_max)))

    inferior, superior, passo, casas = escala_com_zero(valores)
    eixo_y = QValueAxis()
    eixo_y.setTitleText(titulo_y)
    eixo_y.setTitleFont(_fonte(9.5, True))
    eixo_y.setTitleBrush(QBrush(QColor(theme.GRAFICO_TEXTO)))
    eixo_y.setLabelFormat(f"%.{casas}f")
    eixo_y.setLabelsFont(_fonte(9))
    eixo_y.setLabelsColor(QColor(theme.GRAFICO_TEXTO))
    eixo_y.setRange(inferior, superior)
    eixo_y.setTickType(QValueAxis.TickType.TicksDynamic)
    eixo_y.setTickAnchor(0.0)
    eixo_y.setTickInterval(passo)
    eixo_y.setMinorTickCount(1)
    eixo_y.setGridLineColor(QColor(theme.GRAFICO_GRADE))
    eixo_y.setMinorGridLineColor(QColor(theme.GRAFICO_GRADE_MENOR))
    eixo_y.setMinorGridLineVisible(True)
    eixo_y.setLinePenColor(QColor(theme.GRAFICO_EIXO))

    chart.addAxis(eixo_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(eixo_y, Qt.AlignmentFlag.AlignLeft)
    for serie in chart.series():
        serie.attachAxis(eixo_x)
        serie.attachAxis(eixo_y)
    for area in areas_ocultas:
        for marcador in chart.legend().markers(area):
            marcador.setVisible(False)

    return CurvaChartView(chart, xs, leituras, casas, limites=(x_min, x_max),
                          unidade=titulo_y.rsplit("(", 1)[-1].rstrip(")") if "(" in titulo_y else "",
                          modo_horario=modo_horario, serie_ref=serie_ref, retidos=retidos)


# --------------------------------------------------------------------------
# Visualizacao interativa
# --------------------------------------------------------------------------
class CurvaChartView(QChartView):
    """Grafico com linha do zero, leitura por cursor e navegacao no tempo.

    Roda        amplia/reduz o eixo do tempo em torno do cursor.
    Ctrl+roda   amplia/reduz tambem o eixo vertical.
    Shift+roda  desloca no tempo.
    Arrastar    com o botao esquerdo seleciona uma janela de tempo;
                com o botao do meio (ou Shift) desloca o grafico.
    Teclado     + e - ampliam, 0 restaura, setas andam no tempo.
    """

    INTERVALOS_MINIMOS = 8
    visualizacao_alterada = Signal()

    def __init__(self, chart, xs, leituras, casas=2, *, limites=None,
                 modo_horario=False, serie_ref=None, retidos=(), unidade="", parent=None):
        super().__init__(chart, parent)
        self._retidos = list(retidos)
        self._xs = xs
        self._leituras = leituras
        self._visiveis = {leitura["nome"] for leitura in leituras}
        self._casas = casas
        self._modo_horario = modo_horario
        self._unidade = unidade
        self._divisoes_x = self._eixo_x().tickCount()
        self._serie_ref = serie_ref or (chart.series()[0] if chart.series() else None)
        self._limites = limites or ((xs[0], xs[-1]) if xs else (0.0, 1.0))
        self._y_automatico = True
        self._cursor = None
        self._selecao = None
        self._arrasto = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QChartView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(theme.GRAFICO_FUNDO)))
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("Roda: zoom no tempo · Ctrl+roda: zoom vertical · "
                        "arraste: selecionar janela · botão do meio: deslocar · "
                        "duplo clique ou 0: restaurar")
        chart.plotAreaChanged.connect(self._ajustar_rotulos_x)

    def _ajustar_rotulos_x(self, *_):
        """Reduz a densidade dos rotulos sem perder as horas ja alinhadas."""
        eixo = self._eixo_x()
        area = self.chart().plotArea()
        if eixo is None or area.width() <= 0:
            return
        metrica = QFontMetricsF(eixo.labelsFont())
        largura = max(metrica.horizontalAdvance(data.toString(eixo.format()))
                      for data in (eixo.min(), eixo.max())) + 28
        capacidade = max(2, int(area.width() / largura) + 1)
        intervalos = self._divisoes_x - 1
        divisoes = max(n + 1 for n in range(1, intervalos + 1)
                       if intervalos % n == 0 and n + 1 <= capacidade)
        if eixo.tickCount() != divisoes:
            eixo.setTickCount(divisoes)

    def descricao_janela(self):
        inicio, fim = self.janela()
        formato = "HH:mm" if self._modo_horario else "dd/MM/yyyy HH:mm"
        datas = [QDateTime.fromMSecsSinceEpoch(int(v)).toString(formato) for v in (inicio, fim)]
        if self._modo_horario and datas[1] == "00:00" and fim > inicio:
            datas[1] = "24:00"
        return f"Período visível: {datas[0]} — {datas[1]} · {_duracao_legivel(fim - inicio)}"

    def definir_escala_automatica(self, ativa):
        self._y_automatico = bool(ativa)
        if ativa:
            self._reajustar_y()
        self.viewport().update()
        self.visualizacao_alterada.emit()

    # -- eixos -------------------------------------------------------------
    def _eixo_x(self):
        eixos = self.chart().axes(Qt.Orientation.Horizontal)
        return eixos[0] if eixos else None

    def _eixo_y(self):
        eixos = self.chart().axes(Qt.Orientation.Vertical)
        return eixos[0] if eixos else None

    def janela(self):
        """Inicio e fim visiveis, em ms de epoca."""
        eixo = self._eixo_x()
        if eixo is None:
            return self._limites
        return (eixo.min().toMSecsSinceEpoch(), eixo.max().toMSecsSinceEpoch())

    def _passo_amostral(self):
        return (self._xs[1] - self._xs[0]) if len(self._xs) > 1 else 60_000.0

    def _definir_janela(self, inicio, fim):
        eixo = self._eixo_x()
        if eixo is None:
            return
        minimo, maximo = self._limites
        total = max(maximo - minimo, 1.0)
        largura = min(max(fim - inicio, self._passo_amostral() * self.INTERVALOS_MINIMOS), total)
        inicio = min(max(inicio, minimo), maximo - largura)
        formato, divisoes = formato_do_periodo(largura, self._modo_horario)
        # Ao ampliar, encosta a janela em horas redondas para que os rotulos
        # caiam em 03:00, 06:00... e nao em instantes quebrados como 13:23.
        if largura < total * 0.999 and largura <= 10 * 86_400_000:
            encaixe_inicio, encaixe_largura = _encaixar_janela(inicio, largura, divisoes)
            if 0 < encaixe_largura <= total:
                inicio = min(max(encaixe_inicio, minimo), maximo - encaixe_largura)
                largura = encaixe_largura
                formato, divisoes = formato_do_periodo(largura, self._modo_horario)
        eixo.setRange(QDateTime.fromMSecsSinceEpoch(int(inicio)),
                      QDateTime.fromMSecsSinceEpoch(int(inicio + largura)))
        eixo.setFormat(formato)
        self._divisoes_x = divisoes
        self._ajustar_rotulos_x()
        if self._y_automatico:
            self._reajustar_y()
        self.viewport().update()
        self.visualizacao_alterada.emit()

    def _reajustar_y(self):
        """Reenquadra o eixo vertical no trecho de tempo visivel."""
        eixo = self._eixo_y()
        if eixo is None or not self._leituras:
            return
        inicio, fim = self.janela()
        primeiro = max(0, bisect_left(self._xs, inicio) - 1)
        ultimo = min(len(self._xs), bisect_left(self._xs, fim) + 1)
        if ultimo <= primeiro:
            return
        valores = []
        for leitura in self._leituras:
            if leitura["nome"] not in self._visiveis:
                continue
            bloco = leitura["ys"][primeiro:ultimo]
            if bloco.size:
                valores.extend((float(bloco.min()), float(bloco.max())))
            if leitura["faixa"] is not None:
                minimo, maximo = leitura["faixa"]
                valores.extend((float(minimo[primeiro:ultimo].min()),
                                float(maximo[primeiro:ultimo].max())))
        if not valores:
            return
        inferior, superior, passo, casas = escala_com_zero(valores)
        self._casas = casas
        eixo.setRange(inferior, superior)
        eixo.setTickInterval(passo)
        eixo.setLabelFormat(f"%.{casas}f")

    # -- navegacao ---------------------------------------------------------
    def restaurar(self):
        self._y_automatico = True
        self._definir_janela(*self._limites)

    def aplicar_zoom(self, fator, ancora=None):
        inicio, fim = self.janela()
        if ancora is None:
            ancora = (inicio + fim) / 2.0
        ancora = min(max(ancora, inicio), fim)
        self._definir_janela(ancora - (ancora - inicio) / fator, ancora + (fim - ancora) / fator)

    def deslocar(self, fracao):
        inicio, fim = self.janela()
        passo = (fim - inicio) * fracao
        self._definir_janela(inicio + passo, fim + passo)

    def aplicar_zoom_vertical(self, fator):
        eixo = self._eixo_y()
        if eixo is None or not math.isfinite(fator) or fator <= 0:
            return
        self._y_automatico = False
        # Amplia em torno do zero. Arredondar novamente os limites impedia
        # pequenos passos da roda de produzir qualquer mudanca visivel.
        inferior, superior = eixo.min() / fator, eixo.max() / fator
        if not math.isfinite(superior - inferior) or superior - inferior < 1e-9:
            return
        _, _, passo, casas = escala_com_zero([inferior, superior])
        self._casas = casas
        eixo.setRange(inferior, superior)
        eixo.setTickInterval(passo)
        eixo.setLabelFormat(f"%.{casas}f")
        self.viewport().update()
        self.visualizacao_alterada.emit()

    def definir_series_visiveis(self, nomes):
        """Liga e desliga curvas sem reconstruir o grafico."""
        self._visiveis = {leitura["nome"] for leitura in self._leituras if leitura["nome"] in set(nomes)}
        for leitura in self._leituras:
            visivel = leitura["nome"] in self._visiveis
            leitura["serie"].setVisible(visivel)
            if leitura["area"] is not None:
                leitura["area"].setVisible(visivel)
        if self._y_automatico:
            self._reajustar_y()
        self.viewport().update()

    def series_disponiveis(self):
        return [(leitura["nome"], leitura["cor"]) for leitura in self._leituras]

    # -- eventos -----------------------------------------------------------
    def _cena(self, evento):
        posicao = evento.position() if hasattr(evento, "position") else evento.pos()
        return self.mapToScene(int(posicao.x()), int(posicao.y()))

    def _valor_x(self, x_cena):
        if self._serie_ref is None:
            return None
        area = self.chart().plotArea()
        return self.chart().mapToValue(QPointF(x_cena, area.center().y()), self._serie_ref).x()

    def wheelEvent(self, event):
        passos = event.angleDelta().y() / 120.0
        if not passos:
            return event.accept()
        modificadores = event.modifiers()
        if modificadores & Qt.KeyboardModifier.ShiftModifier:
            self.deslocar(-0.15 * passos)
        else:
            fator = 1.25 ** passos
            cena = self._cena(event)
            ancora = self._valor_x(cena.x()) if self.chart().plotArea().contains(cena) else None
            if modificadores & Qt.KeyboardModifier.ControlModifier:
                self.aplicar_zoom_vertical(fator)
            else:
                self.aplicar_zoom(fator, ancora)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.restaurar()
        event.accept()

    def mousePressEvent(self, event):
        cena = self._cena(event)
        dentro = self.chart().plotArea().contains(cena)
        deslocando = (event.button() == Qt.MouseButton.MiddleButton
                      or (event.button() == Qt.MouseButton.LeftButton
                          and event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        if deslocando and dentro:
            self._arrasto = (cena.x(), *self.janela())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return event.accept()
        if event.button() == Qt.MouseButton.LeftButton and dentro:
            self._selecao = (cena.x(), cena.x())
            return event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        cena = self._cena(event)
        area = self.chart().plotArea()
        if self._arrasto is not None:
            origem, inicio, fim = self._arrasto
            escala = (fim - inicio) / max(area.width(), 1.0)
            passo = (origem - cena.x()) * escala
            self._definir_janela(inicio + passo, fim + passo)
            return event.accept()
        if self._selecao is not None:
            self._selecao = (self._selecao[0], min(max(cena.x(), area.left()), area.right()))
            self.viewport().update()
            return event.accept()
        self._cursor = cena if area.contains(cena) else None
        self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._arrasto is not None:
            self._arrasto = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            return event.accept()
        if self._selecao is not None:
            inicio_x, fim_x = sorted(self._selecao)
            self._selecao = None
            if fim_x - inicio_x >= 6:
                inicio, fim = self._valor_x(inicio_x), self._valor_x(fim_x)
                if inicio is not None and fim is not None:
                    self._definir_janela(inicio, fim)
            self.viewport().update()
            return event.accept()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._cursor = None
        self.viewport().update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        tecla = event.key()
        if tecla in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.aplicar_zoom(1.4)
        elif tecla in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.aplicar_zoom(1 / 1.4)
        elif tecla in (Qt.Key.Key_0, Qt.Key.Key_Home):
            self.restaurar()
        elif tecla == Qt.Key.Key_Left:
            self.deslocar(-0.25)
        elif tecla == Qt.Key.Key_Right:
            self.deslocar(0.25)
        else:
            return super().keyPressEvent(event)
        event.accept()

    # -- pintura -----------------------------------------------------------
    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        area = self.chart().plotArea()
        if area.isEmpty() or not self.chart().series():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._pintar_zero(painter, area)
        if self._selecao is not None:
            self._pintar_selecao(painter, area)
        elif self._cursor is not None:
            self._pintar_leitura(painter, area)
        painter.restore()

    def _pintar_zero(self, painter, area):
        eixo_y = self._eixo_y()
        if eixo_y is None or self._serie_ref is None or not (eixo_y.min() <= 0.0 <= eixo_y.max()):
            return
        y = self.chart().mapToPosition(QPointF(self._xs[0], 0.0), self._serie_ref).y()
        if not (area.top() - 1 <= y <= area.bottom() + 1):
            return
        # Halo claro garante que a marcacao do zero continue legivel mesmo
        # quando uma serie repousa exatamente sobre ela.
        halo = QPen(QColor(255, 255, 255, 215))
        halo.setWidthF(3.4)
        halo.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(halo)
        painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        caneta = QPen(QColor(theme.LINHA_ZERO))
        caneta.setWidthF(1.5)
        caneta.setStyle(Qt.PenStyle.CustomDashLine)
        caneta.setDashPattern([7, 4])
        painter.setPen(caneta)
        painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        painter.setFont(_fonte(8.5, True))
        metrica = QFontMetricsF(painter.font())
        texto = f"0 {self._unidade}".strip()
        largura = metrica.horizontalAdvance(texto) + 16
        altura = metrica.height() + 4
        topo = max(area.top() + 2, min(y - altura / 2, area.bottom() - altura - 2))
        etiqueta = QRectF(area.right() - largura - 4, topo, largura, altura)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(theme.LINHA_ZERO)))
        painter.drawRoundedRect(etiqueta, altura / 2, altura / 2)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(etiqueta, Qt.AlignmentFlag.AlignCenter, texto)

    def _pintar_selecao(self, painter, area):
        inicio, fim = sorted(self._selecao)
        faixa = QRectF(inicio, area.top(), max(fim - inicio, 1.0), area.height())
        preenchimento = QColor(theme.PRIMARIA)
        preenchimento.setAlpha(38)
        painter.setPen(QPen(QColor(theme.PRIMARIA), 1.0))
        painter.setBrush(QBrush(preenchimento))
        painter.drawRect(faixa)
        if fim - inicio >= 6 and self._serie_ref is not None:
            duracao = abs(self._valor_x(fim) - self._valor_x(inicio))
            painter.setFont(_fonte(9, True))
            painter.setPen(QPen(QColor(theme.PRIMARIA_ESCURA)))
            painter.drawText(QRectF(inicio, area.top() + 4, max(fim - inicio, 1.0), 18),
                             Qt.AlignmentFlag.AlignCenter, _duracao_legivel(duracao))

    def _pintar_leitura(self, painter, area):
        chart = self.chart()
        if self._serie_ref is None:
            return
        alvo = self._valor_x(self._cursor.x())
        indice = self._indice_proximo(alvo)
        if indice is None:
            return
        x_ponto = chart.mapToPosition(QPointF(self._xs[indice], 0.0), self._serie_ref).x()
        if not (area.left() - 1 <= x_ponto <= area.right() + 1):
            return

        caneta = QPen(QColor(theme.CURSOR))
        caneta.setWidthF(1.2)
        caneta.setStyle(Qt.PenStyle.CustomDashLine)
        caneta.setDashPattern([3, 3])
        painter.setPen(caneta)
        painter.drawLine(QPointF(x_ponto, area.top()), QPointF(x_ponto, area.bottom()))

        instante = QDateTime.fromMSecsSinceEpoch(int(self._xs[indice]))
        linhas = [instante.toString("HH:mm" if self._modo_horario else "dd/MM/yyyy HH:mm")]
        cores = []
        for leitura in self._leituras:
            if leitura["nome"] not in self._visiveis:
                continue
            valor = float(leitura["ys"][indice])
            ponto = chart.mapToPosition(QPointF(self._xs[indice], valor), self._serie_ref)
            if area.contains(ponto):
                painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
                painter.setBrush(QBrush(leitura["cor"]))
                painter.drawEllipse(ponto, 4.2, 4.2)
            nome = leitura["nome"].removesuffix(f" ({self._unidade})")
            linhas.append(f"{nome}: {formatar_br(valor, max(self._casas, 2))} {self._unidade}".rstrip())
            cores.append(leitura["cor"])
        if len(linhas) > 1:
            self._pintar_balao(painter, area, x_ponto, linhas, cores)

    def _indice_proximo(self, alvo):
        xs = self._xs
        if not xs or alvo is None:
            return None
        posicao = bisect_left(xs, alvo)
        if posicao <= 0:
            return 0
        if posicao >= len(xs):
            return len(xs) - 1
        anterior, seguinte = xs[posicao - 1], xs[posicao]
        return posicao - 1 if abs(alvo - anterior) <= abs(seguinte - alvo) else posicao

    def _pintar_balao(self, painter, area, x_ponto, linhas, cores):
        painter.setFont(_fonte(9))
        metrica = QFontMetricsF(painter.font())
        largura = min(max(metrica.horizontalAdvance(linha) for linha in linhas) + 18,
                      max(36, area.width() - 8))
        linhas = [metrica.elidedText(linha, Qt.TextElideMode.ElideMiddle, int(largura - 18))
                  for linha in linhas]
        altura = metrica.height() * len(linhas) + 14
        x = x_ponto + 14
        if x + largura > area.right():
            x = x_ponto - largura - 14
        x = max(area.left() + 4, min(x, area.right() - largura - 4))
        y = max(area.top() + 6, min(self._cursor.y() - altura / 2, area.bottom() - altura - 6))
        caixa = QRectF(x, y, largura, altura)
        fundo = QColor(theme.BALAO_FUNDO)
        fundo.setAlpha(theme.BALAO_OPACIDADE)
        painter.setPen(QPen(QColor(theme.BALAO_BORDA)))
        painter.setBrush(QBrush(fundo))
        painter.drawRoundedRect(caixa, 6, 6)
        painter.setPen(QPen(QColor(theme.BALAO_TEXTO)))
        painter.setFont(_fonte(9, True))
        painter.drawText(QRectF(x + 9, y + 7, largura - 18, metrica.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, linhas[0])
        painter.setFont(_fonte(9))
        for posicao, (texto, cor) in enumerate(zip(linhas[1:], cores), start=1):
            painter.setPen(QPen(cor))
            painter.drawText(QRectF(x + 9, y + 7 + metrica.height() * posicao, largura - 18, metrica.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, texto)


def _encaixar_janela(inicio, largura, divisoes):
    """Inicio e largura ajustados ao passo redondo mais proximo do pedido."""
    alvo = largura / max(divisoes - 1, 1)
    intervalo = min((minutos * 60_000 for minutos in PASSOS_TEMPO_MIN), key=lambda passo: abs(passo - alvo))
    deslocamento = QDateTime.fromMSecsSinceEpoch(int(inicio)).offsetFromUtc() * 1000
    encaixado = math.floor((inicio + deslocamento) / intervalo) * intervalo - deslocamento
    return encaixado, intervalo * max(divisoes - 1, 1)


def _duracao_legivel(duracao_ms):
    minutos = duracao_ms / 60_000.0
    if minutos < 90:
        return f"{minutos:.0f} min"
    horas = minutos / 60.0
    if horas < 48:
        return f"{formatar_br(horas, 1)} h"
    return f"{formatar_br(horas / 24.0, 1)} dias"


# Nome historico mantido para compatibilidade com scripts e testes antigos.
ZoomChartView = CurvaChartView


# --------------------------------------------------------------------------
# Seletor de curvas
# --------------------------------------------------------------------------
class SeletorDeSeries(QWidget):
    """Caixas de selecao, uma por curva, com a cor usada no grafico."""

    alterado = Signal(list)

    def __init__(self, series, parent=None):
        super().__init__(parent)
        linha = QHBoxLayout(self)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(14)
        rotulo = QLabel("Curvas exibidas")
        rotulo.setObjectName("ajuda")
        linha.addWidget(rotulo)
        self.caixas = {}
        for nome, cor in series:
            caixa = QCheckBox(nome)
            caixa.setChecked(True)
            caixa.setStyleSheet(
                f"QCheckBox{{color:{QColor(cor).name()};font-weight:600}}"
                f"QCheckBox::indicator{{width:13px;height:13px;border-radius:3px;"
                f"border:1.5px solid {QColor(cor).name()}}}"
                f"QCheckBox::indicator:checked{{background:{QColor(cor).name()}}}"
            )
            caixa.toggled.connect(self._emitir)
            self.caixas[nome] = caixa
            linha.addWidget(caixa)
        linha.addStretch()

    def selecionadas(self):
        return [nome for nome, caixa in self.caixas.items() if caixa.isChecked()]

    def _emitir(self):
        selecionadas = self.selecionadas()
        if not selecionadas:
            # Nunca deixar o grafico vazio: reativa a ultima curva desmarcada.
            remetente = self.sender()
            if remetente is not None:
                remetente.blockSignals(True)
                remetente.setChecked(True)
                remetente.blockSignals(False)
            selecionadas = self.selecionadas()
        self.alterado.emit(selecionadas)


# --------------------------------------------------------------------------
# Curvas tipicas por tipo de dia
# --------------------------------------------------------------------------
COLUNAS_RESUMO = ("Tipo de dia", "Dias", "Energia média/dia (kWh)",
                  "Pico médio (kW)", "Mínimo médio (kW)", "Fator de carga")

EXPLICACAO_TIPICAS = (
    "Cada curva é a média dos dias do mesmo tipo no período, e a faixa mostra o menor e o maior "
    "valor observado em cada intervalo de 15 minutos.\n\n"
    "Feriado e domingo compartilham o mesmo comportamento típico da BDGD (ambos são DO), por isso a "
    "carga sem GD tem a mesma forma nos dois; aparecem separados porque são dias diferentes do ano, "
    "o que muda a geração solar e, com ela, a curva líquida."
)


class PainelCurvasTipicas(QWidget):
    """Compara o comportamento medio de dia util, sabado, domingo e feriado."""

    grafico_alterado = Signal()

    def __init__(self, resultado, parent=None):
        super().__init__(parent)
        self.resultado = resultado
        self.tipos_dia = tuple(resultado.get("tipos_dia") or ())
        self.tipicas = {}
        self.titulos_y = {}
        fontes = [(resultado.get("series") or {}, "Potência (kW)")]
        if resultado.get("irradiancia"):
            fontes.append((resultado["irradiancia"]["series"], "Irradiância (W/m²)"))
        for series, titulo_y in fontes:
            for nome, vetor in series.items():
                try:
                    self.tipicas[nome] = curvas_tipicas(vetor, self.tipos_dia)
                except ErroBDGD:
                    continue
                self.titulos_y[nome] = titulo_y

        base = resultado["timestamps"][0].replace(hour=0, minute=0, second=0, microsecond=0)
        self.instantes = tuple(base + timedelta(minutes=15 * i) for i in range(96))
        self.view = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        explicacao = QLabel(
            "Média dos dias de cada tipo no período · feriado e domingo usam o mesmo "
            "comportamento típico da BDGD, mas são dias diferentes do ano."
        )
        explicacao.setObjectName("ajuda")
        explicacao.setWordWrap(True)
        explicacao.setToolTip(EXPLICACAO_TIPICAS)
        layout.addWidget(explicacao)

        controles = QHBoxLayout()
        controles.setSpacing(10)
        rotulo_serie = QLabel("Curva")
        rotulo_serie.setObjectName("ajuda")
        controles.addWidget(rotulo_serie)
        self.combo = QComboBox()
        for nome in self.tipicas:
            self.combo.addItem(nome, nome)
        self.combo.setMinimumWidth(260)
        self.combo.currentIndexChanged.connect(self.reconstruir)
        controles.addWidget(self.combo)
        self.faixa = QCheckBox("Faixa mín–máx")
        self.faixa.setChecked(True)
        self.faixa.setToolTip("Área sombreada entre o menor e o maior valor observado em cada intervalo.")
        self.faixa.toggled.connect(self.reconstruir)
        controles.addWidget(self.faixa)
        controles.addStretch()
        layout.addLayout(controles)

        self.caixas = {}
        linha_dias = QHBoxLayout()
        linha_dias.setSpacing(14)
        rotulo_dias = QLabel("Tipos de dia")
        rotulo_dias.setObjectName("ajuda")
        linha_dias.addWidget(rotulo_dias)
        primeira = next(iter(self.tipicas.values()), {})
        for rotulo, tipica in primeira.items():
            cor = theme.CORES_TIPO_DIA.get(rotulo, theme.CORES_SERIES[0])
            caixa = QCheckBox(f"{rotulo} ({tipica.dias})")
            caixa.setChecked(True)
            caixa.setStyleSheet(
                f"QCheckBox{{color:{cor};font-weight:600}}"
                f"QCheckBox::indicator{{width:13px;height:13px;border-radius:3px;border:1.5px solid {cor}}}"
                f"QCheckBox::indicator:checked{{background:{cor}}}"
            )
            caixa.toggled.connect(self.reconstruir)
            self.caixas[rotulo] = caixa
            linha_dias.addWidget(caixa)
        linha_dias.addStretch()
        layout.addLayout(linha_dias)

        moldura = QWidget()
        self.area_grafico = QVBoxLayout(moldura)
        self.area_grafico.setContentsMargins(0, 0, 0, 0)
        moldura.setMinimumHeight(290)

        self.resumo = QTableWidget(0, len(COLUNAS_RESUMO))
        self.resumo.setHorizontalHeaderLabels(COLUNAS_RESUMO)
        self.resumo.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.resumo.setAlternatingRowColors(True)
        self.resumo.setShowGrid(False)
        self.resumo.verticalHeader().setVisible(False)
        self.resumo.verticalHeader().setDefaultSectionSize(26)
        self.resumo.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.resumo.setMinimumHeight(84)

        # Divisor: o grafico fica com o espaco util e o resumo pode ser
        # reduzido pelo usuario quando ele quiser ver a curva maior.
        self.divisor = QSplitter(Qt.Orientation.Vertical)
        self.divisor.setChildrenCollapsible(False)
        self.divisor.setHandleWidth(6)
        self.divisor.addWidget(moldura)
        self.divisor.addWidget(self.resumo)
        self.divisor.setStretchFactor(0, 1)
        self.divisor.setStretchFactor(1, 0)
        layout.addWidget(self.divisor, 1)

        self.reconstruir()

    # -- construcao --------------------------------------------------------
    def rotulos_selecionados(self):
        escolhidos = [rotulo for rotulo, caixa in self.caixas.items() if caixa.isChecked()]
        return escolhidos or list(self.caixas)[:1]

    def reconstruir(self):
        if self.caixas and not any(caixa.isChecked() for caixa in self.caixas.values()):
            remetente = self.sender()
            caixa = remetente if remetente in self.caixas.values() else next(iter(self.caixas.values()))
            caixa.blockSignals(True)
            caixa.setChecked(True)
            caixa.blockSignals(False)
        nome = self.combo.currentData()
        if not nome or nome not in self.tipicas:
            return
        tipicas = self.tipicas[nome]
        rotulos = [r for r in self.rotulos_selecionados() if r in tipicas]
        series = {}
        faixas = {}
        cores = []
        for rotulo in rotulos:
            tipica = tipicas[rotulo]
            chave = f"{rotulo} · {tipica.dias} dia" + ("s" if tipica.dias != 1 else "")
            series[chave] = tipica.media_kw
            cores.append(theme.CORES_TIPO_DIA.get(rotulo, theme.CORES_SERIES[0]))
            if self.faixa.isChecked():
                faixas[chave] = (tipica.minimo_kw, tipica.maximo_kw)
        nova = construir_grafico(series, self.instantes, f"{nome} · dia típico",
                                 self.titulos_y.get(nome, "Potência (kW)"),
                                 cores=cores, faixas=faixas or None, modo_horario=True)
        nova.setMinimumHeight(280)
        if self.view is not None:
            nova._definir_janela(*self.view.janela())
            self.area_grafico.removeWidget(self.view)
            self.view.deleteLater()
        self.view = nova
        self.area_grafico.addWidget(nova)
        self._preencher_resumo(tipicas, rotulos)
        self.grafico_alterado.emit()

    def _preencher_resumo(self, tipicas, rotulos):
        self.resumo.setRowCount(len(rotulos))
        cabecalho = max(self.resumo.horizontalHeader().sizeHint().height(), 30)
        linha_px = self.resumo.verticalHeader().defaultSectionSize()
        self.resumo.setMaximumHeight(cabecalho + linha_px * len(rotulos) + 2 * self.resumo.frameWidth() + 6)
        for linha, rotulo in enumerate(rotulos):
            tipica = tipicas[rotulo]
            celulas = (rotulo, f"{tipica.dias}", formatar_br(tipica.energia_media_kwh, 3),
                       formatar_br(tipica.pico_medio_kw, 3), formatar_br(tipica.minimo_medio_kw, 3),
                       formatar_br(tipica.fator_carga, 3))
            for coluna, texto in enumerate(celulas):
                item = QTableWidgetItem(texto)
                if coluna:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setForeground(QBrush(QColor(theme.CORES_TIPO_DIA.get(rotulo, theme.TEXTO))))
                self.resumo.setItem(linha, coluna, item)
