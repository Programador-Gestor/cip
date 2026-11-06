"""Classificação do campo CPF/CNPJ (15 dígitos, completado com zeros à esquerda)."""

from functools import lru_cache

CPF = "CPF"
CNPJ = "CNPJ"
SEM_DOCUMENTO = "SEM_DOCUMENTO"
INVALIDO = "INVALIDO"


def cpf_valido(numero: str) -> bool:
    if len(numero) != 11 or not numero.isdigit() or len(set(numero)) == 1:
        return False
    d = [int(c) for c in numero]
    v1 = (sum(d[i] * (10 - i) for i in range(9)) * 10) % 11 % 10
    v2 = (sum(d[i] * (11 - i) for i in range(10)) * 10) % 11 % 10
    return v1 == d[9] and v2 == d[10]


def cnpj_valido(numero: str) -> bool:
    if len(numero) != 14 or not numero.isdigit() or len(set(numero)) == 1:
        return False
    d = [int(c) for c in numero]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    r = sum(a * b for a, b in zip(d[:12], pesos1)) % 11
    v1 = 0 if r < 2 else 11 - r
    r = sum(a * b for a, b in zip(d[:13], pesos2)) % 11
    v2 = 0 if r < 2 else 11 - r
    return v1 == d[12] and v2 == d[13]


@lru_cache(maxsize=1_000_000)
def classificar(campo15: str) -> tuple[str, bool]:
    """Devolve (tipo, ambiguo).

    Regra (validada no arquivo de agosto/2026):
    - todos os dígitos zero            -> SEM_DOCUMENTO
    - 4 zeros à esquerda e CPF válido  -> CPF  (ambiguo=True se também for CNPJ válido)
    - CNPJ válido                      -> CNPJ
    - nenhum dos dois                  -> INVALIDO
    """
    if campo15.strip("0") == "":
        return SEM_DOCUMENTO, False
    eh_cpf = campo15[:4] == "0000" and cpf_valido(campo15[-11:])
    eh_cnpj = cnpj_valido(campo15[-14:])
    if eh_cpf:
        return CPF, eh_cnpj
    if eh_cnpj:
        return CNPJ, False
    return INVALIDO, False
