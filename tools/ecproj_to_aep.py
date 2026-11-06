#!/usr/bin/env python3
"""ecproj_to_aep.py — convertit un projet EffectCraft (.ecproj) en projet After Effects (.aep).

EffectCraft ne sait pas écrire de .aep : ce convertisseur s'exécute donc **hors**
EffectCraft (Python), en post-traitement du batch. Il reconstruit le projet avec la
bibliothèque py_aep (`Scripts/py-aep-0.18.2` en priorité, replis 0.18.1 et 0.13.0),
qui écrit des .aep au format AE 2026+ (version par défaut de `py_aep.new()`), le même
chemin que l'ancien mode « py_aep Offline » du bridge Windows.

Repris du .ecproj : media importés (chemins), **solides** (couleur, largeur/hauteur, pixel
aspect), comps (nom/taille/pixel aspect/durée/cadence), calques (nom, in/out, audio, label,
anchor/position/scale) — c'est-à-dire tout ce que produit le panel Media Solution (segments
coupés et recentrés) — plus les **masques**, les **effets** (table EFFECT_MAP : Gaussian Blur
pour l'instant) et le **Time Remap** (Freeze Frame : une clé Hold, donc l'image figée sur
tout le calque). Un solide est créé au premier calque qui l'utilise via `comp.add_solid`,
puis son FootageItem est réutilisé pour les calques suivants.

Les **objets nuls** créés par le panel (« Null Segments ») ne sont pas exportés : ils
servent à regrouper/parenter les segments dans EffectCraft, et py_aep n'expose pas la
parenté. Leur rôle est compensé : la position des segments est écrite en coordonnées
**monde** (celle d'avant la parenté), donc le rendu du .aep est identique.

Les effets s'appuient sur `ae_effect_defs.aepdefs` (à côté de ce script) : py_aep ne sait
créer un effet que si sa définition existe déjà dans le projet (LIST:EfdG), ce fichier
fournit les gabarits AE (voir extract_ae_effect_defs.py pour le (re)construire depuis un
.aep Adobe qui utilise déjà l'effet). Un effet sans définition est signalé et ignoré.

Les masques sont écrits en pixels source, comme After Effects. py_aep ≤ 0.16 les
normalisait par la taille de la comp (et non par celle de la source) : la convention est
détectée à l'exécution et compensée si besoin (voir py_aep_masks_are_source_based), donc
les deux versions de py_aep produisent le même fichier.

Usage :
  python3 ecproj_to_aep.py <projet.ecproj> [-o sortie.aep] [--path-map SRC=DST] [--verify]
  python3 ecproj_to_aep.py <dossier> --all [--path-map …] [--verify]

Options :
  -o, --out FICHIER   chemin du .aep (défaut : à côté du .ecproj, même nom)
  --all               convertit tous les *.ecproj du dossier donné
  --path-map SRC=DST  réécrit le préfixe des chemins de médias (répétable) ;
                      ex. --path-map /mnt/cache=/F:  pour rouvrir sous Windows.
                      Le média est toujours lu depuis le chemin réel (Linux), seul le
                      chemin écrit dans le .aep est réécrit.
  --skip-missing      ignore les médias absents au lieu de s'arrêter
  --effect-defs PATH  fichier de définitions d'effets (défaut : ae_effect_defs.aepdefs)
  --mask-space MODE   espace des coordonnées de masque écrites : « source » (défaut,
                      coordonnées EC lues comme des pixels source — conversion interne
                      selon la version de py_aep) ou « comp » (coordonnées telles quelles)
  --verify            relit le .aep produit avec py_aep et affiche un résumé
  --py-aep PATH       dossier `src` de py_aep (défaut : recherche automatique)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

TICKS_PER_SECOND = 254_016_000_000.0

# Même ordre que py_aep.enums.Label et que les labels d'EffectCraft / After Effects.
LABEL_NAMES = [
    "None", "Red", "Yellow", "Aqua", "Pink", "Lavender", "Peach", "Sea Foam",
    "Blue", "Green", "Purple", "Orange", "Brown", "Fuchsia", "Cyan", "Sandstone",
    "Dark Green",
]

# Versions py_aep connues, la plus récente d'abord : il suffit de déposer une version
# dans Scripts/ pour que les convertisseurs la prennent (les masques s'adaptent, voir
# py_aep_masks_are_source_based). Les releases récentes utilisent un dossier
# « py-aep-… » (tiret), les anciennes « py_aep-… » (underscore) : les deux sont acceptés.
PY_AEP_VERSIONS = ("0.18.2", "0.18.1", "0.13.0")
PY_AEP_ROOTS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Scripts"),
    os.path.expanduser("~/Media_Solution/Scripts"),
    os.path.expanduser("~/Documents/Scripts"),
)
DEFAULT_PY_AEP_CANDIDATES = [os.environ.get("PY_AEP_PATH", "")] + [
    os.path.join(root, f"{prefix}{version}", "src")
    for root in PY_AEP_ROOTS for version in PY_AEP_VERSIONS
    for prefix in ("py-aep-", "py_aep-")
]

DEFAULT_EFFECT_DEFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ae_effect_defs.aepdefs")

# Table des effets EffectCraft → After Effects (extensible). Clé : id d'effet .ecproj
# (`kind.effect`) ; « ae » : match name AE (défini dans ae_effect_defs.aepdefs) ;
# « params » : match .ecproj → (nom AE, conversion de valeur ou None).
EFFECT_MAP: dict[str, dict[str, Any]] = {
    "ec.blur.gaussian": {
        "ae": "ADBE Gaussian Blur 2",
        "params": {
            "blurriness": ("Blurriness", None),
            # EffectCraft compte les popups depuis 0, AE depuis 1.
            "dimensions": ("Blur Dimensions", lambda value: float(value) + 1.0),
            "repeatEdge": ("Repeat Edge Pixels", bool),
        },
    },
}

MASK_MODES = {
    "None": "NONE", "Add": "ADD", "Subtract": "SUBTRACT", "Intersect": "INTERSECT",
    "Lighten": "LIGHTEN", "Darken": "DARKEN", "Difference": "DIFFERENCE",
}
MASK_MOTION_BLUR = {"SameAsLayer": "SAME_AS_LAYER", "On": "ON", "Off": "OFF"}
MASK_FEATHER_FALLOFF = {"Smooth": "FFO_SMOOTH", "Linear": "FFO_LINEAR"}


def load_py_aep(explicit: str | None):
    candidates = ([explicit] if explicit else []) + DEFAULT_PY_AEP_CANDIDATES
    for cand in candidates:
        if cand and os.path.isdir(cand):
            src = os.path.abspath(os.path.join(cand, "py_aep"))
            if os.path.isdir(src):
                if cand not in sys.path:
                    sys.path.insert(0, os.path.abspath(cand))
                break
    try:
        import py_aep  # type: ignore
    except Exception as e:  # pragma: no cover - dépend de l'environnement
        raise ImportError(
            "py_aep introuvable (installez ses dépendances : attrs, PyYAML) ou indiquez\n"
            f"son dossier src avec --py-aep. Erreur : {e}"
        ) from e
    return py_aep


_MASK_SOURCE_BASED_CACHE: dict[int, bool] = {}


def py_aep_masks_are_source_based(py_aep: Any) -> bool:
    """Vrai si py_aep écrit les masques en pixels source (≥ 0.17, comme After Effects).

    Les versions ≤ 0.16 normalisent par la comp : les coordonnées .ecproj (pixels
    source) doivent alors être multipliées par comp/source pour que le fichier soit
    correct (voir write_masks). Sondé sur un projet jetable — comp 100×100, solide
    200×100, sommet x=200 : 1.0 dans le fichier = espace source, 2.0 = espace comp —
    pour ne pas dépendre du numéro de version (0.13 s'annonce « 0.0.0 »).
    """
    key = id(py_aep)
    if key not in _MASK_SOURCE_BASED_CACHE:
        result = True
        try:
            app = py_aep.new()
            comp = app.project.root_folder.add_comp(name="Sonde masque", width=100, height=100,
                                                    pixel_aspect=1.0, duration=1.0, frame_rate=25.0)
            layer = comp.add_solid(color=[0.0, 0.0, 0.0], width=200, height=100)
            shape = layer["ADBE Mask Parade"].add_property("ADBE Mask Atom").property("ADBE Mask Shape")
            shape.value = py_aep.Shape(vertices=[[200.0, 0.0], [200.0, 100.0], [0.0, 100.0]])
            raw = shape.value._shph
            result = abs(float(raw.bottom_right_x) - 1.0) < 0.01
        except Exception:
            result = True
        _MASK_SOURCE_BASED_CACHE[key] = result
    return _MASK_SOURCE_BASED_CACHE[key]


def py_aep_info(py_aep: Any) -> str:
    """« 0.18.2 (…/Scripts/py-aep-0.18.2/src) » — version et origine, pour les journaux."""
    version = str(getattr(py_aep, "__version__", "") or "?").lstrip("v") or "?"
    path = os.path.dirname(os.path.dirname(os.path.abspath(getattr(py_aep, "__file__", ""))))
    if version in ("?", "0", "0.0.0"):
        # Certaines releases (0.18.2) laissent __version__ à 0.0.0 : reprendre le
        # numéro du dossier py-aep-X.Y.Z / py_aep-X.Y.Z.
        folder = os.path.basename(os.path.dirname(path))
        for prefix in ("py-aep-", "py_aep-"):
            if folder.startswith(prefix) and folder[len(prefix):]:
                version = folder[len(prefix):]
                break
    return f"{version} ({path})"


def ticks_to_seconds(ticks: Any) -> float:
    return float(ticks) / TICKS_PER_SECOND


def frame_rate(value: Any) -> float:
    if isinstance(value, dict):
        num = float(value.get("num") or 0)
        den = float(value.get("den") or 1)
        return num / den if den else 25.0
    return float(value or 25.0)


def apply_path_map(path: str, maps: list[tuple[str, str]]) -> str:
    for src, dst in maps:
        if path.startswith(src):
            return dst + path[len(src):]
    return path


def label_index(name: Any) -> int:
    if isinstance(name, int):
        return max(0, min(16, name))
    norm = str(name or "").replace(" ", "").lower()
    for i, candidate in enumerate(LABEL_NAMES):
        if candidate.replace(" ", "").lower() == norm:
            return i
    return 0


def vec3(node: dict) -> list[float] | None:
    value = node.get("value") or {}
    if value.get("t") in ("Vec3", "Vec2") and isinstance(value.get("v"), list):
        return [float(x) for x in value["v"]]
    return None


# Propriétés du groupe Transform reprises dans le .aep (match .ecproj → nom AE).
TRANSFORM_PROPS: tuple[tuple[str, str], ...] = (
    ("anchor", "ADBE Anchor Point"),
    ("position", "ADBE Position"),
    ("scale", "ADBE Scale"),
    ("orientation", "ADBE Orientation"),
    ("rotation", "ADBE Rotate Z"),
    ("rotationX", "ADBE Rotate X"),
    ("rotationY", "ADBE Rotate Y"),
    ("opacity", "ADBE Opacity"),
)


def prop_value(value: Any) -> Any:
    """Valeur d'un nœud .ecproj : {"t": "Vec3", "v": [...]} ou scalaire → valeur AE."""
    if isinstance(value, dict):
        raw = value.get("v")
        if isinstance(raw, list):
            return [float(x) for x in raw]
        if raw is None:
            return None
        return float(raw)
    return value


def _fix_value(value: Any, z_default: float | None) -> Any:
    """AE attend 100 pour l'échelle Z ; EffectCraft stocke 0 (propriétés 3D)."""
    if z_default is not None and isinstance(value, list) and len(value) > 2 and value[2] == 0.0:
        return [value[0], value[1], z_default]
    return value


def _shift(value: Any, offset: tuple[float, float]) -> Any:
    if offset == (0.0, 0.0) or not isinstance(value, list) or len(value) < 2:
        return value
    return [value[0] + offset[0], value[1] + offset[1]] + list(value[2:])


def find_group(layer: dict, match: str) -> dict | None:
    """Groupe du calque .ecproj (`transform`, `masks`, `effects`…)."""
    for child in (layer.get("props") or {}).get("children") or []:
        if child.get("match") == match:
            return child
    return None


def find_layer_property(layer: dict, match: str) -> dict | None:
    """Propriété directe du calque (`props.children`), par ex. `timeRemap`.

    Les propriétés du Transform sont dans le sous-groupe `transform` (voir
    find_transform_node) ; le Time Remap est à la racine du calque, comme dans AE.
    """
    for child in (layer.get("props") or {}).get("children") or []:
        if child.get("match") == match:
            return child
    return None


def find_transform_node(layer: dict, match: str) -> dict | None:
    """Nœud brut d'une propriété du groupe Transform (contient valeur et keyframes)."""
    transform = find_group(layer, "transform") or {}
    for node in transform.get("children") or []:
        if node.get("match") == match:
            return node
    return None


def apply_keyframe_meta(kf: Any, key: dict, py_aep: Any) -> None:
    """Interpolation (linéaire/bezier/hold), easings temporels et tangentes spatiales
    d'une clé écrite dans le .aep."""
    interp = {
        "in": str(key.get("in_interp") or "Linear").upper(),
        "out": str(key.get("out_interp") or "Linear").upper(),
    }
    for side, value in interp.items():
        try:
            setattr(kf, f"{side}_interpolation_type",
                    getattr(py_aep.KeyframeInterpolationType, value))
        except Exception:
            pass
    for side in ("in", "out"):
        ease = key.get(f"{side}_ease") or []
        if interp[side] != "BEZIER" or not ease:
            continue
        try:
            setattr(kf, f"{side}_temporal_ease",
                    [py_aep.KeyframeEase(e.get("speed") or 0.0, e.get("influence") or 0.0)
                     for e in ease])
        except Exception:
            pass
    try:
        if key.get("spatial_in") and kf.in_spatial_tangent is not None:
            kf.in_spatial_tangent = [float(x) for x in key["spatial_in"]]
        if key.get("spatial_out") and kf.out_spatial_tangent is not None:
            kf.out_spatial_tangent = [float(x) for x in key["spatial_out"]]
        if key.get("spatial_auto") is not None:
            kf.spatial_auto_bezier = bool(key["spatial_auto"])
        if key.get("spatial_continuous") is not None:
            kf.spatial_continuous = bool(key["spatial_continuous"])
    except Exception:
        pass


def apply_property(prop: Any, node: dict, py_aep: Any, offset: tuple[float, float] = (0.0, 0.0),
                   z_default: float | None = None, value_map: Any = None) -> int:
    """Écrit une propriété dans le .aep : valeur statique, ou keyframes complètes.

    Les keyframes reprennent temps/valeurs, interpolation (linéaire/bezier/hold), easings
    temporels et tangentes spatiales. `offset` décale les positions des calques parentés
    (leur position stockée est relative au parent). `value_map` convertit chaque valeur
    (ex. popup EffectCraft 0-based → AE 1-based). Retourne le nombre de clés écrites.
    """
    keys = node.get("keys") or []
    if not keys:
        value = prop_value(node.get("value"))
        if value is None:
            return 0
        if value_map is not None:
            value = value_map(value)
        prop.value = _shift(_fix_value(value, z_default), offset)
        return 0

    for key in keys:
        value = prop_value(key.get("value"))
        if value is None:
            continue
        if value_map is not None:
            value = value_map(value)
        prop.set_value_at_time(ticks_to_seconds(key.get("time") or 0),
                               _shift(_fix_value(value, z_default), offset))

    written = 0
    for index, key in enumerate(keys):
        try:
            kf = prop.keyframes[index]
        except (IndexError, AttributeError):
            break
        apply_keyframe_meta(kf, key, py_aep)
        written += 1
    return written


def find_transform_nodes(layer: dict) -> dict[str, list[float]]:
    """Extrait anchor/position/scale du groupe Transform du calque .ecproj."""
    out: dict[str, list[float]] = {}
    props = layer.get("props") or {}
    for child in props.get("children") or []:
        if child.get("match") != "transform":
            continue
        for node in child.get("children") or []:
            match = node.get("match")
            if match in ("anchor", "position", "scale"):
                value = vec3(node)
                if value:
                    out[match] = value
    return out


def set_audio(layer_obj: Any, enabled: bool) -> None:
    for attr in ("audio_enabled", "audio_active"):
        if hasattr(layer_obj, attr):
            try:
                setattr(layer_obj, attr, enabled)
                return
            except Exception:
                continue


def relink_stored_path(source: Any, stored_path: str) -> None:
    """Réécrit le chemin stocké d'une FileSource déjà importée (chunk LIST:Als2 → alas).

    Le média est importé depuis le chemin réel (Linux, pour le probe), puis le chemin
    écrit dans le .aep est remplacé — c'est ce qui permet le mapping /mnt/cache → F:.
    """
    pin = getattr(source, "_pin", None)
    if pin is None:
        raise ValueError("relink impossible : source sans chunk de chemin (_pin)")
    for chunk in pin.chunks:
        if getattr(chunk, "list_type", None) != "Als2":
            continue
        for sub in chunk.chunks:
            if getattr(sub, "chunk_type", None) == "alas":
                data = json.loads(sub.value)
                data["fullpath"] = stored_path
                sub.value = json.dumps(data, sort_keys=True, separators=(",", ":"))
                try:
                    source._file = stored_path
                except Exception:
                    pass
                return
    raise ValueError("relink impossible : chunk 'alas' introuvable")


def world_offset(layer: dict, layers_by_id: dict) -> tuple[float, float]:
    """Décalage à ajouter à la position d'un calque parenté pour retrouver sa position
    « monde ».

    EffectCraft compense la parenté (comme le pick-whip d'AE) : la position stockée est
    relative au parent. py_aep n'exposant pas la parenté, le .aep reçoit la position monde.
    Les objets nuls n'ont ni rotation ni échelle : le décalage est la somme des
    (position - ancre) de la chaîne de parents.
    """
    dx = dy = 0.0
    seen: set[int] = set()
    current = layer
    while True:
        parent_id = current.get("parent")
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        parent = layers_by_id.get(parent_id)
        if not parent:
            break
        values = find_transform_nodes(parent)
        pos = values.get("position") or [0.0, 0.0]
        anchor = values.get("anchor") or [0.0, 0.0]
        dx += float(pos[0]) - float(anchor[0])
        dy += float(pos[1]) - float(anchor[1])
        current = parent
    return dx, dy


def load_effect_defs(path: str) -> list:
    """Définitions d'effets du fichier .aepdefs : `(match_name, nom, gabarit sspc)`.

    py_aep n'accepte d'ajouter un effet (Effect Parade) que si le projet en connaît la
    définition (LIST:EfdG) : ces gabarits, extraits d'un .aep Adobe réel par
    extract_ae_effect_defs.py, sont injectés dans le projet neuf.
    """
    from py_aep.binary.chunk import read_aep
    from py_aep.parsers.effect import effect_definition_entries

    with open(path, "rb") as handle:
        root, _ = read_aep(handle)
    return effect_definition_entries(root.chunks)


def collect_effects(layer: dict) -> list[dict]:
    """Instances d'effets du calque .ecproj (groupe `effects`)."""
    group = find_group(layer, "effects") or {}
    return [node for node in group.get("children") or []
            if (node.get("kind") or {}).get("kind") == "Effect"]


def project_effects(items: dict) -> list[dict]:
    """Toutes les instances d'effets du projet .ecproj (toutes comps)."""
    found: list[dict] = []
    for item in items.values():
        kind = item.get("kind") or {}
        if kind.get("type") != "Comp":
            continue
        for layer in kind.get("layers") or []:
            found.extend(collect_effects(layer))
    return found


def enabled_masks(layer: dict) -> list[dict]:
    """Masques actifs du calque .ecproj (un masque désactivé ne découpe rien)."""
    group = find_group(layer, "masks") or {}
    return [node for node in group.get("children") or []
            if (node.get("kind") or {}).get("kind") == "Mask"
            and node.get("enabled") is not False]


def write_effects(av: Any, layer: dict, py_aep: Any, report: dict, name: str) -> None:
    """Écrit les effets du calque (EFFECT_MAP) dans le .aep."""
    for node in collect_effects(layer):
        effect_id = (node.get("kind") or {}).get("effect") or ""
        spec = EFFECT_MAP.get(effect_id)
        if spec is None:
            report["effect_warnings"].append(f"{name} : effet {effect_id or '?'} non géré")
            continue
        try:
            prop_group = av["ADBE Effect Parade"].add_property(spec["ae"])
        except Exception as e:
            report["effect_warnings"].append(
                f"{name} : {effect_id} non écrit ({e}) — vérifiez "
                f"{os.path.basename(report.get('effect_defs_path') or DEFAULT_EFFECT_DEFS)} "
                f"(extract_ae_effect_defs.py)")
            continue
        try:
            prop_group.enabled = bool(node.get("enabled", True))
        except Exception:
            pass
        params = {getattr(p, "name", ""): p for p in prop_group.properties
                  if hasattr(p, "value")}
        for child in node.get("children") or []:
            target = spec["params"].get(child.get("match"))
            if target is None:
                continue
            param_name, value_map = target
            param = params.get(param_name)
            if param is None:
                report["effect_warnings"].append(
                    f"{name} : paramètre {param_name} introuvable dans {spec['ae']}")
                continue
            try:
                report["keys"] += apply_property(param, child, py_aep, value_map=value_map)
            except Exception as e:
                report["effect_warnings"].append(f"{name} : {param_name} non appliqué ({e})")
        report["effects"] += 1


def write_time_remap(av: Any, layer: dict, py_aep: Any, report: dict, name: str) -> int:
    """Écrit le Time Remap du calque .ecproj dans le .aep.

    EffectCraft stocke le Time Remap comme AE : une propriété scalaire à la racine du
    calque, en secondes de source. Un Freeze Frame y laisse **une seule clé Hold** ;
    une propriété à une clé étant constante, tout le calque montre cette image.

    py_aep active le remap en semant deux clés d'identité : on n'en garde qu'une, puis
    on écrit la courbe EC par-dessus (temps en secondes de comp, valeurs en secondes
    de source), interpolation comprise.
    """
    node = find_layer_property(layer, "timeRemap")
    if node is None:
        return 0
    if not getattr(av, "can_set_time_remap_enabled", False):
        report["time_remap_warnings"].append(
            f"{name} : time remap ignoré (source sans durée, comme une image fixe)")
        return 0

    keys = sorted(node.get("keys") or [], key=lambda key: float(key.get("time") or 0.0))
    keys = [key for key in keys if prop_value(key.get("value")) is not None]
    deduped = []
    for key in keys:
        if deduped and float(key.get("time") or 0.0) == float(deduped[-1].get("time") or 0.0):
            report["time_remap_warnings"].append(
                f"{name} : deux clés de time remap au même temps, la seconde est ignorée")
            continue
        deduped.append(key)
    keys = deduped
    enabled = False
    try:
        av.time_remap_enabled = True
        enabled = True
        prop = av["ADBE Time Remapping"]
        while len(prop.keyframes) > 1:
            prop.remove_key(len(prop.keyframes) - 1)
        if not prop.keyframes:
            raise ValueError("propriété Time Remap sans clé après activation")

        if keys:
            first = keys[0]
            kf = prop.keyframes[0]
            kf.time = ticks_to_seconds(first.get("time") or 0)
            kf.value = prop_value(first.get("value"))
            for key in keys[1:]:
                prop.set_value_at_time(ticks_to_seconds(key.get("time") or 0),
                                       prop_value(key.get("value")))
        else:
            # Remap activé sans clé (toutes supprimées dans EffectCraft) : la valeur
            # statique s'applique au calque entier. Une clé Hold unique l'exprime.
            kf = prop.keyframes[0]
            kf.time = ticks_to_seconds(layer.get("in_point") or 0)
            value = prop_value(node.get("value"))
            kf.value = value if value is not None else 0.0

        written = 0
        if keys:
            for index, key in enumerate(keys):
                try:
                    kf = prop.keyframes[index]
                except (IndexError, AttributeError):
                    break
                apply_keyframe_meta(kf, key, py_aep)
                written += 1
        else:
            for side in ("in", "out"):
                setattr(kf, f"{side}_interpolation_type",
                        py_aep.KeyframeInterpolationType.HOLD)
            written = 1
        report["time_remaps"] += 1
        return written
    except Exception as e:
        if enabled:
            # Ne pas laisser un remap à moitié écrit (clés d'identité seules) : le
            # désactiver rend le calque au comportement d'avant, comme un remap ignoré.
            try:
                av.time_remap_enabled = False
            except Exception:
                pass
        report["time_remap_warnings"].append(f"{name} : time remap non appliqué ({e})")
        return 0


def mask_path_shape(node: dict, py_aep: Any, scale: tuple[float, float]) -> Any:
    """Shape py_aep d'un nœud Mask Path .ecproj (vertices/tangentes mis à l'échelle)."""
    value = ((node.get("value") or {}).get("v")) or {}
    sx, sy = scale
    vertices = [[float(x) * sx, float(y) * sy] for x, y in value.get("vertices") or []]
    count = len(vertices)

    def tangents(key: str) -> list[list[float]]:
        raw = value.get(key) or []
        points = [[float(x) * sx, float(y) * sy] for x, y in raw[:count]]
        points += [[0.0, 0.0]] * (count - len(points))
        return points

    return py_aep.Shape(vertices=vertices, in_tangents=tangents("in_tangents"),
                        out_tangents=tangents("out_tangents"),
                        closed=bool(value.get("closed", True)))


def write_mask_path(prop: Any, node: dict, py_aep: Any, scale: tuple[float, float],
                    report: dict, name: str) -> None:
    """Mask Path : valeur statique.

    py_aep ne sait pas animer une forme de masque créée de zéro (« animating a static
    'ADBE Mask Shape' ») : un masque animé dans EffectCraft est donc exporté à sa première
    clé, avec un avertissement.
    """
    keys = node.get("keys") or []
    if not keys:
        prop.value = mask_path_shape(node, py_aep, scale)
        return
    if any(key.get("value") != keys[0].get("value") for key in keys):
        report["mask_warnings"].append(
            f"{name} : masque animé — seule la 1re clé du tracé est exportée (limite py_aep)")
    prop.value = mask_path_shape({**node, "value": keys[0].get("value")}, py_aep, scale)


def write_masks(av: Any, layer: dict, py_aep: Any, source_size: tuple[int, int] | None,
                mask_space: str, comp_size: tuple[int, int], report: dict, name: str) -> None:
    """Écrit les masques du calque .ecproj dans le .aep.

    Les coordonnées .ecproj sont en pixels source ; py_aep normalise par la comp, AE par
    la source : on écrit donc `pixels_ec × comp/source` (option « source », défaut) pour
    retrouver la même géométrie à l'écran.
    """
    group = find_group(layer, "masks") or {}
    for node in group.get("children") or []:
        if (node.get("kind") or {}).get("kind") != "Mask":
            continue
        if node.get("enabled") is False:
            # interrupteur de masque d'EffectCraft : un masque désactivé ne découpe rien,
            # donc il n'est pas écrit (l'équivalent AE est l'absence de masque).
            report["mask_warnings"].append(f"{name} : masque « {node.get('name')} » désactivé, non exporté")
            continue
        if mask_space == "source" and not py_aep_masks_are_source_based(py_aep) \
                and source_size and source_size[0] and source_size[1]:
            # py_aep ≤ 0.16 normalise les masques par la comp : compenser pour écrire
            # des pixels source (≥ 0.17 suit AE et n'a besoin d'aucune conversion).
            scale = (comp_size[0] / float(source_size[0]), comp_size[1] / float(source_size[1]))
        else:
            scale = (1.0, 1.0)
        try:
            mask = av["ADBE Mask Parade"].add_property("ADBE Mask Atom")
        except Exception as e:
            report["mask_warnings"].append(f"{name} : masque non écrit ({e})")
            continue
        try:
            mask.name = node.get("name") or mask.name
        except Exception:
            pass
        kind = node.get("kind") or {}
        try:
            mask.mask_mode = getattr(py_aep.MaskMode,
                                     MASK_MODES.get(str(kind.get("mode")), "ADD"))
            mask.inverted = bool(kind.get("inverted"))
            mask.locked = bool(kind.get("locked"))
            color = kind.get("color") or []
            if len(color) == 3:
                mask.color = [float(c) / 255.0 for c in color]
            mask.mask_motion_blur = getattr(py_aep.MaskMotionBlur,
                                            MASK_MOTION_BLUR.get(str(kind.get("motion_blur")),
                                                                 "SAME_AS_LAYER"))
            mask.mask_feather_falloff = getattr(py_aep.MaskFeatherFalloff,
                                                MASK_FEATHER_FALLOFF.get(str(kind.get("feather_falloff")),
                                                                         "FFO_SMOOTH"))
        except Exception as e:
            report["mask_warnings"].append(f"{name} : attributs de masque non appliqués ({e})")
        children = {child.get("match"): child for child in node.get("children") or []}
        try:
            path = children.get("path")
            if path is not None:
                write_mask_path(mask.property("ADBE Mask Shape"), path, py_aep, scale, report, name)
            feather = children.get("feather")
            if feather is not None:
                report["keys"] += apply_property(mask.property("ADBE Mask Feather"), feather, py_aep)
            opacity = children.get("opacity")
            if opacity is not None:
                report["keys"] += apply_property(mask.property("ADBE Mask Opacity"), opacity, py_aep)
            expansion = children.get("expansion")
            if expansion is not None:
                report["keys"] += apply_property(mask.property("ADBE Mask Offset"), expansion, py_aep)
            if kind.get("roto_bezier"):
                mask.roto_bezier = True
        except Exception as e:
            report["mask_warnings"].append(f"{name} : masque partiellement écrit ({e})")
            continue
        report["masks"] += 1


def convert(
    ecproj_path: str,
    out_path: str,
    py_aep: Any,
    path_maps: list[tuple[str, str]],
    skip_missing: bool,
    effect_defs_path: str | None = None,
    mask_space: str = "source",
) -> dict[str, Any]:
    with open(ecproj_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items") or {}
    if not isinstance(items, dict):
        raise ValueError("schéma .ecproj inattendu : 'items' doit être un objet")

    Label = __import__("py_aep.enums", fromlist=["Label"]).Label

    app = py_aep.new()
    project = app.project

    # 0) Définitions d'effets (LIST:EfdG) : nécessaires pour créer un effet dans le .aep.
    effect_defs_path = effect_defs_path or DEFAULT_EFFECT_DEFS
    if project_effects(items):
        defs: list = []
        if os.path.isfile(effect_defs_path):
            try:
                defs = load_effect_defs(effect_defs_path)
            except Exception as e:
                print(f"  ! définitions d'effets illisibles ({effect_defs_path}) : {e}", file=sys.stderr)
        if defs:
            project._effect_definitions_cache = defs
        elif effect_defs_path:
            print(f"  ! fichier de définitions d'effets introuvable : {effect_defs_path}",
                  file=sys.stderr)

    # 1) Médias
    footage_by_item: dict[str, Any] = {}
    solid_specs: dict[str, dict[str, Any]] = {}
    media_report: list[str] = []
    source_size_by_item: dict[str, tuple[int, int]] = {}
    for item_id, item in items.items():
        kind = item.get("kind") or {}
        if kind.get("type") == "Solid":
            # Un solide n'a pas de fichier : il sera créé dans la comp au premier
            # calque qui l'utilise (comp.add_solid), puis réutilisé par son FootageItem.
            width = int(kind.get("width") or 0)
            height = int(kind.get("height") or 0)
            source_size_by_item[str(item_id)] = (width, height)
            color = kind.get("color") or [0.0, 0.0, 0.0]
            try:
                raw_rgb = [float(c) for c in list(color)[:3]]
            except (TypeError, ValueError):
                raw_rgb = [0.0, 0.0, 0.0]
            # Le schéma .ecproj stocke 0..1 ; on tolère aussi 0..255.
            rgb = [max(0.0, min(1.0, c / 255.0 if c > 1.5 else c)) for c in raw_rgb]
            while len(rgb) < 3:
                rgb.append(0.0)
            solid_specs[str(item_id)] = {
                "name": str(item.get("name") or "Solid"),
                "color": rgb,
                "width": width or None,
                "height": height or None,
                "pixel_aspect": float(kind.get("pixel_aspect") or 1.0),
            }
            continue
        if kind.get("type") != "Footage":
            continue
        source_size_by_item[str(item_id)] = (int(kind.get("width") or 0),
                                             int(kind.get("height") or 0))
        real_path = str(kind.get("path") or "")
        if not real_path:
            media_report.append(f"  [{item_id}] {item.get('name')} : pas de chemin, ignoré")
            continue
        stored_path = apply_path_map(real_path, path_maps)
        if not os.path.exists(real_path):
            message = f"  [{item_id}] média introuvable : {real_path}"
            if skip_missing:
                media_report.append(message + " (ignoré)")
                continue
            raise FileNotFoundError(
                message + "\n  (média absent du disque : copiez-le, ou utilisez --skip-missing)"
            )
        footage = project.import_file(py_aep.ImportOptions(real_path))
        if stored_path != real_path:
            relink_stored_path(footage.main_source, stored_path)
            media_report.append(f"  [{item_id}] {os.path.basename(real_path)} importé (chemin .aep : {stored_path})")
        else:
            media_report.append(f"  [{item_id}] {os.path.basename(real_path)} importé")
        footage_by_item[str(item_id)] = footage

    # 2) Compositions
    comps_done = 0
    layers_done = 0
    nulls_skipped = 0
    reparented = 0
    keys_written = 0
    report = {
        "masks": 0,
        "effects": 0,
        "keys": 0,
        "time_remaps": 0,
        "mask_warnings": [],
        "effect_warnings": [],
        "time_remap_warnings": [],
        "effect_defs_path": effect_defs_path,
    }
    for item_id, item in items.items():
        kind = item.get("kind") or {}
        if kind.get("type") != "Comp":
            continue
        comp_width = int(kind.get("width") or 1080)
        comp_height = int(kind.get("height") or 1920)
        comp = project.root_folder.add_comp(
            name=item.get("name") or "Comp",
            width=comp_width,
            height=comp_height,
            pixel_aspect=float(kind.get("pixel_aspect") or 1.0),
            duration=max(ticks_to_seconds(kind.get("duration") or 1.0), 1.0 / max(frame_rate(kind.get("frame_rate")), 1.0)),
            frame_rate=frame_rate(kind.get("frame_rate")),
        )
        comps_done += 1
        comp_layers = kind.get("layers") or []
        layers_by_id = {int(l["id"]): l for l in comp_layers if l.get("id") is not None}

        # Le tableau .ecproj est ordonné du haut vers le bas (comme comp.layer(1..n)) ;
        # comp.add() empile en tête → on ajoute en ordre inverse pour préserver l'ordre.
        for layer in reversed(comp_layers):
            source = layer.get("source") or {}
            if (source.get("type") or "") == "Null":
                # Les objets nuls (« Null Segments ») sont propres à EffectCraft : ils ne
                # sont pas exportés. Les calques qui leur étaient parentés reçoivent leur
                # position monde (voir world_offset), le rendu est donc identique.
                nulls_skipped += 1
                continue
            item_key = str(source.get("item"))
            is_solid = (source.get("type") or "") == "Solid"
            if is_solid:
                # Première utilisation d'un solide : comp.add_solid crée à la fois
                # l'élément de projet (dossier Solids d'AE) et le calque. On garde
                # ensuite son FootageItem pour les autres calques qui le référencent.
                footage = footage_by_item.get(item_key)
                if footage is None:
                    spec = solid_specs.get(item_key)
                    if spec is None:
                        print(f"  ! calque '{layer.get('name')}' : solide source absent, ignoré",
                              file=sys.stderr)
                        continue
                    try:
                        av = comp.add_solid(
                            color=spec["color"],
                            name=spec["name"],
                            width=spec["width"],
                            height=spec["height"],
                            pixel_aspect=spec["pixel_aspect"],
                        )
                    except Exception as e:
                        print(f"  ! calque '{layer.get('name')}' : solide non créé ({e})",
                              file=sys.stderr)
                        continue
                    footage_by_item[item_key] = av.source
                else:
                    av = comp.add(footage)
            else:
                footage = footage_by_item.get(item_key)
                if footage is None:
                    print(f"  ! calque '{layer.get('name')}' : média source absent, ignoré",
                          file=sys.stderr)
                    continue
                av = comp.add(footage)
            try:
                av.name = layer.get("name") or av.name
            except Exception:
                pass
            try:
                av.in_point = ticks_to_seconds(layer.get("in_point") or 0)
                av.out_point = ticks_to_seconds(layer.get("out_point") or 0)
            except Exception as e:
                print(f"  ! calque '{layer.get('name')}' : in/out non appliqués ({e})", file=sys.stderr)
            # Un solide n'a pas de piste audio : ne pas activer le switch audio.
            set_audio(av, False if is_solid
                      else bool((layer.get("switches") or {}).get("audio", False)))
            if (layer.get("switches") or {}).get("adjustment"):
                try:
                    av.adjustment_layer = True
                except Exception:
                    pass
            try:
                av.label = Label(label_index(layer.get("label")))
            except Exception:
                pass

            offset = world_offset(layer, layers_by_id) if layer.get("parent") else (0.0, 0.0)
            if offset != (0.0, 0.0):
                reparented += 1
            for match, prop_name in TRANSFORM_PROPS:
                node = find_transform_node(layer, match)
                if not node:
                    continue
                try:
                    keys_written += apply_property(
                        av.transform[prop_name], node, py_aep,
                        offset if match == "position" else (0.0, 0.0),
                        100.0 if match == "scale" else None,
                    )
                except Exception as e:
                    print(f"  ! calque '{layer.get('name')}' : {prop_name} non appliqué ({e})", file=sys.stderr)
            write_masks(av, layer, py_aep, source_size_by_item.get(str(source.get("item"))),
                        mask_space, (comp_width, comp_height), report, layer.get("name") or "?")
            write_effects(av, layer, py_aep, report, layer.get("name") or "?")
            keys_written += write_time_remap(av, layer, py_aep, report,
                                             layer.get("name") or "?")
            layers_done += 1

    # py_aep refuse d'écraser un .aep existant (save() lève FileExistsError) :
    # on écrit dans un fichier temporaire puis on remplace, ce qui garde l'ancien
    # .aep intact si la sérialisation échoue.
    tmp_out = out_path + ".tmp"
    for stale in (tmp_out,):
        if os.path.exists(stale):
            os.remove(stale)
    project.save(tmp_out)
    os.replace(tmp_out, out_path)
    return {
        "comps": comps_done,
        "layers": layers_done,
        "nulls_skipped": nulls_skipped,
        "unparented": reparented,
        "keys": keys_written + report["keys"],
        "masks": report["masks"],
        "effects": report["effects"],
        "time_remaps": report["time_remaps"],
        "mask_warnings": report["mask_warnings"],
        "effect_warnings": report["effect_warnings"],
        "time_remap_warnings": report["time_remap_warnings"],
        "media": media_report,
    }


def verify(aep_path: str, py_aep: Any) -> None:
    app = py_aep.parse(aep_path)
    project = app.project
    print(f"Vérification de {os.path.basename(aep_path)} (AE {getattr(app, 'version', '?')}) :")
    for comp in project.compositions:
        print(f"  comp '{comp.name}' {comp.width}x{comp.height} @ {comp.frame_rate:.3f} fps, {comp.duration:.3f} s, {len(comp.layers)} calque(s)")
        for layer in comp.layers:
            try:
                anchor = layer.transform["ADBE Anchor Point"].value
                pos = layer.transform["ADBE Position"].value
                scale = layer.transform["ADBE Scale"].value
            except Exception:
                anchor = pos = scale = "?"
            animated = [(p.name, len(p.keyframes)) for p in layer.transform if len(p.keyframes) > 0]
            extras = []
            try:
                effects = layer.effects
                if effects:
                    extras.append("effets: " + ", ".join(
                        e.name for e in effects.properties if getattr(e, "name", None)))
            except Exception:
                pass
            try:
                masks = layer.masks
                if masks:
                    extras.append("masques: " + ", ".join(
                        m.name for m in masks.properties if getattr(m, "name", None)))
            except Exception:
                pass
            try:
                if layer.time_remap_enabled:
                    remap = layer["ADBE Time Remapping"]
                    keys = ", ".join(f"{k.time:.3f}s→{k.value:.3f}s"
                                     for k in remap.keyframes)
                    extras.append(f"time remap ({keys})")
            except Exception:
                pass
            print(
                f"    '{layer.name}' in={layer.in_point:.3f} out={layer.out_point:.3f} "
                f"label={layer.label!r} anchorX={anchor[0] if isinstance(anchor, list) else anchor} "
                f"posX={pos[0] if isinstance(pos, list) else pos} scale={scale}"
                + (f" | keyframes: {animated}" if animated else "")
                + (f" | {' | '.join(extras)}" if extras else "")
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Convertit un .ecproj EffectCraft en .aep After Effects.")
    parser.add_argument("input", help="fichier .ecproj, ou dossier avec --all")
    parser.add_argument("-o", "--out", help="chemin du .aep de sortie")
    parser.add_argument("--all", action="store_true", help="convertit tous les *.ecproj du dossier")
    parser.add_argument("--path-map", action="append", default=[], metavar="SRC=DST",
                        help="réécrit le préfixe des chemins de médias (répétable)")
    parser.add_argument("--skip-missing", action="store_true", help="ignore les médias absents")
    parser.add_argument("--effect-defs", metavar="PATH", default=None,
                        help="fichier de définitions d'effets (défaut : ae_effect_defs.aepdefs)")
    parser.add_argument("--mask-space", choices=("source", "comp"), default="source",
                        help="espace des coordonnées de masque écrites (défaut : source)")
    parser.add_argument("--verify", action="store_true", help="relit le .aep produit")
    parser.add_argument("--py-aep", help="dossier src de py_aep")
    args = parser.parse_args()

    try:
        py_aep = load_py_aep(args.py_aep)
    except ImportError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"py_aep {py_aep_info(py_aep)}")

    maps: list[tuple[str, str]] = []
    for m in args.path_map:
        if "=" not in m:
            parser.error(f"--path-map attend SRC=DST, reçu : {m}")
        src, dst = m.split("=", 1)
        maps.append((src, dst))

    jobs: list[tuple[str, str]] = []
    if args.all:
        if not os.path.isdir(args.input):
            parser.error("--all attend un dossier")
        for name in sorted(os.listdir(args.input)):
            if name.lower().endswith(".ecproj"):
                src = os.path.join(args.input, name)
                jobs.append((src, os.path.splitext(src)[0] + ".aep"))
    else:
        out = args.out or os.path.splitext(args.input)[0] + ".aep"
        jobs.append((args.input, out))

    if not jobs:
        print("Aucun .ecproj à convertir.")
        return 0

    failures = 0
    for src, out in jobs:
        print(f"→ {os.path.basename(src)}  ⇒  {out}")
        try:
            report = convert(src, out, py_aep, maps, args.skip_missing,
                             args.effect_defs, args.mask_space)
        except Exception as e:
            failures += 1
            print(f"  ÉCHEC : {e}", file=sys.stderr)
            continue
        for line in report["media"]:
            print(line)
        detail = f"{report['comps']} comp(s), {report['layers']} calque(s)"
        if report.get("keys"):
            detail += f", {report['keys']} keyframe(s)"
        if report.get("masks"):
            detail += f", {report['masks']} masque(s)"
        if report.get("effects"):
            detail += f", {report['effects']} effet(s)"
        if report.get("time_remaps"):
            detail += f", {report['time_remaps']} time remap(s)"
        if report.get("nulls_skipped"):
            detail += f", {report['nulls_skipped']} objet(s) nul(s) non exporté(s)"
        if report.get("unparented"):
            detail += f", {report['unparented']} calque(s) parenté(s) replacé(s) en position monde"
        print(f"  {detail} → {out}")
        for key in ("mask_warnings", "effect_warnings", "time_remap_warnings"):
            for line in report.get(key) or []:
                print(f"  ! {line}", file=sys.stderr)
        if args.verify:
            try:
                verify(out, py_aep)
            except Exception as e:
                print(f"  vérification impossible : {e}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
