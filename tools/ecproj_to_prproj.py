#!/usr/bin/env python3
"""ecproj_to_prproj.py — convertit un projet EffectCraft (.ecproj) en projet Premiere Pro (.prproj).

EffectCraft ne sait pas écrire de .prproj et son export FCP7/xmeml centre mal les clips à
l'import dans Premiere Pro. Cet outil reconstruit **directement** le projet Premiere depuis le
JSON .ecproj, hors Adobe, sans passer par le .aep.

Le format .prproj (gzip d'un XML `PremiereData`) n'est pas documenté : la structure écrite ici
a été relevée sur des projets de référence générés par Adobe (Premiere/AE 2026) et alignée
valeur par valeur avec le .ecproj d'origine. Correspondances vérifiées :

  - une comp EffectCraft  → une séquence Premiere (1 piste V + 1 piste A, davantage si des
                            calques se chevauchent — allocation automatique) ;
  - un calque Footage     → un item de piste (Start/End = in_point/out_point) ;
  - le groupe Transform   → le filtre « AE.ADBE Motion » (11 paramètres) :
        Position = [x / largeur_comp, y / hauteur_comp]     (Point normalisé)
        Anchor   = [x / largeur_source, y / hauteur_source]
        Scale    = pourcentage (2 emplacements + Uniform Scale), Rotation = degrés ;
  - les keyframes         → StartKeyframe (sentinelle -100 h = valeur de la 1re clé) + lignes
                            `temps,x:y,<ease>,5,4,<tangentes>` ; clés linéaires : vitesses
                            |Δnormalisé|/Δt et influence 1/6 ; clés Bézier (ease 0 / 33 %) :
                            gabarit Adobe « 5,0,0,0,0,0 » ; tangentes spatiales automatiques
                            (P_next − P_prev)/6, les valeurs explicites du .ecproj sinon ;
  - le Time Remap         → un Freeze Frame (une clé Hold constante) devient un **Frame Hold**
                            natif (`FrameHold` 4 + `FrameHoldStart` sur le `VideoClip`) ; une
                            vraie courbe de vitesse devient un objet `TimeRemapping` +
                            paramètre « Speed » (courbe TIME, valeurs en secondes source),
                            référencé après `Source` ;
  - les calques parentés  → position **monde** (les objets nuls « Null Segments » ne sont pas
                            exportés, comme pour le .aep) ;
  - les médias            → vidéo, image fixe (structure « still » à durée infinie) et audio
                            seul (sans flux vidéo) ; chemins réécrits par défaut selon
                            PATH_MAP (/mnt/cache → F:, pour l'ouverture sous Windows) ;
  - les masques « Add »   → un filtre `AE.ADBE AEMask2` (27 paramètres) écrit en sous-composant
                            du filtre Opacité du clip (l'Opacité devient explicite) ; le tracé
                            est un blob binaire (points normalisés [0..1], 3 points par sommet,
                            tangentes absolues) ; tracé animé : 1re clé (comme l'export .aep) ;
  - l'effet Gaussian Blur → le filtre `AE.Impact_Blur_FX` de Premiere (18 paramètres, intensité
                            dans « Amount ») ; `dimensions`/`repeatEdge` d'EffectCraft ne sont
                            pas repris (avertissement).

Les temps écrits sont ceux du .ecproj. La chaîne Adobe .aep→Premiere décalait les clés de
1 à 4 images (`floor(image × 25/24)`, artefact de timebase) : ce décalage n'est pas reproduit.

Limites (avertissement, non exporté) : sources autres que Footage et Solid (Text/Shape/Camera/
Light/comp imbriquée), plus d'une comp, stretch ≠ 100.

Masques, opacité, fusion et effets — structures relevées sur un projet Premiere 2026 de référence.
Sont exportés :
  - Masques : modes Add, Subtract, Intersect, Difference, ainsi que l'inversion (paramètre 17) ;
  - Opacité : valeur fixe ou animée par keyframes (paramètre 1) ;
  - Modes de fusion : les 27 modes de compositing Premiere (paramètres 2 et 3) ;
  - Solides & Calques d'effet : générateurs Color Matte (COLR, couleur RGB dans
    `ImporterPrefs`) et Black Video (BLAK + IsAdjustmentLayer) ;
  - Effets : Gaussian Blur (AE.Impact_Blur_FX).
Seuls les cas non repris (mode de masque exotique, feather par points, roto-bezier, effet hors table)
bloquent l'export en .prproj. `--force` passe outre.

Usage :
  python3 ecproj_to_prproj.py <projet.ecproj> [-o sortie.prproj] [--path-map SRC=DST] [--verify]
  python3 ecproj_to_prproj.py <dossier> --all [--compare-with REF.prproj]
  python3 ecproj_to_prproj.py            # mode bureau : scan de ROOT + fenêtres zenity

Options :
  -o, --out FICHIER     chemin du .prproj (défaut : à côté du .ecproj, même nom)
  --all                 convertit tous les *.ecproj du dossier donné (récursif)
  --path-map SRC=DST    réécrit le préfixe des chemins de médias (répétable ; défaut :
                        la constante PATH_MAP, /mnt/cache → F:)
  --no-path-map         n'applique aucune réécriture (chemins Linux écrits tels quels)
  --skip-missing        n'affiche qu'un décompte des médias absents (pas la liste)
  --force               exporte même si des éléments ne sont pas repris (masque non « Add »,
                        inversé, effet inconnu) — le .prproj sera incomplet
  --verify              relit le .prproj produit et affiche un résumé (lecture seule)
  --compare-with REF    compare le résultat à un .prproj de référence (diff sémantique)
  --xml-dump            écrit aussi le XML décompressé à côté du .prproj (débogage)
  --no-gui              force la sortie texte (mode bureau sans fenêtre)
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime

# ------------------------------------------------------------------- réglages

# Mode bureau (double-clic, sans argument) : les seuls réglages à modifier.
ROOT = "/mnt/cache"                       # racine scannée (dossiers projet = ses enfants directs)
NAME_FILTERS = ("camille",)               # sous-chaînes de nom de dossier — tuple : la virgule compte
PATH_MAP = [("/mnt/cache", "F:")]         # réécriture des chemins de médias pour Windows ; [] = inchangés
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prproj_export.log")

TICKS_PER_SECOND = 254_016_000_000

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ecproj_to_aep as conv  # helpers partagés avec le convertisseur .aep
except Exception as exc:  # pragma: no cover - dépend de l'environnement
    print(f"ecproj_to_aep.py introuvable ou invalide (doit être dans le même dossier) : {exc}",
          file=sys.stderr)
    sys.exit(2)

# ------------------------- constantes du format .prproj (fichier Premiere 2026)

CLASS_IDS: dict[str, tuple[str, str]] = {
    "Project": ("62ad66dd-0dcd-42da-a660-6d8fbde94876", "45"),
    "RootProjectItem": ("1c307a89-9318-47d7-a583-bf2553736543", "1"),
    "ClipProjectItem": ("cb4e0ed7-aca1-4171-8525-e3658dec06dd", "1"),
    "ProjectSettings": ("50c16708-a1a1-4d2f-98d5-4e283ae28353", "21"),
    "CompileSettings": ("18a35d66-597e-4157-b783-938b5bec3547", "4"),
    "ScratchDiskSettings": ("4c6ed82b-a81c-4df1-8bd0-750504c4b560", "4"),
    "IngestSettings": ("2db8f76b-2c37-48ee-925d-9a4f7278152d", "2"),
    "WorkspaceSettings": ("c4372273-e1aa-4683-98aa-a2ceadf3066c", "1"),
    "VideoSettings": ("58474264-30c4-43a2-bba5-dc0812df8a3a", "10"),
    "AudioSettings": ("6baf5521-b132-4634-840e-13cec5bc86a4", "8"),
    "VideoCompileSettings": ("db372db5-7de2-4d3c-98ae-f42659d77b22", "9"),
    "AudioCompileSettings": ("34b10007-ab6d-49a7-bac5-7b60d919e387", "6"),
    "DummyCaptureSettings": ("328c2aa2-47f9-4211-805b-b6a6dbd4ca29", "1"),
    "DefaultSequenceSettings": ("567bdf53-d6d9-4d61-b2f1-f4834bebea9b", "2"),
    "MasterClip": ("fb11c33a-b0a9-4465-aa94-b6d5db2628cf", "12"),
    "ClipLoggingInfo": ("77ab7fdd-dcdf-465d-9906-7a330ca1e738", "10"),
    "AudioComponentChain": ("3cb131d1-d3c0-47ae-a19a-bdf75ea11674", "4"),
    "VideoComponentChain": ("0970e08a-f58f-4108-b29a-1a717b8e12e2", "3"),
    "VideoClip": ("9308dbef-2440-4acb-9ab2-953b9a4e82ec", "11"),
    "AudioClip": ("b8830d03-de02-41ee-84ec-fe566dc70cd9", "8"),
    "ClipChannelGroupVectorSerializer": ("a3127a8c-95d4-456e-a7f5-171b3f922426", "1"),
    "VideoMediaSource": ("e64ddf74-8fac-4682-8aa8-0e0ca2248949", "2"),
    "AudioMediaSource": ("f588da05-fc2a-4fbc-9383-74d653b379e3", "2"),
    "SecondaryContent": ("f9d004b5-cb04-4e2f-af6f-64fadc2c4be9", "1"),
    "ClipChannelVectorSerializer": ("333d203b-3a53-4195-8894-fc7523ff3dc7", "1"),
    "AudioSequenceSource": ("e8d4cc83-38cb-491f-9d94-e5f7e3b205ee", "7"),
    "VideoSequenceSource": ("4752dfa9-7a7e-4a3b-a25b-cafde1a8d036", "3"),
    "Media": ("7a5c103e-f3ac-4391-b6b4-7cc3d2f9a7ff", "30"),
    "ClipChannelSerializer": ("5c89aa7a-89a6-4483-becd-f2b1def42316", "1"),
    "Sequence": ("6a15d903-8739-11d5-af2d-9b7855ad8974", "12"),
    "AudioStream": ("0b5cf52f-2b85-4863-890b-8844b64ecfe9", "8"),
    "VideoStream": ("a36e4719-3ec6-4a0c-ab11-8b4aab377aa5", "22"),
    "VideoTrackGroup": ("9e9abf7a-0918-49c2-91ae-991b5dde77bb", "13"),
    "AudioTrackGroup": ("9b9238b9-53a8-4cc3-b03f-b36246d052e6", "6"),
    "DataTrackGroup": ("b714b71d-6838-48dd-9b77-db19088ced7e", "1"),
    "VideoClipTrack": ("f68dcd81-8805-11d5-af2d-9bfa89d4ddd4", "1"),
    "AudioClipTrack": ("097f6203-99ae-11d5-84f2-8cf14bde7040", "7"),
    "AudioMixTrack": ("4b1d8400-e89e-11d5-abc4-a1a13b1e80a0", "4"),
    "VideoClipTrackItem": ("368b0406-29e3-4923-9fcd-094fbf9a1089", "8"),
    "AudioClipTrackItem": ("064ec682-9ba6-11d5-af2d-9ca32c7d6164", "11"),
    "StereoToStereoPanProcessor": ("7bf86a01-efbe-11d5-abc4-c1ce2b1e9090", "1"),
    "DefaultPanProcessor": ("33a94282-ee2c-11d5-abc4-c1cd7f9e3c10", "2"),
    "AudioTrackInlet": ("be3af080-e8c6-11d5-abc4-a1c6d5dee670", "4"),
    "SubClip": ("e0c58dc9-dbdd-4166-aef7-5db7e3f22e84", "6"),
    "AudioFader": ("1a38c583-ed5c-11d5-abc4-c1cbf61ec590", "3"),
    "AudioMeter": ("72ea4700-f615-11d5-abc4-c186585e63e0", "2"),
    "VideoFilterComponent": ("d10da199-beea-4dd1-b941-ed3a78766d50", "9"),
    "AudioFilterComponent": ("d77a90a0-6c9e-44bf-9b20-de8c21168fe1", "4"),
    "PointComponentParam": ("ca81d347-309b-44d2-acc7-1c572efb973c", "4"),
    "VideoComponentParam": ("fe47129e-6c94-4fc0-95d5-c056a517aaf3", "10"),
    "AudioComponentParam": ("a714635e-a628-4b27-9d59-77eba47dbc1a", "10"),
    "TimeRemapping": ("ace5148e-9c9b-40ed-9f82-64cb67308464", "2"),
    "TimeComponentParam": ("278ae1f9-ab7b-4dff-a53c-21029a399a9d", "9"),
}
CLASS_UNIFORM_PARAM = ("cc12343e-f113-4d3b-ae05-b287db77d461", "10")
CLASS_ALT_PARAM = ("a4ff2d6e-7ac2-44f8-9d52-17d9ca50e542", "10")
CLASS_MUTE_PARAM = ("32657501-3aa4-445f-a49b-d09ecb9fa1ae", "10")
CLASS_VOLUME_PARAM = ("a714635e-a628-4b27-9d59-77eba47dbc1a", "10")
CLASS_ENUM_PARAM = ("6e02e8bb-2569-46b2-8ab1-4ab11c43e9c8", "10")
CLASS_ARB_PARAM = ("313e54d4-6903-49ad-b0bf-8262cdd10f4e", "3")
# Sens de lecture du paramètre « Speed » du Time Remapping (25_time_remap) : valeur
# statique sentinelle, puis clés « temps_ticks,secondes_source,6,0,0,0,0,0 ».
REMAP_START_KEYFRAME = "-91445760000000000,0.000000000000000000000000,0,0,0,0,0,0"

GUID_MEDIA_VIDEO = "228cda18-3625-4d2d-951e-348879e4ed93"
GUID_MEDIA_AUDIO = "80b8e3d5-6dca-4195-aefb-cb5f407ab009"
GUID_MEDIA_DATA = "d8143ffe-eec4-4d2a-a909-d5f7bf094dc5"
GUID_IMPLEMENTATION = "bf7c0e58-488e-4211-9dad-8a71d808748f"
GUID_GENERATOR_IMPLEMENTATION = "42008e7a-de6f-4270-96de-7e287abb9b4b"
TOKEN_BLACK_VIDEO = "1112293707"
TOKEN_COLOR_MATTE = "1129270354"
GUID_EDITING_MODE = "5125b1eb-7925-433f-beea-f5d884227812"

PY_PREMIERE_SEARCH_ROOTS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                 "Media_Solution", "Scripts", "py-premiere-0.1.0", "src"),
    os.path.join(os.path.expanduser("~"), "Media_Solution", "Scripts", "py-premiere-0.1.0", "src"),
]


def load_py_premiere(explicit: str | None = None):
    """Charge dynamiquement la bibliothèque py_premiere si demandée ou disponible."""
    if explicit:
        sys.path.insert(0, explicit)
    for root in PY_PREMIERE_SEARCH_ROOTS:
        if os.path.isdir(root) and root not in sys.path:
            sys.path.insert(0, root)
    try:
        import py_premiere
        return py_premiere
    except ImportError:
        return None

SENTINEL = "-91445760000000000"
AUDIO_LAYOUT = '[{"channellabel":100},{"channellabel":101}]'
COLOR_SPACE = '{"baseColorProfile":{"colorProfileName":"BT.709 RGB Full"},"baseProfileType":1}'
IMMERSIVE = ('{"ambisonicsHRIR":"","ambisonicsMonitoringType":0,"capturedHorizontalView":360,'
             '"capturedVerticalView":180,"fieldOfHorizontalView":108,"fieldOfVerticalView":108,'
             '"projectionType":0,"stereoscopicEye":0,"stereoscopicType":0,"version":3}')
TONEMAP = '{"peak":-1,"version":3}'
INFLUENCE = "0.16666666666666666"
STILL_DURATION_TICKS = 10973491200000000   # durée « infinie » d'un média image : 12 h (comme Adobe)
STILL_CLIP_TICKS = 1524096000000           # plage nominale du clip image dans le masterclip (6 s)
# Générateurs synthétiques (Color Matte / Black Video) : mêmes conventions qu'Adobe.
STILL_CODEC_TYPE = "1380013856"            # CodecType « RAW » d'un flux still (Premiere 2026)
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
NO_CONFORMED_AUDIO_RATE = "9223372036854775807"  # i64::MAX, média généré sans audio
COLOR_MATTE_MODIFICATION_STATE = "GHEjmi0XPID7m/zIAAAAQA=="
COLOR_MATTE_MODIFICATION_STATE_HASH = "e36a7dbe-b1a9-af63-b399-e5190000001c"
BLACK_VIDEO_MODIFICATION_STATE = "9G7cYo7OaZ9ewoS+AAAAQA=="
BLACK_VIDEO_MODIFICATION_STATE_HASH = "a4f9f615-16ac-e2c1-b049-f6b00000001c"
GENERATOR_SOURCE_IN_TICKS = 3600 * TICKS_PER_SECOND  # les placements Adobe commencent à 1 h dans la source 12 h
GENERATOR_TEMPLATE_CLIP_TICKS = 5 * TICKS_PER_SECOND  # template du masterclip Adobe : 0–5 s
DEFAULT_IS_DROP_FRAME = "false"            # 25/30 fps : jamais drop-frame (valeur des mattes Adobe)

# --- masques et effets (structures relevées sur un projet Premiere 2026 de référence) ----------
# Un masque est un filtre `AE.ADBE AEMask2` (27 paramètres) écrit en sous-composant du filtre
# Opacité du clip ; son tracé est un blob binaire base64 (paramètre « Path »). Le « Gaussian
# Blur » de Premiere est le filtre `AE.Impact_Blur_FX` (18 paramètres), composant de premier
# niveau de la chaîne (comme Motion).
MASK2_MATCH_NAME = "AE.ADBE AEMask2"
MASK2_DISPLAY_NAME = "Mask2"
MASK_PATH_TAG = b"Vy"
# Données privées du filtre : constantes pour toutes les instances de masque du projet de
# référence (relevées telles quelles ; Premiere les recalcule à l'enregistrement).
MASK2_PRIVATE_DATA = "a2NpbgEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIDl+f//////AQAAAAAAAACA5fn//////wEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
MASK_TRACKER_DATA = "AQAAAAAAgD8AAAAAAAAAAAAAAAAAAIA/AAAAAAAAAAAAAAAAAACAPwAAgD8AAAAAAAAAAAAAAAAAAIA/AAAAAAAAAAAAAAAAAACAPwAAAAAAAAAAAAAAAAAAAAAAAIA/AACAPwAAAAA="
MASK_TRACKER24_DATA = "DAAAAAgADAAEAAgACAAAABgAAABMAAAAEAAMAAAAAAAAAAcAAAAIABAAAAAAAAAABAAAACQAAABiZWI3MTgwNi0yZjAzLTQ0NTgtOWFhMy0xZmMxNTAwNGYyYWQAAAAAAAAAAA=="
MASK_INTERACTIONS_DATA = "AA=="
# Tracé vide (masque sans points) : conteneur « 2, 0 tracé » observé sur l'échantillon.
MASK_EMPTY_PATH = "AgAAAAAAAAA="
# Hashes Adobe connus des payloads constants ci-dessus (mêmes octets → même hash dans
# tous les projets Premiere) : ils sont réutilisés tels quels.
MASK_KNOWN_HASHES = {
    MASK2_PRIVATE_DATA: "801239b7-73d1-ddc5-2fb7-1cc30000007c",
    MASK_TRACKER_DATA: "11d96c46-af5b-7d09-d31f-ff4d00000074",
    MASK_TRACKER24_DATA: "f00faa56-7d8f-97ac-729c-312c00000070",
    MASK_INTERACTIONS_DATA: "c40c6399-6b26-8c2c-feaf-d01b0000000d",
    MASK_EMPTY_PATH: "5b3d1e1b-2b68-8c23-e1d2-827a00000014",
}
# Paramètre « Type » du masque = mode (0 = None, 1 = Difference, 2 = Add, 3 = Subtract, 4 = Intersect).
MASK_TYPE_ENUM: dict[str, int] = {
    "None": 0,
    "Difference": 1,
    "Add": 2,
    "Subtract": 3,
    "Intersect": 4,
}

# Mapping des 38 modes de fusion EffectCraft vers les jumeaux Premiere Pro (AE.ADBE Opacity) :
# ParameterID 2 (API/UXP 0..27) et ParameterID 3 (GUI 0..31).
BLEND_MODE_MAP: dict[str, tuple[int, int]] = {
    "Normal": (18, 0),
    "Dissolve": (6, 1),
    "DancingDissolve": (6, 1),
    "Darken": (3, 3),
    "Multiply": (17, 4),
    "ColorBurn": (1, 5),
    "ClassicColorBurn": (1, 5),
    "LinearBurn": (13, 6),
    "DarkerColor": (4, 7),
    "Lighten": (11, 9),
    "Screen": (22, 10),
    "ColorDodge": (2, 11),
    "ClassicColorDodge": (2, 11),
    "LinearDodge": (14, 12),
    "Add": (14, 12),
    "LighterColor": (12, 13),
    "Overlay": (19, 15),
    "SoftLight": (23, 16),
    "HardLight": (8, 17),
    "VividLight": (24, 18),
    "LinearLight": (15, 19),
    "PinLight": (20, 20),
    "HardMix": (9, 21),
    "Difference": (5, 23),
    "ClassicDifference": (5, 23),
    "Exclusion": (7, 24),
    "Subtract": (25, 25),
    "Divide": (26, 26),
    "Hue": (10, 28),
    "Saturation": (21, 29),
    "Color": (0, 30),
    "Luminosity": (16, 31),
}
PP_BLUR_MATCH_NAME = "AE.Impact_Blur_FX"
PP_BLUR_DISPLAY_NAME = "Gaussian Blur"
PP_BLUR_EFFECTS = ("ec.blur.gaussian",)


# -------------------------------------------------------------- mini-DOM XML


def esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_num(value) -> str:
    """Format Adobe : « 207. », « 0.5 », « -0.034987815221150714 » (entier suffixé d'un point)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    f = float(value)
    rounded = round(f)
    if abs(f - rounded) < 1e-9 and abs(rounded) < 1e15:
        if rounded == 0:
            return "-0" if str(f).startswith("-") else "0"
        return f"{int(rounded)}."
    return repr(f)


def fmt_scalar(value) -> str:
    """Comme fmt_num mais zéro = « 0. » (forme des valeurs scalaires Adobe)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    f = float(value)
    rounded = round(f)
    if abs(f - rounded) < 1e-9 and abs(rounded) < 1e15:
        if rounded == 0:
            return "-0." if str(f).startswith("-") else "0."
        return f"{int(rounded)}."
    return repr(f)


class E:
    """Élément XML structurel (un attribut peut porter une référence à un Obj)."""

    __slots__ = ("name", "attrs", "children", "text")

    def __init__(self, name: str, attrs: dict | None = None, children: list | None = None,
                 text: str | None = None):
        self.name = name
        self.attrs = attrs or {}
        self.children = children
        self.text = text


class Obj:
    """Objet du graphe .prproj : ObjectID (ou ObjectUID) + ClassID + Version + champs."""

    __slots__ = ("tag", "classid", "version", "uid", "id", "fields")

    def __init__(self, tag: str, uid: bool = False, classid_version: tuple[str, str] | None = None):
        cid, ver = classid_version or CLASS_IDS[tag]
        self.tag = tag
        self.classid = cid
        self.version = ver
        self.uid = str(uuid.uuid4()) if uid else None
        self.id: int | None = None
        self.fields: list[tuple] = []

    def txt(self, tag: str, value) -> "Obj":
        self.fields.append(("t", tag, str(value)))
        return self

    def ref(self, tag: str, target: "Obj") -> "Obj":
        self.fields.append(("r", tag, target))
        return self

    def el(self, element: E) -> "Obj":
        self.fields.append(("x", element))
        return self


def _emit_e(e: E, depth: int) -> list[str]:
    ind = "\t" * depth
    attrs = []
    has_ref = False
    for key, value in e.attrs.items():
        if isinstance(value, Obj):
            ref_key = "ObjectURef" if value.uid else "ObjectRef"
            ref_val = value.uid if value.uid else value.id
            attrs.append(f'{ref_key}="{ref_val}"')
            has_ref = True
        else:
            attrs.append(f'{key}="{esc(value)}"')
    head = f"{ind}<{e.name}" + (" " + " ".join(attrs) if attrs else "")
    if has_ref and e.children is None and e.text is None:
        return [head + "/>"]
    if e.text is not None:
        return [f"{head}>{esc(e.text)}</{e.name}>"]
    lines = [head + ">"]
    for child in e.children or []:
        lines.extend(_emit_e(child, depth + 1))
    lines.append(f"{ind}</{e.name}>")
    return lines


def _emit_obj(o: Obj, depth: int) -> list[str]:
    ind = "\t" * depth
    ind_child = "\t" * (depth + 1)
    if o.uid is not None:
        head = f'{ind}<{o.tag} ObjectUID="{o.uid}" ClassID="{o.classid}" Version="{o.version}">'
    else:
        head = f'{ind}<{o.tag} ObjectID="{o.id}" ClassID="{o.classid}" Version="{o.version}">'
    lines = [head]
    for field in o.fields:
        kind = field[0]
        if kind == "t":
            lines.append(f'{ind_child}<{field[1]}>{esc(field[2])}</{field[1]}>')
        elif kind == "r":
            tag, target = field[1], field[2]
            if target.uid is not None:
                lines.append(f'{ind_child}<{tag} ObjectURef="{target.uid}"/>')
            else:
                lines.append(f'{ind_child}<{tag} ObjectRef="{target.id}"/>')
        else:
            lines.extend(_emit_e(field[1], depth + 1))
    lines.append(f"{ind}</{o.tag}>")
    return lines


def serialize(document: list) -> str:
    counter = 0
    for entry in document:
        if isinstance(entry, Obj) and entry.uid is None:
            counter += 1
            entry.id = counter
    lines = ['<?xml version="1.0" encoding="UTF-8" ?>', '<PremiereData Version="3">']
    for entry in document:
        if isinstance(entry, Obj):
            lines.extend(_emit_obj(entry, 1))
        else:
            lines.extend(_emit_e(entry, 1))
    lines.append("</PremiereData>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- valeurs du .ecproj


def ticks_per_frame(frame_rate) -> int:
    """Durée d'une image en ticks (les champs FrameRate du .prproj sont des ticks/image)."""
    if isinstance(frame_rate, dict):
        num = int(frame_rate.get("num") or 25)
        den = int(frame_rate.get("den") or 1)
    else:
        from fractions import Fraction
        frac = Fraction(float(frame_rate or 25)).limit_denominator(1000)
        num, den = frac.numerator, frac.denominator
    return int(round(TICKS_PER_SECOND * den / num)) if num else int(TICKS_PER_SECOND / 25)


def pixel_aspect_str(value) -> str:
    try:
        pa = float(value if value is not None else 1.0)
    except (TypeError, ValueError):
        pa = 1.0
    if abs(pa - 1.0) < 1e-9:
        return "1,1"
    from fractions import Fraction
    frac = Fraction(pa).limit_denominator(1000)
    return f"{frac.numerator},{frac.denominator}"


def solid_rgb(color) -> tuple[int, int, int]:
    """Couleur d'un solide .ecproj (0..1, éventuellement 0..255) → octets RGB."""
    values = list(color or [0.0, 0.0, 0.0])[:3]
    while len(values) < 3:
        values.append(0.0)
    out: list[int] = []
    for value in values:
        try:
            channel = float(value)
        except (TypeError, ValueError):
            channel = 0.0
        if channel > 1.5:
            channel /= 255.0
        out.append(max(0, min(255, int(round(channel * 255.0)))))
    return out[0], out[1], out[2]


def audio_sample_type(path: str, codec) -> str:
    """Code `AudioStream/SampleType` d'Adobe, d'après le conteneur et le codec.

    Relevés sur les projets Premiere de référence : WAV PCM16 = 3, AIFF PCM16 = 11,
    MOV/MXF PCM = 4, AAC/MP3 = 7. Le code décrit le format **sur disque**
    (16/24 bits, flottant, compressé), pas le format interne de Premiere.
    """
    ext = os.path.splitext(path or "")[1].lower()
    if ext in (".wav", ".wave"):
        return "3"
    if ext in (".aif", ".aiff", ".aifc"):
        return "11"
    if ext == ".mxf":
        return "4"
    if ext == ".mov":
        # Un .mov ProRes / DNx / non compressé porte du PCM ; un .mov H.264
        # (téléphone, écran) porte presque toujours de l'AAC -> 7.
        name = str(codec or "").lower()
        if any(word in name for word in
               ("prores", "dnx", "uncompressed", "raw", "pcm", "xdcam", "422", "444")):
            return "4"
        return "7"
    return "7"


def node_value(node: dict | None):
    if not node:
        return None
    return conv.prop_value(node.get("value"))


def pair_from(value, fallback=(0.0, 0.0)) -> tuple[float, float]:
    if isinstance(value, list) and len(value) >= 2:
        return float(value[0]), float(value[1])
    if value is not None:
        return float(value), float(fallback[1])
    return float(fallback[0]), float(fallback[1])


def node_pair(node: dict | None, fallback=(0.0, 0.0)) -> tuple[float, float]:
    return pair_from(node_value(node), fallback)


def key_pair(key: dict, fallback=(0.0, 0.0)) -> tuple[float, float]:
    return pair_from(conv.prop_value((key or {}).get("value")), fallback)


def node_keys(node: dict | None) -> list[dict]:
    return (node or {}).get("keys") or []


def scalar_of(value, default=0.0) -> float:
    if isinstance(value, list):
        return float(value[0]) if value else default
    if value is None:
        return default
    return float(value)


def keys_are_constant(node: dict | None) -> bool:
    keys = node_keys(node)
    if len(keys) <= 1:
        return True
    values = []
    for key in keys:
        value = conv.prop_value(key.get("value"))
        values.append(tuple(value) if isinstance(value, list) else value)
    return len(set(values)) == 1


# ------------------------------------------------- encodage des paramètres


def point_start(x_norm, y_norm) -> str:
    return (f"{SENTINEL},{fmt_num(x_norm)}:{fmt_num(y_norm)},"
            "0,0,0,0,0,0,5,4,0,0,0,0")


def point_row(time_ticks, x_norm, y_norm, tail, spatial) -> str:
    return (f"{int(time_ticks)},{fmt_num(x_norm)}:{fmt_num(y_norm)},"
            + ",".join(tail) + ",5,4," + ",".join(spatial))


def scalar_start(value) -> str:
    return f"{SENTINEL},{fmt_scalar(value)},0,0,0,0,0,0"


def scalar_row(time_ticks, value, tail) -> str:
    return f"{int(time_ticks)},{fmt_scalar(value)}," + ",".join(tail)


def is_linear_key(key: dict) -> bool:
    return (str(key.get("in_interp") or "Linear").lower() == "linear"
            and str(key.get("out_interp") or "Linear").lower() == "linear")


def temporal_tail(key: dict, in_speed: float, out_speed: float) -> list[str]:
    """Gabarit relevé sur Adobe : clé linéaire → vitesses (u/s) + influence 1/6 ;
    clé Bézier (ease 0 / 33 %) → « 5,0,0,0,0,0 » (courbe lissée native)."""
    if is_linear_key(key):
        return ["0", "0", fmt_num(in_speed), INFLUENCE, fmt_num(out_speed), INFLUENCE]
    return ["5", "0", "0", "0", "0", "0"]


def point_keyframes(node: dict, cw: float, ch: float, offset=(0.0, 0.0)):
    """(StartKeyframe, Keyframes) d'un paramètre Point (Position/Anchor)."""
    keys = node_keys(node)
    dx, dy = offset
    points = []
    for key in keys:
        x, y = key_pair(key)
        points.append((x + dx, y + dy))
    if not points:
        x, y = node_pair(node)
        return point_start((x + dx) / cw, (y + dy) / ch), None
    if keys_are_constant(node):
        x, y = points[0]
        return point_start(x / cw, y / ch), None

    def norm(pt):
        return (pt[0] / cw, pt[1] / ch)

    times = [int(k.get("time") or 0) for k in keys]
    speeds_in, speeds_out = [0.0] * len(points), [0.0] * len(points)
    for i in range(1, len(points)):
        dt = conv.ticks_to_seconds(times[i] - times[i - 1])
        if dt > 0:
            (px, py), (qx, qy) = norm(points[i - 1]), norm(points[i])
            speeds_in[i] = ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5 / dt
    for i in range(len(points) - 1):
        dt = conv.ticks_to_seconds(times[i + 1] - times[i])
        if dt > 0:
            (px, py), (qx, qy) = norm(points[i]), norm(points[i + 1])
            speeds_out[i] = ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5 / dt

    rows = []
    for i, key in enumerate(keys):
        nx, ny = norm(points[i])
        tail = temporal_tail(key, speeds_in[i], speeds_out[i])
        if bool(key.get("spatial_auto", True)):
            if 0 < i < len(points) - 1:
                delta = (norm(points[i + 1])[0] - norm(points[i - 1])[0],
                         norm(points[i + 1])[1] - norm(points[i - 1])[1])
            elif i == 0:
                delta = (norm(points[1])[0] - norm(points[0])[0],
                         norm(points[1])[1] - norm(points[0])[1])
            else:
                delta = (norm(points[-1])[0] - norm(points[-2])[0],
                         norm(points[-1])[1] - norm(points[-2])[1])
            tin = (0.0, 0.0) if i == 0 else (-delta[0] / 6.0, -delta[1] / 6.0)
            tout = (0.0, 0.0) if i == len(points) - 1 else (delta[0] / 6.0, delta[1] / 6.0)
        else:
            si = key.get("spatial_in") or [0.0, 0.0, 0.0]
            so = key.get("spatial_out") or [0.0, 0.0, 0.0]
            tin = (float(si[0]) / cw, float(si[1]) / ch)
            tout = (float(so[0]) / cw, float(so[1]) / ch)
        rows.append(point_row(times[i], nx, ny, tail,
                              [fmt_num(tin[0]), fmt_num(tin[1]),
                               fmt_num(tout[0]), fmt_num(tout[1])]))
    x0, y0 = points[0]
    return point_start(x0 / cw, y0 / ch), ";".join(rows) + ";"


def scalar_keyframes(node: dict):
    """(StartKeyframe, Keyframes) d'un paramètre scalaire (Échelle, Rotation)."""
    keys = node_keys(node)
    if not keys or keys_are_constant(node):
        value = node_value(node) if not keys else conv.prop_value(keys[0].get("value"))
        return scalar_start(scalar_of(value)), None

    def val(key):
        return scalar_of(conv.prop_value(key.get("value")))

    times = [int(k.get("time") or 0) for k in keys]
    speeds_in, speeds_out = [0.0] * len(keys), [0.0] * len(keys)
    for i in range(1, len(keys)):
        dt = conv.ticks_to_seconds(times[i] - times[i - 1])
        if dt > 0:
            speeds_in[i] = abs(val(keys[i]) - val(keys[i - 1])) / dt
    for i in range(len(keys) - 1):
        dt = conv.ticks_to_seconds(times[i + 1] - times[i])
        if dt > 0:
            speeds_out[i] = abs(val(keys[i + 1]) - val(keys[i])) / dt
    rows = [scalar_row(times[i], val(key), temporal_tail(key, speeds_in[i], speeds_out[i]))
            for i, key in enumerate(keys)]
    return scalar_start(val(keys[0])), ";".join(rows) + ";"


def format_remap_value(value: float) -> str:
    """Valeur de la courbe Speed : secondes source à 24 décimales (format Adobe)."""
    return f"{float(value):.24f}"


def format_remap_bound(value: float) -> str:
    """Borne du paramètre Speed : entier nu, ou double complet si fractionnaire."""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def build_time_remap(entry: dict, warn) -> dict | None:
    """Objets TimeRemapping/Speed d'un clip, d'après le nœud `timeRemap` EffectCraft.

    Un Freeze Frame EffectCraft (une seule clé Hold, valeur constante sur tout le calque)
    est écrit comme **Frame Hold** natif de Premiere (`FrameHold` + `FrameHoldStart` sur
    le `VideoClip`), la méthode validée dans Premiere 2026 : le clip garde sa durée et
    toutes les images source pointent vers l'image gelée. Ce n'est pas un Time Remapping
    (celui-ci, combiné au masque de V3, rendait le calque invisible).

    Pour une vraie courbe de vitesse à plusieurs clés, on écrit des objets
    `TimeRemapping`/`Speed` : les temps EC (temps de comp, comme dans AE) sont ramenés au
    temps clip-relatif (`clé - in_point`) et les valeurs restent des secondes source.
    Premiere interpole cette courbe en mode TIME (6) : les paliers Hold ne sont pas
    reproduits et sont signalés.

    Retour : `{"kind": "hold", "start": ticks, ...}` pour un gel, ou
    `{"kind": "remap", "param": Obj, "remap": Obj, ...}` pour une courbe.
    """
    layer = entry["layer"]
    node = conv.find_layer_property(layer, "timeRemap")
    if node is None:
        return None
    bundle = entry["bundle"]
    name = str(layer.get("name") or bundle["name"])
    if not bundle.get("has_video") or bundle.get("is_still"):
        warn(f"calque « {name} » : time remap ignoré (source sans durée)")
        return None
    upper_ticks = int(bundle.get("duration") or 0)
    span = int(entry["end"] - entry["start"])
    if upper_ticks <= 0 or span <= 0:
        warn(f"calque « {name} » : time remap ignoré (durée source/clip nulle)")
        return None
    upper = upper_ticks / TICKS_PER_SECOND

    keys = sorted(node.get("keys") or [], key=lambda key: int(key.get("time") or 0))
    keys = [key for key in keys if conv.prop_value(key.get("value")) is not None]
    points: list[tuple[int, float]] = []
    if len(keys) <= 1:
        raw_value = (keys[0] if keys else node).get("value")
        value = conv.prop_value(raw_value)
        points = [(0, float(value if value is not None else 0.0)), (span, float(value if value is not None else 0.0))]
    else:
        base = int(entry["start"])  # temps de comp → temps clip-relatif
        for key in keys:
            tick = int(key.get("time") or 0) - base
            value = float(conv.prop_value(key.get("value")))
            if tick < 0:
                warn(f"calque « {name} » : clé de time remap avant le début du clip, ramenée à 0")
                tick = 0
            points.append((tick, value))
        if any(not is_linear_key(key) for key in keys):
            warn(f"calque « {name} » : time remap à plusieurs clés non linéaires — "
                 f"courbe Premiere linéaire (les paliers Hold ne sont pas reproduits)")

    points.sort()
    deduped: list[tuple[int, float]] = []
    for tick, value in points:
        if deduped and tick == deduped[-1][0]:
            warn(f"calque « {name} » : deux clés de time remap au même temps, la seconde est ignorée")
            continue
        deduped.append((tick, value))
    points = deduped

    clamped: list[tuple[int, float]] = []
    for tick, value in points:
        if value < 0.0:
            warn(f"calque « {name} » : valeur de time remap négative, ramenée à 0")
            value = 0.0
        elif value > upper:
            warn(f"calque « {name} » : valeur de time remap au-delà de la source, ramenée à {upper:.3f} s")
            value = upper
        clamped.append((tick, value))
    points = clamped
    if not points:
        warn(f"calque « {name} » : time remap sans clé exploitable, ignoré")
        return None

    # Valeur constante sur tout le clip = gel (Freeze Frame EffectCraft). On l'écrit en
    # Frame Hold natif, pas en Time Remapping : c'est la forme validée dans Premiere.
    if all(abs(value - points[0][1]) <= 1e-9 for _, value in points):
        return {"kind": "hold", "start": int(round(points[0][1] * TICKS_PER_SECOND)),
                "param": None, "remap": None, "keys": points}

    # Premiere complète une courbe inachevée par une clé à la fin de la source.
    last_tick, last_value = points[-1]
    if last_value < upper - 1e-9:
        terminal = last_tick + int(round((upper - last_value) * TICKS_PER_SECOND))
        points.append((terminal, upper))

    keyframes = ";".join(
        f"{tick},{format_remap_value(value)},6,0,0,0,0,0"
        for tick, value in points
    ) + ";"
    param = Obj("TimeComponentParam")
    param.txt("Name", "Speed")
    param.txt("IsTimeVarying", "true")
    param.txt("IsLocked", "false")
    param.txt("DiscontinuousInterpolate", "false")
    param.txt("LowerBound", "0")
    param.txt("UpperBound", format_remap_bound(upper))
    param.txt("StartKeyframe", REMAP_START_KEYFRAME)
    param.txt("Keyframes", keyframes)
    param.txt("CurrentValue", "0")
    param.txt("ParameterID", "-1")
    remap = Obj("TimeRemapping")
    remap.ref("Keyframes", param)
    return {"kind": "remap", "start": None, "param": param, "remap": remap,
            "keys": points}


# -------------------------------------------------- tracés de masque (blob Premiere)


def mask_children(mask: dict) -> dict[str, dict]:
    """Enfants d'un masque .ecproj indexés par `match` (path, feather, opacity, expansion)."""
    return {child.get("match"): child for child in mask.get("children") or []}


def mask_path_value(node: dict | None) -> dict:
    """Valeur du tracé d'un nœud Mask Path (1re clé si le tracé est animé, comme le .aep)."""
    if node is None:
        return {}
    keys = node_keys(node)
    if keys:
        return (keys[0].get("value") or {}).get("v") or {}
    return (node.get("value") or {}).get("v") or {}


def mask_path_is_closed(mask: dict) -> bool:
    """Vrai si le tracé du masque est fermé."""
    return bool(mask_path_value(mask_children(mask).get("path")).get("closed", False))


def mask_is_applied(mask: dict) -> bool:
    """Vrai si EffectCraft applique ce masque au rendu (même règle que le moteur).

    `crates/render/src/masks.rs` ne retient que les masques actifs dont le mode n'est pas
    « None » et dont le tracé est **fermé** : « an open mask makes no transparency: the
    layer shows whole until the path is closed ». Un masque ignoré par EffectCraft ne doit
    pas être écrit dans le .prproj — il découperait le clip alors qu'EffectCraft le laisse
    entier (cas des masques vides non fermés, fréquents dans les .ecproj).
    """
    kind = mask.get("kind") or {}
    return str(kind.get("mode") or "Add") != "None" and mask_path_is_closed(mask)


def pack_mask_path(points, closed: bool) -> str:
    """Blob base64 du paramètre « Path » à partir de points **normalisés** [0..1].

    `points` : un tuple `(x_entrée, y_entrée, x_sommet, y_sommet, x_sortie, y_sortie)` par
    sommet (tangentes en positions absolues). Assemblage : en-tête de 25 octets (version,
    balise « Vy », nombre de sommets), puis 3 points par sommet séparés par un float 0.0,
    et un octet final (1 = tracé fermé, 0 = ouvert) — structure relevée octet par octet sur
    un rectangle à 4 sommets (134 octets, round-trip vérifié).
    """
    count = len(points)
    raw = bytearray(struct.pack("<II", 2, 1))          # version du conteneur, 1 tracé
    raw += MASK_PATH_TAG + b"\x00\x00\x00"             # balise « Vy » + remplissage observé
    raw += struct.pack("<III", 2, count, 0)            # constant, nombre de sommets, constant
    for index, point in enumerate(points):
        raw += struct.pack("<6f", *point)
        if index != count - 1:
            raw += struct.pack("<f", 0.0)
    raw += bytes([1 if closed else 0])
    return base64.b64encode(bytes(raw)).decode("ascii")


def encode_mask_path(vertices, in_tangents, out_tangents, closed, frame_w, frame_h) -> str:
    """Blob « Path » d'un masque (pixels → normalisé [0..1] par la taille de la frame).

    Les tangentes .ecproj sont relatives au sommet ; Premiere stocke les trois points
    (entrée, sommet, sortie) en absolu. Le nombre de sommets est écrit sur 4 octets ;
    au-delà de 255, l'ordre des octets reste à confirmer (masques usuels : moins de
    256 sommets, où toutes les variantes d'encodage coïncident).
    """
    points = []
    for index, (vx, vy) in enumerate(vertices):
        tx, ty = in_tangents[index] if index < len(in_tangents) else (0.0, 0.0)
        ox, oy = out_tangents[index] if index < len(out_tangents) else (0.0, 0.0)
        points.append(((vx + tx) / frame_w, (vy + ty) / frame_h,
                       vx / frame_w, vy / frame_h,
                       (vx + ox) / frame_w, (vy + oy) / frame_h))
    return pack_mask_path(points, bool(closed))


def decode_mask_path(blob: str) -> list[tuple[float, ...]]:
    """Points normalisés d'un blob « Path » (miroir de `pack_mask_path`)."""
    try:
        raw = base64.b64decode(blob, validate=True)
    except Exception:
        return []
    if len(raw) < 26 or raw[:8] != struct.pack("<II", 2, 1):
        return []
    count = struct.unpack_from("<I", raw, 17)[0]
    body = raw[25:-1]
    floats = struct.unpack(f"<{len(body) // 4}f", body)
    points = []
    index = 0
    for position in range(count):
        if index + 6 > len(floats):
            break
        points.append(tuple(floats[index:index + 6]))
        index += 7 if position < count - 1 else 6
    return points


def mask_bounds_center(vertices, frame_w: float, frame_h: float) -> tuple[float, float]:
    """Centre de la bounding-box d'un tracé (Position/Anchor du masque Premiere)."""
    if not vertices:
        return 0.5, 0.5
    xs = [float(x) for x, _ in vertices]
    ys = [float(y) for _, y in vertices]
    return ((min(xs) + max(xs)) / 2.0 / frame_w, (min(ys) + max(ys)) / 2.0 / frame_h)


def mask_feather_node(mask: dict) -> dict | None:
    """Nœud « feather » d'un masque, réduit à un scalaire (max des deux axes, v1)."""
    node = mask_children(mask).get("feather")
    if node is None:
        return None

    def greatest(value):
        vec = conv.prop_value(value)
        if isinstance(vec, list) and vec:
            return max(float(component) for component in vec)
        return float(vec or 0.0)

    reduced = dict(node)
    reduced["value"] = {"t": "Scalar", "v": greatest(node.get("value"))}
    reduced["keys"] = [{**key, "value": {"t": "Scalar", "v": greatest(key.get("value"))}}
                       for key in node_keys(node)]
    return reduced


def make_mask_bool_param(parameter_id: int, control: int | None = None, name: str | None = None,
                         upper: bool = False, value: bool = False) -> Obj:
    """Paramètre booléen d'un masque ou d'un effet (classe « cc12343e »)."""
    param = Obj("VideoComponentParam", classid_version=CLASS_UNIFORM_PARAM)
    if control is not None:
        param.txt("ParameterControlType", str(control))
    param.txt("ParameterID", str(parameter_id))
    if name:
        param.txt("Name", name)
    param.txt("StartKeyframe", f"{SENTINEL},{'true' if value else 'false'},0,0,0,0,0,0")
    if upper:
        param.txt("UpperBound", "false")
    return param


def make_mask_scalar_param(parameter_id: int, name: str | None, node: dict | None, default: float,
                           lower: int, upper: int, lower_ui: int | None = None,
                           upper_ui: int | None = None, control: int | None = None,
                           angle_class: bool = False, current: str | None = None) -> Obj:
    """Paramètre scalaire borné d'un masque ou d'un effet (« a4ff2d6e » / « fe47129e »)."""
    param = Obj("VideoComponentParam",
                classid_version=(CLASS_IDS["VideoComponentParam"] if angle_class
                                 else CLASS_ALT_PARAM))
    if lower_ui is not None:
        param.txt("LowerUIBound", str(lower_ui))
    if upper_ui is not None:
        param.txt("UpperUIBound", str(upper_ui))
    if control is not None:
        param.txt("ParameterControlType", str(control))
    param.txt("ParameterID", str(parameter_id))
    if name:
        param.txt("Name", name)
    if node is not None and node_keys(node) and not keys_are_constant(node):
        start, rows = scalar_keyframes(node)
        param.txt("StartKeyframe", start)
        if rows is not None:
            param.txt("Keyframes", rows)
    else:
        value = scalar_of(node_value(node), default) if node is not None else default
        param.txt("StartKeyframe", scalar_start(value))
    if current is not None:
        param.txt("CurrentValue", current)
    param.txt("LowerBound", str(lower))
    param.txt("UpperBound", str(upper))
    return param


def make_mask_enum_param(parameter_id: int, name: str | None, value: int, lower: int, upper: int,
                         control: int | None = None) -> Obj:
    """Paramètre énuméré d'un masque ou d'un effet (classe « 6e02e8bb »)."""
    param = Obj("VideoComponentParam", classid_version=CLASS_ENUM_PARAM)
    param.txt("DiscontinuousInterpolate", "true")
    if control is not None:
        param.txt("ParameterControlType", str(control))
    param.txt("ParameterID", str(parameter_id))
    if name:
        param.txt("Name", name)
    param.txt("StartKeyframe", f"{SENTINEL},{int(value)},0,0,0,0,0,0")
    param.txt("LowerBound", str(lower))
    param.txt("UpperBound", str(upper))
    return param


def make_mask_point_param(parameter_id: int, name: str, x, y) -> Obj:
    """Paramètre Point d'un masque (Position, Anchor Point, Scale)."""
    param = Obj("PointComponentParam")
    param.txt("ParameterID", str(parameter_id))
    param.txt("Name", name)
    param.txt("StartKeyframe", point_start(x, y))
    return param


def mask_payload_hash(payload: str) -> str:
    """Identifiant `BinaryHash` d'un payload base64 (clé, pas une somme de contrôle).

    Premiere s'en sert de clé pour ne stocker qu'une copie d'un payload (les copies
    suivantes sont vides et portent le même hash) : il n'est ni recalculé ni validé à
    la lecture — des écrivains tiers en créent librement (py_premiere utilise des
    uuid4, et Premiere ouvre ces fichiers). On garde la forme observée chez Adobe
    (identifiant + taille du blob + 12) en la rendant déterministe, et on réutilise
    les hashes Adobe des payloads constants.
    """
    known = MASK_KNOWN_HASHES.get(payload)
    if known:
        return known
    raw = base64.b64decode(payload)
    digest = hashlib.md5(raw).digest()
    return (f"{digest[0:4].hex()}-{digest[4:6].hex()}-{digest[6:8].hex()}-"
            f"{digest[8:10].hex()}-{digest[10:12].hex()}"
            f"{struct.pack('>I', len(raw) + 12).hex()}")


def make_mask_arb_param(parameter_id: int, name: str, payload: str | None = None) -> Obj:
    """Paramètre opaque d'un masque (blob base64 : tracé, tracker, interactions)."""
    param = Obj("ArbVideoComponentParam", classid_version=CLASS_ARB_PARAM)
    param.txt("ParameterControlType", "22")
    param.txt("ParameterID", str(parameter_id))
    param.txt("Name", name)
    param.txt("StartKeyframePosition", SENTINEL)
    attrs = {"Encoding": "base64"}
    if payload:
        attrs["BinaryHash"] = mask_payload_hash(payload)
    param.el(E("StartKeyframeValue", attrs, text=payload))
    return param


def build_mask_component(mask: dict, index: int, source_w: float, source_h: float,
                         warn) -> tuple[Obj, list[Obj]]:
    """Filtre `AE.ADBE AEMask2` (27 paramètres) d'un masque .ecproj.

    Le masque est écrit en sous-composant du filtre Opacité du clip (structure validée
    dans Premiere 2026 : le masque se rend sans conteneur `SelectionComponents`
    supplémentaire). Le tracé est en coordonnées normalisées par la taille **source** du
    clip (les masques vivent dans l'espace du calque, comme dans After Effects :
    1920×1080 pour une source paysage, même si la séquence est en 1080×1920),
    Position/Anchor au centre de sa bounding-box. Un masque sans tracé prend l'état par
    défaut d'Adobe (`Type 0`, Position/Anchor `0:0`) : il ne découpe rien. Un tracé animé
    est écrit à sa première clé (comme l'export .aep) ; les options non reprises (mode ≠
    Add, inversion…) sont refusées en amont par `premiere_skip_reason` et signalées ici
    par un avertissement.
    """
    kind = mask.get("kind") or {}
    children = mask_children(mask)
    name = mask.get("name")
    path_node = children.get("path")
    value = mask_path_value(path_node)
    vertices = [(float(x), float(y)) for x, y in value.get("vertices") or []]
    in_tangents = [(float(x), float(y)) for x, y in (value.get("in_tangents") or [])]
    out_tangents = [(float(x), float(y)) for x, y in (value.get("out_tangents") or [])]
    blob = (encode_mask_path(vertices, in_tangents, out_tangents,
                             bool(value.get("closed", True)), source_w, source_h)
            if vertices else MASK_EMPTY_PATH)
    if value.get("feather"):
        warn(f"masque « {name} » : feather variable (points) non repris (v1)")
    center_x, center_y = (mask_bounds_center(vertices, source_w, source_h) if vertices
                          else (0.0, 0.0))
    mode = str(kind.get("mode") or "Add")
    if mode not in MASK_TYPE_ENUM:
        warn(f"masque « {name} » : mode {mode} non repris (v1), écrit comme Add")
    is_inverted = bool(kind.get("inverted"))
    if path_node is not None and vertices and node_keys(path_node) and any(
            key.get("value") != node_keys(path_node)[0].get("value")
            for key in node_keys(path_node)):
        warn(f"masque « {name} » : tracé animé — seule la 1re clé est écrite (v1)")
    mask_type = MASK_TYPE_ENUM.get(mode, MASK_TYPE_ENUM["Add"]) if vertices else 0

    params = [
        make_mask_bool_param(1, control=11, upper=True),
        make_mask_bool_param(2, control=16),
        make_mask_bool_param(3, control=16),
        make_mask_bool_param(4, control=16),
        make_mask_bool_param(19, control=16),
        make_mask_bool_param(20, control=16),
        make_mask_bool_param(5, control=12, upper=True),
        make_mask_arb_param(6, "Tracker", MASK_TRACKER_DATA),
        make_mask_arb_param(24, "Tracker", MASK_TRACKER24_DATA),
        make_mask_arb_param(7, "Path", blob),
        make_mask_bool_param(8, control=11, name="Transform", upper=True),
        make_mask_point_param(9, "Position", center_x, center_y),
        make_mask_point_param(11, "Scale", 1, 1),
        make_mask_scalar_param(26, "Scale Height", None, 100.0, -5000, 5000,
                               lower_ui=0, upper_ui=200),
        make_mask_scalar_param(27, "Scale Width", None, 100.0, -5000, 5000,
                               lower_ui=0, upper_ui=200),
        make_mask_bool_param(28, name="Uniform Scale"),
        make_mask_scalar_param(12, "Rotation", None, 0.0, -32768, 32767, control=3,
                               angle_class=True),
        make_mask_point_param(10, "Anchor Point", center_x, center_y),
        make_mask_bool_param(13, control=12, upper=True),
        make_mask_scalar_param(14, "Feather", mask_feather_node(mask), 0.0, 0, 5000,
                               upper_ui=300),
        make_mask_scalar_param(15, "Opacity", children.get("opacity"), 100.0, 0, 100),
        make_mask_scalar_param(16, "Expansion", children.get("expansion"), 0.0, -5000, 5000,
                               lower_ui=-300, upper_ui=300),
        make_mask_bool_param(17, value=is_inverted),
        make_mask_scalar_param(25, None, None, 32.0, 0, 192),
        make_mask_enum_param(21, "Blend Mode", 0, 0, 2),
        make_mask_enum_param(22, "Type", mask_type, 0, 4, control=10),
        make_mask_arb_param(23, "User Interactions", MASK_INTERACTIONS_DATA),
    ]
    component_children: list[E] = [
        E("Params", {"Version": "1"}, children=[
            E("Param", {"Index": str(position), "ref": param})
            for position, param in enumerate(params)
        ]),
        E("DisplayName", text=MASK2_DISPLAY_NAME),
        E("InstanceName", text=f"{index + 1:02d}"),
    ]
    filter_obj = Obj("VideoFilterComponent")
    filter_obj.el(E("Component", {"Version": "7"}, children=component_children))
    filter_obj.el(E("PremiereFilterPrivateData",
                    {"Encoding": "base64", "BinaryHash": mask_payload_hash(MASK2_PRIVATE_DATA)},
                    text=MASK2_PRIVATE_DATA))
    filter_obj.txt("MatchName", MASK2_MATCH_NAME)
    filter_obj.txt("VideoFilterType", "2")
    return filter_obj, params


def build_opacity_component(mask_filters: list[Obj] | None = None,
                            opacity_node: dict | None = None,
                            blend_mode: str = "Normal") -> tuple[Obj, list[Obj]]:
    """Filtre Opacité explicite (`AE.ADBE Opacity`) : masques en sous-composants, mode de
    fusion et opacité (statique ou animée). Les masques `AE.ADBE AEMask2` sont écrits ici
    comme le fait Adobe pour un masque de calque (voir build_mask_component).
    """
    mask_filters = list(mask_filters or [])
    api_val, gui_val = BLEND_MODE_MAP.get(blend_mode, (18, 0))

    opacity = Obj("VideoComponentParam")
    opacity.txt("ParameterID", "1")
    opacity.txt("Name", "Opacity")
    if opacity_node is not None and node_keys(opacity_node) and not keys_are_constant(opacity_node):
        start, rows = scalar_keyframes(opacity_node)
        opacity.txt("StartKeyframe", start)
        if rows is not None:
            opacity.txt("Keyframes", rows)
    else:
        op_val = scalar_of(node_value(opacity_node), 100.0) if opacity_node is not None else 100.0
        opacity.txt("StartKeyframe", scalar_start(op_val))
        opacity.txt("CurrentValue", f"{op_val:g}")
    opacity.txt("LowerBound", "0")
    opacity.txt("UpperBound", "100")

    blend = Obj("VideoComponentParam", classid_version=CLASS_ENUM_PARAM)
    blend.txt("DiscontinuousInterpolate", "true")
    blend.txt("ParameterControlType", "10")
    blend.txt("ParameterID", "2")
    blend.txt("Name", "Blend Mode")
    blend.txt("StartKeyframe", f"{SENTINEL},{api_val},0,0,0,0,0,0")
    blend.txt("LowerBound", "0")
    blend.txt("UpperBound", "27")

    matte = Obj("VideoComponentParam", classid_version=CLASS_ENUM_PARAM)
    matte.txt("DiscontinuousInterpolate", "true")
    matte.txt("ParameterID", "3")
    matte.txt("Name", "Blend Mode")
    matte.txt("StartKeyframe", f"{SENTINEL},{gui_val},0,0,0,0,0,0")
    matte.txt("LowerBound", "0")
    matte.txt("UpperBound", "31")

    filter_obj = Obj("VideoFilterComponent")
    filter_obj.el(E("Component", {"Version": "7"}, children=[
        E("Params", {"Version": "1"}, children=[
            E("Param", {"Index": "0", "ref": opacity}),
            E("Param", {"Index": "1", "ref": blend}),
            E("Param", {"Index": "2", "ref": matte}),
        ]),
        E("DisplayName", text="Opacity"),
        E("Intrinsic", text="true"),
        E("ID", text="1"),
    ]))
    if mask_filters:
        filter_obj.el(E("SubComponents", {"Version": "1"}, children=[
            E("SubComponent", {"Index": str(index), "ref": mask})
            for index, mask in enumerate(mask_filters)
        ]))
    filter_obj.txt("MatchName", "AE.ADBE Opacity")
    filter_obj.txt("VideoFilterType", "2")
    return filter_obj, [opacity, blend, matte]


def build_blur_component(effect: dict, warn) -> tuple[Obj, list[Obj]]:
    """Filtre `AE.Impact_Blur_FX` (le « Gaussian Blur » de Premiere) + ses 18 paramètres.

    Mapping v1 de `ec.blur.gaussian` : `blurriness` → « Amount » (borné 0-100, comme
    l'interface) ; « Thickness » reçoit la même valeur (corrélation observée sur le projet
    de référence) ; `dimensions` et `repeatEdge` n'ont pas d'équivalent (avertissement).
    """
    children = {child.get("match"): child for child in effect.get("children") or []}
    amount = 0.0
    if "blurriness" in children:
        amount = min(100.0, max(0.0, scalar_of(node_value(children["blurriness"]), 0.0)))
    dimensions = (scalar_of(node_value(children.get("dimensions")), 0.0)
                  if "dimensions" in children else 0.0)
    repeat = bool(node_value(children.get("repeatEdge"))) if "repeatEdge" in children else False
    if abs(dimensions) > 1e-9 or repeat:
        warn(f"effet « {effect.get('name')} » : dimensions/repeatEdge non repris par le "
             f"Gaussian Blur de Premiere (v1)")
    params = [
        make_mask_bool_param(8100, control=16, name="Error occurred"),
        make_mask_bool_param(1, control=11, name="Controls", upper=True),
        make_mask_bool_param(8040, control=16),
        make_mask_scalar_param(8041, "Seed", None, 0.0, 0, 99999),
        make_mask_scalar_param(2, "Angle", None, 0.0, -32768, 32767, control=3,
                               angle_class=True),
        make_mask_scalar_param(3, "Amount", None, amount, 0, 100, current=f"{amount:g}"),
        make_mask_scalar_param(4, "Thickness", None, amount, 0, 100),
        make_mask_bool_param(5, name="Uniform Blur", value=True),
        make_mask_scalar_param(6, "Chromatic Aberration", None, 0.0, 0, 100),
        make_mask_enum_param(8, "Edge Behavior", 1, 0, 2),
        make_mask_bool_param(8240, control=16),
        make_mask_bool_param(7, control=12, name="Controls", upper=True),
        make_mask_bool_param(8141, control=16),
        make_mask_scalar_param(8140, "Applied Version", None, 260000.0, 0, 999999),
        make_mask_bool_param(9020, name="Overlays Enabled"),
        make_mask_scalar_param(9040, "Sequence Width", None, -1.0, -1, 1000000000),
        make_mask_scalar_param(9041, "Sequence Height", None, -1.0, -1, 1000000000),
        make_mask_scalar_param(9042, "Sequence Pixel Ratio", None, -1.0, -1, 1000000000),
    ]
    filter_obj = Obj("VideoFilterComponent")
    filter_obj.el(E("Component", {"Version": "7"}, children=[
        E("Params", {"Version": "1"}, children=[
            E("Param", {"Index": str(position), "ref": param})
            for position, param in enumerate(params)
        ]),
        E("DisplayName", text=PP_BLUR_DISPLAY_NAME),
        E("ID", text="3"),
    ]))
    filter_obj.txt("MatchName", PP_BLUR_MATCH_NAME)
    filter_obj.txt("VideoFilterType", "2")
    return filter_obj, params


# ------------------------------------------------------- fabriques d'objets


def layout_e() -> E:
    return E("AudioChannelLayout", text=AUDIO_LAYOUT)


def make_audio_chain(components: list[Obj], with_defaults: bool = True) -> Obj:
    chain = Obj("AudioComponentChain")
    if with_defaults:
        chain.txt("DefaultVol", "true")
        chain.txt("DefaultVolumeComponentID", "1")
        chain.txt("DefaultChannelVolumeComponentID", "2")
    if components:
        chain.el(E("ComponentChain", {"Version": "3"}, children=[
            E("Components", {"Version": "1"}, children=[
                E("Component", {"Index": str(i), "ref": obj}) for i, obj in enumerate(components)
            ])
        ]))
    else:
        chain.el(E("ComponentChain", {"Version": "3"}))
    chain.el(layout_e())
    chain.txt("ChannelType", "1")
    return chain


def make_ccgvs():
    ccgvs = Obj("ClipChannelGroupVectorSerializer")
    ccvs = Obj("ClipChannelVectorSerializer")
    serializers = []
    for channel in range(2):
        serializer = Obj("ClipChannelSerializer")
        serializer.txt("SourceClipIndex", "0")
        serializer.txt("mSourceChannelIndex", str(channel))
        serializers.append(serializer)
    ccvs.el(E("ClipChannels", {"Version": "1"}, children=[
        E("ClipChannelItem", {"Index": "0", "ref": serializers[0]}),
        E("ClipChannelItem", {"Index": "1", "ref": serializers[1]}),
    ]))
    ccvs.txt("ChannelType", "1")
    ccgvs.el(E("ClipChannelVectors", {"Version": "1"}, children=[
        E("ClipChannelVectorItem", {"Index": "0", "ref": ccvs})
    ]))
    return ccgvs, ccvs, serializers


def make_video_chain(components: list[Obj] | None = None, intrinsic_motion: bool = False,
                     default_opacity: bool = True) -> Obj:
    """Chaîne de composants vidéo d'un clip.

    `components` : filtres de premier niveau dans l'ordre d'application (l'Opacité qui porte
    les masques d'abord, puis Motion, puis les effets). `default_opacity=False` quand une
    Opacité explicite est écrite (cas des masques — relevé sur le projet de référence :
    la chaîne ne porte alors plus DefaultOpacity).
    """
    components = list(components or [])
    chain = Obj("VideoComponentChain")
    if not components and not intrinsic_motion:
        chain.el(E("ComponentChain", {"Version": "3"}))
        return chain
    if intrinsic_motion:
        chain.txt("DefaultMotion", "true")
    if default_opacity:
        chain.txt("DefaultOpacity", "true")
    if intrinsic_motion:
        chain.txt("DefaultMotionComponentID", "2")
    if default_opacity:
        chain.txt("DefaultOpacityComponentID", "1")
    if components:
        chain.el(E("ComponentChain", {"Version": "3"}, children=[
            E("Components", {"Version": "1"}, children=[
                E("Component", {"Index": str(index), "ref": obj})
                for index, obj in enumerate(components)
            ])
        ]))
    else:
        chain.el(E("ComponentChain", {"Version": "3"}))
    return chain


def make_point_param(node: dict | None, cw: float, ch: float, fallback, offset=(0.0, 0.0)) -> Obj:
    param = Obj("PointComponentParam")
    if node is None:
        x, y = fallback
        param.txt("ParameterControlType", "17")
        param.txt("ParameterID", "-1")
        param.txt("StartKeyframe", point_start((x + offset[0]) / cw, (y + offset[1]) / ch))
        return param
    animated = bool(node_keys(node)) and not keys_are_constant(node)
    start, rows = point_keyframes(node, cw, ch, offset)
    if animated:
        param.txt("IsTimeVarying", "true")
    param.txt("ParameterControlType", "17")
    param.txt("ParameterID", "-1")
    param.txt("StartKeyframe", start)
    if rows is not None:
        param.txt("Keyframes", rows)
    return param


def make_scalar_param(node: dict | None, fallback=0.0, time_varying=False,
                      classid_version=None, bool_value=None) -> Obj:
    param = Obj("VideoComponentParam",
                classid_version=classid_version or CLASS_IDS["VideoComponentParam"])
    animated = bool(node_keys(node)) and not keys_are_constant(node)
    if animated and bool_value is None:
        start, rows = scalar_keyframes(node)
        param.txt("IsTimeVarying", "true")
        param.txt("ParameterControlType", "17")
        param.txt("ParameterID", "-1")
        param.txt("StartKeyframe", start)
        param.txt("Keyframes", rows)
        return param
    if bool_value is not None:
        param.txt("IsTimeVarying", "true")
        param.txt("ParameterControlType", "17")
        param.txt("ParameterID", "-1")
        param.txt("StartKeyframe", f"{SENTINEL},{'true' if bool_value else 'false'},0,0,0,0,0,0")
        return param
    value = scalar_of(node_value(node), fallback) if node is not None else fallback
    if time_varying:
        param.txt("IsTimeVarying", "true")
    param.txt("ParameterControlType", "17")
    param.txt("ParameterID", "-1")
    param.txt("StartKeyframe", scalar_start(value))
    return param


# ------------------------------------------------------------- construction


def convert(ec_path: str, out_path: str, path_maps: list[tuple[str, str]], skip_missing: bool,
            xml_dump: bool, log, audio_fallback: bool = False) -> dict:
    with open(ec_path, "r", encoding="utf-8") as handle:
        ec = json.load(handle)

    warnings: list[str] = []
    missing: list[str] = []
    warn = warnings.append
    warn_missing = missing.append

    items = ec.get("items") or {}
    comps = [it for it in items.values() if (it.get("kind") or {}).get("type") == "Comp"]
    if not comps:
        raise ValueError("aucune comp dans le .ecproj")
    if len(comps) > 1:
        warn(f"{len(comps)} comps trouvées : seule « {comps[0].get('name')} » est exportée (v1)")
    comp = comps[0]
    comp_kind = comp.get("kind") or {}
    cw = float(comp_kind.get("width") or 1080)
    ch = float(comp_kind.get("height") or 1920)
    frame_ticks = ticks_per_frame(comp_kind.get("frame_rate"))
    comp_duration = int(comp_kind.get("duration") or 0)
    sample_rate = int(((ec.get("settings") or {}).get("audio_sample_rate") or 48000))
    audio_ticks = int(round(TICKS_PER_SECOND / sample_rate)) if sample_rate else 5292000

    # --- médias -------------------------------------------------------------
    bundles: dict[str, dict] = {}
    for item_id, item in items.items():
        item_kind = item.get("kind") or {}
        itype = item_kind.get("type")
        if itype not in ("Footage", "Solid"):
            continue

        if itype == "Solid":
            is_adj = any(
                str((l.get("source") or {}).get("item")) == str(item_id)
                and (l.get("switches") or {}).get("adjustment")
                for l in (comp_kind.get("layers") or [])
            )
            name = str(item.get("name") or ("Adjustment Layer" if is_adj else "Color Matte"))
            token = TOKEN_BLACK_VIDEO if is_adj else TOKEN_COLOR_MATTE
            sw = int(item_kind.get("width") or cw)
            sh = int(item_kind.get("height") or ch)
            bundles[str(item_id)] = {
                "id": item_id,
                "name": name,
                "stored_path": token,
                "real_path": "",
                "width": sw,
                "height": sh,
                "frame_rate": comp_kind.get("frame_rate") or {"num": 25, "den": 1},
                "duration": STILL_DURATION_TICKS,
                "has_audio": False,
                "has_video": True,
                "is_still": True,
                "is_generator": True,
                "is_adjustment": is_adj,
                "color_rgb": solid_rgb(item_kind.get("color")),
            }
            continue

        real_path = str(item_kind.get("path") or "")
        if not real_path:
            warn(f"média « {item.get('name')} » sans chemin, ignoré")
            continue
        stored_path = conv.apply_path_map(real_path, path_maps)
        if stored_path != real_path and re.match(r"^[A-Za-z]:", stored_path):
            stored_path = stored_path.replace("/", "\\")
        missing = not os.path.exists(real_path)
        if missing:
            warn_missing(f"média absent du disque (référencé quand même) : {real_path}")
        is_still = str(item_kind.get("kind") or "") == "Still"
        bundles[str(item_id)] = {
            "id": item_id,
            "name": str(item.get("name") or os.path.basename(real_path)),
            "stored_path": stored_path,
            "real_path": real_path,
            "width": int(item_kind.get("width") or 1920),
            "height": int(item_kind.get("height") or 1080),
            # une image fixe est sans audio et de durée « infinie » (structure Adobe) ;
            # sa cadence est celle de la séquence, pas celle déclarée par l'item EC
            "frame_rate": ((comp_kind.get("frame_rate") if is_still
                            else item_kind.get("frame_rate")) or {"num": 25, "den": 1}),
            "duration": (STILL_DURATION_TICKS if is_still
                         else int(item_kind.get("duration") or 0)),
            "has_audio": bool(item_kind.get("has_audio", True)) and not is_still,
            "has_video": bool(item_kind.get("has_video", True)),
            "is_still": is_still,
            "is_generator": False,
            "is_adjustment": False,
            "missing": missing,
            "codec": str(item_kind.get("codec") or ""),
            "audio_sample_type": audio_sample_type(real_path, item_kind.get("codec")),
        }
        if not bundles[str(item_id)]["has_video"] and not bundles[str(item_id)]["has_audio"]:
            warn(f"média « {item.get('name')} » sans vidéo ni audio : traité comme vidéo")
            bundles[str(item_id)]["has_video"] = True
        elif not bundles[str(item_id)]["has_video"]:
            warn(f"média « {item.get('name')} » audio seul : exporté comme média audio "
                 f"(pas de flux vidéo)")
        if is_still:
            warn(f"média « {item.get('name')} » de type image : exporté comme image fixe "
                 f"(durée infinie, sans audio)")

    # --- calques → entrées, allocation de pistes ----------------------------
    layers = comp_kind.get("layers") or []
    layers_by_id = {int(l["id"]): l for l in layers if l.get("id") is not None}

    tracks: list[list[dict]] = []
    audio_only_entries: list[dict] = []
    for layer in reversed(layers):  # du bas vers le haut : piste libre la plus basse
        source = layer.get("source") or {}
        stype = source.get("type") or ""
        if stype == "Null":
            continue
        if stype not in ("Footage", "Solid") or str(source.get("item")) not in bundles:
            warn(f"calque « {layer.get('name')} » : source {stype or '?'} non exportée (v1)")
            continue
        if abs(float(layer.get("stretch") or 100.0) - 100.0) > 1e-6:
            warn(f"calque « {layer.get('name')} » : stretch ≠ 100 % non géré (v1), ignoré")
            continue
        blend = str(layer.get("blend_mode") or "Normal")
        if blend not in BLEND_MODE_MAP:
            warn(f"calque « {layer.get('name')} » : mode de fusion {blend} non géré (v1)")
        start = int(layer.get("in_point") or 0)
        end = int(layer.get("out_point") or 0)
        if end <= start:
            warn(f"calque « {layer.get('name')} » : in/out invalides, ignoré")
            continue
        layer_start = int(layer.get("start_time") or 0)
        entry = {
            "layer": layer,
            "bundle": bundles[str(source.get("item"))],
            "start": start,
            "end": end,
            "src_in": max(0, start - layer_start),
            "src_out": max(0, end - layer_start),
            "audio": bool((layer.get("switches") or {}).get("audio")),
            "track": -1,
        }
        if not entry["bundle"]["has_video"]:
            audio_only_entries.append(entry)  # média audio seul : pas de piste vidéo
            continue
        placed = False
        for index, track in enumerate(tracks):
            if all(entry["end"] <= item["start"] or entry["start"] >= item["end"]
                   for item in track):
                track.append(entry)
                entry["track"] = index
                placed = True
                break
        if not placed:
            tracks.append([entry])
            entry["track"] = len(tracks) - 1
    for track in tracks:
        track.sort(key=lambda item: item["start"])
    entries: list[dict] = ([entry for track in tracks for entry in track]
                           + audio_only_entries)
    entries_by_track = tracks
    track_count = max(1, len(entries_by_track))

    # Audio de secours (optionnel) : un segment vidéo dont tous les calques ont
    # `switches.audio=false` est muet dans EffectCraft. Sur demande, on reprend l'audio
    # du calque le plus bas (V1) de ce segment — cas typique d'un freeze créé par
    # duplication de la V1, où l'audio a été coupé sur les trois copies.
    if audio_fallback:
        candidates = [entry for entry in entries
                      if entry["bundle"]["has_video"] and entry["bundle"]["has_audio"]]
        for entry in sorted(candidates, key=lambda item: (item["track"], item["start"])):
            if entry["audio"]:
                continue
            overlap = [other for other in entries
                       if other["bundle"]["has_audio"]
                       and other["start"] < entry["end"] and entry["start"] < other["end"]]
            if any(other["audio"] for other in overlap):
                continue
            entry["audio"] = True
            warn(f"segment {entry['start'] / TICKS_PER_SECOND:.3f}–"
                 f"{entry['end'] / TICKS_PER_SECOND:.3f} s : aucune piste audio active — "
                 f"audio de secours pris sur « {entry['layer'].get('name')} »")

    # pistes audio : allocation indépendante (seuls les calques réellement audibles)
    audio_tracks_list: list[list[dict]] = []
    for entry in sorted((item for item in entries
                         if item["audio"] and item["bundle"]["has_audio"]),
                        key=lambda item: item["start"]):
        placed = False
        for track in audio_tracks_list:
            if all(entry["end"] <= item["start"] or entry["start"] >= item["end"]
                   for item in track):
                track.append(entry)
                placed = True
                break
        if not placed:
            audio_tracks_list.append([entry])
    audio_entries = [entry for track in audio_tracks_list for entry in track]
    audio_track_count = max(1, len(audio_tracks_list))

    # --- objets (création puis assemblage dans l'ordre du document) ---------
    document: list = []

    project_ref = Obj("Project", classid_version=CLASS_IDS["Project"])
    project = Obj("Project")
    project.el(E("Node", {"Version": "1"}, children=[E("Properties", {"Version": "1"}, children=[
        E("MZ.Project.ApplicationID", text="Pro"),
        E("MZ.BuildVersion.Modified", text=f"Pr 26.2.1 - {datetime.now():%d/%m/%Y %H:%M:%S}"),
        E("MZ.BuildVersion.Created", text=f"Pr 26.2.1 - {datetime.now():%d/%m/%Y %H:%M:%S}"),
    ])]))
    root = Obj("RootProjectItem", uid=True)
    root.el(E("ProjectItem", {"Version": "1"}, children=[E("Name", text="Root Bin")]))
    settings = Obj("ProjectSettings")
    compile_settings = [Obj("CompileSettings") for _ in range(5)]
    scratch = Obj("ScratchDiskSettings")
    for tag in ("AudioPreviewLocation0", "VideoPreviewLocation0", "CapsuleMediaLocation0",
                "DVDEncodingLocation0", "TransferMediaLocation0", "CapturedVideoLocation0",
                "CCLibrariesLocation0", "AutoSaveLocation0"):
        scratch.txt(tag, "SameAsProject")
    ingest = Obj("IngestSettings")
    ingest.txt("Enabled", "false")
    ingest.txt("Action", "copy")
    workspace = Obj("WorkspaceSettings")
    project.ref("RootProjectItem", root)
    project.ref("ProjectSettings", settings)
    project.ref("MovieCompileSettings", compile_settings[0])
    project.ref("StillCompileSettings", compile_settings[1])
    project.ref("AudioCompileSettings", compile_settings[2])
    project.ref("CustomCompileSettings", compile_settings[3])
    project.ref("VideoPreviewCompileSettings", compile_settings[4])
    project.ref("ScratchDiskSettings", scratch)
    project.ref("IngestSettings", ingest)
    project.ref("ProjectWorkspace", workspace)
    project.txt("NextID", "1000000")

    media_items = []
    for bundle in bundles.values():
        item_obj = Obj("ClipProjectItem", uid=True)
        item_obj.el(E("ProjectItem", {"Version": "1"}, children=[E("Name", text=bundle["name"])]))
        bundle["item"] = item_obj
        media_items.append(item_obj)
    sequence_item = Obj("ClipProjectItem", uid=True)
    sequence_item.el(E("ProjectItem", {"Version": "1"},
                       children=[E("Name", text=str(comp.get("name") or "Sequence"))]))
    root.el(E("ProjectItemContainer", {"Version": "1"}, children=[
        E("Items", {"Version": "1"}, children=[
            E("Item", {"Index": str(index), "ref": item})
            for index, item in enumerate(media_items + [sequence_item])
        ])
    ]))

    vs_global = Obj("VideoSettings")
    vs_global.txt("FrameRate", frame_ticks)
    vs_global.txt("FrameSize", f"0,0,{int(cw)},{int(ch)}")
    vs_global.txt("PixelAspectRatio", pixel_aspect_str(comp_kind.get("pixel_aspect")))
    as_global = Obj("AudioSettings")

    def make_vcs(target_vs: Obj) -> Obj:
        vcs = Obj("VideoCompileSettings")
        vcs.ref("VideoSettings", target_vs)
        for tag, value in (("Compressor", "1685288558"), ("VideoCompilerClassIDFourCC", "1061109567"),
                           ("VideoFileTypeFourCC", "1096173910"), ("Depth", "24"),
                           ("RenderDepth", "0"), ("Aspect43", "false"), ("Quality", "100"),
                           ("UseDataRate", "false"), ("DataRate", "3500"),
                           ("ForceRecompress", "true"), ("ForceRecompressValue", "2"),
                           ("Deinterlace", "false"), ("IgnoreVideoFilters", "false"),
                           ("OptimizeStills", "false"), ("FramesAtMarkers", "false"),
                           ("RealTimePreview", "true"), ("VideoFieldType", "0"),
                           ("DoKeyframeEveryNFrames", "false"),
                           ("DoKeyframeEveryNFramesValue", "0"),
                           ("AddKeyframesAtMarkers", "false"), ("AddKeyframesAtEdits", "false"),
                           ("RelativeFrameSize", "1")):
            vcs.txt(tag, value)
        return vcs

    def make_acs(target_as: Obj) -> Obj:
        acs = Obj("AudioCompileSettings")
        acs.ref("AudioSettings", target_as)
        acs.txt("SampleType", "3")
        acs.txt("Compressor", "1380013856")
        acs.txt("Interleave", "1")
        return acs

    global_pair_vs, global_pair_as = Obj("VideoSettings"), Obj("AudioSettings")
    vcs_global = make_vcs(global_pair_vs)
    acs_global = make_acs(global_pair_as)
    dummy_capture = Obj("DummyCaptureSettings")
    default_seq = Obj("DefaultSequenceSettings")
    default_seq.txt("TotalVideoTracks", "1")
    default_seq.txt("DefaultAudioStandardMonoTracks", "0")
    default_seq.txt("DefaultAudioStandardStereoTracks", "1")
    default_seq.txt("DefaultAudioStandard51Tracks", "0")
    default_seq.txt("DefaultAudioSubmixMonoTracks", "0")
    default_seq.txt("DefaultAudioSubmixStereoTracks", "0")
    default_seq.txt("DefaultAudioSubmix51Tracks", "0")

    compile_pairs = []
    for _ in range(5):
        pair_vs, pair_as = Obj("VideoSettings"), Obj("AudioSettings")
        compile_pairs.append((make_vcs(pair_vs), make_acs(pair_as), pair_vs, pair_as))
    for compile_obj, pair in zip(compile_settings, compile_pairs):
        compile_obj.ref("VideoCompileSettings", pair[0])
        compile_obj.ref("AudioCompileSettings", pair[1])
    for compile_obj in compile_settings:
        compile_obj.txt("CompilerClassIDFourCC", "0")
        compile_obj.txt("CompilerFourCC", "0")
        compile_obj.txt("ExportVideo", "true")
        compile_obj.txt("ExportAudio", "true")
        compile_obj.txt("AddToProjectWhenFinished", "true")
        compile_obj.txt("BeepWhenFinished", "false")
        compile_obj.txt("ExportWorkAreaOnly", "false")
        compile_obj.txt("EmbedProjectLink", "false")
    # le dernier (aperçu vidéo) reprend les FourCC du compilateur vidéo
    compile_settings[4].fields = [
        field for field in compile_settings[4].fields
        if not (field[0] == "t" and field[1] in ("CompilerClassIDFourCC", "CompilerFourCC"))
    ]
    insert_at = next(i for i, field in enumerate(compile_settings[4].fields)
                     if field[0] == "t" and field[1] == "ExportVideo")
    compile_settings[4].fields.insert(insert_at, ("t", "CompilerFourCC", "1096173910"))
    compile_settings[4].fields.insert(insert_at, ("t", "CompilerClassIDFourCC", "1061109567"))

    settings.ref("VideoSettings", vs_global)
    settings.ref("AudioSettings", as_global)
    settings.ref("VideoCompileSettings", vcs_global)
    settings.ref("AudioCompileSettings", acs_global)
    settings.ref("CaptureSettings", dummy_capture)
    settings.ref("DefaultSequenceSettings", default_seq)
    settings.txt("VideoTimeDisplay", "101")
    settings.txt("AudioTimeDisplay", "200")
    settings.txt("VideoTimeDisplayInitial", "999")
    settings.txt("ActionSafeWidth", "10")
    settings.txt("ActionSafeHeight", "10")
    settings.txt("TitleSafeWidth", "20")
    settings.txt("TitleSafeHeight", "20")
    settings.txt("ShouldScaleMedia", "false")
    settings.txt("EditingModeID", GUID_EDITING_MODE)
    settings.txt("PreviewFileFormatID", "00000000-0000-0000-0000-000000000000")
    settings.txt("UsePreviewCache", "false")
    settings.txt("ColorManagementSettings",
                 '{"enableLogColorManagement":2,"graphicsWhiteLuminance":203,'
                 '"lutInterpolationMethod":1}')

    # masterclips (médias puis séquence)
    masters = []
    for bundle in bundles.values():
        has_audio = bundle["has_audio"]
        is_still = bundle["is_still"]
        master = Obj("MasterClip", uid=True)
        if bundle.get("is_adjustment"):
            master.txt("IsAdjustmentLayer", "true")
        logging = Obj("ClipLoggingInfo")
        if is_still:
            logging.txt("CaptureMode", "2")
        logging.txt("ClipName", bundle["name"])
        logging.txt("TimecodeFormat", "101")
        if not is_still:
            logging.txt("MediaInPoint", "0")
            logging.txt("MediaOutPoint", str(bundle["duration"]))
        logging.txt("MediaFrameRate", str(ticks_per_frame(bundle["frame_rate"])))
        audio_chain = audio_source = audio_clip = None
        video_source = video_clip = None
        secondary = []
        clips_children: list[E] = []
        source_fields: list[Obj] = []
        children_order: list[Obj] = []
        if bundle["has_video"]:
            video_source = Obj("VideoMediaSource")
            video_clip_children = []
            if bundle.get("is_generator"):
                # Les clips de média synthétique portent cette préférence Adobe
                # (pas de MarkerOwner).
                video_clip_children.append(E("Node", {"Version": "1"}, children=[
                    E("Properties", {"Version": "1"}, children=[
                        E("BE.Prefs.SyntheticMedia.DefaultIsDropFrame", text=DEFAULT_IS_DROP_FRAME)
                    ])
                ]))
            video_clip_children += [E("Source", {"ref": video_source}),
                                    E("ClipID", text=str(uuid.uuid4()))]
            if is_still:
                template_ticks = (GENERATOR_TEMPLATE_CLIP_TICKS
                                  if bundle.get("is_generator") else STILL_CLIP_TICKS)
                video_clip_children += [E("InPoint", text="0"),
                                        E("OutPoint", text=str(template_ticks))]
            video_clip_children.append(E("InUse", text="false"))
            video_clip = Obj("VideoClip")
            video_clip.el(E("Clip", {"Version": "18"}, children=video_clip_children))
            clips_children.append(E("Clip", {"Index": "0", "ref": video_clip}))
            source_fields.append(video_source)
        if has_audio:
            audio_chain = make_audio_chain([])
            audio_source = Obj("AudioMediaSource")
            audio_clip = Obj("AudioClip")
            for channel in range(2):
                sec = Obj("SecondaryContent")
                sec.ref("Content", audio_source)
                sec.txt("ChannelIndex", str(channel))
                secondary.append(sec)
            audio_clip.el(E("Clip", {"Version": "18"}, children=[
                E("Source", {"ref": audio_source}),
                E("ClipID", text=str(uuid.uuid4())),
                E("InUse", text="false"),
            ]))
            audio_clip.el(E("SecondaryContents", {"Version": "1"}, children=[
                E("SecondaryContentItem", {"Index": "0", "ref": secondary[0]}),
                E("SecondaryContentItem", {"Index": "1", "ref": secondary[1]}),
            ]))
            audio_clip.el(layout_e())
            clips_children.append(E("Clip", {"Index": str(len(clips_children)), "ref": audio_clip}))
            source_fields += [audio_source, secondary[0], secondary[1]]
        if has_audio:
            ccgvs, ccvs, ccs = make_ccgvs()
            source_fields.append(ccvs)
        else:
            ccgvs, ccvs, ccs = Obj("ClipChannelGroupVectorSerializer"), None, []
        # ordre d'émission des enfants du masterclip, comme Adobe :
        # LoggingInfo, chaîne audio, clip vidéo, clip audio, groupes de canaux
        children_order = [logging]
        if audio_chain is not None:
            children_order.append(audio_chain)
        if video_clip is not None:
            children_order.append(video_clip)
        if audio_clip is not None:
            children_order.append(audio_clip)
        children_order.append(ccgvs)
        master.ref("LoggingInfo", logging)
        if has_audio:
            master.el(E("AudioComponentChains", {"Version": "1"}, children=[
                E("AudioComponentChain", {"Index": "0", "ref": audio_chain})
            ]))
        master.el(E("Clips", {"Version": "1"}, children=clips_children))
        master.ref("AudioClipChannelGroups", ccgvs)
        master.txt("Name", bundle["name"])
        master.txt("MasterClipChangeVersion", "0")
        bundle["master_obj"] = master
        bundle["video_source"] = video_source
        bundle["audio_source"] = audio_source
        masters.append({"master": master, "logging": logging, "audio_chain": audio_chain,
                        "video_source": video_source, "audio_source": audio_source,
                        "video_clip": video_clip, "audio_clip": audio_clip,
                        "secondary": secondary, "ccgvs": ccgvs, "ccvs": ccvs, "ccs": ccs,
                        "children": children_order, "sources": source_fields})

    sequence = Obj("Sequence", uid=True)
    seq_master = Obj("MasterClip", uid=True)
    seq_logging = Obj("ClipLoggingInfo")
    seq_audio_chain = make_audio_chain([])
    seq_audio_source = Obj("AudioSequenceSource")
    seq_video_source = Obj("VideoSequenceSource")
    seq_secondary = []
    for channel in range(2):
        sec = Obj("SecondaryContent")
        sec.ref("Content", seq_audio_source)
        sec.txt("ChannelIndex", str(channel))
        seq_secondary.append(sec)
    seq_audio_clip = Obj("AudioClip")
    seq_audio_clip.el(E("Clip", {"Version": "18"}, children=[
        E("Source", {"ref": seq_audio_source}),
        E("ClipID", text=str(uuid.uuid4())),
        E("InUse", text="false"),
    ]))
    seq_audio_clip.el(E("SecondaryContents", {"Version": "1"}, children=[
        E("SecondaryContentItem", {"Index": "0", "ref": seq_secondary[0]}),
        E("SecondaryContentItem", {"Index": "1", "ref": seq_secondary[1]}),
    ]))
    seq_audio_clip.el(layout_e())
    seq_video_clip = Obj("VideoClip")
    seq_video_clip.el(E("Clip", {"Version": "18"}, children=[
        E("Source", {"ref": seq_video_source}),
        E("ClipID", text=str(uuid.uuid4())),
        E("InUse", text="false"),
    ]))
    seq_ccgvs, seq_ccvs, seq_ccs = make_ccgvs()
    seq_master.ref("LoggingInfo", seq_logging)
    seq_master.el(E("AudioComponentChains", {"Version": "1"}, children=[
        E("AudioComponentChain", {"Index": "0", "ref": seq_audio_chain})
    ]))
    seq_master.el(E("Clips", {"Version": "1"}, children=[
        E("Clip", {"Index": "0", "ref": seq_audio_clip}),
        E("Clip", {"Index": "1", "ref": seq_video_clip}),
    ]))
    seq_master.ref("AudioClipChannelGroups", seq_ccgvs)
    seq_master.txt("Name", str(comp.get("name") or "Sequence"))
    seq_master.txt("MasterClipChangeVersion", "7")
    sequence_item.ref("MasterClip", seq_master)
    for bundle, entry in zip(bundles.values(), masters):
        bundle["item"].ref("MasterClip", entry["master"])

    # groupes de pistes et pistes
    video_group_chain = make_video_chain(None)
    video_group = Obj("VideoTrackGroup")
    audio_group = Obj("AudioTrackGroup")
    video_tracks = [Obj("VideoClipTrack", uid=True) for _ in range(track_count)]
    audio_tracks = [Obj("AudioClipTrack", uid=True) for _ in range(audio_track_count)]
    audio_mix_track = Obj("AudioMixTrack")

    # composants audio des pistes : un fader + un meter par piste (plus le mixeur),
    # un panner (avec son paramètre Balance) par piste — structure relevée sur Adobe
    def make_balance_param() -> Obj:
        param = Obj("AudioComponentParam", classid_version=CLASS_VOLUME_PARAM)
        param.txt("StartKeyframe", f"{SENTINEL},0.5,0,0,0,0,0,0")
        param.txt("CurrentValue", "0.5")
        param.txt("IsInverted", "true")
        param.txt("Name", "Balance")
        return param

    balance_params = [make_balance_param() for _ in range(audio_track_count)]

    def make_panner(balance: Obj | None):
        panner = (Obj("StereoToStereoPanProcessor") if balance is not None
                  else Obj("DefaultPanProcessor"))
        component_children = [E("ID", text="4294967280")]
        if balance is not None:
            component_children.insert(0, E("Params", {"Version": "1"}, children=[
                E("Param", {"Index": "0", "ref": balance})
            ]))
        panner.el(E("PanProcessor", {"Version": "3"}, children=[E("AudioComponent", {"Version": "3"}, children=[
            E("Component", {"Version": "7"}, children=component_children),
            layout_e(),
            E("ChannelType", text="1"),
            E("FrameRate", text=str(audio_ticks)),
            E("AudioComponentType", text="0"),
        ])]))
        if balance is None:
            panner.txt("DefaultPannerInputChannelType", "1")
            panner.txt("DefaultPannerOutputChannelType", "1")
        return panner

    panners = [make_panner(balance) for balance in balance_params]
    default_panner = make_panner(None)

    def make_fader(params):
        fader = Obj("AudioFader")
        fader.el(E("AudioComponent", {"Version": "3"}, children=[
            E("Component", {"Version": "7"}, children=[
                E("Params", {"Version": "1"}, children=[
                    E("Param", {"Index": "0", "ref": params[0]}),
                    E("Param", {"Index": "1", "ref": params[1]}),
                ]),
                E("ID", text="1"),
            ]),
            layout_e(),
            E("ChannelType", text="1"),
            E("FrameRate", text=str(audio_ticks)),
            E("AudioComponentType", text="1"),
        ]))
        return fader

    def make_meter():
        meter = Obj("AudioMeter")
        meter.el(E("AudioComponent", {"Version": "3"}, children=[
            E("Component", {"Version": "7"}, children=[E("ID", text="2")]),
            layout_e(),
            E("ChannelType", text="1"),
            E("FrameRate", text=str(audio_ticks)),
            E("AudioComponentType", text="2"),
        ]))
        return meter

    def make_fader_params():
        volume_param = Obj("AudioComponentParam", classid_version=CLASS_VOLUME_PARAM)
        volume_param.txt("UpperBound", "5.6234130859375")
        volume_param.txt("RangeLocked", "false")
        volume_param.txt("UnitsString", "dB")
        volume_param.txt("Name", "Volume")
        mute_param = Obj("AudioComponentParam", classid_version=CLASS_MUTE_PARAM)
        mute_param.txt("RangeLocked", "false")
        mute_param.txt("Name", "Mute")
        return volume_param, mute_param

    fader_params = [make_fader_params() for _ in range(audio_track_count + 1)]
    faders = [make_fader(pair) for pair in fader_params]
    meters = [make_meter() for _ in range(audio_track_count + 1)]
    track_chains = [make_audio_chain([faders[index], meters[index]], with_defaults=False)
                    for index in range(audio_track_count)]
    mix_chain = make_audio_chain([faders[audio_track_count], meters[audio_track_count]],
                                 with_defaults=False)
    inlet = Obj("AudioTrackInlet")
    # Adobe route TOUTES les pistes audio dans l'inlet du mixage ; n'en
    # déclarer qu'une laissait les autres pistes hors du bus master.
    inlet.el(E("Sources", {"Version": "1"}, children=[
        E("Source", {"Index": str(index), "ref": track})
        for index, track in enumerate(audio_tracks)
    ]))
    inlet.el(layout_e())

    # --- médias (objets Media + flux) et sources -----------------------------
    for bundle in bundles.values():
        audio_stream = None
        if bundle["has_audio"]:
            audio_stream = Obj("AudioStream")
            audio_stream.el(layout_e())
            audio_stream.txt("FrameRate", audio_ticks)
            audio_stream.txt("Duration", str(bundle["duration"]))
            audio_stream.txt("SampleType", bundle.get("audio_sample_type") or "7")
        video_stream = None
        if bundle["has_video"]:
            video_stream = Obj("VideoStream")
            if bundle.get("is_generator"):
                # Flux still d'un générateur synthétique : structure des projets
                # Premiere 2026 (COLR / BLAK), sans fichier ni codec réel.
                video_stream.txt("IsStill", "true")
                video_stream.txt("FrameRate", ticks_per_frame(bundle["frame_rate"]))
                video_stream.txt("FrameRect", f"0,0,{bundle['width']},{bundle['height']}")
                video_stream.txt("Duration", str(bundle["duration"]))
                video_stream.txt("CodecType", STILL_CODEC_TYPE)
                if bundle.get("is_adjustment"):
                    video_stream.txt("IsContinuousTime", "true")
                video_stream.txt("FieldTypeIsUncertain", "true")
            else:
                video_stream.txt("FrameRate", ticks_per_frame(bundle["frame_rate"]))
                video_stream.txt("Duration", str(bundle["duration"]))
                video_stream.txt("CodecType", "1061109567")
                if bundle["is_still"]:
                    video_stream.txt("IsStill", "true")
                video_stream.txt("FrameRect", f"0,0,{bundle['width']},{bundle['height']}")
                video_stream.txt("OriginalColorSpace", COLOR_SPACE)
                video_stream.txt("IsPAROverridden", "true")
                video_stream.txt("OriginalFieldType", "-1")
                video_stream.txt("IsFieldTypeOverridden", "true")
                video_stream.txt("OverriddenFieldType", "0")
        media_obj = Obj("Media", uid=True)
        if audio_stream is not None:
            media_obj.ref("AudioStream", audio_stream)
        if video_stream is not None:
            media_obj.ref("VideoStream", video_stream)
        if bundle.get("is_generator"):
            if not bundle.get("is_adjustment"):
                # La couleur d'un Color Matte tient dans ces 8 octets :
                # [R, G, B, 0, 1, 0, 0, 0] (opaque, version 1).
                r, g, b = bundle.get("color_rgb") or (0, 0, 0)
                prefs = base64.b64encode(bytes([r, g, b, 0, 1, 0, 0, 0])).decode("ascii")
                media_obj.el(E("ImporterPrefs", {
                    "Encoding": "base64",
                    "BinaryHash": mask_payload_hash(prefs),
                }, text=prefs))
                state, state_hash = (COLOR_MATTE_MODIFICATION_STATE,
                                     COLOR_MATTE_MODIFICATION_STATE_HASH)
            else:
                # Black Video (calque de réglage) n'a pas d'ImporterPrefs.
                state, state_hash = (BLACK_VIDEO_MODIFICATION_STATE,
                                     BLACK_VIDEO_MODIFICATION_STATE_HASH)
            media_obj.el(E("ModificationState", {
                "Encoding": "base64",
                "BinaryHash": state_hash,
            }, text=state))
            media_obj.txt("FilePath", bundle["stored_path"])
            media_obj.txt("Infinite", "true")
            media_obj.txt("ImplementationID", GUID_GENERATOR_IMPLEMENTATION)
            media_obj.txt("Title", "Black Video" if bundle.get("is_adjustment") else bundle["name"])
            # Conservé pour les tests/archives existants : Adobe l'omet, mais
            # Premiere l'accepte sur les générateurs (0 = pas de raison d'offline).
            media_obj.txt("OfflineReason", "0")
            media_obj.txt("ActualMediaFilePath", bundle["stored_path"])
            media_obj.txt("ContentAndMetadataState", ZERO_GUID)
            media_obj.txt("ConformedAudioRate", NO_CONFORMED_AUDIO_RATE)
        else:
            media_obj.txt("FilePath", bundle["stored_path"])
            media_obj.txt("ImplementationID", GUID_IMPLEMENTATION)
            media_obj.txt("Title", os.path.basename(bundle["real_path"]) or bundle["name"])
            if bundle["is_still"]:
                media_obj.txt("Infinite", "true")
            # Adobe n'écrit OfflineReason que pour un média hors ligne ; un
            # fichier présent doit rester sans cette balise, sinon Premiere
            # marque le média offline et doit le reconnecter à l'ouverture.
            if bundle.get("missing"):
                media_obj.txt("OfflineReason", "1")
            if audio_stream is not None:
                # Ticks par échantillon audio conformé (48 kHz -> 5292000).
                # Adobe l'écrit sur tout média porteur d'audio ; sans cette
                # valeur, le moteur audio de Premiere 2026 part avec un taux
                # invalide et plante à la lecture (AudioFilterHost).
                media_obj.txt("ConformedAudioRate", str(audio_ticks))
            media_obj.txt("ContentAndMetadataState", str(uuid.uuid4()))
            media_obj.txt("ActualMediaFilePath", bundle["stored_path"])
        bundle["media_obj"] = media_obj
        bundle["audio_stream"] = audio_stream
        bundle["video_stream"] = video_stream
        for source in (bundle["video_source"], bundle["audio_source"]):
            if source is None:
                continue
            source.el(E("MediaSource", {"Version": "4"}, children=[
                E("Content", {"Version": "10"}),
                E("Media", {"ref": media_obj}),
            ]))
            source.txt("OriginalDuration", str(bundle["duration"]))
    for source in (seq_audio_source, seq_video_source):
        source.el(E("SequenceSource", {"Version": "4"}, children=[
            E("Content", {"Version": "10"}),
            E("Sequence", {"ref": sequence}),
        ]))
        source.txt("OriginalDuration", str(comp_duration))

    # --- objets par entrée : items, sous-clips, chaînes, filtres, paramètres --
    for entry in entries:
        layer = entry["layer"]
        bundle = entry["bundle"]
        name = str(layer.get("name") or bundle["name"])
        time_effect = build_time_remap(entry, warn)
        entry["time_effect"] = time_effect
        entry["time_remap"] = (time_effect if time_effect is not None
                               and time_effect["kind"] == "remap" else None)
        entry["frame_hold"] = (time_effect["start"] if time_effect is not None
                               and time_effect["kind"] == "hold" else None)
        sw = float(bundle["width"])
        sh = float(bundle["height"])
        offset = conv.world_offset(layer, layers_by_id) if layer.get("parent") else (0.0, 0.0)

        position_node = conv.find_transform_node(layer, "position")
        anchor_node = conv.find_transform_node(layer, "anchor")
        scale_node = conv.find_transform_node(layer, "scale")
        rotation_node = conv.find_transform_node(layer, "rotation")
        opacity_node = conv.find_transform_node(layer, "opacity")

        scale_value = node_value(scale_node) or [100.0, 100.0, 100.0]
        scale_x = scalar_of(scale_value, 100.0)
        scale_y = (float(scale_value[1]) if isinstance(scale_value, list)
                   and len(scale_value) > 1 else scale_x)
        rotation_value = scalar_of(node_value(rotation_node), 0.0)

        def animated(node):
            return bool(node) and bool(node_keys(node)) and not keys_are_constant(node)

        if opacity_node is not None:
            opacity_value = node_value(opacity_node)
            if animated(opacity_node) or (opacity_value is not None
                                          and abs(scalar_of(opacity_value, 100.0) - 100.0) > 1e-6):
                warn(f"opacité ≠ 100 sur « {name} » : non exportée (v1)")
        if animated(anchor_node):
            warn(f"ancre animée sur « {name} » : encodage expérimental")
        if animated(rotation_node):
            warn(f"rotation animée sur « {name} » : encodage expérimental")
        if animated(scale_node):
            warn(f"échelle animée sur « {name} » : encodage expérimental")

        if animated(scale_node):
            scale_param_x = make_scalar_param(scale_node, 100.0)
            scale_param_y = make_scalar_param(scale_node, 100.0)
        else:
            scale_param_x = make_scalar_param(None, fallback=scale_x)
            scale_param_y = make_scalar_param(None, fallback=scale_y)
        rotation_param = (make_scalar_param(rotation_node, 0.0) if rotation_node is not None
                          else make_scalar_param(None, fallback=rotation_value))

        # Transform identique aux valeurs par défaut → chaîne intrinsèque d'Adobe
        # (« DefaultMotion », aucun filtre Motion) ; c'est le cas des images fixes posées
        # plein cadre, que Premiere rend avec ses composants par défaut.
        pos_x, pos_y = node_pair(position_node, (cw / 2.0, ch / 2.0))
        anc_x, anc_y = node_pair(anchor_node, (sw / 2.0, sh / 2.0))
        default_motion = (
            not animated(position_node) and not animated(anchor_node)
            and not animated(scale_node) and not animated(rotation_node)
            and offset == (0.0, 0.0)
            and abs(pos_x - cw / 2.0) < 1e-6 and abs(pos_y - ch / 2.0) < 1e-6
            and abs(anc_x - sw / 2.0) < 1e-6 and abs(anc_y - sh / 2.0) < 1e-6
            and abs(scale_x - 100.0) < 1e-6 and abs(scale_y - 100.0) < 1e-6
            and abs(rotation_value) < 1e-6
        )

        # masques : sous-composants du filtre Opacité du clip (structure la plus simple,
        # validée dans Premiere 2026 à côté de la variante Adobe avec conteneur
        # `SelectionComponents`). Les effets restent des composants de premier niveau de
        # la chaîne, comme Motion.
        layer_masks: list[dict] = []
        for mask in conv.enabled_masks(layer):
            mode = str((mask.get("kind") or {}).get("mode") or "Add")
            if mode == "None":
                continue                       # mode None : aucun découpage, rien à écrire
            if not mask_path_is_closed(mask):
                warn(f"masque « {mask.get('name')} » de « {name} » : tracé ouvert (non fermé) "
                     f"— non écrit, comme dans EffectCraft (le calque reste entier)")
                continue
            layer_masks.append(mask)

        mask_filters: list[Obj] = []
        mask_params: list[Obj] = []
        for mask_index, mask in enumerate(layer_masks):
            mask_filter, params = build_mask_component(mask, mask_index, sw, sh, warn)
            mask_filters.append(mask_filter)
            mask_params += params

        opacity_node = conv.find_transform_node(layer, "opacity")
        has_animated_opacity = animated(opacity_node)
        opacity_val = scalar_of(node_value(opacity_node), 100.0) if opacity_node is not None else 100.0
        has_custom_opacity = has_animated_opacity or (abs(opacity_val - 100.0) > 1e-6)
        has_custom_blend = (blend != "Normal" and blend in BLEND_MODE_MAP)

        if mask_filters or has_custom_opacity or has_custom_blend:
            opacity_filter, opacity_params = build_opacity_component(
                mask_filters=mask_filters,
                opacity_node=opacity_node if has_custom_opacity else None,
                blend_mode=blend if has_custom_blend else "Normal",
            )
        else:
            opacity_filter, opacity_params = None, []

        blur_filters: list[Obj] = []
        blur_params: list[Obj] = []
        for effect in conv.collect_effects(layer):
            if effect.get("enabled") is False:
                continue
            if (effect.get("kind") or {}).get("effect") not in PP_BLUR_EFFECTS:
                warn(f"calque « {name} » : effet « {effect.get('name')} » non repris "
                     f"par Premiere (v1)")
                continue
            blur_filter, params = build_blur_component(effect, warn)
            blur_filters.append(blur_filter)
            blur_params += params
        if (opacity_filter is not None or blur_filters) and default_motion:
            # opacité personnalisée ou effet + Transform par défaut : écrire un filtre
            # Motion explicite (un masque de clip ne l'exige pas)
            default_motion = False

        if default_motion:
            motion_params = []
            motion_filter = None
        else:
            motion_params = [
                make_point_param(position_node, cw, ch, (cw / 2.0, ch / 2.0), offset),
                scale_param_x,
                scale_param_y,
                make_scalar_param(None, fallback=0.0, classid_version=CLASS_UNIFORM_PARAM,
                                  bool_value=abs(scale_x - scale_y) < 1e-9),
                rotation_param,
                make_point_param(anchor_node, sw, sh, (sw / 2.0, sh / 2.0)),
                make_scalar_param(None, fallback=0.0, time_varying=True,
                                  classid_version=CLASS_ALT_PARAM),
                make_scalar_param(None, fallback=0.0, time_varying=True),
                make_scalar_param(None, fallback=0.0, time_varying=True),
                make_scalar_param(None, fallback=0.0, time_varying=True),
                make_scalar_param(None, fallback=0.0, time_varying=True),
            ]
            motion_filter = Obj("VideoFilterComponent")
            motion_filter.el(E("Component", {"Version": "7"}, children=[
                E("Params", {"Version": "1"}, children=[
                    E("Param", {"Index": str(index), "ref": param})
                    for index, param in enumerate(motion_params)
                ]),
                E("Intrinsic", text="true"),
                E("ID", text="2"),
            ]))
            motion_filter.txt("MatchName", "AE.ADBE Motion")
            motion_filter.txt("VideoFilterType", "2")
        components = ([opacity_filter] if opacity_filter is not None else []) \
            + ([motion_filter] if motion_filter is not None else []) + blur_filters
        video_chain = make_video_chain(components or None, intrinsic_motion=not components,
                                       default_opacity=opacity_filter is None)
        entry["params"] = motion_params
        entry["extra_params"] = opacity_params + mask_params + blur_params
        entry["keyframed"] = animated(position_node)
        entry["motion_filter"] = motion_filter
        entry["mask_filters"] = mask_filters
        entry["opacity_filter"] = opacity_filter
        entry["blur_filters"] = blur_filters
        segment_video_clip = Obj("VideoClip")
        clip_children = []
        if bundle.get("is_generator"):
            clip_children.append(E("Node", {"Version": "1"}, children=[
                E("Properties", {"Version": "1"}, children=[
                    E("BE.Prefs.SyntheticMedia.DefaultIsDropFrame", text=DEFAULT_IS_DROP_FRAME)
                ])
            ]))
        clip_children.append(E("Source", {"ref": bundle["video_source"]}))
        if entry["time_remap"] is not None:
            clip_children.append(E("TimeRemapping", {"ref": entry["time_remap"]["remap"]}))
        # Adobe place les clips de média synthétique à 1 h dans la source de 12 h
        # (le template du masterclip reste à 0–5 s).
        source_offset = (GENERATOR_SOURCE_IN_TICKS
                         if bundle.get("is_generator") and entry["time_remap"] is None else 0)
        clip_children += [
            E("ClipID", text=str(uuid.uuid4())),
            E("InPoint", text=str(entry["src_in"] + source_offset)),
            E("OutPoint", text=str(entry["src_out"] + source_offset)),
        ]
        segment_video_clip.el(E("Clip", {"Version": "18"}, children=clip_children))
        if entry["frame_hold"] is not None:
            # Add Frame Hold de Premiere : 4 = hold sur une image source précise.
            segment_video_clip.txt("FrameHold", "4")
            segment_video_clip.txt("FrameHoldStart", str(entry["frame_hold"]))
        video_subclip = Obj("SubClip")
        video_subclip.ref("Clip", segment_video_clip)
        video_subclip.ref("MasterClip", bundle["master_obj"])
        video_subclip.txt("OrigChGrp", "0")
        video_subclip.txt("Name", bundle["name"])
        video_item = Obj("VideoClipTrackItem")
        video_item.el(E("ClipTrackItem", {"Version": "8"}, children=[
            E("ComponentOwner", {"Version": "1"},
              children=[E("Components", {"ref": video_chain})]),
            E("TrackItem", {"Version": "4"}, children=(
                [E("Start", text=str(entry["start"]))] if entry["start"] else []
            ) + [E("End", text=str(entry["end"]))]),
            E("SubClip", {"ref": video_subclip}),
        ]))
        video_item.txt("FrameRect", f"0,0,{int(cw)},{int(ch)}")
        video_item.txt("PixelAspectRatio", "1,1")
        video_item.txt("ToneMapSettings", TONEMAP)
        entry["video_item"] = video_item
        entry["video_chain"] = video_chain
        entry["video_subclip"] = video_subclip
        entry["segment_video_clip"] = segment_video_clip

        if not bundle["has_video"]:
            # média audio seul : pas d'item vidéo (les objets construits plus haut ne sont
            # pas émis — seule la partie audio de ce calque compte)
            entry["params"] = []
            entry["extra_params"] = []
            entry["motion_filter"] = None
            entry["mask_filters"] = []
            entry["opacity_filter"] = None
            entry["blur_filters"] = []
            entry["frame_hold"] = None
            entry["time_remap"] = None
            entry["video_chain"] = None
            entry["video_item"] = None
            entry["video_subclip"] = None
            entry["segment_video_clip"] = None
            entry["keyframed"] = False

        if entry["audio"] and bundle["has_audio"]:
            # Chaîne vide comme les clips audio par défaut d'Adobe : le volume
            # reste au niveau neutre du fader de piste. Ajouter un filtre
            # « Internal Volume » à chaque clip faisait apparaître un badge fx
            # et passait inutilement par AudioFilterHost à la lecture.
            audio_chain = make_audio_chain([])
            segment_secondary = []
            for channel in range(2):
                sec = Obj("SecondaryContent")
                sec.ref("Content", bundle["audio_source"])
                sec.txt("ChannelIndex", str(channel))
                segment_secondary.append(sec)
            segment_audio_clip = Obj("AudioClip")
            segment_audio_clip.el(E("Clip", {"Version": "18"}, children=[
                E("Source", {"ref": bundle["audio_source"]}),
                E("ClipID", text=str(uuid.uuid4())),
                E("InPoint", text=str(entry["src_in"])),
                E("OutPoint", text=str(entry["src_out"])),
            ]))
            segment_audio_clip.el(E("SecondaryContents", {"Version": "1"}, children=[
                E("SecondaryContentItem", {"Index": "0", "ref": segment_secondary[0]}),
                E("SecondaryContentItem", {"Index": "1", "ref": segment_secondary[1]}),
            ]))
            segment_audio_clip.el(layout_e())
            audio_subclip = Obj("SubClip")
            audio_subclip.ref("Clip", segment_audio_clip)
            audio_subclip.ref("MasterClip", bundle["master_obj"])
            audio_subclip.txt("OrigChGrp", "0")
            audio_subclip.txt("Name", bundle["name"])
            audio_item = Obj("AudioClipTrackItem")
            audio_item.el(E("ClipTrackItem", {"Version": "8"}, children=[
                E("ComponentOwner", {"Version": "1"},
                  children=[E("Components", {"ref": audio_chain})]),
                E("TrackItem", {"Version": "4"}, children=(
                    [E("Start", text=str(entry["start"]))] if entry["start"] else []
                ) + [E("End", text=str(entry["end"]))]),
                E("SubClip", {"ref": audio_subclip}),
            ]))
            audio_item.txt("PreRenderComponentChainHashVersion", "1")
            audio_item.txt("ID", str(uuid.uuid4()))
            entry["audio_item"] = audio_item
            entry["audio_chain"] = audio_chain
            entry["audio_subclip"] = audio_subclip
            entry["segment_audio_clip"] = segment_audio_clip
            entry["audio_filter"] = None
            entry["audio_tail_params"] = []
            entry["audio_tail_secondary"] = segment_secondary

    # --- remplissage des pistes, groupes et séquence -------------------------
    video_group.el(E("TrackGroup", {"Version": "1"}, children=[
        E("Tracks", {"Version": "1"}, children=[
            E("Track", {"Index": str(i), "ref": track}) for i, track in enumerate(video_tracks)
        ]),
        E("FrameRate", text=str(frame_ticks)),
        E("NextTrackID", text=str(track_count + 1)),
    ]))
    video_group.txt("FrameRect", f"0,0,{int(cw)},{int(ch)}")
    video_group.txt("OutputColorSpace", COLOR_SPACE)
    video_group.txt("ImmersiveVideoVRConfiguration", IMMERSIVE)
    video_group.txt("IsGraphicsWhiteSameAsProject", "false")
    video_group.txt("IsColorAwareEffectsEnabledSameAsProject", "false")
    video_group.el(E("ComponentOwner", {"Version": "1"},
                     children=[E("Components", {"ref": video_group_chain})]))

    for index, (track_obj, track_entries) in enumerate(zip(video_tracks, entries_by_track)):
        track_obj.el(E("ClipTrack", {"Version": "2"}, children=[
            E("Track", {"Version": "4"}, children=[
                E("Node", {"Version": "1"}, children=[E("Properties", {"Version": "1"}, children=[
                    E("MZ.TrackTargeted", text="1")
                ])]),
                E("MediaType", text=GUID_MEDIA_VIDEO),
                E("Index", text=str(index)),
                E("ID", text=str(index + 1)),
            ]),
            E("ClipItems", {"Version": "3"}, children=[
                E("TrackItems", {"Version": "1"}, children=[
                    E("TrackItem", {"Index": str(i), "ref": entry["video_item"]})
                    for i, entry in enumerate(track_entries)
                ]),
                E("MediaType", text=GUID_MEDIA_VIDEO),
                E("Index", text=str(index)),
            ]),
            E("TransitionItems", {"Version": "3"}, children=[
                E("MediaType", text=GUID_MEDIA_VIDEO),
                E("Index", text=str(index)),
            ]),
        ]))
    audio_entries_by_track = audio_tracks_list + [[] for _ in range(
        audio_track_count - len(audio_tracks_list))]
    for index, (track_obj, track_entries, panner, chain) in enumerate(
            zip(audio_tracks, audio_entries_by_track, panners, track_chains)):
        track_obj.el(E("ClipTrack", {"Version": "2"}, children=[
            E("Track", {"Version": "4"}, children=[
                E("Node", {"Version": "1"}, children=[E("Properties", {"Version": "1"}, children=[
                    E("MZ.TrackTargeted", text="1")
                ])]),
                E("MediaType", text=GUID_MEDIA_AUDIO),
                E("Index", text=str(index)),
                E("ID", text=str(index + 2)),
            ]),
            E("ClipItems", {"Version": "3"}, children=[
                E("TrackItems", {"Version": "1"}, children=[
                    E("TrackItem", {"Index": str(i), "ref": entry["audio_item"]})
                    for i, entry in enumerate(track_entries)
                ]),
                E("MediaType", text=GUID_MEDIA_AUDIO),
                E("Index", text=str(index)),
            ]),
            E("TransitionItems", {"Version": "3"}, children=[
                E("MediaType", text=GUID_MEDIA_AUDIO),
                E("Index", text=str(index)),
            ]),
        ]))
        track_obj.el(E("AudioTrack", {"Version": "12"}, children=[
            E("ComponentOwner", {"Version": "1"}, children=[E("Components", {"ref": chain})]),
            E("Panner", {"ref": panner}),
            E("NextPannerID", text="4294967279"),
            E("ID", text=str(uuid.uuid4())),
        ]))

    audio_group.el(E("TrackGroup", {"Version": "1"}, children=[
        E("Tracks", {"Version": "1"}, children=[
            E("Track", {"Index": str(i), "ref": track}) for i, track in enumerate(audio_tracks)
        ]),
        E("FrameRate", text=str(audio_ticks)),
        E("NextTrackID", text=str(audio_track_count + 2)),
    ]))
    audio_group.ref("MasterTrack", audio_mix_track)
    audio_group.txt("AutomationSafeFlags", "0")
    audio_group.txt("NumAdaptiveChannels", "2")
    audio_group.txt("ID", str(uuid.uuid4()))
    audio_mix_track.el(E("AudioTrack", {"Version": "12"}, children=[
        E("ComponentOwner", {"Version": "1"}, children=[E("Components", {"ref": mix_chain})]),
        E("Panner", {"ref": default_panner}),
        E("SubType", text="3"),
        E("Assign", text="0"),
        E("NextPannerID", text="4294967279"),
        E("ID", text=str(uuid.uuid4())),
    ]))
    audio_mix_track.el(E("Track", {"Version": "4"}, children=[
        E("MediaType", text=GUID_MEDIA_AUDIO),
        E("Index", text="0"),
        E("ID", text="1"),
    ]))
    audio_mix_track.ref("Inlet", inlet)

    data_group = Obj("DataTrackGroup")
    data_group.el(E("TrackGroup", {"Version": "1"}, children=[
        E("FrameRate", text=str(frame_ticks)),
        E("NextTrackID", text="1"),
    ]))

    sequence.el(E("Node", {"Version": "1"}, children=[E("Properties", {"Version": "1"}, children=[
        E("AECompID", text=str(comp.get("id") or "")),
        E("MZ.Sequence.VideoTimeDisplayFormat", text="101"),
        E("MZ.ZeroPoint", text="0"),
    ])]))
    sequence.el(E("PersistentGroupContainer", {"Version": "1"}, children=[
        E("LinkContainer", {"Version": "1"})
    ]))
    sequence.el(E("TrackGroups", {"Version": "1"}, children=[
        E("TrackGroup", {"Version": "1", "Index": "0"}, children=[
            E("First", text=GUID_MEDIA_VIDEO), E("Second", {"ref": video_group})]),
        E("TrackGroup", {"Version": "1", "Index": "1"}, children=[
            E("First", text=GUID_MEDIA_AUDIO), E("Second", {"ref": audio_group})]),
        E("TrackGroup", {"Version": "1", "Index": "2"}, children=[
            E("First", text=GUID_MEDIA_DATA), E("Second", {"ref": data_group})]),
    ]))
    sequence.txt("Name", str(comp.get("name") or "Sequence"))

    # --- assemblage du document (ordre du fichier de référence) ---------------
    document = [
        E("Project", {"ref": project}),
        project,
        root,
        settings,
        *compile_settings,
        scratch,
        ingest,
        workspace,
        *media_items,
        sequence_item,
        vs_global,
        as_global,
        vcs_global,
        acs_global,
        dummy_capture,
        default_seq,
        *[field for pair in compile_pairs for field in (pair[0], pair[1])],
        *[entry["master"] for entry in masters],
        seq_master,
        global_pair_vs,
        global_pair_as,
        *[field for pair in compile_pairs for field in (pair[2], pair[3])],
        *[field for entry in masters for field in entry["children"]],
        seq_logging,
        seq_audio_chain,
        seq_audio_clip,
        seq_video_clip,
        seq_ccgvs,
        *[field for entry in masters for field in entry["sources"]],
        seq_audio_source,
        seq_secondary[0],
        seq_secondary[1],
        seq_video_source,
        seq_ccvs,
        *[field for bundle, entry in zip(bundles.values(), masters)
          for field in (bundle["media_obj"], *entry["ccs"])],
        sequence,
        seq_ccs[0],
        seq_ccs[1],
        *[field for bundle in bundles.values()
          for field in (bundle["audio_stream"], bundle["video_stream"]) if field is not None],
        video_group,
        audio_group,
        data_group,
        *video_tracks,
        video_group_chain,
        *audio_tracks,
        audio_mix_track,
        *[entry["video_item"] for entry in entries if entry["video_item"] is not None],
        *[entry["audio_item"] for entry in audio_entries],
        *[field for index in range(audio_track_count)
          for field in (track_chains[index], panners[index])],
        mix_chain,
        default_panner,
        inlet,
        *[field for entry in entries if entry["video_chain"] is not None
          for field in (entry["video_chain"], entry["video_subclip"])],
        *[field for entry in audio_entries
          for field in (entry["audio_chain"], entry["audio_subclip"])],
        *[field for index in range(audio_track_count)
          for field in (faders[index], meters[index], balance_params[index])],
        faders[audio_track_count],
        meters[audio_track_count],
        *[field for entry in entries for field in entry["mask_filters"]],
        *[field for entry in entries if entry["opacity_filter"] is not None
          for field in (entry["opacity_filter"],)],
        *[field for entry in entries if entry["blur_filters"]
          for field in entry["blur_filters"]],
        *[field for entry in entries
          if entry["motion_filter"] is not None and entry["segment_video_clip"] is not None
          for field in (entry["motion_filter"], entry["segment_video_clip"])],
        *[field for entry in entries
          if entry["motion_filter"] is None and entry["segment_video_clip"] is not None
          for field in (entry["segment_video_clip"],)],
        *[field for entry in entries if entry.get("time_remap") is not None
          for field in (entry["time_remap"]["param"], entry["time_remap"]["remap"])],
        *[field for entry in audio_entries
          for field in (entry["audio_filter"], entry["segment_audio_clip"])
          if field is not None],
        *[field for pair in fader_params for field in pair],
        *[param for entry in entries for param in entry["params"] + entry["extra_params"]],
        *[field for entry in audio_entries
          for field in entry["audio_tail_params"] + entry["audio_tail_secondary"]],
    ]

    xml_text = serialize(document)
    tmp_path = out_path + ".tmp"
    with gzip.open(tmp_path, "wb", compresslevel=9) as handle:
        handle.write(xml_text.encode("utf-8"))
    os.replace(tmp_path, out_path)
    if xml_dump:
        with open(out_path + ".xml", "w", encoding="utf-8") as handle:
            handle.write(xml_text)

    report = {
        "media": len(bundles),
        "tracks": track_count,
        "items": len(entries),
        "audio_items": len(audio_entries),
        "keyframed": sum(1 for entry in entries if entry.get("keyframed")),
        "time_remaps": sum(1 for entry in entries if entry.get("time_remap") is not None),
        "frame_holds": sum(1 for entry in entries if entry.get("frame_hold") is not None),
        "warnings": warnings,
        "missing": missing,
    }
    log(f"{os.path.basename(ec_path)} → {os.path.basename(out_path)} : "
        f"{report['media']} média(s), {report['tracks']} piste(s), {report['items']} item(s), "
        f"{report['audio_items']} audio, {report['keyframed']} animé(s), "
        f"{report['time_remaps']} time remap(s), {report['frame_holds']} gel(s)")
    return report


# ------------------------------------------------------------- vérification


def read_prproj(path: str):
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw.decode("utf-8"))
    by_id, by_uid = {}, {}
    for element in root:
        if element.get("ObjectID") is not None:
            by_id[element.get("ObjectID")] = element
        if element.get("ObjectUID") is not None:
            by_uid[element.get("ObjectUID")] = element
    return root, by_id, by_uid


def resolve(element, by_id, by_uid):
    if element is None:
        return None
    if "ObjectRef" in element.attrib:
        return by_id.get(element.get("ObjectRef"))
    if "ObjectURef" in element.attrib:
        return by_uid.get(element.get("ObjectURef"))
    return None


def check_references(root, by_id, by_uid) -> list[str]:
    problems = []
    for element in root:
        for node in element.iter():
            for key, value in node.attrib.items():
                if key == "ObjectRef" and value not in by_id:
                    problems.append(f"{element.tag}: ObjectRef={value}")
                elif key == "ObjectURef" and value not in by_uid:
                    problems.append(f"{element.tag}: ObjectURef={value}")
    return problems


def sequence_snapshot(root, by_id, by_uid) -> list[dict]:
    """Modèle lisible du fichier : séquences → pistes → items (temps, plage média, Motion)."""
    sequences = []
    for element in root:
        if element.tag != "Sequence":
            continue
        tracks = []
        track_groups = element.find("TrackGroups")
        for group_ref in (track_groups.findall("TrackGroup") if track_groups is not None else []):
            group = resolve(group_ref.find("Second"), by_id, by_uid)
            if group is None or group.tag not in ("VideoTrackGroup", "AudioTrackGroup"):
                continue
            kind = "V" if group.tag == "VideoTrackGroup" else "A"
            frame_rect = (group.findtext("FrameRect") or "0,0,0,0").split(",")
            dims = (frame_rect[2], frame_rect[3]) if len(frame_rect) == 4 else ("0", "0")
            tracks_list = group.find("TrackGroup/Tracks")
            for track_ref in (tracks_list.findall("Track") if tracks_list is not None else []):
                track = resolve(track_ref, by_id, by_uid)
                if track is None:
                    continue
                items = []
                for item_ref in track.findall("ClipTrack/ClipItems/TrackItems/TrackItem"):
                    item = resolve(item_ref, by_id, by_uid)
                    if item is None:
                        continue
                    info = {"start": 0, "end": 0, "in": 0, "out": 0, "params": {},
                            "masks": 0, "effects": [], "time_remap": None,
                            "frame_hold": None}
                    track_item = item.find("ClipTrackItem/TrackItem")
                    if track_item is not None:
                        info["start"] = int(track_item.findtext("Start") or 0)
                        info["end"] = int(track_item.findtext("End") or 0)
                    subclip = resolve(item.find("ClipTrackItem/SubClip"), by_id, by_uid)
                    if subclip is not None:
                        clip = resolve(subclip.find("Clip"), by_id, by_uid)
                        if clip is not None:
                            inner = clip.find("Clip")
                            info["in"] = int(inner.findtext("InPoint") or 0)
                            info["out"] = int(inner.findtext("OutPoint") or 0)
                            hold = clip.findtext("FrameHoldStart")
                            info["frame_hold"] = int(hold) if hold else None
                            remap_ref = inner.find("TimeRemapping")
                            remap = resolve(remap_ref, by_id, by_uid)
                            param = (resolve(remap.find("Keyframes"), by_id, by_uid)
                                     if remap is not None else None)
                            if param is not None:
                                info["time_remap"] = {
                                    "upper": param.findtext("UpperBound"),
                                    "keys": param.findtext("Keyframes"),
                                }
                    chain = resolve(item.find("ClipTrackItem/ComponentOwner/Components"),
                                    by_id, by_uid)
                    if chain is not None:
                        for component_ref in chain.findall("ComponentChain/Components/Component"):
                            filter_obj = resolve(component_ref, by_id, by_uid)
                            if filter_obj is None:
                                continue
                            match = filter_obj.findtext("MatchName") or ""
                            if match == MASK2_MATCH_NAME:
                                info["masks"] += 1
                            elif match not in ("AE.ADBE Motion", "AE.ADBE Opacity"):
                                info["effects"].append(match or filter_obj.tag)
                            for sub_ref in filter_obj.findall("SubComponents/SubComponent"):
                                sub = resolve(sub_ref, by_id, by_uid)
                                sub_match = sub.findtext("MatchName") if sub is not None else None
                                if sub_match == MASK2_MATCH_NAME:
                                    info["masks"] += 1
                            for param_ref in filter_obj.findall("Component/Params/Param"):
                                param = resolve(param_ref, by_id, by_uid)
                                if param is None:
                                    continue
                                info["params"][int(param_ref.get("Index"))] = {
                                    "start": param.findtext("StartKeyframe"),
                                    "keys": param.findtext("Keyframes"),
                                }
                    selection = resolve(item.find("SelectionComponents"), by_id, by_uid)
                    if selection is not None:
                        for component_ref in selection.findall(
                                "ComponentChain/Components/Component"):
                            # `SelectionComponents` porte le conteneur intrinsèque (tracé
                            # vide) : seuls ses éventuels sous-masques sont réels.
                            mask_obj = resolve(component_ref, by_id, by_uid)
                            if (mask_obj is not None
                                    and mask_obj.findtext("MatchName") == MASK2_MATCH_NAME):
                                for sub_ref in mask_obj.findall("SubComponents/SubComponent"):
                                    sub = resolve(sub_ref, by_id, by_uid)
                                    if (sub is not None
                                            and sub.findtext("MatchName") == MASK2_MATCH_NAME):
                                        info["masks"] += 1
                    items.append(info)
                tracks.append({"kind": kind, "items": items, "dims": dims})
        sequences.append({"name": element.findtext("Name") or "", "tracks": tracks})
    return sequences


def parse_point(start_keyframe: str | None):
    if not start_keyframe:
        return None
    parts = start_keyframe.split(",")
    if len(parts) < 2 or ":" not in parts[1]:
        return None
    x, _, y = parts[1].partition(":")
    try:
        return float(x), float(y)
    except ValueError:
        return None


def verify_summary(path: str) -> str:
    root, by_id, by_uid = read_prproj(path)
    problems = check_references(root, by_id, by_uid)
    sequences = sequence_snapshot(root, by_id, by_uid)
    lines = [f"Vérification de {os.path.basename(path)} :"]
    if problems:
        lines.append(f"  !! {len(problems)} référence(s) non résolue(s), ex. {problems[:3]}")
    else:
        lines.append("  références : toutes résolues")
    for seq in sequences:
        video_tracks = [track for track in seq["tracks"] if track["kind"] == "V"]
        audio_tracks = [track for track in seq["tracks"] if track["kind"] == "A"]
        video = video_tracks[0] if video_tracks else {"items": [], "dims": ("0", "0")}
        n_video = sum(len(track["items"]) for track in video_tracks)
        n_audio = sum(len(track["items"]) for track in audio_tracks)
        n_remaps = sum(1 for track in video_tracks for item in track["items"]
                       if item.get("time_remap"))
        n_holds = sum(1 for track in video_tracks for item in track["items"]
                      if item.get("frame_hold"))
        lines.append(f"  séquence « {seq['name']} » : {n_video} clip(s) vidéo sur "
                     f"{len(video_tracks)} piste(s), {n_audio} clip(s) audio sur "
                     f"{len(audio_tracks)} piste(s), {n_remaps} time remap(s), "
                     f"{n_holds} gel(s), cadre {video['dims'][0]}x{video['dims'][1]}")
        for item in video["items"][:3]:
            state = "animé" if item["params"].get(0, {}).get("keys") else "fixe"
            extras = f", {item['masks']} masque(s)" if item["masks"] else ""
            if item.get("time_remap"):
                extras += ", time remap"
            if item.get("frame_hold"):
                extras += ", gel"
            if item["effects"]:
                extras += ", effets " + ", ".join(item["effects"])
            lines.append(
                f"    [{item['start'] / TICKS_PER_SECOND:8.3f} .. "
                f"{item['end'] / TICKS_PER_SECOND:8.3f}] média "
                f"{item['in'] / TICKS_PER_SECOND:8.3f}..{item['out'] / TICKS_PER_SECOND:8.3f} "
                f"{state}{extras}, Position={parse_point(item['params'].get(0, {}).get('start'))}")
        if len(video["items"]) > 3:
            lines.append(f"    … et {len(video['items']) - 3} autre(s) clip(s)")
    return "\n".join(lines)


def compare_prproj(ours_path: str, ref_path: str) -> None:
    our_root, our_id, our_uid = read_prproj(ours_path)
    ref_root, ref_id, ref_uid = read_prproj(ref_path)
    ours = sequence_snapshot(our_root, our_id, our_uid)
    refs = sequence_snapshot(ref_root, ref_id, ref_uid)
    ref_by_name = {seq["name"]: seq for seq in refs}
    print(f"Comparaison avec la référence « {os.path.basename(ref_path)} » :")
    for seq in ours:
        ref = ref_by_name.get(seq["name"]) or (refs[0] if len(refs) == 1 else None)
        if ref is None:
            print(f"  « {seq['name']} » : pas de séquence correspondante dans la référence")
            continue
        for ours_track, ref_track in zip(seq["tracks"], ref["tracks"]):
            if ours_track["kind"] != ref_track["kind"]:
                continue
            ours_items, ref_items = ours_track["items"], ref_track["items"]
            if len(ours_items) != len(ref_items):
                print(f"  piste {ours_track['kind']} : {len(ours_items)} clip(s) contre "
                      f"{len(ref_items)} dans la référence — à contrôler")
                continue
            max_shift = 0
            same_values = total_values = 0
            mismatches = []
            for ours_item, ref_item in zip(ours_items, ref_items):
                max_shift = max(max_shift, abs(ours_item["start"] - ref_item["start"]),
                                abs(ours_item["end"] - ref_item["end"]))
                for slot in (0, 5):
                    ours_point = parse_point(ours_item["params"].get(slot, {}).get("start"))
                    ref_point = parse_point(ref_item["params"].get(slot, {}).get("start"))
                    if ours_point is None or ref_point is None:
                        continue
                    total_values += 1
                    if (abs(ours_point[0] - ref_point[0]) < 1e-6
                            and abs(ours_point[1] - ref_point[1]) < 1e-6):
                        same_values += 1
                    elif len(mismatches) < 3:
                        mismatches.append(f"Pos/Anchor {ours_point} contre {ref_point}")
            print(f"  piste {ours_track['kind']} : {len(ours_items)} clip(s) ; Position/Anchor "
                  f"identiques {same_values}/{total_values} (valeurs normalisées, tol 1e-6) ; "
                  f"écart de temps max {max_shift / TICKS_PER_SECOND:.3f} s "
                  f"(la référence a subi le décalage 24→25 fps de la chaîne Adobe)")
            for line in mismatches:
                print(f"      écart : {line}")


# ------------------------------------------------------- compatibilité Premiere


def mask_unsupported_reason(mask: dict) -> str | None:
    """Ce que l'écriture .prproj ne reprend pas d'un masque .ecproj, sinon None."""
    if not mask_is_applied(mask):
        return None            # EffectCraft ne l'applique pas non plus : rien à reprendre
    kind = mask.get("kind") or {}
    mode = str(kind.get("mode") or "Add")
    if mode not in MASK_TYPE_ENUM:
        return f"mode {mode} non repris (v1)"
    if kind.get("roto_bezier"):
        return "roto-bezier non repris (v1)"
    if mask_path_value(mask_children(mask).get("path")).get("feather"):
        return "feather variable (points) non repris (v1)"
    if str(kind.get("feather_falloff") or "Smooth") != "Smooth":
        return f"feather « {kind.get('feather_falloff')} » non repris (v1)"
    if str(kind.get("motion_blur") or "SameAsLayer") != "SameAsLayer":
        return f"motion blur « {kind.get('motion_blur')} » non repris (v1)"
    return None


def pp_unsupported_reasons(ec: dict) -> list[str]:
    """Éléments du .ecproj que l'écriture .prproj ne reprend pas (masques, effets, modes)."""
    reasons: list[str] = []
    for item in (ec.get("items") or {}).values():
        kind = item.get("kind") or {}
        if kind.get("type") != "Comp":
            continue
        for layer in kind.get("layers") or []:
            if (layer.get("source") or {}).get("type") == "Null":
                continue
            layer_name = layer.get("name")
            blend = str(layer.get("blend_mode") or "Normal")
            if blend not in BLEND_MODE_MAP:
                reasons.append(f"calque « {layer_name} » : mode de fusion « {blend} » non repris (v1)")
            for mask in conv.enabled_masks(layer):
                reason = mask_unsupported_reason(mask)
                if reason:
                    reasons.append(f"masque « {mask.get('name')} » de « {layer_name} » : "
                                   f"{reason}")
            for effect in conv.collect_effects(layer):
                if effect.get("enabled") is False:
                    continue
                if (effect.get("kind") or {}).get("effect") not in PP_BLUR_EFFECTS:
                    reasons.append(f"effet « {effect.get('name')} » de « {layer_name} » : "
                                   f"non repris par Premiere (v1)")
    return reasons


def premiere_skip_reason(ec_path: str) -> str | None:
    """Raison de ne pas exporter ce .ecproj en .prproj, sinon None.

    Masques et effets sont désormais écrits (masque « Add » non inversé sous l'Opacité du
    clip, Gaussian Blur en `AE.Impact_Blur_FX`) : seuls les cas non repris – autre mode,
    inversion, options exotiques, effet hors table – bloquent l'export, pour ne pas produire
    un .prproj incomplet (seul le .aep est alors écrit).
    """
    try:
        with open(ec_path, "r", encoding="utf-8") as handle:
            reasons = pp_unsupported_reasons(json.load(handle))
    except Exception:
        return None
    return " ; ".join(reasons) if reasons else None


def remove_stale_prproj(ec_path: str) -> str | None:
    """Supprime le .prproj voisin d'un projet non exportable ; retourne le chemin supprimé."""
    stale = os.path.splitext(ec_path)[0] + ".prproj"
    if os.path.isfile(stale):
        os.remove(stale)
        return stale
    return None


def report_skip(ec_path: str, reason: str, log) -> None:
    """Signale un projet sauté et retire le .prproj obsolète éventuel."""
    removed = remove_stale_prproj(ec_path)
    print(f"  ignoré : {reason} — export .aep uniquement"
          + (" ; .prproj obsolète supprimé" if removed else ""))
    log(f"[prproj] {os.path.basename(ec_path)} : ignoré ({reason})"
        + (" ; .prproj obsolète supprimé" if removed else ""))


# ------------------------------------------------------------------- bureau


def log_line(message: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def scan_root(root: str, filters) -> list[str]:
    """Dossiers projet enfants directs de `root` — même détection que le panel et que
    l'export .aep : nom contenant un des filtres (insensible à la casse) et sous-dossier `docs/`."""
    needles = [needle.strip().lower() for needle in filters if len(needle.strip()) >= 2]
    folders = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return folders
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
            continue
        if needles and not any(needle in entry.name.lower() for needle in needles):
            continue
        if not os.path.isdir(os.path.join(entry.path, "docs")):
            continue
        folders.append(entry.path)
    return sorted(folders)


def collect_ecproj(folders) -> list[str]:
    found = set()
    for folder in folders:
        for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda exc: None):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for name in filenames:
                if name.lower().endswith(".ecproj"):
                    found.add(os.path.join(dirpath, name))
    return sorted(found)


def run_desktop(args, maps) -> int:
    gui = (not args.no_gui
           and shutil.which("zenity") is not None
           and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))
    if not os.path.isdir(ROOT):
        print(f"Racine introuvable : {ROOT}", file=sys.stderr)
        return 2
    files = collect_ecproj(scan_root(ROOT, NAME_FILTERS))
    log_line(f"--- {len(files)} projet(s), mapping={maps or 'aucun'} ---")
    if not files:
        text = f"Aucun .ecproj trouvé sous {ROOT}."
        if gui:
            subprocess.run(["zenity", "--info", "--title", "Export PRPROJ", "--text", text])
        else:
            print(text)
        return 0
    progress = None
    if gui:
        progress = subprocess.Popen(
            ["zenity", "--progress", "--title", "Export PRPROJ",
             "--text", "Conversion des .ecproj…", "--percentage=0", "--auto-close"],
            stdin=subprocess.PIPE, text=True)
    conversions = skipped = failures = 0
    for index, src in enumerate(files):
        if progress is not None:
            try:
                progress.stdin.write(f"#{int(index * 100 / len(files))}\n")
                progress.stdin.write(f"{os.path.basename(src)}\n")
                progress.stdin.flush()
            except (BrokenPipeError, AttributeError):
                progress = None
        out = os.path.splitext(src)[0] + ".prproj"
        reason = None if args.force else premiere_skip_reason(src)
        if reason:
            skipped += 1
            report_skip(src, reason, log_line)
            continue
        try:
            report = convert(src, out, maps, args.skip_missing, args.xml_dump, log_line,
                             audio_fallback=args.audio_fallback)
            conversions += 1
            print(f"OK  {src} → {out} ({report['items']} item(s))")
        except Exception as exc:  # pragma: no cover - dépend des données
            failures += 1
            print(f"ÉCHEC {src} : {exc}", file=sys.stderr)
            log_line(f"ÉCHEC {src} : {exc}")
    if progress is not None:
        try:
            progress.stdin.close()
        except (BrokenPipeError, AttributeError):
            pass
        progress.wait()
    summary = f"{conversions} projet(s) converti(s), {failures} échec(s)"
    if skipped:
        summary += (f", {skipped} ignoré(s) (éléments non repris : export .aep uniquement)")
    summary += "."
    if gui:
        subprocess.run(["zenity", "--info", "--title", "Export PRPROJ", "--text", summary])
    else:
        print(summary)
    return 1 if failures else 0


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convertit un projet EffectCraft (.ecproj) en projet Premiere Pro (.prproj).")
    parser.add_argument("input", nargs="?", help="fichier .ecproj, ou dossier avec --all")
    parser.add_argument("-o", "--out", help="chemin du .prproj de sortie")
    parser.add_argument("--all", action="store_true", help="convertit tous les *.ecproj du dossier")
    parser.add_argument("--path-map", action="append", default=None, metavar="SRC=DST",
                        help="réécrit le préfixe des chemins de médias (répétable)")
    parser.add_argument("--no-path-map", action="store_true",
                        help="n'applique aucune réécriture (chemins Linux écrits tels quels)")
    parser.add_argument("--skip-missing", action="store_true",
                        help="résume les médias absents au lieu de les lister")
    parser.add_argument("--force", action="store_true",
                        help="exporte même si des éléments ne sont pas repris (masque non "
                             "« Add », inversé, effet inconnu) : le .prproj sera incomplet")
    parser.add_argument("--audio-fallback", action="store_true",
                        help="si aucun calque n'est audible sur un segment, reprend l'audio "
                             "du calque vidéo le plus bas (V1) au lieu de laisser le silence")
    parser.add_argument("--verify", action="store_true", help="relit le .prproj produit")
    parser.add_argument("--compare-with", metavar="REF.prproj",
                        help="compare le résultat à un .prproj de référence")
    parser.add_argument("--xml-dump", action="store_true",
                        help="écrit aussi le XML décompressé à côté du .prproj")
    parser.add_argument("--no-gui", action="store_true",
                        help="force la sortie texte (mode bureau sans fenêtre)")
    args = parser.parse_args()

    if args.no_path_map and args.path_map:
        parser.error("--path-map et --no-path-map sont incompatibles")
    if args.path_map is None:
        # sans indication : mapping du mode bureau (les .prproj sont ouverts sous Windows)
        maps: list[tuple[str, str]] = [] if args.no_path_map else list(PATH_MAP)
    else:
        maps = []
        for mapping in args.path_map:
            if "=" not in mapping:
                parser.error(f"--path-map attend SRC=DST, reçu : {mapping}")
            src, dst = mapping.split("=", 1)
            maps.append((src, dst))

    if not args.input:
        return run_desktop(args, maps)

    jobs: list[tuple[str, str]] = []
    if args.all:
        if not os.path.isdir(args.input):
            parser.error("--all attend un dossier")
        if args.out:
            parser.error("-o et --all sont incompatibles")
        jobs = [(path, os.path.splitext(path)[0] + ".prproj")
                for path in collect_ecproj([args.input])]
    else:
        if not os.path.isfile(args.input):
            parser.error(f"fichier introuvable : {args.input}")
        jobs.append((args.input, args.out or os.path.splitext(args.input)[0] + ".prproj"))

    if not jobs:
        print("Aucun .ecproj à convertir.")
        return 0

    log_line(f"--- {len(jobs)} projet(s), mapping={maps or 'aucun'} ---")
    failures = 0
    skipped = 0
    for src, out in jobs:
        print(f"→ {os.path.basename(src)}  ⇒  {out}")
        reason = None if args.force else premiere_skip_reason(src)
        if reason:
            skipped += 1
            report_skip(src, reason, log_line)
            continue
        try:
            report = convert(src, out, maps, args.skip_missing, args.xml_dump, log_line,
                             audio_fallback=args.audio_fallback)
        except Exception as exc:  # pragma: no cover - dépend des données
            failures += 1
            print(f"  ÉCHEC : {exc}", file=sys.stderr)
            log_line(f"ÉCHEC {src} : {exc}")
            continue
        detail = (f"{report['media']} média(s), {report['tracks']} piste(s), "
                  f"{report['items']} item(s)")
        if report["audio_items"]:
            detail += f", {report['audio_items']} audio"
        if report["keyframed"]:
            detail += f", {report['keyframed']} animé(s)"
        if report.get("time_remaps"):
            detail += f", {report['time_remaps']} time remap(s)"
        if report.get("frame_holds"):
            detail += f", {report['frame_holds']} gel(s)"
        print(f"  {detail} → {out}")
        if report["missing"]:
            if args.skip_missing:
                print(f"  ! {len(report['missing'])} média(s) absent(s)")
            else:
                for line in report["missing"]:
                    print(f"  ! {line}")
        for line in report["warnings"]:
            print(f"  ! {line}")
        if args.verify:
            try:
                print(verify_summary(out))
            except Exception as exc:
                print(f"  vérification impossible : {exc}", file=sys.stderr)
        if args.compare_with:
            try:
                compare_prproj(out, args.compare_with)
            except Exception as exc:
                print(f"  comparaison impossible : {exc}", file=sys.stderr)

    if skipped:
        print(f"{skipped} projet(s) ignoré(s) (éléments non repris : export .aep uniquement).")
        log_line(f"{skipped} projet(s) ignoré(s) (éléments non repris)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
