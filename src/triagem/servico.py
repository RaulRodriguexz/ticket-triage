"""API pública: a única porta de entrada para a lógica de triagem (CLI e web usam esta)."""

import dataclasses
from datetime import date
from functools import lru_cache
from typing import Any, Literal

from pydantic import ValidationError

from . import config as cfg
from .classificador import classificar
from .entrada import descrever_erro
from .erros import ErroEntrada, FalhaTriagem
from .modelos import ChamadaModelo, Chamado, ErroTriagem, Meta, ResultadoTriagem, Roteamento
from .persistencia import Registro
from .roteador import RegraRoteamento, decidir_modelo, deve_escalar

Resultado = ResultadoTriagem | ErroTriagem


class Triador:
    """Reutilizável e thread-safe. Crie um por processo (o servidor cria um no startup)."""

    def __init__(
        self,
        modelo: str | None = None,
        *,
        cliente: Any | None = None,
        max_retries: int = 2,
        regra: RegraRoteamento | None = None,
        registro: Registro | None = None,
    ) -> None:
        # modelo: argumento > TRIAGEM_MODELO > padrão ("auto").
        # Com cliente injetado (testes, simulado) a chave não é exigida.
        self._config = cfg.carregar(modelo, exigir_chave=cliente is None, max_retries=max_retries)
        self._cliente = cliente if cliente is not None else cfg.criar_cliente(self._config)
        # Regra: parâmetro > variáveis TRIAGEM_ROTEADOR_* > padrões. Só existe no modo auto.
        self._regra = (regra or RegraRoteamento.do_ambiente()) if self._config.eh_auto else None
        self._registro = registro
        self._simulado = bool(getattr(self._cliente, "simulado", False))

    @property
    def modelo(self) -> str:
        """ID do modelo fixo, ou "auto" no modo roteador."""
        return self._config.modelo

    @property
    def regra(self) -> RegraRoteamento | None:
        return self._regra

    @property
    def registro(self) -> Registro | None:
        return self._registro

    def triar(
        self,
        texto: str,
        *,
        data_referencia: date | None = None,
        assunto: str | None = None,
        canal: Literal["email", "ticket"] | None = None,
        id: str | None = None,
    ) -> ResultadoTriagem:
        """Um chamado. Sucesso → ResultadoTriagem. Falha → levanta FalhaTriagem."""
        try:
            chamado = Chamado(
                texto=texto, data_referencia=data_referencia, assunto=assunto, canal=canal, id=id
            )
        except ValidationError as erro:
            raise ErroEntrada(descrever_erro(erro)) from None

        resultado = self.triar_chamado(chamado)
        if isinstance(resultado, ErroTriagem):
            erro = resultado.erro
            raise FalhaTriagem(erro.mensagem, tipo=erro.tipo, retry_after=erro.retry_after)
        return resultado

    def triar_chamado(self, chamado: Chamado) -> Resultado:
        """Para lotes (CLI classificar/avaliar): NÃO levanta por item; devolve ErroTriagem."""
        if self._regra is None:
            resultado = self._classificar(chamado, self._config.modelo)
            roteamento = Roteamento(modo="fixo", modelo_inicial=self._config.modelo)
            finais = [resultado]
        else:
            resultado, roteamento, finais = self._rotear(chamado, self._regra)

        # Todas as chamadas pagas entram no custo, inclusive as que falharam.
        roteamento.chamadas = [c for c in map(_chamada, finais) if c is not None]
        if isinstance(resultado, ResultadoTriagem):
            resultado = resultado.model_copy(
                update={"meta": _meta_total(resultado.meta.modelo, roteamento, chamado)}
            )
        else:
            modelo_final = roteamento.chamadas[-1].modelo if roteamento.chamadas else self._config.modelo
            resultado = resultado.model_copy(
                update={"gasto": _meta_total(modelo_final, roteamento, chamado)}
            )
        if self._registro is not None:
            # Nunca levanta: uma falha no banco não derruba a triagem.
            self._registro.gravar(chamado, resultado, roteamento, simulado=self._simulado)
        return resultado

    def _classificar(self, chamado: Chamado, modelo: str) -> Resultado:
        return classificar(chamado, self._cliente, dataclasses.replace(self._config, modelo=modelo))

    def _rotear(
        self, chamado: Chamado, regra: RegraRoteamento
    ) -> tuple[Resultado, Roteamento, list[Resultado]]:
        """decidir_modelo → classificar → deve_escalar → classificar de novo, se for o caso."""
        decisao = decidir_modelo(chamado, regra)
        roteamento = Roteamento(
            modo="auto",
            modelo_inicial=decisao.modelo,
            motivo="texto_longo" if decisao.motivo == "texto_longo" else None,
        )
        inicial = self._classificar(chamado, decisao.modelo)
        motivo = deve_escalar(inicial, regra) if decisao.modelo == regra.modelo_base else None
        if motivo is None:
            return inicial, roteamento, [inicial]

        forte = self._classificar(chamado, regra.modelo_forte)
        if isinstance(forte, ResultadoTriagem):
            roteamento.escalado, roteamento.motivo = True, motivo
            return forte, roteamento, [inicial, forte]
        # A escalada falhou: fica a resposta do modelo base, marcada. As duas chamadas
        # continuam no custo (a do forte como desconhecida se não houve usage).
        roteamento.motivo = f"{motivo} (falhou: {forte.erro.tipo})"
        if isinstance(inicial, ResultadoTriagem):
            return inicial, roteamento, [inicial, forte]
        return forte, roteamento, [inicial, forte]


def _chamada(resultado: Resultado) -> ChamadaModelo | None:
    """Uma entrada de custo por chamada a `classificar`, com sucesso ou não."""
    ok = isinstance(resultado, ResultadoTriagem)
    meta = resultado.meta if ok else resultado.gasto
    if meta is None:
        return None
    return ChamadaModelo(
        modelo=meta.modelo,
        tokens_entrada=meta.tokens_entrada,
        tokens_saida=meta.tokens_saida,
        custo_usd=meta.custo_usd,
        latencia_ms=meta.latencia_ms,
        status="ok" if ok else "erro",
        erro_tipo=None if ok else resultado.erro.tipo,
        custo_desconhecido=meta.custos_desconhecidos > 0,
    )


def _meta_total(modelo_final: str, roteamento: Roteamento, chamado: Chamado) -> Meta:
    """Totais das chamadas: custo é a soma de cada chamada pelo preço do seu modelo.

    Custo desconhecido não vira zero: é contado em `custos_desconhecidos`, e então
    `custo_usd` é só a parte conhecida (um piso); None se nada é conhecido.
    """
    chamadas = roteamento.chamadas
    conhecidos = [c.custo_usd for c in chamadas if c.custo_usd is not None]
    return Meta(
        modelo=modelo_final,
        latencia_ms=sum(c.latencia_ms for c in chamadas),
        tokens_entrada=sum(c.tokens_entrada for c in chamadas),
        tokens_saida=sum(c.tokens_saida for c in chamadas),
        custo_usd=round(sum(conhecidos), 6) if conhecidos else None,
        custos_desconhecidos=sum(c.custo_desconhecido for c in chamadas),
        data_referencia=chamado.data_referencia.isoformat() if chamado.data_referencia else None,
        roteamento=roteamento,
    )


@lru_cache(maxsize=1)
def _triador_padrao() -> Triador:
    return Triador()


def triar(texto: str, data_referencia: date | None = None, **opcoes: Any) -> ResultadoTriagem:
    """Atalho: usa um Triador padrão criado sob demanda e chama .triar()."""
    return _triador_padrao().triar(texto, data_referencia=data_referencia, **opcoes)
