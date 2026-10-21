# Recomendação de arquitetura comercial

Revisão em 20/10/2026 UTC, a partir da dúvida do proprietário sobre a
dependência de uma conta observadora. **Recomendação técnica**, não uma
mudança já aprovada da proposta de produto. Nenhum novo teste no Instagram,
cadastro externo, contratação ou publicação foi realizado nesta revisão.

## Conclusão

**Preferência posterior do proprietário:** preservar a entrada apenas por @.
A prioridade operacional voltou à [inspeção anônima](anonymous-initial-inspection.md),
cuja tentativa de 20/10/2026 foi inconclusiva por falha de interação. Sessão
local do titular permanece uma alternativa, sem adoção aprovada.

**Atualização de prioridade:** o proprietário descartou exportação manual
devido à demora e ao esforço. A [pesquisa de projetos existentes](established-projects-review.md)
recomenda investigar um piloto na sessão local do próprio titular, sem ZIP.
Portabilidade direta permanece uma possibilidade não validada, fora da
prioridade imediata. As questões abaixo ficam como referência.

Não recomendo lançar um serviço pago para qualquer @ com a conta
`conta_observadora` como coletor central. Ela é uma conta secundária legítima,
controlada pelo proprietário, mas o acesso que uma conta tem pela interface
não constitui um contrato de fornecimento de dados para o ByeFan.

Playwright é uma ferramenta adequada para a prova de conceito. O risco
comercial está em depender de uma sessão e de uma interface que não
controlamos, sem garantia de completude, disponibilidade ou vazão.
Trocar de biblioteca ou aumentar o número de servidores não resolve essa
dependência. Não se propõe rotação de contas ou qualquer contorno de limites.

A prova de conceito local demonstra que há um fluxo técnico funcional para um alvo autorizado. Ela não comprova viabilidade de um serviço comercial com qualquer perfil, sem login ou arquivo e com muitas consultas simultâneas. O marco atual é um piloto funcional, não um lançamento pronto.

## Fontes oficiais e limites da pesquisa

1. A Meta descreve a coleta automatizada sem sua permissão como violação dos
   seus termos e explica limites de acesso e medidas contra scraping em
   [How We Combat Scraping](https://about.fb.com/news/2021/04/how-we-combat-scraping/)
   (2021). É orientação oficial histórica, não leitura integral dos termos
   vigentes. Os termos atuais do Instagram retornaram 429 e os termos de
   coleta automatizada redirecionaram para login nesta pesquisa. Esses erros
   vieram da ferramenta de pesquisa, não da sessão observadora. Não se infere
   aprovação comercial a partir da autorização do dono do perfil.
2. O [objeto IGUser do SDK oficial](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/iguser.py)
   consultado expõe contadores e não métodos gerais `get_followers` ou
   `get_following`. A [coleção da Meta no Postman](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)
   descreve APIs para contas profissionais. Não identificamos nessa superfície
   um contrato para enumerar ambas as listas de qualquer perfil. Essa conclusão
   sobre a Instagram API convencional não exclui ferramentas distintas de
   portabilidade, parcerias ou futuras funcionalidades.
3. A DTI informou em abril de 2026 que a Meta usa os sinais do Data Trust
   Registry no processo de acesso às suas APIs de portabilidade:
   [DTR post-pilot](https://dtinit.org/blog/2026/04/28/dtr-now-post-pilot).
   Isso é evidência de uma rota de avaliação de destinatários, não prova de
   que o ByeFan está elegível, aprovado ou terá acesso às listas necessárias.
4. A Meta descreve exportação e transferência de informações do próprio usuário
   na Central de Contas em [Manage Your Information](https://about.fb.com/news/2023/10/manage-your-information-across-apps/)
   (2023). A página confirma a funcionalidade, mas o exemplo de transferência
   é de fotos/vídeos; não comprova o contrato de followers/following.

A busca encontrou relatos do próprio Fabric sobre uso de portabilidade para
conexões sociais. Não usamos a alegação de um fornecedor como garantia do
contrato da Meta, da cobertura geográfica ou da precisão. Não foi contratado
ou integrado nenhum intermediário. As páginas atuais de documentação de
portabilidade da Meta não ficaram acessíveis nesta consulta; nenhum endpoint,
scope, requisito de pagamento ou prazo de aprovação foi inventado.

## Alternativas e experiência do usuário

| Caminho | Participação do usuário | Situação e recomendação |
| --- | --- | --- |
| Observadora central + Playwright | Apenas @ na frente; sessão nossa por trás | PoC funcional. Não recomendado como fundação comercial nas condições demonstradas. |
| Instagram API convencional | Autorização oficial; requisitos de conta | Não demonstrou o conjunto de dados necessário. Adicionar OAuth sozinho não resolve. |
| Portabilidade direta consentida | Usuário autoriza a transferência na plataforma | Possibilidade futura, fora da prioridade imediata; contrato e latência não validados para ByeFan. |
| Exportação oficial processada localmente | Usuário obtém e seleciona o arquivo | Alternativa de MVP que elimina a coleta por observadora. Exige mudança explícita do requisito sem ZIP; conferir formato, período e integridade. |
| Extensão ou coletor no dispositivo do proprietário | Instalação e sessão local do usuário | Distribui o trabalho; continua dependente da interface e das regras da plataforma. Não é solução automaticamente autorizada ou confiável. |
| Fornecedor de dados | Depende da fonte do fornecedor | Fora do escopo atual. Pagamento não comprova origem autorizada, precisão ou direito de revenda. |

Se “somente @, nenhum consentimento/login e nenhum arquivo” continuar
inegociável, não há hoje um caminho comercial validado pelo projeto. A opção
responsável é resolver a fonte ou revisar a promessa antes de vender.

## Referência futura: qualificar portabilidade

Antes de construir um conector de produção, obter documentação oficial ou
acesso autorizado ao portal e responder:

1. ByeFan pode ser destinatário? Quais requisitos de organização, revisão,
   segurança, uso dos dados, custos e regiões se aplicam? Não presumir
   disponibilidade no Brasil nem que aprovação em um registro basta.
2. Followers **e** following estão incluídos para o tipo de conta escolhido?
   Quais campos identificam as pessoas? IDs estáveis ou somente usernames?
3. Como se determina que todos os arquivos/lotes chegaram? Existe manifesto,
   paginação, data do snapshot, indicação de período ou sinal de término?
4. A transferência permite análise de reciprocidade para o proprietário?
   Qual mecanismo de consentimento, revogação, exclusão e retenção é exigido?
5. Qual a latência e a frequência disponibilizada? Pode haver espera na
   geração; portabilidade não implica resposta instantânea nem tempo real.

O experimento seguinte, se essa rota for elegível, deve usar somente a conta
própria autorizada, receber o mínimo de dados necessário e comparar com uma
referência independente. Sem credenciais do Instagram no ByeFan, sem contas
observadoras centrais e sem transformar contagem coincidente em integridade.
Cadastro, submissão de informações e eventual custo precisam de escopo
concreto e autorização do proprietário; esta revisão não os iniciou.

## Reaproveitamento e critérios de lançamento

Preservar site, comparação por ID, distinção de resultados parciais,
histórico, busca, testes e mecanismos de trabalhos assíncronos. Manter o
coletor híbrido como experimento separado, desativado por padrão.

A fonte futura deve entregar um snapshot com procedência, titular, momento,
identificadores e evidência de conclusão. Dados por username ficam em uma
comparação experimental distinta; não criar IDs fictícios para satisfazer o
comparador. Nenhuma referência deve ser gerada a partir da própria captura.

Para lançar, precisamos demonstrar: obtenção permitida dos dados necessários,
resultado conferido, experiência aceitável com o consentimento necessário,
isolamento/exclusão de dados e desempenho com a fonte real. A decisão pendente
do produto é aceitar essa participação do proprietário. Não implementamos
importação ZIP, OAuth ou extensão como substituição silenciosa do requisito.
