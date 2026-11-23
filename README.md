# limpador

Liberta espaço apagando o que se pode regenerar em projectos que já não estás a usar: `node_modules`, caches de frameworks, `__pycache__`, `target` (Rust/Maven), `.gradle`, venvs e mais. Só usa a biblioteca padrão do Python, sem dependências.

O limpador só marca uma pasta como **segura** quando consegue provar que se recria (há `package.json`, `Cargo.toml`, o git confirma que é gerada, etc.). O que for duvidoso aparece com `?` e nunca é apagado em `--auto`.

## Instalação (Windows)

Uma única vez, na pasta do projecto:

```bash
python main.py --instalar
```

Cria o `limpador.cmd` ao lado do script e junta essa pasta ao PATH do utilizador. Abre um terminal **novo** e já podes usar `limpador` de qualquer lado.

Se mudares a pasta do projecto de sítio, corre `--instalar` outra vez. Se não quiseres versionar o `limpador.cmd`, junta-o ao `.gitignore`.

## Como usar

```bash
# modo interactivo na pasta actual: mostra a lista e escolhes
limpador

# noutra pasta
limpador C:\Projects

# ver o que seria apagado, sem apagar nada
limpador C:\Projects --auto --simular

# automático: apaga o que é seguro e está parado há 2+ dias
limpador C:\Projects --auto

# incluir venvs, dist, build, bin/obj
limpador C:\Projects --tudo

# incluir caches globais (npm, pip, yarn, uv, gradle, maven)
limpador --sistema
```

Também funciona sem instalar: `python main.py C:\Projects` ou `uv run main.py C:\Projects`.

Recomendado: corre sempre primeiro com `--simular`.

## Opções

| Opção | O que faz |
|---|---|
| `pasta` | Onde procurar (padrão: pasta actual) |
| `--dias N` | Dias sem uso para contar como parado (padrão: 2) |
| `--auto` | Apaga sem perguntar quais, só as seguras e paradas |
| `--incertos` | Com `--auto`, inclui também as incertas (`?`) |
| `--simular` | Só mostra, não apaga |
| `-y`, `--sim` | Não pede confirmação final |
| `--tudo` | Inclui venvs, `dist`, `build`, `coverage`, `bin`/`obj` |
| `--sistema` | Inclui caches globais (só no modo interactivo) |
| `--min-mb N` | Esconde pastas menores que N MB (padrão: 1; `0` mostra tudo) |
| `--excluir PADRAO` | Não toca em caminhos que contenham o padrão (repetível) |
| `--instalar` | Cria o comando `limpador` e junta-o ao PATH |

Exemplo: `limpador C:\Projects --excluir "meu-app" --excluir "*\clientes\*"`

## Modo interactivo

Depois da lista, escreve:

- `1,3,5-8` para escolher números e intervalos
- `v` para as seguras e paradas (marcadas com `*`)
- `t` para todas
- `tipo:node` para todas de um tipo (`node`, `python`, `rust`...)
- Enter para sair

Na lista: `*` = segura e parada, `?` = incerta (a razão aparece ao lado), sem marca = projecto activo.

## O que procura

**Por padrão** (precisam do ficheiro do projecto ao lado):

| Tipo | Pastas | Exige |
|---|---|---|
| node | `node_modules` | `package.json` |
| node-cache | `.next`, `.nuxt`, `.turbo`, `.parcel-cache`, `.svelte-kit`, `.angular`, `.vite`, `.docusaurus`, `.cache` | `package.json` |
| python-cache | `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.hypothesis`, `.tox`, `.nox` | nada |
| rust/java | `target` | `Cargo.toml`, `pom.xml` ou `build.sbt` |
| gradle | `.gradle` | `build.gradle(.kts)` ou `settings.gradle(.kts)` |
| flutter | `.dart_tool` | `pubspec.yaml` |

**Com `--tudo`** (custam mais a recriar, por isso têm verificações extra):

| Tipo | Pastas | Verificação |
|---|---|---|
| python-venv | `.venv`, `venv`, `env` | tem `pyvenv.cfg`; sem `requirements`/`pyproject` fica `?` |
| dist / build / coverage | `dist`, `build`, `coverage` | o git tem de a ignorar; se estiver no git, nunca é tocada |
| .net | `bin`, `obj` | há `.csproj` e o git ignora |

**Com `--sistema`:** caches globais do npm, pip, yarn, uv, gradle e maven. Apagá-las obriga a descarregar tudo de novo da internet, por isso aparecem sempre como `?` e nunca são apagadas em `--auto`.

## Seguro ou incerto?

Uma pasta fica `?` quando falta alguma prova:

- `node_modules` sem lockfile: reinstalar pode mudar versões
- venv sem `requirements`/`pyproject`: não há como a recriar
- `dist`/`build`/`bin`/`obj` que o git não confirma que são geradas (ou não há git)

Pastas que estão **no git** (`dist` commitada, por exemplo) nunca são tocadas.

## Como decide que um projecto está parado

O último uso é o mais recente entre:

- o ficheiro mais recente do código-fonte (sem contar as pastas que o limpador apaga)
- a actividade do git (`.git/index`, `.git/HEAD`, `.git/logs/HEAD`)

Editar ficheiros, commits ou um `git status` (o VS Code faz isso em segundo plano) contam como uso. Projectos activos aparecem como `activo`.

## Segurança

- Não entra em `AppData`, `Program Files`, `Windows`, `.nvm`, `.cargo`, `miniconda3`, VS Code, etc.: os `node_modules` dessas pastas não são projectos teus
- Não segue symlinks nem junctions
- Pede confirmação antes de apagar (a não ser com `-y`) e avisa se a selecção inclui pastas incertas
- Avisa antes de analisar uma raiz de disco ou a pasta de utilizador inteira
- No Windows, trata ficheiros só-de-leitura e caminhos longos
- Regista tudo o que apaga em `%USERPROFILE%\.limpador.log`
- Se falhar a apagar uma pasta, normalmente é porque o editor ou um terminal está a usá-la
- Os tamanhos são aproximados: com `pnpm` (hardlinks) o espaço realmente libertado pode ser menor

No fim, mostra como recriar o que foi apagado (`npm install`, `cargo build`, etc.).

## Dica

Para correr sozinho todos os dias, no Agendador de Tarefas (Windows) usa o caminho completo do comando:

```
C:\Projects\Gadgets\Limpador\limpador.cmd C:\Projects --auto -y
```

Em `--auto` só entram as pastas seguras e paradas, nunca as incertas.
