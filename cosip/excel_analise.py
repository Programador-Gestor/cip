"""Excel da análise (todos os registros + fórmulas) gerado sob demanda, sem guardar nada.

O administrador seleciona de novo o arquivo da Enel do mês; o sistema confere pelo
SHA-256 que é o mesmo arquivo daquele resultado, gera o Excel numa pasta temporária,
entrega para download e apaga tudo. Nem o arquivo da Enel nem o Excel ficam no
servidor ou no banco.

Um Excel por vez (gera ~85 MB e usa ~700 MB de memória para 460 mil linhas): se
outra geração estiver em andamento, o pedido é recusado com aviso para tentar de novo.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .planilha_analise import gerar

try:
    import fcntl          # Linux (servidor): trava entre os workers do gunicorn
except ImportError:       # Windows (desenvolvimento): trava só dentro do processo
    fcntl = None

_trava_local = threading.Lock()


class Ocupado(Exception):
    """Outra geração do Excel da análise está em andamento."""


def pasta_tmp() -> str | None:
    p = os.environ.get("CIP_TMP")
    if p:
        Path(p).mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _uma_por_vez():
    if not _trava_local.acquire(blocking=False):
        raise Ocupado
    try:
        if fcntl is None:
            yield
            return
        caminho = Path(pasta_tmp() or tempfile.gettempdir()) / "cip_excel_analise.lock"
        with open(caminho, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Ocupado from None
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        _trava_local.release()


def gerar_e_abrir(caminho_txt: str | Path, resultado):
    """Gera o Excel e devolve o arquivo ABERTO para leitura, já apagado do disco.

    No Linux, um arquivo apagado continua legível por quem já o abriu; o espaço é
    liberado quando o download termina e o arquivo é fechado. Assim não sobra nada
    no disco nem se o download for interrompido.
    """
    with _uma_por_vez():
        fd, destino = tempfile.mkstemp(prefix="cip_analise_", suffix=".xlsx", dir=pasta_tmp())
        os.close(fd)
        try:
            gerar(caminho_txt, resultado, destino)
            if os.name == "nt":     # Windows (desenvolvimento) não apaga arquivo aberto
                with open(destino, "rb") as f:
                    return io.BytesIO(f.read())
            return open(destino, "rb")
        finally:
            Path(destino).unlink(missing_ok=True)
