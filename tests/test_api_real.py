"""Teste manual contra a API real. Pulado sem ANTHROPIC_API_KEY."""

import os

import pytest

pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requer ANTHROPIC_API_KEY"),
]


def test_chamada_real():
    from triagem import config as cfg
    from triagem.classificador import classificar
    from triagem.entrada import de_texto
    from triagem.modelos import ResultadoTriagem

    config = cfg.carregar()
    chamado = de_texto("Como exporto o relatório de vendas para Excel?")
    resultado = classificar(chamado, cfg.criar_cliente(config), config)
    assert isinstance(resultado, ResultadoTriagem)
