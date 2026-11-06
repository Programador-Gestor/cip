-- Preparação do schema "cip" no projeto Supabase compartilhado (etapa 3).
-- Executado UMA vez, como postgres (SQL Editor ou conector). Idempotente.
--
-- Não toca no schema public nem em nenhuma tabela existente: só cria o
-- usuário cip_app e o schema cip, e fecha o acesso da API (anon/authenticated).
--
-- A SENHA do cip_app NÃO está aqui. Defina-a você mesmo, no SQL Editor:
--   ALTER ROLE cip_app WITH PASSWORD 'SomenteLetrasENumeros';
-- e use a mesma senha só no /etc/cip.env.
--
-- Para desfazer tudo (sem afetar outros sistemas):
--   DROP SCHEMA cip CASCADE;  DROP ROLE cip_app;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cip_app') THEN
        CREATE ROLE cip_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20;
    END IF;
END
$$;

-- Permite ao postgres criar o schema em nome do cip_app.
GRANT cip_app TO postgres;

CREATE SCHEMA IF NOT EXISTS cip AUTHORIZATION cip_app;

-- O cip_app enxerga só o schema cip.
ALTER ROLE cip_app SET search_path = cip;

-- A API do Supabase não acessa o schema cip.
REVOKE ALL ON SCHEMA cip FROM PUBLIC;
REVOKE ALL ON SCHEMA cip FROM anon, authenticated;

-- Tabelas/sequências/funções criadas no futuro pelo cip_app nascem fechadas.
ALTER DEFAULT PRIVILEGES FOR ROLE cip_app IN SCHEMA cip
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE cip_app IN SCHEMA cip
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE cip_app IN SCHEMA cip
    REVOKE ALL ON FUNCTIONS FROM PUBLIC, anon, authenticated;
