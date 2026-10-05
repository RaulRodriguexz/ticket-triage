# Triagem automática de chamados (POC)

Biblioteca Python, CLI e endpoint HTTP opcional que classificam um chamado de suporte (categoria e prioridade) e devolvem um resumo de uma linha e um rascunho de resposta. Por padrão, um roteador manda cada chamado ao Claude Haiku 4.5 e escala para o Claude Sonnet 5.5 os casos difíceis, com saída estruturada. A IA sugere; um humano decide e envia.

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
triagem avaliar --arquivo docs/exemplos.json --comparar              # roteador x sempre-Sonnet x sempre-Haiku
```

- **Modelo:** `--modelo`, senão `TRIAGEM_MODELO`, senão `auto`. `auto` é o roteador; `claude-sonnet-5-5` ou `claude-haiku-4-5-20251001` fixam um modelo.
- **Roteador (`auto`):** um texto com mais de `TRIAGEM_ROTEADOR_MAX_CARACTERES` caracteres vai direto ao Sonnet. Os demais vão ao Haiku, que escala para o Sonnet quando declara confiança abaixo de `TRIAGEM_ROTEADOR_CONFIANCA_MINIMA`, quando dá uma prioridade listada em `TRIAGEM_ROTEADOR_PRIORIDADES` ou quando a resposta falha na validação duas vezes. O custo em `meta.custo_usd` soma as duas chamadas, cada uma pelo preço do seu modelo, e `meta.roteamento` traz o motivo e as chamadas. Na biblioteca, a regra também pode ser passada em `Triador(regra=RegraRoteamento(...))`.
- **`--comparar`:** roda as três estratégias sobre o mesmo arquivo e mostra, para cada uma, acerto, críticos subestimados, custo médio, economia em relação ao sempre-Sonnet e taxa de escalonamento. O bloco `comparacao` dá o veredito: além das metas do roteador, exige economia de pelo menos 40% sobre o sempre-Sonnet e acerto conjunto pelo menos igual ao dele. O bloco traz também `escalonamento_maximo_para_meta`, a maior taxa de escalonamento que ainda daria essa economia. O exit code segue esse veredito; a opção não aceita `--modelo`. Com `--simulado`, o relatório traz `"simulado": true` e um aviso: o simulado devolve o gabarito, com tokens e confiança fictícios, e por isso não mede acerto nem latência.
- **Histórico:** `classificar` e `POST /v1/triagem` gravam cada triagem em SQLite (`data/triage.db`); `avaliar` nunca grava. Toda chamada paga entra no custo, inclusive tentativas inválidas e escaladas que falharam. Uma chamada sem `usage` (timeout, por exemplo) conta em `custos_desconhecidos`, e nesse caso `custo_usd` é um piso. Por padrão, o banco guarda só o hash do texto, nunca o texto, o resumo, a resposta nem a chave.
- **Confiança:** cada resposta tem o campo `confianca` (`alta`, `media` ou `baixa`), novo na versão 0.3.0. É uma mudança compatível, porque só acrescenta um campo.
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

- **Rotas:**
  - `POST /v1/triagem` recebe `{"texto": "...", "assunto", "canal", "id", "data_referencia"}`; só `texto` é obrigatório. Cada triagem é gravada no histórico.
  - `GET /v1/saude` responde sem chamar a API e mostra o modo, a regra do roteador e se a persistência está ativa.
  - `GET /v1/stats?desde=AAAA-MM-DD&ate=AAAA-MM-DD&simulado=0|1` devolve agregados do histórico: totais, erros por tipo, custo acumulado e médio, distribuição por categoria, prioridade e modelo, taxa de escalonamento e latência p50 e p95. Não traz texto nem hash. Responde 503 se a persistência estiver desligada.
  - A documentação OpenAPI fica em `http://127.0.0.1:8000/docs`.
- **Erros:** o corpo é sempre `{"erro": {"tipo", "mensagem"}}`. Os status são: 422 para entrada ou recusa, 429 para limite de taxa (com `Retry-After`), 502 para falha da API ou do schema, 503 para configuração, 504 para timeout e 500 para erro interno.
- **Variáveis de ambiente:** `ANTHROPIC_API_KEY` (sem ela o servidor não sobe), `TRIAGEM_MODELO`, `TRIAGEM_SIMULADO=1` (cliente falso, sem custo), `TRIAGEM_FUSO` (padrão `America/Sao_Paulo`, define o "hoje" quando o pedido não traz data), `TRIAGEM_MAX_RETRIES` (padrão 1) e `TRIAGEM_CORS_ORIGENS` (lista separada por vírgula; o CORS fica desligado por padrão).

## Variáveis de ambiente (roteador e histórico)

| Variável | Padrão | Efeito |
|---|---|---|
| `TRIAGEM_MODELO` | `auto` | `auto` (roteador), `claude-sonnet-5-5` ou `claude-haiku-4-5-20251001` |
| `TRIAGEM_ROTEADOR_MAX_CARACTERES` | `1500` | Texto maior vai direto ao Sonnet |
| `TRIAGEM_ROTEADOR_CONFIANCA_MINIMA` | `media` | Confiança do Haiku abaixo disto escala (`alta` escala também `media`) |
| `TRIAGEM_ROTEADOR_PRIORIDADES` | `critica` | Prioridades do Haiku que escalam, separadas por vírgula (ex.: `critica,alta`) |
| `TRIAGEM_DB` | `data/triage.db` | Caminho do SQLite; `off` desliga o histórico |
| `TRIAGEM_DB_GUARDAR_TEXTO` | (vazio) | `1` grava também o texto do chamado |
| `TRIAGEM_HASH_SEGREDO` | (vazio) | Segredo do HMAC do hash do texto; sem ele, o hash é SHA-256 simples |

Um valor inválido gera erro de configuração (exit 2 na CLI; o servidor não sobe). Se o banco não abrir, a triagem continua sem histórico.

## Publicar a demo estática (Vercel)

Só a pasta `web/` vai para a Vercel, como site estático. A API Python e o banco continuam locais, e nenhuma chave vai para `web/`. A página `web/index.html` ainda não existe; por enquanto, a pasta tem só o `vercel.json`. Com a página pronta e o OK do Theo, publique **sempre de dentro de `web/`**:

```powershell
cd web; vercel deploy --prod --yes
```

Se alguém rodar `vercel` da raiz por engano, o `.vercelignore` da raiz ignora tudo e libera só `web/`. Ficam de fora `.venv`, `data/`, `docs/`, `src/` e os arquivos pessoais, e mesmo dentro de `web/` são ignorados `.env` e `.db`.

Antes do deploy, confira que `web/` não tem `.env`, `.db` nem dados reais.
- **Segurança:** a API não tem autenticação. Ela só deve escutar em `127.0.0.1`.

## Testes

```powershell
pytest
```

Os testes usam o cliente falso (`src/triagem/simulado.py`) e não chamam a API. `tests/test_web.py` precisa do extra `[web]`; sem ele, o teste é pulado. `tests/test_api_real.py` só roda quando `ANTHROPIC_API_KEY` existe.

## Limitações

- **Simulado não é medição.** O modo `--simulado` devolve o gabarito, com tokens e confiança fictícios. Ele demonstra o fluxo, o roteamento e a conta de custo, mas não mede acerto nem latência. A decisão sobre o roteador só vale com um `avaliar --comparar` real.
- **Roteador ainda não calibrado.** O limiar de confiança, o limite de caracteres e as prioridades escaláveis são valores iniciais. A confiança é autodeclarada pelo modelo e pode ser mal calibrada; para checar, use `acerto_por_confianca` no relatório. O padrão já é `auto` por decisão do Theo, antes da primeira medição real.
- **Crítico subestimado pelo Haiku.** Escalar `critica` só corrige alarme falso. Se o Haiku classificar um crítico como `media` com confiança alta, o chamado não escala. Se a medição real mostrar críticos subestimados, acrescente `alta` em `TRIAGEM_ROTEADOR_PRIORIDADES`.
- **Gabarito pequeno.** 14 exemplos fictícios. Um 8/10 pode ser sorte; a fase 2 prevê 50 ou mais chamados reais.
- **API local, sem autenticação.** O servidor deve escutar só em `127.0.0.1`. Antes de expor fora da máquina, é preciso autenticação e limite de taxa.
- **Um chamado por vez no HTTP.** Lotes só pela CLI.
- **Detecção de promessas por padrões.** A checagem da resposta sugerida usa expressões regulares e pode deixar passar formulações novas. Um humano revisa antes de enviar.

## Resultados com a API real

**Pendente.** Até 05/10/2026 não houve rodada real (`ANTHROPIC_API_KEY` não estava definida). Ainda faltam acerto, críticos subestimados, custo médio, p95 e estabilidade do roteador, do sempre-Sonnet e do sempre-Haiku, e a calibração dos tokens do simulado.

| Execução | Acerto conjunto | Críticos subestimados | Estabilidade (3×) | Custo médio | Economia vs Sonnet | Exit |
|---|---|---|---|---|---|---|
| `pytest` | 224 passaram, 1 pulado (`test_api_real`, sem chave) | | | | | 0 |
| `avaliar --simulado` (auto) | 14/14 | 0 | não medida | US$ 0,0060 (fictício) | | 0 |
| `avaliar --simulado --repeticoes 3` | 14/14 | 0 | 14/14 | US$ 0,0060 (fictício) | | 0 |
| `avaliar --simulado --comparar` (roteador) | 14/14 | 0 | | US$ 0,0060 | 21,3%: **falhou** (meta ≥ 40%) | 1 |
| `avaliar` sem chave | — | — | — | — | — | 2 (`configuracao`) |
| Roteador, real | pendente | | | | | |
| Sonnet 5.5, real | pendente | | | | | |
| Haiku 4.5, real | pendente | | | | | |

No `--comparar` simulado, o roteador escala 42,9% dos chamados (6 de 14, todos por confiança baixa). Com os custos médios simulados, a meta de 40% de economia exigiria escalar no máximo 24,1% (`escalonamento_maximo_para_meta`).

O modo simulado devolve o próprio gabarito, com confiança e tokens fictícios. Os 14/14 e a economia acima só provam que o fluxo, o relatório, a conta de custo e os códigos de saída funcionam. Não dizem nada sobre o acerto nem sobre o custo real do modelo.
