# Triagem automática de chamados (POC)

Biblioteca Python, CLI e endpoint HTTP opcional que classificam um chamado de suporte (categoria e prioridade) e devolvem um resumo de uma linha e um rascunho de resposta. Usa uma chamada à API da Anthropic por chamado, com saída estruturada. A IA sugere; um humano decide e envia.

Documentos: [PRD](docs/PRD.md) · [Arquitetura](docs/ARQUITETURA.md) · gabarito em [docs/exemplos.json](docs/exemplos.json).

## Instalar e rodar (Windows / PowerShell, Python ≥ 3.11)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
$env:ANTHROPIC_API_KEY = "sk-ant-..."
triagem avaliar --arquivo docs/exemplos.json
```

Sem chave, use `--simulado` em qualquer comando. O cliente falso devolve o gabarito, sem custo e sem rede.

## Uso

```powershell
triagem classificar --texto "Não consigo emitir nota, erro 500" --assunto "Erro"
triagem classificar --arquivo chamados.json --saida resultado.json   # objeto ou lista
triagem avaliar --arquivo docs/exemplos.json --repeticoes 3           # mede a estabilidade
triagem avaliar --arquivo docs/exemplos.json --modelo claude-haiku-4-5-20251001
```

- **Modelo:** `--modelo`, senão `TRIAGEM_MODELO`, senão `claude-sonnet-5-5`. A alternativa barata é `claude-haiku-4-5-20251001`.
- **Códigos de saída:** `0` = tudo classificado ou avaliação aprovada; `1` = algum chamado com erro ou avaliação reprovada; `2` = erro de configuração ou de entrada.
- **Data de referência:** cada chamado aceita `data_referencia` (AAAA-MM-DD), que o prompt usa como "hoje". Para os chamados sem ela, `--data-referencia` define a data. Se nenhuma for informada, `classificar` usa a data atual e `avaliar` envia "não informada", sem ler o relógio.
- **Avaliação:** o relatório mostra cada meta do PRD §5 em `metas`, como `ok`, `falhou` ou `nao_medida`. `aprovado` só é verdadeiro se nenhuma meta falhar. Metas `nao_medida` não reprovam, mas aparecem destacadas em `metas_nao_medidas`, em `aviso` e no stderr. A estabilidade fica `nao_medida` sem `--repeticoes 3`.
- **Critérios de classificação:** ficam só em `src/triagem/prompts/criterios.md`, que é incluído no pacote. Para ajustar a triagem, edite `src/triagem/prompts/`, não o código.

## Usar como biblioteca

```python
from datetime import date
from triagem import triar, FalhaTriagem

resultado = triar("Boleto vence amanhã", data_referencia=date(2026, 10, 2))  # ResultadoTriagem; em erro, levanta FalhaTriagem (.tipo)
```

Para reaproveitar o cliente ou injetar um falso, use `Triador(modelo, cliente=...)`. A API estável é a lista `triagem.__all__`.

## Subir a API local

```powershell
pip install -e .[web]
uvicorn triagem.web:app --host 127.0.0.1 --port 8000
```

- **Rotas:** `POST /v1/triagem` recebe `{"texto": "...", "assunto", "canal", "id", "data_referencia"}`; só `texto` é obrigatório. `GET /v1/saude` responde sem chamar a API. A documentação OpenAPI fica em `http://127.0.0.1:8000/docs`.
- **Erros:** o corpo é sempre `{"erro": {"tipo", "mensagem"}}`. Os status são: 422 para entrada ou recusa, 429 para limite de taxa (com `Retry-After`), 502 para falha da API ou do schema, 503 para configuração, 504 para timeout e 500 para erro interno.
- **Variáveis de ambiente:** `ANTHROPIC_API_KEY` (sem ela o servidor não sobe), `TRIAGEM_MODELO`, `TRIAGEM_SIMULADO=1` (cliente falso, sem custo), `TRIAGEM_FUSO` (padrão `America/Sao_Paulo`, define o "hoje" quando o pedido não traz data), `TRIAGEM_MAX_RETRIES` (padrão 1) e `TRIAGEM_CORS_ORIGENS` (lista separada por vírgula; o CORS fica desligado por padrão).
- **Segurança:** a API não tem autenticação. Ela só deve escutar em `127.0.0.1`.

## Testes

```powershell
pytest
```

Os testes usam o cliente falso (`src/triagem/simulado.py`) e não chamam a API. `tests/test_web.py` precisa do extra `[web]`; sem ele, o teste é pulado. `tests/test_api_real.py` só roda quando `ANTHROPIC_API_KEY` existe.

## Resultados com a API real

**Pendente.** Na avaliação de 01/10/2026 (Nina), `ANTHROPIC_API_KEY` não estava definida, nem na sessão nem nas variáveis de usuário e de máquina do Windows. Por isso não houve rodada real com Sonnet nem com Haiku. Ainda faltam acerto x/10, custo médio, p95 e estabilidade.

| Execução | Acerto conjunto | Categoria | Críticos subestimados | Estabilidade (3×) | Custo médio | p95 | Exit |
|---|---|---|---|---|---|---|---|
| `pytest` | 50 passaram, 1 pulado (`test_api_real`, sem chave) | | | | | | 0 |
| `avaliar --simulado --repeticoes 3` | 10/10 | 10/10 | 0 | 10/10 | US$ 0,006 (tokens fictícios) | 0 ms | 0 |
| `avaliar` sem chave | — | — | — | — | — | — | 2 (`configuracao`) |
| Sonnet 5.5, real | pendente | | | | | | |
| Haiku 4.5, real | pendente | | | | | | |

O modo simulado devolve o próprio gabarito. Os 10/10 só provam que o fluxo, o relatório e os códigos de saída funcionam. Não dizem nada sobre o acerto do modelo.
