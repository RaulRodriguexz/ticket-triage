import anthropic
import httpx2
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from triagem import Triador  # noqa: E402
from triagem.simulado import ClienteSimulado, resposta  # noqa: E402
from triagem.web import criar_app  # noqa: E402

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _cliente_http(fila=(), padrao=None, **kwargs) -> TestClient:
    triador = Triador(cliente=ClienteSimulado(fila, padrao=padrao))
    return TestClient(criar_app(triador), **kwargs)


def test_saude():
    with _cliente_http() as http:
        r = http.get("/v1/saude")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "modelo": "claude-sonnet-5-5"}


def test_triagem_200(triagem_valida):
    with _cliente_http([resposta(triagem_valida)]) as http:
        r = http.post("/v1/triagem", json={
            "texto": "Boleto vence dia 15/10", "canal": "ticket", "id": "T-1", "data_referencia": "2026-10-02",
        })
    corpo = r.json()
    assert r.status_code == 200
    assert corpo["id"] == "T-1"
    assert corpo["categoria"] == "bug_tecnico"
    assert corpo["meta"]["data_referencia"] == "2026-10-02"


def test_sem_data_usa_hoje_no_fuso(triagem_valida, monkeypatch):
    monkeypatch.setenv("TRIAGEM_FUSO", "America/Sao_Paulo")
    with _cliente_http([resposta(triagem_valida)]) as http:
        r = http.post("/v1/triagem", json={"texto": "Oi"})
    assert r.status_code == 200
    assert r.json()["meta"]["data_referencia"] is not None


@pytest.mark.parametrize("corpo,campo", [
    ({}, "texto"),
    ({"texto": ""}, "texto"),
    ({"texto": "oi", "data_referencia": "amanhã"}, "data_referencia"),
    ({"texto": "oi", "modelo": "claude-opus-5-5"}, "modelo"),  # o cliente não escolhe o modelo
])
def test_corpo_invalido_422(corpo, campo):
    with _cliente_http() as http:
        r = http.post("/v1/triagem", json=corpo)
    assert r.status_code == 422
    assert r.json()["erro"]["tipo"] == "entrada"
    assert campo in r.json()["erro"]["mensagem"]


def test_recusa_422():
    with _cliente_http([resposta(None, stop_reason="refusal")]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == 422
    assert r.json()["erro"]["tipo"] == "recusa"


def test_limite_de_taxa_429_com_retry_after():
    erro = anthropic.RateLimitError("erro", response=httpx2.Response(429, request=REQ), body=None)
    with _cliente_http([erro]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"
    assert set(r.json()) == {"erro"} and set(r.json()["erro"]) == {"tipo", "mensagem"}


@pytest.mark.parametrize("erro,status", [
    (anthropic.APIConnectionError(request=REQ), 502),
    (anthropic.InternalServerError("erro", response=httpx2.Response(500, request=REQ), body=None), 502),
    (anthropic.APITimeoutError(request=REQ), 504),
])
def test_falhas_da_dependencia(erro, status):
    with _cliente_http([erro]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == status
    assert "Traceback" not in r.text


def test_validacao_502(triagem_valida):
    invalida = '{"categoria": "financeiro"}'
    with _cliente_http([resposta(invalida), resposta(invalida)]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == 502
    assert r.json()["erro"]["tipo"] == "validacao"


def test_erro_interno_500_sem_stack_trace():
    # Fila vazia sem padrão: o cliente falso levanta RuntimeError.
    with _cliente_http(raise_server_exceptions=False) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == 500
    assert r.json() == {"erro": {"tipo": "interno", "mensagem": "Erro interno"}}


def test_startup_sem_chave_falha(monkeypatch):
    from triagem import ErroConfiguracao

    monkeypatch.delenv("TRIAGEM_SIMULADO", raising=False)
    with pytest.raises(ErroConfiguracao):
        with TestClient(criar_app()):
            pass


def test_startup_simulado_pelo_ambiente(monkeypatch):
    monkeypatch.setenv("TRIAGEM_SIMULADO", "1")
    with TestClient(criar_app()) as http:
        r1 = http.post("/v1/triagem", json={"texto": "oi"})
        r2 = http.post("/v1/triagem", json={"texto": "tchau"})
    assert r1.status_code == r2.status_code == 200


def test_retry_after_usa_valor_da_api():
    resposta_http = httpx2.Response(429, request=REQ, headers={"retry-after": "7"})
    erro = anthropic.RateLimitError("erro", response=resposta_http, body=None)
    with _cliente_http([erro]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "7"


@pytest.mark.parametrize("erro,status,tipo", [
    (anthropic.PermissionDeniedError("e", response=httpx2.Response(403, request=REQ), body=None), 502, "autenticacao"),
    (anthropic.NotFoundError("e", response=httpx2.Response(404, request=REQ), body=None), 503, "configuracao"),
])
def test_403_e_404_da_api(erro, status, tipo):
    with _cliente_http([erro]) as http:
        r = http.post("/v1/triagem", json={"texto": "oi"})
    assert r.status_code == status
    assert r.json()["erro"]["tipo"] == tipo


@pytest.mark.parametrize("valor", ["abc", "-1", "1.5"])
def test_max_retries_invalido_e_erro_de_configuracao(monkeypatch, valor):
    from triagem import ErroConfiguracao

    monkeypatch.setenv("TRIAGEM_SIMULADO", "1")
    monkeypatch.setenv("TRIAGEM_MAX_RETRIES", valor)
    with pytest.raises(ErroConfiguracao, match="TRIAGEM_MAX_RETRIES"):
        with TestClient(criar_app()):
            pass
