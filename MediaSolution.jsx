/**
 * Media Solution v12 — port EffectCraft (panel ScriptUI)
 * =====================================================
 * Adaptation du panel CEP « Media Solution v12.0 » (dossier MediaSolution-CEP) à
 * l'écosystème d'EffectCraft : fichier unique .jsx, ScriptUI natif, JavaScript pur.
 *
 * Installer ce fichier dans :
 *   ~/.config/effectcraft/Scripts/ScriptUI Panels/MediaSolution.jsx
 * puis l'ouvrir par Fenêtre ▸ MediaSolution (ou « Install ScriptUI Panel… »).
 *
 * Différences assumées par rapport au panel CEP (voir docs/COMPATIBILITY.md) :
 *   - plus de CEP / HTML / Node : l'UI est un panel ScriptUI dessiné par EffectCraft ;
 *   - plus de bridge Python : les calculs (CSV, snap, recentrage) sont ceux des
 *     fallbacks JavaScript d'origine ;
 *   - plus de dialogs fichier : les chemins se saisissent ou se détectent ;
 *   - projets au format .ecproj (EffectCraft ne lit/écrit pas les .aep) ;
 *   - préférence requise : Préférences ▸ Scripting & Expressions ▸
 *     « Allow Scripts to Write Files and Access Network » (bouton d'activation inclus).
 *
 * Prérequis navigateur d'API : les propriétés de transformation se lisent via
 * layer.transform (layer.property("Position") renvoie null dans EffectCraft).
 */

(function mediaSolutionEC(thisObj) {
  "use strict";

  var SCRIPT_VERSION = "12.0-ec";
  var SCRIPT_BUILD_ID = "12.0.0-ec-20261008-01";

  // ------------------------------------------------------------------ config

  var DEFAULTS = {
    configFileName: "mediasolution_ec.json",
    docsSubFolderName: "docs",
    projectsSubFolderName: "projets",
    autoDetectRootPath: "/mnt/cache",
    autoDetectNameFilter: "camille",
    nullParentSegments: true,
    nullLayerName: "Null Segments",
    compNameSuffix: "_9x16",
    projectFileSuffix: ".ecproj",
    logFileNamePrefix: "MediaSolutionLog_V12.0_EC_",
    videoExtensions: ["mp4", "mov", "avi", "mxf", "mpg", "mpeg", "wmv", "webm", "mkv"],
    videoSkipExtensions: ["mov"],
    videoSkipNames: ["m6+.mp4"],
    imageExtensions: ["jpg", "jpeg", "png", "gif", "tif", "tiff", "psd", "bmp"],
    targetCompWidth: 1080,
    targetCompHeight: 1920,
    defaultVideoScaleX: 180,
    defaultVideoScaleY: 180,
    purgeEveryNProjects: 1,
    maxErrorDetailsInSummary: 15,
    SPREAD_THRESHOLD: 200,
    ENABLE_CONFIDENCE_WEIGHTING: true,
    CONFIDENCE_WEIGHT: 0.35,
    LABEL_HIGH_SPREAD: 12,
    LABEL_STABLE: 3,
    LABEL_FALLBACK_CENTER: 1,
    enableCuts: true,
    enableAutoRecentering: true,
    enableLogFile: false,
    reuseExistingProjects: false,
    maxTrackingJsonBytes: 100 * 1024 * 1024,
    logMaxLines: 400
  };

  // ------------------------------------------------------------------ state

  var MS = {
    config: {},
    state: {
      batchRunning: false,
      cancelRequested: false,
      plan: [],
      unitIndex: 0,
      processed: 0,
      errors: [],
      rootPath: "",
      folderChecks: []
    },
    log: {
      lines: [],
      file: null,
      fileOpened: false,
      buffer: [],
      dir: ""
    },
    ui: null,
    refs: {}
  };

  // ------------------------------------------------------------------ utils

  function pad2(num) { return (num < 10 ? "0" : "") + num; }

  function getTimestamp() {
    var now = new Date();
    return now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) + "_" +
      pad2(now.getHours()) + pad2(now.getMinutes()) + pad2(now.getSeconds());
  }

  function getTimeShort() {
    var now = new Date();
    return pad2(now.getHours()) + ":" + pad2(now.getMinutes()) + ":" + pad2(now.getSeconds());
  }

  function joinPath(dir, name) {
    if (!dir) return name || "";
    var last = dir.charAt(dir.length - 1);
    if (last === "/" || last === "\\") return dir + name;
    return dir + "/" + name;
  }

  function safeJsonParse(text) {
    if (typeof text !== "string") return null;
    var trimmed = text.replace(/^\uFEFF/, "").replace(/^\u00EF\u00BB\u00BF/, "").replace(/^\s+|\s+$/g, "");
    if (!trimmed) return null;
    try { return JSON.parse(trimmed); } catch (e) { return null; }
  }

  function decodeNameForLog(name) {
    try { return decodeURI(String(name)); } catch (e) { return String(name); }
  }

  function readText(fileObj) {
    if (!fileObj || !fileObj.exists) return "";
    try {
      fileObj.encoding = "UTF-8";
      if (!fileObj.open("r")) return "";
      var content = fileObj.read();
      fileObj.close();
      return content || "";
    } catch (e) {
      return "";
    }
  }

  // Écriture directe (EffectCraft n'implémente pas File.rename ; écriture atomique
  // impossible). Renvoie true/false, ne jette pas : la préférence scripting peut
  // refuser l'écriture.
  function writeText(fileObj, text) {
    try {
      fileObj.encoding = "UTF-8";
      if (!fileObj.open("w")) return false;
      fileObj.write(text);
      fileObj.close();
      return true;
    } catch (e) {
      return false;
    }
  }

  function updateLogUI() {
    if (MS.refs.log) {
      try {
        var lines = MS.log.lines;
        var start = lines.length > MS.config.logMaxLines ? lines.length - MS.config.logMaxLines : 0;
        MS.refs.log.text = lines.slice(start).join("\n");
      } catch (e) { }
    }
  }

  function setStatus(text) {
    if (MS.refs.status) {
      try { MS.refs.status.text = text; } catch (e) { }
    }
  }

  function msLog(message, level) {
    level = level || "info";
    var line = getTimeShort() + " | " + message;
    MS.log.lines.push("[" + level.toUpperCase() + "] " + line);
    updateLogUI();
    try { $.writeln("[" + level.toUpperCase() + "] " + line); } catch (e) { }

    // Fichier de session (optionnel, best effort)
    if (MS.config.enableLogFile && MS.log.fileOpened && MS.log.file) {
      MS.log.buffer.push(line);
      if (MS.log.buffer.length >= 5) flushLogBuffer();
    }
  }

  function flushLogBuffer() {
    if (!MS.log.fileOpened || !MS.log.file || MS.log.buffer.length === 0) return;
    try {
      if (MS.log.file.open("a")) {
        MS.log.file.encoding = "UTF-8";
        for (var i = 0; i < MS.log.buffer.length; i++) MS.log.file.writeln(MS.log.buffer[i]);
        MS.log.file.close();
        MS.log.buffer = [];
      }
    } catch (e) { }
  }

  // Fichier de log de session, écrit dans le dossier projet traité (ex. « …/117 Camille ») :
  // un batch peut couvrir plusieurs dossiers, le log suit donc le dossier courant (voir
  // ensureLogForUnit).
  function initLogFile(dir, folderName) {
    if (!MS.config.enableLogFile) return;
    if (!dir) return;
    flushLogBuffer(); // résidu du dossier précédent (batch multi-dossiers)
    try {
      var logFile = new File(joinPath(dir, MS.config.logFileNamePrefix + getTimestamp() + ".log"));
      MS.log.file = logFile;
      MS.log.fileOpened = true;
      MS.log.buffer = [];
      var where = folderName ? " — dossier " + folderName : "";
      writeText(logFile, "=== Media Solution v12 (EffectCraft) — session " + getTimestamp() + where + " ===\n");
      MS.log.fileOpened = logFile.exists;
      MS.log.dir = dir;
    } catch (e) {
      MS.log.fileOpened = false;
      MS.log.file = null;
    }
  }

  function closeLogFile() {
    flushLogBuffer();
    try { if (MS.log.file && MS.log.file.isOpen) MS.log.file.close(); } catch (e) { }
    MS.log.fileOpened = false;
    MS.log.file = null;
    MS.log.dir = "";
  }

  function mergeConfig(partial) {
    if (!partial || typeof partial !== "object") return;
    for (var k in partial) {
      if (Object.prototype.hasOwnProperty.call(partial, k) && DEFAULTS.hasOwnProperty(k)) {
        MS.config[k] = partial[k];
      }
    }
  }

  function configAsJson() {
    var out = {};
    for (var k in DEFAULTS) {
      if (DEFAULTS.hasOwnProperty(k)) out[k] = MS.config[k];
    }
    return JSON.stringify(out);
  }

  // Vérifie l'accès disque du scripting par une écriture sonde (révèle la
  // préférence « Allow Scripts to Write Files and Access Network »).
  function checkScriptingAccess(dirPath) {
    var dir = dirPath;
    if (!dir) {
      try { dir = Folder.current ? Folder.current.fsName : ""; } catch (e) { dir = ""; }
    }
    if (!dir) return { ok: false, error: "aucun dossier de référence" };
    try {
      var probe = new File(joinPath(dir, ".mediasolution_probe.tmp"));
      if (!probe.open("w")) return { ok: false, error: probe.error || "écriture refusée" };
      probe.write("probe");
      probe.close();
      probe.remove();
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.toString() };
    }
  }

  // ------------------------------------------------------------------ fichiers / dossiers

  function getFilesByExtensions(folder, extensions) {
    if (!folder || !folder.exists) return [];
    if (!extensions || extensions.length === 0) return [];
    var allFiles = folder.getFiles();
    var matched = [];
    for (var i = 0; i < allFiles.length; i++) {
      var file = allFiles[i];
      if (!(file instanceof File)) continue;
      var ext = file.name.toLowerCase().split(".").pop();
      for (var k = 0; k < extensions.length; k++) {
        if (extensions[k] === ext) { matched.push(file); break; }
      }
    }
    return matched;
  }

  function isVideoSkipped(fileName, cfg) {
    if (!fileName) return false;
    var variants = [String(fileName).toLowerCase()];
    try { variants.push(decodeURI(String(fileName)).toLowerCase()); } catch (e1) { }
    try { variants.push(decodeURIComponent(String(fileName)).toLowerCase()); } catch (e2) { }
    for (var v = 0; v < variants.length; v++) {
      var name = variants[v];
      var ext = name.split(".").pop();
      for (var s = 0; s < cfg.videoSkipExtensions.length; s++) {
        if (cfg.videoSkipExtensions[s] === ext) return true;
      }
      for (var t = 0; t < cfg.videoSkipNames.length; t++) {
        if (cfg.videoSkipNames[t] === name) return true;
      }
    }
    return false;
  }

  function getProcessableVideoFiles(folder, cfg) {
    var files = getFilesByExtensions(folder, cfg.videoExtensions);
    var kept = [];
    var skipped = [];
    for (var i = 0; i < files.length; i++) {
      if (isVideoSkipped(files[i].name, cfg)) skipped.push(files[i].name);
      else kept.push(files[i]);
    }
    if (skipped.length > 0) {
      msLog(skipped.length + " vidéo(s) ignorée(s) par exclusion batch : " + skipped.join(", "), "info");
    }
    return kept;
  }

  // ------------------------------------------------------------------ CSV / timecodes

  function splitCsvLine(line) {
    var result = [];
    var current = "";
    var inQuotes = false;
    var i = 0;
    while (i < line.length) {
      var ch = line.charAt(i);
      if (inQuotes) {
        if (ch === '"') {
          if (line.charAt(i + 1) === '"') { current += '"'; i += 2; continue; }
          inQuotes = false;
        } else {
          current += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        result.push(current);
        current = "";
      } else {
        current += ch;
      }
      i++;
    }
    result.push(current);
    return result;
  }

  function csvTimecodeToSeconds(tc) {
    try {
      var parts = String(tc).split(":");
      if (parts.length === 3) {
        var s3 = String(parts[2]).replace(",", ".");
        return (parseFloat(parts[0]) * 3600) + (parseFloat(parts[1]) * 60) + parseFloat(s3);
      }
      if (parts.length === 2) {
        var s2 = String(parts[1]).replace(",", ".");
        return (parseFloat(parts[0]) * 60) + parseFloat(s2);
      }
      throw new Error("format attendu HH:MM:SS.mmm ou MM:SS");
    } catch (e) {
      msLog("Timecode invalide '" + tc + "' : " + e.message, "error");
      return -1;
    }
  }

  function secondsToTimecode(timeInSeconds) {
    var absTime = Math.abs(timeInSeconds);
    var hours = Math.floor(absTime / 3600);
    var minutes = Math.floor((absTime % 3600) / 60);
    var seconds = absTime % 60;
    var hStr = (hours < 10 ? "0" : "") + hours;
    var mStr = (minutes < 10 ? "0" : "") + minutes;
    var sStr = seconds.toFixed(3);
    if (seconds < 10) sStr = "0" + sStr;
    return (timeInSeconds < 0 ? "-" : "") + hStr + ":" + mStr + ":" + sStr.replace(".", ",");
  }

  function parseCsvSegments(content) {
    var lines = String(content).split(/\r\n|\r|\n/);
    var segments = [];
    for (var i = 1; i < lines.length; i++) {
      var line = lines[i].replace(/^\s+|\s+$/g, "");
      if (line === "") continue;
      var columns = splitCsvLine(line);
      if (columns.length < 3) continue;
      var startTime = csvTimecodeToSeconds(columns[1].replace(/^\s+|\s+$/g, ""));
      var endTime = csvTimecodeToSeconds(columns[2].replace(/^\s+|\s+$/g, ""));
      if (startTime >= 0 && endTime > startTime) {
        segments.push({ num: columns[0].replace(/^\s+|\s+$/g, ""), startTime: startTime, endTime: endTime });
      }
    }
    return segments;
  }

  // ------------------------------------------------------------------ tracking JSON

  function trimString(value) {
    if (value === null || value === undefined) return "";
    return value.toString().replace(/^\s+|\s+$/g, "");
  }

  function normalizeToStringArray(value) {
    if (value === null || value === undefined) return [];
    if (value instanceof Array) {
      var out = [];
      for (var i = 0; i < value.length; i++) {
        var v = trimString(value[i]);
        if (v) out.push(v);
      }
      return out;
    }
    var asString = trimString(value);
    if (!asString) return [];
    if (asString.indexOf(",") > -1) {
      var parts = asString.split(",");
      var out2 = [];
      for (var j = 0; j < parts.length; j++) {
        var p = trimString(parts[j]);
        if (p) out2.push(p);
      }
      return out2;
    }
    return [asString];
  }

  function indexReducedTrackingFrames(framesArray) {
    var extracted = {};
    var maxFrameSeen = 0;
    if (!framesArray || !(framesArray instanceof Array)) return { dataByFrame: extracted, maxFrame: maxFrameSeen };
    for (var i = 0; i < framesArray.length; i++) {
      var frameObj = framesArray[i];
      if (!frameObj) continue;
      var frameNum = parseInt(frameObj.frame, 10);
      if (isNaN(frameNum)) continue;
      if (frameNum > maxFrameSeen) maxFrameSeen = frameNum;
      var tracked = frameObj.tracked_objects;
      if (!tracked || !(tracked instanceof Array)) continue;
      for (var j = 0; j < tracked.length; j++) {
        var obj = tracked[j];
        if (!obj) continue;
        var source = obj.source;
        var label = obj.label;
        var isRelevant = (source === "face_landmarker") || (source === "object_detector" && label === "person");
        if (!isRelevant) continue;
        var centroidX = parseFloat(obj.centroid_x);
        if (isNaN(centroidX)) continue;
        var bboxWidth = parseFloat(obj.bbox_width);
        var bboxHeight = parseFloat(obj.bbox_height);
        var bboxSurface = (!isNaN(bboxWidth) && !isNaN(bboxHeight)) ? bboxWidth * bboxHeight : 0;
        var confidence = parseFloat(obj.confidence);
        if (isNaN(confidence)) confidence = 0;
        var jawOpenVal = null;
        if (typeof obj.jawOpen === "number") jawOpenVal = obj.jawOpen;
        else if (typeof obj.jaw_open === "number") jawOpenVal = obj.jaw_open;
        var isSpeakingVal = (obj.is_speaking === true || obj.is_speaking === 1);
        var tierVal = (typeof obj.tier === "number") ? obj.tier : null;
        var speakerLabelVal = obj.speaker_label ? String(obj.speaker_label) : null;

        if (!extracted[frameNum]) extracted[frameNum] = [];
        extracted[frameNum].push({
          id: obj.id,
          centroid_x: centroidX,
          source: source,
          label: label,
          speaker_label: speakerLabelVal,
          video_speakers: normalizeToStringArray(obj.active_speakers || obj.video_speakers),
          bbox_width: bboxWidth || 0,
          bbox_height: bboxHeight || 0,
          bbox_surface: bboxSurface,
          confidence: confidence,
          jawOpen: jawOpenVal,
          is_speaking: isSpeakingVal,
          tier: tierVal
        });
      }
    }
    return { dataByFrame: extracted, maxFrame: maxFrameSeen };
  }

  // Chargement du tracking : JSON natif d'EffectCraft (plus de scan streaming —
  // la lecture complète est traitée par le parseur natif, rapide).
  function loadVideoTrackingData(videoJsonFile, cfg) {
    var empty = { dataByFrame: {}, maxFrame: 0, audioByFrame: {}, segmentRecommendations: null };
    if (!videoJsonFile || !videoJsonFile.exists) return empty;
    try {
      var size = videoJsonFile.length || 0;
      if (size > (cfg.maxTrackingJsonBytes || DEFAULTS.maxTrackingJsonBytes)) {
        msLog("Tracking JSON ignoré (trop volumineux : " + size + " octets) : " + videoJsonFile.name, "warning");
        return empty;
      }
      var root = safeJsonParse(readText(videoJsonFile));
      if (!root) {
        msLog("Tracking JSON illisible : " + videoJsonFile.name, "warning");
        return empty;
      }
      var segRec = root.segment_recommendations || root.segmentRecommendations || null;
      if (root.dataByFrame) {
        return {
          dataByFrame: root.dataByFrame || {},
          maxFrame: root.maxFrame || 0,
          audioByFrame: root.audioByFrame || {},
          segmentRecommendations: segRec
        };
      }
      if (root.frames_analysis && (root.frames_analysis instanceof Array)) {
        var indexed = indexReducedTrackingFrames(root.frames_analysis);
        var audioByFrame = {};
        try {
          var jsonName = videoJsonFile.name;
          var audioStem = jsonName;
          if (audioStem.lastIndexOf("_tracking.json") > -1) {
            audioStem = audioStem.substring(0, audioStem.lastIndexOf("_tracking.json"));
          } else if (audioStem.lastIndexOf(".json") > -1) {
            audioStem = audioStem.substring(0, audioStem.lastIndexOf(".json"));
          }
          var audioFile = new File(joinPath(videoJsonFile.parent.fsName, audioStem + "_audio.json"));
          if (audioFile.exists) {
            var audioRoot = safeJsonParse(readText(audioFile));
            if (audioRoot && audioRoot.frames && (audioRoot.frames instanceof Array)) {
              for (var af = 0; af < audioRoot.frames.length; af++) {
                var aFrame = audioRoot.frames[af];
                if (aFrame && aFrame.audio_info) {
                  audioByFrame[parseInt(aFrame.frame, 10)] = aFrame.audio_info;
                }
              }
            } else if (audioRoot && audioRoot.audioByFrame) {
              audioByFrame = audioRoot.audioByFrame;
            }
          }
        } catch (eAudio) { }
        return {
          dataByFrame: indexed.dataByFrame,
          maxFrame: indexed.maxFrame,
          audioByFrame: audioByFrame,
          segmentRecommendations: segRec
        };
      }
      msLog("Tracking JSON sans 'dataByFrame' ni 'frames_analysis' : " + videoJsonFile.name, "warning");
      return empty;
    } catch (e) {
      msLog("Erreur lecture tracking " + videoJsonFile.name + " : " + e.toString(), "warning");
      return empty;
    }
  }

  function findTrackingJsonForVideo(docsFolder, videoName) {
    var names = [String(videoName)];
    try { names.push(decodeURI(String(videoName))); } catch (e1) { }
    try { names.push(decodeURIComponent(String(videoName))); } catch (e2) { }
    for (var n = 0; n < names.length; n++) {
      var baseName = names[n];
      if (baseName.lastIndexOf(".") > -1) baseName = baseName.substring(0, baseName.lastIndexOf("."));
      var variants = [baseName + "_ae.json", baseName + "_tracking.json", baseName + ".json"];
      for (var i = 0; i < variants.length; i++) {
        var f = new File(joinPath(docsFolder.fsName, variants[i]));
        if (f.exists) return f;
      }
    }
    return null;
  }

  function calculateStats(numberArray) {
    if (!numberArray || numberArray.length === 0) return { min: 0, max: 0, spread: 0, average: 0, count: 0 };
    var sum = 0;
    var minVal = numberArray[0];
    var maxVal = numberArray[0];
    for (var i = 0; i < numberArray.length; i++) {
      sum += numberArray[i];
      if (numberArray[i] < minVal) minVal = numberArray[i];
      if (numberArray[i] > maxVal) maxVal = numberArray[i];
    }
    return { min: minVal, max: maxVal, spread: maxVal - minVal, average: sum / numberArray.length, count: numberArray.length };
  }

  function calculateTrimmedStats(numberArray) {
    if (!numberArray || numberArray.length === 0) return { min: 0, max: 0, spread: 0, average: 0, count: 0 };
    var count = numberArray.length;
    var sum = 0;
    var minVal = numberArray[0];
    var maxVal = numberArray[0];
    for (var i = 0; i < count; i++) {
      var v = numberArray[i];
      sum += v;
      if (v < minVal) minVal = v;
      if (v > maxVal) maxVal = v;
    }
    var spread = maxVal - minVal;
    if (count <= 10) {
      return { min: minVal, max: maxVal, spread: spread, average: sum / count, count: count };
    }

    var sorted = [];
    for (var s = 0; s < count; s++) sorted.push(numberArray[s]);
    sorted.sort(function (a, b) { return a - b; });

    var k = Math.floor(count * 0.10);
    if (k < 1) k = 1;
    var trimmedSum = 0;
    var trimmedCount = 0;
    for (var t = k; t < count - k; t++) {
      trimmedSum += sorted[t];
      trimmedCount++;
    }
    var trimmedAvg = (trimmedCount > 0) ? (trimmedSum / trimmedCount) : (sum / count);
    return { min: minVal, max: maxVal, spread: spread, average: trimmedAvg, count: count };
  }

  function clamp01(v) {
    if (v === null || v === undefined) return 0;
    var f = parseFloat(v);
    if (isNaN(f)) return 0;
    if (f < 0) return 0;
    if (f > 1) return 1;
    return f;
  }

  function computePresenceScore(presence, avgConfidence, cfg) {
    if (!cfg.ENABLE_CONFIDENCE_WEIGHTING) return presence;
    var w = parseFloat(cfg.CONFIDENCE_WEIGHT);
    if (isNaN(w) || w < 0) w = 0;
    return presence * (1 + (clamp01(avgConfidence) * w));
  }

  function isFaceCandidate(obj) {
    if (!obj) return false;
    var src = (obj.source || obj.label || "").toLowerCase();
    return src.indexOf("face") !== -1;
  }

  function isObjectSpeakingInFrame(obj, audioFrame) {
    if (!audioFrame || !audioFrame.is_speech_present) {
      return false;
    }
    if (obj.is_speaking === true || obj.audio_match === true) {
      return true;
    }
    var activeSpeakers = audioFrame.active_speaker_labels || audioFrame.active_speakers || audioFrame.video_speakers || [];
    if (activeSpeakers && activeSpeakers.length > 0) {
      if (obj.speaker_label) {
        for (var s = 0; s < activeSpeakers.length; s++) {
          if (activeSpeakers[s] === obj.speaker_label) return true;
        }
      }
      var objSpeakers = obj.video_speakers || obj.active_speakers || [];
      if (objSpeakers && objSpeakers.length > 0) {
        for (var os = 0; os < objSpeakers.length; os++) {
          for (var as = 0; as < activeSpeakers.length; as++) {
            if (objSpeakers[os] === activeSpeakers[as]) return true;
          }
        }
      }
    }
    var jaw = (typeof obj.jawOpen === "number") ? obj.jawOpen : (typeof obj.jaw_open === "number" ? obj.jaw_open : null);
    if (isFaceCandidate(obj) && jaw !== null && jaw > 0.12) {
      return true;
    }
    return false;
  }

  // Sélection robuste alignée STEP6 (4 paliers + arbitrage audio + moyenne tronquée)
  function pickBestObjectInRange(trackingData, p2, p3, p4, p5) {
    var dataByFrame = trackingData && trackingData.dataByFrame ? trackingData.dataByFrame : null;
    if (!dataByFrame) return null;
    var audioByFrame = trackingData.audioByFrame || {};

    var minFrame, maxFrame, cfg;
    if (arguments.length >= 4 && typeof p3 === "number" && typeof p4 === "number") {
      var frameRate = p2;
      var inPoint = p3;
      var outPoint = p4;
      cfg = p5 || {};
      minFrame = Math.floor(inPoint * frameRate);
      maxFrame = Math.ceil(outPoint * frameRate);
    } else {
      minFrame = p2;
      maxFrame = p3;
      cfg = p4 || {};
    }

    var totalSegmentFrames = Math.max(1, maxFrame - minFrame + 1);
    var statsById = {};
    var seen = 0;

    for (var f = minFrame; f <= maxFrame; f++) {
      var frameObjs = dataByFrame[f];
      if (!frameObjs || !frameObjs.length) continue;
      var audioFrame = audioByFrame[f] || null;
      var speechActiveOnFrame = Boolean(audioFrame && audioFrame.is_speech_present);

      for (var i = 0; i < frameObjs.length; i++) {
        var obj = frameObjs[i];
        if (!obj) continue;
        var id = String(obj.id !== undefined ? obj.id : obj.object_id);
        if (!statsById[id]) {
          statsById[id] = {
            id: id,
            source: obj.source,
            isFace: isFaceCandidate(obj),
            presence: 0,
            speechFrames: 0,
            confSum: 0,
            areaSum: 0,
            xValues: [],
            minX: Infinity,
            maxX: -Infinity
          };
        }
        var st = statsById[id];
        if (isFaceCandidate(obj)) {
          st.isFace = true;
          st.source = "face_landmarker";
        }
        st.presence += 1;
        var conf = (typeof obj.confidence === "number") ? obj.confidence : 0.5;
        st.confSum += conf;

        var surf = 0;
        if (typeof obj.bbox_surface === "number" && obj.bbox_surface > 0) {
          surf = obj.bbox_surface;
        } else if (typeof obj.bbox_width === "number" && typeof obj.bbox_height === "number") {
          surf = obj.bbox_width * obj.bbox_height;
        } else if (typeof obj.width === "number" && typeof obj.height === "number") {
          surf = obj.width * obj.height;
        }
        st.areaSum += surf;

        var cx = (typeof obj.centroid_x === "number") ? obj.centroid_x : ((typeof obj.center_x === "number") ? obj.center_x : null);
        if (cx !== null && !isNaN(cx)) {
          st.xValues.push(cx);
          if (cx < st.minX) st.minX = cx;
          if (cx > st.maxX) st.maxX = cx;
        }

        if (isObjectSpeakingInFrame(obj, audioFrame) || (speechActiveOnFrame && frameObjs.length === 1)) {
          st.speechFrames += 1;
        }
        seen++;
      }
    }

    if (seen === 0) return null;

    var bestCandidate = null;
    var confWeight = (cfg && typeof cfg.CONFIDENCE_WEIGHT === "number") ? cfg.CONFIDENCE_WEIGHT : 0.35;

    for (var key in statsById) {
      if (!Object.prototype.hasOwnProperty.call(statsById, key)) continue;
      var cand = statsById[key];
      if (cand.presence <= 0 || cand.xValues.length === 0) continue;

      // Rejeter les pistes parasites / fantômes (< 10 % du segment et < 3 frames)
      if (cand.presence < 3 && (cand.presence / totalSegmentFrames) < 0.10) {
        continue;
      }

      var avgConf = cand.confSum / cand.presence;
      var speechRatio = cand.speechFrames / cand.presence;
      var hasAudioSupport = (cand.speechFrames >= 3) || (speechRatio >= 0.20);

      // Hiérarchie de sélection à 4 paliers (Parité STEP6) :
      // Tier 4: best_audio_face
      // Tier 3: best_audio_person
      // Tier 2: best_face
      // Tier 1: best_person
      if (hasAudioSupport && cand.isFace) {
        cand.tier = 4;
      } else if (hasAudioSupport && !cand.isFace) {
        cand.tier = 3;
      } else if (cand.isFace) {
        cand.tier = 2;
      } else {
        cand.tier = 1;
      }

      cand.score = cand.presence * (1.0 + (avgConf * confWeight) + (speechRatio * 0.50));
      cand.avgArea = cand.areaSum / cand.presence;
      cand.spreadX = (cand.maxX > cand.minX && cand.maxX !== -Infinity) ? (cand.maxX - cand.minX) : 0;

      if (!bestCandidate) {
        bestCandidate = cand;
      } else if (cand.tier > bestCandidate.tier) {
        bestCandidate = cand;
      } else if (cand.tier === bestCandidate.tier) {
        if (cand.score > bestCandidate.score) {
          bestCandidate = cand;
        } else if (Math.abs(cand.score - bestCandidate.score) < 1e-4 && cand.avgArea > bestCandidate.avgArea) {
          bestCandidate = cand;
        }
      }
    }

    if (!bestCandidate) return null;

    // Moyenne tronquée robuste en X (Anti-Outliers)
    var stats = calculateTrimmedStats(bestCandidate.xValues);
    return {
      id: bestCandidate.id,
      source: bestCandidate.source || (bestCandidate.isFace ? "face_landmarker" : "object_detector"),
      centerX: stats.average,
      spread: stats.spread,
      samples: stats.count,
      tier: bestCandidate.tier
    };
  }

  // ------------------------------------------------------------------ projet / comps / calques

  // EffectCraft : les propriétés de transformation vivent sous le groupe « Transform »
  // (layer.property("Position") → null). Ce helper est LE point d'adaptation du port.
  function msProp(layer, name) {
    return layer.transform.property(name);
  }

  function findCompByName(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var item = app.project.item(i);
      if (item instanceof CompItem && item.name === name) return item;
    }
    return null;
  }

  function findCompBySuffix(suffix) {
    for (var i = 1; i <= app.project.numItems; i++) {
      var item = app.project.item(i);
      if (item instanceof CompItem && item.name.indexOf(suffix) > -1) return item;
    }
    return null;
  }

  function findFirstComp() {
    for (var i = 1; i <= app.project.numItems; i++) {
      var item = app.project.item(i);
      if (item instanceof CompItem) return item;
    }
    return null;
  }

  function findMainVideoLayer(comp) {
    if (!comp) return null;
    var candidates = [];
    for (var i = 1; i <= comp.numLayers; i++) {
      try {
        var layer = comp.layer(i);
        if (layer instanceof AVLayer && layer.hasVideo && !layer.locked &&
            layer.source instanceof FootageItem && !layer.source.mainSource.isStill) {
          var score = 10;
          if (layer.name.toLowerCase().indexOf("audio") > -1) score -= 5;
          candidates.push({ layer: layer, score: score });
        }
      } catch (e) {
        msLog("WARN (findMainVideoLayer) calque " + i + " : " + e.toString(), "warning");
      }
    }
    if (candidates.length === 0) return null;
    candidates.sort(function (a, b) { return b.score - a.score; });
    return candidates[0].layer;
  }

  function closeAllCompViewers() {
    // EffectCraft n'expose pas app.project.viewers : no-op conservé (garde d'origine).
    try {
      if (!app.project.viewers) return;
    } catch (e) { }
  }

  function closeCurrentProject() {
    try { closeAllCompViewers(); } catch (e0) { }
    try { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch (e) { }
  }

  function ensureFolder(folderPath) {
    try {
      var folder = new Folder(folderPath);
      if (!folder.exists) return folder.create();
      return true;
    } catch (e) {
      return false;
    }
  }

  // ------------------------------------------------------------------ étapes métier

  function importImages(docsFolder, cfg) {
    var imageFiles = getFilesByExtensions(docsFolder, cfg.imageExtensions);
    var count = 0;
    for (var i = 0; i < imageFiles.length; i++) {
      try {
        app.project.importFile(new ImportOptions(imageFiles[i]));
        count++;
      } catch (e) {
        msLog("WARN : import image impossible '" + decodeNameForLog(imageFiles[i].name) + "' : " + e.toString(), "warning");
      }
    }
    if (count > 0) msLog(count + " image(s) importée(s) depuis docs/", "info");
    return count;
  }

  function createBaseProjectInMemory(unit, cfg) {
    var footageItem = app.project.importFile(new ImportOptions(new File(unit.videoPath)));
    var pixelAspect = footageItem.pixelAspect;
    if (!(pixelAspect > 0)) pixelAspect = 1;
    var compName = unit.baseName + cfg.compNameSuffix;
    var comp = app.project.items.addComp(
      compName,
      footageItem.width,
      footageItem.height,
      pixelAspect,
      Math.max(footageItem.duration, 1.0),
      Math.max(footageItem.frameRate, 25.0)
    );
    var layer = comp.layers.add(footageItem);
    try { layer.audioEnabled = !!footageItem.hasAudio; } catch (eAudio) { }
    unit.compName = comp.name;
    msLog("Projet de base créé : comp '" + comp.name + "' (" + comp.width + "x" + comp.height + ", " +
      Number(comp.duration).toFixed(3) + " s)", "info");
    return comp;
  }

  function prepareLayer(comp, layer, cfg) {
    comp.width = cfg.targetCompWidth;
    comp.height = cfg.targetCompHeight;
    var layerWidth = layer.width;
    msProp(layer, "Anchor Point").setValue([layerWidth / 2, 0, 0]);
    msProp(layer, "Position").setValue([comp.width / 2, 0, 0]);
    msProp(layer, "Scale").setValue([cfg.defaultVideoScaleX || 100, cfg.defaultVideoScaleY || 100]);
    msLog("Calque préparé : comp " + comp.width + "x" + comp.height + ", anchor top-centre, position [" +
      (comp.width / 2) + ", 0], échelle " + (cfg.defaultVideoScaleX || 100) + "%", "info");
  }

  // Recentrage global par défaut (fallback JS d'origine : centre horizontal comp).
  function applyDefaultCentering(comp, layer) {
    var centerX = comp.width / 2;
    var anchorProp = msProp(layer, "Anchor Point");
    var posProp = msProp(layer, "Position");
    var anchorV = anchorProp.value;
    var posV = posProp.value;
    var anchorOut = [centerX, anchorV[1]];
    if (anchorV && anchorV.length > 2) anchorOut.push(anchorV[2]);
    var posOut = [centerX, posV[1]];
    if (posV && posV.length > 2) posOut.push(posV[2]);
    anchorProp.setValue(anchorOut);
    posProp.setValue(posOut);
    msLog("Recentrage par défaut appliqué (centre comp X=" + centerX + ")", "info");
  }

  // Bornage de l'ancre de recentrage à ce que le calque peut réellement couvrir.
  //
  // L'ancre est exprimée en pixels de la source, l'échelle en pourcentage. Avec un
  // calque positionné au centre de la composition (Position = [comp.width / 2, 0]),
  // la composition n'est entièrement couverte que si l'ancre reste dans
  // [demiComp / échelle, largeurSource - demiComp / échelle] : au-delà, le recentrage
  // pousse le calque hors champ et laisse apparaître le fond (bug historique conservé
  // du panneau CEP - ex. source 1920 px à 180 % dans une comp de 1080 px : [300, 1620]).
  function clampAnchorToCoverage(centerX, compWidth, sourceWidth, scalePercent) {
    var scale = Math.abs(scalePercent) / 100;
    if (!(scale > 0) || !(sourceWidth > 0) || !(compWidth > 0)) return centerX;
    var halfCompInLayer = (compWidth / 2) / scale;
    var maxAnchor = sourceWidth - halfCompInLayer;
    if (maxAnchor < halfCompInLayer) return sourceWidth / 2; // calque plus étroit que la composition
    return Math.min(Math.max(centerX, halfCompInLayer), maxAnchor);
  }

  function findMatchingCsv(docsFolder, compName, cfg) {
    var csvFiles = getFilesByExtensions(docsFolder, ["csv"]);
    var baseName = compName;
    if (baseName.indexOf(cfg.compNameSuffix) > -1) baseName = baseName.replace(cfg.compNameSuffix, "");
    for (var k = 0; k < csvFiles.length; k++) {
      var csvName = decodeURI(csvFiles[k].name);
      var csvBase = csvName;
      var lastDot = csvBase.lastIndexOf(".");
      if (lastDot > 0) csvBase = csvBase.substring(0, lastDot);
      if (csvBase === baseName) return { file: csvFiles[k], baseName: baseName, count: csvFiles.length };
    }
    return { file: null, baseName: baseName, count: csvFiles.length };
  }

  // Objet nul parent commun des segments. Parente chaque segment par script : dans l'app,
  // sélectionner plusieurs calques puis les parenter n'en parente qu'un seul (limitation
  // actuelle de la multi-sélection), ce qui oblige sinon à les parenter un par un.
  // `layer.parent = nul` compense la transformation (comme le pick-whip) : le rendu des
  // découpes reste identique, seule l'expression du transform change.
  function createSegmentNull(comp, layers, cfg) {
    if (cfg.nullParentSegments === false) return null;
    if (!layers || layers.length === 0) return null;
    var nul = null;
    try {
      nul = comp.layers.addNull(comp.duration);
      try { nul.name = cfg.nullLayerName || "Null Segments"; } catch (eName) { }
      try { nul.label = cfg.LABEL_STABLE; } catch (eLabel) { }
    } catch (e) {
      msLog("Objet nul non créé (" + e.toString() + ") : segments non parentés", "warning");
      return null;
    }
    // Position haut-centre de la comp (même convention que prepareLayer) : le null reste
    // centré horizontalement, mais vient se fixer sur le bord haut, là où les segments ont
    // leur anchor. À faire AVANT la parenté : layer.parent compense la transformation, la
    // position monde des segments reste donc inchangée.
    try {
      msProp(nul, "Position").setValue([comp.width / 2, 0]);
    } catch (ePos) {
      msLog("Position du null non ajustée (" + ePos.toString() + ") : null laissé au centre de la comp", "warning");
    }
    var parented = 0;
    for (var i = 0; i < layers.length; i++) {
      try {
        layers[i].parent = nul;
        parented++;
      } catch (eParent) {
        msLog("Parenté impossible pour '" + layers[i].name + "' : " + eParent.toString(), "warning");
      }
    }
    msLog("Objet nul '" + nul.name + "' créé (haut-centre de la comp) : " + parented + "/" + layers.length +
      " segment(s) parenté(s)", "success");
    return nul;
  }

  // Application des cuts (port de applyCutsToSelectedLayer) : segments CSV,
  // snap 1.5 frame, recentrage intelligent par segment depuis le tracking.
  function applyCutsToLayer(comp, targetLayer, docsFolder, cfg, explicitCsv) {
    var result = { segments: 0, warning: null, csvName: null, trackingUsed: false };

    var match = explicitCsv
      ? { file: explicitCsv, baseName: comp.name, count: 1 }
      : findMatchingCsv(docsFolder, comp.name, cfg);
    if (!match.file) {
      result.warning = "Aucun CSV correspondant à '" + match.baseName + "' (" + match.count + " CSV dans docs/)";
      msLog("WARN : " + result.warning, "warning");
      return result;
    }
    result.csvName = match.file.name;

    var content = readText(match.file);
    if (!content) {
      result.warning = "CSV illisible : " + match.file.name;
      msLog("WARN : " + result.warning, "warning");
      return result;
    }

    var parsedSegments = parseCsvSegments(content);
    if (parsedSegments.length === 0) {
      result.warning = "Aucun segment valide dans " + match.file.name;
      msLog("WARN : " + result.warning, "warning");
      return result;
    }

    var frameRate = comp.frameRate;
    var frameDuration = 1 / frameRate;
    var snapThreshold = frameDuration * 1.5;

    // Snap des micro-trous entre segments
    for (var k = 0; k < parsedSegments.length - 1; k++) {
      var gap = parsedSegments[k + 1].startTime - parsedSegments[k].endTime;
      if (gap > 0 && gap <= snapThreshold) parsedSegments[k].endTime = parsedSegments[k + 1].startTime;
    }
    // Snap de fin de comp
    var lastSeg = parsedSegments[parsedSegments.length - 1];
    var gapToEnd = comp.duration - lastSeg.endTime;
    if (gapToEnd > 0 && gapToEnd <= snapThreshold) lastSeg.endTime = comp.duration;

    // Tracking intelligent
    var trackingData = { dataByFrame: {}, maxFrame: 0 };
    var videoName = "";
    try { videoName = decodeURI(targetLayer.source.name); } catch (e) { videoName = targetLayer.source.name; }
    var trackingJson = (docsFolder && docsFolder.exists) ? findTrackingJsonForVideo(docsFolder, videoName) : null;
    if (trackingJson && cfg.ENABLE_CONFIDENCE_WEIGHTING) {
      trackingData = loadVideoTrackingData(trackingJson, cfg);
      result.trackingUsed = true;
      var frameCount = 0;
      for (var key in trackingData.dataByFrame) {
        if (Object.prototype.hasOwnProperty.call(trackingData.dataByFrame, key)) frameCount++;
      }
      msLog("Tracking JSON '" + decodeNameForLog(trackingJson.name) + "' : " + frameCount + " frame(s)", "info");
    } else {
      msLog("Aucun tracking JSON pour '" + videoName + "' : recentrage par segment au centre", "info");
    }

    // Cache de repli (calque non recentré)
    var layerCache = {
      scale: msProp(targetLayer, "Scale").value,
      anchorPoint: msProp(targetLayer, "Anchor Point").value,
      position: msProp(targetLayer, "Position").value
    };

    var createdCount = 0;
    var recenteredCount = 0;
    var segmentLayers = [];
    for (var j = 0; j < parsedSegments.length; j++) {
      var segment = parsedSegments[j];
      if (segment.endTime - segment.startTime < frameDuration) continue;

      var newLayer = targetLayer.duplicate();
      newLayer.name = "Segment " + segment.num + " (" + secondsToTimecode(segment.startTime) + " - " + secondsToTimecode(segment.endTime) + ")";
      newLayer.inPoint = segment.startTime;
      newLayer.outPoint = segment.endTime;
      newLayer.audioEnabled = true;

      var recentered = false;
      var pick = null;
      var segRec = (trackingData && trackingData.segmentRecommendations)
        ? (trackingData.segmentRecommendations[segment.num] || trackingData.segmentRecommendations[String(j + 1)])
        : null;

      if (segRec && segRec.recommended_object_id && typeof segRec.target_anchor_x === "number") {
        var isFaceRec = (segRec.tier === 4 || segRec.tier === 2);
        pick = {
          id: segRec.recommended_object_id,
          source: isFaceRec ? "face_landmarker" : "object_detector",
          centerX: segRec.target_anchor_x,
          spread: (typeof segRec.spread_x === "number") ? segRec.spread_x : 0,
          tier: segRec.tier,
          fromRecommendation: true
        };
      } else if (trackingData && trackingData.dataByFrame) {
        pick = pickBestObjectInRange(trackingData, frameRate, segment.startTime, segment.endTime, cfg);
      }

      if (pick) {
        var isHighSpread = (pick.spread > (cfg.SPREAD_THRESHOLD || DEFAULTS.SPREAD_THRESHOLD));
        var labelColor = isHighSpread
          ? (cfg.LABEL_HIGH_SPREAD || DEFAULTS.LABEL_HIGH_SPREAD)
          : (cfg.LABEL_STABLE || DEFAULTS.LABEL_STABLE);
        var currentAnchor = msProp(newLayer, "Anchor Point").value;
        var layerScale = msProp(newLayer, "Scale").value[0] || cfg.defaultVideoScaleX || 100;
        var anchorX = clampAnchorToCoverage(pick.centerX, comp.width, newLayer.width, layerScale);
        if (Math.abs(anchorX - pick.centerX) > 0.5) {
          msLog("Segment " + segment.num + " : ancre ramenée de " + Math.round(pick.centerX) +
            " à " + Math.round(anchorX) + " px (cadrage limité par la couverture de la composition)", "warning");
        }
        msProp(newLayer, "Anchor Point").setValue([anchorX, currentAnchor[1], 0]);
        msProp(newLayer, "Position").setValue([comp.width / 2, 0, 0]);
        newLayer.label = labelColor;
        recentered = true;
        recenteredCount++;
      }
      if (!recentered) {
        msProp(newLayer, "Scale").setValue(layerCache.scale);
        msProp(newLayer, "Anchor Point").setValue(layerCache.anchorPoint);
        msProp(newLayer, "Position").setValue(layerCache.position);
        try {
          newLayer.label = cfg.LABEL_FALLBACK_CENTER || DEFAULTS.LABEL_FALLBACK_CENTER;
        } catch (eFallbackLabel) { }
      }
      segmentLayers.push(newLayer);
      createdCount++;
    }

    if (createdCount > 0) {
      targetLayer.remove();
      msLog("Calque original supprimé après duplication", "info");
      createSegmentNull(comp, segmentLayers, cfg);
    }
    result.segments = createdCount;
    msLog("Segments créés : " + createdCount + " (" + recenteredCount + " recentrés depuis le tracking)", "success");
    return result;
  }

  // ------------------------------------------------------------------ unité de travail (1 vidéo)

  function processUnit(unit, cfg) {
    var res = { ok: false, segments: 0, error: null, projectPath: unit.projectPath };
    var undoGroupStarted = false;
    var t0 = new Date().getTime();
    try {
      closeCurrentProject();

      var comp = null;
      if (unit.reuse) {
        var existing = new File(unit.projectPath);
        if (!existing.exists) throw new Error("Projet existant introuvable : " + unit.projectPath);
        app.open(existing);
        comp = findCompByName(unit.baseName + cfg.compNameSuffix) || findCompBySuffix(cfg.compNameSuffix) || findFirstComp();
        msLog("Projet existant ouvert : " + decodeNameForLog(existing.name), "info");
      } else {
        comp = createBaseProjectInMemory(unit, cfg);
      }
      if (!comp) throw new Error("Aucune composition exploitable dans le projet");

      var layer = findMainVideoLayer(comp);
      if (!layer) throw new Error("Calque vidéo principal introuvable dans '" + comp.name + "'");

      var docsFolder = new Folder(unit.docsPath);
      if (!docsFolder.exists) throw new Error("Dossier docs introuvable : " + unit.docsPath);

      app.beginUndoGroup("Media Solution - " + comp.name);
      undoGroupStarted = true;

      importImages(docsFolder, cfg);
      prepareLayer(comp, layer, cfg);
      if (cfg.enableAutoRecentering) applyDefaultCentering(comp, layer);
      if (cfg.enableCuts) {
        var cutRes = applyCutsToLayer(comp, layer, docsFolder, cfg, null);
        res.segments = cutRes.segments;
        if (cutRes.warning) res.warning = cutRes.warning;
      }

      app.endUndoGroup();
      undoGroupStarted = false;

      app.project.save(new File(res.projectPath));
      res.ok = true;
      msLog("Projet enregistré : " + decodeNameForLog(res.projectPath.split("/").pop()) +
        " (" + ((new Date().getTime() - t0) / 1000).toFixed(1) + " s)", "success");
    } catch (e) {
      res.error = e.toString();
      msLog("Erreur sur " + unit.videoName + " : " + res.error, "error");
    } finally {
      if (undoGroupStarted) {
        try { app.endUndoGroup(); } catch (e2) { }
      }
      try { closeCurrentProject(); } catch (e3) { }
    }
    return res;
  }

  // ------------------------------------------------------------------ orchestration batch

  function buildPlan(rootPath, folderPaths, cfg, reuse) {
    var plan = [];
    for (var i = 0; i < folderPaths.length; i++) {
      var folderPath = folderPaths[i];
      var folder = new Folder(folderPath);
      if (!folder.exists) {
        msLog("Dossier ignoré (introuvable) : " + folderPath, "warning");
        continue;
      }
      var docsPath = joinPath(folder.fsName, cfg.docsSubFolderName);
      var docsFolder = new Folder(docsPath);
      if (!docsFolder.exists) {
        msLog("Dossier ignoré (pas de '" + cfg.docsSubFolderName + "') : " + folderPath, "warning");
        continue;
      }
      var projectsPath = joinPath(folder.fsName, cfg.projectsSubFolderName);
      if (!reuse) ensureFolder(projectsPath);

      var videos = getProcessableVideoFiles(docsFolder, cfg);
      if (videos.length === 0) {
        msLog("Aucune vidéo processable dans " + docsPath, "warning");
        continue;
      }
      for (var v = 0; v < videos.length; v++) {
        var videoName = decodeNameForLog(videos[v].name);
        var baseName = videos[v].name;
        try { baseName = decodeURI(baseName); } catch (eD) { }
        var dot = baseName.lastIndexOf(".");
        baseName = (dot > 0) ? baseName.substring(0, dot) : baseName;
        plan.push({
          folderPath: folder.fsName,
          docsPath: docsPath,
          videoPath: videos[v].fsName,
          videoName: videoName,
          baseName: baseName,
          projectPath: joinPath(projectsPath, baseName + cfg.compNameSuffix + cfg.projectFileSuffix),
          reuse: !!reuse,
          compName: null
        });
      }
    }
    return plan;
  }

  function updateProgressUI() {
    var st = MS.state;
    if (MS.refs.progress) {
      try {
        MS.refs.progress.maxvalue = Math.max(st.plan.length, 1);
        MS.refs.progress.value = st.unitIndex;
      } catch (e) { }
    }
  }

  // Log fichier : suit le dossier projet traité. Le fichier est créé au démarrage du premier
  // projet (la racine du batch n'est pas le bon emplacement) ; lors d'un batch multi-dossiers
  // (« Tout exécuter »), il bascule sur le dossier du projet courant.
  function ensureLogForUnit(unit) {
    if (!MS.config.enableLogFile) return;
    var parent = "";
    var name = "";
    try {
      var docsFolder = new Folder(unit.docsPath);
      parent = docsFolder.parent ? docsFolder.parent.fsName : "";
      name = docsFolder.parent ? docsFolder.parent.name : "";
    } catch (e) { }
    var dir = parent || MS.state.rootPath || "";
    if (!dir || dir === MS.log.dir) return;
    initLogFile(dir, name);
  }

  function beginBatch(rootPath, folderPaths, cfg) {
    if (MS.state.batchRunning) return { ok: false, error: "Batch déjà en cours" };
    closeLogFile();
    var plan = buildPlan(rootPath, folderPaths, cfg, cfg.reuseExistingProjects);
    if (plan.length === 0) {
      return { ok: false, error: "Aucun projet à traiter (vérifiez docs/ et les exclusions)" };
    }
    MS.state.plan = plan;
    MS.state.unitIndex = 0;
    MS.state.processed = 0;
    MS.state.errors = [];
    MS.state.cancelRequested = false;
    MS.state.batchRunning = true;
    MS.state.rootPath = rootPath || "";
    updateProgressUI();
    msLog("Batch démarré : " + plan.length + " projet(s) sur " + folderPaths.length + " dossier(s)", "info");
    return { ok: true, total: plan.length };
  }

  function finishBatch() {
    var st = MS.state;
    try { app.purge(); } catch (e) { }
    flushLogBuffer();
    var summary = st.processed + "/" + st.plan.length + " projet(s) traité(s)";
    if (st.errors.length > 0) summary += " — " + st.errors.length + " erreur(s)";
    msLog("Batch terminé : " + summary, st.errors.length > 0 ? "warning" : "success");
    for (var i = 0; i < st.errors.length && i < MS.config.maxErrorDetailsInSummary; i++) {
      msLog("  erreur : " + st.errors[i], "error");
    }
    flushLogBuffer();
    st.batchRunning = false;
    st.cancelRequested = false;
    setStatus("Terminé : " + summary);
    updateProgressUI();
  }

  // Traite exactement une unité. Renvoie true s'il reste du travail.
  function stepOnce() {
    var st = MS.state;
    if (!st.batchRunning) return false;
    if (st.unitIndex >= st.plan.length) { finishBatch(); return false; }

    var unit = st.plan[st.unitIndex];
    ensureLogForUnit(unit);
    setStatus("Traitement " + (st.unitIndex + 1) + "/" + st.plan.length + " : " + unit.videoName);
    msLog("--- " + (st.unitIndex + 1) + "/" + st.plan.length + " : " + unit.videoName, "info");

    var res = processUnit(unit, MS.config);
    if (res.ok) {
      st.processed++;
      if (res.warning) msLog("  rappel : " + res.warning, "warning");
    } else {
      st.errors.push(unit.videoName + " : " + res.error);
    }
    st.unitIndex++;
    updateProgressUI();

    if (st.unitIndex >= st.plan.length) { finishBatch(); return false; }
    setStatus("Prêt pour la suite (" + st.unitIndex + "/" + st.plan.length + ") — prochain : " + st.plan[st.unitIndex].videoName);
    return true;
  }

  function runAllUnits() {
    var guard = 0;
    while (MS.state.batchRunning && !MS.state.cancelRequested && MS.state.unitIndex < MS.state.plan.length) {
      stepOnce();
      guard++;
      if (guard > 100000) break;
    }
    if (MS.state.cancelRequested) cancelBatch();
  }

  function cancelBatch() {
    var st = MS.state;
    st.cancelRequested = true;
    if (st.batchRunning) {
      msLog("Batch annulé (" + st.unitIndex + "/" + st.plan.length + " traité(s))", "warning");
      setStatus("Annulé (" + st.unitIndex + "/" + st.plan.length + ")");
      flushLogBuffer();
      st.batchRunning = false;
    }
    updateProgressUI();
    return { ok: true };
  }

  // ------------------------------------------------------------------ diagnostics / détection

  function runDiagnostics(folderPath, cfg) {
    try {
      var folder = new Folder(folderPath);
      if (!folder.exists) return { ok: false, errors: ["Dossier projet invalide : " + folderPath] };
      var report = { ok: true, warnings: [], errors: [], videos: 0, csv: 0, tracking: 0 };
      var docsFolder = new Folder(joinPath(folder.fsName, cfg.docsSubFolderName));
      if (!docsFolder.exists) {
        report.ok = false;
        report.errors.push("Dossier '" + cfg.docsSubFolderName + "' introuvable");
      }
      var projectsFolder = new Folder(joinPath(folder.fsName, cfg.projectsSubFolderName));
      if (!projectsFolder.exists) report.warnings.push("Dossier '" + cfg.projectsSubFolderName + "' absent (créé au traitement)");
      if (docsFolder.exists) {
        var videos = getProcessableVideoFiles(docsFolder, cfg);
        report.videos = videos.length;
        if (videos.length === 0) report.warnings.push("Aucune vidéo processable dans 'docs'");
        report.csv = getFilesByExtensions(docsFolder, ["csv"]).length;
        report.tracking = getFilesByExtensions(docsFolder, ["json"]).length;
        for (var i = 0; i < videos.length; i++) {
          var vName = decodeNameForLog(videos[i].name);
          var base = vName;
          var dot = base.lastIndexOf(".");
          if (dot > 0) base = base.substring(0, dot);
          var csv = findMatchingCsv(docsFolder, base + cfg.compNameSuffix, cfg);
          if (!csv.file) report.warnings.push("Pas de CSV pour " + vName);
          if (!findTrackingJsonForVideo(docsFolder, vName)) report.warnings.push("Pas de tracking JSON pour " + vName);
        }
      }
      return report;
    } catch (e) {
      return { ok: false, errors: [e.toString()] };
    }
  }

  function autoDetectFolders(rootPath, nameFilter, cfg) {
    var detected = [];
    try {
      var rootFolder = new Folder(rootPath);
      if (!rootFolder.exists) return { ok: false, error: "Racine introuvable : " + rootPath, folders: [] };
      var subs = rootFolder.getFiles();
      var filterLower = String(nameFilter || "").toLowerCase();
      for (var i = 0; i < subs.length; i++) {
        var sub = subs[i];
        if (!(sub instanceof Folder)) continue;
        if (filterLower && sub.name.toLowerCase().indexOf(filterLower) === -1) continue;
        var docsFolder = new Folder(joinPath(sub.fsName, cfg.docsSubFolderName));
        if (!docsFolder.exists) continue;
        var videos = getProcessableVideoFiles(docsFolder, cfg);
        detected.push({ path: sub.fsName, name: sub.name, videos: videos.length });
      }
      return { ok: true, folders: detected };
    } catch (e) {
      return { ok: false, error: e.toString(), folders: [] };
    }
  }

  // ------------------------------------------------------------------ config fichier

  function configPath(rootPath) {
    if (MS.refs && MS.refs.configPath && MS.refs.configPath.text) return MS.refs.configPath.text;
    if (!rootPath) return "";
    return joinPath(rootPath, DEFAULTS.configFileName);
  }

  function saveConfigFile(rootPath) {
    var path = configPath(rootPath);
    if (!path) return { ok: false, error: "chemin de config inconnu" };
    var ok = writeText(new File(path), configAsJson());
    if (ok) msLog("Configuration enregistrée : " + path, "info");
    else msLog("Configuration non enregistrée (" + path + ") — préférence scripting requise ?", "warning");
    return { ok: ok };
  }

  function loadConfigFile(path) {
    var file = new File(path);
    if (!file.exists) return { ok: false, error: "Fichier de configuration introuvable : " + path };
    var parsed = safeJsonParse(readText(file));
    if (!parsed) return { ok: false, error: "Configuration illisible : " + path };
    mergeConfig(parsed);
    msLog("Configuration chargée : " + path, "info");
    return { ok: true };
  }

  // ------------------------------------------------------------------ UI

  function buildPanelUI(ui) {
    ui.orientation = "column";
    ui.alignChildren = ["fill", "top"];
    ui.spacing = 6;
    ui.margins = 8;

    // ----- Dossiers
    var foldersPanel = ui.add("panel", undefined, "Dossiers");
    foldersPanel.orientation = "column";
    foldersPanel.alignChildren = ["fill", "top"];
    foldersPanel.spacing = 4;
    foldersPanel.margins = 8;

    var rootRow = foldersPanel.add("group");
    rootRow.alignChildren = ["left", "center"];
    rootRow.add("statictext", undefined, "Racine :");
    MS.refs.rootPath = rootRow.add("edittext", undefined, MS.config.autoDetectRootPath || "", { name: "rootPath" });
    MS.refs.rootPath.characters = 34;
    MS.refs.rootPath.preferredSize.width = 260;
    MS.refs.detectBtn = rootRow.add("button", undefined, "Détection auto");
    MS.refs.detectBtn.preferredSize.width = 110;

    var filterRow = foldersPanel.add("group");
    filterRow.alignChildren = ["left", "center"];
    filterRow.add("statictext", undefined, "Filtre :");
    MS.refs.filter = filterRow.add("edittext", undefined, MS.config.autoDetectNameFilter || "", { name: "filter" });
    MS.refs.filter.characters = 12;
    MS.refs.filter.preferredSize.width = 110;
    filterRow.add("statictext", undefined, "(dossier contenant docs/)");

    var addRow = foldersPanel.add("group");
    addRow.alignChildren = ["left", "center"];
    addRow.add("statictext", undefined, "Ajouter :");
    MS.refs.addPath = addRow.add("edittext", undefined, "", { name: "addPath" });
    MS.refs.addPath.characters = 34;
    MS.refs.addPath.preferredSize.width = 260;
    MS.refs.addBtn = addRow.add("button", undefined, "Ajouter");
    MS.refs.addBtn.preferredSize.width = 110;

    MS.refs.foldersGroup = foldersPanel.add("group");
    MS.refs.foldersGroup.orientation = "column";
    MS.refs.foldersGroup.alignChildren = ["left", "top"];
    MS.refs.foldersGroup.spacing = 2;

    var folderBtnRow = foldersPanel.add("group");
    MS.refs.checkAllBtn = folderBtnRow.add("button", undefined, "Tout cocher");
    MS.refs.uncheckAllBtn = folderBtnRow.add("button", undefined, "Tout décocher");
    MS.refs.clearFoldersBtn = folderBtnRow.add("button", undefined, "Vider");

    // ----- Options
    var optionsPanel = ui.add("panel", undefined, "Options");
    optionsPanel.orientation = "column";
    optionsPanel.alignChildren = ["fill", "top"];
    optionsPanel.spacing = 4;
    optionsPanel.margins = 8;

    var optRow1 = optionsPanel.add("group");
    MS.refs.optCuts = optRow1.add("checkbox", undefined, "Cuts CSV");
    MS.refs.optCuts.value = MS.config.enableCuts !== false;
    MS.refs.optRecenter = optRow1.add("checkbox", undefined, "Auto-recentrage");
    MS.refs.optRecenter.value = MS.config.enableAutoRecentering !== false;
    MS.refs.optLogFile = optRow1.add("checkbox", undefined, "Log fichier");
    MS.refs.optLogFile.value = MS.config.enableLogFile === true;

    var optRow2 = optionsPanel.add("group");
    optRow2.alignChildren = ["left", "center"];
    optRow2.add("statictext", undefined, "Échelle :");
    MS.refs.scale = optRow2.add("edittext", undefined, String(MS.config.defaultVideoScaleX));
    MS.refs.scale.characters = 4;
    optRow2.add("statictext", undefined, "%   Cible :");
    MS.refs.targetW = optRow2.add("edittext", undefined, String(MS.config.targetCompWidth));
    MS.refs.targetW.characters = 5;
    optRow2.add("statictext", undefined, "×");
    MS.refs.targetH = optRow2.add("edittext", undefined, String(MS.config.targetCompHeight));
    MS.refs.targetH.characters = 5;
    MS.refs.reuseChk = optRow2.add("checkbox", undefined, "Repartir des projets existants");
    MS.refs.reuseChk.value = MS.config.reuseExistingProjects === true;

    var cfgRow = optionsPanel.add("group");
    cfgRow.alignChildren = ["left", "center"];
    cfgRow.add("statictext", undefined, "Config :");
    MS.refs.configPath = cfgRow.add("edittext", undefined, "", { name: "configPath" });
    MS.refs.configPath.characters = 30;
    MS.refs.configPath.preferredSize.width = 230;
    MS.refs.loadCfgBtn = cfgRow.add("button", undefined, "Charger");
    MS.refs.saveCfgBtn = cfgRow.add("button", undefined, "Enregistrer");

    var prefRow = optionsPanel.add("group");
    prefRow.alignChildren = ["left", "center"];
    MS.refs.probeBtn = prefRow.add("button", undefined, "Vérifier la préférence scripting");
    MS.refs.probeBtn.preferredSize.width = 210;
    MS.refs.enablePrefBtn = prefRow.add("button", undefined, "Activer");
    MS.refs.enablePrefBtn.preferredSize.width = 70;

    // ----- Cuts manuels
    var manualPanel = ui.add("panel", undefined, "Cuts manuels (comp active)");
    manualPanel.orientation = "column";
    manualPanel.alignChildren = ["fill", "top"];
    manualPanel.spacing = 4;
    manualPanel.margins = 8;
    var csvRow = manualPanel.add("group");
    csvRow.alignChildren = ["left", "center"];
    csvRow.add("statictext", undefined, "CSV :");
    MS.refs.csvPath = csvRow.add("edittext", undefined, "", { name: "csvPath" });
    MS.refs.csvPath.characters = 34;
    MS.refs.csvPath.preferredSize.width = 260;
    MS.refs.applyCutsBtn = csvRow.add("button", undefined, "Appliquer");
    MS.refs.applyCutsBtn.preferredSize.width = 100;
    MS.refs.refreshTargetBtn = manualPanel.add("button", undefined, "Afficher la cible active (comp / calque)");
    MS.refs.refreshTargetBtn.preferredSize.width = 300;

    // ----- Traitement
    var batchPanel = ui.add("panel", undefined, "Traitement");
    batchPanel.orientation = "column";
    batchPanel.alignChildren = ["fill", "top"];
    batchPanel.spacing = 4;
    batchPanel.margins = 8;

    var modeRow = batchPanel.add("group");
    modeRow.alignChildren = ["left", "center"];
    modeRow.add("statictext", undefined, "Mode :");
    MS.refs.mode = modeRow.add("dropdownlist", undefined, ["Pas-à-pas (1 projet par clic)", "Tout exécuter (interface figée)"], { name: "mode" });
    MS.refs.mode.selection = 0;
    MS.refs.mode.preferredSize.width = 240;

    var btnRow = batchPanel.add("group");
    MS.refs.startBtn = btnRow.add("button", undefined, "Démarrer");
    MS.refs.startBtn.preferredSize.width = 90;
    MS.refs.continueBtn = btnRow.add("button", undefined, "▶ Continuer");
    MS.refs.continueBtn.preferredSize.width = 110;
    MS.refs.cancelBtn = btnRow.add("button", undefined, "Annuler");
    MS.refs.cancelBtn.preferredSize.width = 80;
    MS.refs.diagBtn = btnRow.add("button", undefined, "Diagnostics");
    MS.refs.diagBtn.preferredSize.width = 100;

    MS.refs.progress = batchPanel.add("progressbar", undefined, 0, 100);
    MS.refs.progress.preferredSize.height = 16;
    MS.refs.status = batchPanel.add("statictext", undefined, "Prêt");
    MS.refs.status.characters = 60;

    // ----- Logs
    var logPanel = ui.add("panel", undefined, "Logs");
    logPanel.orientation = "column";
    logPanel.alignChildren = ["fill", "top"];
    logPanel.spacing = 4;
    logPanel.margins = 8;
    MS.refs.log = logPanel.add("edittext", undefined, "", { multiline: true, readonly: true });
    MS.refs.log.characters = 60;
    MS.refs.log.preferredSize.height = 200;

    var logBtnRow = logPanel.add("group");
    logBtnRow.alignChildren = ["left", "center"];
    MS.refs.clearLogBtn = logBtnRow.add("button", undefined, "Effacer");
    logBtnRow.add("statictext", undefined, "Exporter vers :");
    MS.refs.exportPath = logBtnRow.add("edittext", undefined, "", { name: "exportPath" });
    MS.refs.exportPath.characters = 24;
    MS.refs.exportPath.preferredSize.width = 200;
    MS.refs.exportLogBtn = logBtnRow.add("button", undefined, "Exporter");

    var footer = ui.add("statictext", undefined, "Media Solution " + SCRIPT_VERSION + " (EffectCraft) — build " + SCRIPT_BUILD_ID);
    footer.characters = 60;

    // ----- Handlers
    MS.refs.detectBtn.onClick = onDetectClick;
    MS.refs.addBtn.onClick = onAddClick;
    MS.refs.checkAllBtn.onClick = function () { setAllFolders(true); };
    MS.refs.uncheckAllBtn.onClick = function () { setAllFolders(false); };
    MS.refs.clearFoldersBtn.onClick = function () { MS.state.folderChecks = []; rebuildFoldersUI(); setStatus("Liste vidée"); };
    MS.refs.loadCfgBtn.onClick = onLoadConfigClick;
    MS.refs.saveCfgBtn.onClick = onSaveConfigClick;
    MS.refs.probeBtn.onClick = onProbeClick;
    MS.refs.enablePrefBtn.onClick = onEnablePrefClick;
    MS.refs.applyCutsBtn.onClick = onManualCutsClick;
    MS.refs.refreshTargetBtn.onClick = onRefreshTargetClick;
    MS.refs.startBtn.onClick = onStartClick;
    MS.refs.continueBtn.onClick = onContinueClick;
    MS.refs.cancelBtn.onClick = onCancelClick;
    MS.refs.diagBtn.onClick = onDiagnosticsClick;
    MS.refs.clearLogBtn.onClick = function () { MS.log.lines = []; updateLogUI(); };
    MS.refs.exportLogBtn.onClick = onExportLogClick;

    rebuildFoldersUI();
    updateLogUI();
    syncConfigPathField();
  }

  function syncConfigPathField() {
    if (!MS.refs.configPath || MS.refs.configPath.text) return;
    var root = MS.refs.rootPath ? MS.refs.rootPath.text : "";
    if (root) {
      try { MS.refs.configPath.text = joinPath(root, DEFAULTS.configFileName); } catch (e) { }
    }
  }

  function rebuildFoldersUI() {
    var g = MS.refs.foldersGroup;
    if (!g) return;
    try { g.removeAll(); } catch (e) { }
    var list = MS.state.folderChecks;
    if (list.length === 0) {
      g.add("statictext", undefined, "(aucun dossier — « Détection auto » ou « Ajouter »)");
      return;
    }
    for (var i = 0; i < list.length; i++) {
      var cb = g.add("checkbox", undefined, list[i].name + "  (" + list[i].videos + " vidéo(s))");
      cb.value = list[i].checked;
      cb.helpTip = list[i].path;
      bindFolderCheckbox(cb, i);
    }
  }

  function bindFolderCheckbox(box, index) {
    box.onClick = function () {
      if (MS.state.folderChecks[index]) MS.state.folderChecks[index].checked = box.value;
    };
  }

  function setAllFolders(value) {
    for (var i = 0; i < MS.state.folderChecks.length; i++) MS.state.folderChecks[i].checked = value;
    rebuildFoldersUI();
    setStatus(value ? "Tous les dossiers cochés" : "Tous les dossiers décochés");
  }

  function addFolderToUI(path, cfg) {
    var folder = new Folder(path);
    if (!folder.exists) {
      msLog("Dossier introuvable : " + path, "warning");
      return false;
    }
    for (var i = 0; i < MS.state.folderChecks.length; i++) {
      if (MS.state.folderChecks[i].path === folder.fsName) {
        msLog("Dossier déjà présent : " + folder.fsName, "warning");
        return false;
      }
    }
    var docsFolder = new Folder(joinPath(folder.fsName, cfg.docsSubFolderName));
    if (!docsFolder.exists) {
      msLog("Dossier ignoré (pas de '" + cfg.docsSubFolderName + "') : " + folder.fsName, "warning");
      return false;
    }
    var videos = getProcessableVideoFiles(docsFolder, cfg);
    MS.state.folderChecks.push({ path: folder.fsName, name: folder.name, checked: true, videos: videos.length });
    return true;
  }

  function checkedFolderPaths() {
    var paths = [];
    for (var i = 0; i < MS.state.folderChecks.length; i++) {
      if (MS.state.folderChecks[i].checked) paths.push(MS.state.folderChecks[i].path);
    }
    return paths;
  }

  function gatherConfigFromUI() {
    var cfg = MS.config;
    try {
      if (MS.refs.filter && MS.refs.filter.text) cfg.autoDetectNameFilter = MS.refs.filter.text;
      if (MS.refs.rootPath && MS.refs.rootPath.text) cfg.autoDetectRootPath = MS.refs.rootPath.text;
      if (MS.refs.optCuts) cfg.enableCuts = MS.refs.optCuts.value === true;
      if (MS.refs.optRecenter) cfg.enableAutoRecentering = MS.refs.optRecenter.value === true;
      if (MS.refs.optLogFile) cfg.enableLogFile = MS.refs.optLogFile.value === true;
      if (MS.refs.reuseChk) cfg.reuseExistingProjects = MS.refs.reuseChk.value === true;
      if (MS.refs.scale) {
        var sc = parseFloat(MS.refs.scale.text);
        if (!isNaN(sc) && sc > 0) { cfg.defaultVideoScaleX = sc; cfg.defaultVideoScaleY = sc; }
      }
      if (MS.refs.targetW) {
        var tw = parseInt(MS.refs.targetW.text, 10);
        if (!isNaN(tw) && tw >= 4) cfg.targetCompWidth = tw;
      }
      if (MS.refs.targetH) {
        var th = parseInt(MS.refs.targetH.text, 10);
        if (!isNaN(th) && th >= 4) cfg.targetCompHeight = th;
      }
    } catch (e) { }
    return cfg;
  }

  // ------------------------------------------------------------------ handlers UI

  function onDetectClick() {
    try {
      var cfg = gatherConfigFromUI();
      var root = MS.refs.rootPath.text;
      if (!root) { setStatus("Renseignez une racine de détection"); return; }
      syncConfigPathField();
      var res = autoDetectFolders(root, cfg.autoDetectNameFilter, cfg);
      if (!res.ok) { msLog("Détection impossible : " + res.error, "error"); setStatus("Détection impossible"); return; }
      MS.state.folderChecks = [];
      for (var i = 0; i < res.folders.length; i++) {
        MS.state.folderChecks.push({ path: res.folders[i].path, name: res.folders[i].name, checked: true, videos: res.folders[i].videos });
      }
      rebuildFoldersUI();
      msLog(res.folders.length + " dossier(s) détecté(s) dans " + root, res.folders.length ? "success" : "warning");
      setStatus(res.folders.length + " dossier(s) détecté(s)");
      tryLoadConfigForRoot(root);
    } catch (e) {
      msLog("Erreur détection : " + e.toString(), "error");
    }
  }

  function onAddClick() {
    try {
      var cfg = gatherConfigFromUI();
      var path = MS.refs.addPath.text;
      if (!path) { setStatus("Renseignez un dossier à ajouter"); return; }
      if (addFolderToUI(path, cfg)) {
        rebuildFoldersUI();
        msLog("Dossier ajouté : " + path, "success");
        setStatus("Dossier ajouté");
      }
    } catch (e) {
      msLog("Erreur ajout dossier : " + e.toString(), "error");
    }
  }

  function tryLoadConfigForRoot(root) {
    try {
      var path = joinPath(root, DEFAULTS.configFileName);
      var f = new File(path);
      if (f.exists) {
        var res = loadConfigFile(path);
        if (res.ok) {
          if (MS.refs.configPath) MS.refs.configPath.text = path;
          applyConfigToUI();
        }
      }
    } catch (e) { }
  }

  function applyConfigToUI() {
    try {
      if (MS.refs.rootPath && MS.config.autoDetectRootPath) MS.refs.rootPath.text = MS.config.autoDetectRootPath;
      if (MS.refs.filter) MS.refs.filter.text = MS.config.autoDetectNameFilter || "";
      if (MS.refs.optCuts) MS.refs.optCuts.value = MS.config.enableCuts !== false;
      if (MS.refs.optRecenter) MS.refs.optRecenter.value = MS.config.enableAutoRecentering !== false;
      if (MS.refs.optLogFile) MS.refs.optLogFile.value = MS.config.enableLogFile === true;
      if (MS.refs.reuseChk) MS.refs.reuseChk.value = MS.config.reuseExistingProjects === true;
      if (MS.refs.scale) MS.refs.scale.text = String(MS.config.defaultVideoScaleX);
      if (MS.refs.targetW) MS.refs.targetW.text = String(MS.config.targetCompWidth);
      if (MS.refs.targetH) MS.refs.targetH.text = String(MS.config.targetCompHeight);
    } catch (e) { }
  }

  function onLoadConfigClick() {
    try {
      var path = MS.refs.configPath.text || configPath(MS.refs.rootPath.text);
      if (!path) { setStatus("Chemin de config vide"); return; }
      var res = loadConfigFile(path);
      if (res.ok) { applyConfigToUI(); setStatus("Configuration chargée"); }
      else { setStatus("Chargement impossible"); msLog(res.error, "error"); }
    } catch (e) {
      msLog("Erreur chargement config : " + e.toString(), "error");
    }
  }

  function onSaveConfigClick() {
    try {
      gatherConfigFromUI();
      var path = MS.refs.configPath.text || configPath(MS.refs.rootPath.text);
      if (path && MS.refs.configPath) MS.refs.configPath.text = path;
      var res = saveConfigFile(MS.refs.rootPath.text);
      setStatus(res.ok ? "Configuration enregistrée" : "Enregistrement impossible (préférence scripting ?)");
    } catch (e) {
      msLog("Erreur sauvegarde config : " + e.toString(), "error");
    }
  }

  function onProbeClick() {
    try {
      var root = MS.refs.rootPath.text;
      var access = checkScriptingAccess(root);
      if (access.ok) {
        msLog("Accès disque scripting : OK", "success");
        setStatus("Préférence scripting : OK");
      } else {
        msLog("Accès disque refusé : " + access.error, "error");
        msLog("Activez Préférences ▸ Scripting & Expressions ▸ « Allow Scripts to Write Files and Access Network » (bouton « Activer »).", "warning");
        setStatus("Préférence scripting requise");
      }
    } catch (e) {
      msLog("Erreur vérification : " + e.toString(), "error");
    }
  }

  function onEnablePrefClick() {
    try {
      app.run("prefs.set", { key: "scripting.allowScriptsWriteFiles", value: true });
      msLog("Préférence « Allow Scripts to Write Files and Access Network » activée (préférences EffectCraft).", "success");
      onProbeClick();
    } catch (e) {
      msLog("Impossible d'activer la préférence : " + e.toString(), "error");
    }
  }

  function onManualCutsClick() {
    try {
      if (MS.state.batchRunning) { setStatus("Batch en cours"); return; }
      var cfg = gatherConfigFromUI();
      var comp = app.project.activeItem;
      if (!comp || !(comp instanceof CompItem)) { msLog("Aucune composition active", "error"); setStatus("Aucune comp active"); return; }
      var layer = comp.selectedLayers.length > 0 ? comp.selectedLayers[0] : comp.layer(1);
      if (!layer) { msLog("Aucun calque cible", "error"); return; }
      var csvFile = new File(MS.refs.csvPath.text);
      if (!MS.refs.csvPath.text || !csvFile.exists) { msLog("CSV introuvable : " + MS.refs.csvPath.text, "error"); setStatus("CSV introuvable"); return; }
      var docsFolder = csvFile.parent;

      msLog("Cuts manuels sur '" + comp.name + "' / '" + layer.name + "' depuis " + decodeNameForLog(csvFile.name), "info");
      app.beginUndoGroup("Media Solution - Cuts manuels");
      var res = applyCutsToLayer(comp, layer, docsFolder, cfg, csvFile);
      app.endUndoGroup();
      if (res.warning) msLog(res.warning, "warning");
      setStatus("Cuts manuels : " + res.segments + " segment(s)");
    } catch (e) {
      try { app.endUndoGroup(); } catch (e2) { }
      msLog("Erreur cuts manuels : " + e.toString(), "error");
    }
  }

  function onRefreshTargetClick() {
    try {
      var comp = app.project.activeItem;
      if (!comp || !(comp instanceof CompItem)) { msLog("Aucune composition active (ouvrez/activez une comp).", "warning"); return; }
      var layer = comp.selectedLayers.length > 0 ? comp.selectedLayers[0] : comp.layer(1);
      var info = "Comp active : '" + comp.name + "' " + comp.width + "x" + comp.height + " @" + comp.frameRate + " — " + comp.numLayers + " calque(s)";
      if (layer) info += " | Calque : '" + layer.name + "' (index " + layer.index + ")";
      msLog(info, "info");
      setStatus("Cible : " + comp.name + (layer ? " / " + layer.name : ""));
    } catch (e) {
      msLog("Erreur cible active : " + e.toString(), "error");
    }
  }

  function onStartClick() {
    try {
      if (MS.state.batchRunning) { setStatus("Batch déjà en cours — utilisez « Continuer »"); return; }
      var cfg = gatherConfigFromUI();
      var folders = checkedFolderPaths();
      if (folders.length === 0) { msLog("Aucun dossier coché", "warning"); setStatus("Aucun dossier coché"); return; }
      var root = (MS.refs.rootPath.text || folders[0]);
      syncConfigPathField();
      saveConfigFile(root);

      var access = checkScriptingAccess(root);
      if (!access.ok) {
        msLog("Accès disque refusé (" + access.error + "). Le batch a besoin de la préférence scripting.", "error");
        setStatus("Préférence scripting requise (bouton « Activer »)");
        return;
      }

      var begin = beginBatch(root, folders, cfg);
      if (!begin.ok) { msLog(begin.error, "error"); setStatus(begin.error); return; }

      var allMode = MS.refs.mode && MS.refs.mode.selection && MS.refs.mode.selection.index === 1;
      if (allMode) {
        msLog("Mode « Tout exécuter » : l'interface restera figée jusqu'à la fin.", "warning");
        runAllUnits();
      } else {
        stepOnce();
      }
    } catch (e) {
      msLog("Erreur démarrage batch : " + e.toString(), "error");
      MS.state.batchRunning = false;
    }
  }

  function onContinueClick() {
    try {
      if (!MS.state.batchRunning) { setStatus("Aucun batch en cours — cliquez « Démarrer »"); return; }
      if (MS.state.cancelRequested) { setStatus("Batch annulé"); return; }
      var allMode = MS.refs.mode && MS.refs.mode.selection && MS.refs.mode.selection.index === 1;
      if (allMode) runAllUnits();
      else stepOnce();
    } catch (e) {
      msLog("Erreur poursuite batch : " + e.toString(), "error");
    }
  }

  function onCancelClick() {
    try { cancelBatch(); } catch (e) { msLog("Erreur annulation : " + e.toString(), "error"); }
  }

  function onDiagnosticsClick() {
    try {
      var cfg = gatherConfigFromUI();
      var paths = checkedFolderPaths();
      if (paths.length === 0 && MS.refs.addPath.text) paths = [MS.refs.addPath.text];
      if (paths.length === 0 && MS.refs.rootPath.text) paths = [MS.refs.rootPath.text];
      if (paths.length === 0) { setStatus("Aucun dossier pour les diagnostics"); return; }
      for (var i = 0; i < paths.length; i++) {
        var rep = runDiagnostics(paths[i], cfg);
        msLog("Diagnostics " + paths[i] + " : " + (rep.ok ? "OK" : "ERREUR") +
          " — " + rep.videos + " vidéo(s), " + rep.csv + " CSV, " + rep.tracking + " JSON", rep.ok ? "success" : "error");
        for (var e = 0; e < rep.errors.length; e++) msLog("  erreur : " + rep.errors[e], "error");
        for (var w = 0; w < rep.warnings.length; w++) msLog("  avertissement : " + rep.warnings[w], "warning");
      }
      setStatus("Diagnostics terminés");
    } catch (e) {
      msLog("Erreur diagnostics : " + e.toString(), "error");
    }
  }

  function onExportLogClick() {
    try {
      var path = MS.refs.exportPath.text;
      if (!path) { setStatus("Chemin d'export vide"); return; }
      var ok = writeText(new File(path), MS.log.lines.join("\n") + "\n");
      msLog(ok ? "Logs exportés : " + path : "Export impossible : " + path, ok ? "success" : "error");
      setStatus(ok ? "Logs exportés" : "Export impossible");
    } catch (e) {
      msLog("Erreur export logs : " + e.toString(), "error");
    }
  }

  // ------------------------------------------------------------------ entrées headless

  function engineApplyConfig(configJson) {
    var parsed = safeJsonParse(configJson);
    if (!parsed) return { ok: false, error: "config JSON illisible" };
    mergeConfig(parsed);
    return { ok: true };
  }

  function engineRunBatch(foldersJson, configJson, maxUnits) {
    try {
      if (configJson) engineApplyConfig(configJson);
      var folders = foldersJson;
      if (typeof folders === "string") folders = safeJsonParse(folders);
      if (!folders || !folders.length) return JSON.stringify({ ok: false, error: "aucun dossier" });
      var root = MS.config.autoDetectRootPath || folders[0];
      var begin = beginBatch(root, folders, MS.config);
      if (!begin.ok) return JSON.stringify({ ok: false, error: begin.error });
      var limit = (typeof maxUnits === "number" && maxUnits > 0) ? maxUnits : MS.state.plan.length;
      var steps = 0;
      while (MS.state.batchRunning && !MS.state.cancelRequested && MS.state.unitIndex < MS.state.plan.length && steps < limit) {
        stepOnce();
        steps++;
      }
      if (MS.state.batchRunning && MS.state.unitIndex >= MS.state.plan.length) finishBatch();
      return JSON.stringify({
        ok: true,
        total: MS.state.plan.length,
        processed: MS.state.processed,
        unitIndex: MS.state.unitIndex,
        running: MS.state.batchRunning,
        errors: MS.state.errors
      });
    } catch (e) {
      return JSON.stringify({ ok: false, error: e.toString() });
    }
  }

  function engineStatus() {
    return JSON.stringify({
      version: SCRIPT_VERSION,
      running: MS.state.batchRunning,
      unitIndex: MS.state.unitIndex,
      total: MS.state.plan.length,
      processed: MS.state.processed,
      errors: MS.state.errors,
      config: safeJsonParse(configAsJson())
    });
  }

  // ------------------------------------------------------------------ init

  function initConfig() {
    MS.config = {};
    for (var k in DEFAULTS) {
      if (DEFAULTS.hasOwnProperty(k)) MS.config[k] = DEFAULTS[k];
    }
  }

  initConfig();

  // Entrées globales (pilotage headless : effectcraft-cli / MCP run_script)
  $.global.mediasolutionEC = {
    version: SCRIPT_VERSION,
    build: SCRIPT_BUILD_ID,
    defaults: function () { return configAsJson(); },
    applyConfig: engineApplyConfig,
    detect: function (rootPath, filter) {
      var res = autoDetectFolders(rootPath, filter || MS.config.autoDetectNameFilter, MS.config);
      return JSON.stringify(res);
    },
    diagnostics: function (folderPath) {
      return JSON.stringify(runDiagnostics(folderPath, MS.config));
    },
    runBatch: engineRunBatch,
    status: engineStatus,
    cancel: function () { return JSON.stringify(cancelBatch()); }
  };

  // UI : uniquement en panel dockable (Fenêtre ▸ MediaSolution). En exécution
  // headless (CLI/MCP), seules les entrées globales ci-dessus sont disponibles.
  var inPanel = false;
  try {
    inPanel = (typeof Panel !== "undefined") && (thisObj instanceof Panel);
  } catch (ePanel) {
    inPanel = false;
  }

  if (inPanel) {
    try {
      MS.ui = thisObj;
      buildPanelUI(MS.ui);
      msLog("Media Solution " + SCRIPT_VERSION + " prêt (build " + SCRIPT_BUILD_ID + ")", "info");
      // Racine pré-remplie (constante autoDetectRootPath) et configuration de cette
      // racine rechargée automatiquement si elle existe déjà.
      tryLoadConfigForRoot(MS.refs.rootPath ? MS.refs.rootPath.text : "");
      var probeDir = "";
      try { probeDir = Folder.current ? Folder.current.fsName : ""; } catch (eDir) { }
      if (probeDir) {
        var access = checkScriptingAccess(probeDir);
        if (!access.ok) {
          msLog("Accès disque scripting refusé : le batch nécessitera l'activation de la préférence (bouton « Activer »).", "warning");
        }
      } else {
        msLog("Aucun projet ouvert : la préférence scripting sera vérifiée au démarrage du batch.", "info");
      }
      if (MS.ui instanceof Window) {
        MS.ui.center();
        MS.ui.show();
      } else {
        try { MS.ui.layout.layout(true); } catch (eLayout) { }
      }
    } catch (eUI) {
      msLog("Erreur construction UI : " + eUI.toString(), "error");
    }
  } else {
    try { $.writeln("Media Solution " + SCRIPT_VERSION + " — mode headless (entrées : $.global.mediasolutionEC)"); } catch (eH) { }
  }
})(this);
