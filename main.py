#!/usr/bin/env python3
"""limpador — encontra e apaga node_modules e caches de projectos parados.

Dois modos:
  * interactivo (padrão): mostra a lista e tu escolhes quais apagar
  * --auto: apaga tudo o que está parado há mais de N dias (padrão: 2)

O "último uso" de um projecto é o ficheiro mais recente do código-fonte
(ignorando node_modules, caches, etc.) mais a actividade do git
(.git/index, .git/HEAD, .git/logs/HEAD).

Só usa a biblioteca padrão. Corre com:  uv run limpador.py [pasta]
"""
import argparse
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ALVOS_PADRAO = {
    "node_modules", ".next", ".nuxt", ".turbo",
    ".parcel-cache", ".svelte-kit", ".cache",
}
# só entram com --tudo (podem conter coisas que não se regeneram sozinhas)
ALVOS_EXTRA = {"dist", "build", ".venv", "venv", "__pycache__", ".pytest_cache"}

IGNORAR_NA_BUSCA = {".git", ".hg", ".svn", "AppData", "$Recycle.Bin"}
MARCADORES = ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml")


@dataclass
class Item:
    caminho: Path
    projecto: Path
    tamanho: int
    parado: float  # dias desde o último uso do projecto


def formatar_tamanho(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} TB"


def encontrar(raiz: Path, alvos: set) -> list:
    """Procura pastas-alvo sem entrar nelas e sem seguir symlinks."""
    achados = []
    pilha = [str(raiz)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as it:
                for e in it:
                    try:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if e.name in alvos:
                        achados.append(Path(e.path))
                    elif e.name in IGNORAR_NA_BUSCA:
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
                        if e.is_symlink():
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


def projecto_de(alvo: Path, raiz: Path) -> Path:
    """Sobe até achar a raiz do projecto (.git de preferência, senão package.json etc.)."""
    atual = alvo.parent
    candidato = None
    while True:
        if (atual / ".git").exists():
            return atual
        if candidato is None and any((atual / m).exists() for m in MARCADORES):
            candidato = atual
        if atual == raiz or atual.parent == atual:
            break
        atual = atual.parent
    return candidato or alvo.parent


def ultimo_uso(projecto: Path, ignorar: set) -> float:
    """Timestamp da actividade mais recente do projecto."""
    mais_recente = 0.0
    for sub in (".git/index", ".git/HEAD", ".git/logs/HEAD"):
        try:
            mais_recente = max(mais_recente, (projecto / sub).stat().st_mtime)
        except OSError:
            pass
    pilha = [str(projecto)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in ignorar and e.name not in IGNORAR_NA_BUSCA:
                                pilha.append(e.path)
                        else:
                            mais_recente = max(mais_recente, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    if mais_recente == 0.0:
        try:
            mais_recente = projecto.stat().st_mtime
        except OSError:
            mais_recente = time.time()
    return mais_recente


def _ao_falhar(func, caminho, _exc):
    """No Windows, ficheiros só-de-leitura impedem o rmtree; remove o atributo e tenta de novo."""
    try:
        os.chmod(caminho, stat.S_IWRITE)
        func(caminho)
    except Exception:
        pass


def apagar(caminho: Path) -> bool:
    alvo = str(caminho)
    if os.name == "nt":  # evita o limite de 260 caracteres do node_modules
        alvo = "\\\\?\\" + os.path.abspath(alvo)
    shutil.rmtree(alvo, onerror=_ao_falhar)
    return not caminho.exists()


def analisar(raiz: Path, alvos: set, ignorar: set) -> list:
    print(f"A procurar em {raiz} ...")
    pastas = encontrar(raiz, alvos)
    cache_projectos = {}
    agora = time.time()
    itens = []
    for i, p in enumerate(pastas, 1):
        print(f"\r  a analisar {i}/{len(pastas)}", end="", flush=True)
        proj = projecto_de(p, raiz)
        if proj not in cache_projectos:
            cache_projectos[proj] = ultimo_uso(proj, ignorar)
        parado = (agora - cache_projectos[proj]) / 86400
        itens.append(Item(p, proj, tamanho(p), parado))
    if pastas:
        print()
    itens.sort(key=lambda it: it.tamanho, reverse=True)
    return itens


def mostrar(itens: list, raiz: Path, dias: float) -> None:
    print()
    print(f"{'#':>3}  {'Tamanho':>9}  {'Parado':>8}   Projecto / pasta")
    print("-" * 70)
    for i, it in enumerate(itens, 1):
        marca = "*" if it.parado >= dias else " "
        try:
            rel = it.caminho.relative_to(raiz)
        except ValueError:
            rel = it.caminho
        print(f"{i:>3}{marca} {formatar_tamanho(it.tamanho):>9}  {it.parado:>6.1f} d   {rel}")
    velhos = [it for it in itens if it.parado >= dias]
    print("-" * 70)
    print(
        f"Total: {formatar_tamanho(sum(it.tamanho for it in itens))} em {len(itens)} pastas | "
        f"* parados há {dias:g}+ dias: {formatar_tamanho(sum(it.tamanho for it in velhos))} "
        f"em {len(velhos)} pastas"
    )


def parse_selecao(txt: str, itens: list, dias: float) -> list:
    txt = txt.strip().lower()
    if txt in ("", "q", "nada", "sair", "n"):
        return []
    if txt in ("todos", "t"):
        return list(range(len(itens)))
    if txt in ("velhos", "v"):
        return [i for i, it in enumerate(itens) if it.parado >= dias]
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
    print("  números e intervalos: 1,3,5-8  |  v = só os parados (*)  |  t = todos  |  Enter = sair")
    while True:
        try:
            idx = parse_selecao(input("> "), itens, dias)
            return [itens[i] for i in idx]
        except ValueError:
            print("Não percebi. Exemplo: 1,3,5-8")


def executar_apagar(escolhidos: list, sim: bool, simular: bool) -> None:
    if not escolhidos:
        print("Nada para apagar.")
        return
    total = sum(it.tamanho for it in escolhidos)
    print(f"\nVai apagar {len(escolhidos)} pastas ({formatar_tamanho(total)}).")
    if simular:
        print("[simulação] nada foi apagado.")
        return
    if not sim and input("Confirmas? [s/N] ").strip().lower() not in ("s", "sim", "y"):
        print("Cancelado.")
        return
    libertado = 0
    for it in escolhidos:
        ok = apagar(it.caminho)
        estado = "apagado" if ok else "FALHOU (em uso? fecha o editor/terminal)"
        print(f"  {estado}: {it.caminho}")
        if ok:
            libertado += it.tamanho
    print(f"\nEspaço libertado: {formatar_tamanho(libertado)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Apaga node_modules e caches de projectos parados.")
    ap.add_argument("pasta", nargs="?", default=".", help="pasta onde procurar (padrão: actual)")
    ap.add_argument("--dias", type=float, default=2, help="dias sem uso para contar como parado (padrão: 2)")
    ap.add_argument("--auto", action="store_true", help="apaga automaticamente os parados há mais de --dias")
    ap.add_argument("--simular", action="store_true", help="mostra o que apagaria, sem apagar")
    ap.add_argument("-y", "--sim", action="store_true", help="não pedir confirmação")
    ap.add_argument("--tudo", action="store_true", help="inclui também dist, build, .venv, __pycache__...")
    args = ap.parse_args()

    raiz = Path(args.pasta).expanduser().resolve()
    if not raiz.is_dir():
        print(f"Pasta não existe: {raiz}")
        return 1

    alvos = set(ALVOS_PADRAO) | (ALVOS_EXTRA if args.tudo else set())
    ignorar = ALVOS_PADRAO | ALVOS_EXTRA

    itens = analisar(raiz, alvos, ignorar)
    if not itens:
        print("Nenhuma pasta encontrada. Está limpo.")
        return 0

    mostrar(itens, raiz, args.dias)

    if args.auto:
        escolhidos = [it for it in itens if it.parado >= args.dias]
    else:
        escolhidos = escolher(itens, args.dias)

    executar_apagar(escolhidos, args.sim, args.simular)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido.")
        sys.exit(130)