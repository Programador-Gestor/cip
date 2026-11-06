# CIP — Gestão da CIP/COSIP (Santo André)

Publicado em **https://cip.sistemagip.tech**. Repositório privado `Programador-Gestor/cip`.

Todo mês o administrador envia o arquivo `B_A_SAN_COSIP.txt` da Enel. O sistema mostra na tela
(e em PDF / Word / Excel) os 4 indicadores abaixo e mantém a base histórica dos valores da CIP.

**Nenhum dado pessoal fica guardado.** O arquivo da Enel é lido, analisado e apagado; o banco
guarda só os totais. O Excel da análise (com nomes e CPF/CNPJ) é gerado na hora e entregue para
download, sem ficar no servidor.

## Indicadores

Mês do arquivo = mês do cabeçalho (posições 3–8). Mês da fatura = mês de referência (161–166),
nunca a data de pagamento. Só Santo André (cidade, 128–152). CPF/CNPJ pelo campo 242–256,
conferido pelos dígitos verificadores. Cada indicador de 1 a 3 mostra **registros** (cada pagamento)
e, abaixo, **distintos** (cada CPF/CNPJ contado uma vez), para CPF, CNPJ e total. CIP zero (só juros/multa) e estornos (CIP negativa)
ficam fora das contagens de 1 a 3.

| # | Indicador | Regra |
|---|---|---|
| 1 | Pagamentos de faturas do mês do arquivo | Referência = mês do arquivo, CIP > 0 |
| 2 | … com valor diferente da CIP do mês | Dentre as do indicador 1, CIP ≠ valor do mês na base histórica (CPF e CNPJ separados) |
| 3 | Pagamentos de faturas de outros meses | Referência ≠ mês do arquivo, CIP > 0 |
| 4 | Valor arrecadado | CIP + juros + multa, estornos descontados: faturas do mês do arquivo, faturas de outros meses (lista mês a mês recolhível) e total |

O que ficou fora das contagens (outros municípios, sem CPF/CNPJ, CIP zero/estorno) aparece em
"Fora das contagens e conferência", no fim da tela. Antes de gravar, o sistema confere que as
quantidades e os valores fecham entre si e com o rodapé da Enel; se não fecharem, recusa o arquivo.

## Base histórica de valores da CIP

- Começa com a planilha `Valores_CIP.xlsx` (mar/26 a nov/26), arredondada para centavos como a Enel cobra.
- A cada arquivo, o sistema identifica o valor mais pago de CPF e de CNPJ nas faturas do mês do arquivo:
  - igual à base → usa a base;
  - mês fora da base → pergunta ao administrador se acrescenta o valor encontrado;
  - diferente da base → mostra os dois e o administrador escolhe manter a base ou atualizar.
  Até a escolha, o arquivo fica "aguardando confirmação" e os indicadores não aparecem.
- Tela **Valores da CIP**: lista, baixa a planilha (mesmo formato) e importa planilha nova.
  A importação só acrescenta meses novos; mês com valor diferente é mostrado e só muda se o
  administrador clicar em "Substituir". Toda alteração guarda o valor anterior (registro de alterações).

## Acesso (como no Gestão ARP)

Entrada com **e-mail institucional e senha**. Senha mínima de 8 caracteres, guardada só como hash.

| Perfil | Pode |
|---|---|
| Administrador | Enviar arquivos, confirmar o valor da CIP do mês, manter a base de valores, os usuários e a auditoria, baixar o Excel da análise |
| Visualização | Consultar os arquivos já processados e baixar os resumos (PDF, Word, Excel) |

Usuários são criados na tela **Usuários**. Sempre existe pelo menos um administrador ativo.
Tela **Auditoria**: login (e tentativas negadas), envio de arquivo, confirmação/cancelamento,
importação e substituição de valores, usuários e downloads do Excel da análise (horário de Brasília).

## Excel da análise (dados + fórmulas) — só administrador

Na tela do resultado, **Baixar Excel da análise**: o administrador seleciona de novo o mesmo
arquivo da Enel daquele mês. O sistema confere pelo SHA-256 que é o mesmo arquivo, gera o Excel
e entrega para download; o `.txt` e o Excel são apagados na hora. Um Excel por vez (≈ 1 minuto
e ≈ 700 MB de memória para 460 mil linhas).

- **Indicadores**: parâmetros (mês, cidade, valor da CIP de CPF e CNPJ, células amarelas) e cada
  indicador com o resultado da fórmula, o texto da fórmula, o número do sistema e "Confere?".
- **Dados**: todos os registros do arquivo da Enel, como vieram, e as colunas de fórmula
  (CIP com sinal, valor pago, validação de CPF/CNPJ, tipo, Santo André?, escopo, contagens).

## Banco de dados

- Projeto Supabase **compartilhado**; o CIP usa só o schema `cip`, usuário dedicado `cip_app`
  (`search_path = cip`). O sistema **se recusa a iniciar** fora do schema `cip`.
- Tabelas: `usuarios`, `auditoria`, `valores_cip`, `valores_cip_alteracoes`, `arquivos_cip`
  (só totais). Criadas pelo próprio sistema a partir de `schema_postgres.sql` (idempotente),
  com RLS e sem acesso para anon/authenticated.
- Mudança de banco = **só tabelas novas**, em `schema_postgres.sql` **e** no `SCHEMA_SQLITE`
  de `cosip/db.py`.
- Conexão pelo pooler, porta 6543, usuário `cip_app.<ref>`, senha só com letras e números.
- Plano gratuito: sem backup automático e pausa após ~7 dias sem uso. Fazer exportação periódica.

## Estrutura

```
app.py                     Flask: login, upload, confirmação, resultado, downloads, valores, usuários, auditoria
cosip/leiaute.py           posições dos campos do arquivo
cosip/leitor.py            leitura + validação + conferência com o rodapé
cosip/documentos.py        CPF / CNPJ
cosip/analise.py           os 4 indicadores
cosip/valores.py           base histórica (planilha ↔ centavos)
cosip/acesso.py            login, perfis, CSRF
cosip/db.py                SQLite (local) ou Postgres/Supabase (DATABASE_URL), trava do schema cip
cosip/relatorios.py        PDF, Word, Excel (resumo dos indicadores)
cosip/planilha_analise.py  Excel da análise: todos os registros + fórmulas
cosip/excel_analise.py     geração sob demanda, um por vez, sem guardar nada
templates/, static/        telas
schema_postgres.sql        tabelas do schema cip
deploy/                    systemd, Nginx, preparação do schema no Supabase
tests/                     testes (pytest)
```

## Rodar localmente (Windows / PowerShell)

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements-dev.txt
$env:CIP_HTTPS="0"
$env:CIP_ADMIN_EMAIL="admin@santoandre.sp.gov.br"
$env:CIP_ADMIN_SENHA_INICIAL="uma-senha-local"
flask --app app run --debug --port 5002      # http://127.0.0.1:5002  (SQLite em instance/cip.db)
python -m pytest -q
```

Testes com o arquivo real de agosto/2026: `$env:CIP_ARQUIVO_AGOSTO="C:\caminho\B_A_SAN_COSIP.txt"`.

## Atualizar no servidor

sandbox → testes → GitHub → no servidor (como root):

```bash
cd /opt/cip && git pull && venv/bin/pip install -r requirements.txt -q && systemctl restart cip
```

Nunca editar código direto no servidor.

## Manutenção no servidor

```bash
cd /opt/cip
sudo bash -c 'set -a; . /etc/cip.env; set +a; sudo -E -u cip venv/bin/flask --app app usuarios'
sudo bash -c 'set -a; . /etc/cip.env; set +a; sudo -E -u cip venv/bin/flask --app app senha admin@santoandre.sp.gov.br'
sudo bash -c 'set -a; . /etc/cip.env; set +a; sudo -E -u cip venv/bin/flask --app app criar-admin'
```
