import os
from pathlib import Path

import pytest

from cosip.analise import Apuracao
from conftest import cabecalho, linha_detalhe

CPF_A, CPF_B = "000054974746839", "000026620502849"
CNPJ_A = "046522942000130"


def test_indicadores_sinteticos(montar_arquivo):
    p = montar_arquivo([
        linha_detalhe(cpf_cnpj=CPF_A, cosip=2593),                              # mês, valor certo
        linha_detalhe(cpf_cnpj=CPF_A, cosip=269),                               # mesmo CPF, outro valor
        linha_detalhe(cpf_cnpj=CPF_B, cosip=2593),
        linha_detalhe(cpf_cnpj=CNPJ_A, cosip=7720),
        linha_detalhe(cpf_cnpj=CNPJ_A, cosip=7746),                             # CNPJ, outro valor
        linha_detalhe(cpf_cnpj=CPF_B, cosip=0, juros=10, multa=52),             # CIP zero: fora das contagens
        linha_detalhe(cpf_cnpj=CPF_B, cosip=-2593, documento="****130024310040"),  # estorno: fora
        linha_detalhe(cpf_cnpj="000000000000000", cosip=2593),                  # sem documento: fora
        linha_detalhe(cpf_cnpj=CPF_A, cosip=2601, mes="202607", juros=16, multa=51),  # outro mês
        linha_detalhe(cpf_cnpj=CPF_A, cosip=2588, mes="202606"),
        linha_detalhe(cpf_cnpj=CNPJ_A, cosip=7746, mes="202607"),
        linha_detalhe(cpf_cnpj=CPF_B, cosip=2593, cidade="SAO CAETANO DO SUL"),  # outro município
    ])
    a = Apuracao(p)
    r = a.resultado(2593, 7720)
    assert r.mes_arquivo == "202608"
    assert (r.ind1.cpf.documentos, r.ind1.cpf.registros) == (2, 3)
    assert (r.ind1.cnpj.documentos, r.ind1.cnpj.registros) == (1, 2)
    assert (r.ind1.total.documentos, r.ind1.total.registros) == (3, 5)
    assert (r.ind2.cpf.documentos, r.ind2.cpf.registros) == (1, 1)
    assert (r.ind2.cnpj.documentos, r.ind2.cnpj.registros) == (1, 1)
    assert (r.ind3.cpf.documentos, r.ind3.cpf.registros) == (1, 2)
    assert (r.ind3.cnpj.documentos, r.ind3.cnpj.registros) == (1, 1)
    mes = 2593 + 269 + 2593 + 7720 + 7746 + 62 - 2593 + 2593
    assert r.valor_mes.valor == mes and r.valor_mes.registros == 8
    assert [(m.mes, m.valor) for m in r.outros_meses] == [("202607", 2601 + 16 + 51 + 7746), ("202606", 2588)]
    assert r.valor_total == mes + 2601 + 16 + 51 + 7746 + 2588
    f = r.fora
    assert (f.outros_municipios, f.sem_documento_mes, f.zero_estorno_mes) == (1, 1, 2)
    assert a.valor_encontrado("CPF").valor == 2593


def test_indicador2_depende_do_valor(montar_arquivo):
    a = Apuracao(montar_arquivo([linha_detalhe(cosip=2593), linha_detalhe(cosip=2586, cpf_cnpj=CPF_B)]))
    assert a.indicador2(2593, 7720).cpf.registros == 1
    assert a.indicador2(2751, 7720).cpf.registros == 2


def test_mes_vem_do_cabecalho(montar_arquivo):
    a = Apuracao(montar_arquivo([linha_detalhe(mes="202609"), linha_detalhe(mes="202608", cpf_cnpj=CPF_B)],
                                cab=cabecalho(mes="202609")))
    assert (a.ind1.total.registros, a.ind3.total.registros) == (1, 1)


ARQUIVO_REAL = os.environ.get("CIP_ARQUIVO_AGOSTO")


@pytest.mark.skipif(not ARQUIVO_REAL or not Path(ARQUIVO_REAL).exists(),
                    reason="defina CIP_ARQUIVO_AGOSTO com o caminho do arquivo de agosto/2026")
def test_agosto_2026():
    """Números conferidos em 06/11/2026 (planilha CIP_Agosto_2026_dados_e_formulas.xlsx)."""
    r = Apuracao(ARQUIVO_REAL).resultado(2593, 7720)
    assert (r.ind1.cpf.documentos, r.ind1.cpf.registros) == (236_372, 264_416)
    assert (r.ind1.cnpj.documentos, r.ind1.cnpj.registros) == (5_336, 8_323)
    assert (r.ind2.cpf.documentos, r.ind2.cpf.registros) == (135_210, 146_826)
    assert (r.ind2.cnpj.documentos, r.ind2.cnpj.registros) == (1_104, 1_657)
    assert r.valor_mes.valor == 542_574_441
    assert r.valor_outros == 506_974_701
    assert r.valor_total == 1_049_549_142
    assert r.fora.outros_municipios == 66
