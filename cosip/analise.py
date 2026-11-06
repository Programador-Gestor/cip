"""Cálculo dos 4 indicadores a partir do arquivo mensal da Enel.

Regras (definidas com a Prefeitura em 06/11/2026):
- Mês do arquivo = mês do cabeçalho (posições 3–8). Mês da fatura = mês de referência
  (posições 161–166). Nunca a data de pagamento.
- Só Santo André (cidade, posições 128–152). Registros de outros municípios ficam fora de tudo.
- CPF / CNPJ pelo campo 242–256, validado pelos dígitos verificadores.
- Indicadores 1, 2 e 3 contam só pagamentos com CIP maior que zero (CIP zero = linha só com
  juros/multa; CIP negativa = estorno). Mostram documentos sem repetir e registros.
- Indicador 2: faturas do mês do arquivo com CIP diferente do valor do mês na base histórica
  (CPF e CNPJ têm valores próprios).
- Indicador 4: valor recebido (CIP + juros + multa, estornos descontados) das faturas do mês do
  arquivo e de cada um dos outros meses.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .documentos import CNPJ, CPF, classificar
from .leitor import ArquivoInvalido, LeituraCOSIP

MUNICIPIO = "SANTO ANDRE"
TIPOS = (CPF, CNPJ)


@dataclass
class Contagem:
    documentos: int = 0     # CPF/CNPJ sem repetir
    registros: int = 0      # pagamentos (linhas)


@dataclass
class Indicador:
    cpf: Contagem = field(default_factory=Contagem)
    cnpj: Contagem = field(default_factory=Contagem)

    @property
    def total(self) -> Contagem:
        # um número não pode ser CPF e CNPJ ao mesmo tempo, então somar não duplica
        return Contagem(self.cpf.documentos + self.cnpj.documentos, self.cpf.registros + self.cnpj.registros)

    def de(self, tipo: str) -> Contagem:
        return self.cpf if tipo == CPF else self.cnpj

    @classmethod
    def de_dict(cls, d: dict) -> "Indicador":
        return cls(Contagem(**d["cpf"]), Contagem(**d["cnpj"]))


@dataclass
class ValorMes:
    mes: str          # AAAAMM (referência da fatura)
    registros: int
    valor: int        # centavos


@dataclass
class ForaDasContagens:
    """O que não entra nos indicadores 1–3 (e por quê). Mês = faturas do mês do arquivo."""
    outros_municipios: int = 0
    outros_municipios_valor: int = 0
    sem_documento_mes: int = 0
    sem_documento_outros: int = 0
    zero_estorno_mes: int = 0
    zero_estorno_outros: int = 0


@dataclass
class Resultado:
    nome_arquivo: str
    hash_sha256: str
    mes_arquivo: str                  # AAAAMM
    qtd_registros_arquivo: int
    valor_cpf: int | None             # valor da CIP do mês usado no indicador 2 (centavos)
    valor_cnpj: int | None
    ind1: Indicador                   # pagamentos de faturas do mês do arquivo
    ind2: Indicador | None            # ... com valor diferente da CIP do mês
    ind3: Indicador                   # pagamentos de faturas de outros meses
    valor_mes: ValorMes               # indicador 4: faturas do mês do arquivo
    outros_meses: list[ValorMes]      # indicador 4: cada um dos outros meses (mais recente primeiro)
    fora: ForaDasContagens
    avisos: list[str] = field(default_factory=list)

    @property
    def valor_outros(self) -> int:
        return sum(m.valor for m in self.outros_meses)

    @property
    def registros_outros(self) -> int:
        return sum(m.registros for m in self.outros_meses)

    @property
    def valor_total(self) -> int:
        return self.valor_mes.valor + self.valor_outros

    def para_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict) -> "Resultado":
        d = dict(d)
        d["ind1"] = Indicador.de_dict(d["ind1"])
        d["ind2"] = Indicador.de_dict(d["ind2"]) if d.get("ind2") else None
        d["ind3"] = Indicador.de_dict(d["ind3"])
        d["valor_mes"] = ValorMes(**d["valor_mes"])
        d["outros_meses"] = [ValorMes(**m) for m in d["outros_meses"]]
        d["fora"] = ForaDasContagens(**d["fora"])
        return cls(**d)


@dataclass
class ValorEncontrado:
    """Valor da CIP mais pago no mês do arquivo, por tipo (para conferir com a base)."""
    valor: int | None
    registros: int       # quantos pagamentos tiveram esse valor
    de: int              # de quantos pagamentos (CIP > 0) do tipo no mês


class Apuracao:
    """Lê o arquivo uma vez e guarda o necessário para calcular o indicador 2 com qualquer valor."""

    def __init__(self, caminho: str | Path, nome_arquivo: str = "", hash_sha256: str = ""):
        self.leitura = LeituraCOSIP(caminho)
        mes_valor: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # mes -> [registros, valor]
        fora = ForaDasContagens()
        docs_mes = {t: defaultdict(set) for t in TIPOS}    # tipo -> documento -> valores de CIP pagos
        reg_mes = {t: 0 for t in TIPOS}
        valores_mes = {t: Counter() for t in TIPOS}         # tipo -> valor -> registros
        docs_outros = {t: set() for t in TIPOS}
        reg_outros = {t: 0 for t in TIPOS}
        valor_fora = 0
        mes_arq = None

        for reg in self.leitura.registros():
            if mes_arq is None:
                mes_arq = self.leitura.cabecalho.mes_movimento
            if reg.cidade != MUNICIPIO:
                fora.outros_municipios += 1
                fora.outros_municipios_valor += reg.total
                valor_fora += reg.total
                continue
            do_mes = reg.mes_cobranca == mes_arq
            mv = mes_valor[reg.mes_cobranca]
            mv[0] += 1
            mv[1] += reg.total
            tipo, _ = classificar(reg.cpf_cnpj)
            if tipo not in TIPOS:
                if do_mes:
                    fora.sem_documento_mes += 1
                else:
                    fora.sem_documento_outros += 1
                continue
            if reg.cosip <= 0:
                if do_mes:
                    fora.zero_estorno_mes += 1
                else:
                    fora.zero_estorno_outros += 1
                continue
            numero = reg.cpf_cnpj[-11:] if tipo == CPF else reg.cpf_cnpj[-14:]
            if do_mes:
                docs_mes[tipo][numero].add(reg.cosip)
                reg_mes[tipo] += 1
                valores_mes[tipo][reg.cosip] += 1
            else:
                docs_outros[tipo].add(numero)
                reg_outros[tipo] += 1

        self.mes_arquivo = self.leitura.cabecalho.mes_movimento
        self._docs_mes = docs_mes
        self._valores_mes = valores_mes
        self.ind1 = Indicador(Contagem(len(docs_mes[CPF]), reg_mes[CPF]), Contagem(len(docs_mes[CNPJ]), reg_mes[CNPJ]))
        self.ind3 = Indicador(Contagem(len(docs_outros[CPF]), reg_outros[CPF]),
                              Contagem(len(docs_outros[CNPJ]), reg_outros[CNPJ]))
        rv = mes_valor.get(self.mes_arquivo, [0, 0])
        self.valor_mes = ValorMes(self.mes_arquivo, rv[0], rv[1])
        self.outros_meses = [ValorMes(m, q, v) for m, (q, v) in sorted(mes_valor.items(), reverse=True)
                             if m != self.mes_arquivo]
        self.fora = fora
        self.nome_arquivo = nome_arquivo or Path(caminho).name
        self.hash_sha256 = hash_sha256
        self._conferir(valor_fora)

    # ------------------------------------------------------------------ valores da CIP
    def valor_encontrado(self, tipo: str) -> ValorEncontrado:
        c = self._valores_mes[tipo]
        if not c:
            return ValorEncontrado(None, 0, 0)
        valor, n = c.most_common(1)[0]
        return ValorEncontrado(valor, n, sum(c.values()))

    def indicador2(self, valor_cpf: int, valor_cnpj: int) -> Indicador:
        out = Indicador()
        for tipo, alvo in ((CPF, valor_cpf), (CNPJ, valor_cnpj)):
            regs = sum(self._valores_mes[tipo].values()) - self._valores_mes[tipo].get(alvo, 0)
            docs = sum(1 for vals in self._docs_mes[tipo].values() if vals - {alvo})
            c = out.de(tipo)
            c.documentos, c.registros = docs, regs
        return out

    def resultado(self, valor_cpf: int | None, valor_cnpj: int | None) -> Resultado:
        ind2 = None
        if valor_cpf is not None and valor_cnpj is not None:
            ind2 = self.indicador2(valor_cpf, valor_cnpj)
        return Resultado(
            nome_arquivo=self.nome_arquivo, hash_sha256=self.hash_sha256, mes_arquivo=self.mes_arquivo,
            qtd_registros_arquivo=self.leitura.qtd_registros, valor_cpf=valor_cpf, valor_cnpj=valor_cnpj,
            ind1=self.ind1, ind2=ind2, ind3=self.ind3, valor_mes=self.valor_mes,
            outros_meses=self.outros_meses, fora=self.fora, avisos=self.avisos(),
        )

    def avisos(self) -> list[str]:
        out = list(self.leitura.avisos)
        futuros = [m.mes for m in self.outros_meses if m.mes > self.mes_arquivo]
        if futuros:
            out.append("Faturas com referência posterior ao mês do arquivo: " + ", ".join(futuros)
                       + " (contadas em outros meses).")
        return out

    # ------------------------------------------------------------------ conferência
    def _conferir(self, valor_fora: int) -> None:
        """Confere que as quantidades e os valores fecham. Se não fecharem, recusa o arquivo."""
        lt = self.leitura
        erros = []
        total_arquivo = lt.soma_cosip + lt.soma_juros + lt.soma_multa
        valor_sa = self.valor_mes.valor + sum(m.valor for m in self.outros_meses)
        if valor_sa + valor_fora != total_arquivo:
            erros.append("Valor por mês + outros municípios não fecha com o total do arquivo.")
        regs_sa = self.valor_mes.registros + sum(m.registros for m in self.outros_meses)
        if regs_sa + self.fora.outros_municipios != lt.qtd_registros:
            erros.append("Quantidade de registros por mês não fecha com o total do arquivo.")
        f = self.fora
        if self.ind1.total.registros + f.sem_documento_mes + f.zero_estorno_mes != self.valor_mes.registros:
            erros.append("Registros do mês do arquivo não fecham (CPF + CNPJ + fora das contagens).")
        if (self.ind3.total.registros + f.sem_documento_outros + f.zero_estorno_outros
                != sum(m.registros for m in self.outros_meses)):
            erros.append("Registros de outros meses não fecham (CPF + CNPJ + fora das contagens).")
        for i in (self.ind1, self.ind3):
            for c in (i.cpf, i.cnpj):
                if c.documentos > c.registros:
                    erros.append("Mais documentos do que registros.")
        if erros:
            raise ArquivoInvalido(["Conferência interna falhou: " + e for e in erros])
