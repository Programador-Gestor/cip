from cosip.documentos import CNPJ, CPF, INVALIDO, SEM_DOCUMENTO, classificar


def test_cpf_valido():
    assert classificar("000054974746839") == (CPF, False)


def test_cnpj_valido():
    # Município de Santo André: 46.522.942/0001-30
    assert classificar("046522942000130") == (CNPJ, False)


def test_sem_documento():
    assert classificar("000000000000000") == (SEM_DOCUMENTO, False)


def test_invalido():
    assert classificar("000012345678900") == (INVALIDO, False)


def test_ambiguo_conta_como_cpf():
    # passa como CPF (001.000.000-37) e como CNPJ (00.000.010/0000-37);
    # no arquivo de agosto há 15.823 casos assim, todos de pessoa física
    assert classificar("000000100000037") == (CPF, True)
