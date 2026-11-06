"""
CIP — Gestão dos valores arrecadados da CIP/COSIP
Prefeitura Municipal de Santo André

Esqueleto inicial: login, perfis (admin e visualização), usuários e auditoria.
As funções de negócio serão desenvolvidas depois.

Base técnica herdada do Gestão ARP (Flask + Jinja + camada db.py que roda o
mesmo código em SQLite local e PostgreSQL/Supabase em produção).

Em produção o banco é o schema "cip" de um projeto Supabase compartilhado.
O app se recusa a iniciar se a conexão não estiver nesse schema, para nunca
criar ou alterar tabelas de outro sistema.
"""

import getpass
import os
import sys
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    session, flash, abort,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

import db as banco

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Schema do Postgres em que o CIP pode trabalhar. Qualquer outro = erro.
SCHEMA_CIP = "cip"

app = Flask(__name__)

# A chave de sessão NUNCA fica no código em produção: quem a conhece consegue
# forjar um cookie e entrar como administrador. Em desenvolvimento, cai numa
# chave fixa para não atrapalhar.
app.secret_key = os.environ.get("CIP_SECRET_KEY") or "dev-inseguro-somente-local"

# Atrás do Nginx, o Flask precisa saber que a requisição original veio por
# HTTPS; sem isso, os redirects saem como http://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Só envia o cookie por HTTPS. Desligue (CIP_HTTPS=0) em desenvolvimento
    # local, onde não há certificado.
    SESSION_COOKIE_SECURE=os.environ.get("CIP_HTTPS", "1") == "1",
    SESSION_COOKIE_NAME="cip_session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = banco.conectar()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Schema SQLite (desenvolvimento local). O equivalente PostgreSQL está em
# schema_postgres.sql. Mudança de banco = só tabelas novas, nos DOIS lugares.
SCHEMA = """
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


def _carregar_schema_postgres():
    caminho = os.path.join(BASE_DIR, "schema_postgres.sql")
    with open(caminho, encoding="utf-8") as fh:
        return fh.read()


def _agora():
    return datetime.now().isoformat(timespec="seconds")


def _conferir_schema(db):
    """Impede o app de tocar em qualquer schema que não seja o do CIP.

    O usuário de banco cip_app tem search_path = cip. Se por qualquer motivo
    (usuário errado na DATABASE_URL, pooler sem o search_path, schema ainda
    não criado) a conexão não estiver no schema cip, paramos aqui — antes de
    qualquer CREATE TABLE — em vez de criar tabelas no public.
    """
    if not banco.IS_POSTGRES:
        return
    atual = db.schema_atual()
    if atual != SCHEMA_CIP:
        db.close()
        raise RuntimeError(
            f"Conexão no schema '{atual}', esperado '{SCHEMA_CIP}'. "
            "Confira o usuário da DATABASE_URL (cip_app.<ref>) e o "
            "search_path do usuário no Supabase. Nada foi alterado no banco."
        )


def init_db():
    """Cria as tabelas (se não existirem) e o administrador inicial.

    Idempotente: roda a cada inicialização sem apagar nada.
    """
    db = banco.conectar()
    _conferir_schema(db)
    db.executescript(_carregar_schema_postgres() if banco.IS_POSTGRES else SCHEMA)
    db.commit()
    _criar_admin_inicial(db)
    db.commit()
    db.close()


def _criar_admin_inicial(db):
    """Cria o primeiro administrador quando a tabela de usuários está vazia.

    Nenhuma senha fica no código. A senha inicial vem da variável
    CIP_ADMIN_SENHA_INICIAL (só no /etc/cip.env do servidor). Depois do
    primeiro acesso e da troca de senha, apague a variável do arquivo.
    """
    if db.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()["c"] > 0:
        return

    email = os.environ.get("CIP_ADMIN_EMAIL", "").strip().lower()
    senha = os.environ.get("CIP_ADMIN_SENHA_INICIAL", "")
    if not email or not senha:
        print(
            "AVISO: nenhum usuário cadastrado e CIP_ADMIN_EMAIL / "
            "CIP_ADMIN_SENHA_INICIAL não definidas. Defina-as no arquivo de "
            "ambiente ou rode: python app.py --criar-admin <e-mail>",
            file=sys.stderr,
        )
        return

    # ON CONFLICT: com mais de um worker do gunicorn iniciando ao mesmo tempo,
    # só um insere; o outro segue sem erro.
    db.execute(
        "INSERT INTO usuarios (nome, email, senha, perfil, ativo, criado_em) "
        "VALUES (?, ?, ?, 'admin', 1, ?) ON CONFLICT (email) DO NOTHING",
        ("Administrador", email, generate_password_hash(senha), _agora()),
    )
    db.execute(
        "INSERT INTO auditoria (usuario, acao, detalhe, criado_em) "
        "VALUES (?, ?, ?, ?)",
        ("sistema", "admin_inicial_criado", email, _agora()),
    )


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

def registrar_auditoria(acao, detalhe=""):
    db = get_db()
    db.execute(
        "INSERT INTO auditoria (usuario, acao, detalhe, criado_em) VALUES (?, ?, ?, ?)",
        (session.get("nome", "sistema"), acao, detalhe, _agora()),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Controle de acesso
# ---------------------------------------------------------------------------

PERFIS = {
    "admin": {
        "rotulo": "Administrador",
        "descricao": "Gestão completa do sistema, usuários e auditoria.",
    },
    "viewer": {
        "rotulo": "Visualização",
        "descricao": "Somente consulta, sem poder lançar, editar ou excluir "
                     "registros.",
    },
}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("perfil") != "admin":
            flash("Acesso restrito a administradores.", "erro")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_sessao():
    return {
        "sessao_nome": session.get("nome"),
        "sessao_perfil": session.get("perfil"),
        "sessao_id": session.get("usuario_id"),
    }


@app.template_filter("data_hora_br")
def data_hora_br(valor):
    """'AAAA-MM-DDTHH:MM:SS' -> 'DD/MM/AAAA HH:MM'."""
    if not valor:
        return ""
    try:
        return datetime.fromisoformat(str(valor)[:19]).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(valor)


# ---------------------------------------------------------------------------
# Sessão
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        db = get_db()
        user = db.execute(
            "SELECT * FROM usuarios WHERE LOWER(email) = ? AND ativo = 1", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["senha"], senha):
            flash("E-mail ou senha inválidos.", "erro")
            registrar_auditoria("login_negado", email or "(sem e-mail)")
            return redirect(url_for("login"))

        session.clear()
        session.permanent = True
        session["usuario_id"] = user["id"]
        session["nome"] = user["nome"]
        session["perfil"] = user["perfil"]
        registrar_auditoria("login", f"{user['nome']} ({user['perfil']})")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    if "usuario_id" in session:
        registrar_auditoria("logout", session.get("nome", ""))
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/saude")
def saude():
    """Verificação simples para monitoramento: app no ar e banco respondendo."""
    try:
        get_db().execute("SELECT 1 AS ok").fetchone()
    except Exception:
        return "erro", 503, {"Content-Type": "text/plain; charset=utf-8"}
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

def _outros_admins_ativos(db, usuario_id):
    return db.execute(
        "SELECT COUNT(*) AS c FROM usuarios "
        "WHERE perfil = 'admin' AND ativo = 1 AND id <> ?",
        (usuario_id,),
    ).fetchone()["c"]


@app.route("/usuarios")
@login_required
@admin_required
def usuarios():
    db = get_db()
    lista = db.execute(
        "SELECT * FROM usuarios ORDER BY ativo DESC, nome"
    ).fetchall()
    return render_template("usuarios.html", usuarios=lista, perfis=PERFIS)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
@admin_required
def novo_usuario():
    db = get_db()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmacao", "")
        perfil = request.form.get("perfil", "viewer")

        erros = []
        if not nome:
            erros.append("Informe o nome do usuário.")
        if not email:
            erros.append("Informe o e-mail institucional.")
        elif db.execute(
            "SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)
        ).fetchone():
            erros.append(f"Já existe um usuário com o e-mail {email}.")
        if len(senha) < 8:
            erros.append("A senha deve ter ao menos 8 caracteres.")
        elif senha != confirmacao:
            erros.append("A senha e a confirmação não coincidem.")
        if perfil not in PERFIS:
            erros.append("Selecione um perfil válido.")

        if erros:
            for e in erros:
                flash(e, "erro")
            return render_template(
                "form_usuario.html", perfis=PERFIS, usuario=None,
                form={"nome": nome, "email": email, "perfil": perfil},
            )

        db.execute(
            "INSERT INTO usuarios (nome, email, senha, perfil, ativo, criado_em) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (nome, email, generate_password_hash(senha), perfil, _agora()),
        )
        db.commit()
        registrar_auditoria("usuario_criado", f"{nome} <{email}> ({perfil})")
        flash(f"Usuário {nome} cadastrado.", "ok")
        return redirect(url_for("usuarios"))

    return render_template("form_usuario.html", perfis=PERFIS, usuario=None, form=None)


@app.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@admin_required
def editar_usuario(usuario_id):
    db = get_db()
    usuario = db.execute(
        "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
    ).fetchone()
    if usuario is None:
        abort(404)

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        perfil = request.form.get("perfil", usuario["perfil"])
        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmacao", "")
        ativo = 1 if request.form.get("ativo") else 0

        erros = []
        if not nome:
            erros.append("Informe o nome do usuário.")
        if not email:
            erros.append("Informe o e-mail institucional.")
        elif db.execute(
            "SELECT id FROM usuarios WHERE LOWER(email) = ? AND id <> ?",
            (email, usuario_id),
        ).fetchone():
            erros.append(f"Já existe outro usuário com o e-mail {email}.")
        if perfil not in PERFIS:
            erros.append("Selecione um perfil válido.")
        if senha:
            if len(senha) < 8:
                erros.append("A nova senha deve ter ao menos 8 caracteres.")
            elif senha != confirmacao:
                erros.append("A senha e a confirmação não coincidem.")

        # O sistema não pode ficar sem administrador ativo.
        if usuario["perfil"] == "admin" and (perfil != "admin" or not ativo):
            if not _outros_admins_ativos(db, usuario_id):
                erros.append(
                    "Este é o último administrador ativo. Promova outro usuário "
                    "antes de alterar o perfil ou desativá-lo."
                )

        if erros:
            for e in erros:
                flash(e, "erro")
            return redirect(url_for("editar_usuario", usuario_id=usuario_id))

        db.execute(
            "UPDATE usuarios SET nome = ?, email = ?, perfil = ?, ativo = ? WHERE id = ?",
            (nome, email, perfil, ativo, usuario_id),
        )
        if senha:
            db.execute(
                "UPDATE usuarios SET senha = ? WHERE id = ?",
                (generate_password_hash(senha), usuario_id),
            )
        db.commit()

        # Mantém o nome da barra lateral em dia quando o usuário edita a si mesmo.
        if usuario_id == session.get("usuario_id"):
            session["nome"] = nome
            session["perfil"] = perfil

        registrar_auditoria(
            "usuario_editado",
            f"{nome} <{email}> ({perfil}{', inativo' if not ativo else ''})"
            + (" — senha redefinida" if senha else ""),
        )
        flash(f"Usuário {nome} atualizado.", "ok")
        return redirect(url_for("usuarios"))

    return render_template(
        "form_usuario.html", perfis=PERFIS, usuario=usuario, form=None
    )


@app.route("/usuarios/<int:usuario_id>/excluir", methods=["POST"])
@login_required
@admin_required
def excluir_usuario(usuario_id):
    db = get_db()
    usuario = db.execute(
        "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
    ).fetchone()
    if usuario is None:
        abort(404)

    if usuario_id == session.get("usuario_id"):
        flash("Você não pode excluir o próprio usuário.", "erro")
        return redirect(url_for("usuarios"))

    if usuario["perfil"] == "admin" and not _outros_admins_ativos(db, usuario_id):
        flash("Não é possível excluir o último administrador ativo.", "erro")
        return redirect(url_for("usuarios"))

    db.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    db.commit()
    registrar_auditoria(
        "usuario_excluido", f"{usuario['nome']} <{usuario['email']}>"
    )
    flash(f"Usuário {usuario['nome']} excluído.", "ok")
    return redirect(url_for("usuarios"))


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

@app.route("/auditoria")
@login_required
@admin_required
def auditoria():
    db = get_db()
    logs = db.execute(
        "SELECT * FROM auditoria ORDER BY criado_em DESC, id DESC LIMIT 200"
    ).fetchall()
    return render_template("auditoria.html", logs=logs)


# ---------------------------------------------------------------------------
# Linha de comando (manutenção no servidor)
# ---------------------------------------------------------------------------

def _pedir_senha():
    """Lê a senha sem ecoar e sem deixá-la no histórico do shell."""
    senha = getpass.getpass("Nova senha (mín. 8 caracteres): ")
    if len(senha) < 8:
        print("ERRO: a senha deve ter ao menos 8 caracteres.")
        return None
    if senha != getpass.getpass("Confirme a senha: "):
        print("ERRO: a senha e a confirmação não coincidem.")
        return None
    return senha


def _cli_listar_usuarios():
    db = banco.conectar()
    _conferir_schema(db)
    for u in db.execute(
        "SELECT id, nome, email, perfil, ativo FROM usuarios ORDER BY id"
    ).fetchall():
        status = "ativo" if u["ativo"] else "INATIVO"
        print(f"{u['id']:>3}  {u['email']:<40} {u['perfil']:<7} {status:<8} {u['nome']}")
    db.close()


def _cli_redefinir_senha(email):
    db = banco.conectar()
    _conferir_schema(db)
    usuario = db.execute(
        "SELECT * FROM usuarios WHERE LOWER(email) = ?", (email.lower(),)
    ).fetchone()
    if usuario is None:
        print(f"ERRO: nenhum usuário com o e-mail {email}.")
        db.close()
        return
    senha = _pedir_senha()
    if senha is None:
        db.close()
        return
    db.execute(
        "UPDATE usuarios SET senha = ?, ativo = 1 WHERE id = ?",
        (generate_password_hash(senha), usuario["id"]),
    )
    db.execute(
        "INSERT INTO auditoria (usuario, acao, detalhe, criado_em) VALUES (?, ?, ?, ?)",
        ("linha de comando", "senha_redefinida", usuario["email"], _agora()),
    )
    db.commit()
    db.close()
    print(f"Senha de {usuario['email']} redefinida.")


def _cli_criar_admin(email):
    email = email.strip().lower()
    db = banco.conectar()
    _conferir_schema(db)
    if db.execute(
        "SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)
    ).fetchone():
        print(f"ERRO: já existe usuário com o e-mail {email}. Use --senha.")
        db.close()
        return
    senha = _pedir_senha()
    if senha is None:
        db.close()
        return
    db.execute(
        "INSERT INTO usuarios (nome, email, senha, perfil, ativo, criado_em) "
        "VALUES (?, ?, ?, 'admin', 1, ?)",
        ("Administrador", email, generate_password_hash(senha), _agora()),
    )
    db.execute(
        "INSERT INTO auditoria (usuario, acao, detalhe, criado_em) VALUES (?, ?, ?, ?)",
        ("linha de comando", "admin_criado", email, _agora()),
    )
    db.commit()
    db.close()
    print(f"Administrador {email} criado.")


# O Gunicorn importa este módulo sem executar o bloco __main__. Por isso a
# criação das tabelas acontece aqui, na importação.
init_db()


if __name__ == "__main__":
    argumentos = sys.argv[1:]

    if argumentos[:1] == ["--usuarios"]:
        _cli_listar_usuarios()
    elif argumentos[:1] == ["--senha"] and len(argumentos) == 2:
        _cli_redefinir_senha(argumentos[1])
    elif argumentos[:1] == ["--criar-admin"] and len(argumentos) == 2:
        _cli_criar_admin(argumentos[1])
    elif argumentos:
        print("""
CIP — Gestão da CIP/COSIP

  python app.py                         inicia em modo desenvolvimento (porta 5002)
  python app.py --usuarios              lista os usuários cadastrados
  python app.py --senha <e-mail>        redefine a senha (pergunta sem exibir)
  python app.py --criar-admin <e-mail>  cria um administrador (pergunta a senha)
""")
    else:
        # Apenas desenvolvimento local. Em produção quem serve é o Gunicorn.
        # debug=True jamais deve ir ao ar.
        app.run(host="127.0.0.1", port=5002, debug=True)
