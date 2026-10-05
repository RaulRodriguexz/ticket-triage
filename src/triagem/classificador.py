"""Uma chamada à API por chamado, com validação e no máximo 1 nova tentativa."""

import time
from typing import Any

import anthropic
from pydantic import ValidationError

from . import prompt
from .config import Config, custo_usd
from .modelos import (
    Chamado,
    DetalheErro,
    ErroTriagem,
    Meta,
    ResultadoTriagem,
    Triagem,
)

BETA_FALLBACK = "server-side-fallback-2026-07-01"

# O mesmo JSON Schema que `messages.parse(output_format=Triagem)` enviaria. Usamos
# `create` + validação local porque o `parse` lança ValidationError sem devolver
# o `usage`, e os tokens da tentativa inválida também entram no custo.
FORMATO_SAIDA = {"type": "json_schema", "schema": anthropic.transform_schema(Triagem)}

# Da mais específica para a mais geral (APITimeoutError herda de APIConnectionError).
# {tentativas} = 1 + max_retries, só nos erros que o SDK repete (408/409/429/5xx, rede).
# 403 é falha de permissão da chave (dependência externa, 502 no HTTP); 404 indica
# modelo inexistente ou indisponível para a chave: é configuração do servidor (503).
ERROS_API: list[tuple[type[Exception], str, str]] = [
    (anthropic.AuthenticationError, "autenticacao", "Chave da API inválida"),
    (anthropic.PermissionDeniedError, "autenticacao", "A chave da API não tem permissão para este recurso"),
    (anthropic.NotFoundError, "configuracao", "Modelo não encontrado ou indisponível para esta chave"),
    (anthropic.RateLimitError, "limite_taxa", "Limite de requisições atingido após {tentativas} tentativa(s)"),
    (anthropic.APITimeoutError, "timeout", "Tempo esgotado após {tentativas} tentativa(s)"),
    (anthropic.APIConnectionError, "conexao", "Falha de conexão após {tentativas} tentativa(s)"),
    (anthropic.BadRequestError, "requisicao_invalida", "Requisição rejeitada pela API"),
    (anthropic.InternalServerError, "api", "Erro da API após {tentativas} tentativa(s)"),
    (anthropic.APIStatusError, "api", "Erro da API"),
]


class _RespostaInvalida(Exception):
    def __init__(self, aviso: str):
        super().__init__(aviso)
        self.aviso = aviso


def _chamar_api(cliente: Any, config: Config, system: str, user: str) -> Any:
    comum = {
        "model": config.modelo,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if config.eh_haiku:
        # O SDK 1.x não expõe mais `temperature`; vai no corpo bruto (RNF-03).
        return cliente.messages.create(
            max_tokens=1000,
            output_config={"format": FORMATO_SAIDA},
            extra_body={"temperature": 0},
            **comum,
        )
    # Sonnet: sem temperature (400), raciocínio adaptativo com esforço baixo.
    return cliente.beta.messages.create(
        max_tokens=2000,
        output_config={"effort": "low", "format": FORMATO_SAIDA},
        betas=[BETA_FALLBACK],
        fallbacks="default",
        **comum,
    )


def _validar(resposta: Any) -> Triagem:
    """Valida no cliente o que a API não garante (resumo, promessas na resposta)."""
    if resposta.stop_reason == "max_tokens":
        raise _RespostaInvalida(
            "Atenção: na tentativa anterior, a resposta foi cortada por limite de tokens. Seja mais conciso."
        )
    texto = "".join(bloco.text for bloco in resposta.content if bloco.type == "text")
    if not texto.strip():
        raise _RespostaInvalida(
            "Atenção: na tentativa anterior, a resposta não seguiu o formato pedido."
        )
    try:
        return Triagem.model_validate_json(texto)
    except ValidationError as erro:
        raise _RespostaInvalida(_aviso_validacao(erro)) from erro


def _aviso_validacao(erro: ValidationError) -> str:
    primeiro = erro.errors()[0]
    campo = ".".join(str(parte) for parte in primeiro["loc"]) or "a resposta"
    return f"Atenção: na tentativa anterior, {campo} violou a regra: {primeiro['msg']}."


def classificar(
    chamado: Chamado, cliente: Any, config: Config
) -> ResultadoTriagem | ErroTriagem:
    """Classifica um chamado. Nunca levanta exceção da API: devolve ErroTriagem.

    Toda tentativa paga entra no gasto, inclusive as inválidas. No ErroTriagem, o gasto
    vai em `gasto` (fora do JSON); uma chamada sem `usage` (timeout, erro de rede)
    conta em `custos_desconhecidos` em vez de virar custo zero.
    """
    id_chamado = chamado.id or "sem-id"
    tokens_entrada = tokens_saida = 0
    modelo = config.modelo
    aviso: str | None = None
    inicio = time.perf_counter()

    def gasto(desconhecidos: int = 0) -> Meta:
        custo = custo_usd(modelo, tokens_entrada, tokens_saida)
        pagou_algo = tokens_entrada or tokens_saida
        return Meta(
            modelo=modelo,
            latencia_ms=round((time.perf_counter() - inicio) * 1000),
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
            custo_usd=round(custo, 6) if custo is not None and pagou_algo else None,
            custos_desconhecidos=desconhecidos + (custo is None and bool(pagou_algo)),
        )

    for _ in range(2):
        system, user = prompt.montar(chamado, aviso)
        try:
            resposta = _chamar_api(cliente, config, system, user)
        except anthropic.APIError as erro:
            falha = _erro_api(id_chamado, erro, tentativas=config.max_retries + 1)
            falha.gasto = gasto(desconhecidos=1)  # esta chamada não devolveu usage
            return falha

        # Soma antes de validar: tentativas inválidas também custam.
        tokens_entrada += resposta.usage.input_tokens
        tokens_saida += resposta.usage.output_tokens
        modelo = resposta.model or config.modelo

        if resposta.stop_reason == "refusal":
            return ErroTriagem(
                id=id_chamado,
                erro=DetalheErro(tipo="recusa", mensagem="O modelo recusou classificar este chamado"),
                gasto=gasto(),
            )
        try:
            triagem = _validar(resposta)
        except _RespostaInvalida as invalida:
            aviso = invalida.aviso
            continue

        meta = gasto()
        if meta.custo_usd is None:
            meta.custos_desconhecidos = 1
        return ResultadoTriagem(id=id_chamado, meta=meta, **triagem.model_dump())

    return ErroTriagem(
        id=id_chamado,
        erro=DetalheErro(
            tipo="validacao",
            mensagem=f"Resposta fora do schema após 2 tentativas. {aviso or ''}".strip(),
        ),
        gasto=gasto(),
    )


def _retry_after(erro: anthropic.APIError) -> str | None:
    """Valor de Retry-After devolvido pela API, se for um número de segundos."""
    resposta = getattr(erro, "response", None)
    valor = resposta.headers.get("retry-after") if resposta is not None else None
    return valor.strip() if valor and valor.strip().isdigit() else None


def _erro_api(id_chamado: str, erro: anthropic.APIError, tentativas: int) -> ErroTriagem:
    for classe, tipo, mensagem in ERROS_API:
        if isinstance(erro, classe):
            detalhe = DetalheErro(
                tipo=tipo,
                mensagem=mensagem.format(tentativas=tentativas),
                retry_after=_retry_after(erro) if tipo == "limite_taxa" else None,
            )
            return ErroTriagem(id=id_chamado, erro=detalhe)
    return ErroTriagem(id=id_chamado, erro=DetalheErro(tipo="api", mensagem="Erro da API"))
