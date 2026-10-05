"""API pública: a única porta de entrada para a lógica de triagem (CLI e web usam esta)."""

from datetime import date
from functools import lru_cache
from typing import Any, Literal

from pydantic import ValidationError

from . import config as cfg
from .classificador import classificar
from .entrada import descrever_erro
from .erros import ErroEntrada, FalhaTriagem
from .modelos import Chamado, ErroTriagem, ResultadoTriagem


class Triador:
    """Reutilizável e thread-safe. Crie um por processo (o servidor cria um no startup)."""

    def __init__(
        self,
        modelo: str | None = None,
        *,
        cliente: Any | None = None,
        max_retries: int = 2,
    ) -> None:
        # Com cliente injetado (testes, simulado) a chave não é exigida.
        self._config = cfg.carregar(modelo, exigir_chave=cliente is None, max_retries=max_retries)
        self._cliente = cliente if cliente is not None else cfg.criar_cliente(self._config)

    @property
    def modelo(self) -> str:
        return self._config.modelo

    def triar(
        self,
        texto: str,
        *,
        data_referencia: date | None = None,
        assunto: str | None = None,
        canal: Literal["email", "ticket"] | None = None,
        id: str | None = None,
    ) -> ResultadoTriagem:
        """Um chamado. Sucesso → ResultadoTriagem. Falha → levanta FalhaTriagem."""
        try:
            chamado = Chamado(
                texto=texto, data_referencia=data_referencia, assunto=assunto, canal=canal, id=id
            )
        except ValidationError as erro:
            raise ErroEntrada(descrever_erro(erro)) from None

        resultado = self.triar_chamado(chamado)
        if isinstance(resultado, ErroTriagem):
            erro = resultado.erro
            raise FalhaTriagem(erro.mensagem, tipo=erro.tipo, retry_after=erro.retry_after)
        return resultado

    def triar_chamado(self, chamado: Chamado) -> ResultadoTriagem | ErroTriagem:
        """Para lotes (CLI classificar/avaliar): NÃO levanta por item; devolve ErroTriagem."""
        resultado = classificar(chamado, self._cliente, self._config)
        if isinstance(resultado, ResultadoTriagem) and chamado.data_referencia:
            meta = resultado.meta.model_copy(
                update={"data_referencia": chamado.data_referencia.isoformat()}
            )
            resultado = resultado.model_copy(update={"meta": meta})
        return resultado


@lru_cache(maxsize=1)
def _triador_padrao() -> Triador:
    return Triador()


def triar(texto: str, data_referencia: date | None = None, **opcoes: Any) -> ResultadoTriagem:
    """Atalho: usa um Triador padrão criado sob demanda e chama .triar()."""
    return _triador_padrao().triar(texto, data_referencia=data_referencia, **opcoes)
