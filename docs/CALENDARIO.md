# Calendário municipal

O arquivo contém códigos IBGE de sete dígitos e deve usar UTF-8. O exemplo abaixo é **uma fixture**, não uma declaração oficial para Cuiabá:

```yaml
versao: '1.0'
municipios:
  '5103403':
    nome: Cuiabá
    uf: MT
    cobertura:
      inicio: 2024-01-01
      fim: 2024-12-31
      declarada_completa: false
    feriados: []
    ajustes: []
```

Um feriado local usa `data`, `fonte` e, opcionalmente, `descricao`. Um ajuste usa também `acao: incluir` ou `acao: excluir` e `justificativa`. Use datas ISO (`AAAA-MM-DD`) únicas no município. A UF deve coincidir com o prefixo IBGE.

Só marque `declarada_completa: true` depois de conferir as fontes oficiais de todo o período. Não é necessário repetir os feriados nacionais ou estaduais. Pontos facultativos entram apenas como inclusões explícitas documentadas.

A precedência é: feriado aplicável → DO; domingo → DO; sábado → SA; demais dias → DU. Feriados no fim de semana são contados uma vez. O núcleo de domínio recebe os conjuntos prontos, sem ler YAML nem importar a biblioteca de feriados.
