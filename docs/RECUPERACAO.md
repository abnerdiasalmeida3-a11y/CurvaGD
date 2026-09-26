# Recuperação e rastreabilidade

Cada importação escreve em `staging/<import_id>`, valida contagens e promove uma pasta imutável para `datasets`. A atualização do catálogo ocorre em uma transação. Cancelamento ou falha anteriores à promoção removem apenas o staging dessa operação; relatórios de falha permanecem disponíveis.

Se ocorrer falha depois da promoção da pasta e antes do commit, a versão anterior permanece ativa. A nova pasta contém `ORFA_RECUPERAVEL.txt` e não é usada pelas consultas. Não a trate como importação ativa apenas porque seu manifesto diz READY.

Para recuperar:

1. Feche o aplicativo e consulte `logs/app.log` e o relatório Markdown da operação.
2. Faça uma cópia de segurança do armazenamento completo, incluindo `catalog.duckdb` e os Parquet.
3. Reimporte a fonte original. Isso cria outro identificador e uma nova transação.
4. Após validar a nova versão, uma pasta órfã identificada pode ser removida manualmente. Nunca remova pastas registradas no catálogo nem o diretório `datasets` completo.

Uma interrupção forçada do processo pode deixar staging residual. Com o aplicativo fechado, confirme o identificador no log e remova apenas essa pasta incompleta. Não há limpeza automática de versões históricas ou de diretórios órfãos durante a abertura.

O manifesto registra SHA-256 de cada arquivo de entrada, contratos, versões das bibliotecas relevantes, contagens e arquivos Parquet. Os CSVs detalhados preservam valores originais resumidos; a fonte original permanece inalterada. Prefixos potencialmente executáveis como fórmulas de planilha são escapados nos relatórios CSV.

Não execute dois processos escritores no mesmo catálogo. Encerre a interface antes de usar a CLI para importar no mesmo armazenamento. Consultas e importação em threads de um único aplicativo são suportadas.
