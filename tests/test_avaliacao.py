import pytest

from triagem.avaliacao import avaliar, estabilidade, p95
from triagem.modelos import Chamado, DetalheErro, ErroTriagem, Meta, ResultadoTriagem


def _lote(n: int) -> list[Chamado]:
    """n chamados sintéticos; o primeiro é crítico, os demais baixa."""
    return [
        Chamado(
            id=f"S-{i:02d}",
            texto="t",
            gabarito={"categoria": "bug_tecnico", "prioridade": "critica" if i == 0 else "baixa"},
        )
        for i in range(n)
    ]


def _resultado(chamado, categoria=None, prioridade=None, latencia=1000, custo=0.005):
    g = chamado.gabarito
    return ResultadoTriagem(
        id=chamado.id,
        categoria=categoria or g.categoria,
        prioridade=prioridade or g.prioridade,
        resumo="r", resposta_sugerida="r", justificativa="r", confianca="alta",
        meta=Meta(modelo="m", latencia_ms=latencia, tokens_entrada=1, tokens_saida=1, custo_usd=custo),
    )


def _corretos(chamados, **kwargs):
    return [_resultado(c, **kwargs) for c in chamados]


def test_tudo_certo_aprova_e_mostra_cada_meta():
    chamados = _lote(10)
    rel = avaliar(chamados, _corretos(chamados), "m", estabilidade=10)

    assert rel.aprovado
    assert set(rel.metas) == {
        "acerto_conjunto", "acerto_categoria", "criticos_subestimados", "schema_valido",
        "estabilidade", "latencia_p95_ms", "custo_medio_usd",
    }
    assert all(meta.status == "ok" for meta in rel.metas.values())
    assert rel.metas["acerto_conjunto"].valor == "10/10"


@pytest.mark.parametrize("total,erros_prioridade,aprovado", [
    (10, 2, True),    # 8/10 = 80%
    (10, 3, False),   # 7/10
    (13, 2, True),    # 11/13 = 84,6%
    (13, 3, False),   # 10/13 = 76,9%: com 13 exemplos, 10 acertos não bastam
])
def test_acerto_conjunto_usa_proporcao(total, erros_prioridade, aprovado):
    chamados = _lote(total)
    resultados = _corretos(chamados)
    for i in range(1, 1 + erros_prioridade):  # erra prioridade de não críticos
        resultados[i] = _resultado(chamados[i], prioridade="media")

    rel = avaliar(chamados, resultados, "m")
    assert rel.metas["acerto_conjunto"].status == ("ok" if aprovado else "falhou")
    assert rel.aprovado is aprovado


def test_acerto_categoria_abaixo_de_90_por_cento_reprova():
    chamados = _lote(13)
    resultados = _corretos(chamados)
    resultados[1] = _resultado(chamados[1], categoria="outro")
    resultados[2] = _resultado(chamados[2], categoria="outro")  # 11/13 = 84,6%

    rel = avaliar(chamados, resultados, "m")
    assert rel.metas["acerto_conjunto"].status == "ok"
    assert rel.metas["acerto_categoria"].status == "falhou"
    assert not rel.aprovado


def test_critico_subestimado_reprova():
    chamados = _lote(10)
    resultados = _corretos(chamados)
    resultados[0] = _resultado(chamados[0], prioridade="media")

    rel = avaliar(chamados, resultados, "m")
    assert rel.criticos_subestimados == 1
    assert rel.metas["criticos_subestimados"].status == "falhou"
    assert not rel.aprovado


def test_critico_como_alta_nao_conta_como_subestimado():
    chamados = _lote(10)
    resultados = _corretos(chamados)
    resultados[0] = _resultado(chamados[0], prioridade="alta")
    assert avaliar(chamados, resultados, "m").criticos_subestimados == 0


def test_erro_conta_como_errado_e_como_critico():
    chamados = _lote(10)
    resultados = _corretos(chamados)
    resultados[0] = ErroTriagem(id="S-00", erro=DetalheErro(tipo="validacao", mensagem="x"))

    rel = avaliar(chamados, resultados, "m")
    assert rel.acerto_conjunto == 9
    assert rel.criticos_subestimados == 1
    assert rel.metas["schema_valido"].status == "falhou"
    assert rel.erros[0].obtido is None and rel.erros[0].erro == "validacao"


def test_estabilidade_baixa_reprova_e_ausente_fica_nao_medida():
    chamados = _lote(13)
    assert not avaliar(chamados, _corretos(chamados), "m", estabilidade=11).aprovado  # 84,6%
    sem = avaliar(chamados, _corretos(chamados), "m")
    assert sem.metas["estabilidade"].status == "nao_medida"
    assert sem.aprovado  # não medida não reprova, mas fica visível


def test_latencia_p95_acima_de_10s_reprova():
    chamados = _lote(10)
    rel = avaliar(chamados, _corretos(chamados, latencia=10_001), "m")
    assert rel.metas["latencia_p95_ms"].status == "falhou"
    assert not rel.aprovado


def test_custo_medio_acima_de_1_centavo_reprova():
    chamados = _lote(10)
    rel = avaliar(chamados, _corretos(chamados, custo=0.011), "m")
    assert rel.metas["custo_medio_usd"].status == "falhou"
    assert not rel.aprovado
    assert rel.custo_total_usd == pytest.approx(0.11)


def test_custo_desconhecido_fica_nao_medido():
    chamados = _lote(10)
    resultados = _corretos(chamados)
    resultados[3] = _resultado(chamados[3], custo=None)

    rel = avaliar(chamados, resultados, "m")
    assert rel.custos_desconhecidos == 1
    assert rel.custo_total_usd == pytest.approx(0.045)
    assert rel.metas["custo_medio_usd"].status == "nao_medida"


def test_p95_nearest_rank():
    assert p95(list(range(1, 21))) == 19
    assert p95([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]) == 1000
    assert p95([5]) == 5
    assert p95([]) == 0


def test_estabilidade():
    chamados = _lote(13)
    base = _corretos(chamados)
    variada = list(base)
    variada[1] = _resultado(chamados[1], prioridade="media")
    assert estabilidade([base, base, base]) == 13
    assert estabilidade([base, variada, base]) == 12


def _relatorio(custo, acertos=10, critico=False, taxa=None, total=10):
    chamados = _lote(total)
    resultados = _corretos(chamados, custo=custo)
    for i in range(1, 1 + total - acertos):
        resultados[i] = _resultado(chamados[i], prioridade="media", custo=custo)
    if critico:
        resultados[0] = _resultado(chamados[0], prioridade="baixa", custo=custo)
    rel = avaliar(chamados, resultados, "m")
    return rel.model_copy(update={"taxa_escalonamento": taxa})


def _comparar(roteador, sonnet=None, haiku=None):
    from triagem.avaliacao import comparar

    return comparar({"roteador": roteador, "sonnet": sonnet or _relatorio(0.010),
                     "haiku": haiku or _relatorio(0.003)}).comparacao


def test_comparacao_aprovada_com_economia_e_acerto():
    bloco = _comparar(_relatorio(0.006, taxa=0.3))  # 40% mais barato, mesmo acerto
    assert bloco.economia_pct == 40.0
    assert {n: m.status for n, m in bloco.metas.items()} == {
        "metas_individuais": "ok", "acerto_conjunto": "ok", "criticos_subestimados": "ok",
        "economia_vs_sonnet": "ok", "acerto_vs_sonnet": "ok",
    }
    assert bloco.aprovado


def test_economia_abaixo_de_40_reprova():
    bloco = _comparar(_relatorio(0.0079))  # 21% de economia
    assert bloco.metas["economia_vs_sonnet"].status == "falhou"
    assert not bloco.aprovado


def test_acerto_abaixo_do_sonnet_reprova_mesmo_barato():
    bloco = _comparar(_relatorio(0.004, acertos=8), sonnet=_relatorio(0.010, acertos=9))
    assert bloco.metas["acerto_conjunto"].status == "ok"      # 80% ainda atinge a meta absoluta
    assert bloco.metas["acerto_vs_sonnet"].status == "falhou"
    assert not bloco.aprovado


def test_critico_subestimado_no_roteador_reprova():
    bloco = _comparar(_relatorio(0.004, critico=True))
    assert bloco.metas["criticos_subestimados"].status == "falhou"
    assert not bloco.aprovado


def test_escalonamento_maximo_para_meta():
    from triagem.avaliacao import escalonamento_maximo

    # Haiku custa 30% do Sonnet: 0,3 + t <= 0,6  →  t <= 30%.
    assert escalonamento_maximo(0.003, 0.010) == pytest.approx(0.3)
    assert escalonamento_maximo(0.007, 0.010) == 0.0   # nem sem escalar daria 40%
    assert escalonamento_maximo(None, 0.010) is None
    assert _comparar(_relatorio(0.006, taxa=0.5)).escalonamento_maximo_para_meta == pytest.approx(0.3)


def test_gasto_de_triagem_com_erro_entra_no_custo():
    from triagem.modelos import Meta as MetaModelo

    chamados = _lote(10)
    resultados = _corretos(chamados, custo=0.005)
    gasto = MetaModelo(modelo="m", latencia_ms=1, tokens_entrada=1, tokens_saida=1, custo_usd=0.004)
    resultados[1] = ErroTriagem(id="S-01", erro=DetalheErro(tipo="validacao", mensagem="x"), gasto=gasto)
    sem_usage = gasto.model_copy(update={"custo_usd": None, "custos_desconhecidos": 1})
    resultados[2] = ErroTriagem(id="S-02", erro=DetalheErro(tipo="timeout", mensagem="x"), gasto=sem_usage)

    rel = avaliar(chamados, resultados, "m")
    assert rel.custo_total_usd == pytest.approx(8 * 0.005 + 0.004)
    assert rel.custos_desconhecidos == 1
    assert rel.metas["custo_medio_usd"].status == "nao_medida"
