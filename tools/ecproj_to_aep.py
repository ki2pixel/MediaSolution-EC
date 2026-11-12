#!/usr/bin/env python3
"""ecproj_to_aep.py — convertit un projet EffectCraft (.ecproj) en After Effects (.aep).

Moteur principal : l'export natif d'EffectCraft (`file.exportAep`, EffectCraft ≥ 0.24.1),
qui écrit l'`.aep` depuis le projet EffectCraft lui-même. Les chemins de métrage sont
écrits **tels quels** : un `.ecproj` qui référence `/mnt/cache/...` produit un `.aep`
qui référence `/mnt/cache/...`, sans relink ni mapping implicite.

Repli : l'ancien convertisseur py_aep (`ecproj_to_aep_legacy.py`) est utilisé
automatiquement pour les cas que l'export natif ne sait pas encore écrire, notamment
les clips et séquences à cadence fractionnaire (29,97 / 23,976 / 59,94). Le moteur
utilisé est indiqué dans la sortie.

Politique de pertes : l'export natif signale tout ce qu'il n'écrit pas. Les pertes
bénignes connues (interprétation alpha `Straight`, profil couleur standard, guides,
noms tronqués, temps arrondis) sont autorisées et journalisées. Toute perte qui change
le rendu bloque, sauf `--allow-losses`.

Usage :
  python3 ecproj_to_aep.py <projet.ecproj> [-o sortie.aep] [--path-map SRC=DST] [--verify]
  python3 ecproj_to_aep.py <dossier> --all [--path-map …] [--verify]

Options principales :
  -o, --out FICHIER   chemin du .aep (défaut : à côté du .ecproj, même nom)
  --all               convertit tous les *.ecproj du dossier
  --path-map SRC=DST  réécrit le préfixe des chemins stockés (répétable) ; par défaut
                      aucun mapping, donc /mnt/cache est conservé.
  --verify            relit le .aep produit (import EffectCraft en dry-run pour le
                      moteur natif, py_aep pour le legacy)
  --legacy            force l'ancien convertisseur py_aep
  --no-fallback       export natif uniquement, échec si le natif ne sait pas écrire
  --allow-losses      écrit malgré des pertes bloquantes (déconseillé)
  --cli PATH          chemin de effectcraft-cli (défaut : PATH, EFFECTCRAFT_CLI)
  --skip-missing      legacy uniquement : ignore les médias absents
  --effect-defs PATH  legacy uniquement : définitions d'effets py_aep
  --mask-space MODE   legacy uniquement : espace des masques (source/comp)
  --py-aep PATH       legacy uniquement : dossier src de py_aep (implique --legacy)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from typing import Any

# Réexportés pour `ecproj_to_prproj.py`, qui importe ces helpers depuis ce module
# (l'implémentation vit dans ecproj_common.py pour être partagée).
from ecproj_common import (
    TICKS_PER_SECOND,
    apply_path_map,
    collect_effects,
    enabled_masks,
    find_group,
    find_layer_property,
    find_transform_node,
    find_transform_nodes,
    frame_rate,
    project_effects,
    prop_value,
    ticks_to_seconds,
    vec3,
    world_offset,
)

MIN_CLI_VERSION = (0, 24, 1)
DEFAULT_CLI_CANDIDATES = (
    "/usr/bin/effectcraft-cli",
    "/usr/local/bin/effectcraft-cli",
    os.path.expanduser("~/.local/bin/effectcraft-cli"),
)

# Catégories dont une perte ne change pas le rendu (elle est journalisée).
BENIGN_CATEGORIES = {"names", "render_queue", "rounded_times"}


class ConverterError(RuntimeError):
    """Erreur de conversion ou d'outillage."""


# ---------------------------------------------------------------------------
# Outillage EffectCraft CLI.


_CLI_CACHE: dict[str, tuple[str, tuple[int, int, int] | None]] = {}


def find_cli(explicit: str | None = None) -> str:
    candidates: list[str | None] = [
        explicit,
        os.environ.get("EFFECTCRAFT_CLI"),
        shutil.which("effectcraft-cli"),
        *DEFAULT_CLI_CANDIDATES,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = candidate
        if os.path.sep not in path:
            found = shutil.which(path)
            if found:
                path = found
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path)
    raise ConverterError(
        "effectcraft-cli introuvable. Installez EffectCraft ≥ 0.24.1 ou indiquez "
        "son chemin avec --cli / EFFECTCRAFT_CLI."
    )


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def ensure_cli(explicit: str | None = None) -> tuple[str, tuple[int, int, int] | None]:
    """Retourne (chemin, version) et vérifie la présence de file.exportAep."""
    path = find_cli(explicit)
    if path in _CLI_CACHE:
        return _CLI_CACHE[path]
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConverterError(f"effectcraft-cli inutilisable ({path}) : {exc}") from exc
    version = _parse_version((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if version is not None and version < MIN_CLI_VERSION:
        raise ConverterError(
            "EffectCraft 0.24.1 ou plus récent est requis pour l'export natif .aep "
            f"(effectcraft-cli détecté : {'.'.join(map(str, version))}, {path})."
        )
    result = (path, version)
    _CLI_CACHE[path] = result
    return result


def cli_info(explicit: str | None = None) -> str:
    path, version = ensure_cli(explicit)
    label = ".".join(map(str, version)) if version else "version inconnue"
    return f"{label} ({path})"


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ConverterError(f"commande introuvable : {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConverterError(f"délai dépassé : {cmd[0]} {cmd[1:3]}") from exc


def _json_stdout(stdout: str, what: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise ConverterError(f"{what} : sortie vide")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ConverterError(f"{what} : réponse JSON illisible ({text[:300]})")


def _export_aep(
    cli: str,
    ecproj_path: str,
    out_path: str,
    *,
    dry_run: bool = False,
    allow_losses: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {"path": os.path.abspath(out_path)}
    if dry_run:
        params["dryRun"] = True
    if allow_losses:
        params["allowLosses"] = True
    cmd = [
        cli, "exec", "file.exportAep",
        json.dumps(params, ensure_ascii=False),
        os.path.abspath(ecproj_path),
        "--json",
    ]
    proc = _run(cmd)
    data = _json_stdout(proc.stdout, "file.exportAep")
    if proc.returncode != 0 or data.get("error"):
        raise ConverterError(str(data.get("error") or proc.stderr.strip() or
                                   f"file.exportAep a échoué (code {proc.returncode})"))
    return data


# ---------------------------------------------------------------------------
# Lecture du .ecproj.


def _iter_items(data: dict[str, Any]):
    items = data.get("items") or {}
    if isinstance(items, dict):
        return list(items.items())
    if isinstance(items, list):
        return list(enumerate(items))
    return []


def load_ecproj(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConverterError(f"lecture impossible de {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConverterError(f"schéma .ecproj inattendu : {path}")
    return data


def _footage_by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for _, item in _iter_items(data):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or {}
        if kind.get("type") == "Footage":
            out[str(item.get("name") or "")] = kind
    return out


def _dirname_any(path: str) -> str:
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[:cut] if cut > 0 else ""


def expected_alias_paths(data: dict[str, Any], maps: list[tuple[str, str]]) -> list[str]:
    """Chemins que l'export natif doit écrire dans les alias `Als2/alas`."""
    out: list[str] = []
    for _, item in _iter_items(data):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or {}
        if kind.get("type") != "Footage":
            continue
        stored = str(kind.get("path") or "")
        if not stored:
            continue
        mapped = apply_path_map(stored, maps)
        if str(kind.get("kind") or "") == "Sequence":
            out.append(_dirname_any(mapped))
        else:
            out.append(mapped)
    return out


def _extract_alias_paths(aep_bytes: bytes) -> list[str]:
    """Lit les `fullpath` des seuls alias de métrage (pas le XMP)."""
    text = aep_bytes.decode("utf-8", "replace")
    decoder = json.JSONDecoder()
    paths: list[str] = []
    for match in re.finditer(r'\{"ascendcount_base":', text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "fullpath" in obj:
            paths.append(str(obj["fullpath"]))
    return paths


# ---------------------------------------------------------------------------
# Classification des pertes de l'export natif.


def _footage_name(detail: str) -> tuple[str, str]:
    """« footage 'nom': raison » → (nom, raison)."""
    if not detail.startswith("footage '"):
        return "", detail
    rest = detail[len("footage '"):]
    name, sep, why = rest.rpartition("': ")
    if not sep:
        return "", detail
    return name, why


def _truthy(value: Any) -> bool:
    return bool(value) if value is not None else False


def classify_losses(
    report: dict[str, Any], data: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Retourne (bloquantes, métrage, dérivées, avertissements).

    - bloquantes : pertes qui changent le rendu et ne viennent pas du métrage ;
    - métrage : pertes qui empêchent l'export d'un métrage (repli legacy possible) ;
    - dérivées : `unsupported_layers` causées par un métrage non exporté ;
    - avertissements : pertes bénignes.
    """
    footage = _footage_by_name(data)
    blocking: list[str] = []
    footage_blocking: list[str] = []
    derived: list[str] = []
    warnings: list[str] = []

    for loss in report.get("losses") or []:
        if not isinstance(loss, dict):
            continue
        category = str(loss.get("category") or "")
        details = [str(detail) for detail in loss.get("details") or []]
        count = int(loss.get("count") or len(details))
        for detail in details:
            text = f"{category}: {detail}"
            if category == "footage_items":
                name, why = _footage_name(detail)
                kind = footage.get(name, {})
                if "is not exported" in why or "no prototype" in why \
                        or "only whole rates" in why or "fps is not exported" in why \
                        or "sequence" in why.lower() or "layered file" in why \
                        or "no frame number" in why:
                    footage_blocking.append(text)
                elif "Interpret Footage colour profile" in why:
                    profile = str(kind.get("color_profile") or "").replace(" ", "").replace(".", "").lower()
                    if profile in ("", "rec709", "srgb", "none"):
                        warnings.append(text)
                    else:
                        blocking.append(text)
                elif "Interpret Footage alpha interpretation" in why:
                    alpha = str(kind.get("alpha") or "Ignore").strip().lower()
                    if alpha in ("premultiplied", "premult", "premultipliedalpha"):
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "Interpret Footage pixel aspect" in why:
                    try:
                        aspect = abs(float(kind.get("pixel_aspect") or 1.0) - 1.0)
                    except (TypeError, ValueError):
                        aspect = 1.0
                    if aspect > 1e-9:
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "Interpret Footage loop count" in why:
                    try:
                        loop = int(kind.get("loop_count") or 1)
                    except (TypeError, ValueError):
                        loop = 1
                    if loop != 1:
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "field order" in why or "Separate Fields" in why:
                    fields = kind.get("fields")
                    if fields not in (None, 0, "Off", "off", "OFF", "None"):
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "Linear Light" in why:
                    if _truthy(kind.get("linear_light")):
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "Preserve RGB" in why:
                    if _truthy(kind.get("preserve_rgb")):
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "Cineon" in why:
                    blocking.append(text)
                elif "Invert Alpha" in why:
                    if _truthy(kind.get("invert_alpha")):
                        blocking.append(text)
                    else:
                        warnings.append(text)
                elif "proxy" in why.lower():
                    warnings.append(text)
                else:
                    # Perte de métrage non classée : prudence.
                    blocking.append(text)
            elif category == "unsupported_layers":
                if "footage layer whose footage is not exported" in detail:
                    derived.append(text)
                else:
                    blocking.append(text)
            elif category == "composition_options":
                if "guides" in detail.lower():
                    warnings.append(text)
                else:
                    blocking.append(text)
            elif category in BENIGN_CATEGORIES:
                warnings.append(text)
            elif category == "project_settings":
                # L'export natif écrit les réglages qu'il connaît ; les autres
                # changeraient le rendu (espace de travail, OCIO, HDR…).
                blocking.append(text)
            else:
                blocking.append(text)

        if count > len(details):
            missing = count - len(details)
            if category == "footage_items":
                # Le rapport plafonne les détails à 20. Les métrages réellement
                # utilisés qui n'ont pas pu être exportés réapparaissent dans
                # `unsupported_layers` (bloquant) : pas de blocage sur le seul
                # compteur, sinon 21 pertes bénignes d'interprétation bloqueraient.
                warnings.append(
                    f"{category}: {missing} perte(s) de métrage supplémentaire(s) "
                    "non détaillée(s) (rapport limité à 20)"
                )
            elif category in BENIGN_CATEGORIES:
                warnings.append(f"{category}: {missing} perte(s) supplémentaire(s)")
            else:
                blocking.append(
                    f"{category}: {missing} perte(s) supplémentaire(s) non détaillée(s)"
                )

    if derived and not footage_blocking:
        blocking.extend(derived)
        derived = []
    return blocking, footage_blocking, derived, warnings


def _summarize(items: list[str], limit: int = 4) -> str:
    shown = items[:limit]
    suffix = f" (+{len(items) - limit})" if len(items) > limit else ""
    return "; ".join(shown) + suffix


# ---------------------------------------------------------------------------
# Copies temporaires et repli legacy.


def _write_temp_project(data: dict[str, Any], source_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(source_path))
    if not os.path.isdir(directory):
        directory = None
    handle = None
    try:
        import tempfile
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".ecproj", prefix=".aep_export_",
            dir=directory, delete=False,
        )
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.flush()
        return handle.name
    finally:
        if handle is not None:
            handle.close()


def _mapped_project(
    source_path: str, maps: list[tuple[str, str]],
) -> tuple[str, str | None]:
    """Copie temporaire avec les chemins réécrits, ou (source, None) si inutile."""
    data = load_ecproj(source_path)
    changed = False
    for _, item in _iter_items(data):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or {}
        if kind.get("type") != "Footage":
            continue
        path = kind.get("path")
        if isinstance(path, str):
            mapped = apply_path_map(path, maps)
            if mapped != path:
                kind["path"] = mapped
                changed = True
        sequence = kind.get("sequence")
        if isinstance(sequence, list):
            mapped_sequence = [
                apply_path_map(entry, maps) if isinstance(entry, str) else entry
                for entry in sequence
            ]
            if mapped_sequence != sequence:
                kind["sequence"] = mapped_sequence
                changed = True
    if not changed:
        return source_path, None
    temp = _write_temp_project(data, source_path)
    return temp, temp


def _build_basename_index(root: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            index.setdefault(name, os.path.join(dirpath, name))
    return index


def _legacy_relink_project(
    source_path: str,
    path_maps: list[tuple[str, str]],
    skip_missing: bool,
) -> tuple[str, list[tuple[str, str]], str | None]:
    """Prépare un .ecproj temporaire pour py_aep : médias réels → chemins stockés.

    L'ancien convertisseur lit les médias pour les probe. Si un `.ecproj` référence
    `/mnt/cache/131 Camille/...` alors que le dossier Linux est `131 Cvmille`, on
    retrouve le fichier par son nom sous le dossier projet, on importe le chemin réel,
    puis `path_maps` réécrit le chemin final (stocké, éventuellement remappé par
    l'utilisateur) dans le `.aep`.
    """
    data = load_ecproj(source_path)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(source_path)))
    index = _build_basename_index(project_root) if os.path.isdir(project_root) else {}
    relink_maps: list[tuple[str, str]] = []
    missing: list[str] = []
    changed = False

    for _, item in _iter_items(data):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or {}
        if kind.get("type") != "Footage":
            continue
        stored = str(kind.get("path") or "")
        if not stored:
            continue
        real = stored if os.path.exists(stored) else index.get(os.path.basename(stored))
        if not real:
            if not skip_missing:
                missing.append(stored)
            continue
        final = apply_path_map(stored, path_maps)
        if real != stored or final != stored:
            relink_maps.append((real, final))
            changed = True
        kind["path"] = real
        sequence = kind.get("sequence")
        if isinstance(sequence, list):
            real_sequence: list[Any] = []
            for entry in sequence:
                if not isinstance(entry, str):
                    real_sequence.append(entry)
                    continue
                real_entry = entry if os.path.exists(entry) else index.get(os.path.basename(entry))
                if not real_entry:
                    real_entry = os.path.join(os.path.dirname(real), os.path.basename(entry))
                if os.path.exists(real_entry):
                    real_sequence.append(real_entry)
                    final_entry = apply_path_map(entry, path_maps)
                    if real_entry != entry or final_entry != entry:
                        relink_maps.append((real_entry, final_entry))
                        changed = True
                else:
                    real_sequence.append(entry)
                    if not skip_missing:
                        missing.append(entry)
            kind["sequence"] = real_sequence

    if missing:
        unique = list(dict.fromkeys(missing))
        raise ConverterError(
            "repli legacy impossible : média(s) introuvable(s) pour le probe py_aep : "
            + _summarize(unique, limit=3)
            + " ; copiez les médias ou utilisez l'export natif si la cadence le permet."
        )
    if not changed:
        return source_path, path_maps, None
    temp = _write_temp_project(data, source_path)
    return temp, relink_maps, temp


def _legacy_convert(
    ecproj_path: str,
    out_path: str,
    path_maps: list[tuple[str, str]],
    *,
    skip_missing: bool = False,
    effect_defs: str | None = None,
    mask_space: str = "source",
    py_aep_path: str | None = None,
) -> dict[str, Any]:
    from ecproj_to_aep_legacy import convert as legacy_convert
    from ecproj_to_aep_legacy import load_py_aep, py_aep_info

    temp, relink_maps, cleanup = _legacy_relink_project(ecproj_path, path_maps, skip_missing)
    try:
        py_aep = load_py_aep(py_aep_path)
        report = legacy_convert(
            temp, out_path, py_aep, relink_maps, skip_missing, effect_defs, mask_space,
        )
    finally:
        if cleanup and os.path.exists(cleanup):
            os.remove(cleanup)

    warnings = (
        list(report.get("mask_warnings") or [])
        + list(report.get("effect_warnings") or [])
        + list(report.get("time_remap_warnings") or [])
    )
    report.update({
        "engine": "legacy",
        "py_aep": py_aep_info(py_aep),
        "warnings": warnings,
        "losses": warnings,
        "path": out_path,
        "footage": len(report.get("media") or []),
        "time_remaps": report.get("time_remaps", 0),
        "nulls": report.get("nulls_skipped", 0),
    })
    if os.path.exists(out_path):
        report["bytes"] = os.path.getsize(out_path)
    return report


# ---------------------------------------------------------------------------
# Vérification.


def verify_native(
    out_path: str,
    cli: str,
    expected_paths: list[str] | None = None,
) -> None:
    params = {"path": os.path.abspath(out_path), "dryRun": True}
    cmd = [
        cli, "--empty", "exec", "file.importAep",
        json.dumps(params, ensure_ascii=False), "--json",
    ]
    proc = _run(cmd)
    data = _json_stdout(proc.stdout, "file.importAep")
    if proc.returncode != 0 or data.get("error"):
        raise ConverterError(str(data.get("error") or proc.stderr.strip()
                                   or f"file.importAep a échoué (code {proc.returncode})"))
    report = data.get("report") or {}
    imported = report.get("imported") or {}
    losses = report.get("losses") or []
    print(
        "  vérification native : "
        + ", ".join(
            f"{imported.get(key, 0)} {label}"
            for key, label in (
                ("compositions", "comp(s)"),
                ("layers", "calque(s)"),
                ("masks", "masque(s)"),
                ("effects", "effet(s)"),
                ("timeRemaps", "time remap(s)"),
            )
        )
    )
    if losses:
        raise ConverterError("relecture native avec pertes : " + _summarize(
            [f"{loss.get('category')}: {loss.get('count')}" for loss in losses]
        ))
    if expected_paths is not None:
        with open(out_path, "rb") as handle:
            actual = _extract_alias_paths(handle.read())
        expected = Counter(expected_paths)
        found = Counter(actual)
        missing = list((expected - found).elements())
        extra = list((found - expected).elements())
        if missing or extra:
            details = []
            if missing:
                details.append("chemins absents : " + _summarize(missing, 3))
            if extra:
                details.append("chemins inattendus : " + _summarize(extra, 3))
            raise ConverterError("chemins de métrage non conformes — " + " ; ".join(details))
        print(f"  chemins : {len(expected_paths)} alias vérifié(s)")


def verify_legacy(out_path: str, py_aep_path: str | None = None) -> None:
    from ecproj_to_aep_legacy import load_py_aep, verify
    verify(out_path, load_py_aep(py_aep_path))


# ---------------------------------------------------------------------------
# Conversion.


def convert(
    ecproj_path: str,
    out_path: str,
    path_maps: list[tuple[str, str]] | None = None,
    *,
    allow_losses: bool = False,
    no_fallback: bool = False,
    force_legacy: bool = False,
    cli: str | None = None,
    skip_missing: bool = False,
    effect_defs: str | None = None,
    mask_space: str = "source",
    py_aep_path: str | None = None,
) -> dict[str, Any]:
    """Convertit un `.ecproj` en `.aep`. Retourne un rapport sérialisable.

    Moteur natif par défaut. Repli legacy automatique quand le natif ne peut pas
    exporter un métrage (cadence fractionnaire, type sans prototype…). Pour forcer
    l'ancien convertisseur : `force_legacy=True`.
    """
    maps = list(path_maps or [])
    if force_legacy:
        return _legacy_convert(
            ecproj_path, out_path, maps,
            skip_missing=skip_missing, effect_defs=effect_defs,
            mask_space=mask_space, py_aep_path=py_aep_path,
        )

    cli_path, _ = ensure_cli(cli)
    data = load_ecproj(ecproj_path)
    temp_project, cleanup = _mapped_project(ecproj_path, maps)
    project_path = temp_project
    try:
        dry = _export_aep(cli_path, project_path, out_path, dry_run=True)
        report = dry.get("report") or {}
        blocking, footage_blocking, derived, warnings = classify_losses(report, data)

        if blocking or footage_blocking or derived:
            if allow_losses:
                pass
            elif not no_fallback and footage_blocking and not blocking:
                # Le repli legacy sait importer les métrages que le natif refuse.
                legacy = _legacy_convert(
                    ecproj_path, out_path, maps,
                    skip_missing=skip_missing, effect_defs=effect_defs,
                    mask_space=mask_space, py_aep_path=py_aep_path,
                )
                legacy["fallback_reason"] = _summarize(footage_blocking)
                return legacy
            else:
                details = blocking or footage_blocking or derived
                advice = (
                    " Utilisez --allow-losses pour écrire quand même, ou --legacy "
                    "pour l'ancien convertisseur."
                )
                raise ConverterError("export natif refusé : " + _summarize(details) + advice)

        actual = _export_aep(
            cli_path, project_path, out_path, dry_run=False, allow_losses=True,
        )
        actual_report = actual.get("report") or {}
        late_blocking, late_footage, late_derived, late_warnings = classify_losses(
            actual_report, data
        )
        if (late_blocking or late_footage or late_derived) and not allow_losses:
            if os.path.exists(out_path):
                os.remove(out_path)
            raise ConverterError(
                "export natif interrompu : pertes apparues après l'analyse — "
                + _summarize(late_blocking + late_footage + late_derived)
                + " Utilisez --allow-losses pour écrire quand même."
            )
        warnings = list(dict.fromkeys(warnings + late_warnings))
        exported = actual_report.get("exported") or {}
        result = {
            "engine": "native",
            "comps": int(exported.get("compositions") or 0),
            "layers": int(exported.get("layers") or 0),
            "keys": int(exported.get("keyframes") or 0),
            "masks": int(exported.get("masks") or 0),
            "effects": int(exported.get("effects") or 0),
            "time_remaps": int(exported.get("timeRemaps") or 0),
            "nulls": int(exported.get("nullLayers") or 0),
            "footage": int(exported.get("footage") or 0),
            "warnings": warnings,
            "blocking": blocking + footage_blocking + derived if allow_losses else [],
            "losses": warnings + (blocking + footage_blocking + derived if allow_losses else []),
            "media": [],
            "path": out_path,
            "bytes": int(actual.get("bytes") or 0),
            "fallback": None,
        }
        return result
    finally:
        if cleanup and os.path.exists(cleanup):
            os.remove(cleanup)


# ---------------------------------------------------------------------------
# CLI.


def _parse_maps(values: list[str] | None, parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    maps: list[tuple[str, str]] = []
    for value in values or []:
        if "=" not in value:
            parser.error(f"--path-map attend SRC=DST, reçu : {value}")
        src, dst = value.split("=", 1)
        maps.append((src, dst))
    return maps


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convertit un .ecproj EffectCraft en .aep After Effects "
                    "(export natif, repli py_aep).",
    )
    parser.add_argument("input", help="fichier .ecproj, ou dossier avec --all")
    parser.add_argument("-o", "--out", help="chemin du .aep de sortie")
    parser.add_argument("--all", action="store_true", help="convertit tous les *.ecproj du dossier")
    parser.add_argument("--path-map", action="append", default=[], metavar="SRC=DST",
                        help="réécrit le préfixe des chemins stockés (répétable ; défaut : aucun)")
    parser.add_argument("--verify", action="store_true", help="relit le .aep produit")
    parser.add_argument("--legacy", action="store_true", help="force le convertisseur py_aep")
    parser.add_argument("--no-fallback", action="store_true",
                        help="export natif uniquement (échec si le natif ne sait pas écrire)")
    parser.add_argument("--allow-losses", action="store_true",
                        help="écrit malgré des pertes bloquantes (déconseillé)")
    parser.add_argument("--cli", help="chemin de effectcraft-cli (défaut : PATH/EFFECTCRAFT_CLI)")
    parser.add_argument("--skip-missing", action="store_true",
                        help="legacy : ignore les médias absents")
    parser.add_argument("--effect-defs", metavar="PATH", default=None,
                        help="legacy : fichier de définitions d'effets py_aep")
    parser.add_argument("--mask-space", choices=("source", "comp"), default="source",
                        help="legacy : espace des coordonnées de masque")
    parser.add_argument("--py-aep", help="legacy : dossier src de py_aep (implique --legacy)")
    args = parser.parse_args()

    maps = _parse_maps(args.path_map, parser)
    force_legacy = args.legacy or bool(args.py_aep)

    if not force_legacy:
        try:
            print(f"EffectCraft {cli_info(args.cli)} — export natif .aep")
        except ConverterError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        print("Convertisseur legacy py_aep (--legacy)")

    jobs: list[tuple[str, str]] = []
    if args.all:
        if not os.path.isdir(args.input):
            parser.error("--all attend un dossier")
        for name in sorted(os.listdir(args.input)):
            if name.lower().endswith(".ecproj"):
                src = os.path.join(args.input, name)
                jobs.append((src, os.path.splitext(src)[0] + ".aep"))
    else:
        if not os.path.isfile(args.input):
            parser.error(f"fichier introuvable : {args.input}")
        out = args.out or os.path.splitext(args.input)[0] + ".aep"
        jobs.append((args.input, out))

    if not jobs:
        print("Aucun .ecproj à convertir.")
        return 0

    failures = 0
    for src, out in jobs:
        print(f"→ {os.path.basename(src)}  ⇒  {out}")
        try:
            result = convert(
                src, out, maps,
                allow_losses=args.allow_losses,
                no_fallback=args.no_fallback,
                force_legacy=force_legacy,
                cli=args.cli,
                skip_missing=args.skip_missing,
                effect_defs=args.effect_defs,
                mask_space=args.mask_space,
                py_aep_path=args.py_aep,
            )
        except Exception as exc:
            failures += 1
            print(f"  ÉCHEC : {exc}", file=sys.stderr)
            continue

        for line in result.get("media") or []:
            print(line)
        detail = f"{result.get('comps', 0)} comp(s), {result.get('layers', 0)} calque(s)"
        if result.get("keys"):
            detail += f", {result['keys']} keyframe(s)"
        if result.get("masks"):
            detail += f", {result['masks']} masque(s)"
        if result.get("effects"):
            detail += f", {result['effects']} effet(s)"
        if result.get("time_remaps"):
            detail += f", {result['time_remaps']} time remap(s)"
        if result.get("nulls"):
            if result.get("engine") == "legacy":
                detail += f", {result['nulls']} objet(s) nul(s) non exporté(s)"
            else:
                detail += f", {result['nulls']} objet(s) nul(s)"
        detail += f" [{result.get('engine')}]"
        if result.get("py_aep"):
            detail += f" py_aep {result['py_aep']}"
        print(f"  {detail} → {out}")
        if result.get("fallback_reason"):
            print(f"  ! repli legacy : {result['fallback_reason']}", file=sys.stderr)
        for warning in result.get("warnings") or []:
            print(f"  ! {warning}", file=sys.stderr)
        for warning in result.get("blocking") or []:
            print(f"  ! perte autorisée : {warning}", file=sys.stderr)

        if args.verify:
            try:
                if result.get("engine") == "native":
                    verify_native(out, ensure_cli(args.cli)[0], expected_alias_paths(load_ecproj(src), maps))
                else:
                    verify_legacy(out, args.py_aep)
            except Exception as exc:
                print(f"  vérification impossible : {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
