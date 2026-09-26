"""Parametros do calculo de uma UC, pelo metodo da CORRECAO_DEMANDA_BDGD.

A irradiancia (NASA), a base da ANEEL e os parametros da simulacao solar vem da
tela Dados de entrada e valem para todos os modos; aqui so se escolhe o periodo
e, numa UC com GD, se conferem as potencias achadas na ANEEL.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QFormLayout, QHBoxLayout,
    QComboBox, QDoubleSpinBox, QLabel, QPushButton, QDialogButtonBox,
    QGroupBox)

from . import theme
from ..calculo import gd as gd_calc
from ..calculo.potencias import RAZAO_PADRAO
from ..services.aneel_solar import buscar_potencias
from .jobs import Worker

MESES = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
         "Setembro", "Outubro", "Novembro", "Dezembro")

EXPLICACAO = ("A curva da UC sai dos perfis DU/SA/DO da CRVCRG encaixados pelo dia da semana, "
              "em pu do máximo do ano. Em cada mês, a demanda máxima DEM_MAX é a que reproduz o ENE da UC "
              "como energia importada da rede:  ENE = h · Σ max(DEM_MAX · C − G, 0).")
EXPLICACAO_GD = ("A geração G usa o modelo PVSystem vetorizado com a irradiância da NASA importada em Dados de entrada, "
                 "reamostrada para 15 min por PCHIP, com correção por temperatura, eficiência do inversor e cut-in — "
                 "o mesmo cálculo da correção de demanda. Potências: base técnica da ANEEL (soma das linhas do CEG GD). "
                 "Sem cadastro, deixe em zero: o cálculo usa o POT_INST das UGs como kVA e a razão kW/kVA para os módulos.")


class CurveParametersDialog(QDialog):
    def __init__(self, parent=None, *, tem_gd=False, somente_mes=False, codigo_gd="", workspace=None,
                 parametros_salvos=None, parametros_solares=None):
        super().__init__(parent)
        self.tem_gd = tem_gd and not somente_mes
        self.somente_mes = somente_mes
        self.codigo_gd = str(codigo_gd or "").strip()
        self.workspace = Path(workspace) if workspace is not None else None
        self.parametros_solares = {"performance_ratio": gd_calc.PERFORMANCE_RATIO_PADRAO,
                                   "cut_in_percentual": gd_calc.CUTIN_PCT_PADRAO,
                                   "razao_kw_kva": RAZAO_PADRAO, **(parametros_solares or {})}
        self._equipment_worker = None
        self._pending_done = None
        self._power_reference = ""
        self._loaded_powers = None
        self.setWindowTitle("Parâmetros da curva")
        self.resize(720, 560 if self.tem_gd else 260)
        self.setStyleSheet(theme.QSS)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.month = QComboBox()
        self.month.addItem("Todos os 12 meses · Ano completo", 0)
        for numero, nome in enumerate(MESES, 1):
            self.month.addItem(f"{numero:02d} · {nome}", numero)
        form.addRow("Período", self.month)
        layout.addLayout(form)
        if not somente_mes:
            nota = QLabel(EXPLICACAO)
            nota.setObjectName("ajuda")
            nota.setWordWrap(True)
            layout.addWidget(nota)
        if self.tem_gd:
            self._solar(layout)
        layout.addStretch()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color:{theme.ALERTA};font-weight:600")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Gerar curva")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.generate_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if parametros_salvos:
            self._restaurar(parametros_salvos)
        elif self.tem_gd and self.codigo_gd and self.workspace is not None:
            QTimer.singleShot(0, self._buscar_potencias)

    # -- construcao ---------------------------------------------------------
    def _solar(self, layout):
        grupo = QGroupBox("Geração distribuída · modelo PVSystem com a irradiância da NASA")
        form = QFormLayout(grupo)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(10)
        acoes = QWidget()
        linha = QHBoxLayout(acoes)
        linha.setContentsMargins(0, 0, 0, 0)
        self.find_powers = QPushButton("Buscar potências na ANEEL")
        linha.addWidget(self.find_powers)
        linha.addStretch()
        self.find_powers.clicked.connect(lambda: self._buscar_potencias())
        form.addRow(acoes)
        self.power_status = QLabel(f"CEG GD: {self.codigo_gd or 'não informado'}.")
        self.power_status.setWordWrap(True)
        self.power_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.power_status)
        self.modules = self._number_box(0, 10_000_000, 0, 3, " kW")
        self.modules.setSpecialValueText("POT_INST da UG × razão")
        self.inverter = self._number_box(0, 10_000_000, 0, 3, " kVA")
        self.inverter.setSpecialValueText("POT_INST da UG")
        self.modules.valueChanged.connect(self._potencias_editadas)
        self.inverter.valueChanged.connect(self._potencias_editadas)
        form.addRow("Módulos (Pmpp)", self.modules)
        form.addRow("Inversor", self.inverter)
        p = self.parametros_solares
        resumo = QLabel(f"Irradiância-base {p['performance_ratio']:.2f} · cut-in {p['cut_in_percentual']:.1f}% · "
                        f"kW/kVA de reserva {p['razao_kw_kva']:.2f} (altere em Dados de entrada)".replace(".", ","))
        resumo.setObjectName("ajuda")
        resumo.setWordWrap(True)
        form.addRow("Simulação", resumo)
        nota = QLabel(EXPLICACAO_GD)
        nota.setObjectName("ajuda")
        nota.setWordWrap(True)
        form.addRow(nota)
        layout.addWidget(grupo)

    def _restaurar(self, p):
        self.month.setCurrentIndex(max(0, self.month.findData(p.get("mes", 0))))
        if not self.tem_gd:
            return
        for campo, widget in (("potencia_modulos_kwp", self.modules), ("potencia_inversor_kw", self.inverter)):
            if isinstance(p.get(campo), (int, float)):
                widget.setValue(p[campo])
        self._power_reference = p.get("referencia_potencias", "Tratamento anterior da UC")
        self._loaded_powers = (self.modules.value(), self.inverter.value()) if p.get("fonte_potencia") == "aneel" else None
        self.power_status.setText(f"Potências do último cálculo desta UC recuperadas. CEG GD: {self.codigo_gd}. "
                                  "Você pode revisar e gerar novamente.")

    # -- consulta a ANEEL ---------------------------------------------------
    def _buscar_potencias(self):
        if self._pending_done is not None or self._equipment_worker is not None:
            return
        if not self.codigo_gd or self.workspace is None:
            self.power_status.setText("Selecione uma UC com CEG GD para preencher as potências automaticamente.")
            return
        self._power_reference = ""
        self._loaded_powers = None
        self.modules.setValue(0)
        self.inverter.setValue(0)
        self.power_status.setText("Buscando os equipamentos da UC na base da ANEEL importada…")
        self._power_busy(True)
        worker = Worker(lambda token, progress: buscar_potencias(self.workspace, self.codigo_gd,
            token=token, progress=progress), self)
        self._equipment_worker = worker
        worker.result.connect(self._potencias_encontradas)
        worker.error.connect(self._erro_potencias)
        worker.progress.connect(self._progresso_potencias)
        worker.finished.connect(self._fim_potencias)
        worker.start()

    def _power_busy(self, busy):
        for campo in (self.modules, self.inverter, self.find_powers, self.generate_button):
            campo.setEnabled(not busy)

    def _progresso_potencias(self, etapa, atual, total):
        if self._pending_done is None:
            percentual = f" · {atual / total:.0%}" if total else ""
            self.power_status.setText(f"{etapa}{percentual} · CEG GD {self.codigo_gd}")

    def _potencias_encontradas(self, resultado):
        if self._pending_done is not None:
            return
        equipamento = resultado["equipamento"]
        self.modules.setValue(equipamento.potencia_modulos_kwp)
        self.inverter.setValue(equipamento.potencia_inversor_kw)
        self._loaded_powers = (self.modules.value(), self.inverter.value())
        self._power_reference = equipamento.fonte
        self.power_status.setText(f"Preenchido para CEG GD {self.codigo_gd}. Fonte: {equipamento.fonte}\n"
            "Confira se o cadastro corresponde ao ano analisado. Você pode editar as potências." +
            (f"\n{resultado['aviso']}" if resultado.get("aviso") else ""))

    def _erro_potencias(self, mensagem):
        if self._pending_done is None:
            self.power_status.setText(mensagem + "\nSem potências, o cálculo usa o POT_INST das UGs; "
                                      "você também pode informá-las manualmente.")

    def _fim_potencias(self):
        worker = self._equipment_worker
        self._equipment_worker = None
        worker.deleteLater()
        self._power_busy(False)
        if self._pending_done is not None:
            super().done(self._pending_done)

    def _potencias_editadas(self):
        if self._loaded_powers is not None:
            self.power_status.setText(f"Potências editáveis para CEG GD {self.codigo_gd}. "
                "Os valores exibidos serão usados no cálculo.\nFonte consultada: " + self._power_reference)

    def done(self, result):
        if self._equipment_worker is not None:
            self._pending_done = result
            self._equipment_worker.token.cancelar()
            self.power_status.setText("Cancelando consulta da ANEEL…")
            return
        self._pending_done = result
        super().done(result)

    # -- utilitarios --------------------------------------------------------
    @staticmethod
    def _number_box(minimum, maximum, value, decimals, suffix):
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setValue(value)
        box.setSuffix(suffix)
        return box

    def values(self):
        resultado = {"mes": self.month.currentData()}
        if not self.tem_gd:
            return resultado
        potencias = (self.modules.value(), self.inverter.value())
        da_aneel = self._loaded_powers is not None and potencias == self._loaded_powers
        referencia = self._power_reference
        if self._loaded_powers is not None and not da_aneel:
            referencia = "Potências editadas pelo usuário; cadastro consultado: " + referencia
        resultado.update(
            potencia_modulos_kwp=potencias[0] or None, potencia_inversor_kw=potencias[1] or None,
            fonte_potencia="aneel" if da_aneel else "manual", referencia_potencias=referencia)
        return resultado

    def accept(self):
        if self._equipment_worker is not None:
            return
        if self.tem_gd:
            modulos, inversor = self.modules.value(), self.inverter.value()
            if (modulos > 0) != (inversor > 0):
                self.error.setText("Informe as duas potências (módulos e inversor) ou deixe as duas em zero "
                                   "para usar o POT_INST das UGs.")
                return
        super().accept()
