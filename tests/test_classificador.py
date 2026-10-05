import anthropic
import httpx2
import pytest

from triagem.classificador import BETA_FALLBACK, FORMATO_SAIDA, classificar
from triagem.config import custo_usd
from triagem.modelos import ErroTriagem, ResultadoTriagem, Triagem
from triagem.prompt import PASTA_PROMPTS
from triagem.simulado import ClienteSimulado, resposta

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(classe, codigo):
    return classe("erro", response=httpx2.Response(codigo, request=REQ), body=None)


def _resumo_longo(triagem):
    # model_construct pula a validação, como uma resposta da API sem max_length garantido.
    return Triagem.model_construct(**{**triagem.model_dump(), "resumo": "x" * 121})


def test_sucesso_com_meta_e_custo(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(triagem_valida, tokens_entrada=1800, tokens_saida=400)])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ResultadoTriagem)
    assert resultado.id == "T-1"
    assert resultado.categoria == triagem_valida.categoria
    assert resultado.meta.modelo == "claude-sonnet-5-5"
    assert (resultado.meta.tokens_entrada, resultado.meta.tokens_saida) == (1800, 400)
    assert resultado.meta.custo_usd == pytest.approx(0.0076)
    assert resultado.meta.latencia_ms >= 0


def test_parametros_sonnet(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(triagem_valida)])
    classificar(chamado, cliente, config_sonnet)
    kwargs = cliente.chamadas[0]

    assert kwargs["model"] == "claude-sonnet-5-5"
    assert "temperature" not in kwargs and "extra_body" not in kwargs
    assert kwargs["output_config"] == {"effort": "low", "format": FORMATO_SAIDA}
    assert set(FORMATO_SAIDA["schema"]["required"]) == set(Triagem.model_fields)
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == [BETA_FALLBACK]
    assert kwargs["max_tokens"] == 2000
    criterios = (PASTA_PROMPTS / "criterios.md").read_text(encoding="utf-8").strip()
    assert criterios in kwargs["system"]
    assert "<chamado>" in kwargs["messages"][0]["content"]


def test_parametros_haiku(chamado, triagem_valida, config_haiku):
    cliente = ClienteSimulado([resposta(triagem_valida)])
    classificar(chamado, cliente, config_haiku)
    kwargs = cliente.chamadas[0]

    assert kwargs["model"] == "claude-haiku-4-5-20251001"
    assert kwargs["extra_body"] == {"temperature": 0}
    assert kwargs["max_tokens"] == 1000
    assert kwargs["output_config"] == {"format": FORMATO_SAIDA}
    assert "fallbacks" not in kwargs and "betas" not in kwargs


def test_modelo_real_vem_da_resposta(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(triagem_valida, model="claude-opus-4-8")])
    resultado = classificar(chamado, cliente, config_sonnet)
    assert resultado.meta.modelo == "claude-opus-4-8"


def test_nova_tentativa_apos_resumo_longo(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([
        resposta(_resumo_longo(triagem_valida), tokens_entrada=100, tokens_saida=10),
        resposta(triagem_valida, tokens_entrada=100, tokens_saida=10),
    ])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ResultadoTriagem)
    assert len(cliente.chamadas) == 2
    segunda = cliente.chamadas[1]["messages"][0]["content"]
    assert "Atenção: na tentativa anterior, resumo violou a regra" in segunda
    assert resultado.meta.tokens_entrada == 200  # as duas tentativas entram no custo
    assert resultado.meta.custo_usd == pytest.approx(custo_usd("claude-sonnet-5-5", 200, 20))


def test_json_invalido_soma_tokens_e_tenta_de_novo(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([
        resposta('{"categoria": "financeiro"}', tokens_entrada=100, tokens_saida=10),
        resposta(triagem_valida, tokens_entrada=100, tokens_saida=10),
    ])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ResultadoTriagem)
    assert "categoria" in cliente.chamadas[1]["messages"][0]["content"]
    assert (resultado.meta.tokens_entrada, resultado.meta.tokens_saida) == (200, 20)


def test_promessa_na_resposta_gera_nova_tentativa(chamado, triagem_valida, config_sonnet):
    promete = Triagem.model_construct(**{
        **triagem_valida.model_dump(), "resposta_sugerida": "Olá! Vamos estornar o valor em 24 horas.",
    })
    cliente = ClienteSimulado([resposta(promete), resposta(triagem_valida)])

    assert isinstance(classificar(chamado, cliente, config_sonnet), ResultadoTriagem)
    assert "resposta_sugerida violou a regra" in cliente.chamadas[1]["messages"][0]["content"]


def test_resumo_com_quebra_de_linha_gera_nova_tentativa(chamado, triagem_valida, config_sonnet):
    duas_linhas = Triagem.model_construct(**{**triagem_valida.model_dump(), "resumo": "linha 1\nlinha 2"})
    cliente = ClienteSimulado([resposta(duas_linhas), resposta(duas_linhas)])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ErroTriagem) and resultado.erro.tipo == "validacao"
    assert "uma linha" in resultado.erro.mensagem


def test_modelo_fora_da_tabela_tem_custo_desconhecido(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(triagem_valida, model="claude-modelo-novo")])
    resultado = classificar(chamado, cliente, config_sonnet)
    assert resultado.meta.modelo == "claude-modelo-novo"
    assert resultado.meta.custo_usd is None


def test_fallback_usa_preco_do_modelo_que_respondeu(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(triagem_valida, model="claude-opus-4-8", tokens_entrada=1000, tokens_saida=100)])
    resultado = classificar(chamado, cliente, config_sonnet)
    assert resultado.meta.custo_usd == pytest.approx(custo_usd("claude-opus-4-8", 1000, 100))
    assert resultado.meta.custo_usd == pytest.approx(0.0075)


def test_nova_tentativa_apos_max_tokens(chamado, triagem_valida, config_sonnet):
    cliente = ClienteSimulado([resposta(None, stop_reason="max_tokens"), resposta(triagem_valida)])
    assert isinstance(classificar(chamado, cliente, config_sonnet), ResultadoTriagem)
    assert len(cliente.chamadas) == 2


def test_duas_invalidas_viram_erro_de_validacao(chamado, triagem_valida, config_sonnet):
    invalida = _resumo_longo(triagem_valida)
    cliente = ClienteSimulado([resposta(invalida), resposta(invalida)])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ErroTriagem)
    assert resultado.erro.tipo == "validacao"
    assert len(cliente.chamadas) == 2


def test_recusa_sem_nova_tentativa(chamado, config_sonnet):
    cliente = ClienteSimulado([resposta(None, stop_reason="refusal")])
    resultado = classificar(chamado, cliente, config_sonnet)

    assert isinstance(resultado, ErroTriagem)
    assert resultado.erro.tipo == "recusa"
    assert len(cliente.chamadas) == 1


@pytest.mark.parametrize("erro,tipo", [
    (_status(anthropic.RateLimitError, 429), "limite_taxa"),
    (_status(anthropic.AuthenticationError, 401), "autenticacao"),
    (_status(anthropic.BadRequestError, 400), "requisicao_invalida"),
    (_status(anthropic.InternalServerError, 500), "api"),
    (anthropic.APITimeoutError(request=REQ), "timeout"),
    (anthropic.APIConnectionError(request=REQ), "conexao"),
])
def test_erros_da_api_viram_json(chamado, config_sonnet, erro, tipo):
    resultado = classificar(chamado, ClienteSimulado([erro]), config_sonnet)

    assert isinstance(resultado, ErroTriagem)
    assert resultado.erro.tipo == tipo
    assert resultado.model_dump(mode="json")["id"] == "T-1"


@pytest.mark.parametrize("max_retries,esperado", [(0, "1 tentativa(s)"), (2, "3 tentativa(s)")])
def test_mensagem_usa_numero_real_de_tentativas(chamado, max_retries, esperado):
    from triagem.config import Config

    config = Config(modelo="claude-sonnet-5-5", api_key="x", max_retries=max_retries)
    resultado = classificar(chamado, ClienteSimulado([_status(anthropic.RateLimitError, 429)]), config)
    assert esperado in resultado.erro.mensagem


def test_retry_after_da_api_e_preservado(chamado, config_sonnet):
    resposta_http = httpx2.Response(429, request=REQ, headers={"retry-after": "12"})
    erro = anthropic.RateLimitError("erro", response=resposta_http, body=None)
    resultado = classificar(chamado, ClienteSimulado([erro]), config_sonnet)

    assert resultado.erro.retry_after == "12"
    assert "retry_after" not in resultado.model_dump(mode="json")["erro"]  # formato do JSON não muda


@pytest.mark.parametrize("classe,codigo,tipo", [
    (anthropic.PermissionDeniedError, 403, "autenticacao"),
    (anthropic.NotFoundError, 404, "configuracao"),
])
def test_403_e_404(chamado, config_sonnet, classe, codigo, tipo):
    resultado = classificar(chamado, ClienteSimulado([_status(classe, codigo)]), config_sonnet)
    assert resultado.erro.tipo == tipo
