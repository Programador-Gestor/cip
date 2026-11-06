import io

from openpyxl import Workbook

from cosip.valores import centavos, gerar_planilha, ler_planilha, mes_de_rotulo, valores_iniciais


def test_arredondamento_igual_a_enel():
    assert centavos(25.925130495157504) == 2593
    assert centavos(77.07677521) == 7708
    assert centavos("25,93") == 2593


def test_rotulos_de_mes():
    assert mes_de_rotulo("Ago/26") == "202608"
    assert mes_de_rotulo("dez/2025") == "202512"
    assert mes_de_rotulo("03/2026") == "202603"
    assert mes_de_rotulo("Mês") is None


def test_valores_iniciais_da_planilha():
    v = {x.mes: (x.cpf, x.cnpj) for x in valores_iniciais()}
    assert v["202608"] == (2593, 7720)
    assert v["202605"] == (2751, 8193)      # mantido como na planilha (diverge do arquivo da Enel)
    assert len(v) == 9


def test_planilha_ida_e_volta():
    from cosip.valores import ValorCIP
    dados = gerar_planilha([ValorCIP("202612", 3100, 9200)])
    assert [(v.mes, v.cpf, v.cnpj) for v in ler_planilha(dados)] == [("202612", 3100, 9200)]


def test_planilha_no_formato_da_prefeitura():
    wb = Workbook(); ws = wb.active
    ws.append(["C.I.P."]); ws.append([None, "CPF", "CPNJ"]); ws.append(["Mês", "(R$)", "(R$)"])
    ws.append(["Dez/26", 31.004, 92.555])
    buf = io.BytesIO(); wb.save(buf)
    assert [(v.mes, v.cpf, v.cnpj) for v in ler_planilha(buf.getvalue())] == [("202612", 3100, 9256)]
