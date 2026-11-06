"""Sistema COSIP — leitura e análise do arquivo mensal da CIP/COSIP de Santo André."""

from .leitor import ArquivoInvalido, LeituraCOSIP, Registro, validar_arquivo

__all__ = ["ArquivoInvalido", "LeituraCOSIP", "Registro", "validar_arquivo"]
