# Piloto de concorrência e desempenho

Implementado em 19/10/2026. O piloto é **local e sintético**. Não acessa o
Instagram, não abre a sessão da observadora e não habilita outros alvos reais.
O coletor Playwright permanece separado. Receber pedidos em paralelo não
significa coletar 50 perfis simultaneamente na plataforma.

## Implementação

- `backend/pilot/queue.py`: SQLite em WAL, transações `BEGIN IMMEDIATE`,
  persistência `synchronous=FULL` e fila limitada a 100 trabalhos ativos.
- `backend/pilot/api.py`: HTTP apenas em `127.0.0.1`, até 64 threads de
  atendimento, corpo JSON limitado a 4 KiB e token de admissão local.
- `backend/pilot/worker.py`: um executor exclusivo por banco, lock de arquivo
  liberado pelo sistema operacional e aquisição atômica do trabalho.
- `scripts/pilot_server.py`: inicia API e executor sintético. Não existe flag
  para conectar o piloto ao Instagram.

Cada solicitação recebe um ticket e um segredo aleatório de 256 bits; apenas
o hash do segredo fica no banco. O segredo de um ticket não acessa outro,
mesmo quando compartilham a mesma execução. Não há tokens em URLs, logs de
acesso ou relatórios do benchmark. O token de admissão é de uso do operador,
nunca deve ser embutido em frontend público. Isso não é um sistema de login.

Pedidos equivalentes compartilham uma execução ou resultado recente (TTL de
60 segundos). A chave inclui alvo, versão do simulador e meta de cobertura.
Tickets são distintos; cancelar um não cancela os demais. Se todos cancelam,
o trabalho em espera é removido da execução, ou o ativo recebe sinal de
cancelamento cooperativo. São permitidos até 1.000 tickets por trabalho.

Resultados preservam momento, estado e insuficiência dos dados. O simulador
retorna **zero registros reais**, `simulation=true`, `integrity_verified=false`
e `non_followers=null`. `completed` significa conclusão do trabalho sintético,
nunca comprovação da integridade de listas do Instagram.

Se o executor termina durante um trabalho, a próxima inicialização marca-o
como interrompido e pausa a fila. Não existe repetição automática. Uma
restrição simulada ou erro do coletor também pausa a fila globalmente. A
retomada explícita libera apenas trabalhos pendentes; não reexecuta o
interrompido. Uma falha não é armazenada no cache como sucesso.

## Teste de 50 clientes

Executar, sem tokens nem navegador autenticado:

```powershell
.\.venv\Scripts\python -m scripts.benchmark_pilot
```

O script cria banco temporário, servidor HTTP loopback e 50 clientes em
threads liberadas por uma barreira. Faz POSTs simultâneos, consulta resultados
por HTTP, verifica isolamento e faz uma segunda leva para testar cache.
O relatório agregado não inclui tickets ou segredos:
`reports/pilot/benchmark.json` (ignorado pelo Git).

Medição local após organizar as escritas concorrentes no banco:

| Métrica | 50 alvos sintéticos distintos | 50 pedidos do mesmo alvo |
| --- | ---: | ---: |
| Execuções do simulador | 50 | 1 |
| Coletores ativos ao mesmo tempo | 1 | 1 |
| Tickets isolados | 50 | 50 |
| Cache na segunda leva | 50/50 | 50/50 |
| Mediana para aceitar pedido | 176,538 ms | 170,880 ms |
| p95 para aceitar pedido | 312,419 ms | 305,910 ms |
| Duração do cenário | [duração de coleta omitida] | [duração de coleta omitida] |

O serviço sintético tem duração nominal de **0,02 s**, além de persistência
e escalonamento. Esses segundos não estimam a duração de coleta real. O p95
é o percentil da amostra local de 50 pedidos, não um SLA de produção.

Antes do controle local de escritores, o p95 foi 1.807,134/2.255,585 ms.
Threads disputavam o único escritor do SQLite e esperavam pelo backoff do
banco. Um lock de escrita por instância organiza a disputa dentro do processo;
as transações continuam garantindo exclusão entre processos. Leituras não
adquirem esse lock. Mantivemos a mesma persistência. São medições locais de
execuções distintas, não uma garantia de ganho em qualquer servidor.

O primeiro teste encontrou e corrigiu um cálculo de espera negativa no
simulador quando a persistência excedia sua duração nominal. Também foi
corrigido o fechamento de conexão no teste de retenção de segredos. Os testes
de concorrência passaram após as correções, sem consultas reais.

## Executar a API manualmente

Não é necessário instalar dependências novas: o piloto usa a biblioteca
padrão do Python. Gere uma chave apenas no ambiente do terminal:

```powershell
$env:BYEFAN_PILOT_TOKEN = (& .\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))")
.\.venv\Scripts\python -m scripts.pilot_server --synthetic
```

O servidor fica em primeiro plano até Ctrl+C. Para utilizar outro terminal
como cliente, configure nele o mesmo token localmente, sem publicá-lo no chat
ou no repositório. Os exemplos abaixo pressupõem essa configuração:

```powershell
$headers = @{ Authorization = "Bearer $env:BYEFAN_PILOT_TOKEN" }
$ticket = Invoke-RestMethod http://127.0.0.1:8765/jobs -Method Post -Headers $headers -ContentType application/json -Body '{"username":"fixture_0","coverage_target":98}'
$access = @{ Authorization = "Bearer $($ticket.access_token)" }
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($ticket.ticket_id)" -Headers $access
# Opcional: cancela somente este ticket.
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($ticket.ticket_id)" -Method Delete -Headers $access
```

Somente `fixture_0` a `fixture_99` são aceitos. `perfil_autorizado` é rejeitado
pelo piloto para impedir que um teste de carga alcance a sessão real.
`GET /health` exige o token do operador; GET/DELETE do ticket exigem o segredo
desse ticket. Não há endpoint que liste os resultados de todos os clientes.

```powershell
# Retomada administrativa; não repete trabalhos interrompidos.
.\.venv\Scripts\python -m scripts.pilot_server --synthetic --resume
# Manutenção offline: remove trabalhos terminados há mais de 24h e seus tickets.
.\.venv\Scripts\python -m scripts.pilot_server --synthetic --purge
```

O banco fica em `reports/pilot/queue.sqlite3`. Não é criptografado: proteja-o
com as permissões locais do sistema. A limpeza é explícita, não agendada.

## Experimento de rendimento por rolagem

O runner híbrido agora aceita `--viewport-height 800|1000|1200` e registra
o tamanho da janela no relatório. O padrão continua **800**, com largura
390, passo de 75% do contêiner, pausa mínima de 1.100 ms e todas as validações
anteriores. Não foi executada coleta real com a opção nova.

```powershell
# Dry-run: não abre navegador nem acessa Instagram.
.\.venv\Scripts\python -m scripts.hybrid_collector --viewport-height 1200
# Página HTML fictícia; contexto novo, headless, todas as requisições bloqueadas.
.\.venv\Scripts\python -m scripts.benchmark_viewport
```

A fixture disponibiliza lotes de 12 registros após carregamento assíncrono,
mantém apenas as linhas próximas ao viewport montadas e possui uma referência
conhecida de 511 nomes/IDs fictícios. Seu contêiner cresce com a janela.

| Altura da janela | Altura do contêiner fictício | Ciclos | Registros conferidos |
| --- | ---: | ---: | ---: |
| 800 px | 400 px | 84 | 511/511 |
| 1.000 px | 600 px | 83 | 511/511 |
| 1.200 px | 800 px | 42 | 511/511 |

Na fixture, a janela maior reduziu os ciclos pela metade sem perder registros.
A relação não é linear: 1.000 px quase não ajudou, pois os lotes e o ponto de
carregamento afetam a rolagem. **Não é prova de redução de 50% no Instagram**:
o diálogo real pode manter altura fixa, mudar de layout ou impor outro ritmo
de carregamento. O benchmark omite a pausa de produção para terminar rápido;
seus tempos não são estimativas de tempo real. Relatório local em
`reports/pilot/viewport-benchmark.json`.

## Limites e próximo marco

Validação automatizada final: **127 testes, 126 aprovados e um teste real
opt-in ignorado**, em [duração de coleta omitida]. A variável de habilitação do teste de rede foi
removida somente do processo de teste. Foram cobertos isolamento de tickets,
cache/expiração, limite de fila, cancelamento compartilhado e ativo, restrição,
erro sanitizado, recuperação, retenção e lock entre processos. Os testes HTTP
cobrem autenticação, rejeição de alvos reais, campos inesperados, tamanho do
corpo e 50 clientes. Os testes de janela validam o dry-run e a fixture
virtualizada. Nenhum teste mede capacidade real adicional do Instagram.

Esta etapa valida admissão simultânea, persistência, cache e isolamento no
computador local. Não valida 50 coletas reais concorrentes. Nenhuma quantidade
segura de abas, coletas/hora ou consultas/dia foi inferida.

Antes de ligar um worker real: adapter de relatório com validação existente,
lock da sessão compartilhado também com o CLI manual, cancelamento cooperativo
no navegador, limites totais e autorização de execução explícita. Não basta
trocar o simulador por `run_once`: o contrato e o escopo de acesso são diferentes.
O piloto rejeita resultados que não sejam explicitamente sintéticos.

Antes de disponibilizar na internet: servidor de aplicação adequado, TLS,
controle de admissão por cliente, retenção automática, monitoramento, teste
prolongado de falhas e infraestrutura, e autorização de uso dos dados. O HTTP
da biblioteca padrão é apenas para este piloto local. Não foi feito deploy.

O próximo teste real sugerido é **uma amostra limitada de `perfil_autorizado`
com viewport maior**, observando altura real, registros por ciclo, cobertura
e carregamentos. Exige autorização específica; não está agendado. Se a altura
não mudar ou os registros forem perdidos, conservar o tamanho atual. Para
50 consultas reais rápidas e independentes, a dependência de uma única sessão
continua sem solução comprovada; ver a pesquisa de concorrentes.
