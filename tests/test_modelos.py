import pytest
from pydantic import ValidationError

from triagem.modelos import Chamado, ResultadoTriagem, Triagem

VALIDO = {
    "categoria": "bug_tecnico",
    "prioridade": "critica",
    "resumo": "Sistema indisponível (erro 503) para todos os 12 usuários desde as 8h; loja parada.",
    "resposta_sugerida": "Olá! Recebemos seu chamado e entendemos que o sistema está indisponível.",
    "justificativa": "Todos os usuários sem acesso e operação de vendas parada, sem contorno.",
    "confianca": "alta",
}


def test_exemplo_do_prd_e_aceito():
    resultado = ResultadoTriagem(
        id="EX-01",
        meta={"modelo": "claude-sonnet-5-5", "latencia_ms": 2400, "tokens_entrada": 1350,
              "tokens_saida": 210, "custo_usd": 0.0048},
        **VALIDO,
    )
    assert resultado.categoria.value == "bug_tecnico"


@pytest.mark.parametrize("campo,valor", [("categoria", "financeiro"), ("prioridade", "urgente")])
def test_enum_invalido_e_rejeitado(campo, valor):
    with pytest.raises(ValidationError):
        Triagem(**{**VALIDO, campo: valor})


def test_resumo_com_121_caracteres_e_rejeitado():
    Triagem(**{**VALIDO, "resumo": "x" * 120})
    with pytest.raises(ValidationError):
        Triagem(**{**VALIDO, "resumo": "x" * 121})


def test_campo_extra_e_rejeitado():
    with pytest.raises(ValidationError):
        Triagem(**VALIDO, extra="não")


@pytest.mark.parametrize("campo", list(VALIDO))
def test_campos_obrigatorios(campo):
    dados = {k: v for k, v in VALIDO.items() if k != campo}
    with pytest.raises(ValidationError):
        Triagem(**dados)


def test_schema_enviado_a_api_e_fechado():
    schema = Triagem.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(VALIDO)


def test_resumo_deve_ter_uma_linha():
    with pytest.raises(ValidationError, match="uma linha"):
        Triagem(**{**VALIDO, "resumo": "primeira linha\nsegunda linha"})


@pytest.mark.parametrize("resposta", [
    "Olá! Vamos resolver isso em 24 horas.",
    "Retornaremos até amanhã com a solução.",
    "Resolvemos ainda hoje, pode ficar tranquilo.",
    "O prazo de 3 dias úteis será cumprido.",
    "Vamos estornar a cobrança duplicada.",
    "Faremos o reembolso do valor pago.",
    "Seu estorno já foi aprovado.",
    "Garantimos o reembolso integral.",
    "Reembolsaremos a segunda cobrança.",
])
def test_resposta_que_promete_prazo_ou_reembolso_e_rejeitada(resposta):
    with pytest.raises(ValidationError, match="não prometa"):
        Triagem(**{**VALIDO, "resposta_sugerida": resposta})


@pytest.mark.parametrize("resposta", [
    "Olá! Recebemos seu chamado. Pode nos enviar um print do erro e o e-mail da conta?",
    "Entendemos que houve uma cobrança duplicada. Nossa equipe vai analisar o caso e retornar com os próximos passos.",
    "Não podemos garantir o reembolso por aqui; o financeiro vai avaliar sua solicitação.",
    "Vamos verificar as permissões da sua conta.",
])
def test_resposta_sem_promessa_e_aceita(resposta):
    Triagem(**{**VALIDO, "resposta_sugerida": resposta})


def test_few_shots_do_prompt_nao_prometem_nada():
    from triagem.modelos import promessas
    from triagem.prompt import PASTA_PROMPTS

    sistema = (PASTA_PROMPTS / "sistema.md").read_text(encoding="utf-8")
    respostas = [linha for linha in sistema.splitlines() if linha.startswith("- resposta_sugerida:")]
    assert len(respostas) == 3
    assert all(not promessas(r) for r in respostas)


def test_chamado_rejeita_texto_vazio_e_longo():
    with pytest.raises(ValidationError):
        Chamado(texto="   ")
    with pytest.raises(ValidationError):
        Chamado(texto="a" * 8001)
