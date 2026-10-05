"""Detecta promessas da empresa na resposta sugerida (RF-06, RF-08).

Só conta o compromisso da empresa em 1ª pessoa ("vamos estornar", "corrigiremos",
"retornaremos em 24 horas"). Um prazo citado do cliente ("precisa ser corrigida
até amanhã", "vence em 2 dias") não é promessa.
"""

import re

_F = re.IGNORECASE

# Quem se compromete: nós (ou a equipe) no presente/futuro.
_MARCADOR = (
    r"(?:vamos|iremos|vou|irei|faremos|farei|garantimos|garanto|prometemos|prometo"
    r"|podemos\s+garantir|(?:nossa|a)\s+equipe(?:\s+\w+)?\s+(?:vai|irá))"
)
_ACAO = r"(?:estorn|reembols|ressarc|devolv|devoluç|corrig|correç|consert|resolv|soluc)\w*"

# "vamos fazer o estorno", "garantimos o reembolso", "vamos corrigir"
_COMPROMISSO_ACAO = re.compile(rf"\b{_MARCADOR}\s+(?:\w+\s+){{0,2}}?{_ACAO}", _F)
# "corrigiremos", "estornaremos", "resolverei"
_FUTURO_ACAO = re.compile(
    r"\b(?:corrig|estorn|reembols|ressarc|devolv|resolv|consert|soluciona)\w*(?:remos|rei)\b", _F
)
# "a fatura será corrigida", "o valor vai ser estornado"
_PASSIVA_ACAO = re.compile(
    r"\b(?:será|serão|vai\s+ser|vão\s+ser)\s+(?:\w+\s+)?"
    r"(?:corrigid|estornad|reembolsad|ressarcid|devolvid|resolvid|consertad|solucionad)\w*",
    _F,
)
# "seu estorno já foi aprovado", "o reembolso está garantido"
_CONFIRMA_REEMBOLSO = re.compile(
    r"\b(?:reembolso|estorno|ressarcimento|devolução)\s+(?:já\s+)?(?:foi|está|será|vai\s+ser)\s+"
    r"(?:\w+\s+)?(?:aprovad|garantid|confirmad|feit|realizad|efetuad|processad|providenciad)\w*",
    _F,
)

_PRAZO = re.compile(
    r"\b(?:(?:em|dentro\s+de|nas\s+próximas|nos\s+próximos|até)\s+(?:até\s+)?"
    r"(?:\d+|uma?|duas?|dois|três|algumas|poucas)\s*(?:minutos?|horas?|h|dias?(?:\s+úteis)?|semanas?)"
    r"|até\s+(?:hoje|amanhã|o\s+(?:fim|final)\s+do\s+dia|segunda|terça|quarta|quinta|sexta|sábado|domingo)"
    r"|ainda\s+hoje|hoje\s+mesmo|amanhã\s+mesmo|prazo\s+(?:máximo\s+)?de\s+\d+)\b",
    _F,
)
# Um prazo só é promessa se estiver perto de um compromisso nosso.
_COMPROMISSO_PRAZO = re.compile(
    rf"\b(?:{_MARCADOR}|\w+(?:remos|rei)"
    r"|retornamos|respondemos|enviamos|resolvemos|corrigimos|estornamos|devolvemos"
    r"|(?:você|vocês)\s+(?:terá|terão|receberá|receberão)"
    r"|será\s+cumprid\w*|está\s+garantid\w*)\b",
    _F,
)
# Palavras que indicam que o prazo é do cliente, não nosso.
_CONTEXTO_CLIENTE = re.compile(
    r"\b(?:venc\w*|prazo|precis\w*|deve|devem|tem\s+que|têm\s+que|pag\w*|limite)\b[^.!?]{0,25}$", _F
)
_NEGACAO = re.compile(r"\bnão\s+(?:\w+\s+)?$", _F)
_FRASES = re.compile(r"(?<=[.!?])\s+|\n+")
DISTANCIA_MAX_PALAVRAS = 6


def _tipo(trecho: str) -> str:
    return "reembolso" if re.search(r"estorn|reembols|ressarc|devol", trecho, _F) else "correção"


def _negado(frase: str, inicio: int) -> bool:
    return bool(_NEGACAO.search(frase[:inicio]))


def _palavras_entre(frase: str, a: re.Match, b: re.Match) -> int:
    esquerda, direita = sorted((a, b), key=lambda m: m.start())
    return len(frase[esquerda.end():direita.start()].split())


def _promessas_de_acao(frase: str) -> list[tuple[str, str]]:
    achados = []
    for padrao in (_COMPROMISSO_ACAO, _FUTURO_ACAO, _PASSIVA_ACAO, _CONFIRMA_REEMBOLSO):
        for m in padrao.finditer(frase):
            if _negado(frase, m.start()) or re.search(r"\bajud", m.group(0), _F):
                continue  # "não vamos estornar", "vamos ajudar você a resolver"
            achados.append((_tipo(m.group(0)), m.group(0)))
    return achados


def _promessas_de_prazo(frase: str) -> list[tuple[str, str]]:
    achados = []
    compromissos = [
        m for m in _COMPROMISSO_PRAZO.finditer(frase) if not _negado(frase, m.start())
    ]
    for prazo in _PRAZO.finditer(frase):
        if _CONTEXTO_CLIENTE.search(frase[: prazo.start()]):
            continue
        if any(_palavras_entre(frase, prazo, c) <= DISTANCIA_MAX_PALAVRAS for c in compromissos):
            achados.append(("prazo", prazo.group(0)))
    return achados


def promessas(texto: str) -> list[tuple[str, str]]:
    """Devolve (tipo, trecho) de cada promessa: 'prazo', 'reembolso' ou 'correção'."""
    achados = []
    for frase in _FRASES.split(texto):
        achados += _promessas_de_acao(frase) + _promessas_de_prazo(frase)
    return achados
