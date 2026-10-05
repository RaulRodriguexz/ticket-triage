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


class Meta(BaseModel):
    modelo: str
    latencia_ms: int
    tokens_entrada: int
    tokens_saida: int
    # None quando o modelo que respondeu não está na tabela de preços.
    custo_usd: float | None
    # O "hoje" usado no prompt (AAAA-MM-DD), para auditoria.
    data_referencia: str | None = None


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
