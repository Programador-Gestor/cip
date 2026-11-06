"""Base histórica dos valores da CIP/COSIP (CPF e CNPJ por mês de referência).

Os valores ficam em centavos. A planilha da Prefeitura traz os valores com várias casas
decimais (ex.: 25,925130...); a Enel cobra o valor arredondado para centavos (25,93),
o que foi conferido no arquivo de agosto/2026 para mar, abr, jun, jul e ago/2026.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
ARQUIVO_INICIAL = Path(__file__).with_name("valores_cip_inicial.csv")


@dataclass
class ValorCIP:
    mes: str          # AAAAMM (mês de referência da fatura)
    cpf: int          # centavos
    cnpj: int         # centavos
    origem: str = ""


class PlanilhaInvalida(Exception):
    pass


def centavos(valor) -> int:
    """Reais (número ou texto '25,93') -> centavos, arredondando meio centavo para cima."""
    if isinstance(valor, str):
        valor = valor.strip().replace("R$", "").replace(".", "").replace(",", ".").strip()
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def mes_de_rotulo(rotulo) -> str | None:
    """'Mar/26', 'mar/2026', '03/2026', '202603' ou data -> '202603'. None se não for mês."""
    if isinstance(rotulo, (datetime, date)):
        return f"{rotulo.year:04d}{rotulo.month:02d}"
    if rotulo is None:
        return None
    t = str(rotulo).strip().lower()
    m = re.fullmatch(r"([a-zç]{3})[a-zç]*[/\-. ](\d{2}|\d{4})", t)
    if m and m.group(1) in ABREV:
        ano = int(m.group(2))
        ano = ano + 2000 if ano < 100 else ano
        return f"{ano:04d}{ABREV.index(m.group(1)) + 1:02d}"
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}{int(m.group(1)):02d}"
    if re.fullmatch(r"\d{6}", t) and 1 <= int(t[4:]) <= 12:
        return t
    return None


def rotulo_mes(aaaamm: str) -> str:
    """'202603' -> 'Mar/26' (mesmo formato da planilha da Prefeitura)."""
    return f"{ABREV[int(aaaamm[4:]) - 1].capitalize()}/{aaaamm[2:4]}"


def ler_planilha(conteudo: bytes) -> list[ValorCIP]:
    """Lê a planilha de valores (coluna A = mês, B = CPF, C = CNPJ, em reais)."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise PlanilhaInvalida(f"Não consegui abrir a planilha ({e.__class__.__name__}).") from e
    valores: dict[str, ValorCIP] = {}
    problemas = []
    for n, linha in enumerate(wb.worksheets[0].iter_rows(values_only=True), start=1):
        if not linha or len(linha) < 3:
            continue
        mes = mes_de_rotulo(linha[0])
        if not mes:
            continue
        try:
            v = ValorCIP(mes, centavos(linha[1]), centavos(linha[2]), "planilha")
        except Exception:  # noqa: BLE001
            problemas.append(f"linha {n} ({linha[0]}): valores de CPF/CNPJ ilegíveis")
            continue
        if v.cpf <= 0 or v.cnpj <= 0:
            problemas.append(f"linha {n} ({linha[0]}): valor zero ou negativo")
            continue
        if mes in valores and (valores[mes].cpf, valores[mes].cnpj) != (v.cpf, v.cnpj):
            problemas.append(f"linha {n} ({linha[0]}): mês repetido com valores diferentes")
            continue
        valores[mes] = v
    if problemas:
        raise PlanilhaInvalida("Planilha com problemas: " + "; ".join(problemas))
    if not valores:
        raise PlanilhaInvalida("Nenhum mês encontrado. Esperado: coluna A com o mês (ex.: Ago/26), "
                               "B com o valor de CPF e C com o valor de CNPJ, em reais.")
    return sorted(valores.values(), key=lambda v: v.mes)


def valores_iniciais() -> list[ValorCIP]:
    """Valores da planilha Valores_CIP.xlsx enviada em 06/11/2026 (já arredondados)."""
    out = []
    for linha in ARQUIVO_INICIAL.read_text(encoding="utf-8").splitlines()[1:]:
        if linha.strip():
            mes, cpf, cnpj = linha.split(";")
            out.append(ValorCIP(mes, int(cpf), int(cnpj), "planilha"))
    return out


def gerar_planilha(valores: list[ValorCIP]) -> bytes:
    """Planilha no mesmo formato da que a Prefeitura usa: Mês | CPF (R$) | CNPJ (R$)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Valores CIP"
    fonte = "Arial"
    ws["A1"] = "C.I.P."
    ws["A1"].font = Font(name=fonte, bold=True, size=12)
    ws.merge_cells("A1:C1")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([None, "CPF", "CNPJ"])
    ws.append(["Mês", "(R$)", "(R$)"])
    for r in (2, 3):
        for c in ws[r]:
            c.font = Font(name=fonte, bold=True)
            c.alignment = Alignment(horizontal="center")
    for v in sorted(valores, key=lambda x: x.mes):
        ws.append([rotulo_mes(v.mes), v.cpf / 100, v.cnpj / 100])
        for c in ws[ws.max_row]:
            c.font = Font(name=fonte)
        ws.cell(ws.max_row, 2).number_format = "#,##0.00"
        ws.cell(ws.max_row, 3).number_format = "#,##0.00"
    for col, w in (("A", 10), ("B", 12), ("C", 12)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
