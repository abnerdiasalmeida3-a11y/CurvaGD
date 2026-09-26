# Correção de demanda da BDGD

> Documento histórico da ferramenta separada. No CurvaGD atual, os dados são
> escolhidos pelo usuário e a geração usa apenas o modelo PVSystem vetorizado
> de `app/calculo/gd.py`. Consulte o README da pasta `codigo` para executar e
> compartilhar a versão atual.

> README original da ferramenta CORRECAO_DEMANDA_BDGD. No Analisador ela é a página **Correção de demanda** e os módulos estão em `app\correcao` (bdgd, aneel, curvas, demanda, alimentador, estatisticas), `app\calculo` (irradiancia, gd) e `app\ui\correcao` (página, gráfico por fase e painel de estatísticas). O `verificar.py` é `scripts\verificar_correcao.py`. As instruções de instalação abaixo são as da ferramenta separada.

Ferramenta em PyQt6 que lê um circuito inteiro da BDGD e devolve, para cada
unidade consumidora, **doze demandas máximas** — uma por mês.

A BDGD registra a energia mensal da UC (`ENE_01..12`) e não a demanda. Aqui a
demanda sai pelo caminho inverso: monta-se a curva agregada de comportamento do
mês, simula-se a geração fotovoltaica quando a UC tem GD, e procura-se a demanda
máxima que reproduz exatamente aquela energia.

## O que faz

- lê **UCBT_tab e UCMT_tab** direto da BDGD — em **GeoPackage (.gpkg)** ou em
  **File Geodatabase (.gdb)** — filtrando por alimentador (`CTMT`) e por situação
  ativa
- monta a **curva de carga** de cada UC a partir da `CRVCRG`, encaixando os
  perfis DU/SA/DO no calendário do ano
- cruza `CEG_GD` com a **base técnica de GD fotovoltaica da ANEEL** para obter
  a potência dos módulos e dos inversores, e simula a geração pelo modelo do
  `PVSystem` do OpenDSS — em dois motores independentes, OpenDSS e NumPy
- resolve, mês a mês, a demanda máxima que satisfaz o `ENE` da UC:

  ```
  ENE_mes = h · Σ max(Dmax·C − G, 0)
  ```

  com `C` a curva em pu, `G` a geração em kW e `h` o passo em horas
- exporta `DEM_MAX_01..12` em CSV ou XLSX, e a **carga em kW de um recorte
  qualquer** — duas semanas de janeiro, um domingo, o mês inteiro

## Como rodar

```bash
pip install PyQt6 PyQt6-Charts pandas numpy scipy openpyxl py-dss-interface
python app_demanda.py
```

Os três arquivos de entrada ficam em `dados/` e o app os abre sozinho:

| Arquivo | O que é | No repositório? |
|---|---|---|
| `*.gpkg` ou `*.gdb` | BDGD da distribuidora, nos dois formatos | não — 3,4 GB / 3,2 GB |
| `NASA_*.csv` | série horária de irradiância da NASA POWER | não — 154 KB, baixe no NASA POWER |
| `empreendimento-gd-*.csv` | base técnica de GD fotovoltaica da ANEEL | não — 546 MB |

Nenhum dos três está no repositório — os dois grandes passam do limite de 100 MB
por arquivo do GitHub — então precisam ser baixados e postos em `dados/`:

- **BDGD** — portal de dados abertos da ANEEL, conjunto *Base de Dados
  Geográfica da Distribuidora*, escolhendo distribuidora e ano-base;
- **Base técnica de GD** — dados abertos da ANEEL, conjunto
  *Empreendimentos de Geração Distribuída*, arquivo de informações técnicas de
  fotovoltaica;
- **Irradiância** — NASA POWER, dados horários do ponto do alimentador,
  parâmetro `ALLSKY_SFC_SW_DWN`, em CSV.

Nenhum deles é modificado: o GeoPackage é aberto em modo somente-leitura e o
File Geodatabase só é lido. Ler `.gdb` precisa de `pip install pyogrio geopandas`
— quem usa só `.gpkg` não precisa de nada além do `sqlite3` da biblioteca padrão,
e o import é preguiçoso justamente para isso.

Para conferir a pilha de cálculo sem passar pela interface:

```bash
python verificar.py --ctmt 764500
```

Com os dados fora de `dados/`, passe os caminhos:

```bash
python verificar.py --ctmt 764500 --bdgd "<pasta>/BDGD.gpkg" --nasa "<pasta>/NASA_2024_IRRAD.csv" --aneel "<pasta>/empreendimento-gd-....csv"
```

## Módulos

| Arquivo | Papel |
|---|---|
| `app_demanda.py` | interface: seletor de circuito, parâmetros, tabela e exportação |
| `bdgd.py` | leitura da BDGD (.gpkg e .gdb): alimentadores, UCs, UGs, curvas |
| `aneel.py` | potências das GDs na base da ANEEL, com reserva na UGBT/UGMT |
| `curvas.py` | banco `CRVCRG`: perfis DU/SA/DO e a curva do ano em pu |
| `gd.py` | modelo do `PVSystem` nos dois motores e inversão da demanda |
| `irradiancia.py` | leitura do CSV da NASA e reamostragem que conserva energia |
| `demanda.py` | motor mensal: produz `DEM_MAX_01..12` |
| `alimentador.py` | curva sintética do alimentador por fase e indicadores de desequilíbrio |
| `estatisticas.py` | perfil das cargas por alimentador: classes e ligações |
| `painel_estatisticas.py` | aba Estatísticas: visão geral ordenável e detalhe |
| `grafico.py` | gráfico interativo das curvas por fase, em QtCharts |
| `verificar.py` | conferências automáticas da pilha inteira |

## Convenções que importam

- a curva de carga é normalizada pelo **máximo do ano inteiro**, não pelo máximo
  do recorte. É o que permite montar a curva de duas semanas de janeiro,
  multiplicar por `DEM_MAX_01` e obter a mesma potência em kW que o mês inteiro
  daria. Com base móvel, um recorte só de domingo colocaria o pico de domingo em
  1,0 pu e inflaria a carga
- no passo nativo de 15 min todos os dias de um mesmo tipo são idênticos, então
  o máximo do ano é o máximo de qualquer mês: `DEM_MAX_mm` é, literalmente, o
  pico daquele mês
- `ENE_01..12` da UC é a energia que **veio da rede**. Numa UC com GD isso não é
  o consumo: parte da carga foi atendida pela própria geração, e é por isso que
  a demanda precisa da simulação fotovoltaica para ser estimada
- um período é o intervalo **meia-aberto `[início, fim)`** — o `fim` é
  exclusivo. O carimbo de tempo rotula o *início* de um bloco, então o valor de
  `01/02 00:00` é a potência média de `[01/02 00:00, 01/02 01:00)`, que já é
  fevereiro: pedir "de 01/01 a 01/02" é pedir janeiro fechado, 2976 blocos de
  15 min. Daí saem as três propriedades que interessam — o número de blocos é
  exatamente `(fim − início)/passo`, meses vizinhos se encaixam sem repetir a
  virada, e a energia do recorte nunca conta potência de fora da janela. É a
  convenção do `resample` do pandas, do `Loadshape` do OpenDSS e do CSV horário
  da NASA. `irradiancia.recortar` é o único lugar que implementa a regra
- a série de irradiância está em **kW/m²**, potência média do intervalo: a
  energia é `soma × passo_em_horas`, não a soma aritmética
- **UCBT e UCMT participam as duas** da correção, e são consolidadas
  **separadamente**. A mesma UC aparece em mais de uma linha *dentro* de uma
  tabela quando muda de situação no meio do ano — cada linha com os meses da sua
  fase — e aí somar as energias reconstrói o ano. Mas 3.197 `COD_ID` ativos
  estão nas **duas** tabelas ao mesmo tempo, em 523 dos 673 alimentadores: é a
  mesma unidade com dois pontos de conexão (o `PAC` muda, um termina em BT e o
  outro em MT), cada lado com a sua própria curva e a sua própria energia. Essas
  ficam como **duas linhas** na saída. Juntá-las somaria energias de curvas
  diferentes — em 135 casos os dois lados têm energia, e a demanda sairia até 50%
  inflada num deles enquanto o outro desaparecia
- `UCAT_tab` e `UGAT_tab` ficam de fora: 26 e 39 registros na base inteira, e
  sem coluna `CTMT` — não há como atribuí-los a um alimentador de média tensão
- quando a GD não está na base da ANEEL, a potência vem do `POT_INST` da UG, e
  esse campo **não é confiável na unidade**: conferido contra a ANEEL em 158 mil
  GDs, está em kW em 89% dos casos e em W em 1,4%. A trava é o teto legal —
  75 kW para microgeração em BT, 5 MW para minigeração em MT: acima dele, se o
  valor dividido por mil couber, era W e é convertido; se nem assim couber, é
  descartado e a UC vira `GD_SEM_POTENCIA`. Sem isso, uma UC monofásica de BT
  chegou a ser simulada com 89 mil kVA
- o `DEM_01..12` medido que a UCMT traz **não** entra no cálculo: a demanda
  desta ferramenta se baseia inteiramente no `ENE` e nas curvas da BDGD
- o motor NumPy é o padrão, e os dois concordam: 0,05% de desvio na energia
  anual gerada e 0,04% em média (0,22% no pior caso) nas demandas mensais das
  UCs com GD. O OpenDSS resolve o ano ponto a ponto e custa 19 s onde o NumPy
  custa 2,6 s no mesmo circuito — vale como validação cruzada, não como padrão

## Estatísticas dos alimentadores

Abaixo dos arquivos há duas abas: **Demanda**, com o painel do circuito e a tabela
de sempre, e **Estatísticas**, na largura toda, com o perfil das cargas de **todos** os alimentadores da base, uma linha cada. É
para escolher alimentadores com uma característica — os mais industriais, os
mais rurais, os de maioria monofásica — antes de calcular qualquer coisa.

- **Colunas**: participação das classes residencial, comercial, industrial,
  rural e outras; das ligações mono, bi e trifásicas; das UCs com GD; e a classe
  predominante. Clicar no cabeçalho ordena. As porcentagens têm uma barra de
  fundo, com a mesma largura de coluna em todas as tabelas, para a mesma
  porcentagem ter sempre o mesmo comprimento.
- **Participação por** quantidade de UCs ou por energia anual. As duas contam
  histórias diferentes: pela quantidade, o alimentador mais industrial da base
  tem 28% de UCs industriais; pela energia, há alimentadores com 95% da energia
  nelas.
- **Mínimo de UCs** (padrão 100) esconde alimentadores pequenos: com poucas UCs,
  uma carga só já vira 100% de uma classe, e eles dominariam qualquer ordenação.
- **Detalhe** do alimentador selecionado: todas as classes e as ligações, com
  quantidade, % das UCs, energia e % da energia. Duplo clique leva o alimentador
  para a aba Demanda.

A classe sai do prefixo da subclasse `CLAS_SUB`, pelo domínio do Módulo 10 do
PRODIST: `RE` residencial, `CO` comercial, `IN` industrial, `RU` rural, `PP`
poder público, `SP` serviço público, `IP` iluminação pública; `CPR`, `CSPS` e o
que vier sem subclasse caem em "Outras". A ligação é o número de fases no
`FAS_CON` da própria UC.

A contagem é um `GROUP BY` na base inteira — nenhuma demanda é calculada — e
roda a **mesma consulta SQL** nos dois formatos (no `.gdb`, pelo dialeto SQLite
do GDAL). Antes de contar, junta as linhas da mesma UC, como a aba Demanda: quem
mudou de subclasse no meio do ano (RE1 → RE2) tem duas linhas ativas e contaria
duas vezes. O resultado vai para `cache_bdgd/`: a primeira vez leva 7 s no
`.gpkg` e um minuto no `.gdb`; depois, instantâneo. 1.578 UCs da base estão sem
alimentador (`CTMT` vazio) e ficam de fora, com aviso no rodapé.

## Curva sintética do alimentador

Depois de calcular as demandas, o botão **Curva do alimentador** soma carga e
geração de todas as UCs numa curva anual do alimentador — por fase e total, sem
perdas — e resume o desequilíbrio entre fases em indicadores. É o critério para
escolher, na BDGD, os alimentadores que valem um estudo de desequilíbrio. Um
alimentador por vez; o ranking entre eles fica por conta de quem compara os
indicadores exportados.

### Como cada UC chega a uma fase do alimentador

A letra de fase de uma UC de BT é do **secundário** do transformador, não do
alimentador: na base da Energisa MT, 130 mil trafos têm primário só em `A`, e 25
mil têm primário em `B` com secundário rotulado `AN`. Repartir pela letra da UC
chega a inverter a conclusão — no CTMT 2014969 a fase B saía com 1,2% da energia
pela letra e 63,8% pelo primário. Por isso:

| UC | Fases usadas |
|---|---|
| UCMT | o próprio `FAS_CON` |
| UCBT em trafo mono ou bifásico | o primário do trafo (`UNTRMT.FAS_CON_P`) |
| UCBT em trafo trifásico | a letra da UC — aproximação: num Dyn uma carga monofásica do secundário puxa corrente de duas fases do primário |
| UCBT sem trafo encontrado | a letra da UC, contada no relatório |

A **geração** sai pelas fases das UGs do mesmo `CEG_GD`, desde que sejam um
subconjunto das fases da UC; se não forem, ou se a UG não tiver fase, usa as da
UC e entra no relatório. Depois passa pela mesma projeção do trafo. Dentro das
fases escolhidas, a potência se divide em partes iguais.

UGs do alimentador que não pertencem a nenhuma UC — uma PCH, por exemplo — ficam
**fora da curva**; o relatório diz quantas são e quanto injetaram.

### Indicadores

Calculados para a curva **líquida** (carga − geração, o que o alimentador vê) e
para a de **só carga**:

- **desequilíbrio absoluto**: fase mais carregada menos a menos carregada, em kW
  — máximo, média e P95;
- **desequilíbrio relativo**, à moda NEMA sobre magnitudes (a corrente de cada
  fase acompanha `|P|`): maior desvio de uma fase em relação à média das três,
  dividido pela média. Instantes em que a média fica abaixo de 5% do seu máximo
  anual são descartados — perto do cruzamento de zero da curva líquida o
  indicador explodiria;
- pico e vale da soma das fases, energia e participação de cada fase.

O **P95** é o mais útil para ordenar: ignora os 5% de instantes mais extremos.

A janela do resultado tem duas abas. **Gráfico** mostra a potência de cada fase
e o total trifásico ao longo do ano (líquida, só carga ou só geração), em
QtCharts, com a mesma interação do app de referência: roda para zoom no tempo,
Ctrl + roda no eixo vertical, arrasto, clique para fixar o cursor de leitura,
duplo clique para voltar ao ano inteiro e clique na legenda para esconder uma
série. **Relatório** traz os indicadores e a atribuição de fases.

A exportação grava `CURVA_<ctmt>.csv` (35.136 instantes de 15 min), mais
`_indicadores.csv` e `_excecoes.csv` — as UCs que caíram em algum fallback.

### O que a curva é, e o que não é

É a curva **esperada** do alimentador. A energia de cada UC é respeitada
exatamente, e as curvas típicas da CRVCRG já são médias de classe, então a
diversidade entre classes aparece e o pico da soma fica abaixo de Σ `DEM_MAX`. O
que ela não tem: variação dentro de uma classe, perdas, iluminação pública
(tabela `PIP`), UGs sem UC, e uma medição de referência — a energia de entrada
que a `CTMT` registra não fecha com a soma das UCs (a razão varia de 0,43 a 6,8
entre alimentadores).

## Bater com o app de referência

As duas ferramentas implementam o mesmo modelo e chegam ao mesmo número —
`verificar.py --referencia ../TESTE_CORRECAO_CARGA` compara a demanda de janeiro
UC a UC e fecha em **4e-13**. Elas compartilham o `irradiancia.py`, arquivo por
arquivo, e a mesma convenção de recorte: no app de referência, `01/01/2024 00:00`
→ `01/02/2024 00:00` em 15 min dá 2976 blocos e demanda 1,83 kW, o mesmo valor
desta ferramenta.

Restam dois parâmetros para conferir na tela antes de comparar:

- **o passo**, 15 minutos nos dois. Em 1 hora a demanda cai 6%: o ceifamento
  `max(D·C − G, 0)` é não linear, e médias horárias escondem os cruzamentos
  entre carga e geração dentro da hora
- **o motor.** O app de referência abre em OpenDSS, este em NumPy; a diferença
  na demanda é de 0,02%

A energia importada é a mesma nos dois casos, por construção — é ela que está
sendo invertida.

## Desempenho

O trabalho pesado é a geração fotovoltaica, e ela é simulada **por razão
kW/kVA**, não por UC: no modelo do `PVSystem` a potência AC por kVA instalado
depende só dessa razão, então 149 GDs viram 113 simulações. A razão entra na
chave do cache exatamente como é — arredondá-la juntaria mais UCs por simulação,
mas desviaria a demanda de quem não caísse no valor redondo. A curva de carga,
por sua vez, é montada uma vez por código da `CRVCRG` e fatiada por mês.

Alimentador 764500 (2.890 UCs ativas, 152 com GD), motor NumPy, passo de 15 min:

```
BDGD          0,9 s
ANEEL         5,0 s     varredura de 3,95 milhões de linhas
irradiância   0,0 s
geração       2,8 s
demanda       0,3 s
```

O maior alimentador da base (13.616 UCs ativas, 718 com GD) fecha em 18 s.

### O índice do File Geodatabase

Um `.gpkg` é um SQLite e filtra por `CTMT` em 0,7 s. Um `.gdb` não: ele **não tem
índice de atributo em nenhuma tabela de dados** (os 17 `.atx` pertencem todos ao
catálogo interno), e cada consulta vira uma varredura sequencial dos 2,1 milhões
de registros da UCBT — 40 s.

A saída está no `.gdbtablx`, que mapeia FID para deslocamento no arquivo: ler por
FID é quase de graça (3 mil linhas espalhadas em 0,09 s). Então a primeira
abertura varre **só `CTMT` e `SIT_ATIV`** das quatro tabelas, em 26 s, e monta um
índice `CTMT → FIDs ativos` que fica em `cache_bdgd/` com **4 MB**. Dali em
diante, carregar um alimentador custa **0,18 s** — mais rápido que pelo próprio
GeoPackage. O nome do arquivo de cache carrega tamanho e data da BDGD, então uma
base nova refaz o índice sozinha.

| | `.gpkg` | `.gdb` sem índice | `.gdb` com o índice |
|---|---|---|---|
| carregar um alimentador | 0,74 s | 38–54 s | **0,18 s** |
