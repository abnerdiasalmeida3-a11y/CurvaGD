"""Pagina Correcao de demanda: escolhe o circuito e calcula a demanda mensal.

E a janela da ferramenta CORRECAO_DEMANDA_BDGD (app_demanda.py) dentro do
CurvaGD, portada de PyQt6 para PySide6 e para a paleta em preto e branco.

Os arquivos (BDGD, NASA e ANEEL) e os parametros da simulacao solar vem da tela
Dados de entrada, e os alimentadores oferecidos sao os da regiao escolhida em
Regioes. O calculo e o mesmo das unidades consumidoras: passo de 15 min, PCHIP e
modelo PVSystem vetorizado.

Para cada UC ativa do alimentador, a demanda maxima de cada mes sai invertendo a
energia importada da rede (ver `app.correcao.demanda`). O resultado sao 12
colunas DEM_MAX_01..12; multiplicar a curva de qualquer recorte por elas da a
carga em kW daquele recorte, e e isso que o botao "Exportar curva do recorte"
grava.
"""

import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QDate, QDateTime, Qt, QThread, QTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDateTimeEdit,
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTableView, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ...calculo import gd, irradiancia
from ...correcao import alimentador, aneel, bdgd, demanda
from ...services.exportar_alimentador import gravar_quadro_xlsx
from .. import theme
from . import grafico_fases as grafico
from . import painel_estatisticas

# o QTextBrowser do tema e monoespacado (logs); o texto corrido usa a fonte normal
ESTILO_TEXTO = "QTextBrowser { font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }"

FORMATO_EDICAO = "dd/MM/yyyy HH:mm"
PASSO_PADRAO = 15          # o passo nativo da CRVCRG, o mesmo das curvas das UCs
METODO_PADRAO = "pchip"
MAXIMO_RECORTE = 500      # colunas que uma planilha de recorte ainda comporta
LARGURA_CODIGO = 190      # o COD_ID da BDGD e um hash de 64 caracteres


# A convencao do recorte nao e obvia olhando o formulario, e errar nela desloca a
# demanda -- entao ela aparece por tooltip nos dois campos e no grupo inteiro.
DICA_RECORTE = (
    "O periodo vai de \"De\" ate \"Ate\", com o fim EXCLUSIVO.\n\n"
    "Cada carimbo de tempo rotula o inicio de um bloco, entao 01/01 00:00 a "
    "01/02 00:00 sao os 2976 blocos de 15 min de janeiro fechado, terminando em "
    "31/01 23:45. Assim dezembro e janeiro se encaixam sem repetir a virada."
)

# Sem cor nem fonte fixa: o dialogo segue a paleta do tema.
SOBRE = r"""
<h3>Correcao de demanda da BDGD</h3>
<p>Para cada unidade consumidora ativa de um alimentador, esta ferramenta devolve
<b>doze demandas maximas</b>, uma por mes. A BDGD registra a energia mensal da UC
(<code>ENE_01..12</code>) e nao a demanda; aqui o caminho e o inverso.</p>

<h4>Como usar</h4>
<ol>
<li><b>Dados de entrada</b>: a BDGD, o CSV horario da NASA POWER e a base tecnica
de GD fotovoltaica da ANEEL sao escolhidos uma vez, na primeira tela do CurvaGD,
e valem aqui tambem.</li>
<li><b>Regioes</b>: o municipio, a subestacao e o alimentador escolhidos la
definem os alimentadores oferecidos aqui. Com um alimentador escolhido, as UCs
dele sao carregadas ao abrir esta tela.</li>
<li><b>Calcular demandas</b>: preenche a tabela com <code>DEM_MAX_01..12</code>.
Um alimentador tipico leva poucos segundos.</li>
<li><b>Exportar</b>: a tabela inteira em CSV ou XLSX, ou a curva de um recorte.</li>
</ol>

<h4>A BDGD</h4>
<p>O .gdb nao tem indice de atributo em tabela nenhuma, e cada consulta seria uma
varredura de 40 s nos 2,1 milhoes de registros da UCBT. Por isso a importacao
monta um indice de alimentador para FID, guardado em <code>workspace\cache_bdgd</code>
com uns 4 MB. Dali em diante um alimentador carrega em 0,2 s.</p>

<h4>A conta</h4>
<p>Mes a mes, procura-se a demanda maxima que reproduz exatamente a energia que a
UC puxou da rede:</p>
<p align="center"><code>ENE_mes = h * soma( max(Dmax*C - G, 0) )</code></p>
<p><code>C</code> e a curva de carga em pu do mes, montada com os perfis DU/SA/DO
da CRVCRG encaixados no calendario; <code>G</code> e a geracao fotovoltaica em kW,
simulada pelo modelo PVSystem vetorizado com a irradiancia da NASA; <code>h</code> e o passo em horas. Numa UC sem
geracao a conta fecha direto, <code>Dmax = ENE / (h * somaC)</code>.</p>
<p>A curva e normalizada pelo <b>maximo do ano</b>, e nao do recorte. E o que faz
<code>DEM_MAX_mm</code> ser literalmente o pico daquele mes, e o que permite
multiplicar a curva de qualquer pedaco por ele.</p>

<h4>A curva do recorte</h4>
<p>As doze colunas sao doze numeros; o grupo <b>Recorte</b> devolve isso como
curva no tempo. Ele monta o comportamento em pu do periodo pedido e multiplica
cada ponto pela demanda maxima <b>do mes daquele ponto</b>, entao um recorte que
cruza a virada do mes tambem sai certo.</p>
<p>Exige demandas ja calculadas e UCs <b>selecionadas na tabela</b> (limite de
500; use o filtro para reduzir). O CSV tem uma coluna de instante e uma coluna em
kW por UC. O campo <b>Ate</b> e exclusivo: 01/01 a 16/01 sao 15 dias cheios,
1440 blocos de 15 min, o ultimo comecando em 15/01 23:45.</p>

<h4>A tabela</h4>
<p>A coluna <code>STATUS</code> diz de onde veio a potencia da geracao:</p>
<ul>
<li><code>SEM_GD</code>: a UC nao tem <code>CEG_GD</code> na BDGD.</li>
<li><code>COM_GD</code>: potencia dos modulos e dos inversores casada na ANEEL.</li>
<li><code>GD_POT_UGBT</code>: nao casou na ANEEL; o kVA veio da UGBT/UGMT e o kW
saiu da razao de reserva. Estimativa, nao medida. Como esse campo as vezes vem
em W, acima do teto legal (75 kW em BT, 5 MW em MT) ele e dividido por mil ou,
se nem assim couber, descartado.</li>
<li><code>GD_SEM_POTENCIA</code>: tem <code>CEG_GD</code> mas nenhuma potencia em
lugar nenhum; tratada como consumidor puro.</li>
<li><code>SEM_CURVA</code>: o <code>TIP_CC</code> da UC nao existe na CRVCRG; a
linha sai sem demanda.</li>
</ul>
<p>Uma UC pode aparecer em <b>duas linhas</b>, uma <code>UCBT</code> e uma
<code>UCMT</code> com o mesmo <code>COD_ID</code>: e a mesma unidade com dois
pontos de conexao, cada um com a sua curva de carga e a sua energia na BDGD. As
duas sao calculadas separadamente, e o resumo do circuito diz quantas sao.
Somar as duas seria juntar energias de curvas diferentes.</p>
<p><b>Incluir diagnostico</b> acrescenta, por mes, o <code>ENE</code> lido, a
energia importada recalculada com a demanda achada (tem de bater com o
<code>ENE</code>) e a energia exportada prevista.</p>

<h4>Estatisticas</h4>
<p>Ha duas abas. <b>Demanda</b> e o painel do circuito com a tabela de sempre.
<b>Estatisticas</b>, na largura toda, mostra o perfil das cargas dos alimentadores
da regiao escolhida (todos, sem regiao), uma linha cada: a participacao das classes (residencial, comercial,
industrial, rural e outras), das ligacoes mono, bi e trifasicas, e das UCs com
GD. Clicar no cabecalho de uma coluna ordena por ela -- em <b>Indust.</b>, os
mais industriais vao para o topo. Selecionando um alimentador, aparecem embaixo
todas as classes e as ligacoes, com quantidade e participacao; o duplo clique o
leva para a aba Demanda.</p>
<p>A participacao pode ser pela <b>quantidade de UCs</b> ou pela <b>energia
anual</b>, e as duas contam historias diferentes: poucas UCs industriais podem
responder pela maior parte da energia. <b>Minimo de UCs</b> esconde
alimentadores pequenos, onde uma unica carga ja vira 100% de uma classe.</p>
<p>A classe sai do prefixo da subclasse (CLAS_SUB), pelo dominio do Modulo 10 do
PRODIST. A contagem nao calcula demanda nenhuma: na primeira vez leva uns 7 s no
.gpkg e um minuto no .gdb, e depois vem do cache.</p>

<h4>A curva do alimentador</h4>
<p>Depois de calcular, <b>Curva do alimentador</b> soma carga e geracao de todas
as UCs numa curva anual por fase, sem perdas, e mede o desequilibrio entre as
fases. Serve para escolher os alimentadores que valem um estudo de
desequilibrio.</p>
<p>A letra de fase de uma UC de BT e do <b>secundario</b> do trafo, nao do
alimentador. Por isso uma UC de BT num trafo mono ou bifasico vai para as fases
do <b>primario</b>; num trafo trifasico usa a propria letra (aproximacao). UCs
de MT usam o proprio FAS_CON. A geracao sai pelas fases da UG quando elas cabem
nas da UC; senao, pelas da UC, e isso fica no relatorio. UGs sem UC no
alimentador, como uma PCH, ficam de fora.</p>
<p>A janela tem duas abas. <b>Grafico</b> mostra a potencia de cada fase e o
total trifasico ao longo do ano, com seletor entre a curva liquida, so carga e so
geracao. Roda do mouse da zoom no tempo (com Ctrl, no eixo vertical), arrastar
desloca, um clique fixa o cursor de leitura com o valor das quatro series, duplo
clique volta ao ano inteiro, e clicar na legenda esconde ou mostra uma serie.
<b>Relatorio</b> traz os indicadores e como as fases foram atribuidas.</p>
<p>O indicador para comparar alimentadores e o <b>P95</b>: o desequilibrio que
so e superado em 5% do ano, em kW e relativo a media das fases. A exportacao
traz a curva, os indicadores e a lista de UCs que cairam em algum fallback.</p>
<p>E uma curva <b>esperada</b>, sem variacao dentro de uma classe de consumo,
sem perdas e sem iluminacao publica, e sem medicao de referencia: a energia de
entrada da CTMT nao fecha com a soma das UCs.</p>

<h4>Parametros</h4>
<ul>
<li><b>Passo</b>: 15 min, o nativo da CRVCRG, com a irradiancia horaria da NASA
reamostrada por PCHIP -- o mesmo calculo das curvas das unidades consumidoras.</li>
<li><b>Simulacao solar</b>: modelo PVSystem vetorizado, com a irradiancia-base, o cut-in e a razao
kW/kVA de reserva da tela Dados de entrada. Os defaults (1,0, 2% e 1,25) sao os
fisicamente corretos.</li>
<li><b>kW/kVA reserva</b>: so entra quando a GD nao aparece na base da ANEEL.</li>
</ul>

<h4>O que observar</h4>
<ul>
<li>A curva tipica da CRVCRG pode nao representar a UC. A <code>MT-Tipo2</code>,
por exemplo, tem fator de carga 0,95 e achata consumidores de media tensao.</li>
<li>O <code>DEM_01..12</code> medido que a UCMT traz <b>nao</b> entra no calculo:
a demanda daqui se baseia inteiramente no <code>ENE</code> e nas curvas.</li>
<li>Uma UC com <code>ENE</code> zero no mes sai com demanda zero naquele mes.</li>
<li>Nenhum arquivo de entrada e modificado: o GeoPackage e aberto somente para
leitura e o .gdb so e lido.</li>
</ul>
"""


def _qdatetime(instante):
    instante = pd.Timestamp(instante)
    return QDateTime(QDate(instante.year, instante.month, instante.day),
                     QTime(instante.hour, instante.minute))


def _timestamp(qdatetime):
    data, hora = qdatetime.date(), qdatetime.time()
    return pd.Timestamp(data.year(), data.month(), data.day(), hora.hour(), hora.minute())


class ModeloResultado(QAbstractTableModel):
    """Tabela sobre um DataFrame.

    Um QTableWidget guarda um objeto por celula: com 13 mil UCs e 20 colunas ele
    trava so de ser preenchido. Aqui a tabela le o DataFrame sob demanda, entao
    so as linhas visiveis custam alguma coisa.
    """

    def __init__(self, df=None):
        super().__init__()
        self.df = pd.DataFrame() if df is None else df.reset_index(drop=True)

    def trocar(self, df):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.endResetModel()

    def rowCount(self, pai=None):
        return 0 if (pai is not None and pai.isValid()) else len(self.df)

    def columnCount(self, pai=None):
        return 0 if (pai is not None and pai.isValid()) else len(self.df.columns)

    def data(self, indice, papel=Qt.ItemDataRole.DisplayRole):
        if not indice.isValid():
            return None
        valor = self.df.iat[indice.row(), indice.column()]
        if papel == Qt.ItemDataRole.DisplayRole:
            if isinstance(valor, (float, np.floating)):
                return "" if not np.isfinite(valor) else f"{valor:.3f}"
            return str(valor)
        if papel == Qt.ItemDataRole.TextAlignmentRole and isinstance(
            valor, (int, float, np.number)
        ):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, secao, orientacao, papel=Qt.ItemDataRole.DisplayRole):
        if papel != Qt.ItemDataRole.DisplayRole:
            return None
        if orientacao == Qt.Orientation.Horizontal:
            return str(self.df.columns[secao])
        return str(secao + 1)

    def sort(self, coluna, ordem=Qt.SortOrder.AscendingOrder):
        if self.df.empty:
            return
        nome = self.df.columns[coluna]
        self.beginResetModel()
        self.df = self.df.sort_values(
            nome, ascending=ordem == Qt.SortOrder.AscendingOrder, kind="stable"
        ).reset_index(drop=True)
        self.endResetModel()


class Trabalho(QThread):
    """Roda uma funcao pesada fora da thread da interface.

    A funcao recebe um `avisar(etapa, feito, total)` para alimentar a barra de
    progresso e devolve o que quiser; a janela so toca em widget nos slots.
    """

    progresso = Signal(str, int, int)
    concluido = Signal(object)
    falhou = Signal(str)

    def __init__(self, funcao, pai=None):
        super().__init__(pai)
        self._funcao = funcao

    def run(self):
        try:
            self.concluido.emit(self._funcao(self._avisar))
        except Exception as erro:
            traceback.print_exc()
            self.falhou.emit(str(erro))

    def _avisar(self, etapa, feito=0, total=0):
        self.progresso.emit(str(etapa), int(feito), int(total))


class PaginaCorrecao(QWidget):
    """A ferramenta CORRECAO_DEMANDA_BDGD como uma pagina do CurvaGD."""

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self.service = service
        workspace = Path(service.workspace) if service is not None else Path.home() / ".curvagd"
        self.pasta_trabalho = workspace / "correcao_demanda"
        bdgd.definir_pasta_cache(workspace / "cache_bdgd")

        self.fonte = None        # FonteGeoPackage ou FonteFileGDB
        self.banco = {}          # curvas de carga da CRVCRG
        self.serie_nasa = None   # serie horaria lida do CSV
        self.ano = None          # ano-base da importacao
        self.parametros = {"performance_ratio": gd.PERFORMANCE_RATIO_PADRAO,
                           "cut_in_percentual": gd.CUTIN_PCT_PADRAO, "razao_kw_kva": aneel.RAZAO_PADRAO}
        self.ucs = None          # UCs do alimentador carregado
        self.potencias = None    # potencia da GD de cada UC
        self.resultado = None    # tabela de demanda calculada
        self.passo_calculado = PASSO_PADRAO
        self.ano_calculado = None
        self._trabalho = None
        self._sobre = None       # dialogo do menu Ajuda, criado sob demanda
        self.ctmt_carregado = None  # alimentador das UCs em memoria
        self.insumos = None      # curvas e geracao montadas pelo calculo
        self.sintese = None      # (curvas, indicadores, relatorio, ctmt)
        self._dialogo_curva = None
        self.alimentadores = None        # CTMT da base aberta
        self.estatisticas_brutas = None  # contagem por alimentador, sob demanda
        self._estatisticas_pendentes = False
        self._precisa_abrir = False      # a BDGD do projeto ainda nao foi aberta
        self._alimentadores_regiao = None  # [(rotulo, codigo)] da regiao; None = todos
        self._alimentador_regiao = ""

        self.modelo = ModeloResultado()

        self.status = QLabel()
        self.status.setObjectName("ajuda")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 14)
        layout.setSpacing(10)
        titulo = QLabel("Correção de demanda da BDGD")
        titulo.setObjectName("tituloPagina")
        sobre = QPushButton("Sobre o método")
        sobre.clicked.connect(self.mostrar_sobre)
        cabecalho = QHBoxLayout()
        cabecalho.addWidget(titulo)
        cabecalho.addStretch(1)
        cabecalho.addWidget(sobre)
        layout.addLayout(cabecalho)
        self.rotulo_regiao = QLabel("Região: todas")
        self.rotulo_regiao.setObjectName("ajuda")
        self.rotulo_regiao.setWordWrap(True)
        layout.addWidget(self.rotulo_regiao)
        self.rotulo_dados = QLabel("Importe os dados em ‘Dados de entrada’.")
        self.rotulo_dados.setObjectName("ajuda")
        self.rotulo_dados.setWordWrap(True)
        layout.addWidget(self.rotulo_dados)
        layout.addWidget(self._montar_abas(), 1)
        layout.addWidget(self.status)

        self._status("Importe os dados em ‘Dados de entrada’ e escolha a região em ‘Regiões’.")

    def showEvent(self, evento):
        """Abre a BDGD so quando a pagina aparece: um .gdb leva alguns segundos."""
        super().showEvent(evento)
        if self._precisa_abrir:
            self._abrir_projeto()
        self._carregar_regiao_automaticamente()

    def _status(self, texto, *_):
        self.status.setText(texto)

    def statusBar(self):
        """Compatibilidade com o codigo da janela original: a barra e o rotulo de status."""
        return self

    def showMessage(self, texto, *_):
        self._status(texto)

    # ------------------------------------------------------- projeto e regiao

    def definir_projeto(self):
        """A base importada mudou (ou o programa abriu): recomeca do zero."""
        if self.ocupada():
            self._trabalho.wait(3000)
        if self.fonte is not None:
            self.fonte.fechar()
        self.fonte, self.banco, self.serie_nasa, self.alimentadores = None, {}, None, None
        self.estatisticas_brutas = None
        self.painel_estatisticas.mensagem("Abra a aba para contar as UCs da base.")
        self.seletor_ctmt.clear()
        self.seletor_ctmt.setEnabled(False)
        self.botao_carregar.setEnabled(False)
        self._limpar_resultado()
        self.ctmt_carregado = None
        base = self.service is not None and self.service.base_carregada
        self._precisa_abrir = base
        if base:
            projeto = self.service.projeto
            self.ano = int(projeto.ano)
            self.definir_parametros(self.service.parametros_solares())
            self._ajustar_recorte()
            self._status("A BDGD é aberta quando esta tela aparece.")
            if self.isVisible():
                self._abrir_projeto()
        else:
            self.rotulo_dados.setText("Importe os dados em ‘Dados de entrada’.")

    def definir_parametros(self, parametros):
        self.parametros = {**self.parametros, **parametros}
        self._atualizar_rotulo_dados()

    def _atualizar_rotulo_dados(self):
        if self.service is None or not self.service.base_carregada:
            return
        projeto = self.service.projeto
        p = self.parametros
        numero = lambda valor, casas: f"{valor:.{casas}f}".replace(".", ",")
        self.rotulo_dados.setText(
            f"BDGD: {Path(projeto.bdgd).name} · Ano {projeto.ano} · passo de 15 min · PCHIP · "
            f"modelo PVSystem vetorizado (irradiância-base {numero(p['performance_ratio'], 2)}, "
            f"cut-in {numero(p['cut_in_percentual'], 1)}%, kW/kVA de reserva {numero(p['razao_kw_kva'], 2)}) · "
            f"ANEEL: {projeto.aneel_fonte or 'POT_INST das UGs'}")

    def definir_regiao(self, descricao, alimentadores, selecionado=""):
        """Alimentadores da regiao escolhida em Regioes (codigo e rotulo)."""
        self.rotulo_regiao.setText(f"Região: {descricao}")
        self._alimentadores_regiao = list(alimentadores)
        self._alimentador_regiao = selecionado or ""
        self._preencher_circuitos()
        if self.estatisticas_brutas is not None:
            self.painel_estatisticas.definir(self._estatisticas_da_regiao(), self.alimentadores)
        if self.isVisible():
            self._carregar_regiao_automaticamente()

    def _preencher_circuitos(self):
        atual = self._alimentador_regiao or self._ctmt_escolhido() if self.seletor_ctmt.count() else self._alimentador_regiao
        self.seletor_ctmt.blockSignals(True)
        self.seletor_ctmt.clear()
        if self._alimentadores_regiao is not None:
            itens = self._alimentadores_regiao
        elif self.alimentadores is not None:
            itens = [((f"{linha.COD_ID} · {linha.NOME}" if linha.NOME else linha.COD_ID), linha.COD_ID)
                     for linha in self.alimentadores.itertuples()]
        else:
            itens = []
        for rotulo, codigo in itens:
            self.seletor_ctmt.addItem(rotulo, codigo)
        completador = QCompleter([self.seletor_ctmt.itemText(i) for i in range(self.seletor_ctmt.count())], self)
        completador.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completador.setFilterMode(Qt.MatchFlag.MatchContains)
        self.seletor_ctmt.setCompleter(completador)
        posicao = self.seletor_ctmt.findData(atual) if atual else -1
        self.seletor_ctmt.setCurrentIndex(posicao if posicao >= 0 else (0 if itens else -1))
        self.seletor_ctmt.blockSignals(False)
        pronto = self.fonte is not None and bool(itens)
        self.seletor_ctmt.setEnabled(pronto and not self.ocupada())
        self.botao_carregar.setEnabled(pronto and not self.ocupada())

    def _carregar_regiao_automaticamente(self):
        """Com um alimentador escolhido em Regioes, suas UCs ja entram carregadas."""
        alvo = self._alimentador_regiao
        if (alvo and self.fonte is not None and not self.ocupada() and alvo != self.ctmt_carregado
                and self.seletor_ctmt.findData(alvo) >= 0):
            self.seletor_ctmt.setCurrentIndex(self.seletor_ctmt.findData(alvo))
            self.carregar_ucs()

    def _estatisticas_da_regiao(self):
        brutas = self.estatisticas_brutas
        if brutas is None or self._alimentadores_regiao is None:
            return brutas
        codigos = {codigo for _, codigo in self._alimentadores_regiao}
        return brutas[brutas["CTMT"].isin(codigos)]

    # ----------------------------------------------------------------- layout

    def _montar_painel(self):
        # ---- circuito ----
        self.seletor_ctmt = QComboBox()
        self.seletor_ctmt.setEditable(True)
        self.seletor_ctmt.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.seletor_ctmt.setToolTip("Alimentadores ativos da região escolhida em Regiões (tabela CTMT).")
        self.seletor_ctmt.setEnabled(False)

        self.botao_carregar = QPushButton("Carregar UCs")
        self.botao_carregar.clicked.connect(self.carregar_ucs)
        self.botao_carregar.setEnabled(False)

        self.resumo_ucs = QLabel("Nenhum circuito carregado.")
        self.resumo_ucs.setWordWrap(True)

        circuito = QVBoxLayout()
        circuito.addWidget(self.seletor_ctmt)
        circuito.addWidget(self.botao_carregar)
        circuito.addWidget(self.resumo_ucs)
        grupo_circuito = QGroupBox("Circuito")
        grupo_circuito.setLayout(circuito)

        # ---- calculo ----
        self.marcar_diagnostico = QCheckBox("Incluir diagnostico")
        self.marcar_diagnostico.setToolTip(
            "Acrescenta, por mes, o ENE lido, a energia importada recalculada com a "
            "demanda achada (tem de bater com o ENE) e a energia exportada prevista."
        )

        self.botao_calcular = QPushButton("Calcular demandas")
        self.botao_calcular.setObjectName("primario")
        self.botao_calcular.clicked.connect(self.calcular)
        self.botao_calcular.setEnabled(False)

        self.barra = QProgressBar()
        self.barra.setVisible(False)

        calculo = QVBoxLayout()
        calculo.addWidget(self.marcar_diagnostico)
        calculo.addWidget(self.botao_calcular)
        self.botao_curva = QPushButton("Curva do alimentador")
        self.botao_curva.setToolTip(
            "Soma a carga e a geracao de todas as UCs numa curva anual do "
            "alimentador, por fase, e calcula indicadores de desequilibrio."
        )
        self.botao_curva.clicked.connect(self.gerar_curva)
        self.botao_curva.setEnabled(False)
        calculo.addWidget(self.botao_curva)
        calculo.addWidget(self.barra)
        grupo_calculo = QGroupBox("Calculo")
        grupo_calculo.setLayout(calculo)

        # ---- recorte ----
        self.inicio = QDateTimeEdit()
        self.fim = QDateTimeEdit()
        for campo in (self.inicio, self.fim):
            campo.setCalendarPopup(True)
            campo.setDisplayFormat(FORMATO_EDICAO)
            campo.setToolTip(DICA_RECORTE)

        self.botao_recorte = QPushButton("Exportar curva do recorte")
        self.botao_recorte.setToolTip(
            "Grava a carga em kW das UCs selecionadas na tabela: a curva do recorte "
            "em pu vezes a demanda maxima do mes de cada ponto."
        )
        self.botao_recorte.clicked.connect(self.exportar_recorte)
        self.botao_recorte.setEnabled(False)

        recorte = QVBoxLayout()
        formulario_recorte = QFormLayout()
        formulario_recorte.addRow("De:", self.inicio)
        formulario_recorte.addRow("Ate:", self.fim)
        recorte.addLayout(formulario_recorte)
        recorte.addWidget(self.botao_recorte)
        grupo_recorte = QGroupBox("Recorte")
        grupo_recorte.setLayout(recorte)
        grupo_recorte.setToolTip(DICA_RECORTE)

        layout = QVBoxLayout()
        layout.addWidget(grupo_circuito)
        layout.addWidget(grupo_calculo)
        layout.addWidget(grupo_recorte)
        layout.addStretch(1)

        painel = QWidget()
        painel.setLayout(layout)
        painel.setMinimumWidth(300)
        painel.setMaximumWidth(460)
        return painel

    def _montar_abas(self):
        """Abaixo dos arquivos: a demanda de um circuito e o perfil de todos.

        A aba Demanda tem o painel do circuito e a tabela; a Estatisticas olha a
        base inteira, e ocupa a largura toda.
        """
        divisor = QSplitter(Qt.Orientation.Horizontal)
        divisor.addWidget(self._montar_painel())
        divisor.addWidget(self._montar_tabela())
        divisor.setStretchFactor(0, 0)
        divisor.setStretchFactor(1, 1)
        divisor.setChildrenCollapsible(False)
        divisor.setSizes([340, 940])

        self.painel_estatisticas = painel_estatisticas.PainelEstatisticas()
        self.painel_estatisticas.escolhido.connect(self._escolher_alimentador)
        self.abas = QTabWidget()
        self.abas.addTab(divisor, "Demanda")
        self.abas.addTab(self.painel_estatisticas, "Estatisticas")
        self.abas.currentChanged.connect(self._ao_trocar_aba)
        return self.abas

    def _montar_tabela(self):
        self.campo_busca = QLineEdit()
        self.campo_busca.setPlaceholderText("Filtrar por COD_ID, CEG_GD, curva ou status")
        self.campo_busca.textChanged.connect(self._aplicar_filtro)

        self.tabela = QTableView()
        self.tabela.setModel(self.modelo)
        self.tabela.setSortingEnabled(True)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.verticalHeader().setDefaultSectionSize(24)
        self.tabela.verticalHeader().setVisible(False)
        cabecalho = self.tabela.horizontalHeader()
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # medir a largura pelas 13 mil linhas do maior alimentador trava a tela
        cabecalho.setResizeContentsPrecision(50)

        self.botao_csv = QPushButton("Exportar CSV")
        self.botao_csv.clicked.connect(lambda: self.exportar(".csv"))
        self.botao_csv.setEnabled(False)
        self.botao_xlsx = QPushButton("Exportar XLSX")
        self.botao_xlsx.clicked.connect(lambda: self.exportar(".xlsx"))
        self.botao_xlsx.setEnabled(False)
        self.contagem = QLabel("")

        rodape = QHBoxLayout()
        rodape.addWidget(self.contagem, 1)
        rodape.addWidget(self.botao_csv)
        rodape.addWidget(self.botao_xlsx)

        layout = QVBoxLayout()
        layout.addWidget(self.campo_busca)
        layout.addWidget(self.tabela, 1)
        layout.addLayout(rodape)

        caixa = QWidget()
        caixa.setLayout(layout)
        return caixa

    def mostrar_sobre(self):
        """Referencia rapida da ferramenta, numa janela que nao bloqueia o app.

        Nao modal e reaproveitada entre chamadas: da para deixar aberta ao lado
        enquanto se mexe na tabela, e clicar em Sobre de novo traz a mesma janela
        para a frente em vez de empilhar copias.
        """
        if self._sobre is None:
            dialogo = QDialog(self)
            dialogo.setStyleSheet(theme.QSS)
            dialogo.setWindowTitle("Sobre a correção de demanda")
            dialogo.resize(660, 620)

            texto = QTextBrowser()
            texto.setStyleSheet(ESTILO_TEXTO)
            texto.setHtml(SOBRE)
            texto.setOpenExternalLinks(False)

            botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            botoes.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
            botoes.rejected.connect(dialogo.close)

            layout = QVBoxLayout(dialogo)
            layout.addWidget(texto, 1)
            layout.addWidget(botoes)
            self._sobre = dialogo

        self._sobre.show()
        self._sobre.raise_()
        self._sobre.activateWindow()

    # ---------------------------------------------------------------- arquivos

    def _abrir_projeto(self):
        """Abre a BDGD e a irradiancia da importacao atual."""
        self._precisa_abrir = False
        if self.service is None or not self.service.base_carregada:
            return
        projeto = self.service.projeto
        self._atualizar_rotulo_dados()
        nasa = self.service.nasa_path()
        if nasa and Path(nasa).is_file():
            self.carregar_nasa(str(nasa))
        else:
            self._status("CSV da NASA não encontrado: importe os dados de novo.")
        caminho = projeto.bdgd
        if caminho and Path(caminho).exists() and Path(caminho).suffix.lower() in (".gdb", ".gpkg"):
            self.carregar_bdgd(caminho)
        else:
            self._status("A correção de demanda lê a pasta .gdb da BDGD, que não foi encontrada em "
                         f"{caminho or '(vazio)'}. Confira em ‘Dados de entrada’.")

    def _avisar_erro(self, titulo, erro):
        self._status(f"{titulo}: {erro}")

    def carregar_bdgd(self, caminho):
        """Abre a BDGD, le a CRVCRG e enche o seletor de alimentadores.

        Vale para os dois formatos: `bdgd.abrir` escolhe a fonte. Num .gdb, o
        indice por alimentador ja foi montado na importacao; se faltar, ele e
        montado no primeiro "Carregar UCs", na thread de trabalho.
        """
        try:
            fonte = bdgd.abrir(caminho)
            banco = fonte.banco_de_curvas()
            lista = fonte.alimentadores()
        except Exception as erro:
            self._avisar_erro("BDGD", erro)
            return

        if self.fonte is not None:
            self.fonte.fechar()
        self.fonte, self.banco = fonte, banco
        self.alimentadores = lista
        self.estatisticas_brutas = None
        self.painel_estatisticas.mensagem("Abra a aba para contar as UCs da base.")
        if self.abas.currentWidget() is self.painel_estatisticas:
            self._carregar_estatisticas()
        self._limpar_resultado()
        self._preencher_circuitos()
        self.statusBar().showMessage(
            f"BDGD aberta: {self.seletor_ctmt.count()} alimentadores na região, {len(banco)} curvas de carga.", 8000
        )

    def carregar_nasa(self, caminho):
        """Le a serie horaria da NASA importada."""
        try:
            serie, diagnostico = irradiancia.ler_csv_nasa(caminho)
        except Exception as erro:
            self._avisar_erro("Irradiancia", erro)
            return
        self.serie_nasa = serie
        self._ajustar_recorte()

    def _ajustar_recorte(self):
        """Deixa o recorte na primeira quinzena de janeiro do ano-base.

        O fim e 16/01 00:00, e nao 15/01 23:45: com o "Ate" exclusivo, sao os
        1440 blocos de 15 dias cheios.
        """
        ano = self.ano
        if ano is None:
            return
        self.inicio.setDateTime(_qdatetime(f"{ano}-01-01"))
        self.fim.setDateTime(_qdatetime(f"{ano}-01-16"))

    # ----------------------------------------------------------------- trabalho

    def _ocupado(self, ligado, etapa=""):
        for widget in (self.botao_carregar, self.botao_calcular, self.seletor_ctmt,
                       self.botao_csv, self.botao_xlsx, self.botao_recorte,
                       self.botao_curva):
            widget.setEnabled(not ligado and self._habilitado(widget))
        self.barra.setVisible(ligado)
        if ligado:
            self.barra.setRange(0, 0)
            self.statusBar().showMessage(etapa)
        elif self._estatisticas_pendentes:
            self._estatisticas_pendentes = False
            self._carregar_estatisticas()

    def _habilitado(self, widget):
        """Quem pode voltar a ficar ativo quando o trabalho termina."""
        if widget is self.botao_carregar or widget is self.seletor_ctmt:
            return self.fonte is not None
        if widget is self.botao_calcular:
            return self.ucs is not None
        return self.resultado is not None

    def _iniciar(self, funcao, ao_concluir, etapa):
        self._ocupado(True, etapa)
        self._trabalho = Trabalho(funcao, self)
        self._trabalho.progresso.connect(self._mostrar_progresso)
        self._trabalho.concluido.connect(ao_concluir)
        self._trabalho.falhou.connect(self._mostrar_erro)
        self._trabalho.finished.connect(lambda: self._ocupado(False))
        self._trabalho.start()

    def _mostrar_progresso(self, etapa, feito, total):
        if total > 0:
            self.barra.setRange(0, total)
            self.barra.setValue(feito)
        else:
            self.barra.setRange(0, 0)
        self.statusBar().showMessage(f"{etapa}: {feito}" + (f"/{total}" if total else ""))

    def _mostrar_erro(self, mensagem):
        QMessageBox.critical(self, "Erro", mensagem)
        self.statusBar().showMessage("Falhou.", 8000)

    # ------------------------------------------------------------------- fluxo

    def _ctmt_escolhido(self):
        """Codigo do alimentador, aceitando texto digitado no combo editavel."""
        texto = self.seletor_ctmt.currentText().strip()
        posicao = self.seletor_ctmt.findText(texto)
        if posicao >= 0:
            return self.seletor_ctmt.itemData(posicao)
        return texto.split(" ")[0] or None

    def carregar_ucs(self):
        """Le as UCs do alimentador e resolve a potencia de cada GD."""
        ctmt = self._ctmt_escolhido()
        if not ctmt:
            QMessageBox.warning(self, "Circuito", "Escolha um alimentador.")
            return
        workspace = self.service.workspace if self.service is not None else self.pasta_trabalho

        fonte = self.fonte
        razao = float(self.parametros["razao_kw_kva"])

        def trabalho(avisar):
            # num .gdb a primeira chamada monta o indice CTMT -> FIDs, uns 25 s;
            # nas seguintes e nos GeoPackages nao custa nada
            fonte.preparar(avisar)
            avisar("lendo a BDGD")
            ucs, resumo = fonte.unidades(ctmt)
            if ucs.empty:
                raise ValueError(f"O alimentador {ctmt} nao tem UC ativa.")
            ugs = fonte.geradoras(ctmt)
            avisar("potencias pela base da ANEEL importada")
            try:
                base = aneel.potencias_copia_local(workspace, ucs["CEG_GD"])
            except ValueError:
                # sem base da ANEEL, as GDs caem no POT_INST das UGs, como nas UCs
                base = aneel.potencias([], [])
            potencias = aneel.potencias_por_uc(ucs, base, ugs, razao)
            return ucs, resumo, potencias, ctmt

        self._limpar_resultado()
        self._iniciar(trabalho, self._ao_carregar_ucs, "lendo a BDGD")

    def _ao_carregar_ucs(self, saida):
        self.ucs, resumo, self.potencias, self.ctmt_carregado = saida
        contagem = self.potencias["STATUS"].value_counts()
        partes = [f"{resumo['total']} UCs", f"{resumo['bt']} BT / {resumo['mt']} MT",
                  f"{int(contagem.get(aneel.STATUS_ANEEL, 0))} com GD na ANEEL"]
        if contagem.get(aneel.STATUS_UGBT, 0):
            partes.append(f"{int(contagem[aneel.STATUS_UGBT])} com potencia da UG")
        if contagem.get(aneel.STATUS_SEM_POTENCIA, 0):
            partes.append(f"{int(contagem[aneel.STATUS_SEM_POTENCIA])} sem potencia")
        if resumo["repetidos"]:
            partes.append(f"{resumo['repetidos']} linha(s) repetida(s) consolidada(s)")
        if resumo["nos_dois"]:
            # a mesma UC com um ponto de conexao em cada tabela: sao duas linhas
            # na saida, cada uma com a sua curva e a sua energia
            partes.append(f"{resumo['nos_dois']} UC(s) com ponto BT e MT")
        self.resumo_ucs.setText(" - ".join(partes))
        self.statusBar().showMessage("UCs carregadas. Agora calcule as demandas.", 8000)

    def calcular(self):
        """Roda o motor mensal sobre as UCs carregadas."""
        if self.ucs is None or self.serie_nasa is None or self.ano is None:
            QMessageBox.warning(self, "Calculo", "Carregue as UCs e importe o CSV da NASA primeiro.")
            return

        ano = int(self.ano)
        passo, metodo = PASSO_PADRAO, METODO_PADRAO
        pr = float(self.parametros["performance_ratio"])
        cut_in = float(self.parametros["cut_in_percentual"])
        diagnostico = self.marcar_diagnostico.isChecked()
        ucs, potencias, banco = self.ucs, self.potencias, self.banco
        serie = self.serie_nasa

        def trabalho(avisar):
            avisar("preparando a irradiancia")
            irrad = irradiancia.reamostrar(
                irradiancia.serie_ano(serie, ano), passo, metodo, passo_origem=60
            )
            return demanda.calcular(
                ucs, potencias, banco, irrad, ano, passo, pr, cut_in,
                diagnostico=diagnostico, progresso=avisar,
                devolver_insumos=True,
            ) + (ano, passo)

        self._iniciar(trabalho, self._ao_calcular, "calculando")

    def _ao_calcular(self, saida):
        (self.resultado, tempos, self.insumos,
         self.ano_calculado, self.passo_calculado) = saida
        self.sintese = None
        self._aplicar_filtro()
        for botao in (self.botao_csv, self.botao_xlsx, self.botao_recorte,
                      self.botao_curva):
            botao.setEnabled(True)
        self.statusBar().showMessage(
            f"{len(self.resultado)} UCs calculadas em {tempos['total']:.1f}s "
            f"(geracao {tempos['geracao']:.1f}s, demanda {tempos['demanda']:.1f}s).", 15000
        )

    def _limpar_resultado(self):
        self.ucs = self.potencias = self.resultado = None
        self.insumos = self.sintese = None
        self.modelo.trocar(pd.DataFrame())
        self.contagem.setText("")
        self.resumo_ucs.setText("Nenhum circuito carregado.")
        for botao in (self.botao_csv, self.botao_xlsx, self.botao_recorte,
                      self.botao_curva):
            botao.setEnabled(False)
        self.botao_calcular.setEnabled(False)

    # ------------------------------------------------------------------ tabela

    def _aplicar_filtro(self):
        if self.resultado is None:
            return
        texto = self.campo_busca.text().strip().lower()
        df = self.resultado
        if texto:
            colunas = ["COD_ID", "CEG_GD", "TIP_CC", "STATUS", "MUN"]
            casa = np.zeros(len(df), dtype=bool)
            for coluna in colunas:
                casa |= df[coluna].astype(str).str.lower().str.contains(texto, regex=False)
            df = df[casa]
        self.modelo.trocar(df)
        self.tabela.resizeColumnsToContents()
        if self.modelo.columnCount():
            self.tabela.setColumnWidth(0, LARGURA_CODIGO)
        self.contagem.setText(f"{len(df)} de {len(self.resultado)} UCs")

    def _selecionadas(self):
        """DataFrame das linhas marcadas na tabela, na ordem em que aparecem."""
        linhas = sorted({indice.row() for indice in self.tabela.selectionModel().selectedRows()})
        return self.modelo.df.iloc[linhas] if linhas else self.modelo.df.iloc[:0]

    # --------------------------------------------------------------- exportar

    def exportar(self, extensao):
        if self.resultado is None:
            return
        filtro = "CSV (*.csv)" if extensao == ".csv" else "Excel (*.xlsx)"
        sugestao = f"DEMANDA_{self._ctmt_escolhido()}{extensao}"
        caminho, _ = QFileDialog.getSaveFileName(self, "Salvar", sugestao, filtro)
        if not caminho:
            return
        try:
            if extensao == ".csv":
                self.resultado.to_csv(caminho, index=False)
            else:
                gravar_quadro_xlsx(self.resultado, caminho, "DEMANDA")
        except Exception as erro:
            QMessageBox.critical(self, "Exportar", str(erro))
            return
        self.statusBar().showMessage(f"Gravado: {caminho}", 10000)

    def exportar_recorte(self):
        """Carga em kW das UCs selecionadas no recorte pedido.

        E o uso final da ferramenta: a curva do periodo em pu, na base fixa do
        ano, vezes a demanda maxima do mes de cada ponto.
        """
        if self.resultado is None:
            return
        escolhidas = self._selecionadas()
        if escolhidas.empty:
            QMessageBox.warning(
                self, "Recorte",
                "Selecione na tabela as UCs que devem entrar no recorte."
            )
            return
        if len(escolhidas) > MAXIMO_RECORTE:
            QMessageBox.warning(
                self, "Recorte",
                f"{len(escolhidas)} UCs selecionadas; o limite e {MAXIMO_RECORTE}. "
                "Use o filtro para reduzir a selecao."
            )
            return

        inicio = _timestamp(self.inicio.dateTime())
        fim = _timestamp(self.fim.dateTime())
        colunas = {}
        ignoradas = 0
        for linha in escolhidas.itertuples():
            codigo = getattr(linha, "TIP_CC", "")
            if codigo not in self.banco:
                ignoradas += 1
                continue
            demandas = [getattr(linha, coluna) for coluna in demanda.COLUNAS_DEMANDA]
            if not np.isfinite(demandas).all():
                ignoradas += 1
                continue
            try:
                colunas[linha.COD_ID] = demanda.curva_em_kw(
                    self.banco, codigo, demandas, inicio, fim, self.passo_calculado
                )
            except ValueError as erro:
                QMessageBox.critical(self, "Recorte", str(erro))
                return

        if not colunas:
            QMessageBox.warning(self, "Recorte", "Nenhuma das UCs selecionadas tem curva.")
            return

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Salvar curva do recorte",
            f"RECORTE_{self._ctmt_escolhido()}.csv", "CSV (*.csv)"
        )
        if not caminho:
            return
        tabela = pd.DataFrame(colunas).rename_axis("INSTANTE")
        try:
            tabela.to_csv(caminho)
        except Exception as erro:
            QMessageBox.critical(self, "Recorte", str(erro))
            return

        # a cobertura vai do inicio do primeiro bloco ao FIM do ultimo: e o que
        # torna visivel que o "Ate" e exclusivo
        cobertura = tabela.index[-1] + pd.Timedelta(minutes=self.passo_calculado)
        aviso = f", {ignoradas} sem curva ou sem demanda" if ignoradas else ""
        self.statusBar().showMessage(
            f"Recorte de {len(colunas)} UCs gravado em {caminho} "
            f"({len(tabela)} blocos de {self.passo_calculado} min, "
            f"{tabela.index[0]:%d/%m %H:%M} a {cobertura:%d/%m %H:%M}{aviso})", 15000
        )

    # ------------------------------------------------------------ estatisticas

    def _ao_trocar_aba(self, _indice):
        if self.abas.currentWidget() is self.painel_estatisticas:
            self._carregar_estatisticas()

    def _carregar_estatisticas(self):
        """Conta as UCs da base inteira, uma vez por BDGD aberta.

        Na primeira vez leva uns 7 s no .gpkg e um minuto no .gdb; depois vem do
        cache. Se outro trabalho estiver rodando, espera ele terminar.
        """
        if self.fonte is None or self.estatisticas_brutas is not None:
            return
        if self._trabalho is not None and self._trabalho.isRunning():
            self._estatisticas_pendentes = True
            self.painel_estatisticas.mensagem("Esperando o trabalho em andamento terminar...")
            return
        fonte = self.fonte
        self.painel_estatisticas.mensagem(
            "Contando as UCs da base: na primeira vez leva de alguns segundos a um minuto.")
        self._iniciar(lambda avisar: fonte.estatisticas(avisar),
                      self._ao_carregar_estatisticas, "contando as UCs da base")

    def _ao_carregar_estatisticas(self, brutas):
        self.estatisticas_brutas = brutas
        regiao = self._estatisticas_da_regiao()
        self.painel_estatisticas.definir(regiao, self.alimentadores)
        self.statusBar().showMessage(
            f"Estatisticas de {regiao.loc[regiao['CTMT'] != '', 'CTMT'].nunique()} "
            "alimentadores da região prontas.", 8000)

    def _escolher_alimentador(self, ctmt):
        """Duplo clique nas estatisticas: seleciona o alimentador na aba Demanda."""
        posicao = self.seletor_ctmt.findData(ctmt)
        if posicao < 0:
            self.statusBar().showMessage(f"O alimentador {ctmt} nao esta entre os alimentadores ativos da região.", 8000)
            return
        self.seletor_ctmt.setCurrentIndex(posicao)
        self.abas.setCurrentIndex(0)
        self.statusBar().showMessage(
            f"Alimentador {self.seletor_ctmt.currentText()} selecionado: clique em "
            "Carregar UCs.", 10000)

    # ------------------------------------------------------ curva do alimentador

    def gerar_curva(self):
        """Curva sintetica anual do alimentador, por fase, e seus indicadores."""
        if self.resultado is None or self.insumos is None:
            QMessageBox.warning(self, "Curva", "Calcule as demandas primeiro.")
            return
        fonte, ctmt = self.fonte, self.ctmt_carregado
        ucs, resultado, insumos = self.ucs, self.resultado, self.insumos

        def trabalho(avisar):
            avisar("montando a curva do alimentador")
            return alimentador.sintetizar(fonte, ctmt, ucs, resultado, insumos) + (ctmt,)

        self._iniciar(trabalho, self._ao_gerar_curva, "montando a curva do alimentador")

    def _ao_gerar_curva(self, saida):
        self.sintese = saida
        self.statusBar().showMessage(f"Curva do alimentador {saida[3]} pronta.", 8000)
        self._mostrar_curva()

    def _html_da_curva(self):
        """Resumo da curva: indicadores, divisao por fase e como as fases sairam."""
        tabela, ind, rel, ctmt = self.sintese
        rotulos = [
            ("DESEQ_P95_kW", "Desequilibrio P95 [kW]"),
            ("DESEQ_MEDIO_kW", "Desequilibrio medio [kW]"),
            ("DESEQ_MAX_kW", "Desequilibrio maximo [kW]"),
            ("DESEQ_REL_P95_%", "Desequilibrio relativo P95 [%]"),
            ("DESEQ_REL_MEDIO_%", "Desequilibrio relativo medio [%]"),
            ("DESEQ_REL_MAX_%", "Desequilibrio relativo maximo [%]"),
            ("INSTANTES_DESCARTADOS_%", "Instantes fora do relativo [%]"),
            ("PICO_TOTAL_kW", "Pico da soma das fases [kW]"),
            ("VALE_TOTAL_kW", "Vale da soma das fases [kW]"),
            ("PARTICIPACAO_A_%", "Energia na fase A [%]"),
            ("PARTICIPACAO_B_%", "Energia na fase B [%]"),
            ("PARTICIPACAO_C_%", "Energia na fase C [%]"),
        ]

        def numero(valor):
            return "-" if pd.isna(valor) else f"{valor:,.1f}".replace(",", " ")

        linhas = "".join(
            f"<tr><td>{rotulo}</td><td align='right'>{numero(ind.loc['LIQUIDA', chave])}</td>"
            f"<td align='right'>{numero(ind.loc['CARGA', chave])}</td></tr>"
            for chave, rotulo in rotulos
        )
        excecoes = rel["excecoes"]
        return f"""
<h3>Alimentador {ctmt}</h3>
<p>Curva sintetica de {len(tabela)} instantes de {self.passo_calculado} min, sem
perdas: carga menos geracao de todas as UCs, repartidas por fase.</p>
<table cellspacing="0" cellpadding="3">
<tr><th align="left">Indicador</th><th align="right">Liquida</th><th align="right">So carga</th></tr>
{linhas}
</table>
<p><b>Desequilibrio</b> e a diferenca entre a fase mais e a menos carregada;
o <b>relativo</b> e o maior desvio de uma fase em relacao a media das tres,
dividido pela media. O <b>P95</b> ignora os 5% de instantes mais extremos e e o
melhor para comparar alimentadores.</p>
<h4>Como as fases foram atribuidas</h4>
<ul>
<li>UCs de MT, pelo proprio FAS_CON: {rel['mt_direto']}</li>
<li>UCs de BT em trafo mono ou bifasico, pelo primario do trafo: {rel['bt_primario_mono_bi']}</li>
<li>UCs de BT em trafo trifasico, pela letra da UC: {rel['bt_trafo_trifasico']}</li>
<li>UCs de BT sem trafo encontrado (letra da UC): {rel['bt_sem_trafo']}</li>
<li>GDs pelas fases da propria UG: {rel['ug_da_propria_ug']}</li>
<li>GDs com UG fora das fases da UC (usaram as da UC): {rel['ug_fora_da_uc']}</li>
<li>GDs sem registro de fase da UG (usaram as da UC): {rel['ug_sem_registro']}</li>
</ul>
<h4>Fora da curva</h4>
<p>{rel['ugs_sem_uc']} UG(s) do alimentador nao pertencem a nenhuma UC e ficaram
de fora; injetaram {rel['ene_ugs_sem_uc_kwh'] / 1e3:,.0f} MWh no ano.
{len(excecoes)} UC(s) cairam num fallback; a lista vai junto na exportacao.</p>
"""

    def _mostrar_curva(self):
        if self._dialogo_curva is None:
            dialogo = QDialog(self)
            dialogo.setStyleSheet(theme.QSS)
            dialogo.resize(1100, 760)
            self._painel_curva = grafico.PainelCurva()
            self._texto_curva = QTextBrowser()
            self._texto_curva.setStyleSheet(ESTILO_TEXTO)
            self._texto_curva.setOpenExternalLinks(False)
            self._abas_curva = QTabWidget()
            self._abas_curva.addTab(self._painel_curva, "Grafico")
            self._abas_curva.addTab(self._texto_curva, "Relatorio")
            botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            botoes.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
            exportar = botoes.addButton("Exportar CSV...", QDialogButtonBox.ButtonRole.ActionRole)
            exportar.clicked.connect(self.exportar_curva)
            botoes.rejected.connect(dialogo.close)
            layout = QVBoxLayout(dialogo)
            layout.addWidget(self._abas_curva, 1)
            layout.addWidget(botoes)
            self._dialogo_curva = dialogo
        self._dialogo_curva.setWindowTitle(f"Curva do alimentador {self.sintese[3]}")
        self._texto_curva.setHtml(self._html_da_curva())
        self._painel_curva.definir(self.sintese[0], self.passo_calculado)
        self._dialogo_curva.show()
        self._dialogo_curva.raise_()
        self._dialogo_curva.activateWindow()

    def exportar_curva(self):
        """Grava as curvas e, ao lado, os indicadores e as UCs que cairam em fallback."""
        if self.sintese is None:
            return
        tabela, ind, rel, ctmt = self.sintese
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Salvar curva do alimentador", f"CURVA_{ctmt}.csv", "CSV (*.csv)"
        )
        if not caminho:
            return
        base = Path(caminho)
        extras = {
            base.with_name(f"{base.stem}_indicadores.csv"): ind,
            base.with_name(f"{base.stem}_excecoes.csv"): rel["excecoes"],
        }
        try:
            tabela.to_csv(base)
            for destino, df in extras.items():
                df.to_csv(destino, index=destino.stem.endswith("_indicadores"))
        except Exception as erro:
            QMessageBox.critical(self, "Curva", str(erro))
            return
        self.statusBar().showMessage(
            f"Curva gravada em {base} ({len(tabela)} instantes), com os indicadores "
            f"e as {len(rel['excecoes'])} excecoes ao lado.", 15000
        )

    def ocupada(self):
        return self._trabalho is not None and self._trabalho.isRunning()

    def fechar(self):
        """Chamado pela janela principal ao fechar o CurvaGD."""
        if self.ocupada():
            self._trabalho.wait(3000)
        if self.fonte is not None:
            self.fonte.fechar()
            self.fonte = None
