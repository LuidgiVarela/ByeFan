# Piloto real pelo site local

## O que está pronto

O navegador local pode solicitar **uma tentativa real** do coletor híbrido
[detalhes de relacionamento omitidos por privacidade]
é: botão local → admissão de tentativa única → worker → Chromium visível →
coletor → comparação por ID → lista parcial na tela.

A implementação foi testada offline e passou por **uma tentativa real
autorizada de ponta a ponta**, em [resultado omitido]/[resultado omitido]/[resultado omitido]: [resultado omitido]/[resultado omitido] seguidores e [resultado omitido]/[resultado omitido]
seguindo em [resultado omitido] s. A tela exibiu [resultado omitido] candidatos, mantendo o estado
parcial. Veja [as evidências e limitações](local-live-result.md).

Isso aproxima o projeto de um piloto para o proprietário. Não permite ainda
lançar consultas para qualquer perfil ou prometer resultados definitivos.
A prévia hospedada permanece estática, com exemplos fictícios; não recebeu
credenciais, relatórios reais nem acesso ao servidor local.

## Executar uma tentativa autorizada

Pré-requisitos: dependências já instaladas, Chromium do Playwright disponível
e sessão local existente. Este comando não realiza login nem pede senha.
Só execute depois de autorizar uma nova tentativa controlada.

```powershell
.\.venv\Scripts\python -m scripts.site_preview --port 8767 --live-attempt NOME_DA_NOVA_AUTORIZACAO
```

Abra `http://127.0.0.1:8767`, escolha **Abrir piloto real local** e clique em
**Consultar @perfil_autorizado**. Abrir a página ou iniciar o servidor não acessa
o Instagram. O clique consome a tentativa e abre o Chromium visível.

- Alvo e observadora são fixos. O navegador não pode enviar outro @.
- Meta de cobertura: 98%; isso não é uma medida de precisão.
- Até 710 segundos de trabalho e 300 ciclos por lista, mantendo as pausas
  existentes. Inicialização e fechamento do navegador podem adicionar tempo.
- Não há repetição, login automático, requisição privada reproduzida,
  proxy, Apify ou cobrança.
- Restrição, autenticação adicional, desafio ou inconsistência impedem a
  liberação do resultado e encerram a tentativa. Resolva verificações pela
  interface oficial, sem automação; uma nova tentativa precisa de autorização.

A tela mostra tempo e quantidades durante a coleta, sem porcentagem fictícia
de conclusão. Ao terminar, apresenta as ausências observadas como candidatos.
Uma execução normal não remove a exigência de referência independente e
repetição consistente para afirmar integridade.

## Cancelamento e encerramento

**Cancelar consulta** solicita parada cooperativa. Durante rolagem normal,
a verificação ocorre a cada ciclo; na espera por dados, a cada intervalo de
500 ms. Uma navegação ou chamada síncrona já em andamento precisa terminar
ou atingir seu timeout (navegação limitada a até 30 s). O botão não deve
afirmar que o navegador já parou antes de receber o estado terminal.

Ctrl+C no servidor também solicita cancelamento e espera o worker fechar o
Chromium. Nenhuma chamada Playwright é feita pela thread HTTP.
Fechar ou recarregar a aba do site não cancela a coleta: o ticket está apenas
na memória da página. Se perder o acompanhamento, use Ctrl+C no servidor;
os checkpoints permanecem no disco. Não inicie outro teste para recuperar
o acompanhamento.

## Tentativa única e sessão exclusiva

O nome escolhido gera um recibo exclusivo sob
`reports/live-pilot/`, antes de iniciar o worker. Esse nome fica consumido
mesmo após falha, reinício do servidor ou perda da resposta HTTP. Um segundo
POST não inicia uma nova coleta. Não apague o recibo para repetir; somente
uma nova autorização pode ser associada a um novo nome de tentativa.
`piloto-local-01` já foi utilizado no teste documentado e não pode ser reutilizado.

O CLI `scripts.hybrid_collector --execute` e o adaptador do site compartilham
um bloqueio de sistema operacional fora do repositório. Se a sessão estiver
em uso por um deles, o outro falha antes de abrir Chromium. Coletores antigos
de outras etapas não foram convertidos para esse bloqueio; mantenha-os
encerrados, assim como janelas manuais usando o mesmo perfil de testes.

A fila sintética continua isolada. Não foi transformada em um serviço
multiusuário nem aceita alvos reais. A admissão de 50 solicitações simultâneas
foi exercitada com runner fictício: apenas uma entra; as demais são recusadas.
Esse teste não representa 50 consultas reais simultâneas.

O piloto habilitado usa outro banco para a demonstração sintética, de modo
que a prévia já aberta em 8766 possa continuar funcionando. A coleta real
continua limitada pelo recibo e pelo bloqueio exclusivo da sessão híbrida.

## Dados e acesso

Checkpoints e relatório completo seguem em `reports/hybrid-collector/`,
ignorados pelo Git. O recibo não guarda tokens nem nomes das pessoas coletadas.
A sessão permanece no diretório de perfil local existente, fora do repositório.

A API serve somente em `127.0.0.1`. Início e cancelamento exigem Origin exata,
header específico e corpo vazio. Consulta e cancelamento de um ticket exigem
sua capacidade aleatória. Erros usam códigos controlados, sem texto bruto
de exceção, cookies, senhas ou corpos de respostas do Instagram.
Essa proteção é para o piloto local; não substitui autenticação, HTTPS e
isolamento de clientes de um serviço público. Não exponha essa porta à internet.

Para reabrir um relatório terminado sem nova coleta:

```powershell
.\.venv\Scripts\python -m scripts.site_preview --saved-report reports/hybrid-collector/result-ID_DA_EXECUCAO.json
```

## Próximos critérios de liberação

Validação offline desta implementação: **159 testes executados, 158 aprovados
e um teste real opt-in ignorado**, em [duração omitida]. A variável que habilita o teste
de rede foi removida do processo de testes. Os novos casos cobrem admissão
concorrente, recibo persistente após reinício, sessão ocupada antes do browser,
cancelamento, restrições, erros sem dados sensíveis, proteção HTTP e fluxo
completo da interface móvel usando runner fictício. A página não acessou
nenhum domínio externo nos testes de navegador. O JS passou na verificação
de sintaxe. Isso não é validação da coleta atual no Instagram.

[resultado omitido]. **Concluído:** uma tentativa autorizada de ponta a ponta pela interface
   local, medindo tempo, cobertura, erros e funcionamento da sessão existente.
[resultado omitido]. Conferência independente do proprietário para avaliar falsos candidatos.
   Cobertura de 98% não prova 98% de precisão.
3. Somente depois, admissão para novos perfis expressamente autorizados,
   isolamento de resultados, capacidade real e operação adequada fora do
   computador do proprietário. A plataforma ainda determina acesso e limites.
