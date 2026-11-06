"""CIP — Gestão dos valores arrecadados da CIP/COSIP (Prefeitura de Santo André).

Local:      flask --app app run --debug --port 5002
Produção:   gunicorn (ver deploy/cip.service), atrás do Nginx em cip.sistemagip.tech

Nada de dados pessoais fica guardado: o arquivo da Enel é lido, analisado e apagado;
o banco guarda só os totais. O Excel da análise é gerado sob demanda e entregue para
download, sem ficar no servidor.
"""

from __future__ import annotations

import getpass
import hashlib
import io
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from cosip import ArquivoInvalido
from cosip import acesso
from cosip import db
from cosip import excel_analise
from cosip import formato as F
from cosip import relatorios
from cosip.acesso import PERFIS, auditar, login_obrigatorio, somente_admin
from cosip.analise import Apuracao, Indicador
from cosip.documentos import CNPJ, CPF
from cosip.valores import PlanilhaInvalida, ValorCIP, gerar_planilha, ler_planilha, rotulo_mes

TAMANHO_MAXIMO_MB = int(os.environ.get("CIP_MAX_MB", "400"))
FUSO = ZoneInfo("America/Sao_Paulo")

FORMATOS = {
    "pdf": ("application/pdf", relatorios.gerar_pdf),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", relatorios.gerar_docx),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", relatorios.gerar_xlsx),
}
XLSX = FORMATOS["xlsx"][0]


def processar(caminho: str, nome: str, hash_hex: str) -> int:
    """Lê o arquivo, confere o valor da CIP do mês com a base histórica e grava só os totais.

    Se o mês não está na base ou o valor mais pago no arquivo diverge da base, o resultado fica
    pendente até a confirmação do administrador (a base não é alterada sem confirmação).
    """
    ap = Apuracao(caminho, nome_arquivo=nome, hash_sha256=hash_hex)
    base = db.valor_do_mes(ap.mes_arquivo)
    enc_cpf, enc_cnpj = ap.valor_encontrado(CPF), ap.valor_encontrado(CNPJ)
    if base and (base.cpf, base.cnpj) == (enc_cpf.valor, enc_cnpj.valor):
        return db.salvar(ap.resultado(base.cpf, base.cnpj))

    pend = {"tipo": "divergente" if base else "sem_valor",
            "base": {"cpf": base.cpf, "cnpj": base.cnpj, "origem": base.origem} if base else None,
            "arquivo": {"cpf": asdict(enc_cpf), "cnpj": asdict(enc_cnpj)}}
    if base:
        pend["ind2_base"] = asdict(ap.indicador2(base.cpf, base.cnpj))
    if enc_cpf.valor is not None and enc_cnpj.valor is not None:
        pend["ind2_arquivo"] = asdict(ap.indicador2(enc_cpf.valor, enc_cnpj.valor))
    return db.salvar(ap.resultado(None, None), pendencia=pend)


def _receber_upload(arq) -> tuple[str, str]:
    """Grava o upload num temporário (só do usuário do sistema) e devolve (caminho, sha256).
    Quem chama SEMPRE apaga o temporário (finally)."""
    fd, tmp = tempfile.mkstemp(prefix="cip_", suffix=".txt", dir=excel_analise.pasta_tmp())
    h = hashlib.sha256()
    with os.fdopen(fd, "wb") as out:
        for bloco in iter(lambda: arq.stream.read(1024 * 1024), b""):
            h.update(bloco)
            out.write(bloco)
    return tmp, h.hexdigest()


def data_hora_br(valor) -> str:
    """ISO (UTC ou sem fuso, gravado no servidor em UTC) -> 'DD/MM/AAAA HH:MM' de Brasília."""
    if not valor:
        return ""
    try:
        d = datetime.fromisoformat(str(valor).replace(" ", "T")[:32])
    except ValueError:
        return str(valor)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")


def data_br(valor) -> str:
    return data_hora_br(valor)[:10]


def _criar_admin_inicial() -> None:
    """Instalação nova: cria o primeiro admin a partir de CIP_ADMIN_EMAIL / CIP_ADMIN_SENHA_INICIAL.
    Não faz nada se já existir algum usuário."""
    if db.listar_usuarios():
        return
    email = os.environ.get("CIP_ADMIN_EMAIL", "").strip().lower()
    senha = os.environ.get("CIP_ADMIN_SENHA_INICIAL", "")
    if email and len(senha) >= acesso.SENHA_MINIMA:
        db.criar_usuario("Administrador", email, acesso.hash_senha(senha), "admin")
        db.registrar("sistema", "admin_inicial_criado", email)


def create_app() -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["SECRET_KEY"] = os.environ.get("CIP_SECRET_KEY") or "dev-inseguro-somente-local"
    app.config["MAX_CONTENT_LENGTH"] = TAMANHO_MAXIMO_MB * 1024 * 1024
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_COOKIE_NAME"] = "cip_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("CIP_HTTPS", "1") == "1"
    app.jinja_env.globals.update(F=F, rotulo_mes=rotulo_mes, csrf_token=acesso.token_csrf,
                                 eh_admin=acesso.eh_admin, PERFIS=PERFIS)
    app.jinja_env.filters["data_hora_br"] = data_hora_br
    app.jinja_env.filters["data_br"] = data_br

    db.inicializar()
    _criar_admin_inicial()

    app.before_request(acesso.carregar_usuario)
    app.before_request(acesso.conferir_csrf)

    def quer_json() -> bool:
        return request.headers.get("X-Requested-With") == "fetch"

    def _erro(erros: list[str]):
        if quer_json():
            return jsonify({"erros": erros}), 422
        return render_template("index.html", historico=db.listar(), erros=erros,
                               max_mb=TAMANHO_MAXIMO_MB), 422

    def _ir(aid: int):
        url = url_for("resultado", aid=aid)
        return jsonify({"redirect": url}) if quer_json() else redirect(url)

    # ------------------------------------------------------------ login
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.usuario:
            return redirect(url_for("index"))
        erro = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            u = acesso.autenticar(email, request.form.get("senha", ""))
            if u:
                acesso.entrar(u)
                db.registrar(u["nome"], "login", f"{u['nome']} ({u['perfil']})")
                proximo = request.args.get("proximo") or ""
                return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else url_for("index"))
            db.registrar(None, "login_negado", email or "(sem e-mail)")
            erro = "E-mail ou senha inválidos."
        return render_template("login.html", erro=erro), (401 if erro else 200)

    @app.post("/sair")
    def sair():
        if g.usuario:
            auditar("logout", g.usuario["nome"])
        acesso.sair()
        return redirect(url_for("login"))

    @app.get("/saude")
    def saude():
        try:
            db.listar_usuarios()
        except Exception:  # noqa: BLE001
            return "erro", 503, {"Content-Type": "text/plain; charset=utf-8"}
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    # ------------------------------------------------------------ arquivos mensais
    @app.get("/")
    @login_obrigatorio
    def index():
        return render_template("index.html", historico=db.listar(), max_mb=TAMANHO_MAXIMO_MB)

    @app.post("/enviar")
    @somente_admin
    def enviar():
        arq = request.files.get("arquivo")
        if not arq or not arq.filename:
            return _erro(["Nenhum arquivo selecionado."])
        nome = Path(arq.filename).name
        tmp = None
        try:
            tmp, hash_hex = _receber_upload(arq)
            existente = db.buscar_por_hash(hash_hex)
            if existente:
                flash("Este arquivo já tinha sido processado. Mostrando o resultado guardado.")
                return _ir(existente)
            try:
                aid = processar(tmp, nome, hash_hex)
            except ArquivoInvalido as e:
                auditar("arquivo_recusado", f"{nome}: {len(e.erros)} erro(s)")
                return _erro(e.erros)
            dados = db.carregar(aid)
            r = dados["resultado"]
            auditar("arquivo_processado", f"{nome} — {F.mes_extenso(r.mes_arquivo)}"
                    + (" — aguardando confirmação do valor da CIP" if dados["situacao"] == "pendente" else ""))
            return _ir(aid)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)     # o arquivo da Enel nunca fica guardado

    @app.get("/arquivo/<int:aid>")
    @login_obrigatorio
    def resultado(aid: int):
        dados = db.carregar(aid) or abort(404)
        if dados["situacao"] == "pendente":
            return render_template("confirmar.html", d=dados, r=dados["resultado"], p=dados["pendencia"])
        r = dados["resultado"]
        return render_template("resultado.html", r=r, aid=aid, processado_em=dados["processado_em"],
                               max_mb=TAMANHO_MAXIMO_MB,
                               indicadores=relatorios.indicadores(r), fora=relatorios.fora_das_contagens(r),
                               conferencia=relatorios.CONFERENCIA)

    @app.post("/arquivo/<int:aid>/confirmar")
    @somente_admin
    def confirmar(aid: int):
        dados = db.carregar(aid) or abort(404)
        if dados["situacao"] != "pendente":
            return redirect(url_for("resultado", aid=aid))
        r, p = dados["resultado"], dados["pendencia"]
        escolha = request.form.get("escolha")
        if escolha == "base" and p.get("ind2_base"):
            r.valor_cpf, r.valor_cnpj = p["base"]["cpf"], p["base"]["cnpj"]
            r.ind2 = Indicador.de_dict(p["ind2_base"])
            detalhe = "manteve o valor da base histórica"
        elif escolha == "arquivo" and p.get("ind2_arquivo"):
            novo = ValorCIP(r.mes_arquivo, p["arquivo"]["cpf"]["valor"], p["arquivo"]["cnpj"]["valor"])
            origem = f"Arquivo {r.nome_arquivo} ({F.mes_extenso(r.mes_arquivo)})"
            if p["tipo"] == "sem_valor":
                db.incluir_valor(novo, origem)
            else:
                db.alterar_valor(novo, origem, "Valor mais pago no arquivo diferente da base histórica")
            r.valor_cpf, r.valor_cnpj = novo.cpf, novo.cnpj
            r.ind2 = Indicador.de_dict(p["ind2_arquivo"])
            detalhe = f"usou o valor do arquivo (CPF {F.reais(novo.cpf)}, CNPJ {F.reais(novo.cnpj)})"
        else:
            abort(400)
        db.concluir(aid, r)
        auditar("valor_confirmado", f"{F.mes_extenso(r.mes_arquivo)}: {detalhe}")
        return redirect(url_for("resultado", aid=aid))

    @app.post("/arquivo/<int:aid>/cancelar")
    @somente_admin
    def cancelar(aid: int):
        dados = db.carregar(aid) or abort(404)
        if dados["situacao"] == "pendente":
            db.excluir(aid)
            auditar("processamento_cancelado", f"{dados['resultado'].nome_arquivo} — "
                    f"{F.mes_extenso(dados['resultado'].mes_arquivo)}")
            flash("Processamento cancelado. Nada foi alterado na base de valores.")
        return redirect(url_for("index"))

    @app.post("/arquivo/<int:aid>/excel-analise")
    @somente_admin
    def excel_da_analise(aid: int):
        """Gera o Excel da análise a partir do MESMO arquivo da Enel, enviado de novo.
        Nada fica guardado: o .txt e o Excel são apagados assim que o download começa."""
        dados = db.carregar(aid) or abort(404)
        if dados["situacao"] != "concluido":
            return redirect(url_for("resultado", aid=aid))
        r = dados["resultado"]
        arq = request.files.get("arquivo")
        if not arq or not arq.filename:
            flash("Selecione o arquivo da Enel deste mês para gerar o Excel da análise.")
            return redirect(url_for("resultado", aid=aid))
        tmp = None
        try:
            tmp, hash_hex = _receber_upload(arq)
            if hash_hex != r.hash_sha256:
                flash("Este não é o mesmo arquivo deste resultado. Selecione o arquivo "
                      f"{r.nome_arquivo} de {F.mes_extenso(r.mes_arquivo)}.")
                return redirect(url_for("resultado", aid=aid))
            try:
                excel = excel_analise.gerar_e_abrir(tmp, r)
            except excel_analise.Ocupado:
                flash("Outro Excel da análise está sendo gerado agora. Aguarde um minuto e tente de novo.")
                return redirect(url_for("resultado", aid=aid))
            auditar("excel_analise_baixado", f"{F.mes_extenso(r.mes_arquivo)}")
            return send_file(excel, mimetype=XLSX, as_attachment=True,
                             download_name=f"CIP_{r.mes_arquivo}_analise_com_formulas.xlsx")
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    @app.get("/arquivo/<int:aid>/<fmt>")
    @login_obrigatorio
    def baixar(aid: int, fmt: str):
        if fmt not in FORMATOS:
            abort(404)
        dados = db.carregar(aid) or abort(404)
        if dados["situacao"] != "concluido":
            return redirect(url_for("resultado", aid=aid))
        r = dados["resultado"]
        mime, gerar = FORMATOS[fmt]
        return send_file(io.BytesIO(gerar(r, data_hora_br(dados["processado_em"]))), mimetype=mime, as_attachment=True,
                         download_name=relatorios.nome_saida(r, fmt))

    # ------------------------------------------------------------ base histórica de valores
    @app.get("/valores")
    @somente_admin
    def valores():
        return render_template("valores.html", valores=list(reversed(db.valores())), alteracoes=db.alteracoes(),
                               divergencias=None)

    @app.post("/valores/importar")
    @somente_admin
    def importar_valores():
        arq = request.files.get("planilha")
        if not arq or not arq.filename:
            flash("Nenhuma planilha selecionada.")
            return redirect(url_for("valores"))
        try:
            lidos = ler_planilha(arq.read())
        except PlanilhaInvalida as e:
            flash(str(e))
            return redirect(url_for("valores"))
        novos, iguais, divergentes = 0, 0, []
        for v in lidos:
            atual = db.valor_do_mes(v.mes)
            if atual is None:
                db.incluir_valor(v, f"Planilha {Path(arq.filename).name}")
                novos += 1
            elif (atual.cpf, atual.cnpj) == (v.cpf, v.cnpj):
                iguais += 1
            else:
                divergentes.append({"mes": v.mes, "atual": atual, "planilha": v})
        partes = [f"{novos} mês(es) acrescentado(s)"]
        if iguais:
            partes.append(f"{iguais} já cadastrado(s) com o mesmo valor")
        if divergentes:
            partes.append(f"{len(divergentes)} com valor diferente do cadastrado (não alterado(s) — veja abaixo)")
        auditar("valores_importados", f"{Path(arq.filename).name}: " + "; ".join(partes))
        flash("Planilha importada: " + "; ".join(partes) + ".")
        return render_template("valores.html", valores=list(reversed(db.valores())), alteracoes=db.alteracoes(),
                               divergencias=divergentes, nome_planilha=Path(arq.filename).name)

    @app.post("/valores/substituir")
    @somente_admin
    def substituir_valor():
        try:
            v = ValorCIP(request.form["mes"], int(request.form["cpf"]), int(request.form["cnpj"]))
        except (KeyError, ValueError):
            abort(400)
        nome = request.form.get("planilha", "planilha")
        db.alterar_valor(v, f"Planilha {nome}", "Substituído na importação da planilha (confirmado)")
        auditar("valor_substituido", f"{rotulo_mes(v.mes)}: CPF {F.reais(v.cpf)}, CNPJ {F.reais(v.cnpj)}")
        flash(f"Valor de {rotulo_mes(v.mes)} substituído. O valor anterior ficou no registro de alterações.")
        return redirect(url_for("valores"))

    @app.get("/valores/planilha")
    @somente_admin
    def baixar_valores():
        return send_file(io.BytesIO(gerar_planilha(db.valores())), mimetype=XLSX, as_attachment=True,
                         download_name="Valores_CIP.xlsx")

    # ------------------------------------------------------------ usuários (como no Gestão ARP)
    def _validar_usuario(nome, email, perfil, senha, confirmacao, uid=None, senha_obrigatoria=True):
        erros = []
        if not nome:
            erros.append("Informe o nome do usuário.")
        if not email:
            erros.append("Informe o e-mail institucional.")
        else:
            outro = db.usuario_por_email(email)
            if outro and outro["id"] != uid:
                erros.append(f"Já existe um usuário com o e-mail {email}.")
        if perfil not in PERFIS:
            erros.append("Selecione um perfil válido.")
        if senha or senha_obrigatoria:
            erro = acesso.validar_senha(senha)
            if erro:
                erros.append(erro)
            elif senha != confirmacao:
                erros.append("A senha e a confirmação não coincidem.")
        return erros

    @app.get("/usuarios")
    @somente_admin
    def usuarios():
        return render_template("usuarios.html", usuarios=db.listar_usuarios())

    @app.route("/usuarios/novo", methods=["GET", "POST"])
    @somente_admin
    def novo_usuario():
        if request.method == "POST":
            f = request.form
            nome, email = f.get("nome", "").strip(), f.get("email", "").strip().lower()
            perfil, senha = f.get("perfil", "viewer"), f.get("senha", "")
            erros = _validar_usuario(nome, email, perfil, senha, f.get("confirmacao", ""))
            if erros:
                for e in erros:
                    flash(e)
                return render_template("form_usuario.html", usuario=None,
                                       form={"nome": nome, "email": email, "perfil": perfil})
            db.criar_usuario(nome, email, acesso.hash_senha(senha), perfil)
            auditar("usuario_criado", f"{nome} <{email}> ({perfil})")
            flash(f"Usuário {nome} cadastrado.")
            return redirect(url_for("usuarios"))
        return render_template("form_usuario.html", usuario=None, form=None)

    @app.route("/usuarios/<int:uid>/editar", methods=["GET", "POST"])
    @somente_admin
    def editar_usuario(uid: int):
        u = db.usuario_por_id(uid) or abort(404)
        if request.method == "POST":
            f = request.form
            nome, email = f.get("nome", "").strip(), f.get("email", "").strip().lower()
            perfil, senha = f.get("perfil", u["perfil"]), f.get("senha", "")
            ativo = bool(f.get("ativo"))
            erros = _validar_usuario(nome, email, perfil, senha, f.get("confirmacao", ""), uid=uid,
                                     senha_obrigatoria=False)
            if u["perfil"] == "admin" and (perfil != "admin" or not ativo) and not db.outros_admins_ativos(uid):
                erros.append("Este é o último administrador ativo. Promova outro usuário antes de "
                             "alterar o perfil ou desativá-lo.")
            if erros:
                for e in erros:
                    flash(e)
                return redirect(url_for("editar_usuario", uid=uid))
            db.alterar_usuario(uid, nome=nome, email=email, perfil=perfil, ativo=ativo,
                               senha_hash=acesso.hash_senha(senha) if senha else None)
            auditar("usuario_editado", f"{nome} <{email}> ({perfil}{', inativo' if not ativo else ''})"
                    + (" — senha redefinida" if senha else ""))
            flash(f"Usuário {nome} atualizado.")
            return redirect(url_for("usuarios"))
        return render_template("form_usuario.html", usuario=u, form=None)

    @app.post("/usuarios/<int:uid>/excluir")
    @somente_admin
    def excluir_usuario(uid: int):
        u = db.usuario_por_id(uid) or abort(404)
        if uid == g.usuario["id"]:
            flash("Você não pode excluir o próprio usuário.")
        elif u["perfil"] == "admin" and u["ativo"] and not db.outros_admins_ativos(uid):
            flash("Não é possível excluir o último administrador ativo.")
        else:
            db.excluir_usuario(uid)
            auditar("usuario_excluido", f"{u['nome']} <{u['email']}>")
            flash(f"Usuário {u['nome']} excluído.")
        return redirect(url_for("usuarios"))

    # ------------------------------------------------------------ auditoria
    @app.get("/auditoria")
    @somente_admin
    def auditoria():
        return render_template("auditoria.html", logs=db.listar_auditoria(200))

    # ------------------------------------------------------------ erros e linha de comando
    @app.errorhandler(413)
    def grande_demais(_):
        return _erro([f"Arquivo maior que o limite de {TAMANHO_MAXIMO_MB} MB."])

    @app.errorhandler(403)
    def proibido(_):
        if quer_json():
            return jsonify({"erros": ["Seu perfil (Visualização) só permite consultar."]}), 403
        return render_template("mensagem.html", titulo="Acesso restrito",
                               texto="Seu perfil (Visualização) permite consultar os arquivos já processados "
                                     "e baixar os resumos, mas não enviar nem alterar dados."), 403

    def _pedir_senha(mostrar: bool) -> str:
        if mostrar:
            senha = click.prompt("Senha")
            if senha != click.prompt("Repita a senha"):
                raise click.ClickException("As senhas não conferem.")
        else:
            click.echo("(a senha não aparece enquanto você digita; digite e tecle Enter)")
            senha = getpass.getpass("Senha: ")
            if senha != getpass.getpass("Repita a senha: "):
                raise click.ClickException("As senhas não conferem.")
        erro = acesso.validar_senha(senha)
        if erro:
            raise click.ClickException(erro)
        return senha

    @app.cli.command("usuarios")
    def cli_usuarios():
        """Lista os usuários cadastrados."""
        for u in db.listar_usuarios():
            click.echo(f"{u['id']:>3}  {u['email']:<40} {u['perfil']:<7} "
                       f"{'ativo' if u['ativo'] else 'INATIVO':<8} {u['nome']}")

    @app.cli.command("criar-admin")
    @click.option("--mostrar-senha", is_flag=True, help="Mostra a senha enquanto digita.")
    def cli_criar_admin(mostrar_senha):
        """Cria um administrador pelo terminal."""
        nome = click.prompt("Nome")
        email = click.prompt("E-mail").strip().lower()
        if db.usuario_por_email(email):
            raise click.ClickException(f"Já existe usuário com o e-mail {email}. Use 'senha'.")
        db.criar_usuario(nome, email, acesso.hash_senha(_pedir_senha(mostrar_senha)), "admin")
        db.registrar("linha de comando", "admin_criado", email)
        click.echo(f"Administrador {email} criado.")

    @app.cli.command("senha")
    @click.argument("email")
    @click.option("--mostrar-senha", is_flag=True, help="Mostra a senha enquanto digita.")
    def cli_senha(email, mostrar_senha):
        """Redefine a senha de um usuário (e o reativa)."""
        u = db.usuario_por_email(email)
        if not u:
            raise click.ClickException(f"Nenhum usuário com o e-mail {email}.")
        db.alterar_usuario(u["id"], senha_hash=acesso.hash_senha(_pedir_senha(mostrar_senha)), ativo=True)
        db.registrar("linha de comando", "senha_redefinida", u["email"])
        click.echo(f"Senha de {u['email']} redefinida.")

    return app


app = create_app()
