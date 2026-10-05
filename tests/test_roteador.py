import pytest

from triagem.config import MODELO_HAIKU, MODELO_SONNET
from triagem.erros import ErroConfiguracao
from triagem.modelos import (
    Chamado,
    Confianca,
    DetalheErro,
    ErroTriagem,
    Meta,
    Prioridade,
    ResultadoTriagem,
)
from triagem.roteador import RegraRoteamento, decidir_modelo, deve_escalar

REGRA = RegraRoteamento()


def _resultado(confianca="alta", prioridade="media"):
    return ResultadoTriagem(
        id="R", categoria="acesso", prioridade=prioridade, resumo="r", resposta_sugerida="r",
        justificativa="j", confianca=confianca,
        meta=Meta(modelo=MODELO_HAIKU, latencia_ms=1, tokens_entrada=1, tokens_saida=1, custo_usd=0.0),
    )


def _erro(tipo):
    return ErroTriagem(id="R", erro=DetalheErro(tipo=tipo, mensagem="x"))


def test_padroes_da_regra():
    assert REGRA.modelo_base == MODELO_HAIKU and REGRA.modelo_forte == MODELO_SONNET
    assert REGRA.max_caracteres == 1500
    assert REGRA.confianca_minima == Confianca.media
    assert REGRA.prioridades_escalaveis == frozenset({Prioridade.critica})


@pytest.mark.parametrize("tamanho,modelo,motivo", [
    (1499, MODELO_HAIKU, "padrao"),
    (1500, MODELO_HAIKU, "padrao"),       # no limite: ainda não é longo
    (1501, MODELO_SONNET, "texto_longo"),
])
def test_pre_roteamento_por_tamanho_do_texto(tamanho, modelo, motivo):
    decisao = decidir_modelo(Chamado(texto="x" * tamanho), REGRA)
    assert (decisao.modelo, decisao.motivo) == (modelo, motivo)


def test_assunto_nao_conta_no_tamanho():
    decisao = decidir_modelo(Chamado(texto="x" * 1500, assunto="y" * 100), REGRA)
    assert decisao.motivo == "padrao"


@pytest.mark.parametrize("confianca,minima,escala", [
    ("alta", "media", False),
    ("media", "media", False),
    ("baixa", "media", True),
    ("alta", "alta", False),
    ("media", "alta", True),
    ("baixa", "alta", True),
    ("baixa", "baixa", False),
])
def test_confianca_contra_limiar(confianca, minima, escala):
    regra = RegraRoteamento(confianca_minima=Confianca(minima))
    motivo = deve_escalar(_resultado(confianca=confianca), regra)
    assert (motivo == "confianca_baixa") is escala


@pytest.mark.parametrize("prioridade,escalaveis,escala", [
    ("critica", {"critica"}, True),
    ("alta", {"critica"}, False),
    ("alta", {"critica", "alta"}, True),
    ("media", {"critica", "alta"}, False),
])
def test_prioridade_escalavel(prioridade, escalaveis, escala):
    regra = RegraRoteamento(prioridades_escalaveis=frozenset(Prioridade(p) for p in escalaveis))
    assert (deve_escalar(_resultado(prioridade=prioridade), regra) == "prioridade") is escala


def test_confianca_baixa_tem_precedencia_sobre_prioridade():
    assert deve_escalar(_resultado(confianca="baixa", prioridade="critica"), REGRA) == "confianca_baixa"


def test_falha_de_validacao_escala_e_pode_ser_desligada():
    assert deve_escalar(_erro("validacao"), REGRA) == "falha_validacao"
    assert deve_escalar(_erro("validacao"), RegraRoteamento(escalar_em_falha_validacao=False)) is None


@pytest.mark.parametrize("tipo", ["limite_taxa", "timeout", "conexao", "recusa", "autenticacao", "api"])
def test_erros_de_api_e_recusa_nao_escalam(tipo):
    assert deve_escalar(_erro(tipo), REGRA) is None


def test_regra_do_ambiente():
    regra = RegraRoteamento.do_ambiente({
        "TRIAGEM_ROTEADOR_MAX_CARACTERES": "800",
        "TRIAGEM_ROTEADOR_CONFIANCA_MINIMA": "alta",
        "TRIAGEM_ROTEADOR_PRIORIDADES": "critica, alta",
    })
    assert regra.max_caracteres == 800
    assert regra.confianca_minima == Confianca.alta
    assert regra.prioridades_escalaveis == frozenset({Prioridade.critica, Prioridade.alta})
    assert RegraRoteamento.do_ambiente({}) == REGRA
    assert RegraRoteamento.do_ambiente({"TRIAGEM_ROTEADOR_PRIORIDADES": ""}).prioridades_escalaveis == frozenset()


@pytest.mark.parametrize("variavel,valor", [
    ("TRIAGEM_ROTEADOR_MAX_CARACTERES", "abc"),
    ("TRIAGEM_ROTEADOR_MAX_CARACTERES", "0"),
    ("TRIAGEM_ROTEADOR_MAX_CARACTERES", "-5"),
    ("TRIAGEM_ROTEADOR_CONFIANCA_MINIMA", "total"),
    ("TRIAGEM_ROTEADOR_PRIORIDADES", "critica,urgente"),
])
def test_ambiente_invalido_vira_erro_de_configuracao(variavel, valor):
    with pytest.raises(ErroConfiguracao, match=variavel):
        RegraRoteamento.do_ambiente({variavel: valor})
