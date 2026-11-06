import os
from pathlib import Path

import pytest

from cosip import ArquivoInvalido, LeituraCOSIP, validar_arquivo

from conftest import cabecalho, linha_detalhe, rodape


def ler_tudo(caminho):
    leitura = LeituraCOSIP(caminho)
    regs = list(leitura.registros())
    return leitura, regs


# ------------------------------------------------------------------ casos válidos
def test_arquivo_valido(montar_arquivo):
    p = montar_arquivo([
        linha_detalhe(cosip=2593),
        linha_detalhe(cosip=2601, juros=16, multa=51, mes="202607"),
        linha_detalhe(cosip=-2601, documento="****130024310040"),
    ])
    leitura, regs = ler_tudo(p)
    assert leitura.concluida
    assert leitura.cabecalho.mes_movimento == "202608"
    assert len(regs) == 3
    assert [r.cosip for r in regs] == [2593, 2601, -2601]
    assert regs[1].total == 2601 + 16 + 51
    assert regs[0].eh_fatura and not regs[2].eh_fatura
    assert regs[0].linha == 2          # cabeçalho é a linha 1


def test_acentos_latin1(montar_arquivo):
    p = montar_arquivo([linha_detalhe(nome="NÁDIA CONCEIÇÃO")])
    _, regs = ler_tudo(p)
    assert regs[0].nome == "NÁDIA CONCEIÇÃO"


def test_crlf_gera_aviso_mas_aceita(tmp_path):
    d = linha_detalhe()
    conteudo = b"\r\n".join([cabecalho(), d, rodape(1, 2593, 0, 0)]) + b"\r\n"
    p = tmp_path / "x.txt"; p.write_bytes(conteudo)
    leitura, regs = ler_tudo(p)
    assert len(regs) == 1
    assert any("CRLF" in a for a in leitura.avisos)


# ------------------------------------------------------------------ recusas
def test_recusa_sem_cabecalho(tmp_path):
    p = tmp_path / "x.txt"; p.write_bytes(linha_detalhe() + b"\n")
    with pytest.raises(ArquivoInvalido, match="cabeçalho"):
        ler_tudo(p)


def test_recusa_rodape_divergente(montar_arquivo):
    p = montar_arquivo([linha_detalhe(cosip=2593)], rod=rodape(1, 9999, 0, 0))
    with pytest.raises(ArquivoInvalido, match="Total da COSIP não confere"):
        ler_tudo(p)


def test_recusa_quantidade_divergente(montar_arquivo):
    p = montar_arquivo([linha_detalhe(cosip=2593)], rod=rodape(2, 2593, 0, 0))
    with pytest.raises(ArquivoInvalido, match="Quantidade de registros"):
        ler_tudo(p)


def test_recusa_sem_rodape(montar_arquivo):
    p = montar_arquivo([linha_detalhe()], sem_rodape=True)
    with pytest.raises(ArquivoInvalido, match="Rodapé 'TA' não encontrado"):
        ler_tudo(p)


def test_recusa_linha_com_tamanho_errado(montar_arquivo):
    curta = linha_detalhe()[:-1]
    p = montar_arquivo([linha_detalhe(), curta], rod=rodape(2, 5186, 0, 0))
    with pytest.raises(ArquivoInvalido, match="tamanho diferente de 279"):
        ler_tudo(p)


def test_recusa_arquivo_salvo_em_utf8(tmp_path):
    d = linha_detalhe(nome="NÁDIA").decode("latin-1").encode("utf-8")  # 280 bytes
    p = tmp_path / "x.txt"
    p.write_bytes(cabecalho() + b"\n" + d + b"\n" + rodape(1, 2593, 0, 0) + b"\n")
    with pytest.raises(ArquivoInvalido, match="UTF-8"):
        ler_tudo(p)


def test_recusa_valor_nao_numerico(montar_arquivo):
    d = bytearray(linha_detalhe()); d[205] = ord("X")
    p = montar_arquivo([bytes(d)], rod=rodape(1, 2593, 0, 0))
    with pytest.raises(ArquivoInvalido, match="Valor da COSIP não numérico"):
        ler_tudo(p)


def test_recusa_linha_apos_rodape(montar_arquivo):
    p = montar_arquivo([linha_detalhe()], extra=linha_detalhe() + b"\n")
    with pytest.raises(ArquivoInvalido, match="depois do rodapé"):
        ler_tudo(p)


def test_lista_todos_os_problemas_de_uma_vez(montar_arquivo):
    curta = linha_detalhe()[:-1]
    p = montar_arquivo([curta, curta, curta], rod=rodape(3, 0, 0, 0))
    with pytest.raises(ArquivoInvalido) as e:
        ler_tudo(p)
    assert "3 linha(s)" in str(e.value)


def test_data_invalida_e_so_aviso(montar_arquivo):
    p = montar_arquivo([linha_detalhe(venc="20260231")])
    leitura, regs = ler_tudo(p)
    assert len(regs) == 1
    assert any("vencimento inválida" in a for a in leitura.avisos)


# ------------------------------------------------------------------ arquivo real
ARQUIVO_REAL = os.environ.get("CIP_ARQUIVO_AGOSTO")


@pytest.mark.skipif(not ARQUIVO_REAL or not Path(ARQUIVO_REAL).exists(),
                    reason="defina CIP_ARQUIVO_AGOSTO com o caminho do arquivo de agosto/2026")
def test_arquivo_real_agosto_2026():
    leitura = validar_arquivo(ARQUIVO_REAL)
    assert leitura.cabecalho.mes_movimento == "202608"
    assert leitura.qtd_registros == 464_392
    assert leitura.soma_cosip == 1_038_411_324
    assert leitura.soma_juros == 3_444_556
    assert leitura.soma_multa == 7_845_907
