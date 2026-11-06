"""Leiaute do arquivo mensal da CIP/COSIP enviado pela Enel (B_A_SAN_COSIP.txt).

Leiaute reconstruído a partir do arquivo de agosto/2026 e conferido com o rodapé
gravado pela Enel e com a planilha de formação da CIP da Prefeitura.
Não existe leiaute oficial publicado: se a Enel mudar o formato, o leitor
recusa o arquivo em vez de produzir números errados.

Todas as posições abaixo são 1-based e inclusivas, como num editor de texto.
"""

CODIFICACAO = "latin-1"  # ISO-8859-1: um byte por caractere, posições fixas

TAMANHO_DETALHE = 279

# --- Cabeçalho (1ª linha) ---------------------------------------------------
CAB_TIPO = (1, 2)          # "HA"
CAB_MES_MOVIMENTO = (3, 8)  # AAAAMM — mês em que os pagamentos foram creditados
CAB_DATA_GERACAO = (9, 16)  # AAAAMMDD — provável data de geração do arquivo

# --- Rodapé (última linha) --------------------------------------------------
ROD_TIPO = (1, 2)            # "TA"
ROD_QTD_REGISTROS = (3, 11)  # quantidade de linhas de detalhe
ROD_TOTAL_COSIP = (12, 23)   # soma líquida (com sinal) da COSIP, em centavos
ROD_TOTAL_JUROS = (24, 36)   # soma dos juros, em centavos
ROD_TOTAL_MULTA = (37, 49)   # soma da multa, em centavos

# --- Detalhe (demais linhas) ------------------------------------------------
DET_TIPO = (1, 1)            # "D"
DET_CLASSE = (2, 2)          # 1 residencial, 2 comercial, 3 industrial, 5 poder público,
                             # 6 iluminação pública, 7 serviço público, 9 consumo próprio
DET_UNIDADE = (3, 12)        # código da unidade consumidora (imóvel)
DET_NOME = (13, 52)
DET_ENDERECO = (53, 102)
DET_BAIRRO = (103, 127)
DET_CIDADE = (128, 152)
DET_CEP = (153, 160)
DET_MES_COBRANCA = (161, 166)  # AAAAMM — mês de cobrança da CIP (mês da fatura)
DET_VENCIMENTO = (167, 174)    # AAAAMMDD
DET_DATA_CREDITO = (175, 182)  # AAAAMMDD — crédito/baixa da arrecadação
DET_DOCUMENTO = (183, 198)     # B-AAMM-nnnnnnnnn (fatura) ou ****... (ajuste/estorno)
DET_VALOR_COSIP = (199, 210)   # centavos, sem sinal
DET_SINAL = (211, 211)         # " " positivo, "-" negativo
DET_JUROS = (212, 223)         # centavos
DET_MULTA = (224, 236)         # centavos
DET_CODIGO_A = (238, 239)      # código de significado desconhecido
DET_CODIGO_B = (240, 241)      # código de significado desconhecido
DET_CPF_CNPJ = (242, 256)      # 15 dígitos, completado com zeros à esquerda

CLASSES = {
    "1": "Residencial",
    "2": "Comercial",
    "3": "Industrial",
    "5": "Poder público",
    "6": "Iluminação pública",
    "7": "Serviço público",
    "9": "Consumo próprio",
}
CLASSE_RESIDENCIAL = "1"


def campo(linha: str, posicao: tuple[int, int]) -> str:
    """Recorta um campo pela posição 1-based inclusiva."""
    inicio, fim = posicao
    return linha[inicio - 1:fim]
