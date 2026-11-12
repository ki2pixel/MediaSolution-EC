#!/usr/bin/env python3
"""ecproj_common.py — helpers partagés entre les convertisseurs .aep et .prproj.

Le convertisseur `.prproj` (`ecproj_to_prproj.py`) importe `ecproj_to_aep` pour ces
quelques fonctions de lecture du schéma `.ecproj` (propriétés, transformation,
parenté, masques, effets). Elles vivent ici pour que le convertisseur `.aep` puisse
changer de moteur (export natif EffectCraft ou legacy py_aep) sans toucher au
`.prproj`.
"""

from __future__ import annotations

from typing import Any

TICKS_PER_SECOND = 254_016_000_000.0


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


def vec3(node: dict) -> list[float] | None:
    value = node.get("value") or {}
    if value.get("t") in ("Vec3", "Vec2") and isinstance(value.get("v"), list):
        return [float(x) for x in value["v"]]
    return None


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


def world_offset(layer: dict, layers_by_id: dict) -> tuple[float, float]:
    """Décalage à ajouter à la position d'un calque parenté pour retrouver sa position
    « monde ».

    EffectCraft compense la parenté (comme le pick-whip d'AE) : la position stockée est
    relative au parent. py_aep n'exposant pas la parenté, le .aep legacy reçoit la position
    monde. Les objets nuls n'ont ni rotation ni échelle : le décalage est la somme des
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


__all__ = [
    "TICKS_PER_SECOND",
    "apply_path_map",
    "collect_effects",
    "enabled_masks",
    "find_group",
    "find_layer_property",
    "find_transform_node",
    "find_transform_nodes",
    "frame_rate",
    "project_effects",
    "prop_value",
    "ticks_to_seconds",
    "vec3",
    "world_offset",
]
