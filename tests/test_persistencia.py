import logging
import sqlite3
from datetime import date

import anthropic
import httpx2
import pytest

from triagem import Triador
from triagem.modelos import Chamado, Confianca, Prioridade
from triagem.persistencia import Registro
from triagem.simulado import ClienteSimulado, resposta

TEXTO = "Fatura com CNPJ antigo da Padaria Exemplo Ltda, vence amanhã."
CHAVE = "sk-ant-api03-CHAVE-SUPER-SECRETA-DE-TESTE"


def _linhas(caminho):
    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conexao.execute("SELECT * FROM triagens ORDER BY id")]
    finally:
        conexao.close()


def _bytes_do_banco(caminho) -> bytes:
    dados = b""
    for sufixo in ("", "-wal", "-shm"):
        arquivo = caminho.with_name(caminho.name + sufixo)
        if arquivo.exists():
            dados += arquivo.read_bytes()
    return dados


@pytest.fixture
def media(triagem_valida):
    return triagem_valida.model_copy(update={"prioridade": Prioridade.media})


def test_grava_e_le_de_volta(tmp_path, media):
    registro = Registro(tmp_path / "t.db")
    triador = Triador("auto", cliente=ClienteSimulado([resposta(media)]), registro=registro)
    triador.triar(TEXTO, id="T-1", canal="email", data_referencia=date(2026, 10, 1))

    (linha,) = _linhas(tmp_path / "t.db")
    assert linha["chamado_id"] == "T-1" and linha["canal"] == "email"
    assert (linha["status"], linha["categoria"], linha["prioridade"], linha["confianca"]) == (
        "ok", "bug_tecnico", "media", "alta")
    assert (linha["modo"], linha["escalado"], linha["simulado"]) == ("auto", 0, 1)
    assert linha["texto"] is None and linha["texto_tamanho"] == len(TEXTO)
    assert len(linha["texto_hash"]) == 64
    assert linha["data_referencia"] == "2026-10-01"
    assert linha["versao"] == "0.3.0"


def test_schema_idempotente(tmp_path):
    Registro(tmp_path / "t.db")
    Registro(tmp_path / "t.db")
    conexao = sqlite3.connect(tmp_path / "t.db")
    try:
        assert conexao.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conexao.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conexao.close()


def test_texto_resumo_resposta_e_chave_nunca_vao_para_o_banco_nem_para_o_log(
    tmp_path, media, monkeypatch, caplog
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE)
    caplog.set_level(logging.DEBUG)
    registro = Registro(tmp_path / "t.db")
    # A chave está no ambiente e, portanto, na Config do Triador; a resposta vem da fila.
    triador = Triador("auto", cliente=ClienteSimulado([resposta(media)]), registro=registro)
    triador.triar(TEXTO)
    erro = anthropic.RateLimitError("e", response=httpx2.Response(429, request=httpx2.Request("POST", "https://x")), body=None)
    Triador("auto", cliente=ClienteSimulado([erro]), registro=registro).triar_chamado(Chamado(texto=TEXTO))

    bruto = _bytes_do_banco(tmp_path / "t.db")
    for proibido in (TEXTO, media.resumo, media.resposta_sugerida, CHAVE):
        assert proibido.encode("utf-8") not in bruto
    assert CHAVE not in caplog.text and TEXTO not in caplog.text


def test_guardar_texto_quando_pedido(tmp_path, media, monkeypatch):
    monkeypatch.setenv("TRIAGEM_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("TRIAGEM_DB_GUARDAR_TEXTO", "1")
    registro = Registro.do_ambiente()
    Triador("auto", cliente=ClienteSimulado([resposta(media)]), registro=registro).triar(TEXTO)
    assert _linhas(tmp_path / "t.db")[0]["texto"] == TEXTO


def test_hmac_com_segredo_muda_o_hash(tmp_path):
    chamado = Chamado(texto=TEXTO)
    simples = Registro(tmp_path / "a.db")._hash(chamado)
    com_segredo = Registro(tmp_path / "b.db", segredo="s3gr3d0")._hash(chamado)
    assert simples != com_segredo and len(com_segredo) == 64


def test_db_off_desliga(monkeypatch):
    monkeypatch.setenv("TRIAGEM_DB", "off")
    assert Registro.do_ambiente() is None


def test_banco_que_nao_abre_vira_none(tmp_path, monkeypatch):
    pasta = tmp_path / "sou_pasta"
    pasta.mkdir()
    monkeypatch.setenv("TRIAGEM_DB", str(pasta))  # caminho é uma pasta: sqlite não abre
    assert Registro.do_ambiente() is None


def test_falha_ao_gravar_nao_derruba_a_triagem(tmp_path, media, monkeypatch, caplog):
    registro = Registro(tmp_path / "t.db")

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disco cheio")

    monkeypatch.setattr(sqlite3, "connect", explode)
    triador = Triador("auto", cliente=ClienteSimulado([resposta(media)]), registro=registro)
    resultado = triador.triar(TEXTO)

    assert resultado.categoria == media.categoria
    assert registro.gravar(Chamado(texto=TEXTO), resultado) is False
    assert "Falha ao gravar" in caplog.text


def test_erro_de_api_e_gravado_como_erro(tmp_path):
    registro = Registro(tmp_path / "t.db")
    erro = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))
    Triador("auto", cliente=ClienteSimulado([erro]), registro=registro).triar_chamado(Chamado(texto="Oi"))
    (linha,) = _linhas(tmp_path / "t.db")
    assert (linha["status"], linha["erro_tipo"], linha["categoria"]) == ("erro", "timeout", None)


def test_estatisticas(tmp_path, media):
    registro = Registro(tmp_path / "t.db")
    baixa = media.model_copy(update={"confianca": Confianca.baixa})
    fila = [
        resposta(media, tokens_entrada=1000, tokens_saida=100),                         # fica no Haiku
        resposta(baixa, tokens_entrada=1000, tokens_saida=100), resposta(media),        # escala
        resposta(media),                                                                # texto longo
    ]
    triador = Triador("auto", cliente=ClienteSimulado(fila), registro=registro)
    triador.triar("curto 1")
    triador.triar("curto 2")
    triador.triar("x" * 1501)

    s = registro.estatisticas()
    assert (s["tickets"], s["ok"], s["erros"], s["simulados"]) == (3, 3, 0, 3)
    assert s["por_categoria"]["bug_tecnico"] == 3 and s["por_categoria"]["outro"] == 0
    assert s["por_prioridade"] == {"critica": 0, "alta": 0, "media": 3, "baixa": 0}
    assert s["por_modelo"] == {"claude-haiku-4-5-20251001": 1, "claude-sonnet-5-5": 2}
    assert s["roteamento"] == {
        "pre_roteados": 1, "iniciados_no_haiku": 2, "escalados": 1,
        "taxa_escalonamento": 0.5, "por_motivo": {"confianca_baixa": 1},
    }
    linhas = _linhas(tmp_path / "t.db")
    assert s["custo_total_usd"] == pytest.approx(sum(r["custo_usd"] for r in linhas))
    assert s["latencia_ms"]["p50"] is not None
    assert registro.estatisticas(simulado=False)["tickets"] == 0
    assert registro.estatisticas(desde=date(2000, 1, 1), ate=date(2000, 1, 2))["tickets"] == 0


def test_migra_banco_v1(tmp_path):
    caminho = tmp_path / "v1.db"
    conexao = sqlite3.connect(caminho)
    conexao.executescript("""
        CREATE TABLE triagens (id INTEGER PRIMARY KEY, criado_em TEXT NOT NULL, chamado_id TEXT,
          canal TEXT, texto_hash TEXT NOT NULL, texto_tamanho INTEGER NOT NULL, texto TEXT,
          data_referencia TEXT, status TEXT NOT NULL, erro_tipo TEXT, categoria TEXT, prioridade TEXT,
          confianca TEXT, modo TEXT NOT NULL, modelo_inicial TEXT, modelo_final TEXT,
          escalado INTEGER NOT NULL DEFAULT 0, motivo_roteamento TEXT, tokens_entrada INTEGER,
          tokens_saida INTEGER, custo_usd REAL, latencia_ms INTEGER,
          simulado INTEGER NOT NULL DEFAULT 0, versao TEXT NOT NULL);
        PRAGMA user_version = 1;
    """)
    conexao.close()
    registro = Registro(caminho)
    assert registro.gravar(Chamado(texto="Oi"), _erro_validacao())
    assert _linhas(caminho)[0]["custos_desconhecidos"] == 0


def _erro_validacao():
    from triagem.modelos import DetalheErro, ErroTriagem

    return ErroTriagem(id="X", erro=DetalheErro(tipo="validacao", mensagem="x"))
