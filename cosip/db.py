"""Banco: SQLite local, Postgres (Supabase) em produção.

Tabelas (schema "cip" no Supabase compartilhado):
- valores_cip            base histórica dos valores da CIP (CPF e CNPJ por mês, em centavos)
- valores_cip_alteracoes registro de toda mudança de valor já cadastrado (nada é apagado)
- arquivos_cip           cada arquivo mensal processado: só totais (nenhum nome, CPF ou CNPJ)
- usuarios               acesso (e-mail + senha em hash; perfis admin e viewer)
- auditoria              quem fez o quê

O arquivo da Enel e o Excel da análise NUNCA são gravados: só os totais.

DATABASE_URL=postgresql://...   -> Postgres (usuário cip_app, search_path = cip)
sem DATABASE_URL                -> SQLite em CIP_DB_PATH (padrão: instance/cip.db)
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .analise import Resultado
from .valores import ValorCIP, valores_iniciais

# Único schema do Postgres em que o sistema pode trabalhar.
SCHEMA_CIP = "cip"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS valores_cip (
    mes TEXT PRIMARY KEY,
    valor_cpf INTEGER NOT NULL,
    valor_cnpj INTEGER NOT NULL,
    origem TEXT NOT NULL,
    registrado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS valores_cip_alteracoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mes TEXT NOT NULL,
    cpf_anterior INTEGER NOT NULL,
    cnpj_anterior INTEGER NOT NULL,
    cpf_novo INTEGER NOT NULL,
    cnpj_novo INTEGER NOT NULL,
    motivo TEXT NOT NULL,
    alterado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arquivos_cip (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processado_em TEXT NOT NULL,
    nome_arquivo TEXT NOT NULL,
    hash_sha256 TEXT NOT NULL UNIQUE,
    mes_arquivo TEXT NOT NULL,
    situacao TEXT NOT NULL,          -- concluido | pendente
    valor_total INTEGER NOT NULL,
    resultado TEXT NOT NULL,         -- JSON com os indicadores (só totais)
    pendencia TEXT                   -- JSON com a divergência a confirmar
);
CREATE TABLE IF NOT EXISTS usuarios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nome         TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    senha        TEXT NOT NULL,
    perfil       TEXT NOT NULL CHECK (perfil IN ('admin', 'viewer')),
    ativo        INTEGER NOT NULL DEFAULT 1,
    criado_em    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auditoria (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario    TEXT,
    acao       TEXT NOT NULL,
    detalhe    TEXT,
    criado_em  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auditoria_criado ON auditoria(criado_em);
"""


class SchemaErrado(RuntimeError):
    pass


def _url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


def _eh_postgres() -> bool:
    u = _url()
    return bool(u) and u.startswith(("postgres://", "postgresql://"))


def descricao() -> str:
    return "PostgreSQL" if _eh_postgres() else f"SQLite ({_caminho_sqlite()})"


def _caminho_sqlite() -> Path:
    return Path(os.environ.get("CIP_DB_PATH", "instance/cip.db"))


@contextmanager
def conectar():
    if _eh_postgres():
        import psycopg
        # prepare_threshold=None: o pooler do Supabase (porta 6543, modo transação)
        # não suporta prepared statements.
        con = psycopg.connect(_url(), prepare_threshold=None)
    else:
        caminho = _caminho_sqlite()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(caminho)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _sql(q: str) -> str:
    return q.replace("?", "%s") if _eh_postgres() else q


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conferir_schema() -> None:
    """Impede o sistema de tocar em qualquer schema que não seja o "cip".

    O usuário cip_app tem search_path = cip. Se a conexão cair em outro schema
    (usuário errado na DATABASE_URL, schema ainda não criado), paramos antes de
    qualquer CREATE TABLE — nada é criado no public nem em outro sistema.
    """
    if not _eh_postgres():
        return
    with conectar() as con:
        cur = con.cursor()
        cur.execute("SELECT current_schema()")
        atual = cur.fetchone()[0]
    if atual != SCHEMA_CIP:
        raise SchemaErrado(
            f"Conexão no schema '{atual}', esperado '{SCHEMA_CIP}'. Confira o usuário da "
            "DATABASE_URL (cip_app.<ref>) e o search_path no Supabase. Nada foi alterado.")


def _schema_postgres() -> str:
    return (Path(__file__).resolve().parent.parent / "schema_postgres.sql").read_text(encoding="utf-8")


def inicializar() -> None:
    """Cria as tabelas que faltarem (idempotente) e carrega a base histórica inicial se vazia."""
    conferir_schema()
    with conectar() as con:
        if _eh_postgres():
            con.cursor().execute(_schema_postgres())
        else:
            con.executescript(SCHEMA_SQLITE)
    with conectar() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM valores_cip")
        if cur.fetchone()[0] == 0:
            agora = _agora()
            cur.executemany(_sql("INSERT INTO valores_cip (mes, valor_cpf, valor_cnpj, origem, registrado_em) "
                                 "VALUES (?, ?, ?, ?, ?)"),
                            [(v.mes, v.cpf, v.cnpj, "Planilha Valores_CIP.xlsx", agora) for v in valores_iniciais()])


# ---------------------------------------------------------------- valores da CIP
def valores() -> list[ValorCIP]:
    with conectar() as con:
        cur = con.cursor()
        cur.execute("SELECT mes, valor_cpf, valor_cnpj, origem FROM valores_cip ORDER BY mes")
        return [ValorCIP(*r) for r in cur.fetchall()]


def valor_do_mes(mes: str) -> ValorCIP | None:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("SELECT mes, valor_cpf, valor_cnpj, origem FROM valores_cip WHERE mes = ?"), (mes,))
        r = cur.fetchone()
        return ValorCIP(*r) if r else None


def incluir_valor(v: ValorCIP, origem: str) -> bool:
    """Acrescenta um mês novo. Nunca sobrescreve: devolve False se o mês já existe."""
    if valor_do_mes(v.mes):
        return False
    with conectar() as con:
        con.cursor().execute(_sql("INSERT INTO valores_cip (mes, valor_cpf, valor_cnpj, origem, registrado_em) "
                                  "VALUES (?, ?, ?, ?, ?)"), (v.mes, v.cpf, v.cnpj, origem, _agora()))
    return True


def alterar_valor(v: ValorCIP, origem: str, motivo: str) -> None:
    """Muda o valor de um mês já cadastrado, guardando o anterior em valores_cip_alteracoes."""
    atual = valor_do_mes(v.mes)
    if atual is None:
        incluir_valor(v, origem)
        return
    if (atual.cpf, atual.cnpj) == (v.cpf, v.cnpj):
        return
    agora = _agora()
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("INSERT INTO valores_cip_alteracoes (mes, cpf_anterior, cnpj_anterior, cpf_novo, "
                         "cnpj_novo, motivo, alterado_em) VALUES (?, ?, ?, ?, ?, ?, ?)"),
                    (v.mes, atual.cpf, atual.cnpj, v.cpf, v.cnpj, motivo, agora))
        cur.execute(_sql("UPDATE valores_cip SET valor_cpf = ?, valor_cnpj = ?, origem = ?, registrado_em = ? "
                         "WHERE mes = ?"), (v.cpf, v.cnpj, origem, agora, v.mes))


def alteracoes() -> list[dict]:
    with conectar() as con:
        cur = con.cursor()
        cur.execute("SELECT mes, cpf_anterior, cnpj_anterior, cpf_novo, cnpj_novo, motivo, alterado_em "
                    "FROM valores_cip_alteracoes ORDER BY id DESC")
        cols = ["mes", "cpf_anterior", "cnpj_anterior", "cpf_novo", "cnpj_novo", "motivo", "alterado_em"]
        return [dict(zip(cols, r[:-1] + (str(r[-1]),))) for r in cur.fetchall()]


# ---------------------------------------------------------------- arquivos processados
def buscar_por_hash(hash_sha256: str) -> int | None:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("SELECT id FROM arquivos_cip WHERE hash_sha256 = ?"), (hash_sha256,))
        r = cur.fetchone()
        return r[0] if r else None


def salvar(r: Resultado, pendencia: dict | None = None) -> int:
    vals = (_agora(), r.nome_arquivo, r.hash_sha256, r.mes_arquivo, "pendente" if pendencia else "concluido",
            r.valor_total, json.dumps(r.para_dict(), ensure_ascii=False),
            json.dumps(pendencia, ensure_ascii=False) if pendencia else None)
    q = ("INSERT INTO arquivos_cip (processado_em, nome_arquivo, hash_sha256, mes_arquivo, situacao, "
         "valor_total, resultado, pendencia) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    with conectar() as con:
        cur = con.cursor()
        if _eh_postgres():
            cur.execute(_sql(q + " RETURNING id"), vals)
            return cur.fetchone()[0]
        cur.execute(q, vals)
        return cur.lastrowid


def concluir(aid: int, r: Resultado) -> None:
    with conectar() as con:
        con.cursor().execute(_sql("UPDATE arquivos_cip SET situacao = 'concluido', resultado = ?, pendencia = NULL "
                                  "WHERE id = ?"), (json.dumps(r.para_dict(), ensure_ascii=False), aid))


def carregar(aid: int) -> dict | None:
    """{'id', 'processado_em', 'situacao', 'resultado': Resultado, 'pendencia': dict|None}"""
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("SELECT processado_em, situacao, resultado, pendencia FROM arquivos_cip WHERE id = ?"),
                    (aid,))
        row = cur.fetchone()
    if not row:
        return None
    em, situacao, res, pend = row
    return {"id": aid, "processado_em": str(em), "situacao": situacao,
            "resultado": Resultado.de_dict(json.loads(res)),
            "pendencia": json.loads(pend) if pend else None}


def excluir(aid: int) -> None:
    with conectar() as con:
        con.cursor().execute(_sql("DELETE FROM arquivos_cip WHERE id = ?"), (aid,))


def listar() -> list[dict]:
    with conectar() as con:
        cur = con.cursor()
        cur.execute("SELECT id, processado_em, nome_arquivo, mes_arquivo, situacao, valor_total "
                    "FROM arquivos_cip ORDER BY mes_arquivo DESC, id DESC")
        cols = ["id", "processado_em", "nome_arquivo", "mes_arquivo", "situacao", "valor_total"]
        return [dict(zip(cols, (str(x) if i == 1 else x for i, x in enumerate(r)))) for r in cur.fetchall()]


# ---------------------------------------------------------------- usuários
PERFIS = ("admin", "viewer")
_COLS_USU = "id, nome, email, senha, perfil, ativo, criado_em"


def _usuario(row) -> dict | None:
    if not row:
        return None
    u = dict(zip(["id", "nome", "email", "senha", "perfil", "ativo", "criado_em"], row))
    u["ativo"] = bool(u["ativo"])
    u["criado_em"] = str(u["criado_em"])
    return u


def usuario_por_email(email: str) -> dict | None:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql(f"SELECT {_COLS_USU} FROM usuarios WHERE LOWER(email) = ?"), ((email or "").strip().lower(),))
        return _usuario(cur.fetchone())


def usuario_por_id(uid: int) -> dict | None:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql(f"SELECT {_COLS_USU} FROM usuarios WHERE id = ?"), (uid,))
        return _usuario(cur.fetchone())


def listar_usuarios() -> list[dict]:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(f"SELECT {_COLS_USU} FROM usuarios ORDER BY ativo DESC, nome")
        return [_usuario(r) for r in cur.fetchall()]


def criar_usuario(nome: str, email: str, senha_hash: str, perfil: str) -> None:
    if perfil not in PERFIS:
        raise ValueError("perfil inválido")
    with conectar() as con:
        con.cursor().execute(_sql("INSERT INTO usuarios (nome, email, senha, perfil, ativo, criado_em) "
                                  "VALUES (?, ?, ?, ?, 1, ?)"),
                             (nome.strip(), email.strip().lower(), senha_hash, perfil, _agora()))


def alterar_usuario(uid: int, *, nome: str | None = None, email: str | None = None, perfil: str | None = None,
                    ativo: bool | None = None, senha_hash: str | None = None) -> None:
    sets, vals = [], []
    if nome is not None:
        sets.append("nome = ?"); vals.append(nome.strip())
    if email is not None:
        sets.append("email = ?"); vals.append(email.strip().lower())
    if perfil is not None:
        if perfil not in PERFIS:
            raise ValueError("perfil inválido")
        sets.append("perfil = ?"); vals.append(perfil)
    if ativo is not None:
        sets.append("ativo = ?"); vals.append(1 if ativo else 0)
    if senha_hash is not None:
        sets.append("senha = ?"); vals.append(senha_hash)
    if not sets:
        return
    with conectar() as con:
        con.cursor().execute(_sql(f"UPDATE usuarios SET {', '.join(sets)} WHERE id = ?"), (*vals, uid))


def excluir_usuario(uid: int) -> None:
    with conectar() as con:
        con.cursor().execute(_sql("DELETE FROM usuarios WHERE id = ?"), (uid,))


def outros_admins_ativos(uid: int) -> int:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("SELECT COUNT(*) FROM usuarios WHERE perfil = 'admin' AND ativo = 1 AND id <> ?"), (uid,))
        return cur.fetchone()[0]


# ---------------------------------------------------------------- auditoria
def registrar(usuario: str | None, acao: str, detalhe: str = "") -> None:
    with conectar() as con:
        con.cursor().execute(_sql("INSERT INTO auditoria (usuario, acao, detalhe, criado_em) VALUES (?, ?, ?, ?)"),
                             (usuario or "sistema", acao, detalhe, _agora()))


def listar_auditoria(limite: int = 200) -> list[dict]:
    with conectar() as con:
        cur = con.cursor()
        cur.execute(_sql("SELECT usuario, acao, detalhe, criado_em FROM auditoria "
                         "ORDER BY criado_em DESC, id DESC LIMIT ?"), (limite,))
        return [dict(zip(["usuario", "acao", "detalhe", "criado_em"], r)) for r in cur.fetchall()]
