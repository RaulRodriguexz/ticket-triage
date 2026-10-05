"""Monta o system prompt e a mensagem do usuário a partir de triagem/prompts/."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from .modelos import Chamado

# Dentro do pacote (package-data), para funcionar também quando instalado.
PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
MARCADOR_CRITERIOS = "{{CRITERIOS}}"
DIAS_DA_SEMANA = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
)


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """sistema.md com criterios.md inserido no marcador (fonte única, RF-07)."""
    sistema = (PASTA_PROMPTS / "sistema.md").read_text(encoding="utf-8")
    criterios = (PASTA_PROMPTS / "criterios.md").read_text(encoding="utf-8")
    return sistema.replace(MARCADOR_CRITERIOS, criterios.strip())


def linha_data(data_referencia: date | None) -> str:
    if data_referencia is None:
        return "Data de referência: não informada."
    dia = DIAS_DA_SEMANA[data_referencia.weekday()]
    return f"Data de referência (hoje): {data_referencia:%d/%m/%Y}, {dia}."


def mensagem_usuario(chamado: Chamado, aviso: str | None = None) -> str:
    """Data (dado do sistema, fora do bloco) + o chamado delimitado como dado do cliente."""
    texto = (
        f"{linha_data(chamado.data_referencia)}\n\n"
        "<chamado>\n"
        f"canal: {chamado.canal or 'não informado'}\n"
        f"assunto: {chamado.assunto or 'não informado'}\n"
        "texto:\n"
        f"{chamado.texto}\n"
        "</chamado>"
    )
    if aviso:
        texto += f"\n\n{aviso}"
    return texto


def montar(chamado: Chamado, aviso: str | None = None) -> tuple[str, str]:
    return system_prompt(), mensagem_usuario(chamado, aviso)
