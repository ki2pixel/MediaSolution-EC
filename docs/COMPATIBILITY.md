# Compatibilité — MediaSolution-CEP → MediaSolution-EC

Document de référence du portage : ce qui est repris tel quel, ce qui a été adapté
(avec la raison côté EffectCraft), ce qui est abandonné, et les options pour chaque
limite restante.

Source de référence (non modifiée) : `MediaSolution-CEP/host/MediaSolution.jsx`
(3 426 lignes, 38 exports CEP) et `MediaSolution-CEP/client/main.js` (UI HTML).

---

## 1. Logique métier reprise à l'identique

| Original (CEP) | Port EffectCraft | Notes |
|---|---|---|
| `performStep2_1_Logic` | `buildPlan` + logs | contenu identique (docs/, vidéos processables) |
| `performStep2_2_Logic` / `createBaseProjectForVideo` | `createBaseProjectInMemory` | import → `items.addComp` → `layers.add`, en un passage |
| `performStep3_1_Logic` | `processUnit` (résolution comp/calque) | `findCompByName`, `findMainVideoLayer` inchangés |
| `step3_2_importImages` | `importImages` | mêmes extensions d'images |
| `step3_2_prepareLayer` | `prepareLayer` | comp 1080×1920, anchor top-centre, position `[540,0]`, échelle 180 % |
| `applyAutoRecentering` (fallback JS) | `applyDefaultCentering` | centrage horizontal comp |
| `step3_2_applyCuts` + `applyCutsToSelectedLayer` | `applyCutsToLayer` | parseur CSV, snap 1,5 frame, snap fin de comp, recentrage par segment, labels 3/12, suppression du calque d'origine |
| `splitCsvLine`, `csvTimecodeToSeconds`, `secondsToTimecode` | identiques | mêmes formats acceptés (`HH:MM:SS.mmm`, `MM:SS`, virgule décimale) |
| `indexReducedTrackingFrames`, `calculateStats`, `computePresenceScore`, `pickBestObject[InRange]` | identiques | même algorithme de score et de tie-break |
| `findTrackingJsonForVideo` | identique | priorité `_ae.json` → `_tracking.json` → `.json` |
| `getFilesByExtensions`, `isVideoSkipped`, `getProcessableVideoFiles` | identiques | mêmes exclusions (`mov`, `m6+.mp4`, insensible à la casse + variantes URL-décodées) |
| `runDiagnostics` | `runDiagnostics` | enrichi (compte CSV/tracking, manquants par vidéo) |
| `autoDetectFolders` | `autoDetectFolders` | racine et filtre configurables (plus de `F:/` codé en dur) |
| Label haut/bas (`LABEL_HIGH_SPREAD` 12 / `LABEL_STABLE` 3), `SPREAD_THRESHOLD` 200, `CONFIDENCE_WEIGHT` 0,35 | identiques | vérifiés par le smoke test (labels « Brown »/« Aqua ») |
| Log fichier de session, buffer d'écriture, export | repris | chemin = `<racine>/`, option désactivée par défaut |

**Différence volontaire** : le port crée puis traite le projet en **un seul passage**
(l'original sauvait le projet de base, le rouvrait, puis appliquait les cuts). Le
résultat sur disque est identique (`projets/<video>_9x16.ecproj`), en moins d'allers-retours.
En contrepartie, un second passage écrase le projet existant (l'original dupliquait
avec un suffixe timestamp). L'option « Repartir des projets existants » permet de
retraiter des projets déjà présents.

---

## 2. Adaptations obligatoires (et pourquoi)

| # | Contrainte EffectCraft | Adaptation dans le port |
|---|---|---|
| 1 | Aucun support CEP/HTML/webview : le panel doit être un `.jsx` ScriptUI | UI reconstruite avec les widgets natifs (`panel`, `group`, `button`, `edittext` multiline, `checkbox`, `dropdownlist`, `progressbar`, cases à cocher dynamiques pour les dossiers) |
| 2 | Pas d'`ExternalObject`/`CSXSEvent` (host→client CEP) | `sendToCEP` supprimé ; le log et la progression écrivent directement dans les widgets |
| 3 | `csInterface.evalScript` n'existe pas | appels directs (tout est dans le même fichier) |
| 4 | **`app.scheduleTask` est inutilisable en panel** : les tâches planifiées depuis un handler ne s'exécutent jamais, le délai est ignoré, `$.sleep` est un no-op | batch réécrit en **un projet par handler** (mode pas-à-pas) ; mode « tout exécuter » assumé bloquant |
| 5 | Dialogs fichier (`Folder.selectDialog`, `File.openDialog/saveDialog`) renvoient `null` | sélection par **champ texte** + « Détection auto » + bouton « Ajouter » ; export de logs et config par chemin saisi |
| 6 | `Folder.myDocuments/temp/desktop` = `null` | chemins explicites (racine du batch, config, log) ; plus de dépendance à `Documents` |
| 7 | `File.rename` n'existe pas | écriture directe (pas d'écriture atomique `.tmp` + rename) ; le try/catch signale l'échec sans perdre le batch |
| 8 | `PurgeTarget` absent | `app.purge()` (purge complète) après chaque projet |
| 9 | `Viewer`/`app.project.viewers` absents | `closeAllCompViewers()` conservé en no-op gardé |
| 10 | `layer.canSetAudioEnabled` absent | `audioEnabled` affecté directement (try/catch) |
| 11 | `layer.duration` absent (undefined en batch) | fallback `outPoint - inPoint` déjà prévu par l'original, conservé |
| 12 | **`layer.property("Position")` renvoie `null`** (fidélité AE : la recherche ne descend pas dans « Transform ») | helper `msProp(layer, name)` → `layer.transform.property(name)` : **seule adaptation du cœur métier** |
| 13 | Pas de pollution du global `JSON` par des plugins tiers | `myJSON` (polyfill eval) remplacé par `JSON` natif d'EffectCraft |
| 14 | Utilisé par le bridge Python d'origine (mailbox + `$.sleep`) | tout le bridge est supprimé (décision) : aucun polling, aucune écriture de mailbox |
| 15 | `app.settings` est en mémoire seulement (perdu à chaque exécution) | configuration dans `<racine>/mediasolution_ec.json` |
| 16 | Redimensionner une comp dans EffectCraft décale les calques non parentés de la moitié du delta | ordre d'origine conservé (resize → anchor → position → scale explicites) : le décalage est écrasé par les `setValue` |
| 17 | `app.project.items.addComp` n'active pas la comp (pas de viewer ouvert) | sans impact : le port travaille par référence (comp/calque résolus par nom/index) |
| 18 | Le panel produit des `.ecproj` ; EffectCraft ≥ 0.24.1 sait aussi importer/exporter des `.aep` (`file.importAep` / `file.exportAep`) | le panel n'appelle pas ces commandes : il reste sur `.ecproj`, l'export `.aep` est un post-traitement | sorties `.ecproj` ; `projectFileSuffix` configurable ; export `.aep` natif hors panel |
| 19 | Le scripting est limité au dossier du projet courant (lecture) et refuse les écritures sans préférence | préflight + bouton **« Activer »** (`app.run("prefs.set", {key:"scripting.allowScriptsWriteFiles", value:true})`) + message explicite |
| 20 | L'état du panel n'est pas persisté, pas de timers (pas de polling possible) | état reconstruit depuis le fichier de config ; plus de surveillance périodique du bridge ni de la cible active (boutons manuels) |

---

## 3. Fonctionnalités abandonnées

| Fonction d'origine | Raison | Impact |
|---|---|---|
| Client CEP complet (`client/` : HTML/CSS/JS, CSInterface, thème, DevTools) | aucun hôte HTML dans EffectCraft | UI ScriptUI équivalente en fonctionnalités |
| Bridge Python (`bridge_daemon.py`, mailbox JSON, pipe nommé, spawn de daemon, health check 30 s, « Relancer ») | décision utilisateur (« aucun bridge, jamais ») ; EffectCraft ne peut pas lancer de process | **aucun** : les 2 calculs utiles (segments de cuts, recentrage) ont toujours eu un fallback JS complet, qui est ici la seule implémentation |
| Modes `py_aep` (cuts offline, création de base `.aep`), `saveAndGetProjectPath`/`reopenProjectPath` | `.aep` illisible/inexistant dans EffectCraft ≤ 0.7 | remplacés par le passage unique `.ecproj` ; l'export `.aep` se fait **après** le batch par `tools/ecproj_to_aep.py` (export natif EffectCraft ≥ 0.24.1, repli py_aep pour les cadences fractionnaires) |
| `batchPipe*` / orchestration client (pipe) | sans bridge | remplacés par les modes pas-à-pas / tout exécuter |
| `entry.py` / `manifest.py` (CEPy) | inertes même côté AE | — |
| Configuration `enablePython*`, `pythonCmd` | sans objet | remplacés par `enableCuts`, `enableAutoRecentering`, `enableLogFile`, `reuseExistingProjects` |
| `app.settings` saveSetting/getSetting | non persistant | fichier JSON de config |

`MediaSolution-CEP/`, `Scripts/` et `PyShiftAE_CEP_Bridge/` sont **inchangés** : le
setup Windows/AE continue de fonctionner tel quel.

---

## 4. Limites restantes → options

| Limite | Conséquence | Options |
|---|---|---|
| L'app gèle pendant un projet (l'UI d'EffectCraft n'est mise à jour qu'au retour du handler) | pas de progression live ni d'annulation **pendant** un projet | **Retenu** : mode pas-à-pas (1 projet par clic, progression et annulation entre projets). Pour un batch continu automatique : piloter `mediasolutionEC.runBatch` par le control channel/MCP (`tests/run_batch_headless.js`) — le panel reste l'interface interactive |
| Aucun dialog fichier | chemins à saisir | **Retenu** : détection auto + champs texte + config persistée |
| Pas de timers/`scheduleTask` fiables | pas de surveillance périodique | actions manuelles (« Vérifier », « Cible active », « Diagnostics ») |
| Un seul projet en mémoire à la fois | le projet ouvert par l'utilisateur est fermé au démarrage du batch | comportement identique à l'original (anti-cumul) ; ne pas lancer de batch sur un projet en cours d'édition non sauvegardé |
| Pas d'écriture atomique (`File.rename`) | une interruption pendant l'écriture d'un log/config peut laisser un fichier partiel | impact faible (log/config uniquement ; les `.ecproj` sont écrits par le moteur, pas par le scripting) |
| Log fichier : un seul projet en mémoire, mais un batch peut couvrir plusieurs dossiers | le log ne peut pas rester « global » au batch sans être écrit ailleurs que dans les dossiers traités | **retenu** : le log (`MediaSolutionLog_V12.0_EC_<horodatage>.log`) est écrit **dans chaque dossier projet traité** et suit le dossier courant ; deux runs dans la même seconde réutilisent le même nom de fichier |
| Préférence scripting OFF par défaut | lectures/writes refusés | préflight + bouton « Activer » + message explicite ; le smoke test active la préférence lui-même |
| Widgets ScriptUI ≠ HTML/CSS | rendu plus simple (pas de thème, pas de scroll horizontal ni de DevTools) | acceptable pour un outil batch ; le log multiligne et la barre de progression remplacent l'UI client |
| `ListBox` mono-sélection dans EffectCraft | — | contourné : liste de cases à cocher dynamiques |
| Multi-sélection partielle dans EffectCraft : parenter plusieurs calques n'en parente qu'un, déplier les options d'un calque n'affecte qu'un calque | rend fastidieux le parentage d'une série de calques | **contourné dans le panel** : chaque comp générée reçoit un objet nul « Null Segments » (haut-centre de la comp, position `[540, 0]`, même convention top-centre que les segments) et tous les segments y sont parentés par script, un par un ; la position monde des découpes reste inchangée (compensation type pick-whip), la position affichée devient relative au null |
| Codecs vidéo d'EffectCraft | un format non décodable ne peut pas être importé (testé OK : H.264) | tester l'import en amont ; transcoder les formats exotiques (hors périmètre du port) |
| Sorties `.ecproj` uniquement (pas d'export `.aep` depuis le panel) | pas d'échange direct avec After Effects depuis le panel | **Résolu en post-traitement** : `tools/ecproj_to_aep.py` convertit les `.ecproj` en `.aep` **AE 2026+** avec l'export **natif** d'EffectCraft ≥ 0.24.1 (`file.exportAep` : comps, calques, **parenté et objets nuls**, solides, métrages, transforms et keyframes, masques, effets de sa table, Time Remap, labels, audio, réglages projet) ; les clips/séquences à cadence fractionnaire (29,97 / 23,976 / 59,94) basculent automatiquement sur l'ancien moteur **py_aep** (`tools/ecproj_to_aep_legacy.py`, objets nuls supprimés et positions parentées écrites en coordonnées monde), avec `--no-fallback` pour refuser ce repli. Les pertes bénignes (alpha `Straight`, profil couleur standard, guides, noms tronqués, temps arrondis) sont autorisées et journalisées ; toute perte qui change le rendu bloque (`--allow-losses` pour passer outre). `tools/export_aep_from_cache.py` + raccourci Bureau `Export-AEP-Camille.desktop` font le scan de `/mnt/cache` (dossiers Camille) et l'export en lot au double-clic. Le `.aep` **conserve** les chemins stockés (`/mnt/cache/...`, aucun mapping par défaut) ; `--path-map` réécrit les chemins via une copie temporaire du `.ecproj`. `--verify` relit le projet produit (import EffectCraft dry-run + contrôle des alias de métrage) |
| Pas d'export natif `.prproj` (Premiere) ; l'export XML FCP7 d'EffectCraft importe avec des positions incohérentes | pas d'échange direct avec Premiere Pro | **Résolu en post-traitement (v1)** : `tools/ecproj_to_prproj.py` écrit directement le `.prproj` **Premiere 2026** depuis le `.ecproj` (structure relevée sur un projet Adobe de référence ; Position/Anchor vérifiées identiques après normalisation, keyframes et audio repris, temps **exacts** du `.ecproj` — la chaîne Adobe `.aep`→Premiere décalait les clés de 1 à 4 images par un effet de timebase 24→25 fps ; **images fixes** exportées en médias « still » comme Adobe, avec composants Motion/Opacity intrinsèques quand le Transform est par défaut ; **solides & calques d'effet** en Color Matte `COLR` et Black Video `BLAK` ; **audio stable Premiere 2026** avec chaîne de composants vide par clip, `ConformedAudioRate`, SampleType conforme et routage master complet) ; raccourci `Export-PRPROJ-Camille.desktop` (mapping `/mnt/cache ⇒ F:`), `--verify` et `--compare-with` ; `--audio-fallback` reprend l'audio du calque vidéo le plus bas (V1) quand aucun calque n'est audible sur un segment (freeze dupliqué avec audio coupé) ; limites v1 : Footage et Solid uniquement, une seule comp, opacité/blend/stretch particuliers signalés et non exportés. **Masques, effets et Freeze Frame** : les masques « Add » non inversés sont écrits en filtre `AE.ADBE AEMask2` sous-composant de l'Opacité du clip (`InstanceName`, payload privé complet, `BinaryHash`), tracé en blob binaire, 1re clé si animé — la variante Adobe avec conteneur vide dans `SelectionComponents` + masque à payload vide a aussi été validée dans Premiere 2026, mais la forme simple sous l'Opacité est conservée ; l'effet Gaussian Blur en `AE.Impact_Blur_FX` ; un Freeze Frame (Time Remap EC à clé Hold constante) en **Frame Hold** natif (`FrameHold` 4 + `FrameHoldStart`), et une vraie courbe de vitesse en objet `TimeRemapping` + paramètre « Speed » (courbe à plusieurs clés approximée en mode TIME, paliers Hold signalés) — structures relevées sur les fixtures Adobe 26/76 et un projet Premiere 2026 reconstruit (masque + Add Frame Hold) ; les masques hors périmètre (autre mode, inversés, options exotiques) et les effets hors table bloquent toujours l'export `.prproj` (`.aep` seul, `.prproj` obsolète supprimé, `--force` pour passer outre) |
| Encodage/décodage vidéo 100 % CPU (aucun NVENC/QSV/AMF/VAAPI dans EffectCraft, encodeurs pure Rust) | pas de rendu accéléré dans l'app | **Résolu autrement** : les rendus sont lancés côté **Premiere Pro / Media Encoder** (accélération GPU native) à partir des `.prproj` produits par `tools/ecproj_to_prproj.py`. L'outil expérimental `tools/encode_gpu.py` (transcodage NVENC d'un master déjà rendu) a été **retiré** : il n'accélérait pas un rendu direct (l'encodage CPU d'EffectCraft ne pèse que ~2 % du temps) |
| Patch Rust d'EffectCraft (drain des tâches planifiées après chaque handler) | débloquerait progression live + batch automatique in-app | **écarté** (décision : 100 % scripts) ; peut être proposé en amont plus tard |

---

## 5. Vérifications effectuées

- **Smoke test headless** (`tests/smoke_driver.js`) : 2 vidéos, 2 formats de tracking,
  snap (gap 0,03 s absorbé, fin de comp ajustée), labels 3 et 12, anchors moyennes
  (260 / 283,3 / 275 / 310), position 540, échelle 180, audio actif, 3 items par projet
  (vidéo + comp + image importée) — **SMOKE PASSED**.
- **Test in-app** (EffectCraft 0.4.0, control channel) : panel ouvert par
  `window.scriptPanel`, détection auto dans l'UI, batch **pas-à-pas** (progression 1/2
  → 2/2, fichiers créés), batch **tout exécuter**, **Diagnostics**, log complet —
  fichiers relus et vérifiés (labels/anchors/in-out/scale).
- **Convertisseur `.aep`** (`tools/ecproj_to_aep.py`) : les 13 projets réels de
  `/mnt/cache/*/projets` s'exportent par le moteur **natif** (`file.exportAep`, CLI
  0.24.1) en ~0,3 s, avec seulement des pertes bénignes (interprétation alpha/couleur
  des métrages, 2 guides). Relecture native (`file.importAep` en dry-run) : 0 perte.
  Aller-retour export → import → rendu **identique image par image** sur `Raymond`
  (4/4), `CABINE` (3/3), `PP - LRDP` (129 calques, 6 masques, 2 time remaps, 4/4),
  `W9` (3/3) et une variante avec parenté (1/1). Les alias `Als2/alas` conservent les
  chemins stockés `/mnt/cache/...` (accents et espaces compris) ; `--path-map` a été
  vérifié (`F:/...`). Les objets nuls et la parenté sont conservés.
  Repli testé sur la fixture `clip1_9x16.ecproj` (29,97 fps) : bascule automatique sur
  `tools/ecproj_to_aep_legacy.py` (2 calques, null non exporté, positions monde) ;
  `--no-fallback` refuse avec un message explicite. L'ancien `.aep` py_aep diverge du
  rendu natif (Time Remaps inertes), d'où le moteur natif par défaut. Validation finale
  sur After Effects 2026 (Windows) à faire côté utilisateur.
- **Time Remap / Freeze Frame** : projet réel `PP - La revue de presse - Karine
  Dubernet_9x16` (deux calques `Segment 105`, une clé Hold à 475,56 s). Relecture
  py_aep 0.18.2 : `time_remap_enabled = True`, clé `475,560 s → 475,560 s` Hold sur
  les deux calques, le troisième `Segment 105` reste sans remap. Relecture py_premiere
  du `.prproj` : deux clips à 471,96–482,92 s avec
  `FrameHoldStart = 120 799 848 960 000` (475,56 s) et aucun `TimeRemapping` ; le masque
  de V3 est un `AEMask2` sous l'Opacité (`InstanceName 01`, payload privé complet).
- **Export en lot** (`tools/export_aep_from_cache.py`, raccourci Bureau
  `Export-AEP-Camille.desktop`) : scan de `/mnt/cache` en 0,13 s → 8 dossiers projet
  détectés (7 « Cvmille » + « Camille », les deux orthographes du cache) ; conversion du
  projet réel `M6+ - Extrait 3 Le revoilà - Gran Turismo_9x16` : **18 segments**,
  1080×1920 @ 25 fps, labels corrects (Segment 3 en Brown = forte amplitude visage).
  Le `.aep` conserve désormais les chemins `/mnt/cache/...` (aucun mapping) ; seul le
  `.prproj` réécrit en `F:/89 Camille/docs/…` (comportement inchangé).
- **Deux corrections py_aep découvertes à l'usage** (repli legacy) : `Project.save()`
  refuse d'écraser
  un `.aep` existant (contourné par écriture temporaire + remplacement atomique) ; le
  chemin réel ne peut pas servir de chemin stocké quand le mapping Windows s'applique
  (le média est importé depuis Linux, puis le chemin est réécrit dans le chunk
  `LIST:Als2 → alas` de la source) — idempotence vérifiée sur 3 passages consécutifs.
- **Versions py_aep** (repli legacy uniquement) : les convertisseurs prennent la plus
  récente présente sous
  `Scripts/` (`py-aep-0.18.2` en priorité, puis `py_aep-0.18.1`, `py_aep-0.13.0` en
  repli ; les deux préfixes de dossier sont acceptés) et détectent à l'exécution sa
  convention de masques (≤ 0.16 : normalisée par la comp ; ≥ 0.17 : pixels source comme
  After Effects) — le **même** `.aep` est produit dans les deux cas (comparé valeur par
  valeur sur le projet Camille). Le choix est journalisé (`py_aep 0.18.2 (…)` dans
  `tools/aep_export.log`). Dépendances 0.18.2 : `attrs`, `PyYAML`, `fonttools`,
  `uharfbuzz` (site utilisateur, `pip install --user uharfbuzz`).
- **Encodage GPU retiré** (`tools/encode_gpu.py`, expérimental) : le transcodage NVENC d'un
  master déjà rendu n'accélérait pas un rendu direct — comparatif mesuré : rendu + H.264 CPU
  3,32 s, rendu ProRes 3,25 s, rendu `--gpu` (composition GPU) 5,15 s, seul l'encodage était
  accéléré (ProRes 2 s → MP4 en 0,71 s). Les rendus passent désormais par Premiere Pro /
  Media Encoder (GPU natif) à partir des `.prproj` ; script, journal et raccourci supprimés.
- **Masques et Gaussian Blur en `.prproj`** : structures relevées sur un projet Premiere 2026
  de référence (masque + blur créés dans l'application, projet décompressé hors ligne) ; le blob
  de tracé est décodé/encodé **octet pour octet** (rectangle 4 sommets = 134 octets, round-trip
  testé) ; comparaison canonique avec Adobe — masque : mêmes 27 paramètres (classes, bornes,
  valeurs), blur : seul l'état d'UI `ECP.Slider.Expanded` omis. Le premier export ouvert dans
  Premiere a révélé **deux erreurs, corrigées depuis** : les payloads base64 doivent porter
  leur `BinaryHash` (clé opaque non validée — sans elle Premiere ignore le payload, le tracé
  du masque devient vide et l'image noire), et le tracé se normalise par la taille **source**
  du clip (un média 1920×1080 monté dans une séquence 1080×1920 donnait des masques hors-champ).
  Vérifié après correction : tracés décodés au pixel du `.ecproj`, hashes écrits, masques sans
  points en état par défaut Adobe (`Type 0`) ; conversion du projet réel
  `PP - LRDP - Bernard Mabille (1)_9x16` : 6 masques écrits sous l'Opacité. Deuxième passe
  utilisateur : un masque conforme, l'autre calque noir — cause trouvée : les masques au tracé
  **ouvert** (vides) du `.ecproj` ne sont pas appliqués par EffectCraft (`render/src/masks.rs` :
  aucune transparence tant que le tracé n'est pas fermé) ; ils ne sont désormais plus écrits
  non plus, le clip reste entier comme dans EffectCraft. **Troisième passe** (projet
  `PP - La revue de presse - Karine Dubernet_9x16`) : V3 masqué + Time Remapping restait
  invisible, même sans masque dans un test dérivé ; l'utilisateur a reconstruit le segment
  dans Premiere 2026 avec un masque redessiné et **Add Frame Hold** — structure de référence
  décisive : le gel est un `FrameHold` natif, pas un `TimeRemapping` ; le masque peut vivre
  sous l'Opacité avec son payload complet (structure simple, conservée) ou dans la variante
  Adobe conteneur vide `SelectionComponents` + masque à payload vide — les deux ont été
  validées dans Premiere 2026. Le convertisseur écrit désormais le Frame Hold et garde la
  structure simple sous l'Opacité.
- **Calques solides & stabilité audio Premiere 2026** (projet réel `Gulli - MSL1 - L’interview « Quand j’étais petit » d’Igor Paixão_9x16_assist`) :
  - **Calques solides** : exportés en solides natifs dans `.aep` (export natif EffectCraft ; repli `py_aep`) et en générateurs Color Matte (`COLR`, couleur RGB dans l'`ImporterPrefs` de 8 octets) dans `.prproj`.
  - **Crash audio Premiere 2026 résolu** : le dump Windows signalait un crash à la lecture dans `AudioFilterHost.dll` (`AR::IsBypassActuallyMute`, `c0000005`). Causes identifiées et corrigées dans `ecproj_to_prproj.py` :
    1. Filtre factice « Internal Volume » par clip supprimé : les clips gardent une chaîne audio vide comme les clips Adobe par défaut.
    2. `ConformedAudioRate` systématiquement renseigné sur les flux audio (`5292000` ticks pour 48 kHz, et sentinelle `i64::MAX` pour les médias sans audio) pour initialiser correctement le moteur de conformage.
    3. Toutes les pistes audio sont désormais reliées à l'`AudioTrackInlet` du bus master (plus de pistes orphelines).
    4. `AudioStream/SampleType` adapté au conteneur/codec (`3` WAV PCM16, `4` MOV/MXF, `7` AAC/MP4, `11` AIFF).
  Validation utilisateur sous Premiere Pro 2026 : solide bleu affiché et lecture timeline fluide sans plantage.
