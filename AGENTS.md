# AGENTS.md — MediaSolution-EC

EffectCraft port of the Adobe CEP "Media Solution v12" panel: one ScriptUI `.jsx` file plus standalone Python converters. No package manager, build step, linter, CI, or test framework. Code comments and docs are in French.

## Layout

- `MediaSolution.jsx` — the entire panel and batch engine. `install.sh` copies it to `~/.config/effectcraft/Scripts/ScriptUI Panels/`. In a panel it builds ScriptUI; headless it exposes `$.global.mediasolutionEC` (`runBatch`, `detect`, `diagnostics`, `status`, `cancel`, `applyConfig`).
- `tools/ecproj_to_aep.py` — `.ecproj` → `.aep` (After Effects 2026+, py_aep).
- `tools/ae_effect_defs.aepdefs` — py_aep effect templates. After adding an effect to `EFFECT_MAP` in `ecproj_to_aep.py`, rebuild it with `python3 tools/extract_ae_effect_defs.py <project.aep> --effects "ADBE …"`.
- `tools/ecproj_to_prproj.py` — `.ecproj` → `.prproj` (Premiere Pro 2026; the format is written directly).
- `tools/export_aep_from_cache.py` — scans `/mnt/cache` project folders and runs both converters in bulk.
- `tests/smoke_driver.js` — end-to-end fixture test; `tests/run_batch_headless.js` — reusable batch driver.
- `docs/COMPATIBILITY.md` — CEP→EC adaptations and limits. `CLI_AUTOMATION.md` — EffectCraft CLI guide written for 0.7.0.

## Commands

Install/update the panel:

```bash
./install.sh
```

Smoke test (needs a running EffectCraft app on ≥0.8; see below):

```bash
cat MediaSolution.jsx tests/smoke_driver.js > /tmp/opencode/ms_smoke.jsx
effectcraft --control 9878 --empty    # only if no app is already running
effectcraft-cli --bridge 9878 exec file.runScript --params '{"path":"/tmp/opencode/ms_smoke.jsx"}'
# expected: SMOKE PASSED
```

The driver hardcodes `FIX` at `tests/smoke_driver.js:11`; edit it if the checkout path differs. It closes the current project, purges, and deletes/regenerates the tracked `tests/fixture/Camille-test/projets/*.ecproj` — don't run it against unsaved work. `tests/run_batch_headless.js` has a placeholder `ROOT` that must be edited first.

Converter checks (no unit tests; write outputs under `/tmp` so tracked fixture `.aep` files are not overwritten):

```bash
python3 tools/ecproj_to_aep.py tests/fixture/Camille-test/projets/clip1_9x16.ecproj -o /tmp/opencode/clip1.aep --verify
python3 tools/ecproj_to_prproj.py tests/fixture/Camille-test/projets/clip1_9x16.ecproj -o /tmp/opencode/clip1.prproj --verify
```

Bulk export: run `python3 tools/export_aep_from_cache.py --dry-run --no-gui` first; `--no-gui` avoids zenity dialogs.

## EffectCraft constraints

- On EffectCraft ≥0.8 (this machine has CLI 0.24.1), headless `effectcraft-cli script` ignores the user's "Allow Scripts to Write Files and Access Network" preference and refuses file writes. `app.run("prefs.set", ...)` is also refused ("Scripts can't change what scripts are allowed to do"), so the panel's "Activer" button cannot enable the gate — use Settings. The README's bare `effectcraft-cli script` smoke command only works on ≤0.7; use the open-app `--bridge` flow above. Pipeline step 8 already auto-selects bridge on ≥0.8 (`STEP8_MODE=auto`).
- `MediaSolution.jsx` is ES5-style (`var`, functions; no `let`/`const`, arrows, or modules) and must stay a single file: EffectCraft scripts cannot include each other. Tests concatenate panel + driver.
- `layer.property("Position")` returns null; use `layer.transform.property(...)` (the `msProp` helper) for Position/Anchor/Scale.
- No CEP/HTML/Node, file dialogs, Python bridge, or reliable `app.scheduleTask`/`$.sleep`/`File.rename`. The interactive batch is deliberately one project per handler; don't reintroduce these.
- By default, generated comps contain a `Null Segments` layer, and segment layers are parented to it. Exporters omit the null and write segment positions in world coordinates; don't "fix" this by exporting the nulls.

## Config / dependencies

- Panel config: `<root>/mediasolution_ec.json`; defaults live in `DEFAULTS` at the top of `MediaSolution.jsx` (root `/mnt/cache`, filter `camille`, output `projets/*.ecproj`).
- Python converters are not pip packages. `ecproj_to_aep.py` finds py_aep under `~/Media_Solution/Scripts/py-aep-0.18.2` (fallbacks 0.18.1/0.13.0), or via `PY_AEP_PATH` / `--py-aep`; deps are `attrs`, `PyYAML`, `fonttools`, `uharfbuzz`. `ecproj_to_prproj.py` optionally uses py_premiere from `~/Media_Solution/Scripts/py-premiere-0.1.0`.
- `.aep` targets After Effects 2026+; `.prproj` targets Premiere Pro 2026. `ecproj_to_prproj.py` rewrites media paths `/mnt/cache` → `F:` by default (`--no-path-map` to keep Linux paths); `ecproj_to_aep.py` only rewrites when `--path-map` is passed.

## Deployment / two-tree sync

- Two copies of this code are in play: this checkout (used by `tools/*.desktop`) and `~/workflow_mediapipe/scripts/MediaSolution-EC` (read by pipeline step 8). Edits here are not automatically seen by the pipeline.
- `deploy.sh` syncs the workflow repo → this checkout and refuses to run when source == destination.
- Hazard: this checkout is also a git repo. `deploy.sh --check` from the workflow repo reports `.git`/`.gitignore` as extras, and `--prune` would delete them (including `.git`). Never run `--prune` against this checkout; sync manually with explicit excludes if needed.
