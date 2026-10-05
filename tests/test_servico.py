from datetime import date

import anthropic
import httpx2
import pytest

import triagem
from triagem import ErroConfiguracao, ErroEntrada, FalhaTriagem, ResultadoTriagem, Triador
from triagem.simulado import ClienteSimulado, resposta

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_superficie_publica():
    assert set(triagem.__all__) >= {
        "triar", "Triador", "FalhaTriagem", "ErroConfiguracao", "ErroEntrada",
        "ResultadoTriagem", "Triagem", "Meta", "Categoria", "Prioridade", "Chamado", "__version__",
    }
    assert issubclass(ErroConfiguracao, FalhaTriagem) and issubclass(ErroEntrada, FalhaTriagem)
    # Nomes antigos continuam importáveis.
    from triagem.config import ErroConfiguracao as antigo_config
    from triagem.entrada import ErroEntrada as antigo_entrada
    assert antigo_config is ErroConfiguracao and antigo_entrada is ErroEntrada


def test_triar_com_sucesso(triagem_valida):
    cliente = ClienteSimulado([resposta(triagem_valida)])
    resultado = Triador(cliente=cliente).triar("Sistema fora do ar", assunto="Erro", canal="email", id="T-9")

    assert isinstance(resultado, ResultadoTriagem)
    assert resultado.id == "T-9"
    assert resultado.categoria == triagem_valida.categoria


def test_data_referencia_na_mensagem_e_na_meta(triagem_valida):
    cliente = ClienteSimulado([resposta(triagem_valida)])
    resultado = Triador(cliente=cliente).triar("Boleto vence amanhã", data_referencia=date(2026, 10, 2))

    assert resultado.meta.data_referencia == "2026-10-02"
    mensagem = cliente.chamadas[0]["messages"][0]["content"]
    assert "Data de referência (hoje): 02/10/2026, sexta-feira." in mensagem


def test_sem_data_referencia_fica_nao_informada(triagem_valida):
    cliente = ClienteSimulado([resposta(triagem_valida)])
    resultado = Triador(cliente=cliente).triar("Oi")

    assert resultado.meta.data_referencia is None
    assert "Data de referência: não informada." in cliente.chamadas[0]["messages"][0]["content"]


@pytest.mark.parametrize("texto", ["", "   "])
def test_entrada_vazia_levanta_erro_de_entrada(texto):
    with pytest.raises(ErroEntrada) as info:
        Triador(cliente=ClienteSimulado()).triar(texto)
    assert info.value.tipo == "entrada"
    assert "texto" in info.value.mensagem


def test_recusa_levanta_falha():
    cliente = ClienteSimulado([resposta(None, stop_reason="refusal")])
    with pytest.raises(FalhaTriagem) as info:
        Triador(cliente=cliente).triar("Oi")
    assert info.value.tipo == "recusa"
    assert info.value.detalhe().model_dump() == {"tipo": "recusa", "mensagem": info.value.mensagem}


def test_limite_de_taxa_levanta_falha():
    erro = anthropic.RateLimitError("erro", response=httpx2.Response(429, request=REQ), body=None)
    with pytest.raises(FalhaTriagem) as info:
        Triador(cliente=ClienteSimulado([erro])).triar("Oi")
    assert info.value.tipo == "limite_taxa"


def test_triar_chamado_nao_levanta():
    cliente = ClienteSimulado([resposta(None, stop_reason="refusal")])
    resultado = Triador(cliente=cliente).triar_chamado(triagem.Chamado(id="A", texto="Oi"))
    assert resultado.erro.tipo == "recusa"


def test_sem_chave_e_sem_cliente_levanta_configuracao():
    with pytest.raises(ErroConfiguracao, match="ANTHROPIC_API_KEY"):
        Triador()


def test_atalho_triar_usa_triador_padrao(monkeypatch, triagem_valida):
    from triagem import servico

    servico._triador_padrao.cache_clear()
    padrao = Triador(cliente=ClienteSimulado.fixo(triagem_valida))
    monkeypatch.setattr(servico, "_triador_padrao", lambda: padrao)

    resultado = triagem.triar("Oi", data_referencia=date(2026, 10, 1), id="X")
    assert resultado.id == "X" and resultado.meta.data_referencia == "2026-10-01"


def test_cliente_fixo_responde_indefinidamente():
    triador = Triador(cliente=ClienteSimulado.fixo())
    assert all(isinstance(triador.triar("Oi"), ResultadoTriagem) for _ in range(5))
