import json

from conftest import EXEMPLOS
from triagem.cli import main
from triagem.config import MODELO_HAIKU


def _json(capsys):
    return json.loads(capsys.readouterr().out)


def test_classificar_texto_simulado(capsys):
    codigo = main(["classificar", "--texto", "Como exporto o relatório?", "--simulado"])
    saida = _json(capsys)

    assert codigo == 0
    assert saida["id"] == "CH-001"
    assert {"categoria", "prioridade", "resumo", "resposta_sugerida", "justificativa", "meta"} <= set(saida)


def test_classificar_lote_devolve_lista(tmp_path, exemplos):
    destino = tmp_path / "saida.json"
    codigo = main(["classificar", "--arquivo", str(EXEMPLOS), "--simulado", "--saida", str(destino)])
    saida = json.loads(destino.read_text(encoding="utf-8"))

    assert codigo == 0
    assert isinstance(saida, list) and len(saida) == len(exemplos)
    assert all("gabarito" not in item for item in saida)


def test_avaliar_simulado_aprovado(capsys, exemplos):
    codigo = main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado", "--modelo", MODELO_HAIKU])
    rel = _json(capsys)

    assert codigo == 0
    assert rel["aprovado"] is True
    assert rel["acerto_conjunto"] == rel["total"] == len(exemplos)
    assert rel["modelo"] == MODELO_HAIKU
    assert rel["metas"]["estabilidade"]["status"] == "nao_medida"
    assert all(m["status"] in ("ok", "nao_medida") for m in rel["metas"].values())


def test_avaliar_com_repeticoes(capsys, exemplos):
    codigo = main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado", "--repeticoes", "3"])
    rel = _json(capsys)
    assert codigo == 0
    assert rel["estabilidade"] == len(exemplos)
    assert all(m["status"] == "ok" for m in rel["metas"].values())


def test_sem_chave_e_sem_simulado_sai_com_2(capsys):
    codigo = main(["classificar", "--texto", "oi"])
    assert codigo == 2
    assert _json(capsys) == {"erro": {"tipo": "configuracao", "mensagem": "Defina ANTHROPIC_API_KEY"}}


def test_modelo_desconhecido_sai_com_2(capsys):
    assert main(["classificar", "--texto", "oi", "--simulado", "--modelo", "gpt-x"]) == 2
    assert _json(capsys)["erro"]["tipo"] == "configuracao"


def test_entrada_invalida_sai_com_2(capsys, tmp_path):
    arq = tmp_path / "e.json"
    arq.write_text('[{"id": "A"}]', encoding="utf-8")
    assert main(["classificar", "--arquivo", str(arq), "--simulado"]) == 2
    assert _json(capsys)["erro"]["tipo"] == "entrada"


def test_avaliar_sem_gabarito_sai_com_2(capsys, tmp_path):
    arq = tmp_path / "e.json"
    arq.write_text('[{"id": "A", "texto": "oi"}]', encoding="utf-8")
    assert main(["avaliar", "--arquivo", str(arq), "--simulado"]) == 2


def test_chave_nao_aparece_na_saida(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-segredo")
    main(["classificar", "--texto", "oi", "--simulado"])
    assert "sk-ant-segredo" not in capsys.readouterr().out


def test_classificar_usa_hoje_e_aceita_data_referencia(capsys):
    from datetime import date

    main(["classificar", "--texto", "oi", "--simulado"])
    assert _json(capsys)["meta"]["data_referencia"] == date.today().isoformat()
    main(["classificar", "--texto", "oi", "--simulado", "--data-referencia", "2026-10-01"])
    assert _json(capsys)["meta"]["data_referencia"] == "2026-10-01"


def test_avaliar_destaca_metas_nao_medidas(capsys):
    main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado"])
    capturado = capsys.readouterr()
    rel = json.loads(capturado.out)
    assert rel["metas_nao_medidas"] == ["estabilidade"]
    assert "NÃO MEDIDA" in rel["aviso"] and "NÃO MEDIDA" in capturado.err


def test_classificar_grava_e_avaliar_nao(tmp_path, monkeypatch, capsys):
    import sqlite3

    banco = tmp_path / "t.db"
    monkeypatch.setenv("TRIAGEM_DB", str(banco))
    main(["classificar", "--arquivo", str(EXEMPLOS), "--simulado"])
    main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado"])
    main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado", "--comparar"])
    capsys.readouterr()
    conexao = sqlite3.connect(banco)
    try:
        total = conexao.execute("SELECT COUNT(*) FROM triagens").fetchone()[0]
    finally:
        conexao.close()
    assert total == len(json.loads(EXEMPLOS.read_text(encoding="utf-8")))


def test_comparar_simulado(capsys, monkeypatch, exemplos):
    monkeypatch.delenv("TRIAGEM_MODELO")
    codigo = main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado", "--comparar"])
    capturado = capsys.readouterr()
    rel = json.loads(capturado.out)
    linhas = {l["estrategia"]: l for l in rel["estrategias"]}
    bloco = rel["comparacao"]

    assert rel["simulado"] is True and "NÃO MEDE ACERTO NEM LATÊNCIA" in rel["aviso"]
    assert "NÃO MEDE ACERTO" in capturado.err
    assert list(linhas) == ["roteador", "sonnet", "haiku"]
    assert linhas["sonnet"]["economia_pct"] == 0
    assert linhas["haiku"]["custo_medio_usd"] < linhas["roteador"]["custo_medio_usd"] < linhas["sonnet"]["custo_medio_usd"]
    assert all(l["total"] == len(exemplos) for l in linhas.values())
    assert set(rel["relatorios"]) == {"roteador", "sonnet", "haiku"}
    # O simulado escala demais para a meta de economia: o veredito tem que mostrar isso.
    assert bloco["economia_pct"] == linhas["roteador"]["economia_pct"] < 40
    assert bloco["metas"]["economia_vs_sonnet"]["status"] == "falhou"
    assert bloco["metas"]["acerto_vs_sonnet"]["status"] == "ok"
    assert bloco["aprovado"] is False and codigo == 1
    assert bloco["taxa_escalonamento"] > bloco["escalonamento_maximo_para_meta"] > 0
    assert bloco["nota"].startswith("SIMULADO")


def test_comparar_nao_aceita_modelo(capsys):
    assert main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado", "--comparar", "--modelo", MODELO_HAIKU]) == 2
    assert _json(capsys)["erro"]["tipo"] == "entrada"


def test_avaliar_simulado_marca_simulado(capsys):
    main(["avaliar", "--arquivo", str(EXEMPLOS), "--simulado"])
    rel = json.loads(capsys.readouterr().out)
    assert rel["simulado"] is True and "NÃO MEDE ACERTO" in rel["aviso"]
    assert set(rel["acerto_por_confianca"]) <= {"alta", "media", "baixa"}
