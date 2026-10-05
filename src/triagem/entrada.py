"""Lê e valida a entrada: --texto, ou --arquivo com um objeto ou uma lista."""

import json
from pathlib import Path

from pydantic import ValidationError

from .erros import ErroEntrada  # reexportado: mantém `entrada.ErroEntrada`
from .modelos import Chamado


def descrever_erro(erro: ValidationError) -> str:
    """Primeiro erro do Pydantic em uma frase: campo e motivo."""
    detalhe = erro.errors()[0]
    campo = ".".join(str(parte) for parte in detalhe["loc"]) or "chamado"
    return f"campo '{campo}': {detalhe['msg']}"


def de_texto(texto: str, assunto: str | None = None) -> Chamado:
    return _normalizar([{"texto": texto, "assunto": assunto}])[0]


def de_arquivo(caminho: str | Path) -> tuple[list[Chamado], bool]:
    """Devolve (chamados, era_lista)."""
    caminho = Path(caminho)
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErroEntrada(f"Arquivo não encontrado: {caminho}") from None
    except json.JSONDecodeError as erro:
        raise ErroEntrada(f"JSON inválido em {caminho}: {erro.msg} (linha {erro.lineno})") from None

    era_lista = isinstance(dados, list)
    itens = dados if era_lista else [dados]
    if not itens:
        raise ErroEntrada("A lista de chamados está vazia")
    if not all(isinstance(item, dict) for item in itens):
        raise ErroEntrada("Cada chamado deve ser um objeto JSON")
    return _normalizar(itens), era_lista


def _normalizar(itens: list[dict]) -> list[Chamado]:
    chamados = []
    for posicao, item in enumerate(itens, start=1):
        try:
            chamado = Chamado.model_validate(item)
        except ValidationError as erro:
            raise ErroEntrada(f"Chamado {posicao}: {descrever_erro(erro)}") from None
        if not chamado.id:
            chamado.id = f"CH-{posicao:03d}"
        chamados.append(chamado)
    return chamados
