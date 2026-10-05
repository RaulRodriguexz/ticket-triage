"""Configuração: modelo, chave, preços e timeouts, lidos do ambiente."""

import os
from dataclasses import dataclass, field

from .erros import ErroConfiguracao  # reexportado: mantém `config.ErroConfiguracao`

MODELO_PADRAO = "claude-sonnet-5-5"
MODELO_HAIKU = "claude-haiku-4-5-20251001"

# Modelos aceitos em --modelo / TRIAGEM_MODELO.
MODELOS_SUPORTADOS = (MODELO_PADRAO, MODELO_HAIKU)

# US$ por 1M de tokens: (entrada, saída). Inclui os modelos para onde o
# fallback de recusa pode desviar, para o custo usar o preço de quem respondeu.
PRECOS: dict[str, tuple[float, float]] = {
    MODELO_PADRAO: (2.00, 10.00),
    MODELO_HAIKU: (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
}


@dataclass(frozen=True)
class Config:
    modelo: str
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = 30
    max_retries: int = 2

    def __repr__(self) -> str:
        chave = "***" if self.api_key else None
        return (
            f"Config(modelo={self.modelo!r}, api_key={chave!r}, "
            f"timeout_s={self.timeout_s}, max_retries={self.max_retries})"
        )

    @property
    def eh_haiku(self) -> bool:
        return self.modelo == MODELO_HAIKU


def carregar(
    modelo: str | None = None, exigir_chave: bool = True, max_retries: int = 2
) -> Config:
    """Prioridade do modelo: argumento > TRIAGEM_MODELO > padrão."""
    modelo = modelo or os.environ.get("TRIAGEM_MODELO") or MODELO_PADRAO
    if modelo not in MODELOS_SUPORTADOS:
        validos = ", ".join(MODELOS_SUPORTADOS)
        raise ErroConfiguracao(f"Modelo desconhecido: {modelo}. Use um de: {validos}")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    if exigir_chave and not api_key:
        raise ErroConfiguracao("Defina ANTHROPIC_API_KEY")
    return Config(modelo=modelo, api_key=api_key, max_retries=max_retries)


def custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int) -> float | None:
    """Custo pelo preço do modelo que respondeu; None se o preço for desconhecido."""
    if modelo not in PRECOS:
        return None
    preco_in, preco_out = PRECOS[modelo]
    return (tokens_entrada * preco_in + tokens_saida * preco_out) / 1_000_000


def criar_cliente(config: Config):
    """Cliente real. A chave é passada explicitamente (RNF-02)."""
    import anthropic

    return anthropic.Anthropic(
        api_key=config.api_key,
        timeout=config.timeout_s,
        max_retries=config.max_retries,
    )
