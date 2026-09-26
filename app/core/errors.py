from dataclasses import dataclass


class ErroBDGD(ValueError):
    def __init__(self, codigo: str, mensagem: str, *, codigos=()):
        self.codigo = codigo
        self.codigos = tuple(dict.fromkeys((codigo, *codigos)))
        super().__init__(mensagem)


@dataclass(frozen=True)
class Diagnostico:
    codigo: str
    severidade: str
    entidade: str
    chave: str = ""
    campo: str = ""
    valor_original: str = ""
    mensagem: str = ""
    detalhe_tecnico: str = ""
    linha: int = 0
