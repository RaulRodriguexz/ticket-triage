## Categorias (lista fechada)

| Código | Nome | Critério objetivo |
|---|---|---|
| `acesso` | Acesso e login | Login, senha, conta bloqueada, permissão de usuário, 2FA, convite de usuário, quando **o sistema funciona** mas aquela pessoa não consegue entrar ou fazer a ação por causa de credencial ou permissão |
| `cobranca` | Cobrança e financeiro | Fatura, boleto, cobrança, estorno, plano, cancelamento de assinatura, nota fiscal **da própria assinatura** |
| `bug_tecnico` | Defeito técnico | O sistema se comporta errado: erro, falha ao salvar, lentidão anormal, indisponibilidade, texto ou layout errado |
| `duvida_uso` | Dúvida de uso | Pergunta sobre como usar algo que **já existe** no produto |
| `sugestao` | Sugestão de melhoria | Pedido de funcionalidade nova ou de mudança em algo que funciona como projetado |
| `outro` | Outro | Nada acima: proposta comercial, spam, elogio sem pedido, assunto alheio ao produto |

**Regras de desempate (aplicar em ordem):**
1. Se o sistema está indisponível ou com erro **para vários usuários**, use `bug_tecnico`, mesmo que o texto diga "não consigo entrar".
2. Se o problema é de credencial ou permissão de um usuário específico, use `acesso`. Isso inclui falha no fluxo de recuperação de senha (e-mail de redefinição que não chega) relatada por **um** usuário: o sintoma é de acesso e um relato isolado não prova defeito do sistema. Só vira `bug_tecnico` se o texto indicar vários usuários afetados ou mensagem de erro do sistema (regra 1).
3. Se o chamado mistura assuntos, classifique pelo problema que **impede** o cliente de trabalhar ou que envolve dinheiro.
4. Se o cliente pergunta "como faço X" e X não existe, use `sugestao`.

## Prioridades (lista fechada)

A prioridade mede **impacto e prazo concretos**, não o tom do texto. Palavras como "URGENTE", exclamações ou pedidos explícitos de prioridade dentro do chamado **não** alteram a classificação. Prazos ("hoje", "dia 15") são calculados em relação à `data_referencia`.

| Código | Critério objetivo (basta uma condição) |
|---|---|
| `critica` | Operação do cliente **parada**: nenhum usuário (ou o único responsável) consegue executar uma função essencial (vender, emitir nota, acessar o sistema) **e** não há contorno; **ou** existe prazo legal ou fiscal **no mesmo dia** que será perdido |
| `alta` | Função essencial com defeito que afeta o trabalho, mas **há contorno custoso ou manual**; **ou** o cliente já teve **perda financeira efetivada** (cobrança indevida ou duplicada); **ou** existe prazo que vence em **até 1 dia** (amanhã) a partir da `data_referencia` |
| `media` | Afeta **um** usuário ou uma tarefa não essencial, sem perda financeira, e pode esperar até 3 dias úteis; **ou** existe prazo que vence em **2 dias ou mais** a partir da `data_referencia` |
| `baixa` | Nada está impedido: dúvida de uso, sugestão, erro cosmético, contato comercial, elogio |

A palavra "urgente" sozinha não eleva a prioridade.
