# ByeFan — prévia do site

Site estático em `dist/`, sem build ou dependências de frontend. Demonstração
explicitamente fictícia: não consulta Instagram, não cobra e não contém
tokens, cookies, relatórios reais ou listas de pessoas.

O relatório abre em “Possíveis não seguidores”, com o número de candidatos
em destaque e a direção da relação em cada linha. A busca aceita @ e nome;
as outras categorias ficam em filtros separados. Todos os exemplos continuam
parciais e fictícios, sem perfis reais ou links para contas fictícias.

A prévia hospedada roda integralmente no navegador. O ambiente local também
pode testar a fila Python sintética, a partir da raiz do repositório ByeFan:

```powershell
.\.venv\Scripts\python -m scripts.site_preview
```

Abra `http://127.0.0.1:8766`. Ctrl+C encerra o servidor. Em uma retomada após
interrupção inesperada, use `--resume` para liberar a fila sintética;
trabalhos interrompidos não são repetidos. O banco fica em
`reports/site-preview/queue.sqlite3` e não é servido pela aplicação.

Para ver apenas a experiência estática, sem a fila, sirva `dist/` com um
servidor HTTP local. Apenas `exemplo.byefan` abre a demonstração; outros @s
são recusados pela interface sem enviá-los ao backend.

`tests/test_site_preview.py` testa a interface em Chromium com dados locais,
filtragem, busca, cancelamento, isolamento do backend e layout em celular.

Publicação Sites: `.openai/hosting.json` identifica a prévia privada. Somente
estes arquivos públicos são copiados para um checkout de publicação separado
em `reports/site-preview/publish-source`, evitando inicializar um Git aninhado
nesta pasta ou publicar o backend e dados locais. Use o helper de Sites com
credencial temporária por stdin; não salve credenciais em arquivos.

Esta prévia não é o lançamento comercial de consultas reais. A conexão com
o coletor autenticado exige validação de integridade e operação controlada.
