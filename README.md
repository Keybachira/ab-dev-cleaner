# limpador

Encontra e apaga `node_modules` e caches de projectos que já não estás a usar. Só usa a biblioteca padrão do Python, sem dependências.

## Como usar

```bash
# modo interactivo: mostra a lista e escolhes quais apagar
uv run limpador.py C:\Projects

# automático: apaga tudo o que está parado há mais de 2 dias
uv run limpador.py C:\Projects --auto

# ver o que seria apagado, sem apagar nada
uv run limpador.py C:\Projects --auto --simular
```

## Opções

| Opção | O que faz |
|---|---|
| `pasta` | Onde procurar (padrão: pasta actual) |
| `--dias N` | Dias sem uso para contar como parado (padrão: 2) |
| `--auto` | Apaga sem perguntar quais, só os parados |
| `--simular` | Só mostra, não apaga |
| `-y`, `--sim` | Não pede confirmação final |
| `--tudo` | Inclui também `dist`, `build`, `.venv`, `venv`, `__pycache__`, `.pytest_cache` |

## Modo interactivo

Depois da lista, escreve:

- `1,3,5-8` para escolher números e intervalos
- `v` para só os parados (marcados com `*`)
- `t` para todos
- Enter para sair

## O que apaga por padrão

`node_modules`, `.next`, `.nuxt`, `.turbo`, `.parcel-cache`, `.svelte-kit`, `.cache`

Tudo isto regenera-se com `npm install` / `build`. `dist`, `build` e `.venv` ficam de fora a menos que uses `--tudo`.

## Como decide que um projecto está parado

O último uso é o mais recente entre:

- o ficheiro mais recente do código-fonte (sem contar `node_modules`, caches, etc.)
- a actividade do git (`.git/index`, `.git/HEAD`, `.git/logs/HEAD`)

Se estás a trabalhar num projecto, o `git status`, commits ou editar ficheiros contam como uso.

## Segurança

- Não segue symlinks
- Pede confirmação antes de apagar (a não ser com `-y`)
- No Windows, trata ficheiros só-de-leitura e caminhos longos
- Se falhar a apagar uma pasta, normalmente é porque o editor ou um terminal está a usá-la

## Dica

Para correr todos os dias sem pensar: `uv run limpador.py C:\Projects --auto -y` no Agendador de Tarefas (Windows) ou num cron/systemd timer (Linux).