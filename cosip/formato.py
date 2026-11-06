"""Formatação pt-BR."""

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def reais(centavos: int) -> str:
    s = f"{abs(centavos) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-R$ " if centavos < 0 else "R$ ") + s


def inteiro(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def mes_curto(aaaamm: str) -> str:
    return f"{aaaamm[4:]}/{aaaamm[:4]}"


def mes_extenso(aaaamm: str) -> str:
    return f"{MESES[int(aaaamm[4:]) - 1]}/{aaaamm[:4]}"


def data(aaaammdd: str) -> str:
    if len(aaaammdd) != 8:
        return aaaammdd
    return f"{aaaammdd[6:]}/{aaaammdd[4:6]}/{aaaammdd[:4]}"
