"""
Camada de acesso ao banco — SQLite ou PostgreSQL, mesma interface.

Herdada do Gestão ARP, com dois ajustes para o CIP, que divide o projeto
Supabase com outro sistema e usa um schema próprio ("cip"):
  - coluna_existe() olha só o schema atual (antes olhava todos os schemas e
    podia achar uma tabela de mesmo nome do public);
  - schema_atual() permite ao app conferir, ao iniciar, que está gravando no
    schema certo antes de criar qualquer tabela.
"""

import os
import re
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if IS_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
else:
    psycopg = None


def sqlite_path():
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL[len("sqlite:///"):]
    base = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("CIP_DB_PATH") or os.path.join(base, "cip.db")


def traduzir(sql):
    if not IS_POSTGRES:
        return sql
    saida = []
    dentro_de_aspas = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "'":
            if dentro_de_aspas and i + 1 < len(sql) and sql[i + 1] == "'":
                saida.append("''")
                i += 2
                continue
            dentro_de_aspas = not dentro_de_aspas
            saida.append(c)
        elif dentro_de_aspas:
            saida.append(c)
        elif c == "?":
            saida.append("%s")
        elif c == "%":
            saida.append("%%")
        else:
            saida.append(c)
        i += 1
    resultado = "".join(saida)
    resultado = re.sub(r"\bINSTR\s*\(", "STRPOS(", resultado, flags=re.IGNORECASE)
    return resultado


_RE_INSERT = re.compile(r"^\s*INSERT\s+INTO\s", re.IGNORECASE)
_RE_RETURNING = re.compile(r"\bRETURNING\b", re.IGNORECASE)


class Linha(dict):
    __slots__ = ()

    def __getitem__(self, chave):
        if isinstance(chave, int):
            return list(self.values())[chave]
        return dict.__getitem__(self, chave)

    def keys(self):
        return dict.keys(self)


def _linha_pg(cursor):
    colunas = [c.name for c in cursor.description] if cursor.description else []

    def construir(valores):
        return Linha(zip(colunas, valores))

    return construir


class Cursor:
    def __init__(self, cursor_nativo, lastrowid=None):
        self._cur = cursor_nativo
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return getattr(self._cur, "lastrowid", None)

    def fetchone(self):
        linha = self._cur.fetchone()
        if linha is None:
            return None
        return linha if IS_POSTGRES else Linha(zip(linha.keys(), linha))

    def fetchall(self):
        linhas = self._cur.fetchall()
        if IS_POSTGRES:
            return linhas
        return [Linha(zip(l.keys(), l)) for l in linhas]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount


class Conexao:
    def __init__(self, conexao_nativa):
        self._con = conexao_nativa

    def execute(self, sql, params=()):
        sql_final = traduzir(sql)
        if not IS_POSTGRES:
            return Cursor(self._con.execute(sql_final, params))
        precisa_returning = (
            _RE_INSERT.match(sql_final) and not _RE_RETURNING.search(sql_final)
        )
        if precisa_returning:
            sql_final = sql_final.rstrip().rstrip(";") + " RETURNING id"
        cur = self._con.cursor(row_factory=_linha_pg)

        # O psycopg sempre escaneia a consulta por placeholders (%s, %b...)
        # quando recebe uma tupla de parâmetros — mesmo vazia. Isso quebra
        # qualquer SQL administrativo que contenha '%' por outro motivo (um
        # LIKE '%texto%', ou um format('...%I...') dentro de um bloco DO do
        # Postgres): o psycopg vê '%p' ou '%I' e lança ProgrammingError antes
        # de a consulta chegar ao servidor. Sem parâmetros reais para ligar,
        # executamos a consulta como texto puro, e o '%' passa intacto para
        # o Postgres interpretar.
        if params:
            cur.execute(sql_final, params)
        else:
            cur.execute(sql_final)
        novo_id = None
        if precisa_returning:
            linha = cur.fetchone()
            if linha is not None:
                novo_id = linha["id"]
        return Cursor(cur, lastrowid=novo_id)

    def executemany(self, sql, sequencia):
        sql_final = traduzir(sql)
        if not IS_POSTGRES:
            return Cursor(self._con.executemany(sql_final, sequencia))
        cur = self._con.cursor()
        cur.executemany(sql_final, list(sequencia))
        return Cursor(cur)

    def executescript(self, script):
        if not IS_POSTGRES:
            return self._con.executescript(script)
        cur = self._con.cursor()
        cur.execute(script)
        return Cursor(cur)

    def coluna_existe(self, tabela, coluna):
        if not IS_POSTGRES:
            linhas = self._con.execute(f"PRAGMA table_info({tabela})")
            return coluna in {l["name"] for l in linhas}
        cur = self._con.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = %s AND column_name = %s",
            (tabela, coluna),
        )
        return cur.fetchone() is not None

    def schema_atual(self):
        """Schema onde as tabelas sem prefixo serão criadas/lidas."""
        if not IS_POSTGRES:
            return None
        cur = self._con.cursor()
        cur.execute("SELECT current_schema() AS schema")
        linha = cur.fetchone()  # conexão usa dict_row
        return linha["schema"] if linha else None

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def close(self):
        self._con.close()


def conectar():
    if IS_POSTGRES:
        con = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            autocommit=False,
            prepare_threshold=None,
        )
        return Conexao(con)
    con = sqlite3.connect(sqlite_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return Conexao(con)


def descricao_do_banco():
    if IS_POSTGRES:
        sem_credenciais = re.sub(r"//[^@]*@", "//***@", DATABASE_URL)
        return f"PostgreSQL ({sem_credenciais})"
    return f"SQLite ({sqlite_path()})"
