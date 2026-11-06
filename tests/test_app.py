import io

import pytest

from conftest import cabecalho, linha_detalhe, rodape


@pytest.fixture
def app_(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CIP_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("CIP_DB_PATH", str(tmp_path / "teste.db"))
    monkeypatch.setenv("CIP_TMP", str(tmp_path / "tmp"))
    from app import create_app
    from cosip import acesso, db
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ATIVO"] = False
    db.criar_usuario("Leonardo", "admin@santoandre.sp.gov.br", acesso.hash_senha("senha-admin-1"), "admin")
    db.criar_usuario("Diretor", "diretor@santoandre.sp.gov.br", acesso.hash_senha("senha-viewer-1"), "viewer")
    return app


def _entrar(app, email, senha):
    c = app.test_client()
    r = c.post("/login", data={"email": email, "senha": senha})
    assert r.status_code == 302, r.get_data(as_text=True)
    return c


@pytest.fixture
def cliente(app_):
    return _entrar(app_, "admin@santoandre.sp.gov.br", "senha-admin-1")


@pytest.fixture
def visualizador(app_):
    """Perfil Visualização (substitui o antigo 'modo consultor', agora com login)."""
    return _entrar(app_, "diretor@santoandre.sp.gov.br", "senha-viewer-1")


def _arquivo(detalhes, mes="202608"):
    t = [d.decode("latin-1") for d in detalhes]
    c = sum(int(x[198:210]) * (-1 if x[210] == "-" else 1) for x in t)
    j = sum(int(x[211:223]) for x in t)
    m = sum(int(x[223:236]) for x in t)
    return cabecalho(mes=mes) + b"\n" + b"\n".join(detalhes) + b"\n" + rodape(len(detalhes), c, j, m) + b"\n"


def _enviar(cliente, conteudo, ajax=True):
    headers = {"X-Requested-With": "fetch"} if ajax else {}
    return cliente.post("/enviar", data={"arquivo": (io.BytesIO(conteudo), "B_A_SAN_COSIP.txt")},
                        content_type="multipart/form-data", headers=headers)


AGOSTO = [linha_detalhe(cosip=2593),
          linha_detalhe(cosip=7720, cpf_cnpj="046522942000130"),
          linha_detalhe(cosip=269, cpf_cnpj="000026620502849"),
          linha_detalhe(mes="202607", cosip=2601, juros=16, multa=51),
          linha_detalhe(mes="202512", cosip=2549)]


def test_fluxo_com_valor_da_base(cliente):
    url = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    pagina = cliente.get(url).get_data(as_text=True)
    assert "agosto/2026" in pagina
    assert "R$ 25,93" in pagina and "R$ 77,20" in pagina           # valor do mês usado no indicador 2
    assert "dezembro/2025" in pagina and "julho/2026" in pagina     # lista de outros meses
    assert "R$ 105,82" in pagina                                   # 2593 + 7720 + 269
    for fmt, inicio in (("pdf", b"%PDF"), ("docx", b"PK"), ("xlsx", b"PK")):
        d = cliente.get(f"{url}/{fmt}")
        assert d.status_code == 200 and d.data.startswith(inicio), fmt
        assert "COSIP_202608_indicadores" in d.headers["Content-Disposition"]
    assert "agosto/2026" in cliente.get("/").get_data(as_text=True)


def test_mes_divergente_pede_confirmacao_e_mantem_base(cliente):
    linhas = [linha_detalhe(mes="202605", cosip=2586), linha_detalhe(mes="202605", cosip=2586, cpf_cnpj="000026620502849"),
              linha_detalhe(mes="202605", cosip=7700, cpf_cnpj="046522942000130")]
    url = _enviar(cliente, _arquivo(linhas, mes="202605")).get_json()["redirect"]
    pagina = cliente.get(url).get_data(as_text=True)
    assert "diferente do cadastrado" in pagina and "R$ 27,51" in pagina and "R$ 25,86" in pagina
    assert cliente.get(url + "/pdf").status_code == 302                     # sem relatório enquanto pendente
    cliente.post(url + "/confirmar", data={"escolha": "base"})
    pagina = cliente.get(url).get_data(as_text=True)
    assert "CPF R$ 27,51" in pagina
    assert "R$ 27,51" in cliente.get("/valores").get_data(as_text=True)     # base não mudou


def test_mes_divergente_atualiza_base_e_guarda_anterior(cliente):
    linhas = [linha_detalhe(mes="202605", cosip=2586), linha_detalhe(mes="202605", cosip=7700, cpf_cnpj="046522942000130")]
    url = _enviar(cliente, _arquivo(linhas, mes="202605")).get_json()["redirect"]
    cliente.post(url + "/confirmar", data={"escolha": "arquivo"})
    valores = cliente.get("/valores").get_data(as_text=True)
    assert "R$ 25,86" in valores and "Registro de alterações" in valores and "R$ 27,51" in valores


def test_mes_sem_valor_acrescenta_na_base(cliente):
    linhas = [linha_detalhe(mes="202612", cosip=3100), linha_detalhe(mes="202612", cosip=9200, cpf_cnpj="046522942000130")]
    url = _enviar(cliente, _arquivo(linhas, mes="202612")).get_json()["redirect"]
    assert "não está na base histórica" in cliente.get(url).get_data(as_text=True)
    cliente.post(url + "/confirmar", data={"escolha": "arquivo"})
    assert "<td>Dez/26</td>" in cliente.get("/valores").get_data(as_text=True)


def test_cancelar_pendente(cliente):
    linhas = [linha_detalhe(mes="202612", cosip=3100), linha_detalhe(mes="202612", cosip=9200, cpf_cnpj="046522942000130")]
    url = _enviar(cliente, _arquivo(linhas, mes="202612")).get_json()["redirect"]
    cliente.post(url + "/cancelar")
    assert cliente.get(url).status_code == 404
    assert "<td>Dez/26</td>" not in cliente.get("/valores").get_data(as_text=True)


def test_importar_planilha_so_acrescenta(cliente):
    from cosip.valores import ValorCIP, gerar_planilha
    dados = gerar_planilha([ValorCIP("202612", 3100, 9200), ValorCIP("202608", 2600, 7720)])
    r = cliente.post("/valores/importar", data={"planilha": (io.BytesIO(dados), "nova.xlsx")},
                     content_type="multipart/form-data")
    pagina = r.get_data(as_text=True)
    assert "1 mês(es) acrescentado(s)" in pagina and "valor diferente na planilha" in pagina
    valores = cliente.get("/valores").get_data(as_text=True)
    assert "<td>Dez/26</td>" in valores and "R$ 25,93" in valores        # agosto não foi trocado sozinho
    assert cliente.get("/valores/planilha").data.startswith(b"PK")


def test_arquivo_repetido_nao_duplica(cliente):
    u1 = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    u2 = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    assert u1 == u2


def test_arquivo_invalido_mostra_erros(cliente):
    r = _enviar(cliente, b"isto nao e o arquivo da enel\n")
    assert r.status_code == 422
    assert any("cabeçalho" in e for e in r.get_json()["erros"])


def test_envio_sem_javascript(cliente):
    r = _enviar(cliente, _arquivo(AGOSTO), ajax=False)
    assert r.status_code == 302 and "/arquivo/" in r.headers["Location"]


# ------------------------------------------------------------------ login e perfis
def test_sem_login_vai_para_a_tela_de_login(app_):
    c = app_.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert c.get("/arquivo/1").status_code == 302
    assert _enviar(c, _arquivo(AGOSTO)).status_code == 401


def test_senha_errada(app_):
    r = app_.test_client().post("/login", data={"email": "admin@santoandre.sp.gov.br", "senha": "errada"})
    assert r.status_code == 401 and "inválidos" in r.get_data(as_text=True)


def test_consultor_so_visualiza(cliente, visualizador):
    url = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    pagina = visualizador.get(url).get_data(as_text=True)
    assert "R$ 25,93" in pagina
    assert visualizador.get(url + "/pdf").data.startswith(b"%PDF")
    assert "Enviar arquivo do mês" not in visualizador.get("/").get_data(as_text=True)
    assert _enviar(visualizador, _arquivo([linha_detalhe(cosip=2600)])).status_code == 403
    assert visualizador.post("/valores/importar").status_code == 403
    assert visualizador.post("/valores/substituir", data={"mes": "202608", "cpf": "1", "cnpj": "1"}).status_code == 403
    assert visualizador.get("/usuarios").status_code == 403
    assert visualizador.get("/valores").status_code == 403
    assert visualizador.get("/auditoria").status_code == 403
    assert "Visualização" in visualizador.get("/").get_data(as_text=True)


def test_entrada_so_com_login(app_):
    pagina = app_.test_client().get("/login").get_data(as_text=True)
    assert "E-mail institucional" in pagina and "consultor" not in pagina.lower()
    assert app_.test_client().post("/entrar-consultor").status_code in (404, 405)


def test_consultor_nao_confirma_valor(cliente, visualizador):
    linhas = [linha_detalhe(mes="202612", cosip=3100), linha_detalhe(mes="202612", cosip=9200, cpf_cnpj="046522942000130")]
    url = _enviar(cliente, _arquivo(linhas, mes="202612")).get_json()["redirect"]
    assert "depois que um administrador confirmar" in visualizador.get(url).get_data(as_text=True)
    assert visualizador.post(url + "/confirmar", data={"escolha": "arquivo"}).status_code == 403


def test_admin_cria_usuario_viewer_edita_e_desativa(app_, cliente):
    from cosip import db
    r = cliente.post("/usuarios/novo", data={"nome": "Maria", "email": "Maria@SantoAndre.sp.gov.br", "perfil": "viewer",
                                             "senha": "12345678", "confirmacao": "12345678"})
    assert r.status_code == 302
    c = _entrar(app_, "maria@santoandre.sp.gov.br", "12345678")
    assert c.get("/").status_code == 200 and c.get("/usuarios").status_code == 403
    uid = db.usuario_por_email("maria@santoandre.sp.gov.br")["id"]
    cliente.post(f"/usuarios/{uid}/editar", data={"nome": "Maria S.", "email": "maria@santoandre.sp.gov.br",
                                                  "perfil": "admin", "ativo": "1"})
    assert c.get("/usuarios").status_code == 200                          # promoção vale na hora
    cliente.post(f"/usuarios/{uid}/editar", data={"nome": "Maria S.", "email": "maria@santoandre.sp.gov.br",
                                                  "perfil": "admin"})      # sem "ativo" = desativa
    assert c.get("/").status_code == 302                                  # sessão derrubada
    r = app_.test_client().post("/login", data={"email": "maria@santoandre.sp.gov.br", "senha": "12345678"})
    assert r.status_code == 401


def test_senha_curta_e_confirmacao(cliente):
    r = cliente.post("/usuarios/novo", data={"nome": "X", "email": "x@x.gov.br", "perfil": "viewer",
                                             "senha": "123", "confirmacao": "123"})
    assert "8 caracteres" in r.get_data(as_text=True)
    r = cliente.post("/usuarios/novo", data={"nome": "X", "email": "x@x.gov.br", "perfil": "viewer",
                                             "senha": "12345678", "confirmacao": "87654321"})
    assert "não coincidem" in r.get_data(as_text=True)


def test_troca_senha_e_exclui(app_, cliente):
    from cosip import db
    uid = db.usuario_por_email("diretor@santoandre.sp.gov.br")["id"]
    cliente.post(f"/usuarios/{uid}/editar", data={"nome": "Diretor", "email": "diretor@santoandre.sp.gov.br",
                                                  "perfil": "viewer", "ativo": "1",
                                                  "senha": "nova-senha-9", "confirmacao": "nova-senha-9"})
    _entrar(app_, "diretor@santoandre.sp.gov.br", "nova-senha-9")
    cliente.post(f"/usuarios/{uid}/excluir")
    assert db.usuario_por_email("diretor@santoandre.sp.gov.br") is None


def test_nao_remove_ultimo_admin(cliente):
    from cosip import db
    uid = db.usuario_por_email("admin@santoandre.sp.gov.br")["id"]
    cliente.post(f"/usuarios/{uid}/editar", data={"nome": "Leonardo", "email": "admin@santoandre.sp.gov.br",
                                                  "perfil": "viewer", "ativo": "1"})
    cliente.post(f"/usuarios/{uid}/excluir")
    u = db.usuario_por_email("admin@santoandre.sp.gov.br")
    assert u and u["perfil"] == "admin" and u["ativo"]


def test_csrf_obrigatorio(app_):
    app_.config["CSRF_ATIVO"] = True
    c = app_.test_client()
    dados = {"email": "admin@santoandre.sp.gov.br", "senha": "senha-admin-1"}
    assert c.post("/login", data=dados).status_code == 400
    c.get("/login")
    with c.session_transaction() as s:
        token = s["csrf"]
    assert c.post("/login", data={**dados, "_csrf": token}).status_code == 302


def test_auditoria_registra_eventos(app_, cliente):
    app_.test_client().post("/login", data={"email": "admin@santoandre.sp.gov.br", "senha": "errada"})
    url = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    cliente.post("/usuarios/novo", data={"nome": "Ana", "email": "ana@x.gov.br", "perfil": "viewer",
                                         "senha": "12345678", "confirmacao": "12345678"})
    pagina = cliente.get("/auditoria").get_data(as_text=True)
    for acao in ("login_negado", "login", "arquivo_processado", "usuario_criado"):
        assert acao in pagina, acao
    assert "agosto/2026" in pagina


def test_saude(app_):
    r = app_.test_client().get("/saude")
    assert r.status_code == 200 and r.data == b"ok"


# ------------------------------------------------------------------ nada fica guardado
def _pasta_tmp(tmp_path):
    p = tmp_path / "tmp"
    return [x for x in p.iterdir() if x.suffix != ".lock"] if p.exists() else []


def test_arquivo_da_enel_nao_fica_guardado(cliente, tmp_path):
    _enviar(cliente, _arquivo(AGOSTO))
    linhas = [linha_detalhe(mes="202612", cosip=3100), linha_detalhe(mes="202612", cosip=9200, cpf_cnpj="046522942000130")]
    _enviar(cliente, _arquivo(linhas, mes="202612"))                 # pendente
    _enviar(cliente, b"invalido\n")                                   # recusado
    assert _pasta_tmp(tmp_path) == []


# ------------------------------------------------------------------ Excel da análise (dados + fórmulas)
def _pedir_excel(c, url, conteudo):
    return c.post(url + "/excel-analise", data={"arquivo": (io.BytesIO(conteudo), "B_A_SAN_COSIP.txt")},
                  content_type="multipart/form-data")


def test_excel_da_analise_sob_demanda(cliente, visualizador, tmp_path):
    from openpyxl import load_workbook
    conteudo = _arquivo(AGOSTO)
    url = _enviar(cliente, conteudo).get_json()["redirect"]
    assert "Baixar Excel da análise" in cliente.get(url).get_data(as_text=True)
    r = _pedir_excel(cliente, url, conteudo)
    assert r.status_code == 200 and r.data.startswith(b"PK")
    assert "CIP_202608_analise_com_formulas.xlsx" in r.headers["Content-Disposition"]
    arq = tmp_path / "a.xlsx"; arq.write_bytes(r.data)
    r.close()
    wb = load_workbook(arq)
    assert wb.sheetnames == ["Indicadores", "Dados"]
    ind = wb["Indicadores"]
    formulas = [c.value for c in ind["B"] if isinstance(c.value, str) and c.value.startswith("=")]
    assert any(f.startswith("=COUNTIFS(Dados!$AB$2:$AB$6,1") for f in formulas)
    assert any("FORMULATEXT" in str(c.value) for c in ind["C"])
    dados = wb["Dados"]
    assert dados.max_row == len(AGOSTO) + 1
    assert {dados.cell(i, 19).value for i in range(2, 7)} >= {"000054974746839", "046522942000130"}
    assert _pasta_tmp(tmp_path) == []                                  # nada ficou no servidor
    assert "excel_analise_baixado" in cliente.get("/auditoria").get_data(as_text=True)
    assert _pedir_excel(visualizador, url, conteudo).status_code == 403
    assert "Excel da análise" not in visualizador.get(url).get_data(as_text=True)


def test_excel_recusa_arquivo_diferente(cliente, tmp_path):
    url = _enviar(cliente, _arquivo(AGOSTO)).get_json()["redirect"]
    outro = _arquivo([linha_detalhe(cosip=2593)])
    r = _pedir_excel(cliente, url, outro)
    assert r.status_code == 302
    assert "não é o mesmo arquivo" in cliente.get(url).get_data(as_text=True)
    assert _pasta_tmp(tmp_path) == []


def test_excel_so_depois_da_confirmacao(cliente):
    linhas = [linha_detalhe(mes="202612", cosip=3100), linha_detalhe(mes="202612", cosip=9200, cpf_cnpj="046522942000130")]
    conteudo = _arquivo(linhas, mes="202612")
    url = _enviar(cliente, conteudo).get_json()["redirect"]
    assert _pedir_excel(cliente, url, conteudo).status_code == 302     # pendente: não gera
    cliente.post(url + "/confirmar", data={"escolha": "arquivo"})
    assert _pedir_excel(cliente, url, conteudo).data.startswith(b"PK")


def test_um_excel_por_vez(cliente):
    from cosip import excel_analise
    conteudo = _arquivo(AGOSTO)
    url = _enviar(cliente, conteudo).get_json()["redirect"]
    with excel_analise._uma_por_vez():
        r = _pedir_excel(cliente, url, conteudo)
    assert r.status_code == 302
    assert "Outro Excel da análise" in cliente.get(url).get_data(as_text=True)
