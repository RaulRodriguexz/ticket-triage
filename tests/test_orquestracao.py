"""Roteador dentro do Triador: chamadas, custo somado e meta.roteamento em cada caminho."""

import anthropic
import httpx2
import pytest

from triagem import Triador
from triagem.config import MODELO_HAIKU, MODELO_SONNET, custo_usd
from triagem.modelos import Chamado, Confianca, ErroTriagem, Prioridade, ResultadoTriagem
from triagem.roteador import RegraRoteamento
from triagem.simulado import ClienteSimulado, resposta

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.fixture
def media_alta(triagem_valida):
    """Triagem que NÃO escala: prioridade media, confiança alta."""
    return triagem_valida.model_copy(update={"prioridade": Prioridade.media})


def _auto(fila, **kwargs):
    cliente = ClienteSimulado(fila)
    return Triador("auto", cliente=cliente, **kwargs), cliente


def test_padrao_e_auto(monkeypatch):
    monkeypatch.delenv("TRIAGEM_MODELO")
    triador = Triador(cliente=ClienteSimulado())
    assert triador.modelo == "auto"
    assert triador.regra == RegraRoteamento()


def test_sem_escalada_fica_no_haiku(media_alta):
    triador, cliente = _auto([resposta(media_alta, tokens_entrada=1600, tokens_saida=280)])
    r = triador.triar("Senha não chega")

    assert [c["model"] for c in cliente.chamadas] == [MODELO_HAIKU]
    rot = r.meta.roteamento
    assert (rot.modo, rot.modelo_inicial, rot.escalado, rot.motivo) == ("auto", MODELO_HAIKU, False, None)
    assert r.meta.modelo == MODELO_HAIKU
    assert r.meta.custo_usd == pytest.approx(custo_usd(MODELO_HAIKU, 1600, 280))


def test_confianca_baixa_escala_e_soma_custos_por_preco(media_alta):
    baixa = media_alta.model_copy(update={"confianca": Confianca.baixa})
    triador, cliente = _auto([
        resposta(baixa, tokens_entrada=1600, tokens_saida=280),
        resposta(media_alta, tokens_entrada=1750, tokens_saida=480),
    ])
    r = triador.triar("Texto ambíguo")

    assert [c["model"] for c in cliente.chamadas] == [MODELO_HAIKU, MODELO_SONNET]
    rot = r.meta.roteamento
    assert rot.escalado and rot.motivo == "confianca_baixa"
    assert [c.modelo for c in rot.chamadas] == [MODELO_HAIKU, MODELO_SONNET]
    esperado = custo_usd(MODELO_HAIKU, 1600, 280) + custo_usd(MODELO_SONNET, 1750, 480)
    assert r.meta.custo_usd == pytest.approx(esperado)
    # Não é a soma dos tokens multiplicada por um preço só:
    assert r.meta.custo_usd != pytest.approx(custo_usd(MODELO_SONNET, 3350, 760))
    assert (r.meta.tokens_entrada, r.meta.tokens_saida) == (3350, 760)
    assert r.meta.modelo == MODELO_SONNET


def test_critica_do_haiku_escala_por_prioridade(triagem_valida):
    triador, cliente = _auto([resposta(triagem_valida), resposta(triagem_valida)])
    r = triador.triar("Sistema fora do ar para todos")
    assert r.meta.roteamento.motivo == "prioridade"
    assert len(cliente.chamadas) == 2


def test_texto_longo_vai_direto_ao_sonnet(media_alta):
    triador, cliente = _auto([resposta(media_alta)])
    r = triador.triar("x" * 1501)

    assert [c["model"] for c in cliente.chamadas] == [MODELO_SONNET]
    rot = r.meta.roteamento
    assert (rot.modelo_inicial, rot.escalado, rot.motivo) == (MODELO_SONNET, False, "texto_longo")


def test_regra_por_parametro_tem_precedencia(media_alta, monkeypatch):
    monkeypatch.setenv("TRIAGEM_ROTEADOR_MAX_CARACTERES", "5000")
    triador, cliente = _auto([resposta(media_alta)], regra=RegraRoteamento(max_caracteres=10))
    triador.triar("texto com mais de dez caracteres")
    assert cliente.chamadas[0]["model"] == MODELO_SONNET


def test_falha_de_validacao_do_haiku_escala(media_alta):
    invalida = '{"categoria": "financeiro"}'
    triador, cliente = _auto([resposta(invalida), resposta(invalida), resposta(media_alta)])
    r = triador.triar("Oi")

    assert isinstance(r, ResultadoTriagem)
    assert r.meta.roteamento.motivo == "falha_validacao"
    assert [c["model"] for c in cliente.chamadas] == [MODELO_HAIKU, MODELO_HAIKU, MODELO_SONNET]


def test_escalada_que_falha_devolve_o_haiku_marcado(media_alta):
    baixa = media_alta.model_copy(update={"confianca": Confianca.baixa})
    erro = anthropic.RateLimitError("e", response=httpx2.Response(429, request=REQ), body=None)
    triador, _ = _auto([resposta(baixa, tokens_entrada=100, tokens_saida=10), erro])
    r = triador.triar("Oi")

    rot = r.meta.roteamento
    assert r.meta.modelo == MODELO_HAIKU
    assert rot.escalado is False
    assert rot.motivo == "confianca_baixa (falhou: limite_taxa)"
    # A chamada ao Sonnet que falhou continua registrada, com custo desconhecido explícito.
    assert [(c.modelo, c.status) for c in rot.chamadas] == [(MODELO_HAIKU, "ok"), (MODELO_SONNET, "erro")]
    assert rot.chamadas[1].custo_usd is None and rot.chamadas[1].custo_desconhecido
    assert r.meta.custo_usd == pytest.approx(custo_usd(MODELO_HAIKU, 100, 10))
    assert r.meta.custos_desconhecidos == 1


def test_haiku_ok_e_sonnet_timeout_conta_as_duas_chamadas(media_alta, tmp_path):
    from triagem.persistencia import Registro

    baixa = media_alta.model_copy(update={"confianca": Confianca.baixa})
    registro = Registro(tmp_path / "t.db")
    triador, cliente = _auto(
        [resposta(baixa, tokens_entrada=1600, tokens_saida=280), anthropic.APITimeoutError(request=REQ)],
        registro=registro,
    )
    r = triador.triar("Oi")

    assert len(cliente.chamadas) == 2
    assert r.meta.custo_usd == pytest.approx(custo_usd(MODELO_HAIKU, 1600, 280))  # piso, não zero
    assert r.meta.custos_desconhecidos == 1
    assert r.meta.roteamento.chamadas[1].erro_tipo == "timeout"
    s = registro.estatisticas()
    assert s["custo_total_usd"] == pytest.approx(r.meta.custo_usd)
    assert s["custos_desconhecidos"] == 1


def test_haiku_falha_validacao_2x_e_escala_soma_as_tres_chamadas(media_alta, tmp_path):
    from triagem.persistencia import Registro

    invalida = '{"categoria": "financeiro"}'
    registro = Registro(tmp_path / "t.db")
    triador, cliente = _auto([
        resposta(invalida, tokens_entrada=1500, tokens_saida=200),
        resposta(invalida, tokens_entrada=1550, tokens_saida=200),
        resposta(media_alta, tokens_entrada=1700, tokens_saida=450),
    ], registro=registro)
    r = triador.triar("Oi")

    assert [c["model"] for c in cliente.chamadas] == [MODELO_HAIKU, MODELO_HAIKU, MODELO_SONNET]
    rot = r.meta.roteamento
    assert [(c.modelo, c.status, c.erro_tipo) for c in rot.chamadas] == [
        (MODELO_HAIKU, "erro", "validacao"), (MODELO_SONNET, "ok", None)]
    esperado = custo_usd(MODELO_HAIKU, 3050, 400) + custo_usd(MODELO_SONNET, 1700, 450)
    assert r.meta.custo_usd == pytest.approx(esperado)
    assert (r.meta.tokens_entrada, r.meta.tokens_saida) == (4750, 850)
    assert r.meta.custos_desconhecidos == 0
    assert registro.estatisticas()["custo_total_usd"] == pytest.approx(esperado)


def test_falha_final_guarda_o_gasto_no_banco(tmp_path):
    from triagem.persistencia import Registro

    invalida = '{"categoria": "financeiro"}'
    registro = Registro(tmp_path / "t.db")
    triador = Triador(MODELO_SONNET, cliente=ClienteSimulado([
        resposta(invalida, tokens_entrada=1000, tokens_saida=100),
        resposta(invalida, tokens_entrada=1000, tokens_saida=100),
    ]), registro=registro)
    resultado = triador.triar_chamado(Chamado(texto="Oi"))

    assert isinstance(resultado, ErroTriagem)
    assert "gasto" not in resultado.model_dump(mode="json")  # formato do JSON de erro não muda
    s = registro.estatisticas()
    assert s["erros"] == 1 and s["custo_total_usd"] == pytest.approx(custo_usd(MODELO_SONNET, 2000, 200))


def test_erro_de_api_no_haiku_nao_escala():
    erro = anthropic.APITimeoutError(request=REQ)
    triador, cliente = _auto([erro])
    resultado = triador.triar_chamado(Chamado(id="A", texto="Oi"))
    assert isinstance(resultado, ErroTriagem) and resultado.erro.tipo == "timeout"
    assert len(cliente.chamadas) == 1


def test_modelo_sem_preco_deixa_o_custo_como_piso(media_alta):
    baixa = media_alta.model_copy(update={"confianca": Confianca.baixa})
    triador, _ = _auto([resposta(baixa, tokens_entrada=100, tokens_saida=10),
                        resposta(media_alta, model="claude-modelo-novo")])
    r = triador.triar("Oi")
    assert r.meta.roteamento.chamadas[1].custo_usd is None
    assert r.meta.custo_usd == pytest.approx(custo_usd(MODELO_HAIKU, 100, 10))
    assert r.meta.custos_desconhecidos == 1


def test_modo_fixo_tem_roteamento_fixo(media_alta):
    cliente = ClienteSimulado([resposta(media_alta)])
    r = Triador(MODELO_SONNET, cliente=cliente).triar("Oi")
    rot = r.meta.roteamento
    assert (rot.modo, rot.modelo_inicial, rot.escalado, len(rot.chamadas)) == ("fixo", MODELO_SONNET, False, 1)


def test_simulado_por_modelo_e_deterministico_e_responde_pelo_conteudo(exemplos):
    chamados = [Chamado.model_validate(e) for e in exemplos]
    cliente = ClienteSimulado.por_modelo(chamados)
    triador = Triador("auto", cliente=cliente)
    # Fora de ordem e com 1 ou 2 chamadas por chamado: responde pelo texto, não pela fila.
    for chamado in reversed(chamados):
        r = triador.triar_chamado(chamado)
        assert (r.categoria, r.prioridade) == (chamado.gabarito.categoria, chamado.gabarito.prioridade)
    outro = Triador("auto", cliente=ClienteSimulado.por_modelo(chamados))
    a, b = triador.triar_chamado(chamados[0]), outro.triar_chamado(chamados[0])
    assert a.meta.tokens_entrada == b.meta.tokens_entrada and a.meta.custo_usd == b.meta.custo_usd


def test_perfis_simulados_sonnet_gasta_mais_saida():
    chamado = Chamado(texto="Como exporto o relatório?")
    haiku = Triador(MODELO_HAIKU, cliente=ClienteSimulado.por_modelo([chamado])).triar_chamado(chamado)
    sonnet = Triador(MODELO_SONNET, cliente=ClienteSimulado.por_modelo([chamado])).triar_chamado(chamado)
    assert sonnet.meta.tokens_saida > haiku.meta.tokens_saida
    assert sonnet.confianca == Confianca.alta
