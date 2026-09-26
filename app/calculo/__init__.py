"""Calculo da carga das UCs e das curvas, portado da CORRECAO_DEMANDA_BDGD.

- `curvas`: curva anual em pu pelos perfis DU/SA/DO da CRVCRG;
- `irradiancia`: CSV da NASA POWER e reamostragem que conserva energia;
- `gd`: modelo PVSystem vetorizado e inversao exata da demanda;
- `potencias`: potencias da GD pela ANEEL, com reserva no POT_INST da UG;
- `motor`: demanda maxima e balanco de cada mes, com cache da geracao.
"""
