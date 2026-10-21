# Modelos de coleta de concorrentes

Complemento: [auditoria de projetos existentes](established-projects-review.md),
com commits consultados, paginação, limitações de integridade e proposta de
piloto sem exportação manual na sessão do próprio titular.

Pesquisa em 19/10/2026, apenas páginas públicas de produto e lojas oficiais.
Nenhum aplicativo foi instalado, nenhuma conta foi conectada e nenhum
fornecedor foi contratado. Descrições comerciais são declarações dos
publicadores, não auditorias de arquitetura, precisão ou rentabilidade.

| Produto e fonte primária | Modelo declarado | O que podemos aproveitar |
| --- | --- | --- |
| [FollowMeter — Google Play](https://play.google.com/store/apps/details?id=com.beakerapps.instameter2) | Mantém a conta conectada em segundo plano; anuncia histórico e monitoramento. A loja exibe 5 milhões+ de downloads. | Progresso e histórico explícitos são úteis. Conta conectada muda a experiência: não demonstra consultas anônimas por @. |
| [UnfollowTool — site oficial](https://www.unfollowtool.com/) | Processa o ZIP exportado pelo Instagram no navegador, sem enviá-lo ao servidor; compara fotografias de momentos diferentes. | Processamento local distribui custo entre dispositivos e reduz dados centralizados. Exige exportação, fora da proposta atual do ByeFan. |
| [ListLift — Chrome Web Store](https://chromewebstore.google.com/detail/follower-following-export/egkbfngfmhjlioccokflclplnigbmlgm) | Declara captura do conteúdo carregado durante rolagem no navegador do usuário, com IDs; também oferece modo automático e informa limitações de paginação. | A captura passiva se aproxima da nossa PoC. Execução no dispositivo do proprietário pode distribuir trabalho, mas exige extensão/sessão local e não preserva a experiência de somente @. |

A página antiga de FAQ do FollowMeter apareceu indexada na pesquisa, mas
redirecionou para contato ao ser aberta. Não foi usada como confirmação da
implementação atual. A descrição atual da loja confirma somente a dependência
de conta conectada; não revela endpoints, divisão cliente/servidor ou quotas.

O ListLift faz alegações amplas de segurança e completude em sua divulgação.
Não adotamos como fatos garantias de ausência de bloqueio nem a afirmação de
que unir exportações produz uma lista completa: relações mudam com o tempo.
Ler passivamente não torna ilimitado o carregamento que o navegador faz.

## Consequência para o ByeFan

O tamanho da base instalada de um concorrente não prova que ele opere com
uma única conta observadora central. Nos modelos documentados, o usuário
fornece uma sessão/ambiente local ou um arquivo. Não encontramos, nessas
fontes, evidência reproduzível do conjunto **somente @ + sem login/ZIP + listas
integrais + alta concorrência + sem fornecedor pago**.

Inferência arquitetural: processar dados no dispositivo de cada proprietário
reduz a disputa por um coletor central. Não fornece licença de coleta nem
remove os limites de cada conta. Isso seria uma alternativa de produto a
avaliar explicitamente, não uma troca silenciosa dos requisitos e nem uma
infraestrutura de contas usadas para evadir limites.

Para manter a experiência atual, implementamos fila, compartilhamento de
trabalho e cache com validade explícita. Eles eliminam trabalho repetido;
não multiplicam a capacidade de leitura de alvos diferentes. A demanda por
50 alvos inéditos continua exigindo 50 coletas ou outra fonte de dados
autorizada. A capacidade dessa fonte ainda precisa ser demonstrada.

Recomendação: avançar no piloto fechado e em validação independente; conservar
a leitura passiva. Se a exigência comercial for 50 resultados de perfis
diferentes em poucos minutos, avaliar uma fonte autorizada com capacidade
comprovada ou renegociar a experiência para execução local do proprietário.
Não basear a promessa comercial em um suposto endpoint secreto dos concorrentes.
