import logging
from PySide6.QtCore import QThread, Signal
from ..core.jobs import Cancelamento
from ..core.errors import ErroBDGD


class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str, int, int)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action, self.token = action, Cancelamento()

    def run(self):
        try:
            result = self.action(self.token, self.progress.emit)
            self.result.emit(result)
        except Exception as exc:
            logging.getLogger("bdgd").exception("Operação em segundo plano")
            if isinstance(exc, ErroBDGD):
                self.error.emit(f"{exc.codigo}: {exc}")
            else:
                self.error.emit("Não foi possível concluir a operação. Consulte logs/app.log para os detalhes.")
