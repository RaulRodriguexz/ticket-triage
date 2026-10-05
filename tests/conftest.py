import json
from pathlib import Path

import pytest

from triagem.config import MODELO_HAIKU, MODELO_PADRAO, Config
from triagem.modelos import Categoria, Chamado, Prioridade, Triagem

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
    )


@pytest.fixture
def config_sonnet() -> Config:
    return Config(modelo=MODELO_PADRAO, api_key="sk-teste")


@pytest.fixture
def config_haiku() -> Config:
    return Config(modelo=MODELO_HAIKU, api_key="sk-teste")


@pytest.fixture(autouse=True)
def sem_chave(monkeypatch):
    """Nenhum teste depende da chave real nem do modelo do ambiente."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TRIAGEM_MODELO", raising=False)
