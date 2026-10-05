"""Exceção única da API pública: quem consome trata FalhaTriagem e decide pelo `tipo`."""

from .modelos import DetalheErro, TipoErro


class FalhaTriagem(Exception):
    tipo: TipoErro = "interno"

    def __init__(
        self, mensagem: str, tipo: TipoErro | None = None, *, retry_after: str | None = None
    ):
        super().__init__(mensagem)
        self.mensagem = mensagem
        # Segundos sugeridos pela API antes de tentar de novo (só em limite_taxa).
        self.retry_after = retry_after
        if tipo is not None:
            self.tipo = tipo

    def detalhe(self) -> DetalheErro:
        return DetalheErro(tipo=self.tipo, mensagem=self.mensagem, retry_after=self.retry_after)


class ErroConfiguracao(FalhaTriagem):
    """Modelo ou chave inválidos."""

    tipo: TipoErro = "configuracao"


class ErroEntrada(FalhaTriagem):
    """Chamado inválido."""

    tipo: TipoErro = "entrada"
