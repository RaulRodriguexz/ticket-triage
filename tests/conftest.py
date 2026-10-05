import json
from pathlib import Path

import pytest

from triagem.config import MODELO_HAIKU, MODELO_SONNET, Config
from triagem.modelos import Categoria, Chamado, Confianca, Prioridade, Triagem

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "docs" / "exemplos.json"


@pytest.fixture
def exemplos() -> list[dict]:
    return json.loads(EXEMPLOS.read_text(encoding="utf-8"))


@pytest.fixture
def chamado() -> Chamado:
    return Chamado(id="T-1", canal="email", assunto="Erro", texto="Ninguém consegue entrar, erro 503.")


@pytest.fixture
def triagem_valida() -> Triagem:
    return Triagem(
        categoria=Categoria.bug_tecnico,
        prioridade=Prioridade.critica,
        resumo="Sistema indisponível (erro 503) para todos os usuários.",
        resposta_sugerida="Olá! Recebemos seu chamado. Pode enviar um print do erro?",
        justificativa="Desempate 1: vários usuários sem acesso.",
        confianca=Confianca.alta,
    )


@pytest.fixture
def config_sonnet() -> Config:
    return Config(modelo=MODELO_SONNET, api_key="sk-teste")


@pytest.fixture
def config_haiku() -> Config:
    return Config(modelo=MODELO_HAIKU, api_key="sk-teste")


@pytest.fixture(autouse=True)
def ambiente_isolado(monkeypatch):
    """Nenhum teste depende da chave real, do modelo do ambiente nem grava em data/.

    Os testes de modo fixo (fila de respostas, uma chamada por chamado) rodam com o
    Sonnet fixo. Os testes do roteador passam modelo="auto" ou removem a variável.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TRIAGEM_MODELO", MODELO_SONNET)
    monkeypatch.setenv("TRIAGEM_DB", "off")
    for nome in ("TRIAGEM_DB_GUARDAR_TEXTO", "TRIAGEM_HASH_SEGREDO", "TRIAGEM_SIMULADO",
                 "TRIAGEM_ROTEADOR_MAX_CARACTERES", "TRIAGEM_ROTEADOR_CONFIANCA_MINIMA",
                 "TRIAGEM_ROTEADOR_PRIORIDADES"):
        monkeypatch.delenv(nome, raising=False)
