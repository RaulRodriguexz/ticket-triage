"""CLI: `triagem classificar` e `triagem avaliar`. Camada fina sobre servico.Triador."""

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from . import entrada
from .avaliacao import avaliar, estabilidade
from .config import MODELO_PADRAO
from .erros import FalhaTriagem
from .modelos import Chamado, ErroTriagem
from .servico import Triador
from .simulado import ClienteSimulado

SAIDA_OK, SAIDA_FALHA, SAIDA_CONFIG = 0, 1, 2


def _data(valor: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise argparse.ArgumentTypeError("use o formato AAAA-MM-DD") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triagem", description="Triagem automática de chamados (POC)")
    sub = parser.add_subparsers(dest="comando", required=True)

    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--modelo", help=f"ID do modelo (padrão: TRIAGEM_MODELO ou {MODELO_PADRAO})")
    comum.add_argument("--saida", help="Grava o JSON neste arquivo em vez do stdout")
    comum.add_argument("--simulado", action="store_true", help="Usa um cliente falso, sem chamar a API")
    comum.add_argument("--debug", action="store_true", help="Mostra o stack trace no stderr em erro interno")
    comum.add_argument("--data-referencia", type=_data, metavar="AAAA-MM-DD",
                       help="'Hoje' para os chamados sem data_referencia")

    classif = sub.add_parser("classificar", parents=[comum], help="Classifica um chamado ou um lote")
    origem = classif.add_mutually_exclusive_group(required=True)
    origem.add_argument("--texto", help="Texto do chamado")
    origem.add_argument("--arquivo", help="JSON com um chamado (objeto) ou um lote (lista)")
    classif.add_argument("--assunto", help="Assunto do chamado (com --texto)")

    aval = sub.add_parser("avaliar", parents=[comum], help="Avalia contra o gabarito")
    aval.add_argument("--arquivo", required=True, help="JSON com chamados e gabarito")
    aval.add_argument("--repeticoes", type=int, default=1, help="Rodadas para medir estabilidade (padrão 1)")
    return parser


def _escrever(dados: Any, destino: str | None) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=2)
    if destino:
        Path(destino).write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)


def _preencher_data(chamados: list[Chamado], padrao: date | None) -> None:
    for chamado in chamados:
        if chamado.data_referencia is None:
            chamado.data_referencia = padrao


def _triador(args: argparse.Namespace, chamados: list[Chamado]) -> Triador:
    cliente = ClienteSimulado.do_gabarito(chamados) if args.simulado else None
    return Triador(args.modelo, cliente=cliente)


def _classificar(args: argparse.Namespace) -> int:
    if args.texto is not None:
        chamados, era_lista = [entrada.de_texto(args.texto, args.assunto)], False
    else:
        chamados, era_lista = entrada.de_arquivo(args.arquivo)
    # Na classificação do dia a dia, "hoje" é a data local (RF-09).
    _preencher_data(chamados, args.data_referencia or date.today())

    triador = _triador(args, chamados)
    resultados = [triador.triar_chamado(chamado) for chamado in chamados]
    saida = [r.model_dump(mode="json") for r in resultados]
    _escrever(saida if era_lista else saida[0], args.saida)
    return SAIDA_FALHA if any(isinstance(r, ErroTriagem) for r in resultados) else SAIDA_OK


def _avaliar(args: argparse.Namespace) -> int:
    if args.repeticoes < 1:
        raise entrada.ErroEntrada("--repeticoes deve ser 1 ou mais")
    chamados, _ = entrada.de_arquivo(args.arquivo)
    sem_gabarito = [c.id for c in chamados if c.gabarito is None]
    if sem_gabarito:
        raise entrada.ErroEntrada(f"Chamados sem gabarito: {', '.join(sem_gabarito)}")
    # Sem padrão de relógio: a avaliação tem que dar o mesmo resultado em qualquer dia.
    _preencher_data(chamados, args.data_referencia)

    triador = _triador(args, chamados * args.repeticoes)
    rodadas = [
        [triador.triar_chamado(chamado) for chamado in chamados]
        for _ in range(args.repeticoes)
    ]
    estaveis = estabilidade(rodadas) if args.repeticoes > 1 else None
    relatorio = avaliar(chamados, rodadas[0], triador.modelo, estaveis)
    _escrever(relatorio.model_dump(mode="json"), args.saida)
    if relatorio.aviso:
        print(relatorio.aviso, file=sys.stderr)
    return SAIDA_OK if relatorio.aprovado else SAIDA_FALHA


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")

    args = _parser().parse_args(argv)
    comando = _classificar if args.comando == "classificar" else _avaliar
    try:
        return comando(args)
    except FalhaTriagem as falha:
        _escrever({"erro": falha.detalhe().model_dump()}, None)
        return SAIDA_CONFIG if falha.tipo in ("configuracao", "entrada") else SAIDA_FALHA
    except Exception as erro:  # último nível: nunca mostra stack trace cru
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        _escrever({"erro": {"tipo": "interno", "mensagem": f"{type(erro).__name__}: {erro}"}}, None)
        return SAIDA_FALHA


if __name__ == "__main__":
    sys.exit(main())
