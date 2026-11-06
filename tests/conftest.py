"""Gera arquivos sintéticos no leiaute da Enel para os testes."""

import pytest


def linha_detalhe(classe="1", unidade="0203805326", nome="FULANO DE TAL",
                  endereco="R ELZA 97", bairro="CENTRO", cidade="SANTO ANDRE",
                  cep="09175730", mes="202608", venc="20260820", credito="20260818",
                  documento="B-2608-080305603", cosip=2593, juros=0, multa=0,
                  codigo_a="01", codigo_b="16", cpf_cnpj="000054974746839") -> bytes:
    sinal = "-" if cosip < 0 else " "
    s = ("D" + classe + unidade.ljust(10)[:10] + nome.ljust(40)[:40] + endereco.ljust(50)[:50]
         + bairro.ljust(25)[:25] + cidade.ljust(25)[:25] + cep + mes + venc + credito
         + documento.ljust(16)[:16] + str(abs(cosip)).rjust(12) + sinal
         + str(juros).zfill(3).rjust(12) + str(multa).zfill(3).rjust(13)
         + " " + codigo_a + codigo_b + cpf_cnpj + " " * 23)
    assert len(s) == 279, len(s)
    return s.encode("latin-1")


def cabecalho(mes="202608", geracao="20260901") -> bytes:
    s = ("HA" + mes + geracao + "C").ljust(223) + "SAN"
    return s.encode("latin-1")


def rodape(qtd, cosip, juros, multa) -> bytes:
    s = ("TA" + str(qtd).zfill(9) + str(cosip).rjust(12) + str(juros).rjust(13)
         + str(multa).rjust(13)).ljust(217) + "SAN"
    return s.encode("latin-1")


@pytest.fixture
def montar_arquivo(tmp_path):
    """montar_arquivo([linhas_detalhe], rodape_override=None) -> caminho do .txt"""
    def _montar(detalhes, cab=None, rod=None, sem_rodape=False, extra=b"", nome="B_A_SAN_COSIP.txt"):
        if rod is None and not sem_rodape:
            # soma a partir das próprias linhas
            q = len(detalhes)
            c = j = m = 0
            for d in detalhes:
                t = d.decode("latin-1")
                v = int(t[198:210]) * (-1 if t[210] == "-" else 1)
                c += v; j += int(t[211:223]); m += int(t[223:236])
            rod = rodape(q, c, j, m)
        conteudo = (cab or cabecalho()) + b"\n" + b"\n".join(detalhes) + b"\n"
        if not sem_rodape:
            conteudo += rod + b"\n"
        conteudo += extra
        p = tmp_path / nome
        p.write_bytes(conteudo)
        return p
    return _montar
