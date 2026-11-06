# CIP — Gestão da CIP/COSIP

Sistema de gestão dos valores arrecadados da CIP/COSIP (Contribuição para o
Custeio do Serviço de Iluminação Pública) da Prefeitura Municipal de Santo André.

Publicado em **https://cip.sistemagip.tech**.

> Estado atual: **esqueleto** — login, perfis (Administrador e Visualização),
> usuários e auditoria. As funções de negócio serão desenvolvidas depois.

## Estrutura

| Arquivo | Função |
|---|---|
| `app.py` | Aplicação Flask (rotas, perfis, auditoria, criação das tabelas) |
| `db.py` | Mesmo código em SQLite (local) e PostgreSQL (Supabase) |
| `schema_postgres.sql` | Tabelas do schema `cip` (rodado pelo app a cada início) |
| `templates/`, `static/` | Telas Jinja e estilo |
| `deploy/supabase_cip_setup.sql` | Criação única do schema `cip` e do usuário `cip_app` |
| `deploy/cip.service` | Serviço systemd (gunicorn na porta 5002) |
| `deploy/nginx-cip.conf` | Site Nginx de `cip.sistemagip.tech` |

## Banco de dados

- Projeto Supabase **compartilhado** com outro sistema; o CIP usa só o schema
  `cip`, com o usuário dedicado `cip_app` (`search_path = cip`).
- O app **se recusa a iniciar** se a conexão não estiver no schema `cip` —
  nunca cria nem altera tabelas no `public`.
- Toda tabela: RLS ligado e `REVOKE ALL ... FROM anon, authenticated`.
- Mudança de banco = **só tabelas novas** (`CREATE TABLE IF NOT EXISTS`), no
  `SCHEMA` do `app.py` (SQLite) **e** no `schema_postgres.sql`.
- Conexão pelo pooler, porta 6543, usuário `cip_app.<ref>`, senha só com letras
  e números (um `@` quebra a URL).
- Plano gratuito: sem backup automático e pausa após ~7 dias sem uso
  (site dá 502 até *Restore project*). Fazer exportação periódica do schema.

## Desenvolvimento local (Windows / PowerShell)

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
$env:CIP_HTTPS="0"
$env:CIP_ADMIN_EMAIL="admin@santoandre.sp.gov.br"
$env:CIP_ADMIN_SENHA_INICIAL="uma-senha-local"
python app.py            # http://127.0.0.1:5002  (SQLite em cip.db)
```

## Fluxo de atualização

sandbox → testar com banco de cópia → GitHub → no servidor:

```bash
cd /opt/cip && sudo -u cip git pull && sudo systemctl restart cip
```

Nunca editar código direto no servidor.

## Manutenção no servidor

```bash
cd /opt/cip
sudo bash -c 'set -a; . /etc/cip.env; set +a; sudo -E -u cip venv/bin/python app.py --usuarios'
sudo bash -c 'set -a; . /etc/cip.env; set +a; sudo -E -u cip venv/bin/python app.py --senha admin@santoandre.sp.gov.br'
```

A senha é pedida sem aparecer na tela e não fica no histórico.

## Variáveis de ambiente (`/etc/cip.env`, chmod 600)

Ver `.env.example`: `CIP_SECRET_KEY`, `DATABASE_URL`, `CIP_HTTPS`,
`CIP_ADMIN_EMAIL`, `CIP_ADMIN_SENHA_INICIAL` (apagar após o primeiro acesso).
