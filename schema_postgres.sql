-- Sistema CIP/COSIP — schema Postgres (schema "cip" do projeto Supabase compartilhado)
--
-- Executado pelo próprio sistema a cada inicialização, conectado como cip_app
-- (search_path = cip). Tudo é idempotente e sem nome de schema nas tabelas:
-- elas caem no "cip", e o sistema se recusa a iniciar se a conexão estiver em
-- outro schema.
--
-- Guarda só totais: nenhum nome, CPF/CNPJ ou endereço. O arquivo da Enel e o
-- Excel da análise não são gravados em lugar nenhum.
--
-- Regras: mudança de banco = só tabelas novas (CREATE TABLE IF NOT EXISTS),
-- refletidas também no SCHEMA_SQLITE (cosip/db.py). Toda tabela com RLS ligado
-- e sem acesso para anon/authenticated.

-- Usuários e auditoria (criadas na publicação do esqueleto; mantidas iguais)
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

-- Base histórica dos valores da CIP (centavos) e registro de toda alteração
CREATE TABLE IF NOT EXISTS valores_cip (
    mes CHAR(6) PRIMARY KEY,               -- AAAAMM, mês de referência da fatura
    valor_cpf BIGINT NOT NULL,
    valor_cnpj BIGINT NOT NULL,
    origem TEXT NOT NULL,
    registrado_em TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS valores_cip_alteracoes (
    id BIGSERIAL PRIMARY KEY,
    mes CHAR(6) NOT NULL,
    cpf_anterior BIGINT NOT NULL,
    cnpj_anterior BIGINT NOT NULL,
    cpf_novo BIGINT NOT NULL,
    cnpj_novo BIGINT NOT NULL,
    motivo TEXT NOT NULL,
    alterado_em TIMESTAMPTZ NOT NULL
);

-- Arquivos mensais processados: só os totais dos indicadores (JSON)
CREATE TABLE IF NOT EXISTS arquivos_cip (
    id BIGSERIAL PRIMARY KEY,
    processado_em TIMESTAMPTZ NOT NULL,
    nome_arquivo TEXT NOT NULL,
    hash_sha256 CHAR(64) NOT NULL UNIQUE,
    mes_arquivo CHAR(6) NOT NULL,
    situacao TEXT NOT NULL,                -- concluido | pendente
    valor_total BIGINT NOT NULL,           -- centavos
    resultado TEXT NOT NULL,
    pendencia TEXT
);
CREATE INDEX IF NOT EXISTS idx_arquivos_cip_mes ON arquivos_cip (mes_arquivo);

-- Segurança: a API REST do Supabase (anon/authenticated) não acessa nada.
ALTER TABLE usuarios               ENABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria              ENABLE ROW LEVEL SECURITY;
ALTER TABLE valores_cip            ENABLE ROW LEVEL SECURITY;
ALTER TABLE valores_cip_alteracoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE arquivos_cip           ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON usuarios, auditoria, valores_cip, valores_cip_alteracoes, arquivos_cip FROM anon, authenticated;
REVOKE ALL ON SEQUENCE valores_cip_alteracoes_id_seq, arquivos_cip_id_seq FROM anon, authenticated;
