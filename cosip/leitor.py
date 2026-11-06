"""Leitura e validação do arquivo mensal da CIP/COSIP (Enel -> Prefeitura de Santo André).

Uso:

    leitura = LeituraCOSIP("B_A_SAN_COSIP.txt")
    for reg in leitura.registros():   # valida enquanto lê
        ...                           # acumule o que precisar
    # se chegou aqui, o arquivo inteiro foi validado e bateu com o rodapé

Se qualquer problema estrutural for encontrado, `registros()` levanta
ArquivoInvalido AO FINAL da leitura (depois de percorrer o arquivo todo, para
listar todos os problemas de uma vez). Quem consome o gerador nunca chega a usar
um resultado parcial: a exceção interrompe o fluxo antes.
"""

from __future__ import annotations

import gzip

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterator

from . import leiaute as L

MAX_EXEMPLOS_POR_PROBLEMA = 5


class ArquivoInvalido(Exception):
    """O arquivo não segue o leiaute esperado ou não fecha com o rodapé."""

    def __init__(self, erros: list[str]):
        self.erros = erros
        super().__init__("Arquivo recusado:\n- " + "\n- ".join(erros))


@dataclass(slots=True)
class Registro:
    linha: int            # número da linha no arquivo (cabeçalho = linha 1)
    classe: str
    unidade: str
    nome: str
    endereco: str
    bairro: str
    cidade: str
    cep: str
    mes_cobranca: str     # AAAAMM
    vencimento: str       # AAAAMMDD
    data_credito: str     # AAAAMMDD
    documento: str
    cosip: int            # centavos, com sinal
    juros: int            # centavos
    multa: int            # centavos
    codigo_a: str
    codigo_b: str
    cpf_cnpj: str         # 15 dígitos

    @property
    def total(self) -> int:
        """COSIP + juros + multa, em centavos (com sinal)."""
        return self.cosip + self.juros + self.multa

    @property
    def eh_fatura(self) -> bool:
        """Fatura (B-/A-/L-AAMM-...) ou lançamento de ajuste/estorno (****..., F#, 0000...)."""
        return self.documento[:2] in ("B-", "A-", "L-")


@dataclass
class Cabecalho:
    mes_movimento: str   # AAAAMM
    data_geracao: str    # AAAAMMDD (como gravado)


@dataclass
class Rodape:
    qtd_registros: int
    total_cosip: int
    total_juros: int
    total_multa: int


@dataclass
class _Problemas:
    """Agrupa problemas iguais para não gerar 400 mil mensagens."""
    contagem: dict[str, int] = field(default_factory=dict)
    exemplos: dict[str, list[int]] = field(default_factory=dict)

    def add(self, tipo: str, linha: int) -> None:
        self.contagem[tipo] = self.contagem.get(tipo, 0) + 1
        ex = self.exemplos.setdefault(tipo, [])
        if len(ex) < MAX_EXEMPLOS_POR_PROBLEMA:
            ex.append(linha)

    def mensagens(self) -> list[str]:
        out = []
        for tipo, n in self.contagem.items():
            linhas = ", ".join(str(x) for x in self.exemplos[tipo])
            mais = "" if n <= len(self.exemplos[tipo]) else ", ..."
            out.append(f"{tipo}: {n} linha(s) (ex.: linha {linhas}{mais})")
        return out

    def __bool__(self) -> bool:
        return bool(self.contagem)


def _data_valida(aaaammdd: str) -> bool:
    if len(aaaammdd) != 8 or not aaaammdd.isdigit():
        return False
    try:
        date(int(aaaammdd[:4]), int(aaaammdd[4:6]), int(aaaammdd[6:]))
        return True
    except ValueError:
        return False


def _mes_valido(aaaamm: str) -> bool:
    return len(aaaamm) == 6 and aaaamm.isdigit() and 1 <= int(aaaamm[4:]) <= 12


def _inteiro_rodape(texto: str) -> int | None:
    t = texto.strip()
    neg = t.endswith("-") or t.startswith("-")
    t = t.strip("-").strip()
    if not t.isdigit():
        return None
    return -int(t) if neg else int(t)


class LeituraCOSIP:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.cabecalho: Cabecalho | None = None
        self.rodape: Rodape | None = None
        self.qtd_registros = 0
        self.soma_cosip = 0
        self.soma_juros = 0
        self.soma_multa = 0
        self.avisos: list[str] = []
        self.concluida = False

    # ------------------------------------------------------------------ leitura
    def registros(self) -> Iterator[Registro]:
        erros: list[str] = []
        prob = _Problemas()      # problemas que impedem o uso do arquivo
        avis = _Problemas()      # problemas que não afetam as respostas
        linhas_apos_rodape = 0

        abrir = gzip.open if self.caminho.suffix == ".gz" else open   # cópia guardada para gerar o Excel
        with abrir(self.caminho, "rb") as f:
            for n, bruto in enumerate(f, start=1):
                bruto = bruto.rstrip(b"\n")
                if bruto.endswith(b"\r"):
                    bruto = bruto[:-1]
                    avis.add("Quebra de linha no padrão Windows (CRLF)", n)
                if not bruto.strip():
                    avis.add("Linha em branco ignorada", n)
                    continue
                texto = bruto.decode(L.CODIFICACAO)

                # ---- cabeçalho
                if n == 1:
                    if L.campo(texto, L.CAB_TIPO) != "HA":
                        raise ArquivoInvalido([
                            "A primeira linha não é um cabeçalho 'HA'. "
                            "Este não parece ser o arquivo B_A_SAN_COSIP da Enel."])
                    mes = L.campo(texto, L.CAB_MES_MOVIMENTO)
                    if not _mes_valido(mes):
                        raise ArquivoInvalido([f"Mês de movimento inválido no cabeçalho: '{mes}'."])
                    self.cabecalho = Cabecalho(mes, L.campo(texto, L.CAB_DATA_GERACAO))
                    continue

                # ---- rodapé
                if texto.startswith("TA"):
                    if self.rodape is not None:
                        prob.add("Mais de um rodapé 'TA'", n)
                        continue
                    valores = [_inteiro_rodape(L.campo(texto, p)) for p in (
                        L.ROD_QTD_REGISTROS, L.ROD_TOTAL_COSIP, L.ROD_TOTAL_JUROS, L.ROD_TOTAL_MULTA)]
                    if any(v is None for v in valores):
                        erros.append(f"Rodapé (linha {n}) com totais ilegíveis.")
                        self.rodape = Rodape(-1, 0, 0, 0)
                    else:
                        self.rodape = Rodape(*valores)
                    continue

                if self.rodape is not None:
                    linhas_apos_rodape += 1
                    prob.add("Linha depois do rodapé", n)
                    continue

                # ---- detalhe
                reg = self._ler_detalhe(n, bruto, texto, prob, avis)
                if reg is None:
                    continue
                self.qtd_registros += 1
                self.soma_cosip += reg.cosip
                self.soma_juros += reg.juros
                self.soma_multa += reg.multa
                yield reg

        # ---- conferências finais
        if self.cabecalho is None:
            raise ArquivoInvalido(["Arquivo vazio."])
        erros.extend(prob.mensagens())
        if self.rodape is None:
            erros.append("Rodapé 'TA' não encontrado (arquivo incompleto ou cortado).")
        elif self.rodape.qtd_registros >= 0 and not prob:
            r = self.rodape
            if r.qtd_registros != self.qtd_registros:
                erros.append(f"Quantidade de registros não confere: rodapé {r.qtd_registros}, lidos {self.qtd_registros}.")
            if r.total_cosip != self.soma_cosip:
                erros.append(f"Total da COSIP não confere: rodapé {r.total_cosip}, soma {self.soma_cosip} (centavos).")
            if r.total_juros != self.soma_juros:
                erros.append(f"Total de juros não confere: rodapé {r.total_juros}, soma {self.soma_juros} (centavos).")
            if r.total_multa != self.soma_multa:
                erros.append(f"Total da multa não confere: rodapé {r.total_multa}, soma {self.soma_multa} (centavos).")
        if self.qtd_registros == 0 and not erros:
            erros.append("O arquivo não tem nenhuma linha de detalhe.")

        self.avisos = avis.mensagens()
        if erros:
            raise ArquivoInvalido(erros)
        self.concluida = True

    def _ler_detalhe(self, n, bruto: bytes, texto: str, prob: _Problemas, avis: _Problemas) -> Registro | None:
        if len(bruto) != L.TAMANHO_DETALHE:
            if len(bruto) > L.TAMANHO_DETALHE and any(b >= 0x80 for b in bruto):
                prob.add(f"Linha com tamanho diferente de {L.TAMANHO_DETALHE} (o arquivo pode ter sido salvo em UTF-8 em vez de Latin-1)", n)
            else:
                prob.add(f"Linha com tamanho diferente de {L.TAMANHO_DETALHE} posições (leiaute pode ter mudado)", n)
            return None
        c = lambda p: L.campo(texto, p)
        if c(L.DET_TIPO) != "D":
            prob.add("Tipo de registro diferente de 'D'", n)
            return None
        classe = c(L.DET_CLASSE)
        valor, sinal, juros, multa = c(L.DET_VALOR_COSIP), c(L.DET_SINAL), c(L.DET_JUROS), c(L.DET_MULTA)
        mes, doc = c(L.DET_MES_COBRANCA), c(L.DET_CPF_CNPJ)
        ok = True
        if not classe.isdigit():
            prob.add("Classe não numérica", n); ok = False
        for nome, v in (("Valor da COSIP", valor), ("Juros", juros), ("Multa", multa)):
            if not v.strip().isdigit():
                prob.add(f"{nome} não numérico", n); ok = False
        if sinal not in (" ", "-"):
            prob.add("Sinal do valor diferente de ' ' ou '-'", n); ok = False
        if not _mes_valido(mes):
            prob.add("Mês de cobrança inválido", n); ok = False
        if not doc.isdigit():
            prob.add("CPF/CNPJ não numérico", n); ok = False
        if c((237, 237)) != " " or c((257, 279)).strip():
            prob.add("Posições que deveriam estar em branco estão preenchidas (leiaute pode ter mudado)", n); ok = False
        if not ok:
            return None

        venc, cred = c(L.DET_VENCIMENTO), c(L.DET_DATA_CREDITO)
        if not _data_valida(venc):
            avis.add("Data de vencimento inválida", n)
        if not _data_valida(cred):
            avis.add("Data de crédito inválida", n)
        elif self.cabecalho and cred[:6] != self.cabecalho.mes_movimento:
            avis.add("Data de crédito fora do mês de movimento do cabeçalho", n)
        if classe not in L.CLASSES:
            avis.add(f"Classe '{classe}' não conhecida", n)

        cosip = int(valor) * (-1 if sinal == "-" else 1)
        return Registro(
            linha=n, classe=classe, unidade=c(L.DET_UNIDADE), nome=c(L.DET_NOME).strip(),
            endereco=c(L.DET_ENDERECO).strip(), bairro=c(L.DET_BAIRRO).strip(),
            cidade=c(L.DET_CIDADE).strip(), cep=c(L.DET_CEP), mes_cobranca=mes,
            vencimento=venc, data_credito=cred, documento=c(L.DET_DOCUMENTO).strip(),
            cosip=cosip, juros=int(juros), multa=int(multa),
            codigo_a=c(L.DET_CODIGO_A), codigo_b=c(L.DET_CODIGO_B), cpf_cnpj=doc,
        )


def validar_arquivo(caminho: str | Path) -> LeituraCOSIP:
    """Lê o arquivo inteiro só para validar. Levanta ArquivoInvalido se houver problema."""
    leitura = LeituraCOSIP(caminho)
    for _ in leitura.registros():
        pass
    return leitura
