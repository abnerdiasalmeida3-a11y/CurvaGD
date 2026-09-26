"""Simulacao da geracao de unidades com GD fotovoltaica a partir da irradiancia.

Arquivo da ferramenta CORRECAO_DEMANDA_BDGD, trazido para o CurvaGD: e o
modelo usado tanto nas curvas das UCs quanto na pagina Correcao de demanda.

O modelo e o mesmo do TCC/NOVA_FERRAMENTA/CORRECAO_GD_STANDALONE.py (constantes
e formulas identicas), com tres diferencas exigidas por este app: a serie pode
comecar em qualquer hora, o passo pode ser menor que uma hora, e a saida e a
curva de potencia em vez da energia mensal agregada.

Toda GD e tratada como trifasica: a potencia devolvida e a ativa total das tres
fases, em kW.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ==========================================
# CONSTANTES DO MODELO (identicas ao CORRECAO_GD_STANDALONE.py)
# ==========================================

# Curva de correcao de potencia por temperatura (XYCurve.MyPvsT)
PVST_X = [0.0, 25.0, 75.0, 100.0]
PVST_Y = [1.2, 1.0, 0.8, 0.6]

# Curva de eficiencia do inversor (XYCurve.MyEff), x em pu do kVA
EFF_X = [0.1, 0.2, 0.4, 1.0]
EFF_Y = [0.86, 0.90, 0.93, 0.97]

# Curva de temperatura diaria (Tshape.MyTemp), 24 pontos horarios
TEMP_24H = [25, 25, 25, 25, 25, 25, 25, 25, 35, 40, 45, 50,
            60, 60, 55, 40, 35, 30, 25, 25, 25, 25, 25, 25]

# Cut-in do inversor, em % do kVA: potencia DC minima para o inversor ligar.
# Valor padrao de 2%; pode ser ajustado conforme o inversor informado pelo usuario.
CUTIN_PCT_PADRAO = 2.0
PERFORMANCE_RATIO_PADRAO = 1.0

TENSAO_KV_PADRAO = 0.220


@dataclass
class UnidadeConsumidora:
    """Unidade consumidora: tensao nominal de linha, curva de carga e, se houver, GD.

    kva e kw zerados significam UC sem geracao -- consumidor puro.

    Mutavel de proposito: a tabela da interface edita os campos no lugar. Nao e
    usada como chave de dicionario em nenhum ponto, entao nao precisa ser hashable.
    """

    codigo: str
    kv: float = TENSAO_KV_PADRAO   # tensao nominal VFF, em kV
    curva: str = ""                # codigo da curva de carga da BDGD
    kva: float = 0.0               # inversor
    kw: float = 0.0                # modulos fotovoltaicos
    energia_kwh: float = 0.0       # consumo no periodo plotado; so registro, nao entra no calculo

    @property
    def possui_gd(self):
        return self.kva > 0 and self.kw > 0


def _temperatura(indice):
    """Temperatura de cada instante pela hora do relogio.

    O standalone repete o dia de 24 pontos assumindo serie anual comecando a
    meia-noite; aqui o recorte pode comecar em qualquer hora e ter passo menor
    que uma hora, entao a temperatura vem da hora de cada carimbo de tempo.
    """
    return np.asarray(TEMP_24H, dtype=float)[np.asarray(indice.hour)]


def potencia_pvsystem(unidade, serie_irrad, passo_minutos=60,
                      performance_ratio=PERFORMANCE_RATIO_PADRAO, cut_in=CUTIN_PCT_PADRAO):
    """Calcula o modelo PVSystem vetorizado. Devolve a potencia AC total em kW.

    Pdc = Pmpp * fator_T(T) * (irrad_base * mult); inversor com cut-in/cut-out,
    curva de eficiencia em pu do kVA e ceifamento no kVA.
    """
    _validar(unidade, serie_irrad)

    fator_t = np.interp(_temperatura(serie_irrad.index), PVST_X, PVST_Y)
    p_dc = unidade.kw * fator_t * (performance_ratio * serie_irrad.to_numpy(dtype=float))

    # cut_in == cut_out, entao nao ha histerese a modelar
    ligado = p_dc >= (cut_in / 100.0) * unidade.kva
    eficiencia = np.interp(p_dc / unidade.kva, EFF_X, EFF_Y)
    p_ac = np.where(ligado, np.minimum(p_dc * eficiencia, unidade.kva), 0.0)

    return pd.Series(p_ac, index=serie_irrad.index)


def energia_importada(dmax, carga_pu, geracao_kw, passo_minutos):
    """Energia que veio da rede, em kWh: so conta o que a carga passa da geracao.

        E = h * soma(max(Dmax*C - G, 0))
    """
    horas = passo_minutos / 60.0
    carga = np.asarray(carga_pu, dtype=float)
    geracao = np.zeros_like(carga) if geracao_kw is None else np.asarray(geracao_kw, dtype=float)
    return float(horas * np.maximum(dmax * carga - geracao, 0.0).sum())


def energias_liquidas(carga_kw, geracao_kw, passo_minutos):
    """Energia importada e exportada, em kWh, a partir do saldo carga - geracao.

    A parcela importada e a mesma integral que `energia_importada` calcula com a
    carga ja escalada: acima de zero a UC puxa da rede, abaixo ela injeta.
    """
    horas = passo_minutos / 60.0
    carga = np.asarray(carga_kw, dtype=float)
    geracao = np.zeros_like(carga) if geracao_kw is None else np.asarray(geracao_kw, dtype=float)
    saldo = carga - geracao
    return (
        float(horas * np.maximum(saldo, 0.0).sum()),
        float(horas * np.maximum(-saldo, 0.0).sum()),
    )


def demanda_maxima(carga_pu, geracao_kw, energia_kwh, passo_minutos,
                   tolerancia=1e-12, max_iteracoes=200):
    """Demanda maxima, em kW, que reproduz a energia importada informada.

    Inverte `energia_importada` por bissecao. A funcao e monotona crescente em
    Dmax, e os dois limites saem fechados -- nao ha o "dobrar ate achar":

        inferior  E/(h*somaC)        ignora a geracao, entao subestima
        superior  (E/h + somaG)/somaC   porque f(D) >= h*(D*somaC - somaG)

    Numa UC sem geracao os dois coincidem e a resposta sai sem iterar.
    """
    horas = passo_minutos / 60.0
    carga = np.asarray(carga_pu, dtype=float)
    geracao = np.zeros_like(carga) if geracao_kw is None else np.asarray(geracao_kw, dtype=float)
    if carga.shape != geracao.shape:
        raise ValueError("As curvas de carga e de geracao precisam ter o mesmo tamanho.")

    soma_carga = float(carga.sum())
    if soma_carga <= 0:
        raise ValueError("A curva de carga e toda nula: nao ha demanda que gere importacao.")
    if energia_kwh <= 0:
        return 0.0

    baixo = energia_kwh / (horas * soma_carga)
    alto = (energia_kwh / horas + float(geracao.sum())) / soma_carga

    for _ in range(max_iteracoes):
        if alto - baixo <= tolerancia * max(alto, 1.0):
            break
        meio = 0.5 * (baixo + alto)
        if energia_importada(meio, carga, geracao, passo_minutos) < energia_kwh:
            baixo = meio
        else:
            alto = meio
    return 0.5 * (baixo + alto)


def geracao_por_kva(razao, serie_irrad, passo_minutos=60,
                    performance_ratio=PERFORMANCE_RATIO_PADRAO, cut_in=CUTIN_PCT_PADRAO):
    """Curva de potencia de uma GD com 1 kVA de inversor e `razao` kW de modulos.

    A potencia AC do modelo depende so da razao kW/kVA, nao do porte: em

        ligado = Pmpp*fT*irrad >= (cut_in/100)*kVA
        eff    = interp(Pmpp*fT*irrad / kVA)
        Pac    = min(Pmpp*fT*irrad*eff, kVA)

    dividir tudo por kVA deixa apenas r = Pmpp/kVA. Entao a geracao de qualquer
    UC e `geracao_por_kva(r) * kVA`, e o chamador cacheia por r em vez de
    simular cada par (kW, kVA) -- num alimentador tipico isso troca 149
    simulacoes por 103.
    """
    if razao <= 0:
        raise ValueError("A razao kW/kVA precisa ser maior que zero.")
    unidade = UnidadeConsumidora(codigo=f"kva1_r{razao:g}", kva=1.0, kw=razao)
    return potencia_pvsystem(unidade, serie_irrad, passo_minutos,
                             performance_ratio, cut_in)


def demanda_maxima_exata(carga_pu, geracao_kw, energia_kwh, passo_minutos):
    """Mesma resposta de `demanda_maxima`, resolvida de uma vez em vez de iterar.

        E(D) = h * soma(max(D*C - G, 0))

    e linear por partes em D: o ponto i so entra na conta quando D passa de
    t_i = G_i/C_i. Ordenando os t_i, dentro do trecho [t_k, t_k+1] vale

        E(D) = h * (D * somaC_ativos - somaG_ativos)

    entao basta achar o trecho por busca binaria na energia dos nos e inverter a
    reta. Sem geracao cai no fechado D = E/(h*somaC).

    Custa O(n log n) contra as ~200 iteracoes da bissecao, e da o resultado sem
    tolerancia -- o que importa aqui porque a inversao roda 12 vezes por UC e um
    alimentador tem dezenas de milhares delas.
    """
    horas = passo_minutos / 60.0
    carga = np.asarray(carga_pu, dtype=float)
    geracao = None if geracao_kw is None else np.asarray(geracao_kw, dtype=float)
    if geracao is not None and carga.shape != geracao.shape:
        raise ValueError("As curvas de carga e de geracao precisam ter o mesmo tamanho.")

    soma_carga = float(carga.sum())
    if soma_carga <= 0:
        raise ValueError("A curva de carga e toda nula: nao ha demanda que gere importacao.")
    if energia_kwh <= 0:
        return 0.0
    if geracao is None:
        return energia_kwh / (horas * soma_carga)

    # ponto com carga nula nunca importa, qualquer que seja D
    ativos = carga > 0
    carga, geracao = carga[ativos], geracao[ativos]

    limiares = geracao / carga
    ordem = np.argsort(limiares)
    limiares = limiares[ordem]
    soma_c = np.cumsum(carga[ordem])
    soma_g = np.cumsum(geracao[ordem])

    # energia no inicio de cada trecho: no trecho k valem os k+1 primeiros pontos
    nos = np.empty(limiares.size)
    nos[0] = 0.0
    nos[1:] = horas * (limiares[1:] * soma_c[:-1] - soma_g[:-1])
    trecho = int(np.searchsorted(nos, energia_kwh, side="right") - 1)
    return (energia_kwh / horas + soma_g[trecho]) / soma_c[trecho]


def _validar(unidade, serie_irrad):
    if not unidade.possui_gd:
        raise ValueError(f"UC {unidade.codigo}: nao tem GD para simular.")
    if unidade.kv <= 0:
        raise ValueError(f"UC {unidade.codigo}: a tensao nominal precisa ser maior que zero.")
    if serie_irrad is None or serie_irrad.empty:
        raise ValueError("A serie de irradiancia esta vazia.")
