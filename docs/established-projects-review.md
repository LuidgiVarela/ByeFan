# Projetos existentes: caminhos sem exportação manual

Atualização: o proprietário preferiu manter a entrada apenas por @. A
[inspeção anônima posterior](anonymous-initial-inspection.md) teve falha de
interação e resultado inconclusivo. A proposta de sessão local neste documento
permanece alternativa não adotada; nenhuma extensão foi instalada.

Pesquisa em 20/10/2026 UTC. Leitura de código, metadados públicos do GitHub e
descrição de produto; nenhum script externo foi executado, nenhuma sessão
foi conectada e nenhuma consulta nova foi enviada ao Instagram. A demora de
quatro dias é a experiência relatada pelo proprietário, não um prazo medido
ou garantido da plataforma. Exportação manual deixa de ser a recomendação
para a experiência principal.

## O que existe de concreto

| Referência | Evidência inspecionada | Dependência principal |
| --- | --- | --- |
| [InstagramUnfollowers](https://github.com/davidarroyo1234/InstagramUnfollowers) | 4.964 estrelas; commit `29b0c071ee3865d5b409c57f50a36ada680e66e7`, de 18/10/2026 UTC; licença MIT | Executa na aba autenticada do usuário e consulta REST privado. |
| [adrigo/unfollowers-for-instagram](https://github.com/adrigo/unfollowers-for-instagram) | 4 estrelas; commit `0089bf6042be1306afd17e030acbc5d27a37cf7f`, de 27/09/2026 UTC | Extensão/script na sessão do usuário; REST privado. Referência pequena, não produto consolidado demonstrado. |
| [instagrapi](https://github.com/subzeroid/instagrapi) | 6.987 estrelas; commit `59107b13e200dc7b32f93c4671601fcaff8f494c`, de 19/10/2026 UTC | Biblioteca não oficial, com sessão e desafios de autenticação. |
| [FollowMeter — App Store](https://apps.apple.com/us/app/followmeter-for-instagram/id651309421) | Publicador declara conta conectada em segundo plano e milhões de usuários | Código fechado: endpoints e arquitetura de servidores não foram auditados. |

Estrelas, atividade e presença em loja são sinais de adoção/manutenção, não
medições de precisão, disponibilidade ou lucro. Metadados são um retrato da
consulta. O README do adrigo declara MIT, mas não encontramos arquivo LICENSE
na árvore consultada; confirmar os termos antes de reutilizar código. O
[arquivo de licença do instagrapi](https://github.com/subzeroid/instagrapi/blob/59107b13e200dc7b32f93c4671601fcaff8f494c/LICENSE)
contém MIT e atribuições adicionais. Nenhum código foi incorporado.

## O que o código efetivamente faz

No [utilitário do InstagramUnfollowers](https://github.com/davidarroyo1234/InstagramUnfollowers/blob/29b0c071ee3865d5b409c57f50a36ada680e66e7/src/utils/utils.ts),
as consultas usam a sessão da aba, identificadores `pk`/`pk_id` e cursor
`next_max_id`. O comentário registra falhas do GraphQL legado, inclusive
respostas vazias com HTTP 200. O campo interno `follows_viewer` recebe um
resultado calculado: não deve ser confundido com evidência de reciprocidade
retornada diretamente pelo servidor.

No [fluxo principal](https://github.com/davidarroyo1234/InstagramUnfollowers/blob/29b0c071ee3865d5b409c57f50a36ada680e66e7/src/main.tsx),
primeiro são indexados seguidores, depois comparados os seguidos. Ausência de
cursor, `has_more=false` ou página vazia encerram a travessia como bem-sucedida;
não há ali comprovação independente de integridade. A indexação também aceita
username normalizado. Isso não garante ausência de falsos positivos. O fluxo
possui repetição após alguns erros, inclusive mensagens de checkpoint; esse
comportamento não será adotado no ByeFan, que deve interromper diante deles.

O [coletor do adrigo](https://github.com/adrigo/unfollowers-for-instagram/blob/0089bf6042be1306afd17e030acbc5d27a37cf7f/src/api.ts)
deduplica por ID, percorre cursores e usa ordenação por data para seguindo.
A alegação de receber essa lista inteira em uma resposta está no comentário
do autor, sem garantia para outras contas. Cursor repetido é tratado como
fim normal, uma condição que o ByeFan deve continuar tratando como suspeita.
O projeto envia consultas próprias, portanto não equivale à nossa captura
passiva das respostas da interface.

O [README do instagrapi](https://github.com/subzeroid/instagrapi/blob/59107b13e200dc7b32f93c4671601fcaff8f494c/README.md)
reconhece fragilidade em produção e recomenda uso em pesquisa e automação
interna controlada. Atualização recente da biblioteca não prova que a sessão
do ByeFan será aceita. Não reinstalamos nem iniciamos nova autenticação.

## Falhas documentadas e uma hipótese útil

- [Issue 364](https://github.com/davidarroyo1234/InstagramUnfollowers/issues/364):
  usuário relata interrupção em conta com mais de [resultado omitido] mil seguidores.
- [Issue 353](https://github.com/davidarroyo1234/InstagramUnfollowers/issues/353):
  relato de falsos positivos ao inferir reciprocidade pela primeira página da
  lista de outra pessoa. Não adotar esse atalho nem visitar outros perfis.
- [Issue 360](https://github.com/davidarroyo1234/InstagramUnfollowers/issues/360):
  propõe verificar candidatos pela busca na lista de seguidores do próprio
  titular. É um relato/experimento de terceiro, não validação nossa.

Hipótese para pesquisa: verificar relações específicas pode reduzir o trabalho
em contas com muitos seguidores e poucos seguidos. Encontrar o ID correto
nessa lista é evidência positiva; uma busca vazia permanece inconclusiva.
Fazer centenas de buscas individuais pode ser mais lento e produzir mais
requisições que paginar. Medir antes de escolher; não reproduzir endpoints nem
alterar restrições atuais para imitar o projeto.

## Recomendação para o ByeFan

Investigar **execução local na sessão do próprio titular**, com um componente
de navegador e a comparação que já temos. O usuário entra no Instagram pela
interface oficial; o ByeFan não recebe senha. A primeira variante deve ler
somente o conteúdo e as respostas disponibilizados pela navegação normal,
mantendo as restrições atuais. Isso reaproveita a PoC híbrida, sem prometer a
velocidade dos scripts que fazem consultas privadas próprias.

Um site comum não ganha acesso à sessão de outra origem só porque o usuário
está logado em outra aba: ver [isolamento entre origens](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy).
Uma [extensão com permissões explícitas](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
é uma possibilidade concreta para um piloto desktop. Ainda exige instalação
e avaliação de privacidade/distribuição; não resolve automaticamente o produto
mobile-first. Não recomendar colar scripts no console como fluxo comercial.

Inferência de capacidade: executar cada análise no dispositivo do respectivo
titular remove a disputa por uma única sessão observadora central. Não cria
quota ilimitada, não torna a coleta autorizada pela plataforma e não demonstra
50 consultas simultâneas. O backend de comparação e os limites da fonte devem
continuar sendo medidos separadamente.

## Experimento seguinte proposto, ainda não executado

1. Preparar adaptador local separado, restrito à própria conta, sem envio de
   credenciais, sem unfollow e sem consultas privadas reproduzidas.
[resultado omitido]. Testar offline IDs, cursores repetidos, respostas vazias contraditórias,
   mistura de alvos, cancelamento e falso negativo de busca.
3. Com participação e autorização específicas do proprietário, usar a sessão
   do titular `perfil_autorizado` e uma amostra de até 10 registros por direção.
   Essa sessão não está presumida disponível; `conta_observadora` não é o titular.
4. Medir tempo, registros únicos e interrupções; conferir uma amostra manual
   independente na interface oficial. Uma amostra não comprova listas completas.
5. Somente após esse resultado decidir uma extensão e a experiência no celular.

A [tentativa REST anterior](structured-rest-sample.md) falhou na consulta de
perfil, antes de consultar listas. Não prova inviabilidade universal do REST,
mas também não autoriza ignorar a recusa ou retentar. O próximo experimento
acima é uma hipótese diferente, sem promessa de sucesso.

**Decisão atual:** priorizar esta investigação sem ZIP. Sessão do próprio
usuário é a alternativa mais concreta encontrada; “qualquer @ sem participação
do titular” continua sem fonte comercial validada. A proposta de produto não
foi alterada no código e nenhuma nova coleta foi iniciada.
