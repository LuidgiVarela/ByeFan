# ByeFan

ByeFan é uma ferramenta local para comparar listas de seguidores e contas seguidas, com foco em declarar incerteza quando os dados são incompletos. O projeto inclui coletores experimentais, validação de integridade, comparação de relações e uma interface de análise local.

## Estrutura

- `backend/collector`: modelos e coleta.
- `backend/experimental`: protótipos de navegador, integração e diagnóstico.
- `backend/services`: validação, cobertura e comparação de relações.
- `backend/pilot`: fila e interface local para análise.
- `scripts/`: comandos de execução e diagnóstico.
- `tests/`: testes automatizados com fixtures locais.
- `site/dist/`: prévia estática do site.

## Ambiente local

Use Python 3.13 ou compatível. Instale somente as dependências necessárias ao fluxo que pretende executar:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Os requisitos adicionais de navegador e integração autenticada estão em `requirements-browser.txt` e `requirements-instagram.txt`.

Para executar a suíte automatizada:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Documentação técnica

- [Arquitetura e critérios comerciais](docs/commercial-architecture.md)
- [Modelos de coleta de projetos relacionados](docs/competitor-collection-models.md)
- [Revisão de projetos estabelecidos](docs/established-projects-review.md)
- [Piloto local e concorrência sintética](docs/concurrency-pilot.md)
- [Coletor híbrido](docs/hybrid-collector.md)
- [Piloto local](docs/local-live-pilot.md)
- [Autenticação local](docs/authenticated-instagram.md)
- [Coleta estruturada](docs/structured-collection.md)
- [Metadados públicos do Apify](docs/apify-metadata.json)

Relatórios de execução, perfis consultados e dados coletados ficam fora deste repositório.
## Local profile configuration

Profile usernames are read from `.env.local` when present. Keep real account
names in that ignored local file; the published defaults are fictional.

```dotenv
BYEFAN_TARGET_USERNAME=example_target
BYEFAN_OBSERVER_USERNAME=example_observer
```
