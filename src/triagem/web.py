"""Endpoint HTTP opcional (extra [web]): `uvicorn triagem.web:app --host 127.0.0.1 --port 8000`.

Camada fina: traduz JSON para `Triador.triar()` e `FalhaTriagem` para status HTTP.
"""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .erros import ErroConfiguracao, FalhaTriagem
from .modelos import TAMANHO_MAX_TEXTO, ResultadoTriagem, TipoErro
from .persistencia import Registro
from .servico import Triador
from .simulado import ClienteSimulado

log = logging.getLogger("triagem.web")

FUSO_PADRAO = "America/Sao_Paulo"
STATUS_POR_TIPO: dict[TipoErro, int] = {
    "entrada": 422,
    "recusa": 422,
    "limite_taxa": 429,
    "validacao": 502,
    "autenticacao": 502,
    "requisicao_invalida": 502,
    "api": 502,
    "conexao": 502,
    "timeout": 504,
    "configuracao": 503,
    "banco_indisponivel": 503,
    "interno": 500,
}


class PedidoTriagem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=TAMANHO_MAX_TEXTO)
    assunto: str | None = None
    canal: Literal["email", "ticket"] | None = None
    id: str | None = None
    data_referencia: date | None = None


def _hoje() -> date:
    """Data de hoje no fuso TRIAGEM_FUSO (padrão America/Sao_Paulo)."""
    try:
        fuso = ZoneInfo(os.environ.get("TRIAGEM_FUSO", FUSO_PADRAO))
    except (ZoneInfoNotFoundError, ValueError):
        return date.today()
    return datetime.now(fuso).date()


def _max_retries_do_ambiente() -> int:
    # Padrão 1: mantém a requisição web abaixo de ~60 s no pior caso.
    valor = os.environ.get("TRIAGEM_MAX_RETRIES", "1").strip()
    if not valor.isdigit():
        raise ErroConfiguracao(
            f"TRIAGEM_MAX_RETRIES deve ser um inteiro maior ou igual a 0 (recebido: {valor!r})"
        )
    return int(valor)


def _triador_do_ambiente() -> Triador:
    max_retries = _max_retries_do_ambiente()
    cliente = ClienteSimulado.por_modelo() if os.environ.get("TRIAGEM_SIMULADO") == "1" else None
    # Sem banco (TRIAGEM_DB=off ou falha ao abrir), o servidor sobe sem persistência.
    return Triador(cliente=cliente, max_retries=max_retries, registro=Registro.do_ambiente())


RETRY_AFTER_PADRAO = "30"


def _resposta_erro(tipo: TipoErro, mensagem: str, retry_after: str | None = None) -> JSONResponse:
    cabecalhos = {"Retry-After": retry_after or RETRY_AFTER_PADRAO} if tipo == "limite_taxa" else None
    return JSONResponse(
        status_code=STATUS_POR_TIPO.get(tipo, 500),
        content={"erro": {"tipo": tipo, "mensagem": mensagem}},
        headers=cabecalhos,
    )


def criar_app(triador: Triador | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Criado uma vez e reutilizado; sem chave, o startup falha (ErroConfiguracao).
        app.state.triador = triador or _triador_do_ambiente()
        yield

    app = FastAPI(title="Triagem de chamados", version=__version__, lifespan=lifespan)

    origens = [o.strip() for o in os.environ.get("TRIAGEM_CORS_ORIGENS", "").split(",") if o.strip()]
    if origens:
        app.add_middleware(CORSMiddleware, allow_origins=origens, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type"])

    @app.exception_handler(FalhaTriagem)
    async def _falha(_: Request, falha: FalhaTriagem) -> JSONResponse:
        if STATUS_POR_TIPO.get(falha.tipo, 500) >= 500:
            log.warning("Falha na triagem: %s: %s", falha.tipo, falha.mensagem)
        return _resposta_erro(falha.tipo, falha.mensagem, falha.retry_after)

    @app.exception_handler(RequestValidationError)
    async def _corpo_invalido(_: Request, erro: RequestValidationError) -> JSONResponse:
        detalhe = erro.errors()[0] if erro.errors() else {"loc": (), "msg": "corpo inválido"}
        campo = ".".join(str(p) for p in detalhe["loc"] if p != "body") or "corpo"
        return _resposta_erro("entrada", f"campo '{campo}': {detalhe['msg']}")

    @app.exception_handler(Exception)
    async def _interno(_: Request, erro: Exception) -> JSONResponse:
        log.exception("Erro interno na triagem")
        return _resposta_erro("interno", "Erro interno")

    @app.get("/v1/saude")
    def saude(request: Request) -> dict:
        triador: Triador = request.app.state.triador
        return {
            "status": "ok",
            "modelo": triador.modelo,
            "regra": triador.regra.descrever() if triador.regra else None,
            "persistencia": triador.registro is not None,
        }

    @app.get("/v1/stats")
    def stats(
        request: Request,
        desde: date | None = None,
        ate: date | None = None,
        simulado: int | None = Query(default=None, ge=0, le=1),
    ) -> JSONResponse:
        registro = request.app.state.triador.registro
        if registro is None:
            return _resposta_erro("configuracao", "Persistência desligada")
        try:
            dados = registro.estatisticas(desde, ate, None if simulado is None else bool(simulado))
        except (sqlite3.Error, OSError) as erro:
            log.warning("Banco indisponível em /v1/stats: %s", type(erro).__name__)
            return _resposta_erro("banco_indisponivel", "Banco de dados indisponível")
        return JSONResponse(dados)

    @app.post("/v1/triagem", response_model=ResultadoTriagem)
    def triagem(pedido: PedidoTriagem, request: Request) -> JSONResponse:
        resultado = request.app.state.triador.triar(
            pedido.texto,
            data_referencia=pedido.data_referencia or _hoje(),
            assunto=pedido.assunto,
            canal=pedido.canal,
            id=pedido.id,
        )
        return JSONResponse(resultado.model_dump(mode="json"))

    return app


app = criar_app()
