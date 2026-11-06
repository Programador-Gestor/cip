"""Relatório dos 4 indicadores: PDF, Word (.docx) e Excel (.xlsx)."""

from __future__ import annotations

import io

from . import formato as F
from .analise import Indicador, Resultado


# ------------------------------------------------------------------ conteúdo comum
def titulo(r: Resultado) -> str:
    return f"CIP/COSIP Santo André – arquivo de {F.mes_extenso(r.mes_arquivo)}"


def nome_saida(r: Resultado, ext: str) -> str:
    return f"COSIP_{r.mes_arquivo}_indicadores.{ext}"


CAB_CONTAGEM = ["", "CPF", "CNPJ", "Total"]


def linhas_contagem(i: Indicador) -> list[list]:
    """[['Registros', cpf, cnpj, total], ['Distintos', ...]] — números inteiros.
    Registros = cada pagamento; Distintos = cada CPF/CNPJ contado uma vez."""
    t = i.total
    return [["Registros", i.cpf.registros, i.cnpj.registros, t.registros],
            ["Distintos", i.cpf.documentos, i.cnpj.documentos, t.documentos]]


def indicadores(r: Resultado) -> list[dict]:
    mes = F.mes_extenso(r.mes_arquivo)
    out = [{"n": 1, "titulo": f"Pagamentos de faturas de {mes}", "sub": "", "linhas": linhas_contagem(r.ind1)}]
    if r.ind2:
        sub = f"Valor da CIP de {mes}: CPF {F.reais(r.valor_cpf)} · CNPJ {F.reais(r.valor_cnpj)}"
        out.append({"n": 2, "titulo": "Pagamentos com valor diferente da CIP do mês", "sub": sub,
                    "linhas": linhas_contagem(r.ind2)})
    else:
        out.append({"n": 2, "titulo": "Pagamentos com valor diferente da CIP do mês",
                    "sub": "Aguardando confirmação do valor da CIP do mês.", "linhas": []})
    out.append({"n": 3, "titulo": "Pagamentos de faturas de outros meses", "sub": "", "linhas": linhas_contagem(r.ind3)})
    return out


def linhas_valor(r: Resultado) -> list[tuple[str, int, str]]:
    """Indicador 4: (rótulo, centavos, estilo) — estilo: mes | outros | detalhe | total."""
    out = [(f"Faturas de {F.mes_extenso(r.mes_arquivo)}", r.valor_mes.valor, "mes"),
           ("Faturas de outros meses", r.valor_outros, "outros")]
    out += [(F.mes_extenso(m.mes), m.valor, "detalhe") for m in r.outros_meses]
    out.append(("Total recebido no arquivo", r.valor_total, "total"))
    return out


def fora_das_contagens(r: Resultado) -> list[str]:
    f = r.fora
    out = []
    if f.outros_municipios:
        out.append(f"{F.inteiro(f.outros_municipios)} registros de outros municípios "
                   f"({F.reais(f.outros_municipios_valor)}): fora de todos os indicadores.")
    sd = f.sem_documento_mes + f.sem_documento_outros
    if sd:
        out.append(f"{F.inteiro(sd)} registros sem CPF/CNPJ válido ({F.inteiro(f.sem_documento_mes)} do mês, "
                   f"{F.inteiro(f.sem_documento_outros)} de outros meses): fora das quantidades, dentro do valor.")
    ze = f.zero_estorno_mes + f.zero_estorno_outros
    if ze:
        out.append(f"{F.inteiro(ze)} registros com CIP zero ou estorno ({F.inteiro(f.zero_estorno_mes)} do mês, "
                   f"{F.inteiro(f.zero_estorno_outros)} de outros meses): fora das quantidades, dentro do valor.")
    out += r.avisos
    return out


CONFERENCIA = ("Conferido: quantidades e valores fecham entre si e com o rodapé do arquivo da Enel. "
               "Valor = CIP + juros + multa, com estornos descontados.")


# ------------------------------------------------------------------ PDF
def gerar_pdf(r: Resultado, processado_em: str = "") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=titulo(r))
    est = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=est["Heading1"], fontSize=15, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=est["Heading2"], fontSize=11.5, spaceBefore=10, spaceAfter=3)
    sub = ParagraphStyle("sub", parent=est["Normal"], fontSize=8.5, textColor=colors.HexColor("#5b6573"))
    corpo = ParagraphStyle("c", parent=est["Normal"], fontSize=8.5, leading=11)
    escuro = colors.HexColor("#27313d")

    def tabela(dados, larguras, destaque=()):
        t = Table(dados, colWidths=larguras)
        s = [("FONT", (0, 0), (-1, -1), "Helvetica", 9),
             ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
             ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), escuro),
             ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#dfe3e8")),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for i in destaque:
            s += [("FONT", (0, i), (-1, i), "Helvetica-Bold", 9),
                  ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#eef3fb"))]
        t.setStyle(TableStyle(s))
        return t

    hist = [Paragraph(titulo(r), h1),
            Paragraph(f"Arquivo {r.nome_arquivo}" + (f" · processado em {processado_em[:10]}" if processado_em else ""), sub)]
    for ind in indicadores(r):
        hist.append(Paragraph(f"{ind['n']}. {ind['titulo']}", h2))
        if ind["sub"]:
            hist.append(Paragraph(ind["sub"], sub))
            hist.append(Spacer(1, 3))
        if ind["linhas"]:
            dados = [CAB_CONTAGEM] + [[l[0]] + [F.inteiro(x) for x in l[1:]] for l in ind["linhas"]]
            hist.append(tabela(dados, [62 * mm, 32 * mm, 32 * mm, 32 * mm]))
    hist.append(Paragraph("4. Valor arrecadado no arquivo", h2))
    dados = [["Referência da fatura", "Valor"]]
    destaque = []
    for rot, v, estilo in linhas_valor(r):
        dados.append([("      " if estilo == "detalhe" else "") + rot, F.reais(v)])
        if estilo in ("mes", "outros", "total"):
            destaque.append(len(dados) - 1)
    hist.append(tabela(dados, [110 * mm, 48 * mm], destaque))
    hist.append(Spacer(1, 8))
    for t in fora_das_contagens(r) + [CONFERENCIA]:
        hist.append(Paragraph(t, corpo))
    doc.build(hist)
    return buf.getvalue()


# ------------------------------------------------------------------ Word
def gerar_docx(r: Resultado, processado_em: str = "") -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name = "Arial"
    st.font.size = Pt(10)
    d.add_heading(titulo(r), level=1)
    p = d.add_paragraph(f"Arquivo {r.nome_arquivo}" + (f" · processado em {processado_em[:10]}" if processado_em else ""))
    p.runs[0].font.color.rgb = RGBColor(0x5B, 0x65, 0x73)

    def tabela(cab, linhas, negrito=()):
        t = d.add_table(rows=1, cols=len(cab))
        t.style = "Light Grid Accent 1"
        for i, c in enumerate(cab):
            t.rows[0].cells[i].text = c
        for n, l in enumerate(linhas):
            cells = t.add_row().cells
            for i, v in enumerate(l):
                cells[i].text = v
                par = cells[i].paragraphs[0]
                if i > 0:
                    par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                if n in negrito:
                    for run in par.runs:
                        run.bold = True
        return t

    for ind in indicadores(r):
        d.add_heading(f"{ind['n']}. {ind['titulo']}", level=2)
        if ind["sub"]:
            d.add_paragraph(ind["sub"])
        if ind["linhas"]:
            tabela(CAB_CONTAGEM, [[l[0]] + [F.inteiro(x) for x in l[1:]] for l in ind["linhas"]])
    d.add_heading("4. Valor arrecadado no arquivo", level=2)
    linhas, negrito = [], []
    for rot, v, estilo in linhas_valor(r):
        linhas.append([("    " if estilo == "detalhe" else "") + rot, F.reais(v)])
        if estilo != "detalhe":
            negrito.append(len(linhas) - 1)
    tabela(["Referência da fatura", "Valor"], linhas, negrito)
    d.add_paragraph()
    for t in fora_das_contagens(r) + [CONFERENCIA]:
        par = d.add_paragraph(t)
        par.runs[0].font.size = Pt(8.5)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ Excel
def gerar_xlsx(r: Resultado, processado_em: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Indicadores"
    fonte = "Arial"
    cab_fill = PatternFill("solid", fgColor="27313D")
    dest_fill = PatternFill("solid", fgColor="EEF3FB")

    def linha(valores, bold=False, cab=False, fill=None, fmt=None):
        ws.append(valores)
        for i, c in enumerate(ws[ws.max_row]):
            c.font = Font(name=fonte, bold=bold or cab, color="FFFFFF" if cab else "000000")
            if cab:
                c.fill = cab_fill
            elif fill:
                c.fill = fill
            if i > 0 and fmt:
                c.number_format = fmt
            if i > 0:
                c.alignment = Alignment(horizontal="right")

    ws.append([titulo(r)])
    ws["A1"].font = Font(name=fonte, bold=True, size=13)
    ws.append([f"Arquivo {r.nome_arquivo}" + (f" · processado em {processado_em[:10]}" if processado_em else "")])
    ws["A2"].font = Font(name=fonte, italic=True, color="5B6573")
    for ind in indicadores(r):
        ws.append([])
        ws.append([f"{ind['n']}. {ind['titulo']}"])
        ws.cell(ws.max_row, 1).font = Font(name=fonte, bold=True, size=11)
        if ind["sub"]:
            ws.append([ind["sub"]])
            ws.cell(ws.max_row, 1).font = Font(name=fonte, color="5B6573")
        if ind["linhas"]:
            linha(CAB_CONTAGEM, cab=True)
            primeira = ws.max_row + 1
            for l in ind["linhas"]:
                n = ws.max_row + 1
                linha([l[0], l[1], l[2], f"=B{n}+C{n}"], fmt="#,##0")
            assert ws.max_row == primeira + 1
    ws.append([])
    ws.append(["4. Valor arrecadado no arquivo"])
    ws.cell(ws.max_row, 1).font = Font(name=fonte, bold=True, size=11)
    linha(["Referência da fatura", "Valor (R$)"], cab=True)
    lin_mes = ws.max_row + 1
    linha([f"Faturas de {F.mes_extenso(r.mes_arquivo)}", r.valor_mes.valor / 100], bold=True, fill=dest_fill, fmt="#,##0.00")
    lin_outros = ws.max_row + 1
    ini, fim = lin_outros + 1, lin_outros + len(r.outros_meses)
    linha(["Faturas de outros meses", f"=SUM(B{ini}:B{fim})" if r.outros_meses else 0], bold=True, fill=dest_fill,
          fmt="#,##0.00")
    for m in r.outros_meses:
        linha(["    " + F.mes_extenso(m.mes), m.valor / 100], fmt="#,##0.00")
        ws.row_dimensions[ws.max_row].outlineLevel = 1
    linha(["Total recebido no arquivo", f"=B{lin_mes}+B{lin_outros}"], bold=True, fill=dest_fill, fmt="#,##0.00")
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.append([])
    for t in fora_das_contagens(r) + [CONFERENCIA]:
        ws.append([t])
        ws.cell(ws.max_row, 1).font = Font(name=fonte, size=9, color="5B6573")
    ws.column_dimensions["A"].width = 44
    for c in "BCD":
        ws.column_dimensions[c].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
