"""Login (e-mail institucional + senha), perfis e proteção CSRF dos formulários.

admin   envia arquivos, confirma o valor da CIP do mês, mantém a base de valores,
        os usuários, vê a auditoria e baixa o Excel da análise (dados pessoais)
viewer  (Visualização) só consulta os arquivos já processados e baixa os resumos
"""

from __future__ import annotations

import secrets
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

SENHA_MINIMA = 8

PERFIS = {
    "admin": {
        "rotulo": "Administrador",
        "descricao": "Envia os arquivos, confirma o valor da CIP, mantém a base de valores, "
                     "os usuários e a auditoria e baixa o Excel da análise.",
    },
    "viewer": {
        "rotulo": "Visualização",
        "descricao": "Somente consulta os arquivos já processados e baixa os resumos "
                     "(PDF, Word, Excel), sem enviar nem alterar dados.",
    },
}


def hash_senha(senha: str) -> str:
    return generate_password_hash(senha)


def validar_senha(senha: str) -> str | None:
    if len(senha or "") < SENHA_MINIMA:
        return f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres."
    return None


def autenticar(email: str, senha: str) -> dict | None:
    u = db.usuario_por_email(email or "")
    if not u or not u["ativo"] or not check_password_hash(u["senha"], senha or ""):
        return None
    return u


def entrar(u: dict) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["csrf"] = secrets.token_urlsafe(32)


def sair() -> None:
    session.clear()


def carregar_usuario() -> None:
    """before_request: coloca o usuário logado em g.usuario (ou None).

    Relê o usuário no banco a cada requisição: desativar, excluir ou mudar o
    perfil vale na hora, sem esperar a sessão expirar.
    """
    g.usuario = None
    uid = session.get("uid")
    if uid:
        u = db.usuario_por_id(uid)
        if u and u["ativo"]:
            g.usuario = u
        else:
            session.clear()


def nome_atual() -> str | None:
    u = g.get("usuario")
    return u["nome"] if u else None


def auditar(acao: str, detalhe: str = "") -> None:
    db.registrar(nome_atual(), acao, detalhe)


def eh_admin() -> bool:
    return bool(g.get("usuario")) and g.usuario["perfil"] == "admin"


def login_obrigatorio(f):
    @wraps(f)
    def interno(*a, **kw):
        if not g.get("usuario"):
            if request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return redirect(url_for("login", proximo=request.full_path if request.method == "GET" else None))
        return f(*a, **kw)
    return interno


def somente_admin(f):
    @wraps(f)
    @login_obrigatorio
    def interno(*a, **kw):
        if not eh_admin():
            abort(403)
        return f(*a, **kw)
    return interno


# ------------------------------------------------------------------ CSRF
def token_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def conferir_csrf() -> None:
    """before_request: todo POST precisa do token da sessão (campo _csrf ou cabeçalho X-CSRF-Token)."""
    if request.method != "POST" or not current_app.config.get("CSRF_ATIVO", True):
        return
    enviado = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not enviado or not secrets.compare_digest(enviado, session.get("csrf", "")):
        abort(400, "Formulário expirado. Recarregue a página e tente de novo.")
