"""Schemas Pydantic de entrada, saída e relatório."""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from .promessas import promessas

TAMANHO_MAX_TEXTO = 8000
TAMANHO_MAX_RESUMO = 120


class Categoria(str, Enum):
    acesso = "acesso"
    cobranca = "cobranca"
    bug_tecnico = "bug_tecnico"
    duvida_uso = "duvida_uso"
    sugestao = "sugestao"
    outro = "outro"


class Prioridade(str, Enum):
    critica = "critica"
    alta = "alta"
    media = "media"
    baixa = "baixa"


class Confianca(str, Enum):
    alta = "alta"
    media = "media"
    baixa = "baixa"


class Triagem(BaseModel):
    """O que o modelo gera. Vira o JSON Schema enviado à API."""

    model_config = ConfigDict(extra="forbid")

    categoria: Categoria
    prioridade: Prioridade
    resumo: str = Field(
        max_length=TAMANHO_MAX_RESUMO,
        description="Uma linha só, até 120 caracteres",
    )
    resposta_sugerida: str
    justificativa: str
    # Depois da justificativa: o modelo justifica antes de se autoavaliar.
    confianca: Confianca

    @field_validator("resumo")
    @classmethod
    def resumo_uma_linha(cls, valor: str) -> str:
        if "\n" in valor or "\r" in valor:
            raise ValueError("o resumo deve ter uma linha só")
        return valor

    @field_validator("resposta_sugerida")
    @classmethod
    def sem_promessas(cls, valor: str) -> str:
        achados = promessas(valor)
        if achados:
            descricao = "; ".join(f"{tipo} ('{trecho}')" for tipo, trecho in achados)
            raise ValueError(f"não prometa prazo, reembolso nem correção: {descricao}")
        return valor


class Gabarito(BaseModel):
    categoria: Categoria
    prioridade: Prioridade


class Chamado(BaseModel):
    """Um chamado de entrada. Campos desconhecidos são ignorados."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    canal: Literal["email", "ticket"] | None = None
    assunto: str | None = None
    texto: str = Field(min_length=1, max_length=TAMANHO_MAX_TEXTO)
    # Vai para o prompt como "hoje". A biblioteca não lê o relógio: o padrão
    # (data atual ou nenhuma) é decidido pela camada que chama (CLI, web).
    data_referencia: date | None = None
    # Usado só pela avaliação; nunca entra no prompt.
    gabarito: Gabarito | None = Field(default=None, exclude=True)

    @field_validator("texto")
    @classmethod
    def texto_nao_vazio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("texto não pode ser vazio")
        return valor


class ChamadaModelo(BaseModel):
    """Uma chamada paga (ou possivelmente paga) dentro de uma triagem."""

    modelo: str
    tokens_entrada: int
    tokens_saida: int
    # Parte conhecida do custo; None se nada é conhecido (ex.: timeout sem usage).
    custo_usd: float | None
    latencia_ms: int
    status: Literal["ok", "erro"] = "ok"
    erro_tipo: str | None = None
    # True quando parte do custo desta chamada é desconhecida (sem usage ou sem preço).
    custo_desconhecido: bool = False


class Roteamento(BaseModel):
    modo: Literal["auto", "fixo"]
    modelo_inicial: str
    escalado: bool = False
    # "texto_longo" (pré-roteado), "confianca_baixa", "prioridade", "falha_validacao",
    # ou "<motivo> (falhou: <tipo>)" quando a escalada falhou na API.
    motivo: str | None = None
    chamadas: list[ChamadaModelo] = []


class Meta(BaseModel):
    # Modelo da resposta final; tokens, custo e latência são totais das chamadas.
    modelo: str
    latencia_ms: int
    tokens_entrada: int
    tokens_saida: int
    # Soma dos custos conhecidos de todas as chamadas; None se nenhum é conhecido.
    custo_usd: float | None
    # Chamadas cujo custo não é conhecido (sem usage, como em timeout, ou modelo sem preço).
    # Com valor > 0, custo_usd é um piso, não o total.
    custos_desconhecidos: int = 0
    # O "hoje" usado no prompt (AAAA-MM-DD), para auditoria.
    data_referencia: str | None = None
    roteamento: Roteamento | None = None


class ResultadoTriagem(Triagem):
    """O que a CLI devolve para um chamado classificado."""

    id: str
    meta: Meta

    @model_serializer(mode="wrap")
    def _id_primeiro(self, serializar):
        dados = serializar(self)
        return {"id": dados.pop("id"), **dados}


TipoErro = Literal[
    "configuracao",
    "banco_indisponivel",
    "entrada",
    "recusa",
    "validacao",
    "autenticacao",
    "limite_taxa",
    "timeout",
    "conexao",
    "requisicao_invalida",
    "api",
    "interno",
]


class DetalheErro(BaseModel):
    tipo: TipoErro
    mensagem: str
    # Segundos sugeridos pela API em 429; não sai no JSON (vira Retry-After no HTTP).
    retry_after: str | None = Field(default=None, exclude=True)


class ErroTriagem(BaseModel):
    """Um item do lote que não pôde ser classificado."""

    id: str
    erro: DetalheErro
    # O que já foi gasto até a falha; uso interno (custo, banco), fora do JSON.
    gasto: "Meta | None" = Field(default=None, exclude=True)


class ErroAvaliado(BaseModel):
    id: str
    esperado: Gabarito
    obtido: Gabarito | None
    erro: str | None = None


StatusMeta = Literal["ok", "falhou", "nao_medida"]


class AvaliacaoMeta(BaseModel):
    valor: str
    alvo: str
    status: StatusMeta


class Relatorio(BaseModel):
    modelo: str
    total: int
    acerto_conjunto: int
    acerto_categoria: int
    acerto_prioridade: int
    criticos_subestimados: int
    aprovado: bool
    metas: dict[str, AvaliacaoMeta]
    erros: list[ErroAvaliado]
    custo_total_usd: float
    custo_medio_usd: float | None
    custos_desconhecidos: int
    latencia_p95_ms: int | None
    estabilidade: int | None = None
    # Destaque das metas que não puderam ser medidas (não reprovam).
    metas_nao_medidas: list[str] = []
    aviso: str | None = None
    simulado: bool = False
    # Só no modo auto: escalados / chamados que começaram no modelo base.
    taxa_escalonamento: float | None = None
    escalonamentos_por_motivo: dict[str, int] = {}
    # Acerto conjunto por confiança declarada na resposta final: {"alta": {"total", "acertos"}}.
    acerto_por_confianca: dict[str, dict[str, int]] = {}


class LinhaComparacao(BaseModel):
    estrategia: str
    modelo: str
    total: int
    acerto_conjunto: int
    acerto_categoria: int
    criticos_subestimados: int
    aprovado: bool
    custo_total_usd: float
    custo_medio_usd: float | None
    # Economia do custo médio em relação à estratégia "sonnet" (sempre Sonnet).
    economia_pct: float | None
    latencia_p95_ms: int | None
    taxa_escalonamento: float | None
    escalonamentos_por_motivo: dict[str, int]


class BlocoComparacao(BaseModel):
    """Veredito do roteador contra as metas comparativas do PRD §5."""

    aprovado: bool
    metas: dict[str, AvaliacaoMeta]
    economia_pct: float | None
    taxa_escalonamento: float | None
    escalonamentos_por_motivo: dict[str, int]
    # Maior taxa de escalonamento que ainda daria a economia mínima, com o custo médio
    # medido de cada modelo: 1 - economia_minima - custo_haiku / custo_sonnet
    # (com a meta de 30%: 0,7 - custo_haiku / custo_sonnet).
    escalonamento_maximo_para_meta: float | None
    nota: str


class RelatorioComparacao(BaseModel):
    simulado: bool
    comparacao: BlocoComparacao
    estrategias: list[LinhaComparacao]
    relatorios: dict[str, Relatorio]
    aviso: str | None = None
