# Media Solution v12 — port EffectCraft

Portage du panel Adobe CEP « Media Solution v12.0 » (`MediaSolution-CEP/`) vers
**EffectCraft**, qui ne supporte ni CEP/HTML/Node, ni dialogs fichier, ni process
externes : le panel est un **ScriptUI dockable** en **JavaScript pur**, sans bridge
Python (décision de portage).

Le workflow repris à l'identique :

1. par dossier projet (`docs/` + `projets/`), pour chaque vidéo processable ;
2. création du projet (import, comp `<video>_9x16`, calque vidéo) ;
3. préparation du calque : comp `1080×1920`, anchor top-centre, position `[540, 0]`, échelle `180 %` ;
4. recentrage intelligent **par segment** depuis le tracking JSON (`*_ae.json` / `*_tracking.json`) :
   score présence × confiance, moyenne de `centroid_x`, label 3 (stable) ou 12 (forte amplitude, visage) ;
5. cuts depuis le CSV (`1,5` frame de snap, segments confirmés, audio activé) ;
6. enregistrement dans `projets/<video>_9x16.ecproj`.

## Prérequis

- EffectCraft avec sa CLI `effectcraft-cli` (panel testé sur 0.4.0 ; l'export `.aep`
  natif demande la CLI **≥ 0.24.1**, le repli `--legacy` fonctionne sans).
- **Préférences ▸ Scripting & Expressions ▸ « Allow Scripts to Write Files and Access
  Network »** : sans elle, le scripting ne peut ni lire les CSV/tracking hors dossier
  projet, ni écrire logs/config/projets. Le panel propose un bouton **« Activer »**.

## Installation

```bash
./install.sh
# ou manuellement :
mkdir -p ~/.config/effectcraft/Scripts/ScriptUI\ Panels
cp MediaSolution.jsx ~/.config/effectcraft/Scripts/ScriptUI\ Panels/
```

Puis dans EffectCraft : **Fenêtre ▸ MediaSolution** (le panel est relu à chaque
ouverture ; pas besoin de redémarrer si l'app tournait déjà).

## Utilisation du panel

- **Dossiers** : la *Racine* est pré-remplie par défaut (`/mnt/cache` — constante
  `autoDetectRootPath`), et le fichier de configuration de cette racine
  (`<racine>/mediasolution_ec.json`) est **rechargé automatiquement à l'ouverture du
  panel** : rien à re-saisir après un redémarrage d'EffectCraft. Cliquer
  **Détection auto** (dossiers contenant `docs/` dont le nom contient le *Filtre*,
  « camille » par défaut — mettre `mille` pour attraper aussi les dossiers « Cvmille »),
  ou **Ajouter** un dossier au chemin exact. Chaque dossier détecté apparaît en case à
  cocher avec son nombre de vidéos processables (exclusions `mov` et `m6+.mp4` par défaut).
- **Options** : cuts CSV, auto-recentrage, log fichier, échelle cible (`180`), taille
  cible (`1080×1920`), « Repartir des projets existants », chemin du fichier de config.
- **Traitement** :
  - **Pas-à-pas** (défaut) : « Démarrer » traite le **premier** projet, « ▶ Continuer »
    le suivant. Progression et annulation réelles entre chaque projet — l'interface
    d'EffectCraft gèle pendant un projet, pas pendant tout le batch.
  - **Tout exécuter** : un clic traite tout ; l'interface reste figée jusqu'à la fin
    (avertissement dans le log).
  - **Diagnostics** : vérifie `docs/`, `projets/`, compte vidéos/CSV/tracking et
    signale les CSV ou tracking manquants par vidéo.
- **Cuts manuels** : CSV saisi + « Appliquer » sur la comp active (calque sélectionné,
  sinon calque 1) ; « Afficher la cible active » loggue la comp/calque courants.
- **Objet nul parent** : chaque comp générée reçoit un calque **« Null Segments »** en
  **haut de la composition, centré horizontalement** (position `[540, 0]`, même convention
  top-centre que les segments), et **tous les segments y sont parentés** — par script, un
  par un. C'est volontaire : dans l'app, sélectionner plusieurs calques puis les parenter
  n'en parente qu'un seul (limitation de la multi-sélection), et déplier les options d'un
  calque non plus ne suit la sélection que sur un seul calque. La parenté compense la
  transformation (comme le pick-whip d'AE) : la position **affichée** des segments devient
  relative au null (ex. `~620, ~100` pour un segment au bord haut) mais leur **position
  monde est inchangée** — le rendu des découpes est identique. Désactivable via
  `nullParentSegments: false` dans `mediasolution_ec.json`.
- **Logs** : affichage permanent (400 dernières lignes), export vers un chemin saisi.
  Les logs vont aussi sur la sortie d'EffectCraft (console d'erreurs / CLI). Avec l'option
  « Log fichier », chaque dossier projet traité reçoit son propre
  `MediaSolutionLog_V12.0_EC_<horodatage>.log` (le log suit le dossier courant — un batch
  multi-dossiers écrit dans chaque dossier).

## Configuration

Fichier JSON `<racine>/mediasolution_ec.json`, chargé automatiquement à la détection et
réécrit à chaque démarrage de batch. Il contient toutes les constantes du port
(`compNameSuffix`, `projectFileSuffix: .ecproj`, seuils de recentrage, exclusions,
tailles cibles, `autoDetectRootPath`, `autoDetectNameFilter`…). Les boutons
**Charger/Enregistrer** permettent de pointer un autre fichier.

## Vérification / tests

```bash
# Smoke test complet sur la fixture (2 vidéos, CSV, 2 formats de tracking) :
cat MediaSolution.jsx tests/smoke_driver.js > /tmp/ms_smoke.jsx
effectcraft-cli script /tmp/ms_smoke.jsx        # « SMOKE PASSED » + sortie 0

# Driver headless réutilisable (éditer ROOT en tête de fichier) :
cat MediaSolution.jsx tests/run_batch_headless.js > /tmp/ms_run.jsx
effectcraft-cli script /tmp/ms_run.jsx
```

La fixture `tests/fixture/Camille-test/` a été générée avec EffectCraft lui-même
(rendu H.264 d'une comp démo, PNG, CSV et tracking JSON d'exemple).

## Pilotage automatisé (option)

Le moteur est exposé sur `$.global.mediasolutionEC` (`runBatch`, `detect`,
`diagnostics`, `status`, `cancel`). Comme les scripts EffectCraft ne peuvent pas
s'inclure entre eux, on concatène le panel et un driver (voir ci-dessus), puis on
l'exécute dans l'app ouverte :

```bash
effectcraft --control 9878 --empty          # l'app avec le control channel
effectcraft-cli --bridge 9878 exec file.runScript \
  --params '{"path":"/tmp/ms_run.jsx"}'     # batch non bloquant pour le panel
```

C'est la voie recommandée pour un batch vraiment automatique (le panel reste
l'interface interactive).

---

## Étape 8 du workflow `workflow_mediapipe` (automatisation intégrée)

Le pipeline intègre ce panel comme **8ème étape** : après la finalisation (étape 7),
`workflow_scripts/step8/run_mediasolution_ec.py` concatène ce fichier + un driver généré et
exécute `effectcraft-cli script` en headless sur les dossiers du manifeste
`logs/step7/finalized_projects_*.json` — mode « Tout exécuter », un seul `runBatch`
(`maxUnits = 0`) qui enchaîne tous les projets.

- Doc : `../../docs/workflow/pipeline/08-mediasolution-ec.md`
- Variables `.env` : `STEP8_TIMEOUT`, `STEP8_EFFECTCRAFT_CLI`, `STEP8_PANEL_JSX`,
  `STEP8_PANEL_CONFIG` (config `mediasolution_ec.json` appliquée via `applyConfig`).
- Sorties : `.ecproj` dans `<dossier>/projets/` ; l'export `.aep`/`.prproj` reste hors étape 8
  (voir ci-dessous).
- **⚠️ EffectCraft 0.8.0+** (garde-fou vérifié en 0.9.2) : le mode headless est bloqué par le
  garde-fou de scripting (un script ne peut plus activer `allowScriptsWriteFiles`, et le CLI
  headless ignore la préférence utilisateur). L'étape 8 gère les deux cas automatiquement
  (`STEP8_MODE=auto`) : headless en ≤ 0.7.0, **pilotage de l'application ouverte** en 0.8.0+ —
  lancée automatiquement si son canal n'écoute pas (`effectcraft --control 9877`). Détail :
  `../../docs/audits/effectcraft-0.8.0-compatibilite.md`.

---

## Exporter aussi en .aep (post-traitement)

Depuis EffectCraft 0.24.1, l'export `.aep` est **natif** (`file.exportAep`) : le
convertisseur `tools/ecproj_to_aep.py` passe par `effectcraft-cli`, sans py_aep dans le
cas courant. Sont écrits par EffectCraft : comps, calques, parenté, **objets nuls**,
solides, métrages, transforms et keyframes, masques, effets de sa table, Time Remap,
labels, audio et réglages projet. Tout ce qui n'est pas repris est signalé dans le
rapport d'export.

**Chemins de métrage** : l'export natif écrit le chemin stocké dans le `.ecproj` **tel
quel**. Un projet qui référence `/mnt/cache/...` produit donc un `.aep` qui référence
`/mnt/cache/...`, sans relink ni mapping implicite. C'est le comportement par défaut.
`--path-map SRC=DST` reste disponible (copie temporaire du `.ecproj`) si un mapping
Windows est nécessaire.

**Repli py_aep** : les clips et séquences à cadence fractionnaire (29,97 / 23,976 /
59,94) ne sont pas encore exportés par EffectCraft 0.24.1. Le convertisseur bascule
automatiquement sur l'ancien moteur py_aep (`tools/ecproj_to_aep_legacy.py`) pour ces
projets, avec un message explicite. L'ancien comportement est conservé : les objets nuls
sont alors supprimés et les positions parentées écrites en coordonnées monde. Utiliser
`--no-fallback` pour refuser ce repli, ou `--legacy` pour forcer l'ancien moteur.

**Pertes** : les pertes bénignes connues (alpha `Straight`, profil couleur standard,
guides, noms tronqués, temps arrondis) sont autorisées et journalisées. Toute perte qui
change le rendu bloque la conversion, avec `--allow-losses` comme échappatoire
(déconseillé). Les erreurs indiquent les catégories concernées.

### Double-clic (usage courant)

Un raccourci est installé sur le Bureau et dans le menu des applications :

- **`~/Bureau/Export-AEP-Camille.desktop`** — double-clic → il scanne `/mnt/cache`,
  détecte les dossiers projet (enfants directs dont le nom contient le filtre — « camille »
  par défaut — et qui ont un sous-dossier `docs/`, **même détection que le panel**),
  convertit **tous** les `.ecproj` trouvés en `.aep` **et en `.prproj`** à côté d'eux, avec
  barre de progression et résumé en fenêtres (zenity). `--no-prproj` limite la sortie aux `.aep`.
- Le détail de chaque passage est journalisé dans `tools/aep_export.log`.
- Le `.aep` conserve les chemins `/mnt/cache` ; le `.prproj` garde son mapping Windows
  `/mnt/cache ⇒ F:` (comportement inchangé).

### Ligne de commande

```bash
# tout le cache (mêmes réglages que le double-clic) — analyse seule :
python3 tools/export_aep_from_cache.py --dry-run --no-gui
# conversion réelle (.aep et .prproj) :
python3 tools/export_aep_from_cache.py --no-gui
# .aep seulement :
python3 tools/export_aep_from_cache.py --no-gui --no-prproj
# forcer l'ancien moteur py_aep :
python3 tools/export_aep_from_cache.py --no-gui --legacy

# un projet précis, avec relecture de vérification :
python3 tools/ecproj_to_aep.py "/mnt/cache/89 Camille/projets/xxx_9x16.ecproj" --verify
# tout un dossier, chemins conservés (défaut) :
python3 tools/ecproj_to_aep.py "/mnt/cache/89 Camille/projets" --all --verify
# avec mapping Windows explicite :
python3 tools/ecproj_to_aep.py "/mnt/cache/89 Camille/projets" --all \
  --path-map "/mnt/cache=F:" --verify
```

Réglages du scan (racine, filtres, mappings) : constantes en tête de
`tools/export_aep_from_cache.py`. `NAME_FILTERS` est un **tuple** — la virgule est
nécessaire même pour un seul filtre (`("camille",)`). Le filtre par défaut ne capture que
les dossiers contenant « camille » ; pour les dossiers « Cvmille », utiliser
`--filter cvmille` (ou `--filter mille`). `AEP_PATH_MAP` est vide par défaut (chemins
conservés) ; `PRPROJ_PATH_MAP` garde `/mnt/cache ⇒ F:`. Les deux se règlent séparément
(`--aep-path-map`, `--prproj-path-map`) ; `--path-map` s'applique aux deux.

- Cible **After Effects 2026+** (export natif EffectCraft 0.24.1). Sur une version
  antérieure d'EffectCraft, `--legacy` reste disponible (py_aep, cible AE 2026+).
- Les **objets nuls** du panel sont **conservés** par l'export natif, avec les liens de
  parenté ; le rendu est identique au `.ecproj`. Le repli legacy les supprime et écrit
  les positions en coordonnées monde (comme avant).
- Sans mapping, AE ouvrira les médias avec le chemin stocké (`/mnt/cache/...`). Si ce
  chemin n'existe pas côté Windows, utiliser `--path-map` ou relinker dans AE.
- `--verify` relit le `.aep` avec l'import EffectCraft (dry-run) et vérifie les alias de
  métrage ; pour le legacy, il utilise py_aep.
- Les options `--skip-missing`, `--effect-defs`, `--mask-space` et `--py-aep` ne
  concernent que le moteur legacy.
- Chaque passage réécrit les `.aep` (résultat toujours à jour).

## Export Premiere Pro (.prproj) — post-traitement optionnel

Premiere Pro n'ouvre pas les `.ecproj`, et l'export XML (FCP7) d'EffectCraft centre mal les
clips à l'import : `tools/ecproj_to_prproj.py` écrit **directement** un projet Premiere 2026
(`.prproj`) depuis le `.ecproj`, hors Adobe. La structure du format a été relevée sur un
projet de référence généré par Adobe et vérifiée valeur par valeur :

- comp → séquence, calques → clips (Start/End = in/out exacts), pistes multiples si les
  calques se chevauchent ;
- Transform → filtre `AE.ADBE Motion` : Position normalisée par la taille de séquence,
  Anchor par la taille source, échelle en %, rotation en degrés ;
- keyframes Position (linéaires et Bézier : valeurs, vitesses, tangentes spatiales) —
  les temps écrits sont ceux du `.ecproj` exactement (la chaîne Adobe `.aep → Premiere`
  décalait les clés de 1 à 4 images, un artefact de timebase 24→25 fps) ;
- calques parentés → position monde (les « Null Segments » ne sont pas exportés), audio lié ;
- **images fixes** (png, jpg…) → médias « still » Premiere (durée infinie, `IsStill`, sans
  audio) ; un calque dont le Transform est celui par défaut utilise les composants
  Motion/Opacity intrinsèques de Premiere, comme le fait l'import Adobe ;
- **audio seul** (mp3, wav…) → média audio sans flux vidéo, comme Adobe ; l'audio des
  calques vidéo suit `switches.audio` ; `--audio-fallback` reprend l'audio du calque vidéo
  le plus bas (V1) quand **aucun** calque n'est audible sur un segment (cas d'un freeze
  dupliqué dont l'audio a été coupé sur les copies) ;
- **audio & stabilité lecture Premiere 2026** → pistes audio allouées par segments actifs,
  toutes connectées à l'`AudioTrackInlet` du bus master ; les clips gardent une chaîne de
  composants **vide** (comme les clips Adobe par défaut) — **aucun** filtre « Internal Volume »
  n'est écrit par clip (un filtre synthétique provoquait un crash immédiat à la lecture dans
  `AudioFilterHost` / `AR::IsBypassActuallyMute`) ; les médias porteurs d'audio reçoivent
  `ConformedAudioRate` (`5292000` à 48 kHz, ou sentinelle `i64::MAX` si absence de flux) et un
  `AudioStream/SampleType` conforme à la source (`3` WAV PCM16, `4` PCM MOV/MXF, `7` AAC/MP4,
  `11` AIFF) ; un média présent sur disque n'est plus marqué `OfflineReason`. Le gain par clip
  d'EffectCraft n'est pas repris — à ajuster directement dans Premiere ;
- **masques** (modes « Add », « Subtract », « Intersect », « Difference », inversion comprise) → filtre `AE.ADBE AEMask2` (27 paramètres) écrit en sous-composant de
  l'**Opacité** du clip (matérialisée) avec `InstanceName` (« 01 », « 02 »…), payload privé
  complet et `BinaryHash` ; tracé en blob binaire (points normalisés par la taille **source**
  du média — espace du calque, comme After Effects —, 3 points par sommet, tangentes
  absolues), Position/Anchor au centre de la bounding-box ; un tracé animé est écrit à sa
  première clé (comme l'export `.aep`) ; **un masque qu'EffectCraft n'applique pas — tracé
  ouvert (non fermé), mode « None », masque désactivé — n'est pas écrit** : le clip reste
  entier, comme dans EffectCraft (`crates/render/src/masks.rs` : aucune transparence tant que
  le tracé n'est pas fermé) ; un masque vide **mais fermé** découpe à vide et prend l'état par
  défaut d'Adobe (`Type 0`). La variante Adobe avec conteneur intrinsèque vide dans
  `SelectionComponents` + masque à payload vide a aussi été testée dans Premiere 2026 : elle
  fonctionne, mais la forme simple sous l'Opacité est conservée (validée, moins d'objets) ;
- **Gaussian Blur** (`ec.blur.gaussian`) → filtre `AE.Impact_Blur_FX` de Premiere (18
  paramètres) : `blurriness` → « Amount » (borné 0-100) ; `dimensions`/`repeatEdge` d'EffectCraft
  ne sont pas repris (avertissement) ;
- **Freeze Frame** (Time Remap EC à clé Hold constante) → **Frame Hold** natif de Premiere
  (`FrameHold` 4 + `FrameHoldStart` sur le `VideoClip`) : le clip garde sa durée et l'image
  source reste figée — méthode validée dans Premiere 2026, y compris avec masque ; une vraie
  courbe de vitesse à plusieurs clés reste écrite en `TimeRemapping`/`Speed` (paliers Hold
  signalés) ;
- **solides & calques d'effet** → générateurs « Color Matte » (COLR, couleur RGB dans
  l'`ImporterPrefs` de 8 octets) et « Black Video » (BLAK + `IsAdjustmentLayer`) de
  Premiere, comme le fait l'import Adobe ;
- **modes de fusion** → les 27 modes natifs de Premiere (doubles paramètres « Blend Mode » 2 et 3
  de l'Opacité, table `BLEND_MODE_MAP` des 38 noms EffectCraft ; « Normal » reste implicite) ;
- médias référencés par leur chemin écrit (**par défaut `PATH_MAP`, `/mnt/cache ⇒ F:`**,
  pour l'ouverture sous Windows ; `--no-path-map` écrit les chemins Linux tels quels).

**Ce qui reste hors périmètre** (tracé de masque non fermé, roto-bezier /
  feather non lisse, feather variable par points, motion blur, effets hors table — seul
  `ec.blur.gaussian` est repris) est signalé projet par projet : le `.prproj` n'est alors pas
écrit, seul le `.aep` est produit et un `.prproj` voisin devenu obsolète est supprimé (le
journal et l'interface le signalent). `--force` passe outre — le rendu Premiere sera amputé de
ces éléments.

### Double-clic

- **`~/Bureau/Export-PRPROJ-Camille.desktop`** — même détection que l'export `.aep`
  (dossiers projet enfants de `/mnt/cache` + filtre « camille »), convertit **tous** les
  `.ecproj` trouvés en `.prproj` à côté d'eux, progression et résumé en fenêtres (zenity).
- Journal : `tools/prproj_export.log`.

### Ligne de commande

```bash
# un projet précis, avec relecture + comparaison à une référence Adobe :
python3 tools/ecproj_to_prproj.py "/mnt/cache/94 Camille/projets/UJUD - ARRIVEE OLU_9x16.ecproj" \
  --path-map "/mnt/cache=F:" --verify \
  --compare-with "/mnt/cache/94 Camille/projets/UJUD - ARRIVEE OLU_9x16.prproj"

# segment dont tous les calques ont l'audio coupé : reprendre l'audio de la V1
python3 tools/ecproj_to_prproj.py "/mnt/cache/309 Camille/projets/PP - La revue de presse - Karine Dubernet_9x16.ecproj" \
  -o "/mnt/cache/309 Camille/projets/PP - La revue de presse - Karine Dubernet_9x16.prproj" --audio-fallback

# tout un dossier :
python3 tools/ecproj_to_prproj.py "/mnt/cache/94 Camille/projets" --all --path-map "/mnt/cache=F:"
```

`--verify` relit le `.prproj` produit (références cohérentes, comptes, Position/Anchor
dénormalisées) ; `--compare-with` fait le diff sémantique avec un `.prproj` de référence
(items, temps, valeurs Position/Anchor) — les seuls écarts attendus sont le décalage de
temps de la chaîne Adobe et la précision d'affichage des flottants.

Limites v1 (avertissement explicite, non exporté) : sources autres que Footage et Solid
(Text/Shape/Camera/Light, comp imbriquée), plus d'une comp, `stretch` ≠ 100. Sont repris à
l'inverse : solides et calques d'effet (Color Matte / Black Video + Adjustment), les 27 modes
de fusion de Premiere, l'opacité (fixe ou animée) et les quatre modes de masque
(Add/Subtract/Intersect/Difference, inversion comprise). Masques : ceux qu'EffectCraft
n'applique pas (tracé ouvert, mode « None », masque désactivé) sont ignorés — le clip reste
entier, comme dans EffectCraft ; options exotiques et feather variable par points → `.aep`
uniquement (cf. ci-dessus) ; le feather `Vec2` d'EffectCraft est ramené au plus grand des deux
axes (Premiere n'a qu'un scalaire) ; les clés animées d'échelle/rotation/ancre sont encodées en
« expérimental ». Format ciblé : **Premiere Pro 2026**.

## Copie de travail (~/Media_Solution/MediaSolution-EC) et synchronisation

Les raccourcis du Bureau et du menu exécutent la **copie déployée**
`~/Media_Solution/MediaSolution-EC/tools/…`, alors que l'étape 8 du pipeline lit le panel du
**dépôt** (`scripts/MediaSolution-EC/MediaSolution.jsx`) : les deux arbres doivent porter le
même code. `deploy.sh` s'en charge (il part toujours de la copie du dépôt) :

```bash
./deploy.sh             # dépôt → ~/Media_Solution/MediaSolution-EC
./deploy.sh --check     # ne copie rien, liste les écarts (sort en 1 s'il y en a)
./deploy.sh --dry-run   # liste ce qui serait copié
./deploy.sh --prune     # retire aussi les fichiers absents du dépôt
./deploy.sh --no-panel  # ne pas réinstaller le panel EffectCraft
```

- La comparaison se fait **par contenu** (`rsync --checksum`), pas par date.
- Quand `MediaSolution.jsx` change, la copie est automatiquement réinstallée dans EffectCraft
  (`install.sh`) ; `--no-panel` le désactive.
- Journaux d'export (`tools/*.log`), `__pycache__`, `*.pyc` et caches pytest sont **propres à
  chaque copie** : exclus de la synchronisation et jamais supprimés.
- Destination surchargeable par `MEDIASOLUTION_DEPLOY_DIR` ou `--dest DIR`.

## Structure

```
MediaSolution-EC/
  MediaSolution.jsx          panel + moteur (fichier unique déployable)
  install.sh                 installation vers ~/.config/effectcraft/Scripts/ScriptUI Panels/
  deploy.sh                  synchronisation vers la copie de travail ~/Media_Solution/MediaSolution-EC
  docs/COMPATIBILITY.md      correspondance avec le panel CEP, adaptations, limites, options
  CLI_AUTOMATION.md          guide du pilotage headless/bridge (CLI EffectCraft, garde-fous 0.8.0+)
  tools/ecproj_to_aep.py     convertisseur .ecproj → .aep (export natif EffectCraft, repli py_aep)
  tools/ecproj_to_aep_legacy.py  ancien convertisseur py_aep (repli cadences fractionnaires)
  tools/ecproj_common.py     helpers .ecproj partagés avec le convertisseur .prproj
  tools/ae_effect_defs.aepdefs      gabarits d'effets AE pour le repli py_aep (EfdG)
  tools/extract_ae_effect_defs.py   (re)construit ce fichier depuis un .aep Adobe (legacy)
  tools/export_aep_from_cache.py  scan de /mnt/cache (dossiers Camille/Cvmille) + export en lot
  tools/Export-AEP-Camille.desktop  raccourci à double-cliquer (installé sur le Bureau)
  tools/aep_export.log       journal des exports
  tools/ecproj_to_prproj.py  convertisseur .ecproj → .prproj (Premiere Pro 2026, hors Adobe)
  tools/Export-PRPROJ-Camille.desktop  raccourci à double-cliquer (installé sur le Bureau)
  tools/prproj_export.log    journal des exports .prproj
  tests/smoke_driver.js      assertions de bout en bout (fixture)
  tests/run_batch_headless.js driver headless réutilisable
  tests/fixture/             jeu de test (vidéos H.264, CSV, tracking JSON, image)
```

## Limites

Voir `docs/COMPATIBILITY.md` (détail et options) — en résumé : pas de progression
live ni d'annulation pendant un projet, pas de dialogs fichier, pas de bridge Python,
sorties `.ecproj` uniquement, préférence scripting requise, état du panel non persisté.
