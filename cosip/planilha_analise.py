"""Excel da análise mensal: todos os registros do arquivo da Enel + os indicadores com fórmulas reais.

Aba "Indicadores": parâmetros (mês, cidade, valor da CIP de CPF e CNPJ), cada indicador com o
resultado da fórmula, o texto da fórmula (FÓRMULATEXTO), o número do sistema e "Confere?".
Aba "Dados": uma linha por registro, campos originais (A–S) e colunas de fórmula (T–AF).

Para caber e abrir rápido (~460 mil linhas), a aba Dados é gravada direto em XML com fórmulas
compartilhadas (uma fórmula por coluna, como "arrastar para baixo" no Excel). O Excel calcula
tudo ao abrir o arquivo.

A contagem de distintos (CPF/CNPJ sem repetir) usa a ordem da aba Dados: as linhas estão
classificadas por Entra nas contagens → Escopo → Tipo → Documento → Valor diferente, e a coluna
"1º do documento" marca a primeira linha de cada documento. Se a ordem mudar, use a coluna Ordem.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import formato as F
from .analise import MUNICIPIO, Resultado
from .documentos import CNPJ, CPF, classificar
from .leitor import LeituraCOSIP

# ------------------------------------------------------------------ colunas da aba Dados
ORIGINAIS = [  # (coluna, título, largura, tipo)  tipo: n = número, s = texto
    ("A", "Linha no TXT", 9, "n"), ("B", "Classe (pos. 2)", 8, "s"), ("C", "Unidade (3–12)", 12, "s"),
    ("D", "Nome (13–52)", 32, "s"), ("E", "Endereço (53–102)", 34, "s"), ("F", "Bairro (103–127)", 18, "s"),
    ("G", "Cidade (128–152)", 18, "s"), ("H", "CEP (153–160)", 10, "s"), ("I", "Referência (161–166)", 11, "s"),
    ("J", "Vencimento (167–174)", 11, "s"), ("K", "Crédito (175–182)", 11, "s"),
    ("L", "Documento da fatura (183–198)", 19, "s"), ("M", "CIP no arquivo, centavos (199–210)", 12, "n"),
    ("N", "Sinal (211)", 7, "s"), ("O", "Juros, centavos (212–223)", 10, "n"), ("P", "Multa, centavos (224–236)", 10, "n"),
    ("Q", "Código (238–239)", 8, "s"), ("R", "Código (240–241)", 8, "s"), ("S", "CPF/CNPJ (242–256)", 17, "s"),
]

_C11, _C14 = "RIGHT(S{n},11)", "RIGHT(S{n},14)"
_CPF_OK = ('IF(LEFT(S{n},4)<>"0000",0,IF(AND('
           f'MOD(MOD(SUMPRODUCT(--MID({_C11},{{1,2,3,4,5,6,7,8,9}},1),{{10,9,8,7,6,5,4,3,2}})*10,11),10)=--MID({_C11},10,1),'
           f'MOD(MOD(SUMPRODUCT(--MID({_C11},{{1,2,3,4,5,6,7,8,9,10}},1),{{11,10,9,8,7,6,5,4,3,2}})*10,11),10)=--MID({_C11},11,1),'
           f'{_C11}<>REPT(LEFT({_C11},1),11)),1,0))')
_P12, _P13 = "{5,4,3,2,9,8,7,6,5,4,3,2}", "{6,5,4,3,2,9,8,7,6,5,4,3,2}"
_I12, _I13 = "{1,2,3,4,5,6,7,8,9,10,11,12}", "{1,2,3,4,5,6,7,8,9,10,11,12,13}"
_R1 = f"MOD(SUMPRODUCT(--MID({_C14},{_I12},1),{_P12}),11)"
_R2 = f"MOD(SUMPRODUCT(--MID({_C14},{_I13},1),{_P13}),11)"
_CNPJ_OK = (f'IF(AND(IF({_R1}<2,0,11-{_R1})=--MID({_C14},13,1),'
            f'IF({_R2}<2,0,11-{_R2})=--MID({_C14},14,1),{_C14}<>REPT(LEFT({_C14},1),14)),1,0)')

FORMULAS = [  # (coluna, título, largura, fórmula da linha {n} — {p} = linha anterior)
    ("T", "CIP com sinal (centavos)", 10, 'IF(N{n}="-",-M{n},M{n})'),
    ("U", "Valor pago (R$)", 11, "(T{n}+O{n}+P{n})/100"),
    ("V", "CPF válido?", 7, _CPF_OK),
    ("W", "CNPJ válido?", 7, _CNPJ_OK),
    ("X", "Tipo", 8, 'IF(V{n}=1,"CPF",IF(W{n}=1,"CNPJ","SEM DOC"))'),
    ("Y", "Documento", 15, 'IF(X{n}="CPF",RIGHT(S{n},11),IF(X{n}="CNPJ",RIGHT(S{n},14),""))'),
    ("Z", "Santo André?", 8, "IF(TRIM(G{n})=Indicadores!$C$6,1,0)"),
    ("AA", "Escopo", 8, 'IF(I{n}=Indicadores!$C$5,"MÊS","OUTROS")'),
    ("AB", "Entra nas contagens?", 9, 'IF(AND(Z{n}=1,X{n}<>"SEM DOC",T{n}>0),1,0)'),
    ("AC", "Valor oficial (centavos)", 9, 'IF(X{n}="CPF",Indicadores!$C$7,IF(X{n}="CNPJ",Indicadores!$C$8,""))'),
    ("AD", "Valor diferente?", 9, 'IF(AND(AB{n}=1,AA{n}="MÊS",T{n}<>AC{n}),1,0)'),
    ("AE", "1º do documento", 9, "IF(AB{n}=0,0,IF(OR(AB{p}<>1,AA{n}&X{n}&Y{n}<>AA{p}&X{p}&Y{p}),1,0))"),
    ("AF", "1º com valor diferente", 9, "IF(AND(AD{n}=1,OR(AE{n}=1,AD{p}<>1)),1,0)"),
]
ORDEM = ("AG", "Ordem", 8)
ULTIMA_COLUNA = "AG"

EXPLICACAO_COLUNAS = {
    "T": "Aplica o sinal da posição 211: estorno fica negativo.",
    "U": "CIP + juros + multa, em reais.",
    "V": "1 se o campo começa com 0000 e os 2 dígitos verificadores do CPF (últimos 11) conferem.",
    "W": "1 se os 2 dígitos verificadores do CNPJ (últimos 14) conferem.",
    "X": "CPF se V = 1; senão CNPJ se W = 1; senão SEM DOC.",
    "Y": "Número do CPF (11 dígitos) ou do CNPJ (14).",
    "Z": "1 se a cidade é a dos parâmetros (Indicadores!C6).",
    "AA": "MÊS se a referência da fatura é o mês do arquivo (Indicadores!C5); senão OUTROS.",
    "AB": "1 = Santo André, com CPF/CNPJ e CIP maior que zero (CIP zero e estorno ficam fora).",
    "AC": "Valor da CIP do mês para o tipo (Indicadores!C7 e C8).",
    "AD": "1 = fatura do mês com CIP diferente do valor oficial.",
    "AE": "1 na primeira linha de cada documento dentro do escopo (conta distintos). Depende da ordem.",
    "AF": "1 na primeira linha com valor diferente de cada documento (distintos do indicador 2).",
    "AG": "Ordem da aba. Se reclassificar, volte por esta coluna (crescente).",
}

_INVALIDOS_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _txt(v: str) -> str:
    return escape(_INVALIDOS_XML.sub("", v))


# ------------------------------------------------------------------ leitura e ordenação
def _registros(caminho, r: Resultado) -> list[tuple]:
    regs = []
    for reg in LeituraCOSIP(caminho).registros():
        tipo, _ = classificar(reg.cpf_cnpj)
        numero = reg.cpf_cnpj[-11:] if tipo == CPF else reg.cpf_cnpj[-14:] if tipo == CNPJ else ""
        sa = reg.cidade == MUNICIPIO
        conta = sa and tipo in (CPF, CNPJ) and reg.cosip > 0
        do_mes = reg.mes_cobranca == r.mes_arquivo
        oficial = r.valor_cpf if tipo == CPF else r.valor_cnpj
        dif = conta and do_mes and reg.cosip != oficial
        chave = (0 if conta else 1, 0 if do_mes else 1, tipo, numero, 1 if dif else 0, reg.linha)
        regs.append((chave, reg))
    regs.sort(key=lambda x: x[0])
    return [reg for _, reg in regs]


# ------------------------------------------------------------------ aba Indicadores (openpyxl)
def _montar_indicadores(wb, r: Resultado, ultima: int):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.properties import Outline

    ws = wb.active
    ws.title = "Indicadores"
    fonte = "Arial"
    amarelo = PatternFill("solid", fgColor="FFF2CC")
    escuro = PatternFill("solid", fgColor="27313D")
    claro = PatternFill("solid", fgColor="EEF3FB")
    rng = lambda c: f"Dados!${c}$2:${c}${ultima}"  # noqa: E731
    mes = F.mes_extenso(r.mes_arquivo)

    ws["A1"] = f"CIP/COSIP Santo André – análise do arquivo de {mes}"
    ws["A1"].font = Font(name=fonte, bold=True, size=14)
    ws["A2"] = (f"Arquivo {r.nome_arquivo}. Coluna B = resultado da fórmula; coluna C = a fórmula usada; "
                "D = número mostrado pelo sistema; E = confere.")
    ws["A2"].font = Font(name=fonte, italic=True, color="5B6573")

    ws["A4"] = "Parâmetros (pode alterar as células amarelas)"
    ws["A4"].font = Font(name=fonte, bold=True, size=11)
    params = [("Mês do arquivo (AAAAMM)", r.mes_arquivo), ("Cidade", MUNICIPIO),
              ("Valor da CIP – CPF (centavos)", r.valor_cpf), ("Valor da CIP – CNPJ (centavos)", r.valor_cnpj)]
    for i, (rot, val) in enumerate(params, start=5):
        ws.cell(i, 2, rot).font = Font(name=fonte)
        c = ws.cell(i, 3, val)
        c.font = Font(name=fonte, color="0000FF", bold=True)
        c.fill = amarelo

    linha = 10
    for col, t in zip("ABCDE", ["Indicador", "Resultado", "Fórmula usada", "Sistema", "Confere?"]):
        c = ws[f"{col}{linha}"]
        c.value = t
        c.font = Font(name=fonte, bold=True, color="FFFFFF")
        c.fill = escuro
    ws.freeze_panes = "A11"

    def secao(titulo):
        nonlocal linha
        linha += 2
        c = ws.cell(linha, 1, titulo)
        c.font = Font(name=fonte, bold=True, size=11)

    def item(rot, formula, sistema, dinheiro=False, destaque=False, nivel=0):
        nonlocal linha
        linha += 1
        ws.cell(linha, 1, ("      " if nivel else "") + rot).font = Font(name=fonte, bold=destaque)
        b = ws.cell(linha, 2, formula)
        b.font = Font(name=fonte, bold=destaque)
        ws.cell(linha, 3, f"=_xlfn.FORMULATEXT(B{linha})").font = Font(name="Consolas", size=9, color="1F4E79")
        d = ws.cell(linha, 4, sistema)
        d.font = Font(name=fonte, color="5B6573")
        if sistema is not None:
            teste = f"ROUND(B{linha}-D{linha},2)=0" if dinheiro else f"B{linha}=D{linha}"
            ws.cell(linha, 5, f'=IF({teste},"OK","DIFERENTE")').font = Font(name=fonte, bold=True, color="1E7A3C")
        fmt = "#,##0.00" if dinheiro else "#,##0"
        b.number_format = fmt
        d.number_format = fmt
        if destaque:
            for c in range(1, 6):
                ws.cell(linha, c).fill = claro
        if nivel:
            ws.row_dimensions[linha].outlineLevel = 1
            ws.row_dimensions[linha].hidden = True
        return linha

    def contagens(titulo, escopo_reg, escopo_dist, ind):
        secao(titulo)
        lin = {}
        for tipo, c in ((CPF, ind.cpf if ind else None), (CNPJ, ind.cnpj if ind else None)):
            lin[("reg", tipo)] = item(f"{tipo} – registros", f"=COUNTIFS({escopo_reg},{rng('X')},\"{tipo}\")",
                                      c.registros if c else None)
        lin[("reg", "T")] = item("Total – registros", f"=B{lin[('reg', CPF)]}+B{lin[('reg', CNPJ)]}",
                                 ind.total.registros if ind else None, destaque=True)
        for tipo, c in ((CPF, ind.cpf if ind else None), (CNPJ, ind.cnpj if ind else None)):
            lin[("dist", tipo)] = item(f"{tipo} – distintos", f"=SUMIFS({escopo_dist},{rng('X')},\"{tipo}\")",
                                       c.documentos if c else None)
        item("Total – distintos", f"=B{lin[('dist', CPF)]}+B{lin[('dist', CNPJ)]}",
             ind.total.documentos if ind else None, destaque=True)

    contagens(f"1. Pagamentos de faturas de {mes}",
              f"{rng('AB')},1,{rng('AA')},\"MÊS\"", f"{rng('AE')},{rng('AA')},\"MÊS\"", r.ind1)
    contagens("2. Pagamentos com valor diferente da CIP do mês",
              f"{rng('AD')},1", f"{rng('AF')}", r.ind2)
    contagens("3. Pagamentos de faturas de outros meses",
              f"{rng('AB')},1,{rng('AA')},\"OUTROS\"", f"{rng('AE')},{rng('AA')},\"OUTROS\"", r.ind3)

    secao("4. Valor arrecadado no arquivo (R$)")
    l_mes = item(f"Faturas de {mes}", f"=SUMIFS({rng('U')},{rng('Z')},1,{rng('AA')},\"MÊS\")",
                 r.valor_mes.valor / 100, dinheiro=True)
    l_out = item("Faturas de outros meses (clique no + à esquerda para abrir)",
                 f"=SUMIFS({rng('U')},{rng('Z')},1,{rng('AA')},\"OUTROS\")", r.valor_outros / 100, dinheiro=True)
    for m in r.outros_meses:
        item(F.mes_extenso(m.mes), f"=SUMIFS({rng('U')},{rng('Z')},1,{rng('I')},\"{m.mes}\")", m.valor / 100,
             dinheiro=True, nivel=1)
    item("Total recebido no arquivo", f"=B{l_mes}+B{l_out}", r.valor_total / 100, dinheiro=True, destaque=True)

    secao("Fora das contagens")
    f = r.fora
    item("Registros de outros municípios", f"=COUNTIFS({rng('Z')},0)", f.outros_municipios)
    item("Santo André sem CPF/CNPJ válido", f"=COUNTIFS({rng('Z')},1,{rng('X')},\"SEM DOC\")",
         f.sem_documento_mes + f.sem_documento_outros)
    item("Santo André com CIP zero ou estorno", f"=COUNTIFS({rng('Z')},1,{rng('X')},\"<>SEM DOC\",{rng('T')},\"<=0\")",
         f.zero_estorno_mes + f.zero_estorno_outros)
    secao("Conferência")
    item("Registros no arquivo", f"=COUNT({rng('A')})", r.qtd_registros_arquivo)

    linha += 2
    ws.cell(linha, 1, "Como ler a aba Dados").font = Font(name=fonte, bold=True, size=11)
    notas = ["Colunas A a S: campos do arquivo da Enel como vieram (posições no título). Valores em centavos.",
             "Colunas T a AF: fórmulas (cabeçalho cinza). Cada uma é a mesma fórmula em todas as linhas."]
    notas += [f"{col} – {txt}" for col, txt in EXPLICACAO_COLUNAS.items()]
    notas += ["Distintos: a aba Dados está classificada por Entra nas contagens → Escopo → Tipo → Documento → "
              "Valor diferente; a primeira linha de cada documento tem 1 na coluna AE. Se mudar a ordem, "
              "classifique de novo pela coluna AG (Ordem), crescente.",
              "Se alterar os parâmetros (células amarelas), os registros e valores se recalculam; os distintos do "
              "indicador 2 só ficam exatos na ordem original."]
    for t in notas:
        linha += 1
        ws.cell(linha, 1, t).font = Font(name=fonte, size=9, color="5B6573")

    ws.sheet_properties.outlinePr = Outline(summaryBelow=False)
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 90
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 11
    for row in ws.iter_rows(min_row=11, max_col=5):
        for c in row:
            if c.column in (2, 4):
                c.alignment = Alignment(horizontal="right")


def _montar_cabecalho_dados(wb, ultima: int):
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    ws = wb.create_sheet("Dados")
    cab = PatternFill("solid", fgColor="27313D")
    form = PatternFill("solid", fgColor="5B6573")
    colunas = [(c, t, w) for c, t, w, _ in ORIGINAIS] + [(c, t, w) for c, t, w, _ in FORMULAS] + [ORDEM]
    for col, titulo, larg in colunas:
        c = ws[f"{col}1"]
        c.value = titulo
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=9)
        c.fill = form if col in EXPLICACAO_COLUNAS and col != "AG" else cab
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if col in EXPLICACAO_COLUNAS:
            c.comment = Comment(EXPLICACAO_COLUNAS[col], "Sistema CIP/COSIP")
        ws.column_dimensions[col].width = larg
    ws.row_dimensions[1].height = 48
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{ULTIMA_COLUNA}{ultima}"
    ws["U2"] = 0
    ws["U2"].number_format = "#,##0.00"     # registra o estilo de moeda para as linhas de dados


# ------------------------------------------------------------------ aba Dados (XML direto)
def _linhas_xml(regs, estilo_moeda: str):
    ultima = len(regs) + 1
    for i, reg in enumerate(regs):
        n = i + 2
        valores = [
            ("A", reg.linha), ("B", reg.classe), ("C", reg.unidade), ("D", reg.nome), ("E", reg.endereco),
            ("F", reg.bairro), ("G", reg.cidade), ("H", reg.cep), ("I", reg.mes_cobranca), ("J", reg.vencimento),
            ("K", reg.data_credito), ("L", reg.documento), ("M", abs(reg.cosip)),
            ("N", "-" if reg.cosip < 0 else None), ("O", reg.juros), ("P", reg.multa), ("Q", reg.codigo_a),
            ("R", reg.codigo_b), ("S", reg.cpf_cnpj),
        ]
        partes = [f'<row r="{n}">']
        for col, v in valores:
            if v is None:
                continue
            if isinstance(v, int):
                partes.append(f'<c r="{col}{n}"><v>{v}</v></c>')
            else:
                partes.append(f'<c r="{col}{n}" t="inlineStr"><is><t>{_txt(v)}</t></is></c>')
        for si, (col, _, _, f) in enumerate(FORMULAS):
            s = f' s="{estilo_moeda}"' if col == "U" else ""
            if n == 2:
                txt = escape(f.replace("{n}", str(n)).replace("{p}", str(n - 1)))
                partes.append(f'<c r="{col}{n}"{s}><f t="shared" ref="{col}2:{col}{ultima}" si="{si}">{txt}</f></c>')
            else:
                partes.append(f'<c r="{col}{n}"{s}><f t="shared" si="{si}"/></c>')
        partes.append(f'<c r="AG{n}"><v>{i + 1}</v></c></row>')
        yield "".join(partes)


def gerar(caminho_txt: str | Path, r: Resultado, destino: str | Path) -> Path:
    """Gera o Excel em `destino` (escrita atômica). Devolve o caminho."""
    from openpyxl import Workbook

    destino = Path(destino)
    regs = _registros(caminho_txt, r)
    ultima = len(regs) + 1

    wb = Workbook()
    _montar_indicadores(wb, r, ultima)
    _montar_cabecalho_dados(wb, ultima)
    wb.calculation.fullCalcOnLoad = True    # sem valores guardados: o Excel calcula ao abrir

    pasta = destino.parent
    pasta.mkdir(parents=True, exist_ok=True)
    fd, base = tempfile.mkstemp(suffix=".xlsx", dir=pasta)
    os.close(fd)
    fd, final = tempfile.mkstemp(suffix=".xlsx", dir=pasta)
    os.close(fd)
    try:
        wb.save(base)
        with zipfile.ZipFile(base) as zin:
            nome_dados = _arquivo_da_aba(zin, "Dados")
            xml = zin.read(nome_dados).decode("utf-8")
            m = re.search(r'<c r="U2" s="(\d+)"', xml)
            estilo_moeda = m.group(1) if m else "0"
            inicio, resto = xml.split("<sheetData>", 1)
            linha1 = re.search(r'<row r="1".*?</row>', resto, re.S).group(0)
            fim = resto.split("</sheetData>", 1)[1]
            inicio = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:{ULTIMA_COLUNA}{ultima}"/>', inicio)
            with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
                for item in zin.infolist():
                    if item.filename == nome_dados:
                        with zout.open(nome_dados, "w", force_zip64=True) as w:
                            w.write(inicio.encode("utf-8"))
                            w.write(("<sheetData>" + linha1).encode("utf-8"))
                            buf = []
                            for lx in _linhas_xml(regs, estilo_moeda):
                                buf.append(lx)
                                if len(buf) >= 2000:
                                    w.write("".join(buf).encode("utf-8"))
                                    buf = []
                            w.write("".join(buf).encode("utf-8"))
                            w.write(("</sheetData>" + fim).encode("utf-8"))
                    else:
                        zout.writestr(item, zin.read(item.filename))
        shutil.move(final, destino)
    finally:
        Path(base).unlink(missing_ok=True)
        Path(final).unlink(missing_ok=True)
    return destino


def _arquivo_da_aba(z: zipfile.ZipFile, nome: str) -> str:
    wbxml = z.read("xl/workbook.xml").decode("utf-8")
    rid = re.search(rf'<sheet [^>]*name="{nome}"[^>]*r:id="([^"]+)"', wbxml)
    if not rid:
        rid = re.search(rf'<sheet [^>]*r:id="([^"]+)"[^>]*name="{nome}"', wbxml)
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    alvo = re.search(rf'<Relationship [^>]*Id="{rid.group(1)}"[^>]*Target="([^"]+)"', rels)
    if not alvo:
        alvo = re.search(rf'<Relationship [^>]*Target="([^"]+)"[^>]*Id="{rid.group(1)}"', rels)
    t = alvo.group(1).lstrip("/")
    return t if t.startswith("xl/") else "xl/" + t
