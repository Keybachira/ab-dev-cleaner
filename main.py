#!/usr/bin/env python3
"""limpador — liberta espaço apagando o que se pode regenerar em projectos parados.

Procura node_modules, caches de frameworks, __pycache__, target (Rust/Maven),
.gradle, .venv, dist/build, bin/obj (.NET)... mas só marca como "seguro" o que
consegue provar que se recria (há package.json, Cargo.toml, o git confirma que
é gerado, etc.). O que for duvidoso aparece com "?" e nunca é apagado em --auto.

Uso:
  limpador                  analisa a pasta actual; tu escolhes o que apagar
  limpador C:\\Projects      analisa outra pasta
  limpador --simular        mostra o que apagaria, sem apagar
  limpador --auto           apaga o que é seguro e está parado há 2+ dias
  limpador --tudo           inclui também venvs, dist, build, bin/obj
  limpador --sistema        inclui caches globais (npm, pip, yarn, uv, gradle, maven)
  limpador --instalar       cria o comando "limpador" e junta-o ao PATH (Windows)

Só usa a biblioteca padrão.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG = Path.home() / ".limpador.log"
GIT = shutil.which("git")

# Pastas onde nunca se entra (nomes em minúsculas): ferramentas e dados de aplicações,
# cujos node_modules/venvs NÃO são projectos teus e não se devem apagar.
IGNORAR_NA_BUSCA = {
    ".git", ".hg", ".svn", "appdata", "$recycle.bin", "system volume information",
    ".nvm", ".cargo", ".rustup", ".pyenv", ".conda", "miniconda3", "anaconda3",
    "scoop", ".vscode", ".vscode-server", ".cursor", ".docker",
}
# só ignoradas quando estão mesmo na raiz do disco (C:\Windows, C:\Program Files...)
IGNORAR_NA_RAIZ_DO_DISCO = {"windows", "program files", "program files (x86)", "programdata"}

MARCADORES_PROJECTO = (
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "pubspec.yaml", "go.mod",
    "CMakeLists.txt", "*.csproj", "*.sln",
)


# ----------------------------------------------------------------- regras

@dataclass(frozen=True)
class Regra:
    eco: str                      # nome do tipo (mostrado na lista)
    nivel: int = 0                # 0 = padrão, 1 = só com --tudo
    marcadores: tuple = ()        # OBRIGATÓRIOS na pasta-mãe (prova o tipo de projecto)
    recomendados: tuple = ()      # se faltarem, fica "incerto" (?) em vez de seguro
    nota_falta: str = ""
    ancestrais: bool = False      # procurar marcadores também nas pastas acima
    dentro: str = ""              # ficheiro que tem de existir DENTRO da pasta
    git: bool = False             # exigir que o git a considere gerada (ignorada)
    refazer: str = ""             # como recriar


NODE = ("package.json",)
NODE_LOCKS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
              "npm-shrinkwrap.json")
PY_MANIFESTOS = ("requirements*.txt", "pyproject.toml", "Pipfile", "setup.py", "uv.lock",
                 "poetry.lock", "environment.yml")

REGRAS: dict[str, Regra] = {
    "node_modules": Regra(
        "node", marcadores=NODE, recomendados=NODE_LOCKS, ancestrais=True,
        nota_falta="sem lockfile: reinstalar pode mudar versões",
        refazer="npm install / pnpm install / yarn"),
    ".gradle": Regra(
        "gradle", marcadores=("build.gradle", "build.gradle.kts", "settings.gradle",
                              "settings.gradle.kts"), refazer="gradle build"),
    ".dart_tool": Regra("flutter", marcadores=("pubspec.yaml",), refazer="flutter pub get"),
    "target": Regra(
        "rust/java", marcadores=("Cargo.toml", "pom.xml", "build.sbt"),
        refazer="cargo build / mvn package"),
}
for _n in (".next", ".nuxt", ".turbo", ".parcel-cache", ".svelte-kit", ".angular", ".vite",
           ".docusaurus", ".cache"):
    REGRAS[_n] = Regra("node-cache", marcadores=NODE, refazer="regenera-se ao correr o projecto")
for _n in ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
           ".tox", ".nox"):
    REGRAS[_n] = Regra("python-cache", refazer="regenera-se sozinho")

# --- nível 1 (--tudo): podem conter coisas que custam mais a recriar ---
for _n in (".venv", "venv", "env"):
    REGRAS[_n] = Regra(
        "python-venv", nivel=1, dentro="pyvenv.cfg", recomendados=PY_MANIFESTOS,
        nota_falta="sem requirements/pyproject para recriar",
        refazer="python -m venv + pip install / uv sync")
REGRAS["dist"] = Regra(
    "dist", nivel=1, marcadores=("package.json", "pyproject.toml", "setup.py", "setup.cfg"),
    git=True, refazer="voltar a compilar (npm run build / python -m build)")
REGRAS["build"] = Regra(
    "build", nivel=1,
    marcadores=("package.json", "pyproject.toml", "setup.py", "CMakeLists.txt",
                "build.gradle", "build.gradle.kts", "pubspec.yaml"),
    git=True, refazer="voltar a compilar")
REGRAS["coverage"] = Regra(
    "coverage", nivel=1, marcadores=("package.json", "pyproject.toml"), git=True,
    refazer="voltar a correr os testes")
for _n in ("bin", "obj"):
    REGRAS[_n] = Regra(
        ".net", nivel=1, marcadores=("*.csproj", "*.fsproj", "*.vbproj"), git=True,
        refazer="dotnet build")


@dataclass
class Item:
    caminho: Path
    regra: Regra
    projecto: Path
    tamanho: int = 0
    parado: float = 0.0           # dias; -1 = não se aplica (caches globais)
    seguro: bool = True
    nota: str = ""


# ----------------------------------------------------------------- utilitários

def formatar_tamanho(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} TB"


def eh_ligacao(e: os.DirEntry) -> bool:
    """Symlink ou junction (pnpm usa muitos): nunca se segue."""
    try:
        if e.is_symlink():
            return True
        junction = getattr(e, "is_junction", None)  # Python 3.12+
        return bool(junction and junction())
    except OSError:
        return True


def tem(pasta: Path, padroes: tuple) -> bool:
    for p in padroes:
        try:
            if any(c in p for c in "*?["):
                if next(pasta.glob(p), None) is not None:
                    return True
            elif (pasta / p).exists():
                return True
        except OSError:
            pass
    return False


def procurar_marcadores(inicio: Path, padroes: tuple, raiz: Path, ancestrais: bool) -> bool:
    atual = inicio
    while True:
        if tem(atual, padroes):
            return True
        if not ancestrais or atual == raiz or atual.parent == atual:
            return False
        atual = atual.parent


def _git(pasta: Path, *args: str):
    try:
        return subprocess.run([GIT, "-C", str(pasta), *args], capture_output=True, text=True,
                              timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def estado_git(caminho: Path) -> str | None:
    """'ignorado' | 'rastreado' | 'novo' | None (sem git / fora de um repositório)."""
    if not GIT:
        return None
    r = _git(caminho.parent, "check-ignore", "-q", "--", caminho.name)
    if r is None or r.returncode not in (0, 1):
        return None
    if r.returncode == 0:
        return "ignorado"
    r = _git(caminho.parent, "ls-files", "--", caminho.name)
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return "rastreado"
    return "novo"


# ----------------------------------------------------------------- pesquisa

def encontrar(raiz: Path, regras: dict) -> list[Path]:
    """Procura pastas-alvo sem entrar nelas e sem seguir symlinks/junctions."""
    achados = []
    pilha = [str(raiz)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as it:
                for e in it:
                    try:
                        if not e.is_dir(follow_symlinks=False) or eh_ligacao(e):
                            continue
                    except OSError:
                        continue
                    nome = e.name.lower()
                    if nome in regras:
                        achados.append(Path(e.path))
                    elif nome in IGNORAR_NA_BUSCA:
                        continue
                    elif nome in IGNORAR_NA_RAIZ_DO_DISCO and \
                            Path(e.path).parent.parent == Path(e.path).parent:
                        continue
                    else:
                        pilha.append(e.path)
        except OSError:
            pass
    return achados


def tamanho(caminho: Path) -> int:
    total = 0
    pilha = [str(caminho)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as it:
                for e in it:
                    try:
                        if eh_ligacao(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pilha.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def medir(caminhos: list) -> list:
    res = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, t in enumerate(ex.map(tamanho, caminhos), 1):
            res.append(t)
            print(f"\r  a medir {i}/{len(caminhos)}", end="", flush=True)
    print()
    return res


def projecto_de(alvo: Path, raiz: Path) -> Path:
    """Sobe até à raiz do projecto (.git de preferência, senão o 1.º marcador)."""
    atual = alvo.parent
    candidato = None
    while True:
        if (atual / ".git").exists():
            return atual
        if candidato is None and tem(atual, MARCADORES_PROJECTO):
            candidato = atual
        if atual == raiz or atual.parent == atual:
            break
        atual = atual.parent
    return candidato or alvo.parent


def ultimo_uso(projecto: Path, ignorar: set, limite: float) -> float:
    """Timestamp da actividade mais recente. Pára logo que passa `limite` (projecto activo)."""
    mais = 0.0
    for sub in (".git/index", ".git/HEAD", ".git/logs/HEAD"):
        try:
            mais = max(mais, (projecto / sub).stat().st_mtime)
        except OSError:
            pass
    if mais > limite:
        return mais
    pilha = [str(projecto)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as it:
                for e in it:
                    try:
                        if eh_ligacao(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() not in ignorar:
                                pilha.append(e.path)
                        else:
                            m = e.stat(follow_symlinks=False).st_mtime
                            if m > mais:
                                mais = m
                                if mais > limite:
                                    return mais
                    except OSError:
                        pass
        except OSError:
            pass
    if mais == 0.0:
        try:
            mais = projecto.stat().st_mtime
        except OSError:
            mais = time.time()
    return mais


def avaliar(caminho: Path, regra: Regra, raiz: Path):
    """None = não tocar. Senão (seguro, nota)."""
    if regra.dentro and not (caminho / regra.dentro).exists():
        return None
    if regra.marcadores and not procurar_marcadores(
            caminho.parent, regra.marcadores, raiz, regra.ancestrais):
        return None
    notas = []
    if regra.recomendados and not procurar_marcadores(
            caminho.parent, regra.recomendados, raiz, regra.ancestrais):
        notas.append(regra.nota_falta)
    if regra.git:
        estado = estado_git(caminho)
        if estado == "rastreado":      # está no git: é código teu, nunca tocar
            return None
        if estado != "ignorado":
            notas.append("o git não confirma que é gerada")
    return (not notas, "; ".join(notas))


def excluido(caminho: Path, padroes: list) -> bool:
    s = str(caminho).lower()
    return any(fnmatch.fnmatch(s, p) for p in padroes)


def analisar(raiz: Path, regras: dict, dias: float, min_bytes: int, excluir: list):
    print(f"A procurar em {raiz} ...")
    achados = [p for p in encontrar(raiz, regras) if not excluido(p, excluir)]
    if not achados:
        return [], 0, 0
    tamanhos = medir(achados)

    itens, ocultos_n, ocultos_b = [], 0, 0
    for p, t in zip(achados, tamanhos):
        if t < min_bytes:
            ocultos_n += 1
            ocultos_b += t
            continue
        regra = regras[p.name.lower()]
        av = avaliar(p, regra, raiz)
        if av is None:
            continue
        proj = projecto_de(p, raiz)
        if excluido(proj, excluir):
            continue
        itens.append(Item(p, regra, proj, t, 0.0, av[0], av[1]))

    agora = time.time()
    limite = agora - dias * 86400
    ignorar = set(regras) | IGNORAR_NA_BUSCA
    projectos = sorted({it.projecto for it in itens})
    with ThreadPoolExecutor(max_workers=8) as ex:
        ultimos = dict(zip(projectos, ex.map(lambda pr: ultimo_uso(pr, ignorar, limite), projectos)))
    for it in itens:
        it.parado = (agora - ultimos[it.projecto]) / 86400
    itens.sort(key=lambda it: it.tamanho, reverse=True)
    return itens, ocultos_n, ocultos_b


def itens_sistema(min_bytes: int) -> list:
    """Caches globais de gestores de pacotes (só em modo interactivo)."""
    home = Path.home()
    local = os.environ.get("LOCALAPPDATA")
    candidatos = [("gradle", home / ".gradle" / "caches"), ("maven", home / ".m2" / "repository"),
                  ("npm", home / ".npm" / "_cacache"), ("pip", home / ".cache" / "pip"),
                  ("uv", home / ".cache" / "uv")]
    if local:
        L = Path(local)
        candidatos += [("npm", L / "npm-cache"), ("pip", L / "pip" / "Cache"),
                       ("yarn", L / "Yarn" / "Cache"), ("uv", L / "uv" / "cache")]
    out = []
    for rotulo, p in candidatos:
        if p.is_dir():
            t = tamanho(p)
            if t >= min_bytes:
                regra = Regra(f"cache-{rotulo}", refazer="volta a descarregar da internet")
                out.append(Item(p, regra, p, t, -1.0, False, "apagar obriga a descarregar de novo"))
    return out


# ----------------------------------------------------------------- apagar

def _ao_falhar(func, caminho, _exc):
    """No Windows, ficheiros só-de-leitura bloqueiam o rmtree: tira o atributo e repete."""
    try:
        os.chmod(caminho, stat.S_IWRITE)
        func(caminho)
    except Exception:
        pass


def apagar(caminho: Path) -> bool:
    alvo = str(caminho)
    if os.name == "nt":  # evita o limite de 260 caracteres
        alvo = os.path.abspath(alvo)
        alvo = ("\\\\?\\UNC\\" + alvo[2:]) if alvo.startswith("\\\\") else ("\\\\?\\" + alvo)
    for tentativa in range(2):
        if sys.version_info >= (3, 12):
            shutil.rmtree(alvo, onexc=_ao_falhar)
        else:
            shutil.rmtree(alvo, onerror=_ao_falhar)
        if not caminho.exists():
            return True
        time.sleep(0.5)
    return False


def registar(it: Item) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{it.tamanho}\t{it.caminho}\n")
    except OSError:
        pass


# ----------------------------------------------------------------- interface

def apagavel_auto(it: Item, dias: float, incertos: bool) -> bool:
    return it.parado >= dias and (it.seguro or incertos)


def mostrar(itens: list, raiz: Path, dias: float, ocultos: tuple) -> None:
    print()
    print(f"{'#':>3}  {'Tamanho':>9}  {'Parado':>8}  {'Tipo':<12} Pasta")
    print("-" * 78)
    for i, it in enumerate(itens, 1):
        if not it.seguro:
            marca = "?"
        elif it.parado >= dias:
            marca = "*"
        else:
            marca = " "
        if it.parado < 0:
            parado = "-"
        elif it.parado < dias:
            parado = "activo"
        else:
            parado = f"{it.parado:.1f} d"
        try:
            rel = it.caminho.relative_to(raiz)
        except ValueError:
            rel = it.caminho
        nota = f"   [{it.nota}]" if it.nota else ""
        print(f"{i:>3}{marca} {formatar_tamanho(it.tamanho):>9}  {parado:>8}  "
              f"{it.regra.eco:<12} {rel}{nota}")
    print("-" * 78)
    velhos = [it for it in itens if apagavel_auto(it, dias, False)]
    incertos = [it for it in itens if not it.seguro]
    print(f"Total: {formatar_tamanho(sum(it.tamanho for it in itens))} em {len(itens)} pastas")
    print(f"  *  seguras e paradas há {dias:g}+ dias: "
          f"{formatar_tamanho(sum(it.tamanho for it in velhos))} em {len(velhos)} pastas")
    if incertos:
        print(f"  ?  incertas (ver nota; só apagas se escolheres): "
              f"{formatar_tamanho(sum(it.tamanho for it in incertos))} em {len(incertos)} pastas")
    if ocultos[0]:
        print(f"  ({ocultos[0]} pastas pequenas ocultas, {formatar_tamanho(ocultos[1])}; "
              f"usa --min-mb 0 para ver tudo)")


def parse_selecao(txt: str, itens: list, dias: float) -> list:
    txt = txt.strip().lower()
    if txt in ("", "q", "n", "nada", "sair"):
        return []
    if txt in ("t", "todos"):
        return list(range(len(itens)))
    if txt in ("v", "velhos"):
        return [i for i, it in enumerate(itens) if apagavel_auto(it, dias, False)]
    if txt.startswith("tipo:"):
        tipo = txt[5:].strip()
        return [i for i, it in enumerate(itens) if tipo and tipo in it.regra.eco.lower()]
    sel = set()
    for parte in txt.replace(" ", "").split(","):
        if "-" in parte:
            a, b = parte.split("-", 1)
            sel.update(range(int(a) - 1, int(b)))
        else:
            sel.add(int(parte) - 1)
    return sorted(i for i in sel if 0 <= i < len(itens))


def escolher(itens: list, dias: float) -> list:
    print()
    print("Escolhe o que apagar:")
    print("  1,3,5-8  números/intervalos  |  v = as seguras e paradas (*)  |  t = todas")
    print("  tipo:node  todas de um tipo  |  Enter = sair")
    while True:
        try:
            idx = parse_selecao(input("> "), itens, dias)
            return [itens[i] for i in idx]
        except ValueError:
            print("Não percebi. Exemplo: 1,3,5-8")


def executar(escolhidos: list, sim: bool, simular: bool) -> None:
    if not escolhidos:
        print("Nada para apagar.")
        return
    total = sum(it.tamanho for it in escolhidos)
    print(f"\nVai apagar {len(escolhidos)} pastas ({formatar_tamanho(total)}).")
    duvidosas = sum(1 for it in escolhidos if not it.seguro)
    if duvidosas:
        print(f"  Atenção: {duvidosas} são incertas (?). Confirma que as sabes recriar.")
    if simular:
        print("[simulação] nada foi apagado.")
        return
    if not sim and input("Confirmas? [s/N] ").strip().lower() not in ("s", "sim", "y"):
        print("Cancelado.")
        return
    libertado, feitos = 0, []
    for it in escolhidos:
        ok = apagar(it.caminho)
        print(f"  {'apagado' if ok else 'FALHOU (em uso? fecha o editor/terminal)'}: {it.caminho}")
        if ok:
            libertado += it.tamanho
            feitos.append(it)
            registar(it)
    print(f"\nEspaço libertado: {formatar_tamanho(libertado)}  (registo em {LOG})")
    dicas = sorted({(it.regra.eco, it.regra.refazer) for it in feitos if it.regra.refazer})
    if dicas:
        print("Para recriar quando precisares:")
        for eco, como in dicas:
            print(f"  {eco}: {como}")


# ----------------------------------------------------------------- instalação (Windows)

def instalar() -> int:
    script = Path(__file__).resolve()
    if os.name != "nt":
        print(f"Cria um alias:  alias limpador='python3 {script}'")
        return 0
    cmd = script.parent / "limpador.cmd"
    conteudo = (
        "@echo off\r\n"
        "where py >nul 2>nul\r\n"
        "if errorlevel 1 goto sempy\r\n"
        f'py -3 "%~dp0{script.name}" %*\r\n'
        "exit /b %errorlevel%\r\n"
        ":sempy\r\n"
        f'python "%~dp0{script.name}" %*\r\n'
        "exit /b %errorlevel%\r\n"
    )
    cmd.write_bytes(conteudo.encode("utf-8"))
    print(f"Criado: {cmd}")

    import ctypes
    import winreg
    pasta = str(script.parent)
    alvo = os.path.normcase(pasta).rstrip("\\")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        try:
            atual, tipo = winreg.QueryValueEx(k, "Path")
        except FileNotFoundError:
            atual, tipo = "", winreg.REG_EXPAND_SZ
        partes = [p for p in atual.split(";") if p]
        if any(os.path.normcase(os.path.expandvars(p)).rstrip("\\") == alvo for p in partes):
            print("A pasta já estava no PATH.")
        else:
            winreg.SetValueEx(k, "Path", 0, tipo, ";".join(partes + [pasta]))
            print(f"Adicionada ao PATH do utilizador: {pasta}")
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, ctypes.byref(ctypes.c_ulong()))
    print('Abre um terminal NOVO e escreve:  limpador --simular')
    return 0


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="limpador",
        description="Apaga o que se pode regenerar (node_modules, caches, venvs...) em projectos parados.")
    ap.add_argument("pasta", nargs="?", default=None, help="onde procurar (padrão: pasta actual)")
    ap.add_argument("--dias", type=float, default=2, help="dias sem uso para contar como parado (padrão: 2)")
    ap.add_argument("--auto", action="store_true", help="apaga sem perguntar o que é seguro e parado")
    ap.add_argument("--incertos", action="store_true", help="com --auto, inclui também as incertas (?)")
    ap.add_argument("--simular", action="store_true", help="mostra o que apagaria, sem apagar")
    ap.add_argument("-y", "--sim", action="store_true", help="não pedir confirmação")
    ap.add_argument("--tudo", action="store_true", help="inclui venvs, dist, build, bin/obj (com verificações)")
    ap.add_argument("--sistema", action="store_true", help="inclui caches globais (só modo interactivo)")
    ap.add_argument("--min-mb", type=float, default=1, help="ignora pastas menores que isto (padrão: 1)")
    ap.add_argument("--excluir", action="append", default=[], metavar="PADRAO",
                    help='não tocar em caminhos que contenham isto (ex.: --excluir "meu-app"); repetível')
    ap.add_argument("--instalar", action="store_true", help='cria o comando "limpador" no PATH (Windows)')
    args = ap.parse_args()

    if args.instalar:
        return instalar()

    pasta = Path(args.pasta.strip('"')) if args.pasta else Path.cwd()
    raiz = pasta.expanduser().resolve()
    if not raiz.is_dir():
        print(f"Pasta não existe: {raiz}")
        return 1
    if raiz.parent == raiz or raiz == Path.home():
        print(f"Atenção: vais procurar em {raiz} inteira; pode demorar.")
        if not args.sim and input("Continuar? [s/N] ").strip().lower() not in ("s", "sim", "y"):
            return 0

    regras = {n: r for n, r in REGRAS.items() if args.tudo or r.nivel == 0}
    excluir = [(p if any(c in p for c in "*?[") else f"*{p}*").lower() for p in args.excluir]
    min_bytes = int(args.min_mb * 1024 * 1024)

    itens, on, ob = analisar(raiz, regras, args.dias, min_bytes, excluir)
    if args.sistema and not args.auto:
        itens += itens_sistema(min_bytes)
        itens.sort(key=lambda it: it.tamanho, reverse=True)
    if not itens:
        print("Nada que valha a pena limpar. Está limpo.")
        return 0

    mostrar(itens, raiz, args.dias, (on, ob))

    if args.auto:
        escolhidos = [it for it in itens if apagavel_auto(it, args.dias, args.incertos)]
    else:
        escolhidos = escolher(itens, args.dias)

    executar(escolhidos, args.sim, args.simular)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nInterrompido.")
        sys.exit(130)
