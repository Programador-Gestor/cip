-- Schema PostgreSQL do CIP (schema "cip" do projeto Supabase compartilhado)
--
-- Executado pelo próprio app a cada inicialização, conectado como cip_app
-- (search_path = cip). Por isso tudo aqui é idempotente e sem nome de schema:
-- as tabelas caem no schema cip, e o app recusa iniciar se a conexão estiver
-- em outro schema.
--
-- Regras:
--   * Mudança de banco = só tabelas novas (CREATE TABLE IF NOT EXISTS),
--     refletidas também no SCHEMA (SQLite) do app.py.
--   * Toda tabela nova: ENABLE ROW LEVEL SECURITY + REVOKE de anon/authenticated.
--
-- Datas em TEXT ISO, como no Gestão ARP (o app grava com isoformat()).

CREATE TABLE IF NOT EXISTS usuarios (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    nome         TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    senha        TEXT NOT NULL,
    perfil       TEXT NOT NULL CHECK (perfil IN ('admin', 'viewer')),
    ativo        INTEGER NOT NULL DEFAULT 1,
    criado_em    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auditoria (
    id        INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    usuario   TEXT,
    acao      TEXT NOT NULL,
    detalhe   TEXT,
    criado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auditoria_criado ON auditoria(criado_em);

-- Segurança: a API REST do Supabase (anon/authenticated) não acessa nada.
ALTER TABLE usuarios  ENABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE usuarios  FROM anon, authenticated;
REVOKE ALL ON TABLE auditoria FROM anon, authenticated;
