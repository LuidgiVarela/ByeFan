# Autenticação local do Instagram — guia técnico

## Conectar sua conta

Use o terminal PowerShell local no diretório do ByeFan. A conta secundária deve
ser legítima, controlada por você e diferente de `perfil_autorizado`. O perfil alvo
precisa continuar público. Não forneça senha, cookies, sessão ou código 2FA no chat.

1. Instale as dependências isoladas. Se `.venv` ainda não existir, crie-a:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements-instagram.txt
   ```

2. **Este comando realiza login real e deve ser executado por você:**

   ```powershell
   .\.venv\Scripts\python -m scripts.instagram_authenticated login
   ```

   Informe o username da conta secundária, a senha na entrada oculta e o código
   2FA atual, se habilitado. Se não houver 2FA, pressione Enter no campo de código.
   Não há argumento CLI ou variável de ambiente para a senha. O script exige
   terminal interativo para evitar que `getpass` exponha a senha em um ambiente
   sem entrada oculta. Não execute pelo painel de saída não interativo da IDE.

3. Após autenticação e conferência da conta, a sessão fica em:

   ```text
   %LOCALAPPDATA%\ByeFan\instagram-session.dpapi
   ```

   O arquivo contém settings/cookies/tokens **criptografados com Windows DPAPI
   para o usuário atual**, sem senha ou código 2FA. Nunca é escrito JSON de sessão
   em claro. O caminho resolvido é recusado se estiver dentro do repositório.
   A sessão não deve ser compartilhada nem sincronizada. Processos executados
   como o mesmo usuário Windows podem descriptografá-la: DPAPI protege o arquivo,
   não um computador comprometido. A senha permanece transitoriamente em memória
   durante o login; Python não oferece garantia de apagamento físico dessa memória.

4. Execute a amostra inicial:

   ```powershell
   .\.venv\Scripts\python -m scripts.instagram_authenticated collect
   ```

   O script carrega a sessão, confere a identidade autenticada e solicita uma
   página de até 10 itens por lista. O tamanho da página é um pedido ao servidor,
   não garantia de quantos itens ele retornará. O alvo é fixo: `perfil_autorizado`.
   Não há consulta recursiva aos perfis encontrados.

5. Se a amostra terminar sem bloqueio ou desafio, amplie explicitamente:

   ```powershell
   .\.venv\Scripts\python -m scripts.instagram_authenticated collect --max-pages 20 --page-size 50
   ```

   Isso permite até 20 páginas por lista, solicitando 50 contas por página. Os
   limites aceitos são 1–100 para cada argumento. A execução para se detectar
   erro, cursor sem progresso, duplicata, truncamento ou recusa. Nenhum limite de
   acesso é contornado elevando `--max-pages`; esse argumento só controla nosso
   limite local de trabalho.

Não foi necessário cadastrar credenciais durante o desenvolvimento. Já instalamos
a dependência na `.venv` deste workspace; repetir o comando pip usa a mesma versão.

## Sessão expirada, desafios e 2FA

A coleta **nunca chama login automaticamente**. Sessão expirada retorna
`authentication_required`; você decide se/quando iniciar novamente o comando
interativo `login`. Um login bem-sucedido substitui a sessão criptografada existente.
Não há importação de cookies do navegador, troca de proxy, rotação de dispositivo,
tentativa alternativa de login ou resolução automática de CAPTCHA/checkpoint.

O instagrapi não é a API oficial da Meta. O suporte de login/2FA depende do fluxo
que Instagram oferecer à sua conta. O código aceita código 2FA padrão informado
por você, mas interrompe fluxos de verificação de perfil/desafio. Se aparecer
`challenge_required_stop`, `profile_verification_required_stop` ou
`alternate_login_flow_refused`, pare e confira a conta no aplicativo/site oficial.
Não há garantia de que depois disso esta biblioteca consiga autenticar; não
automatizamos essa etapa nem tentamos usar outro endpoint para vencer a recusa.

Para remover **somente a cópia local** da sessão:

```powershell
Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'ByeFan\instagram-session.dpapi')
```

Isso não revoga a sessão no Instagram. Para revogação remota, use o gerenciamento
de sessões no aplicativo oficial. Outros sistemas operacionais são recusados
nesta PoC até haver armazenamento criptografado equivalente implementado.

## Decisões de implementação e testes

`backend/experimental/instagram_authenticated.py` usa o transporte autenticado do
instagrapi, mas controla as páginas com `next_max_id`/`max_id`. Os métodos de lista
de alto nível podem fazer fallback; os métodos `*_chunk` também podem percorrer
várias páginas internamente e eliminar duplicatas. Usamos uma requisição explícita
por página para manter limites, erros e duplicatas observáveis.

A versão instalada foi inspecionada: seu wrapper `private_request` possui retries
e resolução de challenge. Nosso cliente conservador substitui esse wrapper por
uma única chamada ao transporte, desativa retries HTTP, desafios e fallback
público/legado. Também impede redirects, usa transporte requests sem proxy de
ambiente, limita timeout de conexão/leitura e suprime logs/corpos da dependência.
O perfil de cliente padrão da biblioteca não é rotacionado após uma recusa.
Mudança de versão exige nova inspeção; versões diferentes de 3.0.26 são recusadas.

Fontes consultadas: [instagrapi no PyPI](https://pypi.org/project/instagrapi/3.0.26/),
[implementação de usuários](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/user.py),
[transporte privado](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/private.py)
e [autenticação](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/auth.py).
A inspeção para a integração foi feita nos arquivos efetivamente instalados da
versão fixada; o branch remoto pode mudar.

```powershell
.\.venv\Scripts\python -m unittest discover -v
```

Oito testes novos verificam paginação, amostra de ambas as listas, recusa sem
continuação, duplicatas, truncamento, alteração de contagens, integração do
comparador após referência/repetição, criptografia DPAPI e contrato da biblioteca
sem retries/desafios. O teste da dependência bloqueia requisições de rede; DPAPI
é exercitado somente com conteúdo fictício. Nenhum teste faz login real.

**Coleta autenticada real ainda não validada.** Esta entrega prepara o experimento;
o próximo passo é você conectar a conta no terminal local e executar a amostra.
Não houve alteração no coletor anônimo, frontend ou checkout, e a integração
Apify não é importada por este fluxo.
