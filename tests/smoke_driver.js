// ============================================================================
// Smoke driver — exécuté APRÈS MediaSolution.jsx (concaténation) :
//   cat MediaSolution.jsx tests/smoke_driver.js > /tmp/ms_smoke.jsx
//   effectcraft-cli --empty script /tmp/ms_smoke.jsx
//
// Vérifie le workflow complet sur la fixture Camille-test : 2 vidéos, CSV,
// tracking JSON (deux formats), snap 1.5 frame, recentrage intelligent, labels,
// sauvegarde .ecproj, puis relit les projets générés via l'API runtime.
// ============================================================================

var FIX = "/home/kidpixel/Media_Solution/MediaSolution-EC/tests/fixture/Camille-test";
var FRAME = 1001 / 30000;
var failures = [];

function check(cond, label) {
  if (cond) { writeLn("  OK   " + label); }
  else { writeLn("  FAIL " + label); failures.push(label); }
}
function inRange(v, lo, hi) { return v >= lo && v <= hi; }

// 1) Active le gate scripting (équivalent de la préférence GUI)
try {
  app.run("prefs.set", { key: "scripting.allowScriptsWriteFiles", value: true });
} catch (ePref) { writeLn("prefs.set warning: " + ePref); }

// 2) Prépare les sorties
var projets = new Folder(FIX + "/projets");
if (!projets.exists) projets.create();
var old = projets.getFiles("*.ecproj");
for (var i = 0; i < old.length; i++) old[i].remove();
writeLn("Sorties nettoyées (" + old.length + " fichier(s) précédent(s))");

// 3) Batch complet
var res = JSON.parse(mediasolutionEC.runBatch(
  JSON.stringify([FIX]),
  JSON.stringify({ autoDetectRootPath: FIX, enableLogFile: false }),
  0
));
writeLn("runBatch => " + JSON.stringify(res));
check(res.ok === true, "runBatch ok");
check(res.total === 2, "plan = 2 projets (total=" + res.total + ")");
check(res.processed === 2, "2 projets traités (processed=" + res.processed + ")");
check(res.errors.length === 0, "aucune erreur (" + JSON.stringify(res.errors) + ")");

// 4) Vérifications croisées à l'exécution sur les projets générés
function verifyProject(fileName, expect) {
  writeLn("--- " + fileName);
  var f = new File(FIX + "/projets/" + fileName);
  check(f.exists, fileName + " existe sur disque");

  app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
  app.open(f);
  var comp = null;
  for (var i = 1; i <= app.project.numItems; i++) {
    var it = app.project.item(i);
    if (it instanceof CompItem && it.name === expect.comp) comp = it;
  }
  check(comp !== null, "composition '" + expect.comp + "' trouvée");
  if (!comp) return;
  check(comp.width === 1080 && comp.height === 1920, "comp 1080x1920 (" + comp.width + "x" + comp.height + ")");
  check(comp.numLayers === expect.segs.length + 1, "nombre de calques = segments + 1 objet nul (" + comp.numLayers + ")");
  check(app.project.numItems === 3, "3 items (vidéo + comp + image) (" + app.project.numItems + ")");

  // Objet nul parent commun (index 1) : tous les segments y sont parentés.
  var nul = comp.layer(1);
  writeLn("  objet nul : '" + nul.name + "' index=" + nul.index + " pos=" + nul.transform.position.value +
    " anchor=" + nul.transform.anchorPoint.value);
  check(nul.source === null || String(nul.name).indexOf("Null") > -1, "calque 1 = objet nul ('" + nul.name + "')");
  var nulPos = nul.transform.position.value, nulAnchor = nul.transform.anchorPoint.value;
  check(Math.abs(nulPos[0] - 540) < 0.01, "null centre X = 540 (" + nulPos[0] + ")");
  check(Math.abs(nulPos[1]) < 0.01, "null au bord haut Y = 0 (" + nulPos[1] + ")");
  var worldDX = nulPos[0] - nulAnchor[0], worldDY = nulPos[1] - nulAnchor[1];

  for (var s = 0; s < expect.segs.length; s++) {
    var exp = expect.segs[s];
    var L = comp.layer(s + 2);
    writeLn("  segment " + (s + 1) + " : '" + L.name + "' in=" + L.inPoint.toFixed(4) + " out=" + L.outPoint.toFixed(4) + " label=" + L.label);
    check(L.name.indexOf("Segment " + (s + 1) + " ") === 0, "nom du calque " + (s + 1) + " = 'Segment " + (s + 1) + " …'");
    check(inRange(L.inPoint, exp.in - 2 * FRAME, exp.in + 2 * FRAME), "inPoint ≈ " + exp.in.toFixed(3));
    check(inRange(L.outPoint, exp.out - 2 * FRAME, exp.out + 2 * FRAME), "outPoint ≈ " + exp.out.toFixed(3));
    check(L.label === exp.label, "label = " + exp.label + " (" + L.label + ")");
    check(L.audioEnabled === true, "audioEnabled");
    check(L.parent !== null && L.parent.name === nul.name, "parenté à l'objet nul (" + (L.parent ? L.parent.name : "aucun") + ")");
    var anchorX = L.transform.property("Anchor Point").value[0];
    var posX = L.transform.property("Position").value[0];
    var posY = L.transform.property("Position").value[1];
    var scaleX = L.transform.property("Scale").value[0];
    // La position stockée est relative au null (comme dans AE après pick-whip) : c'est la
    // position monde qui doit rester centrée (posX + décalage du null).
    writeLn("    anchorX=" + anchorX + " posX=" + posX + " posY=" + posY + " scaleX=" + scaleX +
      " | position monde X=" + (posX + worldDX) + " Y=" + (posY + worldDY));
    check(inRange(anchorX, exp.anchorLo, exp.anchorHi), "anchor X dans [" + exp.anchorLo + ".." + exp.anchorHi + "] (" + anchorX.toFixed(1) + ")");
    check(Math.abs(posX + worldDX - 540) < 0.01, "position monde X = 540 (" + (posX + worldDX).toFixed(2) + ")");
    check(Math.abs(scaleX - 180) < 0.01, "scale X = 180 (" + scaleX + ")");
  }
}

verifyProject("clip1_9x16.ecproj", {
  comp: "clip1_9x16",
  segs: [
    { in: 15 * FRAME, out: 46 * FRAME, label: 3, anchorLo: 255, anchorHi: 265 },
    { in: 46 * FRAME, out: 90 * FRAME, label: 3, anchorLo: 278, anchorHi: 295 }
  ]
});

verifyProject("clip2_9x16.ecproj", {
  comp: "clip2_9x16",
  segs: [
    { in: 6 * FRAME, out: 36 * FRAME, label: 12, anchorLo: 270, anchorHi: 280 },
    { in: 42 * FRAME, out: 84 * FRAME, label: 3, anchorLo: 305, anchorHi: 315 }
  ]
});

app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);

if (failures.length > 0) {
  throw new Error("SMOKE FAILED (" + failures.length + ") : " + failures.join(" | "));
}
writeLn("SMOKE PASSED");
"smoke-ok";
