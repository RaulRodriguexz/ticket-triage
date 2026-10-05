"""Histórico de triagens em SQLite (stdlib). Gravar nunca derruba a triagem.

Privacidade: guarda só um hash do texto (HMAC com TRIAGEM_HASH_SEGREDO, se houver).
O texto só é gravado com TRIAGEM_DB_GUARDAR_TEXTO=1. Resumo e resposta sugerida
nunca são gravados, nem nada da configuração (chave da API).
"""

import hashlib
import hmac
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .avaliacao import percentil
from .modelos import Categoria, Chamado, ErroTriagem, Prioridade, ResultadoTriagem, Roteamento

log = logging.getLogger("triagem.persistencia")

CAMINHO_PADRAO = "data/triage.db"
VERSAO_SCHEMA = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triagens (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  criado_em            TEXT    NOT NULL,
  chamado_id           TEXT,
  canal                TEXT,
  texto_hash           TEXT    NOT NULL,
  texto_tamanho        INTEGER NOT NULL,
  texto                TEXT,
  data_referencia      TEXT,
  status               TEXT    NOT NULL CHECK (status IN ('ok', 'erro')),
  erro_tipo            TEXT,
  categoria            TEXT,
  prioridade           TEXT,
  confianca            TEXT,
  modo                 TEXT    NOT NULL,
  modelo_inicial       TEXT,
  modelo_final         TEXT,
  escalado             INTEGER NOT NULL DEFAULT 0,
  motivo_roteamento    TEXT,
  tokens_entrada       INTEGER,
  tokens_saida         INTEGER,
  custo_usd            REAL,
  custos_desconhecidos INTEGER NOT NULL DEFAULT 0,
  latencia_ms          INTEGER,
  simulado             INTEGER NOT NULL DEFAULT 0,
  versao               TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_triagens_criado_em ON triagens (criado_em);
"""


def _migrar(conexao: sqlite3.Connection) -> None:
    """v1 → v2: coluna custos_desconhecidos (chamadas pagas sem custo conhecido)."""
    colunas = {linha[1] for linha in conexao.execute("PRAGMA table_info(triagens)")}
    if "custos_desconhecidos" not in colunas:
        conexao.execute(
            "ALTER TABLE triagens ADD COLUMN custos_desconhecidos INTEGER NOT NULL DEFAULT 0"
        )


class Registro:
    def __init__(self, caminho: str | Path, *, guardar_texto: bool = False, segredo: str | None = None):
        """Cria a pasta e o schema. Levanta sqlite3.Error/OSError se não conseguir."""
        self.caminho = Path(caminho)
        self.guardar_texto = guardar_texto
        self._segredo = segredo.encode("utf-8") if segredo else None
        self._avisou_sem_segredo = False
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conexao:
            conexao.execute("PRAGMA journal_mode=WAL")
            conexao.executescript(_SCHEMA)
            _migrar(conexao)
            conexao.execute(f"PRAGMA user_version = {VERSAO_SCHEMA}")

    @classmethod
    def do_ambiente(cls) -> "Registro | None":
        """TRIAGEM_DB (padrão data/triage.db; 'off' desliga). Falha → None, com aviso."""
        caminho = os.environ.get("TRIAGEM_DB", CAMINHO_PADRAO).strip()
        if caminho.lower() in ("off", ""):
            return None
        try:
            return cls(
                caminho,
                guardar_texto=os.environ.get("TRIAGEM_DB_GUARDAR_TEXTO") == "1",
                segredo=os.environ.get("TRIAGEM_HASH_SEGREDO") or None,
            )
        except (sqlite3.Error, OSError) as erro:
            log.warning("Persistência desligada: não foi possível abrir %s (%s)", caminho, type(erro).__name__)
            return None

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        # Uma conexão por operação (simples e seguro no threadpool do FastAPI),
        # com commit ao sair e sempre fechada.
        conexao = sqlite3.connect(self.caminho, timeout=2)
        try:
            with conexao:
                yield conexao
        finally:
            conexao.close()

    def _hash(self, chamado: Chamado) -> str:
        conteudo = f"{chamado.assunto or ''}\n{chamado.texto}".encode("utf-8")
        if self._segredo:
            return hmac.new(self._segredo, conteudo, hashlib.sha256).hexdigest()
        if not self._avisou_sem_segredo:
            log.warning("TRIAGEM_HASH_SEGREDO não definido: hash SHA-256 simples (reversível em textos curtos)")
            self._avisou_sem_segredo = True
        return hashlib.sha256(conteudo).hexdigest()

    def gravar(
        self,
        chamado: Chamado,
        resultado: ResultadoTriagem | ErroTriagem,
        roteamento: Roteamento | None = None,
        *,
        simulado: bool = False,
    ) -> bool:
        """Grava uma triagem. Nunca levanta: em falha, registra aviso e devolve False."""
        from . import __version__

        ok = isinstance(resultado, ResultadoTriagem)
        # Em erro, `gasto` traz o que já foi pago (tentativas inválidas, escalada que falhou).
        meta = resultado.meta if ok else resultado.gasto
        roteamento = roteamento or (meta.roteamento if meta else None)
        linha = {
            "criado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chamado_id": chamado.id,
            "canal": chamado.canal,
            "texto_hash": self._hash(chamado),
            "texto_tamanho": len(chamado.texto),
            "texto": chamado.texto if self.guardar_texto else None,
            "data_referencia": chamado.data_referencia.isoformat() if chamado.data_referencia else None,
            "status": "ok" if ok else "erro",
            "erro_tipo": None if ok else resultado.erro.tipo,
            "categoria": resultado.categoria.value if ok else None,
            "prioridade": resultado.prioridade.value if ok else None,
            "confianca": resultado.confianca.value if ok else None,
            "modo": roteamento.modo if roteamento else "fixo",
            "modelo_inicial": roteamento.modelo_inicial if roteamento else None,
            "modelo_final": meta.modelo if ok else None,
            "escalado": int(bool(roteamento and roteamento.escalado)),
            "motivo_roteamento": roteamento.motivo if roteamento else None,
            "tokens_entrada": meta.tokens_entrada if meta else None,
            "tokens_saida": meta.tokens_saida if meta else None,
            "custo_usd": meta.custo_usd if meta else None,
            "custos_desconhecidos": meta.custos_desconhecidos if meta else 0,
            "latencia_ms": meta.latencia_ms if ok else None,
            "simulado": int(simulado),
            "versao": __version__,
        }
        colunas = ", ".join(linha)
        marcadores = ", ".join(f":{c}" for c in linha)
        try:
            with self._conectar() as conexao:
                conexao.execute(f"INSERT INTO triagens ({colunas}) VALUES ({marcadores})", linha)
            return True
        except (sqlite3.Error, OSError) as erro:
            log.warning("Falha ao gravar triagem %s: %s", chamado.id, type(erro).__name__)
            return False

    def estatisticas(
        self, desde: date | None = None, ate: date | None = None, simulado: bool | None = None
    ) -> dict:
        """Agregados para GET /v1/stats. Levanta sqlite3.Error se o banco falhar."""
        filtros, params = [], []
        if desde:
            filtros.append("date(criado_em) >= ?")
            params.append(desde.isoformat())
        if ate:
            filtros.append("date(criado_em) <= ?")
            params.append(ate.isoformat())
        if simulado is not None:
            filtros.append("simulado = ?")
            params.append(int(simulado))
        onde = " AND ".join(filtros) or "1=1"

        with self._conectar() as c:
            def um(sql: str, extra: str = "") -> tuple:
                return c.execute(f"{sql} WHERE {onde} {extra}", params).fetchone()

            def grupos(coluna: str, extra: str) -> dict:
                sql = f"SELECT {coluna}, COUNT(*) FROM triagens WHERE {onde} AND {extra} GROUP BY {coluna}"
                return {k: n for k, n in c.execute(sql, params) if k is not None}

            tickets, ok, simulados = um(
                "SELECT COUNT(*), COALESCE(SUM(status = 'ok'), 0), COALESCE(SUM(simulado), 0) FROM triagens"
            )
            # Inclui o gasto das triagens com erro. Linhas com alguma chamada sem custo
            # conhecido entram em custos_desconhecidos (o total delas é um piso).
            custo_total, com_custo, sem_custo = um(
                "SELECT COALESCE(SUM(custo_usd), 0), COUNT(custo_usd), "
                "COALESCE(SUM(custos_desconhecidos > 0 OR (status = 'ok' AND custo_usd IS NULL)), 0) "
                "FROM triagens"
            )
            pre_roteados, iniciados, escalados = um(
                "SELECT COALESCE(SUM(motivo_roteamento = 'texto_longo'), 0), "
                "COALESCE(SUM(COALESCE(motivo_roteamento, '') != 'texto_longo'), 0), "
                "COALESCE(SUM(escalado), 0) FROM triagens",
                "AND modo = 'auto'",
            )
            latencias = [
                n for (n,) in c.execute(
                    f"SELECT latencia_ms FROM triagens WHERE {onde} AND status = 'ok' AND latencia_ms IS NOT NULL",
                    params,
                )
            ]
            por_categoria = grupos("categoria", "status = 'ok'")
            por_prioridade = grupos("prioridade", "status = 'ok'")
            por_modelo = grupos("modelo_final", "status = 'ok'")
            erros_por_tipo = grupos("erro_tipo", "status = 'erro'")
            por_motivo = grupos("motivo_roteamento", "escalado = 1")

        return {
            "periodo": {"desde": desde.isoformat() if desde else None, "ate": ate.isoformat() if ate else None},
            "tickets": tickets,
            "ok": ok,
            "erros": tickets - ok,
            "erros_por_tipo": erros_por_tipo,
            "simulados": simulados,
            "custo_total_usd": round(custo_total, 6),
            "custo_medio_usd": round(custo_total / com_custo, 6) if com_custo else None,
            "custos_desconhecidos": sem_custo,
            "por_categoria": {c.value: por_categoria.get(c.value, 0) for c in Categoria},
            "por_prioridade": {p.value: por_prioridade.get(p.value, 0) for p in Prioridade},
            "por_modelo": por_modelo,
            "roteamento": {
                "pre_roteados": pre_roteados,
                "iniciados_no_haiku": iniciados,
                "escalados": escalados,
                "taxa_escalonamento": round(escalados / iniciados, 4) if iniciados else None,
                "por_motivo": por_motivo,
            },
            "latencia_ms": {"p50": percentil(latencias, 50), "p95": percentil(latencias, 95)},
            "banco": {"ativo": True},
        }
