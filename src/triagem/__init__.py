"""Triagem automática de chamados de suporte (POC).

Superfície estável: os nomes em `__all__`. O resto é interno e pode mudar.
"""

from .erros import ErroConfiguracao, ErroEntrada, FalhaTriagem
from .modelos import Categoria, Chamado, Confianca, Meta, Prioridade, ResultadoTriagem, Triagem
from .roteador import RegraRoteamento
from .servico import Triador, triar

__version__ = "0.3.0"

__all__ = [
    "triar",
    "Triador",
    "FalhaTriagem",
    "ErroConfiguracao",
    "ErroEntrada",
    "ResultadoTriagem",
    "Triagem",
    "Meta",
    "Categoria",
    "Prioridade",
    "Confianca",
    "RegraRoteamento",
    "Chamado",
    "__version__",
]
