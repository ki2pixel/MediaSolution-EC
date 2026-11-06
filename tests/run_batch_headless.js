// ============================================================================
// Driver headless réutilisable — exécution d'un batch sans ouvrir le panel.
//
// Usage (la concaténation est nécessaire : les scripts EffectCraft ne peuvent
// pas s'inclure entre eux) :
//   cat MediaSolution.jsx tests/run_batch_headless.js > /tmp/ms_run.jsx
//   effectcraft-cli script /tmp/ms_run.jsx
// ou, sur une application ouverte avec --control PORT :
//   effectcraft-cli --bridge PORT exec file.runScript --params '{"path":"/tmp/ms_run.jsx"}'
//
// Éditer ROOT ci-dessous (racine contenant les dossiers projet « …/docs »).
// ============================================================================

var ROOT = "/chemin/vers/la/racine";           // ← à adapter
var FOLDERS = [];                              // vide = détection auto sous ROOT
var CONFIG = {
  autoDetectRootPath: ROOT,
  enableLogFile: false                          // log disque optionnel
};

function buildFolderList() {
  if (FOLDERS.length > 0) return FOLDERS;
  var det = JSON.parse(mediasolutionEC.detect(ROOT, CONFIG.autoDetectNameFilter || undefined));
  if (!det.ok) { writeLn("Détection impossible : " + det.error); return []; }
  var out = [];
  for (var i = 0; i < det.folders.length; i++) out.push(det.folders[i].path);
  return out;
}

app.run("prefs.set", { key: "scripting.allowScriptsWriteFiles", value: true });

var folders = buildFolderList();
if (folders.length === 0) {
  writeLn("Aucun dossier à traiter sous " + ROOT);
} else {
  writeLn("Dossiers : " + folders.join(", "));
  var res = mediasolutionEC.runBatch(JSON.stringify(folders), JSON.stringify(CONFIG), 0);
  writeLn("Résultat : " + res);
  var parsed = JSON.parse(res);
  if (!parsed.ok) throw new Error("Batch en échec : " + parsed.error);
  if (parsed.errors && parsed.errors.length > 0) {
    throw new Error("Batch terminé avec " + parsed.errors.length + " erreur(s) : " + parsed.errors.join(" | "));
  }
}
