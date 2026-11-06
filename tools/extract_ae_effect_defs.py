#!/usr/bin/env python3
"""extract_ae_effect_defs.py — extrait des définitions d'effets AE (`LIST:EfdG`) d'un .aep.

py_aep ne sait créer un effet que si sa définition figure déjà dans le projet
(`LIST:EfdG`) : le convertisseur `.aep` s'appuie donc sur un petit fichier de
définitions (`ae_effect_defs.aepdefs`, à côté de ce script) qui en contient les
gabarits. Ce script (re)construit ce fichier depuis n'importe quel .aep qui
utilise déjà les effets voulus (projet Adobe réel).

Usage :
  python3 extract_ae_effect_defs.py SRC.aep --list
  python3 extract_ae_effect_defs.py SRC.aep                       # effets par défaut
  python3 extract_ae_effect_defs.py SRC.aep --effects "ADBE Gaussian Blur 2" ...
  python3 extract_ae_effect_defs.py SRC.aep -o /autre/chemin.aepdefs

Sans `--effects`, la liste par défaut (EFFECTS) est extraite.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ecproj_to_aep import load_py_aep  # noqa: E402

# Effets que le convertisseur sait écrire (match names AE) — étendre ici.
EFFECTS = ["ADBE Gaussian Blur 2"]

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ae_effect_defs.aepdefs")


def extract(src_aep: str, wanted: list[str], out_path: str) -> int:
    py_aep = load_py_aep(None)
    from py_aep.binary.chunk import ListChunk, write_chunk
    from py_aep.binary.utils import filter_by_list_type, find_by_type

    app = py_aep.parse(src_aep)
    rifx_chunks = app.project._rifx.chunks
    efdg = None
    for chunk in rifx_chunks:
        if getattr(chunk, "list_type", None) == "EfdG":
            efdg = chunk
            break
    if efdg is None:
        print(f"{src_aep} : aucune définition d'effet (LIST:EfdG).", file=sys.stderr)
        return 2

    wanted_norm = {w.strip().lower() for w in wanted}
    entries = []
    for efdf in filter_by_list_type(chunks=efdg.chunks, list_type="EfDf"):
        tdmn = find_by_type(chunks=efdf.chunks, chunk_type="tdmn")
        match_name = getattr(tdmn, "value", "") or ""
        display = match_name
        try:
            sspc = next(c for c in efdf.chunks if getattr(c, "list_type", None) == "sspc")
            fnam = next(c for c in sspc.chunks if c.chunk_type == "fnam")
            utf8 = next(c for c in fnam.chunks if c.chunk_type == "Utf8")
            display = utf8.value
        except StopIteration:
            pass
        if match_name.lower() in wanted_norm or display.lower() in wanted_norm:
            entries.append(efdf)

    if not entries:
        print("Aucune définition trouvée pour : " + ", ".join(wanted), file=sys.stderr)
        return 1
    found = {find_by_type(chunks=e.chunks, chunk_type="tdmn").value for e in entries}

    root = ListChunk(chunk_type="RIFX", list_type="Egg!", chunks=[
        ListChunk(list_type="EfdG", chunks=entries)
    ])
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as handle:
        write_chunk(handle, root)
    os.replace(tmp, out_path)
    print(f"{out_path} : {len(entries)} définition(s) — " + ", ".join(sorted(found)))
    missing = wanted_norm - {m.lower() for m in found} - {m.replace("adbe ", "").lower() for m in found}
    if missing:
        print("Non trouvées : " + ", ".join(sorted(missing)), file=sys.stderr)
        return 1
    return 0


def list_effects(src_aep: str) -> int:
    py_aep = load_py_aep(None)
    app = py_aep.parse(src_aep)
    names = [m for m, _, _ in app.project._effect_definitions()]
    for name in sorted(names):
        print(name)
    print(f"— {len(names)} définition(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrait des définitions d'effets (LIST:EfdG) d'un .aep vers ae_effect_defs.aepdefs.")
    parser.add_argument("source", help="projet .aep contenant les effets")
    parser.add_argument("--effects", nargs="*", default=None, metavar="MATCH_NAME",
                        help="match names AE à extraire (défaut : liste EFFECTS)")
    parser.add_argument("--list", action="store_true", help="liste les définitions du .aep")
    parser.add_argument("-o", "--out", default=DEFAULT_OUT, help="fichier de sortie")
    args = parser.parse_args()

    if not os.path.isfile(args.source):
        parser.error(f"fichier introuvable : {args.source}")
    if args.list:
        return list_effects(args.source)
    return extract(args.source, args.effects or EFFECTS, args.out)


if __name__ == "__main__":
    sys.exit(main())
