"""Cliente falso, sem rede, para testes e para o modo --simulado."""

from collections import deque
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from .modelos import Categoria, Chamado, Prioridade, Triagem

TOKENS_ENTRADA_SIMULADOS = 1500
TOKENS_SAIDA_SIMULADOS = 300

TRIAGEM_PADRAO = Triagem(
    categoria=Categoria.outro,
    prioridade=Prioridade.baixa,
    resumo="Resposta simulada (sem chamada à API).",
    resposta_sugerida="Olá! Recebemos seu chamado. Esta é uma resposta simulada.",
    justificativa="Modo simulado: triagem fixa.",
)


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


class _Messages:
    def __init__(self, cliente: "ClienteSimulado"):
        self._cliente = cliente

    def create(self, **kwargs: Any) -> SimpleNamespace:
        return self._cliente._responder(kwargs)


class ClienteSimulado:
    """Imita `client.messages.create` e `client.beta.messages.create`.

    Recebe uma fila de respostas (de `resposta()`) ou exceções, devolve uma por
    chamada e registra os kwargs em `chamadas`.
    """

    def __init__(self, fila: Iterable[Any] = (), padrao: Triagem | None = None):
        self._fila = deque(fila)
        # Com `padrao`, responde indefinidamente quando a fila acaba (modo fixo).
        self._padrao = padrao
        self.chamadas: list[dict[str, Any]] = []
        self.messages = _Messages(self)
        self.beta = SimpleNamespace(messages=_Messages(self))

    def _responder(self, kwargs: dict[str, Any]) -> SimpleNamespace:
        self.chamadas.append(kwargs)
        if self._fila:
            item = self._fila.popleft()
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
        """Sempre a mesma triagem, sem fim (servidor com TRIAGEM_SIMULADO=1)."""
        return cls(padrao=triagem)

    @classmethod
    def do_gabarito(cls, chamados: Iterable[Chamado]) -> "ClienteSimulado":
        """Uma resposta por chamado, em ordem: o gabarito, ou a triagem fixa."""
        fila = []
        for chamado in chamados:
            triagem = TRIAGEM_PADRAO
            if chamado.gabarito:
                triagem = TRIAGEM_PADRAO.model_copy(
                    update={
                        "categoria": chamado.gabarito.categoria,
                        "prioridade": chamado.gabarito.prioridade,
                        "justificativa": "Modo simulado: devolve o gabarito.",
                    }
                )
            fila.append(resposta(triagem))
        return cls(fila)
