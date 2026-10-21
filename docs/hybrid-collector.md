# Coletor híbrido experimental

O teste limitado de [auditoria passiva](passive-scroll-audit.md) mostrou lotes
JSON com IDs numéricos entregues ao Chromium durante a rolagem normal. O módulo
`backend/experimental/hybrid_collector.py` combina esses IDs com os usernames
vistos no diálogo do Instagram. Ele não monta chamadas a endpoints internos,
não reutiliza cookies fora do Chromium, não faz login e não usa outros perfis.
[detalhes de relacionamento omitidos por privacidade]

O comando padrão é **offline**:

```powershell
.\.venv\Scripts\python -m scripts.hybrid_collector
.\.venv\Scripts\python -m unittest tests.test_hybrid_collector -v
```

`--execute` inicia **uma tentativa real limitada**, usando o perfil local
autenticado e o Chromium visível. Ele só deve ser usado depois de autorização
específica do proprietário para cada tentativa. Os limites são 300 ciclos por lista, pausa de 1,1 s por ciclo e
orçamento de trabalho de 710 s. Não há repetição automática. Um checkpoint
atômico é salvo a cada ciclo em `reports/hybrid-collector/`, ignorado pelo Git.
O relatório final registra usernames, IDs, contagens, tempos, ciclos, eventos
de lotes correlacionados, perdas de resposta e erros sem gravar corpo JSON,
URLs, cookies ou tokens. Os relatórios locais contêm identificadores pessoais
dos perfis encontrados e devem ficar sob controle do proprietário.

```powershell
.\.venv\Scripts\python -m scripts.hybrid_collector --execute
```

O coletor interrompe diante de autenticação, verificação ou limitação explícita.
Três ciclos sem progresso agora iniciam uma espera passiva de até [resultado omitido] segundos,
sem nova rolagem ou requisição própria. Se aparecerem nomes, IDs ou crescimento
do conteúdo, a travessia continua dentro dos limites originais. Uma base com
contagem menor que a informada, carregamento pendente ou sinal de próxima
página é registrada como truncamento/parada, sem afirmar fim. Para chegar ao estado
`terminal_observed_unverified`, ambas as leituras de contagem do perfil devem
coincidir com a cardinalidade dos IDs; o conjunto de usernames da UI deve ser
idêntico ao dos lotes; não pode haver ID duplicado, conflito de identidade,
perda/limite de resposta nem interrupção. **Esse estado não é “completo”.**
A correlação de lotes por pelo menos dois nomes visíveis é uma heurística;
lotes de um único usuário exigem a correspondência desse nome e recebem um
marcador separado de correlação mais fraca.
Coincidência de contagens ou um cursor presente não prova cobertura integral.

Para uma comparação definitiva, seriam necessárias duas execuções autorizadas,
distintas e consistentes, e listas **independentes** de IDs conferidas pelo
proprietário no mesmo intervalo. O validador offline recebe os dois relatórios
e um JSON de referência com este formato (IDs fictícios):

```json
{
  "source": "independent_owner_reference",
  "username": "perfil_autorizado",
  "followers_ids": ["[resultado omitido]", "[resultado omitido]"],
  "following_ids": ["[resultado omitido]", "[resultado omitido]"]
}
```

```powershell
.\.venv\Scripts\python -m scripts.hybrid_collector --validate reports/hybrid-collector/result-PRIMEIRA.json reports/hybrid-collector/result-SEGUNDA.json reference-owner.json
```

O campo `source` é uma declaração do proprietário, não uma prova criptográfica
de procedência. Não produza a referência a partir dos próprios relatórios.
Sem referência independente, o validador retorna `insufficient_data` e
`non_followers: null`. Se todos os critérios passarem, ele usa o comparador
existente por ID estável. A repetição exige nova autorização; o validador não
faz nenhuma consulta ao Instagram.

## Versão 4: meta de cobertura configurável

O padrão passou a ser **98% de cobertura em cada lista**, escolhido para
priorizar utilidade/tempo. A coleta real continua opt-in e requer autorização
para uma tentativa. `--coverage-target 100` conserva o modo de travessia até
avaliar o fim. Para o perfil com [resultado omitido] seguidores e [resultado omitido] seguindo, a meta de [resultado omitido]%
exige pelo menos **[resultado omitido] e [resultado omitido] IDs**, respectivamente, correlacionados à UI.
O arredondamento é sempre para cima; os denominadores são relidos no fim.
Não se faz média entre as listas: [resultado omitido]% em seguindo e [resultado omitido]% em seguidores continua
sendo uma coleta insuficiente.

```powershell
# Ambos os comandos abaixo são offline.
.\.venv\Scripts\python -m scripts.hybrid_collector --coverage-target 98
.\.venv\Scripts\python -m scripts.hybrid_collector --analyze reports/hybrid-collector/result-ad622eaf65674ae4a4f0ef84bede44b2.json
```

Ao atingir a meta, o motivo de parada é `coverage_target_reached`, mantendo
`state: incomplete`, `terminal_observed: false` e `integrity_verified: false`.
O coletor não acelera a cadência de requisições. Ele pode economizar o final
da rolagem e a espera por registros além da meta. Conflitos entre IDs e nomes,
divergência UI/JSON, respostas pendentes/perdidas ou restrições impedem essa
parada por meta. Se o contador mudar até a leitura final, a meta é reavaliada;
não se inicia outra tentativa automaticamente.

O relatório inclui `coverage`, com percentuais, contadores, lacunas e motivos
por lista. **Cobertura não é precisão da identificação de não seguidores.**
Por exemplo, com duas listas verdadeiras de [resultado omitido] usuários, observar IDs [resultado omitido]–[resultado omitido]
em seguidores e [resultado omitido]–[resultado omitido] em seguindo dá [resultado omitido]% de cobertura de ambas. Os IDs [resultado omitido] e
[resultado omitido] aparecem na diferença, mas podem ser seguidores justamente não capturados:
as duas acusações estariam erradas. Por isso `precision_percent` continua
desconhecido e o comparador definitivo não é liberado por esse percentual.

Na reanálise offline do relatório real da versão [resultado omitido], seguindo tem [resultado omitido]% de
cobertura ([resultado omitido]/[resultado omitido]); seguidores tem [resultado omitido]%. As contagens de seguindo atingiram a
meta no ciclo [resultado omitido], com [resultado omitido] nomes e IDs. Depois dele houve [duração de coleta omitida] em ciclos
e [duração de coleta omitida] de espera passiva. Portanto cerca de **26 s da cauda poderiam ser
evitados naquele histórico**. Isso é uma projeção baseada em contadores,
sem simular todos os critérios de parada, e não uma nova medição de execução.

Foi corrigida também a associação de respostas atrasadas: a lista é fixada
no evento de início da requisição, não quando o corpo termina de chegar.
Respostas de seguidores não migram para seguindo só porque o diálogo mudou.
Uma restrição recebida depois da troca continua interrompendo a coleta.
Esse desenho segue o [ciclo de eventos de Request do Playwright](https://playwright.dev/python/docs/api/class-request).
Os testes usam eventos fictícios e não enviam novas consultas.

As métricas e observações vinculadas a perfis foram removidas desta cópia. A validação técnica usa fixtures locais e não exige novas consultas.
