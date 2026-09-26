"""Potencias da GD de uma UC: kW de modulos e kVA de inversor.

Mesma ordem de fontes do `aneel.py` da CORRECAO_DEMANDA_BDGD:

1. base tecnica de GD fotovoltaica da ANEEL, pelo CEG_GD. As potencias de todas
   as linhas do mesmo codigo sao SOMADAS -- um CEG aparece varias vezes quando
   ha mais de um arranjo ou uma ampliacao, e ficar so com uma linha subestimaria
   o sistema;
2. POT_INST das UGs (UGBT/UGMT) com o mesmo CEG_GD, somado. Esse numero e o kVA;
   o kW sai da razao padrao kW/kVA. Antes passa pela trava do teto legal;
3. sem nenhuma das duas, a UC fica sem potencia (GD_SEM_POTENCIA) e e tratada
   como consumidor puro.
"""

import math

RAZAO_PADRAO = 1.25

STATUS_SEM_GD = "SEM_GD"
STATUS_ANEEL = "COM_GD"
STATUS_INFORMADA = "GD_POT_INFORMADA"
STATUS_UGBT = "GD_POT_UGBT"
STATUS_SEM_POTENCIA = "GD_SEM_POTENCIA"

DESCRICAO_STATUS = {
    STATUS_SEM_GD: "UC sem GD",
    STATUS_ANEEL: "potências da base técnica da ANEEL",
    STATUS_INFORMADA: "potências informadas na tela",
    STATUS_UGBT: "POT_INST da UGBT/UGMT (kVA) e razão kW/kVA padrão",
    STATUS_SEM_POTENCIA: "GD sem potência na ANEEL nem na UG: tratada como UC sem geração",
}

# Teto legal da potencia de uma GD por nivel de tensao, em kW: microgeracao ate
# 75 kW (BT), minigeracao ate 5 MW (MT). Trava para o POT_INST da UG, que vem em
# kW em 89% dos casos mas em W em 1,4%.
TETO_KW = {"ucbt": 75.0, "ucmt": 5000.0}


def corrigir_unidade(pot_inst, entidade):
    """POT_INST em kW, com a trava do teto legal do nivel de tensao da UC.

    Acima do teto, se o valor dividido por mil couber, o registro estava em W e e
    convertido; se nem assim couber, e inverossimil e vira zero.
    """
    if pot_inst is None or not math.isfinite(pot_inst) or pot_inst <= 0:
        return 0.0
    teto = TETO_KW.get(str(entidade).lower(), TETO_KW["ucmt"])
    if pot_inst <= teto:
        return float(pot_inst)
    if pot_inst / 1000.0 <= teto:
        return float(pot_inst) / 1000.0
    return 0.0


def resolver(codigo_gd, entidade, *, aneel=None, informadas=None, pot_ug_kw=None,
             razao_padrao=RAZAO_PADRAO):
    """(kW modulos, kVA inversor, status) de uma UC.

    `aneel` e `informadas` sao pares (kW, kVA) ou None; `pot_ug_kw` e o POT_INST
    somado das UGs do CEG_GD. Potencias informadas na tela valem sobre a ANEEL:
    e o usuario revisando o cadastro.
    """
    if not str(codigo_gd or "").strip():
        return 0.0, 0.0, STATUS_SEM_GD
    for par, status in ((informadas, STATUS_INFORMADA), (aneel, STATUS_ANEEL)):
        if par is not None:
            kw, kva = (float(v or 0.0) for v in par)
            if kw > 0 and kva > 0 and math.isfinite(kw) and math.isfinite(kva):
                return kw, kva, status
    reserva = corrigir_unidade(pot_ug_kw, entidade)
    if reserva > 0:
        return reserva * razao_padrao, reserva, STATUS_UGBT
    return 0.0, 0.0, STATUS_SEM_POTENCIA
