#!/usr/bin/env bash
# Synchronise le dépôt scripts/MediaSolution-EC vers la copie de travail
# ~/Media_Solution/MediaSolution-EC — celle qu'exécutent les raccourcis du Bureau et du menu,
# tandis que l'étape 8 du pipeline lit le panel du dépôt. Les journaux d'export (*.log) et les
# caches restent propres à chaque copie et ne sont jamais touchés.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="${MEDIASOLUTION_DEPLOY_DIR:-$HOME/Media_Solution/MediaSolution-EC}"

MODE="deploy"   # deploy | check | dry-run
PRUNE=0
PANEL=1

usage() {
  cat <<'TXT'
Usage : deploy.sh [options]

Synchronise le dépôt scripts/MediaSolution-EC vers la copie de travail
~/Media_Solution/MediaSolution-EC (exécutée par les raccourcis du Bureau).

Options :
  --check       ne rien copier : liste les écarts, sort en 1 s'il y en a
  --dry-run     liste ce qui serait copié, sans rien modifier
  --prune       supprime aussi dans la copie les fichiers absents du dépôt
  --no-panel    ne pas réinstaller le panel EffectCraft après une modification de MediaSolution.jsx
  --dest DIR    autre destination (défaut : $MEDIASOLUTION_DEPLOY_DIR ou ~/Media_Solution/MediaSolution-EC)
  -h, --help    cette aide

Exclusions (propres à chaque copie) : *.log, __pycache__/, *.pyc, .pytest_cache/
TXT
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE="check" ;;
    --dry-run) [ "$MODE" = "deploy" ] && MODE="dry-run" ;;
    --prune) PRUNE=1 ;;
    --no-panel) PANEL=0 ;;
    --dest)
      shift
      [ $# -ge 1 ] || { echo "option --dest : chemin manquant" >&2; exit 2; }
      DEST_DIR="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "option inconnue : $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

command -v rsync >/dev/null 2>&1 || { echo "rsync requis : sudo apt install rsync" >&2; exit 2; }

if [ "$SRC_DIR" = "$DEST_DIR" ]; then
  echo "ceci est la copie déployée : lancer scripts/MediaSolution-EC/deploy.sh du dépôt" >&2
  exit 2
fi

DEST_EXISTS=1
if [ ! -d "$DEST_DIR" ]; then
  DEST_EXISTS=0
  if [ "$MODE" = "check" ]; then
    echo "copie absente : $DEST_DIR" >&2
    exit 1
  fi
  if [ "$MODE" = "deploy" ]; then
    mkdir -p "$DEST_DIR"
  fi
fi

EXCLUDES=(--exclude='*.log' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/')

# --checksum : comparer les contenus, pas les dates (une modification de même taille dans la
# même seconde passerait sinon inaperçue). Passe de liste : --delete signale les fichiers en
# trop sans les retirer (mode --dry-run).
LIST_FLAGS=(-a --checksum --itemize-changes --dry-run "${EXCLUDES[@]}")
[ "$DEST_EXISTS" -eq 1 ] && LIST_FLAGS+=(--delete)
LIST="$(rsync "${LIST_FLAGS[@]}" "$SRC_DIR/" "$DEST_DIR/")"

COPIES="$(printf '%s\n' "$LIST" | grep -E '^>f' || true)"
EXTRAS="$(printf '%s\n' "$LIST" | grep -E 'deleting ' || true)"

if [ -z "$COPIES" ] && [ -z "$EXTRAS" ]; then
  echo "copie à jour : $DEST_DIR"
  exit 0
fi

if [ -n "$COPIES" ]; then
  echo "à copier vers $DEST_DIR :"
  printf '%s\n' "$COPIES" | sed 's/^/  /'
fi
if [ -n "$EXTRAS" ]; then
  echo "fichiers en trop dans la copie :"
  printf '%s\n' "$EXTRAS" | sed 's/^/  /'
  [ "$PRUNE" -eq 0 ] && echo "  (conservés : --prune pour les retirer)"
fi

case "$MODE" in
  check)
    echo "→ relancer sans --check pour synchroniser" >&2
    exit 1
    ;;
  dry-run)
    exit 0
    ;;
esac

COPY_FLAGS=(-a --checksum "${EXCLUDES[@]}")
[ "$PRUNE" -eq 1 ] && COPY_FLAGS+=(--delete)
rsync "${COPY_FLAGS[@]}" "$SRC_DIR/" "$DEST_DIR/"

if [ "$PANEL" -eq 1 ] && printf '%s\n' "$COPIES" | grep -q 'MediaSolution\.jsx$'; then
  echo
  echo "panel modifié → installation dans EffectCraft :"
  "$DEST_DIR/install.sh"
fi

echo
echo "synchronisé : $SRC_DIR → $DEST_DIR"
