"""Roteador de modelos: Haiku por padrão, Sonnet nos casos difíceis.

Funções puras: não fazem I/O, não leem o ambiente (exceto `do_ambiente`) nem o relógio.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .config import MODELO_HAIKU, MODELO_SONNET
from .erros import ErroConfiguracao
from .modelos import Chamado, Confianca, ErroTriagem, Prioridade, ResultadoTriagem

MotivoEscalonamento = Literal["confianca_baixa", "prioridade", "falha_validacao"]

_ORDEM_CONFIANCA = {Confianca.baixa: 0, Confianca.media: 1, Confianca.alta: 2}


@dataclass(frozen=True)
class RegraRoteamento:
    modelo_base: str = MODELO_HAIKU
    modelo_forte: str = MODELO_SONNET
    # Pré-roteamento: texto do chamado com mais caracteres que isso vai direto ao forte.
    max_caracteres: int = 1500
    # Confiança do modelo base abaixo deste nível escala.
    confianca_minima: Confianca = Confianca.media
    # Prioridades que, vindas do modelo base, escalam para confirmação.
    # Regra de segurança: `critica` sempre escala, mesmo que a configuração a omita.
    prioridades_escalaveis: frozenset[Prioridade] = field(
        default_factory=lambda: frozenset({Prioridade.critica})
    )
    # O modelo base esgotou as 2 tentativas de validação → tenta o forte.
    escalar_em_falha_validacao: bool = True

    def __post_init__(self) -> None:
        if Prioridade.critica not in self.prioridades_escalaveis:
            object.__setattr__(
                self, "prioridades_escalaveis", self.prioridades_escalaveis | {Prioridade.critica}
            )

    @classmethod
    def do_ambiente(cls, ambiente: Mapping[str, str] | None = None) -> "RegraRoteamento":
        """Lê TRIAGEM_ROTEADOR_*; valor inválido → ErroConfiguracao."""
        env = os.environ if ambiente is None else ambiente
        regra = cls()
        mudancas: dict = {}

        bruto = env.get("TRIAGEM_ROTEADOR_MAX_CARACTERES")
        if bruto is not None:
            if not bruto.strip().isdigit() or int(bruto) < 1:
                raise ErroConfiguracao(
                    f"TRIAGEM_ROTEADOR_MAX_CARACTERES deve ser um inteiro positivo (recebido: {bruto!r})"
                )
            mudancas["max_caracteres"] = int(bruto)

        bruto = env.get("TRIAGEM_ROTEADOR_CONFIANCA_MINIMA")
        if bruto is not None:
            try:
                mudancas["confianca_minima"] = Confianca(bruto.strip().lower())
            except ValueError:
                raise ErroConfiguracao(
                    f"TRIAGEM_ROTEADOR_CONFIANCA_MINIMA deve ser alta, media ou baixa (recebido: {bruto!r})"
                ) from None

        bruto = env.get("TRIAGEM_ROTEADOR_PRIORIDADES")
        if bruto is not None:
            nomes = [p.strip().lower() for p in bruto.split(",") if p.strip()]
            try:
                mudancas["prioridades_escalaveis"] = frozenset(Prioridade(n) for n in nomes)
            except ValueError:
                raise ErroConfiguracao(
                    "TRIAGEM_ROTEADOR_PRIORIDADES deve listar critica, alta, media ou baixa "
                    f"separadas por vírgula (recebido: {bruto!r})"
                ) from None

        return cls(**{**regra.__dict__, **mudancas})

    def descrever(self) -> dict:
        return {
            "modelo_base": self.modelo_base,
            "modelo_forte": self.modelo_forte,
            "max_caracteres": self.max_caracteres,
            "confianca_minima": self.confianca_minima.value,
            "prioridades_escalaveis": sorted(p.value for p in self.prioridades_escalaveis),
            "escalar_em_falha_validacao": self.escalar_em_falha_validacao,
        }


@dataclass(frozen=True)
class Decisao:
    modelo: str
    motivo: Literal["padrao", "texto_longo"]


def decidir_modelo(chamado: Chamado, regra: RegraRoteamento) -> Decisao:
    """Pré-roteamento: texto longo vai direto ao modelo forte."""
    if len(chamado.texto) > regra.max_caracteres:
        return Decisao(modelo=regra.modelo_forte, motivo="texto_longo")
    return Decisao(modelo=regra.modelo_base, motivo="padrao")


def deve_escalar(
    resultado: ResultadoTriagem | ErroTriagem, regra: RegraRoteamento
) -> MotivoEscalonamento | None:
    """Pós-roteamento, sobre a resposta do modelo base.

    Erros de API (limite_taxa, timeout...) e recusa NÃO escalam: devolvem o erro.
    """
    if isinstance(resultado, ErroTriagem):
        if resultado.erro.tipo == "validacao" and regra.escalar_em_falha_validacao:
            return "falha_validacao"
        return None
    if _ORDEM_CONFIANCA[resultado.confianca] < _ORDEM_CONFIANCA[regra.confianca_minima]:
        return "confianca_baixa"
    if resultado.prioridade in regra.prioridades_escalaveis:
        return "prioridade"
    return None
