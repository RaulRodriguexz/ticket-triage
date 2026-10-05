"""Compara resultados com o gabarito e monta o relatório (funções puras)."""

import math
from collections.abc import Sequence

from .modelos import (
    AvaliacaoMeta,
    Chamado,
    ErroAvaliado,
    ErroTriagem,
    Gabarito,
    Prioridade,
    Relatorio,
    ResultadoTriagem,
    StatusMeta,
)

Resultado = ResultadoTriagem | ErroTriagem
SUBESTIMADAS = {Prioridade.media, Prioridade.baixa}

# Metas do PRD §5, em proporção do total para não depender do tamanho do gabarito.
META_ACERTO_CONJUNTO = 0.8
META_ACERTO_CATEGORIA = 0.9
META_ESTABILIDADE = 0.9
META_P95_MS = 10_000
META_CUSTO_MEDIO_USD = 0.01


def p95(valores: Sequence[int]) -> int:
    """Percentil 95 por posição (nearest-rank)."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    return ordenados[math.ceil(0.95 * len(ordenados)) - 1]


def _status(ok: bool) -> StatusMeta:
    return "ok" if ok else "falhou"


def _proporcao(valor: int, total: int, minimo: float) -> AvaliacaoMeta:
    return AvaliacaoMeta(
        valor=f"{valor}/{total}",
        alvo=f">= {minimo:.0%}",
        status=_status(total > 0 and valor / total >= minimo),
    )


def avaliar(
    chamados: Sequence[Chamado],
    resultados: Sequence[Resultado],
    modelo: str,
    estabilidade: int | None = None,
) -> Relatorio:
    """`estabilidade` vem de `estabilidade()` quando houve mais de uma rodada."""
    if len(chamados) != len(resultados):
        raise ValueError("chamados e resultados devem ter o mesmo tamanho")

    total = len(chamados)
    acerto_conjunto = acerto_categoria = acerto_prioridade = criticos = 0
    invalidos = custos_desconhecidos = 0
    erros: list[ErroAvaliado] = []
    custos: list[float] = []
    latencias: list[int] = []

    for chamado, resultado in zip(chamados, resultados):
        esperado = chamado.gabarito
        if esperado is None:
            raise ValueError(f"Chamado {chamado.id} sem gabarito")

        if isinstance(resultado, ErroTriagem):
            invalidos += resultado.erro.tipo == "validacao"
            if esperado.prioridade == Prioridade.critica:
                criticos += 1
            erros.append(
                ErroAvaliado(id=chamado.id, esperado=esperado, obtido=None, erro=resultado.erro.tipo)
            )
            continue

        if resultado.meta.custo_usd is None:
            custos_desconhecidos += 1
        else:
            custos.append(resultado.meta.custo_usd)
        latencias.append(resultado.meta.latencia_ms)
        ok_categoria = resultado.categoria == esperado.categoria
        ok_prioridade = resultado.prioridade == esperado.prioridade
        acerto_categoria += ok_categoria
        acerto_prioridade += ok_prioridade
        if ok_categoria and ok_prioridade:
            acerto_conjunto += 1
        else:
            obtido = Gabarito(categoria=resultado.categoria, prioridade=resultado.prioridade)
            erros.append(ErroAvaliado(id=chamado.id, esperado=esperado, obtido=obtido))
        if esperado.prioridade == Prioridade.critica and resultado.prioridade in SUBESTIMADAS:
            criticos += 1

    custo_total = sum(custos)
    custo_medio = custo_total / len(custos) if custos else None
    latencia_p95 = p95(latencias) if latencias else None

    metas = {
        "acerto_conjunto": _proporcao(acerto_conjunto, total, META_ACERTO_CONJUNTO),
        "acerto_categoria": _proporcao(acerto_categoria, total, META_ACERTO_CATEGORIA),
        "criticos_subestimados": AvaliacaoMeta(
            valor=str(criticos), alvo="0", status=_status(criticos == 0)
        ),
        "schema_valido": AvaliacaoMeta(
            valor=f"{total - invalidos}/{total}", alvo="100%", status=_status(invalidos == 0)
        ),
        "estabilidade": (
            _proporcao(estabilidade, total, META_ESTABILIDADE)
            if estabilidade is not None
            else AvaliacaoMeta(valor="-", alvo=f">= {META_ESTABILIDADE:.0%} (use --repeticoes 3)", status="nao_medida")
        ),
        "latencia_p95_ms": AvaliacaoMeta(
            valor="-" if latencia_p95 is None else str(latencia_p95),
            alvo=f"<= {META_P95_MS}",
            status="nao_medida" if latencia_p95 is None else _status(latencia_p95 <= META_P95_MS),
        ),
        "custo_medio_usd": AvaliacaoMeta(
            valor="-" if custo_medio is None else f"{custo_medio:.6f}",
            alvo=f"<= {META_CUSTO_MEDIO_USD}",
            status=(
                "nao_medida"
                if custo_medio is None or custos_desconhecidos
                else _status(custo_medio <= META_CUSTO_MEDIO_USD)
            ),
        ),
    }

    nao_medidas = [nome for nome, meta in metas.items() if meta.status == "nao_medida"]
    aviso = None
    if nao_medidas:
        aviso = (
            f"ATENÇÃO: {len(nao_medidas)} meta(s) NÃO MEDIDA(S): {', '.join(nao_medidas)}. "
            "Não reprovam, mas o aprovado não as cobre."
        )

    return Relatorio(
        modelo=modelo,
        metas_nao_medidas=nao_medidas,
        aviso=aviso,
        total=total,
        acerto_conjunto=acerto_conjunto,
        acerto_categoria=acerto_categoria,
        acerto_prioridade=acerto_prioridade,
        criticos_subestimados=criticos,
        # "nao_medida" não reprova, mas aparece no relatório.
        aprovado=all(meta.status != "falhou" for meta in metas.values()),
        metas=metas,
        erros=erros,
        custo_total_usd=round(custo_total, 6),
        custo_medio_usd=None if custo_medio is None else round(custo_medio, 6),
        custos_desconhecidos=custos_desconhecidos,
        latencia_p95_ms=latencia_p95,
        estabilidade=estabilidade,
    )


def estabilidade(rodadas: Sequence[Sequence[Resultado]]) -> int:
    """Quantos chamados tiveram categoria e prioridade idênticas em todas as rodadas."""
    estaveis = 0
    for resultados in zip(*rodadas):
        if any(isinstance(r, ErroTriagem) for r in resultados):
            continue
        if len({(r.categoria, r.prioridade) for r in resultados}) == 1:
            estaveis += 1
    return estaveis
