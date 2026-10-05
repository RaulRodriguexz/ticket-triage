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
    assert r.json() == {"status": "ok", "modelo": "claude-sonnet-5-5", "regra": None, "persistencia": False}


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


def _app_com_banco(tmp_path, fila):
    from triagem.persistencia import Registro

    registro = Registro(tmp_path / "t.db")
    triador = Triador("auto", cliente=ClienteSimulado(fila), registro=registro)
    return TestClient(criar_app(triador))


def test_stats_depois_de_tres_triagens(tmp_path, triagem_valida):
    from triagem.modelos import Confianca, Prioridade

    media = triagem_valida.model_copy(update={"prioridade": Prioridade.media})
    baixa = media.model_copy(update={"confianca": Confianca.baixa})
    fila = [
        resposta(media, tokens_entrada=1000, tokens_saida=100),
        resposta(baixa, tokens_entrada=1000, tokens_saida=100), resposta(media, tokens_entrada=1000, tokens_saida=100),
        resposta(media, tokens_entrada=1000, tokens_saida=100),
    ]
    with _app_com_banco(tmp_path, fila) as http:
        custos = [http.post("/v1/triagem", json={"texto": t}).json()["meta"]["custo_usd"]
                  for t in ("um", "dois", "tres")]
        r = http.get("/v1/stats")
        assert http.get("/v1/saude").json()["persistencia"] is True
    s = r.json()
    assert r.status_code == 200
    assert s["tickets"] == 3 and s["ok"] == 3
    assert s["custo_total_usd"] == pytest.approx(sum(custos))
    assert s["roteamento"]["escalados"] == 1
    assert s["roteamento"]["taxa_escalonamento"] == pytest.approx(1 / 3, abs=1e-4)
    assert set(s["latencia_ms"]) == {"p50", "p95"}
    assert "texto_hash" not in r.text and "um" not in s


def test_stats_filtros_e_data_invalida(tmp_path):
    with _app_com_banco(tmp_path, []) as http:
        assert http.get("/v1/stats", params={"desde": "2026-10-01", "ate": "2026-10-31", "simulado": 1}).status_code == 200
        r = http.get("/v1/stats", params={"desde": "ontem"})
    assert r.status_code == 422 and r.json()["erro"]["tipo"] == "entrada"


def test_stats_sem_banco_503():
    with _cliente_http() as http:
        r = http.get("/v1/stats")
    assert r.status_code == 503
    assert r.json() == {"erro": {"tipo": "configuracao", "mensagem": "Persistência desligada"}}


def test_startup_com_banco_quebrado_sobe_sem_persistencia(tmp_path, monkeypatch):
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    monkeypatch.setenv("TRIAGEM_SIMULADO", "1")
    monkeypatch.setenv("TRIAGEM_DB", str(pasta))
    with TestClient(criar_app()) as http:
        assert http.post("/v1/triagem", json={"texto": "oi"}).status_code == 200
        assert http.get("/v1/saude").json()["persistencia"] is False
        assert http.get("/v1/stats").status_code == 503


def test_post_grava_no_banco_do_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGEM_SIMULADO", "1")
    monkeypatch.setenv("TRIAGEM_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("TRIAGEM_MODELO")
    with TestClient(criar_app()) as http:
        http.post("/v1/triagem", json={"texto": "oi"})
        saude = http.get("/v1/saude").json()
        s = http.get("/v1/stats").json()
    assert saude["modelo"] == "auto" and saude["regra"]["max_caracteres"] == 1500
    assert s["tickets"] == 1 and s["simulados"] == 1


def test_stats_com_banco_quebrado_responde_banco_indisponivel(tmp_path, monkeypatch):
    import sqlite3

    with _app_com_banco(tmp_path, []) as http:
        def explode(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sqlite3, "connect", explode)
        r = http.get("/v1/stats")
    assert r.status_code == 503
    assert r.json()["erro"]["tipo"] == "banco_indisponivel"
