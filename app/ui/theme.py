"""Tokens visuais da plataforma: paleta, tipografia e folha de estilo global.

Paleta em preto e branco (tons de cinza).
Centraliza cores e espacamentos para que janela principal, dialogos e graficos
compartilhem a mesma identidade visual.
"""

# Paleta em preto e branco: so tons de cinza. As curvas se distinguem pelo tom
# e pelo tracejado da linha, o que tambem funciona em impressao monocromatica.

# --- Superficies e texto ---------------------------------------------------
FUNDO = "#F0F0F0"
SUPERFICIE = "#FFFFFF"
SUPERFICIE_ALT = "#F7F7F7"
BORDA = "#D6D6D6"
BORDA_FORTE = "#B3B3B3"
TEXTO = "#111111"
TEXTO_SUAVE = "#555555"
TEXTO_FRACO = "#8A8A8A"

# --- Destaques (monocromaticos) ---------------------------------------------
PRIMARIA = "#1A1A1A"
PRIMARIA_CLARA = "#3D3D3D"
PRIMARIA_ESCURA = "#000000"
PRIMARIA_SUAVE = "#E4E4E4"
PRIMARIA_PRESSIONADA = "#CFCFCF"
PRIMARIA_INATIVA = "#A6A6A6"
TEXTO_INATIVO_CLARO = "#F2F2F2"
ROLAGEM = "#C4C4C4"
ROLAGEM_ATIVA = "#999999"
DESTAQUE = "#4D4D4D"
SUCESSO = "#333333"
ALERTA = "#000000"

# --- Graficos --------------------------------------------------------------
GRAFICO_FUNDO = "#FFFFFF"
GRAFICO_AREA = "#FFFFFF"
GRAFICO_GRADE = "#E3E3E3"
GRAFICO_GRADE_MENOR = "#F2F2F2"
GRAFICO_EIXO = "#9E9E9E"
GRAFICO_TEXTO = "#333333"
GRAFICO_TITULO = "#000000"
LINHA_ZERO = "#333333"
CURSOR = "#555555"
BALAO_FUNDO = "#FFFFFF"
BALAO_OPACIDADE = 242  # 0-255, aplicado sobre BALAO_FUNDO
BALAO_BORDA = "#B3B3B3"
BALAO_TEXTO = "#111111"

# Series: tons de cinza distintos, cada um com o seu tracejado (ESTILOS_LINHA).
CORES_SERIES = (
    "#000000",  # preto, continua      - carga
    "#6E6E6E",  # cinza medio, traco   - geracao
    "#2E2E2E",  # grafite, ponto       - curva liquida
    "#8C8C8C",  # cinza claro, traco-ponto
    "#474747",  # cinza escuro, traco-ponto-ponto
    "#A3A3A3",  # cinza claro, continua
)

# Tracejado por cor: "continua", "traco", "ponto", "traco_ponto", "traco_ponto_ponto".
ESTILOS_LINHA = {
    "#000000": "continua",
    "#6E6E6E": "traco",
    "#2E2E2E": "ponto",
    "#8C8C8C": "traco_ponto",
    "#474747": "traco_ponto_ponto",
    "#A3A3A3": "continua",
}

# Cor fixa por tipo de dia: a curva do sabado deve ter sempre a mesma cor,
# independente de quais tipos estejam selecionados no momento.
CORES_TIPO_DIA = {
    "Dia útil": "#000000",
    "Sábado": "#6E6E6E",
    "Domingo": "#2E2E2E",
    "Feriado": "#8C8C8C",
}
FAIXA_OPACIDADE = 38  # alfa da faixa min-max sob a curva media

FAMILIA_FONTE = "Segoe UI, Inter, Noto Sans, sans-serif"


QSS = f"""
QWidget {{
    color: {TEXTO};
    font-family: {FAMILIA_FONTE};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {FUNDO};
}}

/* ---- Cabecalho ---- */
QLabel#tituloApp {{
    font-size: 25px;
    font-weight: 600;
    color: {PRIMARIA_ESCURA};
    padding: 2px 0 0 0;
}}
QLabel#subtituloApp {{
    font-size: 13px;
    color: {TEXTO_SUAVE};
    padding-bottom: 2px;
}}
QLabel#tituloPagina {{
    font-size: 19px;
    font-weight: 600;
    color: {PRIMARIA_ESCURA};
    padding-bottom: 2px;
}}
QLabel#carimbo {{
    font-size: 11px;
    color: {TEXTO_FRACO};
    background: {SUPERFICIE};
    border: 1px solid {BORDA};
    border-radius: 10px;
    padding: 3px 10px;
}}
QLabel#ajuda {{
    color: {TEXTO_SUAVE};
}}
QLabel#rodape {{
    color: {TEXTO_SUAVE};
    background: {SUPERFICIE};
    border: 1px solid {BORDA};
    border-radius: 6px;
    padding: 7px 10px;
}}
QFrame#separador {{
    background: {BORDA};
    max-height: 1px;
    border: 0;
}}

/* ---- Navegacao lateral ---- */
QListWidget#nav {{
    background: {SUPERFICIE};
    border: 1px solid {BORDA};
    border-radius: 8px;
    padding: 6px;
    outline: 0;
    font-size: 13px;
}}
QListWidget#nav::item {{
    padding: 11px 12px;
    margin: 2px 0;
    border-radius: 6px;
    color: {TEXTO_SUAVE};
}}
QListWidget#nav::item:hover {{
    background: {SUPERFICIE_ALT};
    color: {TEXTO};
}}
QListWidget#nav::item:selected {{
    background: {PRIMARIA};
    color: #FFFFFF;
    font-weight: 600;
}}

QToolButton#alternarMenu {{
    background: {SUPERFICIE};
    color: {PRIMARIA_ESCURA};
    border: 1px solid {BORDA};
    border-radius: 8px;
    font-size: 17px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
}}
QToolButton#alternarMenu:hover {{
    background: {PRIMARIA_SUAVE};
    border-color: {PRIMARIA_CLARA};
}}
QToolButton#alternarMenu:checked {{
    background: {PRIMARIA};
    color: #FFFFFF;
    border-color: {PRIMARIA};
}}
QListWidget#nav::item:disabled {{
    color: {TEXTO_FRACO};
}}

/* ---- Area de conteudo ---- */
QStackedWidget#conteudo {{
    background: {SUPERFICIE};
    border: 1px solid {BORDA};
    border-radius: 8px;
}}

/* ---- Campos ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextBrowser, QPlainTextEdit {{
    background: {SUPERFICIE};
    border: 1px solid {BORDA_FORTE};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {PRIMARIA};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {PRIMARIA_CLARA};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {SUPERFICIE_ALT};
    color: {TEXTO_FRACO};
}}
QComboBox QAbstractItemView {{
    background: {SUPERFICIE};
    border: 1px solid {BORDA_FORTE};
    selection-background-color: {PRIMARIA_SUAVE};
    selection-color: {TEXTO};
    outline: 0;
    padding: 3px;
}}
QTextBrowser {{
    font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
    font-size: 12px;
    line-height: 150%;
}}

/* ---- Botoes ---- */
QPushButton {{
    background: {SUPERFICIE};
    color: {PRIMARIA_ESCURA};
    border: 1px solid {BORDA_FORTE};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {PRIMARIA_SUAVE};
    border-color: {PRIMARIA_CLARA};
}}
QPushButton:pressed {{
    background: {PRIMARIA_PRESSIONADA};
}}
QPushButton:disabled {{
    background: {SUPERFICIE_ALT};
    color: {TEXTO_FRACO};
    border-color: {BORDA};
}}
QPushButton#primario {{
    background: {PRIMARIA};
    color: #FFFFFF;
    border: 1px solid {PRIMARIA};
    font-weight: 600;
}}
QPushButton#primario:hover {{
    background: {PRIMARIA_CLARA};
    border-color: {PRIMARIA_CLARA};
}}
QPushButton#primario:pressed {{
    background: {PRIMARIA_ESCURA};
}}
QPushButton#primario:disabled {{
    background: {PRIMARIA_INATIVA};
    border-color: {PRIMARIA_INATIVA};
    color: {TEXTO_INATIVO_CLARO};
}}

/* ---- Tabelas ---- */
QTableView {{
    background: {SUPERFICIE};
    alternate-background-color: {SUPERFICIE_ALT};
    border: 1px solid {BORDA};
    border-radius: 8px;
    gridline-color: {BORDA};
    selection-background-color: {PRIMARIA_SUAVE};
    selection-color: {TEXTO};
    padding: 0;
}}
QTableView::item {{
    padding: 5px 6px;
    border: 0;
}}
QHeaderView::section {{
    background: {SUPERFICIE_ALT};
    color: {TEXTO_SUAVE};
    padding: 8px 4px;
    border: 0;
    border-right: 1px solid {BORDA};
    border-bottom: 1px solid {BORDA_FORTE};
    font-weight: 600;
}}
QHeaderView::section:hover {{
    background: {PRIMARIA_SUAVE};
    color: {PRIMARIA_ESCURA};
}}
QTableCornerButton::section {{
    background: {SUPERFICIE_ALT};
    border: 0;
    border-bottom: 1px solid {BORDA_FORTE};
}}

/* ---- Abas ---- */
QTabWidget::pane {{
    background: {SUPERFICIE};
    border: 1px solid {BORDA};
    border-radius: 8px;
    top: -1px;
}}
QTabWidget::tab-bar {{
    left: 6px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXTO_SUAVE};
    padding: 8px 16px;
    margin-right: 2px;
    border: 0;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {PRIMARIA};
}}
QTabBar::tab:selected {{
    color: {PRIMARIA_ESCURA};
    font-weight: 600;
    border-bottom: 2px solid {PRIMARIA};
}}

/* ---- Agrupadores ---- */
QGroupBox {{
    background: {SUPERFICIE_ALT};
    border: 1px solid {BORDA};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 12px 10px 12px;
    font-weight: 600;
    color: {PRIMARIA_ESCURA};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
}}

/* ---- Progresso ---- */
QProgressBar {{
    background: {SUPERFICIE_ALT};
    border: 1px solid {BORDA};
    border-radius: 7px;
    min-height: 16px;
    max-height: 16px;
    text-align: center;
    color: {TEXTO_SUAVE};
    font-size: 11px;
}}
QProgressBar::chunk {{
    background: {PRIMARIA};
    border-radius: 6px;
    margin: 1px;
}}

/* ---- Barras de rolagem ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {ROLAGEM};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {ROLAGEM_ATIVA}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {ROLAGEM};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ROLAGEM_ATIVA}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- Diversos ---- */
QCheckBox {{ spacing: 7px; }}
QToolTip {{
    background: {SUPERFICIE};
    color: {TEXTO};
    border: 1px solid {BORDA_FORTE};
    padding: 5px 7px;
}}
"""
