# Guide d'automatisation Headless & CLI d'EffectCraft

> **⚠️ Compatibilité EffectCraft 0.8.0 (2026-10-11)** : ce guide décrit la **0.7.0**. En 0.8.0, un script ne peut plus activer lui-même `scripting.allowScriptsWriteFiles` (garde-fou « Scripts can't change what scripts are allowed to do ») et le CLI headless n'honore pas la préférence utilisateur : le pilotage headless d'un script qui lit/écrit des fichiers est bloqué. L'étape 8 du workflow gère les deux cas automatiquement (`STEP8_MODE=auto`) : headless ≤ 0.7.0, `--bridge` sur l'application ouverte en 0.8.0+. Détail et preuves : `../../docs/audits/effectcraft-0.8.0-compatibilite.md`.

Ce document synthétise les capacités de l'outil CLI d'EffectCraft ([apps/effectcraft-cli](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/apps/effectcraft-cli)), ses différents modes d'interaction sans interface graphique (*headless*), ses possibilités d'animation et de rendu, ainsi que des exemples concrets d'automatisation de scripts After Effects / ScriptUI Panels (notamment le cas pratique `MediaSolution.jsx`).

---

## 1. Architecture générale : *"Everything is an Engine Command"*

EffectCraft repose sur une séparation stricte entre l'interface utilisateur et le moteur de composition ([crates/engine](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/crates/engine)) :

- **Commandes unifiées** : Tous les menus, raccourcis, manipulations de calques, animations ou rendus passent par des commandes moteur standardisées avec des identifiants stables et des paramètres en JSON (`comp.new`, `layer.newText`, `prop.addKey`, `renderQueue.render`, etc.).
- **Exécution 100% autonome (*headless*)** : L'outil `effectcraft-cli` s'exécute directement en mémoire sans nécessiter de serveur d'affichage X11 ou Wayland, sans fenêtre, avec un démarrage quasi instantané.
- **Rendu CPU et GPU** : Par défaut, le moteur utilise le rendu logiciel sur CPU. L'option `--gpu` active l'accélérateur GPU (Mercury GPU Acceleration) en mode headless lorsque le matériel le permet.
- **Sécurité et intégrité** : Toutes les opérations sont journalisées, transactionnelles et annulables. Le système ne panique jamais et gère les erreurs de manière structurée.

---

## 2. Les 3 modes d'interaction sans UI

`effectcraft-cli` propose trois méthodes pour créer, modifier ou animer des projets :

```
┌─────────────────────────────────────────────────────────────────┐
│                       effectcraft-cli                           │
└──────────────┬──────────────────┬─────────────────┬─────────────┘
               │                  │                 │
               ▼                  ▼                 ▼
     1. Commandes CLI     2. Scripting JS     3. Serveur MCP
       (exec, run, set)     (script *.jsx)    (Claude / Agents)
               │                  │                 │
               └──────────────────┼─────────────────┘
                                  ▼
                    Moteur EffectCraft (Headless)
                                  │
                                  ▼
               Export : Vidéo (H.264/ProRes), Lottie, PNG
```

### 2.1 Mode 1 : Commandes CLI atomiques & chaînées (`exec`, `run`, `set`, `props`)

Idéal pour les scripts Shell / Python, l'intégration continue ou les opérations simples par lots.

- **`effectcraft-cli exec <command-id> [--params JSON]`** : Exécute une commande unique.
  ```bash
  # Créer un projet vide avec une comp 1080p60 de 5 secondes
  effectcraft-cli exec comp.new '{"name":"Main","width":1920,"height":1080,"frameRate":60,"duration":5}' --empty --save-as anim.ecproj
  ```
- **`effectcraft-cli run <id> <json> [<id> <json> ...]`** : Enchaîne plusieurs commandes dans le même processus de façon transactionnelle :
  ```bash
  effectcraft-cli run anim.ecproj \
    layer.newSolid '{"name":"Fond","color":"#0f172a"}' \
    layer.newText '{"name":"Titre","text":"Motion Design","size":120}' \
    --save
  ```
- **`effectcraft-cli props <comp> <layer> [--flat]`** : Inspecte la hiérarchie de propriétés d'un calque et affiche les chemins utilisables (`transform/position`, `effects/#1/blurriness`, etc.).
- **`effectcraft-cli set <comp> <layer> <path> <valeur> [--time S] [--expression E]`** : Modifie une propriété statique, pose une image clé (*keyframe*) ou affecte une expression dynamique :
  ```bash
  # Animer la position entre t=0s et t=1.5s
  effectcraft-cli set Main Titre transform/position '[960,1200]' --time 0 anim.ecproj --save
  effectcraft-cli set Main Titre transform/position '[960,540]' --time 1.5 anim.ecproj --save

  # Ajouter une expression dynamique
  effectcraft-cli set Main Titre transform/rotation 0 --expression 'time * 45' anim.ecproj --save
  ```

---

### 2.2 Mode 2 : Scripting After Effects JavaScript (`effectcraft-cli script`)

Le scripting est l'approche la plus souple pour concevoir des animations complexes de A à Z. EffectCraft intègre le moteur JavaScript [Boa](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/crates/script) et reproduit fidèlement le modèle objet d'After Effects documenté dans le guide de scripting officiel :

- **Objets & API disponibles** ([crates/script/src/prelude.js](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/crates/script/src/prelude.js)) :
  - `app.project`, `CompItem`, `LayerCollection`
  - Calques : `comp.layers.addText()`, `addSolid()`, `addShape()`, `addNull()`, `addCamera()`, `addLight()`, `add(footageItem)`
  - Animation : `property.setValueAtTime(t, v)`, `setValuesAtTimes(times, values)`
  - Lissage de vitesse : `KeyframeEase(speed, influence)`, `setTemporalEaseAtKey(keyIndex, inEase, outEase)`
  - Interpolation : `setInterpolationTypeAtKey(keyIndex, type)` (`KeyframeInterpolationType.LINEAR`, `BEZIER`, `HOLD`)
  - Expressions : `property.expression = "wiggle(5, 20);"`
  - Pont vers le moteur : `app.run(commandId, paramsJson)` pour appeler n'importe quelle commande interne depuis JS.

```bash
# Exécution d'un script JSX autonome
effectcraft-cli script mon_animation.jsx --save-as projet.ecproj

# Évaluation d'une expression JS rapide
effectcraft-cli script --eval 'app.project.item(1).numLayers' projet.ecproj --json
```

---

### 2.3 Mode 3 : Serveur MCP (*Model Context Protocol*) pour agents IA

Pour piloter EffectCraft via des agents intelligents (Claude Code, Antigravity IDE, etc.) en JSON-RPC 2.0 sur `stdio` :
- Outils disponibles : `add_keyframe`, `add_effect`, `batch` (avec variables `$1.layer`), `run_script`, `render_frame`, `get_layer`, `save_project`, etc.
- Option `--autosave` : génère des points de contrôle automatiques sur le disque à chaque action sale, empêchant toute perte de travail.

```bash
effectcraft-cli mcp --autosave
```

---

### 2.4 Mode Bridge (`--bridge PORT`)

Toutes les commandes CLI ou scripts peuvent cibler une instance graphique en cours d'exécution démarrée avec `effectcraft --control 9877` via l'option `--bridge 9877`. Cela permet de voir en temps réel dans l'interface les calques et animations créés par votre script CLI.

---

## 3. Capacités de création d'animation sans l'UI

EffectCraft permet de manipuler l'ensemble du graphe d'animation 2D et 3D de manière purement procédurale :

| Domaine | Fonctionnalités accessibles en CLI / Scripting |
|---|---|
| **Calques** | **Textes** (mise en forme complète, polices système et embarquées, justification, Range Selectors / Text Animators), **Formes vectorielles** (rectangles, ellipses, polygones, étoiles, tracés de Bézier), **Solides**, **Calques de réglage (Adjustment)**, **Nulls**, **Caméras 3D**, **Lumières 3D** (Point, Spot, Parallel, Ambient, Environment), **Précompositions**, **Médias** (vidéos, séquences d'images, SVG, PSD). |
| **Animation vectorielle** | Opérateurs de formes : **Trim Paths** (réduction de tracés), **Repeater** (répéteur), **Round Corners**, contours, dégradés linéaires et radiaux. |
| **Animation temporelle** | Keyframing sub-frame, courbes de Bézier, interpolation spatiale et temporelle, Easy Ease, réglage fin vélocité/influence, Time Remapping. |
| **Expressions** | Moteur d'expressions JavaScript After Effects complet (`time`, `wiggle()`, `loopOut()`, `linear()`, `ease()`, calculs trigonométriques, `valueAtTime()`). |
| **Effets & Filtres** | **306 effets intégrés** ([docs/effects.md](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/docs/effects.md)), exécutables sur GPU ou CPU, 32-bit float, plus support de plug-ins WebAssembly (`.wasm`). |
| **Tracking & IA headless** | **3D Camera Tracker**, **Warp Stabilizer**, **Motion Tracking**, et détourage IA **Roto Brush (MobileSAM)** exécutables headlessly avec le paramètre `"wait": true`. |
| **Gabarits Essential Graphics** | Modèles paramétriques (`.ectemplate` / équivalent MOGRT) : exposition de contrôles (textes, couleurs, sliders), instanciation et personnalisation en batch. |

---

## 4. Exportation et rendu sans UI

Le sous-système d'export ([crates/render](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/crates/render) et [crates/export](file:///home/kidpixel/Media_Solution/effectcraft-0.9.2/crates/export)) permet de sortir l'animation dans le format de votre choix sans logiciel tiers :

### 4.1 Rendu vidéo complet (`render`)
```bash
effectcraft-cli render projet.ecproj --comp Main --out rendu.mp4 --format h264 --fps 60 --bitrate 10000
```
- **Codecs vidéo** : H.264, HEVC (H.265), AV1, Apple ProRes (Proxy, LT, Standard, HQ, 4444 et 4444 XQ avec couche Alpha), WebM (VP9 / AV1 avec Alpha), GIF animé.
- **Séquences d'images** : PNG, JPEG, TIFF, OpenEXR 32-bit float (RGBA multi-passes).
- **Options de contrôle** : `--fps`, `--start`, `--end`, `--resolution` (`full`, `half`, `third`, `quarter`), `--channels rgb|rgba`, `--quality`, `--gpu`.

### 4.2 Export vectoriel Lottie (`file.exportLottie`)
Pour exporter des animations légères destinées au web ou aux applications mobiles :
```bash
effectcraft-cli exec file.exportLottie '{"comp":"Main","path":"anim.json","includeExpressions":true}' projet.ecproj
```

### 4.3 Image fixe avec transparence (`render-frame`)
```bash
effectcraft-cli render-frame projet.ecproj --comp Main --time 1.5 --out vignette.png --transparent
```

---

## 5. Étude de cas : Automatiser un ScriptUI Panel (`MediaSolution.jsx`)

Le script `MediaSolution.jsx` (situé dans `~/.config/effectcraft/Scripts/ScriptUI Panels/MediaSolution.jsx`) sert habituellement à découper et recentrer automatiquement des vidéos au format vertical (9:16) via un panneau graphique.

### 5.1 Architecture headless native de `MediaSolution.jsx`
Le script contient déjà une détection automatique de l'environnement :
- Si `thisObj instanceof Panel` : il construit l'interface graphique ScriptUI.
- Si le script est exécuté hors interface (en CLI) : il n'affiche aucune fenêtre et expose l'objet **`$.global.mediasolutionEC`** avec les méthodes suivantes :
  - `runBatch(foldersJson, configJson, maxUnits)` : lance le traitement sur une liste de dossiers
  - `detect(rootPath, filter)` : auto-détection des dossiers de projets
  - `diagnostics(folderPath)` : analyse préalable des vidéos, CSV et tracking JSON

### 5.2 Préférence d'écriture sur le disque
Par défaut, EffectCraft restreint l'écriture de fichiers par les scripts. Pour que `app.project.save()` fonctionne, il suffit d'activer la préférence via la commande moteur :
```javascript
app.run("prefs.set", { key: "scripting.allowScriptsWriteFiles", value: true });
```

### 5.3 Wrapper Shell prêt à l'emploi (`mediasolution-cli.sh`)

Créez le script exécutable suivant :

```bash
#!/usr/bin/env bash
# mediasolution-cli.sh
# Automatisation CLI pour MediaSolution dans EffectCraft

set -e

MEDIASOLUTION_JSX="$HOME/.config/effectcraft/Scripts/ScriptUI Panels/MediaSolution.jsx"
TARGET_FOLDER="${1:-}"

if [ ! -f "$MEDIASOLUTION_JSX" ]; then
    echo "Erreur : $MEDIASOLUTION_JSX introuvable" >&2
    exit 1
fi

TMP_RUNNER=$(mktemp --suffix=.jsx)

cat << 'EOF' > "$TMP_RUNNER"
// 1. Autoriser l'écriture sur le disque
app.run("prefs.set", { key: "scripting.allowScriptsWriteFiles", value: true });

// 2. Charger le script MediaSolution
EOF

cat "$MEDIASOLUTION_JSX" >> "$TMP_RUNNER"

cat << EOF >> "$TMP_RUNNER"

// 3. Exécution du batch
(function() {
    var target = "$TARGET_FOLDER";
    var folders = [];

    if (target) {
        folders = [target];
    } else {
        // Auto-détection si aucun dossier n'est spécifié
        var detected = JSON.parse($.global.mediasolutionEC.detect("/mnt/cache", "camille"));
        folders = detected.found || [];
        writeLn("Dossiers détectés automatiquement : " + folders.join(", "));
    }

    if (folders.length === 0) {
        writeLn("Aucun dossier à traiter.");
        return;
    }

    var result = $.global.mediasolutionEC.runBatch(folders);
    writeLn("Résultat : " + result);
})();
EOF

echo "==> Lancement du traitement en mode headless..."
effectcraft-cli script "$TMP_RUNNER"

rm -f "$TMP_RUNNER"
echo "==> Traitement terminé."
```

Rendez-le exécutable :
```bash
chmod +x mediasolution-cli.sh
```

Utilisation :
```bash
# Traitement d'un dossier spécifique :
./mediasolution-cli.sh /mnt/cache/117_Camille

# Ou détection automatique complète :
./mediasolution-cli.sh
```

---

## 6. Exemple complet d'animation procédurale en JavaScript

Voici un exemple autonome `demo_motion.jsx` démontrant la création d'une composition, de calques de forme et de texte, d'animations avec lissage temporel, d'expressions et d'effets :

```javascript
// demo_motion.jsx
app.beginUndoGroup("Procedural Motion");

// 1. Créer la composition 1920x1080, 60fps, 4s
var comp = app.project.items.addComp("TitleCard", 1920, 1080, 1, 4, 60);

// 2. Calque de fond sombre
var bg = comp.layers.addSolid([0.06, 0.08, 0.12], "Background", 1920, 1080, 1);

// 3. Calque de texte animé
var textLayer = comp.layers.addText("EFFECTCRAFT");
textLayer.name = "Title";

var pos = textLayer.property("ADBE Transform Group").property("ADBE Position");
var opacity = textLayer.property("ADBE Transform Group").property("ADBE Opacity");
var scale = textLayer.property("ADBE Transform Group").property("ADBE Scale");

// Animation de position du bas vers le centre
pos.setValueAtTime(0.0, [960, 680]);
pos.setValueAtTime(1.2, [960, 540]);

// Courbes d'accélération Easy Ease
var easeIn = new KeyframeEase(0, 33.33);
var easeOut = new KeyframeEase(0, 85.0);
pos.setTemporalEaseAtKey(1, [easeIn, easeIn], [easeOut, easeOut]);
pos.setTemporalEaseAtKey(2, [easeIn, easeIn], [easeOut, easeOut]);

// Fondu d'opacité
opacity.setValueAtTime(0.0, 0);
opacity.setValueAtTime(0.8, 100);

// Respiration dynamique par expression
scale.expression = "var s = 100 + Math.sin(time * 3) * 4; [s, s];";

// Flou gaussien initial
var blur = textLayer.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
blur.property("Blurriness").setValueAtTime(0.0, 35);
blur.property("Blurriness").setValueAtTime(0.9, 0);

app.endUndoGroup();
```

Pour générer et exporter la vidéo :
```bash
# Générer le projet .ecproj
effectcraft-cli script demo_motion.jsx --save-as demo.ecproj

# Rendre la vidéo MP4 finale
effectcraft-cli render demo.ecproj --comp TitleCard --out demo.mp4 --fps 60 --bitrate 8000
```
