"""Ferramenta CORRECAO_DEMANDA_BDGD dentro do CurvaGD.

Le um alimentador direto da BDGD (.gpkg ou .gdb) e devolve DEM_MAX_01..12 de
cada UC, a curva sintetica por fase com indicadores de desequilibrio e as
estatisticas de carga de todos os alimentadores. Os modulos sao os mesmos da
ferramenta original; a irradiancia e o modelo da GD vem de `app.calculo`,
compartilhados com as curvas das UCs.

- `bdgd`: leitura da BDGD (.gpkg e .gdb): alimentadores, UCs, UGs, curvas
- `aneel`: potencias das GDs na base da ANEEL, com reserva na UGBT/UGMT
- `curvas`: banco CRVCRG, perfis DU/SA/DO e a curva do ano em pu
- `demanda`: motor mensal, produz DEM_MAX_01..12
- `alimentador`: curva do alimentador por fase e indicadores de desequilibrio
- `estatisticas`: perfil das cargas por alimentador
"""
