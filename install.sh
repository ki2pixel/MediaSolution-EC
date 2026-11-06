#!/usr/bin/env bash
# Installe (ou met à jour) le panel Media Solution dans EffectCraft.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/MediaSolution.jsx"
DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/effectcraft/Scripts/ScriptUI Panels"

if [ ! -f "$SRC" ]; then
  echo "introuvable : $SRC" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST_DIR/MediaSolution.jsx"

echo "Installé : $DEST_DIR/MediaSolution.jsx"
echo
echo "Dans EffectCraft : Fenêtre ▸ MediaSolution"
echo "(si l'application était déjà lancée avant l'installation, la rouvrir ;"
echo " le panel est relu à chaque ouverture)"
echo
echo "Rappel : Préférences ▸ Scripting & Expressions ▸"
echo "  « Allow Scripts to Write Files and Access Network » doit être activée"
echo "  (le panel propose un bouton « Activer »)."
