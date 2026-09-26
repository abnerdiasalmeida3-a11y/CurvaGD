from PySide6.QtCore import QAbstractTableModel, Qt

COLUMNS = [("id_uc", "Unidade consumidora"), ("municipio", "Município IBGE"),
           ("alimentador", "Alimentador"), ("classe", "Classe"), ("grupo_tensao", "Grupo"),
           ("energia_anual_kwh", "Energia anual (kWh)"), ("codigo_gd", "CEG GD"), ("status", "Curvabilidade")]

STATUS_LABELS = {"CURVAVEL": "Curvável", "NAO_CURVAVEL_TIPOLOGIA": "Tipologia inválida", "NAO_CURVAVEL_ENERGIA": "Energia inválida", "CALENDARIO_PARCIAL": "Calendário parcial", "REFERENCIA_ORFA": "Referência órfã"}


class UCTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        if len(rows) > 200:
            raise ValueError("Página excede 200 registros")
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        value = self.rows[index.row()].get(COLUMNS[index.column()][0])
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
            return " · ".join(STATUS_LABELS.get(v, v) for v in str(value).split("|"))

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1] if orientation == Qt.Orientation.Horizontal else str(section + 1)
