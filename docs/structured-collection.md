# Etapa 10 — investigação de coleta estruturada (18/10/2026)

Pesquisa de código e documentação; **nenhuma consulta ao Instagram foi feita
nesta etapa**. O módulo `backend/experimental/structured_lists.py` só interpreta
respostas fictícias offline. Ele não contém transporte, sessão ou credenciais.

## Comparação dos métodos

| Método | Sessão observadora | Paginação e IDs | Evidência atual |
| --- | --- | --- | --- |
| aiograpi `private_graphql_*_list` | Sim, sessão privada móvel | `next_max_id`, `pk`/`pk_id` | Biblioteca documenta e implementa ambas as listas; não foi validada no ByeFan. |
| aiograpi `user_*_v1` | Sim, sessão privada móvel | `next_max_id`, `pk`/`pk_id` | Implementação direta com páginas e deduplicação; não foi validada no ByeFan. |
| aiograpi `user_*_gql` legado | Host web, mas injeta sessão salva quando disponível | `page_info.end_cursor`, `id` | O rótulo “Public Graphql API” não comprova acesso anônimo atual; `query_hash` legado é frágil. |
| adrigo/unfollowers-for-instagram | Sim, sessão ativa do próprio navegador | `next_max_id`, `pk`/`pk_id` | Projeto usa REST privado v1 para as duas listas e relata limites de paginação; não foi validado no ByeFan. |
| API oficial Meta | Autorização específica da plataforma | Contagens de perfil, não listas completas aqui | Não foi encontrada rota oficial documentada que entregue essas duas listas para um visitante que fornece apenas @. |

O guia do [aiograpi sobre `doc_id`](https://github.com/subzeroid/aiograpi/blob/main/docs/usage-guide/private-graphql.md)
distingue duas superfícies: a hospedada em `www.instagram.com` e a GraphQL
privada móvel em `i.instagram.com`. **As operações de seguidores e seguindo
documentadas no guia são da segunda superfície**. `public_doc_id_graphql_request`
significa host público, não ausência de autenticação: o próprio guia explica
que essa função encaminha cookies de uma sessão salva para consultas que os
exigem. O README do projeto também mostra login antes do exemplo de lista.
O código de [aiograpi/mixins/user.py](https://github.com/subzeroid/aiograpi/blob/main/aiograpi/mixins/user.py)
extrai `data.xdt_api__v1__friendships__followers` ou `...following`, lê `users`
e passa `next_max_id` à chamada seguinte. Os helpers v1 usam o mesmo cursor
na API privada móvel. Os métodos web antigos usam `query_hash` e `end_cursor`,
mas chamam `inject_sessionid_to_public()`; logo, sua presença no código não é
prova de coleta anônima. A [documentação de usuários](https://github.com/subzeroid/aiograpi/blob/main/docs/usage-guide/user.md)
ainda lista esses helpers legados, sem demonstrar cobertura anônima atual.

O [README do projeto adrigo](https://github.com/adrigo/unfollowers-for-instagram)
afirma que suas consultas antigas GraphQL de relacionamento deixaram de
funcionar e que a versão atual usa a API REST privada v1 na aba autenticada.
O [código da consulta](https://github.com/adrigo/unfollowers-for-instagram/blob/main/src/api.ts)
faz `fetch` com credenciais da aba, normaliza `pk_id`/`pk` para string e segue
`next_max_id`. Segundo seus autores, seguindo com ordenação por data às vezes
vem numa única resposta, enquanto seguidores são servidos em páginas pequenas
mesmo com tamanho maior solicitado. Isso é observação do projeto, **não uma
garantia da plataforma** nem medição do ByeFan. Seu critério de cursor ausente
ou repetido como fim não prova cobertura integral; nosso avaliador marca cursor
repetido como incompleto.

Há evidência de compatibilidade **pontual**, mas não de estabilidade: o
[changelog do aiograpi](https://github.com/subzeroid/aiograpi/blob/main/CHANGELOG.md)
registra correções de `doc_id` e testes reais com contas configuradas; também
avisa que IDs de consulta e formatos mudam. Isso não demonstra que a sessão
`conta_observadora` possa usar a superfície móvel, que ambos os conjuntos sejam
acessíveis para qualquer perfil público ou que os resultados sejam completos.
Na Etapa 8 do ByeFan, a navegação normal recebeu JSON estruturado, mas a
sonda examinou só parte das respostas e não identificou cobertura integral.

Nenhum dos projetos demonstra que **apenas um username, sem conta observadora**, 
consiga buscar as duas listas integralmente hoje. O método estruturado mais
promissor para um experimento próprio é o **REST privado v1 com a sessão
legítima da conta observadora**, pois há implementação independente em ambos
os projetos e ele expõe IDs e cursor. O GraphQL privado móvel com `doc_id` é
uma segunda hipótese, com a desvantagem adicional da rotação do identificador
registrado. Ambos poderiam reduzir a rolagem visual se as respostas viessem
completas, mas isso ainda não foi testado no ByeFan; ambos exigem autenticação.

## Integridade e produto

Um `next_max_id` ausente prova apenas que a fonte parou de fornecer páginas.
Contagem igual também não prova que os mesmos perfis foram recuperados:
duplicatas, mudanças durante a coleta e páginas omitidas podem se compensar.
O experimento offline preserva `pk`/`pk_id`, detecta IDs ou usernames
conflitantes, deduplica por ID, marca interrupção, cursor repetido e divergência
de contagem. Mesmo quando o cursor termina e as contagens coincidem, o estado
é `terminal_unverified`, nunca `integrity_verified=True`. A comparação
definitiva depende de referência independente contemporânea e repetição
consistente. O comparador existente por ID pode ser usado somente após essa
validação.

O risco comercial é alto: a arquitetura dependeria de uma conta observadora
autenticada, de superfícies privadas sem contrato estável, de permissões de
acesso e do tratamento de dados de terceiros. Os próprios autores do aiograpi
[desaconselham tratá-lo como base de negócio](https://github.com/subzeroid/aiograpi).
A [Meta descreve a coleta automatizada não autorizada como violação de seus
termos](https://about.fb.com/news/2021/04/how-we-combat-scraping/). Ser dono do
perfil-alvo autoriza o teste do proprietário, mas não concede por si só
permissão da plataforma para um serviço comercial. Esta é uma avaliação
técnica de risco, não uma conclusão jurídica.

**Próximo experimento real proposto, sujeito a autorização separada:** com
[detalhes de relacionamento omitidos por privacidade]
legítima é aceita em uma consulta estruturada **pequena** de cada direção.
Registrar status, tempo, número de `pk` e usernames, cursor e eventual
restrição; interromper em login adicional, desafio, 401/403/429 ou resposta
inesperada. Não importar cookies do Playwright para outra API, não automatizar
novo login, não paginar além da amostra sem nova autorização e não tentar
contornar a plataforma. Para comprovar completude depois, obter uma referência
independente do proprietário e autorização explícita para uma travessia
controlada e uma repetição. Nenhuma dessas chamadas foi implementada ou
executada nesta etapa.

Testes offline: `python -m unittest tests.test_structured_lists -v`.
