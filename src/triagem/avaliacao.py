"""Compara resultados com o gabarito e monta o relatório (funções puras)."""

import math
from collections import Counter
from collections.abc import Mapping, Sequence

from .modelos import (
    AvaliacaoMeta,
    BlocoComparacao,
    Chamado,
    ErroAvaliado,
    ErroTriagem,
    Gabarito,
    LinhaComparacao,
    Prioridade,
    Relatorio,
    RelatorioComparacao,
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
# Metas comparativas (PRD §5): roteador custa no máximo 60% do sempre-Sonnet
# e acerta pelo menos o mesmo que ele na mesma execução.
META_ECONOMIA_PCT = 40.0

AVISO_SIMULADO = (
    "Modo simulado: as respostas vêm do gabarito e a confiança e os tokens são fictícios "
    "por perfil de modelo. Este relatório demonstra o fluxo e a conta de custo do roteador; "
    "NÃO MEDE ACERTO NEM LATÊNCIA. Para decidir, rode sem --simulado."
)


def percentil(valores: Sequence[int], p: float) -> int | None:
    """Percentil por posição (nearest-rank); None sem valores."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[max(1, math.ceil(p / 100 * len(ordenados))) - 1]


def p95(valores: Sequence[int]) -> int:
    return percentil(valores, 95) or 0


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
    simulado: bool = False,
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
    por_confianca: dict[str, dict[str, int]] = {}
    iniciados_no_base = escalados = 0
    motivos: Counter[str] = Counter()

    for chamado, resultado in zip(chamados, resultados):
        esperado = chamado.gabarito
        if esperado is None:
            raise ValueError(f"Chamado {chamado.id} sem gabarito")

        if isinstance(resultado, ErroTriagem):
            # O que foi pago até a falha também entra no custo.
            if resultado.gasto is not None:
                if resultado.gasto.custo_usd is not None:
                    custos.append(resultado.gasto.custo_usd)
                custos_desconhecidos += resultado.gasto.custos_desconhecidos > 0
            invalidos += resultado.erro.tipo == "validacao"
            if esperado.prioridade == Prioridade.critica:
                criticos += 1
            erros.append(
                ErroAvaliado(id=chamado.id, esperado=esperado, obtido=None, erro=resultado.erro.tipo)
            )
            continue

        if resultado.meta.custo_usd is not None:
            custos.append(resultado.meta.custo_usd)
        custos_desconhecidos += resultado.meta.custos_desconhecidos > 0 or resultado.meta.custo_usd is None
        latencias.append(resultado.meta.latencia_ms)
        ok_categoria = resultado.categoria == esperado.categoria
        ok_prioridade = resultado.prioridade == esperado.prioridade
        acerto_categoria += ok_categoria
        acerto_prioridade += ok_prioridade
        conf = por_confianca.setdefault(resultado.confianca.value, {"total": 0, "acertos": 0})
        conf["total"] += 1
        conf["acertos"] += ok_categoria and ok_prioridade
        rot = resultado.meta.roteamento
        if rot and rot.modo == "auto" and rot.motivo != "texto_longo":
            iniciados_no_base += 1
            if rot.escalado:
                escalados += 1
                motivos[rot.motivo] += 1
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
    avisos = []
    if simulado:
        avisos.append(AVISO_SIMULADO)
    if nao_medidas:
        avisos.append(
            f"ATENÇÃO: {len(nao_medidas)} meta(s) NÃO MEDIDA(S): {', '.join(nao_medidas)}. "
            "Não reprovam, mas o aprovado não as cobre."
        )
    aviso = " ".join(avisos) or None

    return Relatorio(
        modelo=modelo,
        metas_nao_medidas=nao_medidas,
        aviso=aviso,
        simulado=simulado,
        taxa_escalonamento=round(escalados / iniciados_no_base, 4) if iniciados_no_base else None,
        escalonamentos_por_motivo=dict(motivos),
        acerto_por_confianca=por_confianca,
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


def comparar(relatorios: Mapping[str, Relatorio], referencia: str = "sonnet") -> RelatorioComparacao:
    """Lado a lado das estratégias; economia_pct é relativa ao custo médio da `referencia`."""
    base = relatorios.get(referencia)
    custo_base = base.custo_medio_usd if base else None
    linhas = []
    for nome, rel in relatorios.items():
        economia = None
        if custo_base and rel.custo_medio_usd is not None:
            economia = round((custo_base - rel.custo_medio_usd) / custo_base * 100, 1)
        linhas.append(
            LinhaComparacao(
                estrategia=nome,
                modelo=rel.modelo,
                total=rel.total,
                acerto_conjunto=rel.acerto_conjunto,
                acerto_categoria=rel.acerto_categoria,
                criticos_subestimados=rel.criticos_subestimados,
                aprovado=rel.aprovado,
                custo_total_usd=rel.custo_total_usd,
                custo_medio_usd=rel.custo_medio_usd,
                economia_pct=economia,
                latencia_p95_ms=rel.latencia_p95_ms,
                taxa_escalonamento=rel.taxa_escalonamento,
                escalonamentos_por_motivo=rel.escalonamentos_por_motivo,
            )
        )
    simulado = any(r.simulado for r in relatorios.values())
    bloco = _bloco_comparacao(relatorios, referencia)
    if simulado:
        bloco.nota = (
            "SIMULADO: confiança e tokens são fictícios; o veredito e a economia abaixo "
            "não valem para decidir. " + bloco.nota
        )
    return RelatorioComparacao(
        simulado=simulado,
        comparacao=bloco,
        estrategias=linhas,
        relatorios=dict(relatorios),
        aviso=AVISO_SIMULADO if simulado else None,
    )


def escalonamento_maximo(custo_base: float | None, custo_forte: float | None,
                         economia_minima_pct: float = META_ECONOMIA_PCT) -> float | None:
    """Maior taxa t tal que custo_base + t * custo_forte <= (1 - economia) * custo_forte.

    Modelo simples: todo chamado passa pelo modelo base e uma fração t também paga o forte
    (ignora os pré-roteados por texto longo). 0 se nem sem escalar daria a meta.
    """
    if not custo_base or not custo_forte:
        return None
    return round(max(0.0, 1 - economia_minima_pct / 100 - custo_base / custo_forte), 4)


def _bloco_comparacao(relatorios: Mapping[str, Relatorio], referencia: str) -> BlocoComparacao:
    roteador, sonnet, haiku = (relatorios.get(n) for n in ("roteador", referencia, "haiku"))
    metas: dict[str, AvaliacaoMeta] = {}
    economia = None
    if roteador is not None:
        metas["metas_individuais"] = AvaliacaoMeta(
            valor="aprovado" if roteador.aprovado else "reprovado",
            alvo="todas as metas do PRD §5 no roteador",
            status=_status(roteador.aprovado),
        )
        metas["acerto_conjunto"] = roteador.metas["acerto_conjunto"]
        metas["criticos_subestimados"] = roteador.metas["criticos_subestimados"]
    if roteador is not None and sonnet is not None:
        if roteador.custo_medio_usd is not None and sonnet.custo_medio_usd:
            economia = round((sonnet.custo_medio_usd - roteador.custo_medio_usd) / sonnet.custo_medio_usd * 100, 1)
        metas["economia_vs_sonnet"] = AvaliacaoMeta(
            valor="-" if economia is None else f"{economia}%",
            alvo=f">= {META_ECONOMIA_PCT:.0f}% (custo <= {100 - META_ECONOMIA_PCT:.0f}% do sempre-Sonnet)",
            status="nao_medida" if economia is None else _status(economia >= META_ECONOMIA_PCT),
        )
        metas["acerto_vs_sonnet"] = AvaliacaoMeta(
            valor=f"{roteador.acerto_conjunto} vs {sonnet.acerto_conjunto}",
            alvo=">= acerto conjunto do sempre-Sonnet",
            status=_status(roteador.acerto_conjunto >= sonnet.acerto_conjunto),
        )
    for nome in ("economia_vs_sonnet", "acerto_vs_sonnet"):
        metas.setdefault(nome, AvaliacaoMeta(valor="-", alvo="requer as estratégias roteador e sonnet",
                                             status="nao_medida"))

    maximo = escalonamento_maximo(
        haiku.custo_medio_usd if haiku else None, sonnet.custo_medio_usd if sonnet else None
    )
    taxa = roteador.taxa_escalonamento if roteador else None
    if maximo is None:
        nota = "Sem custo médio de Haiku e Sonnet para estimar o escalonamento máximo."
    else:
        nota = (
            f"Com os custos médios medidos, a economia de {META_ECONOMIA_PCT:.0f}% exige escalar "
            f"no máximo {maximo:.1%} dos chamados (medido: "
            f"{'-' if taxa is None else f'{taxa:.1%}'}). Para calibrar, ajuste o limiar de confiança "
            "e as prioridades escaláveis."
        )
    return BlocoComparacao(
        aprovado=roteador is not None and all(m.status != "falhou" for m in metas.values()),
        metas=metas,
        economia_pct=economia,
        taxa_escalonamento=taxa,
        escalonamentos_por_motivo=roteador.escalonamentos_por_motivo if roteador else {},
        escalonamento_maximo_para_meta=maximo,
        nota=nota,
    )
