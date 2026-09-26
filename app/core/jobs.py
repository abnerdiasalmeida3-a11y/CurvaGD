from threading import Event, Lock
from .errors import ErroBDGD


class Cancelamento:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._interrupt = None

    def cancelar(self):
        with self._lock:
            self._event.set()
            if self._interrupt:
                self._interrupt()

    def verificar(self):
        if self._event.is_set():
            raise ErroBDGD("IMPORTACAO_CANCELADA", "Operação cancelada; versão anterior preservada.")

    @property
    def solicitado(self):
        return self._event.is_set()

    def associar(self, callback):
        with self._lock:
            self._interrupt = callback
            if callback and self._event.is_set():
                callback()


def sem_progresso(etapa: str, atual: int, total: int):
    pass
