from enum import StrEnum


class TipoDia(StrEnum):
    DU = "DU"
    SA = "SA"
    DO = "DO"


class CoberturaCalendario(StrEnum):
    COMPLETA = "COMPLETA"
    PARCIAL = "PARCIAL"


class StatusUC(StrEnum):
    CURVAVEL = "CURVAVEL"
    TIPOLOGIA = "NAO_CURVAVEL_TIPOLOGIA"
    ENERGIA = "NAO_CURVAVEL_ENERGIA"
    CALENDARIO = "CALENDARIO_PARCIAL"
    REFERENCIA = "REFERENCIA_ORFA"
