"""Cliente falso, sem rede, para testes e para o modo --simulado.

O simulado NÃO mede acerto nem latência: as respostas vêm do gabarito e os tokens e
a confiança são fictícios (determinísticos) por perfil de modelo.
"""

import hashlib
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .config import MODELO_HAIKU, MODELO_SONNET
from .modelos import Categoria, Chamado, Confianca, Prioridade, Triagem

TOKENS_ENTRADA_SIMULADOS = 1500
TOKENS_SAIDA_SIMULADOS = 300

TRIAGEM_PADRAO = Triagem(
    categoria=Categoria.outro,
    prioridade=Prioridade.baixa,
    resumo="Resposta simulada (sem chamada à API).",
    resposta_sugerida="Olá! Recebemos seu chamado. Esta é uma resposta simulada.",
    justificativa="Modo simulado: triagem fixa.",
    confianca=Confianca.alta,
)


@dataclass(frozen=True)
class PerfilSimulado:
    """Tokens e confiança plausíveis por modelo. Recalibrar com o `usage` real."""

    prompt_base: int = 1450        # tamanho aproximado do system prompt atual
    saida: int = 260               # JSON da triagem
    raciocinio: int = 0            # tokens de raciocínio somados à saída
    confianca_baixa_em_10: int = 0  # em quantos de cada 10 chamados declara "baixa"


PERFIS_PADRAO: dict[str, PerfilSimulado] = {
    MODELO_HAIKU: PerfilSimulado(confianca_baixa_em_10=3),
    MODELO_SONNET: PerfilSimulado(raciocinio=220),  # esforço "low"
}

_TEXTO_DO_CHAMADO = re.compile(r"\ntexto:\n(.*)\n</chamado>", re.DOTALL)


def resposta(
    saida: Triagem | str | None,
    stop_reason: str = "end_turn",
    model: str | None = None,
    tokens_entrada: int = TOKENS_ENTRADA_SIMULADOS,
    tokens_saida: int = TOKENS_SAIDA_SIMULADOS,
) -> SimpleNamespace:
    """Monta uma resposta no formato que o classificador lê do SDK.

    `saida` pode ser uma Triagem (serializada como o JSON da API), um texto
    bruto (para simular JSON inválido) ou None (sem conteúdo).
    """
    if isinstance(saida, Triagem):
        saida = saida.model_dump_json()
    conteudo = [] if saida is None else [SimpleNamespace(type="text", text=saida)]
    return SimpleNamespace(
        content=conteudo,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=tokens_entrada, output_tokens=tokens_saida),
    )


def _triagem_do_gabarito(chamado: Chamado | None) -> Triagem:
    if chamado is None or chamado.gabarito is None:
        return TRIAGEM_PADRAO
    return TRIAGEM_PADRAO.model_copy(
        update={
            "categoria": chamado.gabarito.categoria,
            "prioridade": chamado.gabarito.prioridade,
            "justificativa": "Modo simulado: devolve o gabarito.",
        }
    )


class _Messages:
    def __init__(self, cliente: "ClienteSimulado"):
        self._cliente = cliente

    def create(self, **kwargs: Any) -> SimpleNamespace:
        return self._cliente._responder(kwargs)


class ClienteSimulado:
    """Imita `client.messages.create` e `client.beta.messages.create`.

    Responde, nesta ordem: da fila (respostas de `resposta()` ou exceções), da
    função `responder(kwargs)` ou da triagem `padrao`. Registra os kwargs em `chamadas`.
    """

    # Linhas gravadas no banco com simulado = 1.
    simulado = True

    def __init__(
        self,
        fila: Iterable[Any] = (),
        padrao: Triagem | None = None,
        responder: Callable[[dict[str, Any]], SimpleNamespace] | None = None,
    ):
        self._fila = deque(fila)
        self._padrao = padrao
        self._responder_fn = responder
        self.chamadas: list[dict[str, Any]] = []
        self.messages = _Messages(self)
        self.beta = SimpleNamespace(messages=_Messages(self))

    def _responder(self, kwargs: dict[str, Any]) -> SimpleNamespace:
        self.chamadas.append(kwargs)
        if self._fila:
            item = self._fila.popleft()
        elif self._responder_fn is not None:
            item = self._responder_fn(kwargs)
        elif self._padrao is not None:
            item = resposta(self._padrao)
        else:
            raise RuntimeError("ClienteSimulado: fila de respostas vazia")
        if isinstance(item, BaseException):
            raise item
        if item.model is None:
            item.model = kwargs.get("model")
        return item

    @classmethod
    def fixo(cls, triagem: Triagem = TRIAGEM_PADRAO) -> "ClienteSimulado":
        """Sempre a mesma triagem, sem fim."""
        return cls(padrao=triagem)

    @classmethod
    def do_gabarito(cls, chamados: Iterable[Chamado]) -> "ClienteSimulado":
        """Uma resposta por chamado, em ordem de fila: o gabarito, ou a triagem fixa."""
        return cls([resposta(_triagem_do_gabarito(c)) for c in chamados])

    @classmethod
    def por_modelo(
        cls,
        chamados: Iterable[Chamado] = (),
        perfis: Mapping[str, PerfilSimulado] = PERFIS_PADRAO,
    ) -> "ClienteSimulado":
        """Responde pelo CONTEÚDO do chamado (não pela ordem), com tokens e confiança por modelo.

        Funciona com 1 ou 2 chamadas por chamado (roteador). Tudo é determinístico:
        a variação vem de sha256(texto), sem `random`.
        """
        por_texto = {c.texto: c for c in chamados}

        def responder(kwargs: dict[str, Any]) -> SimpleNamespace:
            mensagem = kwargs["messages"][0]["content"]
            achado = _TEXTO_DO_CHAMADO.search(mensagem)
            texto = achado.group(1) if achado else mensagem
            perfil = perfis.get(kwargs["model"], PerfilSimulado())
            semente = int(hashlib.sha256(texto.encode("utf-8")).hexdigest(), 16)
            fator = 0.9 + (semente % 21) / 100  # ±10%, fixo por texto
            confianca = (
                Confianca.baixa if semente % 10 < perfil.confianca_baixa_em_10 else Confianca.alta
            )
            triagem = _triagem_do_gabarito(por_texto.get(texto)).model_copy(
                update={"confianca": confianca}
            )
            return resposta(
                triagem,
                tokens_entrada=round((perfil.prompt_base + len(texto) // 4) * fator),
                tokens_saida=round((perfil.saida + perfil.raciocinio) * fator),
            )

        return cls(responder=responder)
