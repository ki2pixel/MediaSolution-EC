#!/usr/bin/env python3
"""export_aep_from_cache.py — scan des dossiers projet du cache et export .ecproj → .aep.

Conçu pour le double-clic (raccourci .desktop) :
  * lancé sans terminal → barre de progression + résumé final en fenêtres (zenity) ;
  * lancé dans un terminal → sortie texte classique.

Ce qu'il fait, en boucle : scan des dossiers projet de ROOT (ceux dont le nom contient
l'un des filtres, insensible à la casse, et qui ont un sous-dossier `docs/` — même
détection que le panel), puis conversion de **tous** les `.ecproj` trouvés sous ces
dossiers vers `.aep` (convertisseur `ecproj_to_aep.py`, bibliothèque py_aep) **et vers
`.prproj`** (convertisseur `ecproj_to_prproj.py`, Premiere 2026, hors Adobe). Le `.prproj`
peut être désactivé avec `--no-prproj`.
Chaque exécution réécrit les `.aep` et `.prproj` : le résultat est toujours à jour.

Les seuls réglages à modifier sont les constantes ci-dessous.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

# ------------------------------------------------------------------ réglages

ROOT = "/mnt/cache"                       # racine scannée (dossiers projet = ses enfants directs)
NAME_FILTERS = ("camille",)               # sous-chaînes de nom de dossier — tuple : la virgule
                                          # est nécessaire même pour un seul filtre, ("camille",)
PATH_MAP = [("/mnt/cache", "F:")]         # réécriture des chemins de médias pour Windows ; [] = inchangés
VERIFY = True                             # relit chaque .aep produit avec py_aep (recommandé)
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aep_export.log")

TITLE = "Export .aep + .prproj (Media Solution)"

# ------------------------------------------------------------------ scan


def normalize_filters(filters) -> tuple[str, ...]:
    """Filtres de nom de dossier, tolérants à la syntaxe.

    `NAME_FILTERS = ("camille")` n'est **pas** un tuple (il manque la virgule) mais la
    chaîne "camille" : sans ce garde-fou, l'itération porterait sur les caractères
    c, a, m, i, l, e et tout dossier en contenant un seul serait retenu.
    """
    if isinstance(filters, str):
        filters = (filters,)
    needles = tuple(str(f).strip().lower() for f in filters if str(f).strip())
    if not needles:
        raise ValueError("Aucun filtre de dossier : NAME_FILTERS est vide.")
    short = [n for n in needles if len(n) < 2]
    if short:
        raise ValueError(
            f"Filtre de dossier trop court : {short!r}. Écris un tuple, par exemple "
            'NAME_FILTERS = ("camille",) — la virgule est nécessaire pour un seul filtre.'
        )
    return needles


def find_project_dirs(root: str, filters, require_docs: bool = True) -> list[str]:
    """Dossiers projet enfants directs de `root` — même détection que le panel :
    nom contenant un des filtres (insensible à la casse) et sous-dossier `docs/`."""
    needles = normalize_filters(filters)
    out: list[str] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
            continue
        if not any(n in entry.name.lower() for n in needles):
            continue
        if require_docs and not os.path.isdir(os.path.join(entry.path, "docs")):
            continue
        out.append(entry.path)
    return sorted(out)


def collect_ecproj(folders: list[str]) -> list[str]:
    """Tous les .ecproj sous ces dossiers (récursif)."""
    out: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.lower().endswith(".ecproj"):
                    path = os.path.join(dirpath, name)
                    if path not in seen:
                        seen.add(path)
                        out.append(path)
    return sorted(out)


# ------------------------------------------------------------------ interface


class Feedback:
    """Sortie texte (terminal) ou fenêtres zenity (double-clic)."""

    def __init__(self, use_gui: bool):
        self.use_gui = use_gui
        self.proc: subprocess.Popen[bytes] | None = None

    # -- progression
    def start_progress(self, total: int) -> None:
        if self.use_gui:
            try:
                self.proc = subprocess.Popen(
                    ["zenity", "--progress", "--title", TITLE, "--text", "Préparation…",
                     "--width", "460", "--auto-close", "--no-cancel"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                self.proc = None

    def progress(self, index: int, total: int, label: str) -> None:
        text = f"{index}/{total} — {label}"
        print("  " + text)
        if self.proc and self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(("#" + text + "\n").encode("utf-8"))
                self.proc.stdin.write((str(int(100 * index / max(total, 1))) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except Exception:
                pass

    def end_progress(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self.proc = None

    # -- messages
    def say(self, message: str, error: bool = False) -> None:
        stream = sys.stderr if error else sys.stdout
        print(message, file=stream)

    def finish(self, message: str, ok: bool) -> None:
        if self.use_gui:
            try:
                r = subprocess.run(
                    ["zenity", "--info" if ok else "--error", "--title", TITLE,
                     "--text", message, "--width", "520"],
                    check=False,
                )
                if r.returncode == 0:
                    return
            except Exception:
                pass
        self.say(message, error=not ok)


# ------------------------------------------------------------------ programme


def log_lines(lines: list[str]) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan des dossiers projet du cache et export des .ecproj en .aep.")
    parser.add_argument("--root", default=ROOT, help=f"racine à scanner (défaut : {ROOT})")
    parser.add_argument("--filter", action="append", default=None, metavar="TEXTE",
                        help="filtre de nom de dossier (répétable ; défaut : les constantes du script)")
    parser.add_argument("--path-map", action="append", default=None, metavar="SRC=DST",
                        help="réécriture des chemins de médias (répétable ; remplace la constante)")
    parser.add_argument("--no-verify", action="store_true", help="ne pas relire les .aep produits")
    parser.add_argument("--no-prproj", action="store_true",
                        help="ne pas produire les .prproj (seulement les .aep)")
    parser.add_argument("--dry-run", action="store_true", help="liste seulement ce qui serait converti")
    parser.add_argument("--no-gui", action="store_true", help="sortie texte même sans terminal")
    args = parser.parse_args()

    filters = tuple(args.filter) if args.filter else NAME_FILTERS
    if args.path_map is None:
        maps = list(PATH_MAP)
    else:
        maps = []
        for m in args.path_map:
            if "=" not in m:
                parser.error(f"--path-map attend SRC=DST, reçu : {m}")
            src, dst = m.split("=", 1)
            maps.append((src, dst))
    verify = VERIFY and not args.no_verify
    prproj_enabled = not args.no_prproj

    # Mode graphique : lancé hors terminal, avec zenity et un affichage disponibles.
    use_gui = (not args.no_gui) and (not sys.stdout.isatty()) \
        and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) \
        and shutil.which("zenity") is not None
    ui = Feedback(use_gui)

    started = time.time()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (f"[{stamp}] ROOT={args.root} filtres={filters} "
              f"mapping={maps or 'aucun'} dry_run={args.dry_run}")

    ui.say(f"{TITLE} — scan de {args.root}…")
    if not os.path.isdir(args.root):
        message = f"Racine introuvable : {args.root}"
        ui.finish(message, ok=False)
        log_lines([header, "  " + message])
        return 1

    try:
        dirs = find_project_dirs(args.root, filters)
    except ValueError as e:
        ui.finish(str(e), ok=False)
        log_lines([header, "  " + str(e)])
        return 1
    projects = collect_ecproj(dirs)
    ui.say(f"{len(dirs)} dossier(s) correspondant(s), {len(projects)} projet(s) .ecproj :")
    for d in dirs:
        ui.say(f"  • {d}")

    if not projects:
        message = f"Aucun projet .ecproj trouvé dans les dossiers correspondant à {filters}."
        ui.finish(message, ok=True)
        log_lines([header, "  " + message])
        return 0

    if args.dry_run:
        try:
            import ecproj_to_prproj as prproj_mod  # noqa: PLC0415
        except Exception:
            prproj_mod = None
        for p in projects:
            base = os.path.splitext(p)[0]
            skipped = prproj_mod.premiere_skip_reason(p) if (prproj_enabled and prproj_mod) else None
            if skipped:
                note = f"  (.prproj ignoré : {skipped})"
            else:
                note = f" + {base}.prproj" if prproj_enabled else ""
            ui.say(f"  → {p}  ⇒  {base}.aep{note}")
        message = f"Analyse seule : {len(projects)} projet(s) seraient convertis."
        ui.finish(message, ok=True)
        log_lines([header] + [f"  (dry-run) {p}" for p in projects] + ["  " + message])
        return 0

    # Charge les convertisseurs et py_aep
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import ecproj_to_aep as conv  # noqa: E402
        py_aep = conv.load_py_aep(None)
    except ImportError as e:
        message = f"Convertisseur indisponible :\n{e}"
        ui.finish(message, ok=False)
        log_lines([header, "  " + str(e)])
        return 2
    ui.say(f"py_aep {conv.py_aep_info(py_aep)}")
    log_lines([f"  py_aep {conv.py_aep_info(py_aep)}"])
    if prproj_enabled:
        try:
            import ecproj_to_prproj as prproj_conv  # noqa: E402
        except Exception as e:
            ui.say(f"Convertisseur .prproj indisponible ({e}) — export .aep seul.", error=True)
            log_lines([header, f"  convertisseur .prproj indisponible : {e}"])
            prproj_conv = None
            prproj_enabled = False

    report: list[str] = []
    failures: list[str] = []
    prproj_skipped = 0
    ui.start_progress(len(projects))
    for i, src in enumerate(projects, start=1):
        label = os.path.basename(src)
        ui.progress(i, len(projects), label)
        base = os.path.splitext(src)[0]
        details: list[str] = []
        project_ok = True
        try:
            result = conv.convert(src, base + ".aep", py_aep, maps, skip_missing=False)
            detail = f"{result['comps']} comp(s), {result['layers']} calque(s)"
            if result.get("keys"):
                detail += f", {result['keys']} keyframe(s)"
            if result.get("masks"):
                detail += f", {result['masks']} masque(s)"
            if result.get("effects"):
                detail += f", {result['effects']} effet(s)"
            if verify:
                conv.verify(base + ".aep", py_aep)
            details.append(f".aep : {detail}")
            for warning in (result.get("mask_warnings") or []) + (result.get("effect_warnings") or []):
                details.append(f"! {warning}")
        except Exception as e:
            project_ok = False
            failures.append(f"{src} (.aep) : {e}")
            details.append(f".aep : ÉCHEC ({e})")
        if prproj_enabled:
            reason = prproj_conv.premiere_skip_reason(src)
            if reason:
                prproj_skipped += 1
                removed = prproj_conv.remove_stale_prproj(src)
                details.append(f".prproj : ignoré ({reason}"
                               + (", obsolète supprimé" if removed else "")
                               + " — exporter depuis le .aep)")
                log_lines([f"  [prproj] {os.path.basename(src)} : ignoré ({reason})"
                           + (" ; .prproj obsolète supprimé" if removed else "")])
            else:
                try:
                    rep = prproj_conv.convert(
                        src, base + ".prproj", maps, False, False,
                        lambda message: log_lines([f"  [prproj] {message}"]))
                    details.append(f".prproj : {rep['items']} item(s), "
                                   f"{rep['audio_items']} audio")
                    for warning in rep["warnings"]:
                        details.append(f"! {warning}")
                    if rep["missing"]:
                        details.append(f"! {len(rep['missing'])} média(s) absent(s) — "
                                       f"référencé(s) quand même")
                except Exception as e:
                    project_ok = False
                    failures.append(f"{src} (.prproj) : {e}")
                    details.append(f".prproj : ÉCHEC ({e})")
        report.append(("OK    " if project_ok else "ÉCHEC ") + f"{src} → " + " | ".join(details))
    ui.end_progress()

    duration = time.time() - started
    ok_count = sum(1 for line in report if line.startswith("OK"))
    outputs = ".aep et .prproj" if prproj_enabled else ".aep"
    if prproj_enabled and prproj_skipped:
        outputs = (f".aep et .prproj ({prproj_skipped} .prproj ignoré(s) : "
                   f"éléments non repris)")
    summary = (f"{ok_count}/{len(projects)} projet(s) converti(s) en {outputs} "
               f"en {duration:.1f} s\nMédias réécrits : {' → '.join(f'{a} ⇒ {b}' for a, b in maps) if maps else 'aucun'}"
               f"\nJournal : {LOG_FILE}")
    if failures:
        summary += "\n\nErreurs :\n" + "\n".join(f"• {f}" for f in failures[:5])
        if len(failures) > 5:
            summary += f"\n… et {len(failures) - 5} autre(s) (voir le journal)"

    log_lines([header] + [f"  {line}" for line in report] + [f"  Résultat : {summary.splitlines()[0]}"])
    ui.finish(summary, ok=not failures)

    if sys.stdin.isatty():
        try:
            input("\nAppuyez sur Entrée pour fermer…")
        except EOFError:
            pass
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
