/* Seamless Shorts Studio & Player — Unified Standalone + Studio Engine */
(function (globalScope) {
  'use strict';

  // ---------------- Pure Core Helpers (Browser + Node testable) ----------------

  function round3(n) {
    return Math.round((Number(n) || 0) * 1000) / 1000;
  }

  function basename(p) {
    if (!p) return '';
    return String(p).split(/[\\/]/).pop() || '';
  }

  function fmtTime(sec) {
    const sTotal = Math.max(0, Number(sec) || 0);
    const m = Math.floor(sTotal / 60);
    const s = Math.floor(sTotal % 60);
    return m + ':' + String(s).padStart(2, '0');
  }

  function fmtPrecise(sec) {
    const sTotal = Math.max(0, Number(sec) || 0);
    const m = Math.floor(sTotal / 60);
    const s = (sTotal % 60).toFixed(1).padStart(4, '0');
    return m + ':' + s;
  }

  function extractYouTubeId(filename) {
    if (!filename) return '';
    const base = basename(filename);
    const stem = base.replace(/\.[^.]+$/, '');
    return stem.replace(/^(?:Part_\d+_)?\d+_/, '');
  }

  function normalizeYouTubeContentField(val) {
    const raw = String(val || '').trim();
    if (!raw) return '';
    const urlMatch = raw.match(/(?:shorts\/|youtu\.be\/|[?&]v=)([A-Za-z0-9_-]{11})/);
    if (urlMatch) return urlMatch[1];
    return raw;
  }

  function parseCsvRfc4180(text) {
    const clean = String(text || '').replace(/^\uFEFF/, '');
    const records = [];
    let row = [];
    let field = '';
    let inQuotes = false;

    for (let i = 0; i < clean.length; i++) {
      const ch = clean[i];
      if (inQuotes) {
        if (ch === '"') {
          if (clean[i + 1] === '"') {
            field += '"';
            i += 1;
          } else {
            inQuotes = false;
          }
        } else {
          field += ch;
        }
      } else {
        if (ch === '"') {
          inQuotes = true;
        } else if (ch === ',') {
          row.push(field);
          field = '';
        } else if (ch === '\r') {
          if (clean[i + 1] === '\n') i += 1;
          row.push(field);
          records.push(row);
          row = [];
          field = '';
        } else if (ch === '\n') {
          row.push(field);
          records.push(row);
          row = [];
          field = '';
        } else {
          field += ch;
        }
      }
    }
    if (field.length > 0 || row.length > 0) {
      row.push(field);
      records.push(row);
    }

    if (records.length === 0) {
      return { headers: [], rows: [], byId: {} };
    }

    const headers = records[0].map((h) => String(h || '').trim());
    const lowerHeaders = headers.map((h) => h.toLowerCase());
    const findCol = (patterns) => {
      for (let j = 0; j < patterns.length; j++) {
        const pat = patterns[j];
        for (let i = 0; i < lowerHeaders.length; i++) {
          if (lowerHeaders[i] === pat) return i;
        }
      }
      for (let j = 0; j < patterns.length; j++) {
        const pat = patterns[j];
        for (let i = 0; i < lowerHeaders.length; i++) {
          if (lowerHeaders[i].includes(pat)) return i;
        }
      }
      return -1;
    };

    const idxContent = findCol(['content', 'video id', 'videoid', 'id']);
    const idxTitle = findCol(['video title', 'title']);
    const idxPublish = findCol(['video publish time', 'publish time', 'published']);
    const idxCtr = findCol(['impressions click-through rate', 'ctr']);
    const idxViews = findCol(['views']);
    const idxAvgDur = findCol(['average view duration', 'avg view duration']);
    const idxAvgPct = findCol(['average percentage viewed', 'avg percentage']);

    const rows = [];
    const byId = Object.create(null);

    for (let r = 1; r < records.length; r++) {
      const cols = records[r];
      if (!cols || cols.every((c) => !String(c || '').trim())) continue;

      const rawContent = idxContent >= 0 ? cols[idxContent] : cols[0];
      const videoId = normalizeYouTubeContentField(rawContent);
      if (!videoId || videoId.toLowerCase() === 'total') continue;

      const viewsRaw = idxViews >= 0 ? String(cols[idxViews] || '').trim() : '';
      const viewsCleaned = viewsRaw.replace(/,/g, '');
      const viewsNum = viewsCleaned !== '' ? Number(viewsCleaned) : NaN;
      const entry = {
        id: videoId,
        title: idxTitle >= 0 ? String(cols[idxTitle] || '').trim() : '',
        publishTime: idxPublish >= 0 ? String(cols[idxPublish] || '').trim() : '',
        ctr: idxCtr >= 0 ? String(cols[idxCtr] || '').trim() : '',
        views: Number.isFinite(viewsNum) ? viewsNum : null,
        viewsRaw,
        avgDuration: idxAvgDur >= 0 ? String(cols[idxAvgDur] || '').trim() : '',
        avgPercentViewed: idxAvgPct >= 0 ? String(cols[idxAvgPct] || '').trim() : '',
      };
      rows.push(entry);
      byId[videoId] = entry;
    }

    return { headers, rows, byId };
  }

  function matchClipMetadata(filename, csvById) {
    if (!filename || !csvById) return null;
    const lookup = (k) => {
      if (!k) return null;
      if (csvById instanceof Map) return csvById.get(k) || null;
      return Object.prototype.hasOwnProperty.call(csvById, k) ? csvById[k] : null;
    };

    const extracted = extractYouTubeId(filename);
    const direct = lookup(extracted);
    if (direct) return direct;

    const stem = basename(filename).replace(/\.[^.]+$/, '');
    const byStem = lookup(stem);
    if (byStem) return byStem;

    return null;
  }

  function computeSequentialPlan(clips, songDuration, fadeMs) {
    const safeFade = Math.max(0, Number.isFinite(Number(fadeMs)) ? Number(fadeMs) : 15);
    const sorted = (Array.isArray(clips) ? clips.slice() : []).sort((a, b) => {
      const na = String(a.source_filename || a.filename || a.name || '');
      const nb = String(b.source_filename || b.filename || b.name || '');
      return na < nb ? -1 : na > nb ? 1 : 0;
    });

    const hasFiniteSong = Number.isFinite(songDuration) && songDuration > 0;
    const parts = [];
    let cursor = 0;
    let partNum = 1;

    for (let i = 0; i < sorted.length; i++) {
      const clip = sorted[i];
      const rawDur = Number(
        clip.rawDuration !== undefined ? clip.rawDuration : clip.duration
      );
      if (!Number.isFinite(rawDur) || rawDur <= 0) continue;
      if (hasFiniteSong && cursor >= songDuration - 0.05) break;

      const remaining = hasFiniteSong ? songDuration - cursor : rawDur;
      const segDur = Math.min(rawDur, remaining);
      if (segDur <= 0.1) break;

      const sourceName = String(clip.source_filename || clip.filename || clip.name || ('clip_' + partNum + '.mp4'));
      const stem = sourceName.replace(/\.[^.]+$/, '');
      const partName = /^Part_\d+_/i.test(sourceName)
        ? sourceName
        : 'Part_' + String(partNum).padStart(2, '0') + '_' + stem + '.mp4';

      const start = round3(cursor);
      const end = round3(cursor + segDur);

      parts.push({
        part_num: partNum,
        filename: partName,
        source_filename: sourceName,
        duration: round3(segDur),
        actual_duration: round3(segDur),
        raw_duration: round3(rawDur),
        truncated: hasFiniteSong && segDur < rawDur - 0.01,
        song_start: start,
        song_end: end,
        verify_start: start,
        verify_end: end,
        codec: clip.codec || '',
        width: Number(clip.width) || 0,
        height: Number(clip.height) || 0,
        size_bytes: Number(clip.size_bytes || clip.size) || 0,
      });

      cursor += segDur;
      partNum += 1;
    }

    return {
      fade_ms: Math.round(safeFade),
      total_duration: round3(cursor),
      parts,
    };
  }

  function normalizeStudioState(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    const manifestObj = src.manifest && typeof src.manifest === 'object' ? src.manifest : {};

    const playlistUrl = src.playlist_url || src.active_playlist || manifestObj.playlist_url || '';
    const activeSongPath = src.active_song_path || src.active_song || manifestObj.song_path || '';
    const songName = src.song_name || manifestObj.song_name || basename(activeSongPath) || '';
    const parts = Array.isArray(src.parts)
      ? src.parts
      : Array.isArray(manifestObj.parts)
      ? manifestObj.parts
      : [];
    const verifyExists =
      src.verify_exists !== undefined ? Boolean(src.verify_exists) : Boolean(src.has_verify);
    const sources = Array.isArray(src.sources) ? src.sources : [];
    const sourceCount =
      src.source_count !== undefined ? Number(src.source_count) || 0 : sources.length;
    const fadeMs =
      src.fade_ms !== undefined
        ? Number(src.fade_ms)
        : manifestObj.fade_ms !== undefined
        ? Number(manifestObj.fade_ms)
        : 15;

    return {
      version: src.version || '',
      tools: src.tools || {},
      playlistUrl,
      activeSongPath,
      songName,
      isDemoSong: Boolean(
        src.is_demo_song !== undefined ? src.is_demo_song : manifestObj.is_demo_song
      ),
      audioFiles: Array.isArray(src.audio_files) ? src.audio_files : [],
      sources,
      sourceCount,
      parts,
      fadeMs: Number.isFinite(fadeMs) ? fadeMs : 15,
      verifyExists,
      verifyMtime: src.verify_mtime || manifestObj.updated_at || '',
      job: src.job || { running: false, step: 'Ready', progress: 0, logs: [], error: null },
    };
  }

  function sliceAudioChannelsWithFade(channels, sampleRate, startSec, endSec, fadeMs) {
    const sr = Math.max(8000, Number(sampleRate) || 44100);
    const safeStart = Math.max(0, Number(startSec) || 0);
    const safeEnd = Math.max(safeStart, Number(endSec) || safeStart);
    const startSample = Math.floor(safeStart * sr);
    const totalAvailable = channels && channels[0] ? channels[0].length : 0;
    const endSample = Math.min(totalAvailable, Math.max(startSample, Math.ceil(safeEnd * sr)));
    const sliceLen = Math.max(0, endSample - startSample);

    const fadeSec = Math.max(0, (Number(fadeMs) || 0) / 1000);
    const fadeSamples = Math.min(Math.round(fadeSec * sr), Math.floor(sliceLen / 3));

    const outChannels = [];
    for (let ch = 0; ch < channels.length; ch++) {
      const input = channels[ch];
      const out = new Float32Array(sliceLen);
      for (let i = 0; i < sliceLen; i++) {
        let sample = input[startSample + i];
        if (fadeSamples > 0) {
          if (i < fadeSamples) {
            sample *= i / fadeSamples;
          } else if (i >= sliceLen - fadeSamples) {
            const rem = sliceLen - 1 - i;
            sample *= rem / fadeSamples;
          }
        }
        out[i] = sample;
      }
      outChannels.push(out);
    }
    return outChannels;
  }

  function encodeWavFromChannels(channels, sampleRate) {
    const numChannels = Math.max(1, channels ? channels.length : 1);
    const numSamples = channels && channels[0] ? channels[0].length : 0;
    const sr = Math.max(8000, Math.round(Number(sampleRate) || 44100));
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sr * blockAlign;
    const dataSize = numSamples * blockAlign;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    function writeAscii(offset, str) {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    }

    writeAscii(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeAscii(8, 'WAVE');
    writeAscii(12, 'fmt ');
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true); // PCM format
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sr, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true); // bits per sample
    writeAscii(36, 'data');
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < numSamples; i++) {
      for (let ch = 0; ch < numChannels; ch++) {
        const s = Math.max(-1, Math.min(1, channels[ch][i] || 0));
        const pcm = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
        view.setInt16(offset, pcm, true);
        offset += 2;
      }
    }
    return new Uint8Array(buffer);
  }

  function sliceAudioBufferToWav(audioBuffer, startSec, endSec, fadeMs) {
    const numCh = audioBuffer.numberOfChannels || 1;
    const channels = [];
    for (let c = 0; c < numCh; c++) {
      channels.push(audioBuffer.getChannelData(c));
    }
    const sliced = sliceAudioChannelsWithFade(
      channels,
      audioBuffer.sampleRate,
      startSec,
      endSec,
      fadeMs
    );
    const bytes = encodeWavFromChannels(sliced, audioBuffer.sampleRate);
    const blob = typeof Blob !== 'undefined' ? new Blob([bytes], { type: 'audio/wav' }) : null;
    return {
      bytes,
      blob,
      sampleRate: audioBuffer.sampleRate,
      numSamples: sliced[0] ? sliced[0].length : 0,
      duration: sliced[0] ? round3(sliced[0].length / audioBuffer.sampleRate) : 0,
    };
  }

  function computeWaveformPeaks(audioBuffer, numBuckets) {
    const buckets = Math.max(32, Math.min(1200, Number(numBuckets) || 480));
    const channel = audioBuffer.getChannelData(0);
    const step = Math.max(1, Math.floor(channel.length / buckets));
    const peaks = new Float32Array(buckets);
    for (let b = 0; b < buckets; b++) {
      const start = b * step;
      const end = Math.min(channel.length, start + step);
      let maxVal = 0;
      for (let i = start; i < end; i++) {
        const a = Math.abs(channel[i]);
        if (a > maxVal) maxVal = a;
      }
      peaks[b] = maxVal;
    }
    return peaks;
  }

  function buildManifestPayload(opts) {
    const o = opts || {};
    return {
      playlist_url: o.playlistUrl || '',
      song_name: o.songName || '',
      is_demo_song: Boolean(o.isDemoSong),
      fade_ms: Number.isFinite(Number(o.fadeMs)) ? Number(o.fadeMs) : 15,
      updated_at: Math.floor(Date.now() / 1000),
      parts: (Array.isArray(o.parts) ? o.parts : []).map((p) => {
        const copy = {
          part_num: p.part_num,
          filename: p.filename,
          source_filename: p.source_filename,
          duration: round3(p.duration),
          song_start: round3(p.song_start),
          song_end: round3(p.song_end),
          verify_start: round3(p.verify_start),
          verify_end: round3(p.verify_end),
          codec: p.codec || '',
          width: Number(p.width) || 0,
          height: Number(p.height) || 0,
          size_bytes: Number(p.size_bytes) || 0,
        };
        if (p.youtube_id) copy.youtube_id = p.youtube_id;
        if (p.youtube_title) copy.youtube_title = p.youtube_title;
        return copy;
      }),
    };
  }

  const SeamlessCore = {
    round3,
    basename,
    fmtTime,
    fmtPrecise,
    extractYouTubeId,
    parseCsvRfc4180,
    matchClipMetadata,
    computeSequentialPlan,
    normalizeStudioState,
    sliceAudioChannelsWithFade,
    encodeWavFromChannels,
    sliceAudioBufferToWav,
    computeWaveformPeaks,
    buildManifestPayload,
  };

  globalScope.SeamlessCore = SeamlessCore;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = SeamlessCore;
  }

  // ---------------- Browser Application Runtime ----------------
  if (typeof document === 'undefined') {
    return;
  }

  const VIDEO_EXTS = new Set(['mp4', 'mov', 'webm', 'mkv', 'm4v']);
  const AUDIO_EXTS = new Set(['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'opus']);

  function getFileExt(name) {
    const base = basename(name);
    const dot = base.lastIndexOf('.');
    return dot >= 0 ? base.slice(dot + 1).toLowerCase() : '';
  }

  function safeStorageGet(key, fallback) {
    try {
      const val = window.localStorage.getItem(key);
      return val !== null ? val : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function safeStorageSet(key, val) {
    try {
      window.localStorage.setItem(key, val);
    } catch (_) {
      // Ignore storage restriction in private/file:// browsers
    }
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function getEl(id) {
    return document.getElementById(id);
  }

  const dom = {
    modeBadge: getEl('modeBadge'),
    themeToggleBtn: getEl('themeToggleBtn'),
    themeIcon: getEl('themeIcon'),
    themeLabel: getEl('themeLabel'),
    shortcutsBtn: getEl('shortcutsBtn'),
    shortcutsPopover: getEl('shortcutsPopover'),
    closeShortcutsBtn: getEl('closeShortcutsBtn'),

    step1Indicator: getEl('step1Indicator'),
    step2Indicator: getEl('step2Indicator'),
    step3Indicator: getEl('step3Indicator'),
    step1Sub: getEl('step1Sub'),
    step2Sub: getEl('step2Sub'),
    step3Sub: getEl('step3Sub'),

    playerTitle: getEl('playerTitle'),
    playerModeSubtitle: getEl('playerModeSubtitle'),
    modeSequenceBtn: getEl('modeSequenceBtn'),
    modeVerifyBtn: getEl('modeVerifyBtn'),
    demoBanner: getEl('demoBanner'),
    codecNotice: getEl('codecNotice'),
    codecNoticeText: getEl('codecNoticeText'),
    dismissCodecNoticeBtn: getEl('dismissCodecNoticeBtn'),

    playerStage: getEl('playerStage'),
    videoA: getEl('videoA'),
    videoB: getEl('videoB'),
    stageEmpty: getEl('stageEmpty'),
    activeCutBadge: getEl('activeCutBadge'),
    songAudio: getEl('songAudio'),

    nowPlayingTitle: getEl('nowPlayingTitle'),
    nowPlayingMeta: getEl('nowPlayingMeta'),
    timeReadout: getEl('timeReadout'),

    playPauseBtn: getEl('playPauseBtn'),
    playPauseIcon: getEl('playPauseIcon'),
    playPauseLabel: getEl('playPauseLabel'),
    auditionCutBtn: getEl('auditionCutBtn'),
    prevCutBtn: getEl('prevCutBtn'),
    nextCutBtn: getEl('nextCutBtn'),
    loopClipBtn: getEl('loopClipBtn'),
    speedSelect: getEl('speedSelect'),
    fullscreenBtn: getEl('fullscreenBtn'),

    activeSongDisplay: getEl('activeSongDisplay'),
    quickSongBtn: getEl('quickSongBtn'),
    muteVideoBtn: getEl('muteVideoBtn'),
    videoVolSlider: getEl('videoVolSlider'),
    videoVolValue: getEl('videoVolValue'),
    songVolSlider: getEl('songVolSlider'),
    songVolValue: getEl('songVolValue'),

    dropzone: getEl('dropzone'),
    pickFilesBtn: getEl('pickFilesBtn'),
    pickFolderBtn: getEl('pickFolderBtn'),
    pickZipBtn: getEl('pickZipBtn'),
    pickSongBtn: getEl('pickSongBtn'),
    pickCsvBtn: getEl('pickCsvBtn'),
    filesInput: getEl('filesInput'),
    folderInput: getEl('folderInput'),
    zipInput: getEl('zipInput'),
    songInput: getEl('songInput'),
    csvInput: getEl('csvInput'),
    loadSummary: getEl('loadSummary'),
    standaloneFadeMs: getEl('standaloneFadeMs'),
    csvStatusText: getEl('csvStatusText'),
    clearWorkspaceBtn: getEl('clearWorkspaceBtn'),

    studioPanel: getEl('studioPanel'),
    studioToolPills: getEl('studioToolPills'),
    playlistUrl: getEl('playlistUrl'),
    songSelect: getEl('songSelect'),
    studioSongDrop: getEl('studioSongDrop'),
    studioUploadSongBtn: getEl('studioUploadSongBtn'),
    studioSongFileInput: getEl('studioSongFileInput'),
    studioSongStatus: getEl('studioSongStatus'),
    maxDownloads: getEl('maxDownloads'),
    fadeMs: getEl('fadeMs'),
    cookiesFromBrowser: getEl('cookiesFromBrowser'),
    skipDownload: getEl('skipDownload'),
    browserSafe: getEl('browserSafe'),
    runBtn: getEl('runBtn'),
    stepLabel: getEl('stepLabel'),
    pctLabel: getEl('pctLabel'),
    progressBar: getEl('progressBar'),
    progressFill: getEl('progressFill'),
    logBox: getEl('logBox'),

    timelineShell: getEl('timelineShell'),
    timelineSummary: getEl('timelineSummary'),
    timeRuler: getEl('timeRuler'),
    clipBlocksTrack: getEl('clipBlocksTrack'),
    cutMarkersTrack: getEl('cutMarkersTrack'),
    waveformLane: getEl('waveformLane'),
    waveformCanvas: getEl('waveformCanvas'),
    waveformHint: getEl('waveformHint'),
    playheadOverlay: getEl('playheadOverlay'),
    playheadLine: getEl('playheadLine'),
    playheadTooltip: getEl('playheadTooltip'),

    clipsCountBadge: getEl('clipsCountBadge'),
    clipsList: getEl('clipsList'),

    dlVerifyBtn: getEl('dlVerifyBtn'),
    dlManifestBtn: getEl('dlManifestBtn'),
    dlWavZipBtn: getEl('dlWavZipBtn'),
    dlAllZipBtn: getEl('dlAllZipBtn'),
    dlToolkitBtn: getEl('dlToolkitBtn'),
    exportStatus: getEl('exportStatus'),
  };

  // ---------------- Application State ----------------
  const state = {
    mode: 'standalone', // 'standalone' | 'studio'
    playbackView: 'sequence', // 'sequence' | 'verify'
    activeSlot: 'A', // 'A' | 'B'
    activePartIdx: 0,
    lastAuditionedCutIdx: null,
    playing: false,
    loopClip: false,
    playbackRate: 1,
    globalTime: 0,
    totalDuration: 0,

    muteOriginal: true,
    videoVol: 0,
    songVol: 1,
    hasBrowserSongOverride: false,

    fadeMs: 15,
    playlistUrl: '',
    isDemoSong: false,

    // Loaded media assets
    sourceClips: [], // [{ filename, rawDuration, width, height, size_bytes, blob, objectUrl }]
    partMediaByName: new Map(), // filename -> { blob, objectUrl, name }
    verifyMedia: null, // { name, blob, objectUrl, url }
    songMedia: null, // { name, blob, objectUrl, url, duration, audioBuffer, peaks }
    manifestRaw: null,
    parts: [], // Normalized sequence parts

    csvById: Object.create(null),
    csvCount: 0,

    // Studio server metadata
    studio: null,
    studioFirstInit: true,
    studioWasRunning: false,
    studioVerifyVer: '',
    studioAudioFetchedName: '',
    pollTimerId: null,

    // Web Audio mixer & virtual clock
    audioCtx: null,
    mediaNodesReady: false,
    songGainNode: null,
    videoGainNode: null,
    rafId: null,
    lastRafTs: 0,
    virtualClockMode: false, // used when no playable video frames (metadata/audio-only or codec error)
    objectUrls: [],
  };

  // ---------------- Web Audio Mixer Setup ----------------
  function ensureAudioGraph() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return null;
    if (!state.audioCtx) {
      try {
        state.audioCtx = new AudioCtx();
      } catch (_) {
        return null;
      }
    }
    if (!state.mediaNodesReady && state.audioCtx) {
      try {
        const ctx = state.audioCtx;
        state.songGainNode = ctx.createGain();
        state.videoGainNode = ctx.createGain();
        state.songGainNode.gain.value = state.songVol;
        state.videoGainNode.gain.value = state.muteOriginal ? 0 : state.videoVol;

        const srcSong = ctx.createMediaElementSource(dom.songAudio);
        const srcA = ctx.createMediaElementSource(dom.videoA);
        const srcB = ctx.createMediaElementSource(dom.videoB);

        srcSong.connect(state.songGainNode);
        state.songGainNode.connect(ctx.destination);

        srcA.connect(state.videoGainNode);
        srcB.connect(state.videoGainNode);
        state.videoGainNode.connect(ctx.destination);

        state.mediaNodesReady = true;
      } catch (_) {
        // Fallback cleanly to HTMLMediaElement volume controls
        state.mediaNodesReady = false;
      }
    }
    return state.audioCtx;
  }

  function applyAudioVolumes() {
    const effectiveVideoVol = state.muteOriginal ? 0 : state.videoVol;
    if (state.mediaNodesReady && state.videoGainNode && state.songGainNode) {
      dom.videoA.volume = 1;
      dom.videoB.volume = 1;
      dom.songAudio.volume = 1;
      state.videoGainNode.gain.value = effectiveVideoVol;
      state.songGainNode.gain.value = state.songVol;
    } else {
      dom.videoA.volume = effectiveVideoVol;
      dom.videoB.volume = effectiveVideoVol;
      dom.songAudio.volume = state.songVol;
    }
    dom.videoA.muted = state.muteOriginal || effectiveVideoVol === 0;
    dom.videoB.muted = state.muteOriginal || effectiveVideoVol === 0;

    dom.muteVideoBtn.setAttribute('aria-pressed', String(state.muteOriginal));
    dom.muteVideoBtn.textContent = state.muteOriginal ? 'Orig Audio: Muted' : 'Orig Audio: On';
    dom.videoVolValue.textContent = Math.round(state.videoVol * 100) + '%';
    dom.songVolValue.textContent = Math.round(state.songVol * 100) + '%';
  }

  function usingReplacementSongAudio() {
    if (!state.songMedia || (!state.songMedia.objectUrl && !state.songMedia.url)) return false;
    if (state.playbackView === 'verify') {
      return state.hasBrowserSongOverride;
    }
    return true;
  }

  // ---------------- Theme Management ----------------
  function setTheme(theme) {
    const clean = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', clean);
    safeStorageSet('seamless_shorts_theme', clean);
    const isLight = clean === 'light';
    dom.themeToggleBtn.setAttribute('aria-pressed', String(isLight));
    dom.themeIcon.textContent = isLight ? '☾' : '☀';
    dom.themeLabel.textContent = isLight ? 'Dark Mode' : 'Light Mode';
    drawWaveform();
  }

  // ---------------- Active Video Slot Helpers ----------------
  function getActiveVideo() {
    return state.activeSlot === 'A' ? dom.videoA : dom.videoB;
  }

  function getStandbyVideo() {
    return state.activeSlot === 'A' ? dom.videoB : dom.videoA;
  }

  function activateSlot(slot) {
    state.activeSlot = slot === 'B' ? 'B' : 'A';
    dom.videoA.classList.toggle('is-active', state.activeSlot === 'A');
    dom.videoB.classList.toggle('is-active', state.activeSlot === 'B');
    const standby = getStandbyVideo();
    if (standby && !standby.paused) {
      standby.pause();
    }
  }

  function resolvePartUrl(part) {
    if (!part) return '';
    if (part.objectUrl) return part.objectUrl;
    const byPart = state.partMediaByName.get(part.filename);
    if (byPart && byPart.objectUrl) return byPart.objectUrl;
    const bySrc = state.partMediaByName.get(part.source_filename);
    if (bySrc && bySrc.objectUrl) return bySrc.objectUrl;
    const srcMatch = state.sourceClips.find((c) => c.filename === part.source_filename || c.filename === part.filename);
    if (srcMatch && srcMatch.objectUrl) return srcMatch.objectUrl;
    if (state.mode === 'studio') {
      if (state.hasBrowserSongOverride && part.source_filename && state.studio && state.studio.sources.includes(part.source_filename)) {
        return 'media/source/' + encodeURIComponent(part.source_filename);
      }
      if (part.filename) {
        return 'media/part/' + encodeURIComponent(part.filename) + '?v=' + encodeURIComponent(state.studioVerifyVer || '1');
      }
    }
    return '';
  }

  function resolveVerifyUrl() {
    if (state.verifyMedia && state.verifyMedia.objectUrl) return state.verifyMedia.objectUrl;
    if (state.mode === 'studio' && state.studio && state.studio.verifyExists) {
      return 'media/verify?v=' + encodeURIComponent(state.studioVerifyVer || '1');
    }
    return '';
  }

  // ---------------- CSV Metadata Enrichment & UI Refresh ----------------
  function applyCsvEnrichment() {
    state.parts.forEach((p) => {
      const meta =
        matchClipMetadata(p.source_filename, state.csvById) ||
        matchClipMetadata(p.filename, state.csvById);
      if (meta) {
        p.youtube_id = meta.id;
        p.youtube_title = meta.title;
        p.youtube_views = meta.views;
        p.youtube_avg_duration = meta.avgDuration;
        p.youtube_avg_pct = meta.avgPercentViewed;
      }
    });
  }

  function recomputeStandalonePlan() {
    state.lastAuditionedCutIdx = null;
    const rawFadeVal = dom.standaloneFadeMs.value;
    if (rawFadeVal !== '') {
      const parsedFade = Number(rawFadeVal);
      if (Number.isFinite(parsedFade)) {
        state.fadeMs = Math.max(0, parsedFade);
      }
    }

    if (state.sourceClips.length > 0 && (!state.manifestRaw || state.hasBrowserSongOverride || state.partMediaByName.size === 0)) {
      const songDur = state.songMedia && state.songMedia.duration > 0 ? state.songMedia.duration : Infinity;
      const plan = computeSequentialPlan(state.sourceClips, songDur, state.fadeMs);
      state.parts = plan.parts.map((p) => {
        const clip = state.sourceClips.find((c) => c.filename === p.source_filename);
        return Object.assign({}, p, {
          objectUrl: clip ? clip.objectUrl : '',
          codecError: clip ? Boolean(clip.codecError) : false,
        });
      });
      state.totalDuration = plan.total_duration;
    } else if (state.manifestRaw && Array.isArray(state.manifestRaw.parts)) {
      state.parts = state.manifestRaw.parts.map((p) => Object.assign({}, p));
      state.totalDuration = state.parts.reduce((acc, p) => acc + (Number(p.duration) || 0), 0);
      if (state.hasBrowserSongOverride && state.songMedia && state.songMedia.duration > 0) {
        const songDur = state.songMedia.duration;
        const plan = computeSequentialPlan(
          state.parts.map((p) => ({
            source_filename: p.source_filename || p.filename,
            rawDuration: p.raw_duration || p.duration,
            width: p.width,
            height: p.height,
            codec: p.codec,
            size_bytes: p.size_bytes,
          })),
          songDur,
          state.fadeMs
        );
        state.parts = plan.parts;
        state.totalDuration = plan.total_duration;
      }
    } else {
      state.parts = [];
      state.totalDuration = 0;
    }
    applyCsvEnrichment();
  }

  function updateStepperAndButtons() {
    const hasClips = state.parts.length > 0;
    const hasVerify = Boolean(resolveVerifyUrl());
    const hasSong = Boolean(state.songMedia && (state.songMedia.objectUrl || state.songMedia.url || state.songMedia.audioBuffer));

    dom.modeVerifyBtn.disabled = !hasVerify;
    dom.modeSequenceBtn.disabled = !hasClips;
    if (!hasVerify && state.playbackView === 'verify') {
      state.playbackView = 'sequence';
    }
    dom.modeVerifyBtn.classList.toggle('is-active', state.playbackView === 'verify');
    dom.modeSequenceBtn.classList.toggle('is-active', state.playbackView === 'sequence');

    const canPlay = hasClips || hasVerify || hasSong;
    dom.playPauseBtn.disabled = !canPlay;
    dom.auditionCutBtn.disabled = !(hasClips && state.parts.length > 1) && !hasClips;
    dom.prevCutBtn.disabled = !hasClips;
    dom.nextCutBtn.disabled = !hasClips;

    dom.dlVerifyBtn.disabled = !hasVerify;
    dom.dlManifestBtn.disabled = !hasClips;
    dom.dlWavZipBtn.disabled = !(hasClips && state.songMedia && state.songMedia.audioBuffer);
    dom.dlAllZipBtn.disabled = !(hasClips || hasVerify);
    dom.clearWorkspaceBtn.hidden = state.mode === 'studio' || (!hasClips && !hasVerify && !hasSong && state.csvCount === 0);

    dom.step1Indicator.classList.toggle('is-done', hasClips || hasVerify);
    dom.step2Indicator.classList.toggle('is-active', hasClips || hasVerify);
    dom.step3Indicator.classList.toggle('is-done', hasClips && (hasSong || hasVerify));

    if (hasClips || hasVerify) {
      dom.step1Sub.textContent = state.parts.length + ' clips' + (hasSong ? ' + song ready' : '');
      dom.step2Sub.textContent = Math.max(0, state.parts.length - 1) + ' transition cuts';
      dom.step3Sub.textContent = state.songMedia && state.songMedia.audioBuffer ? 'WAV slices + ZIP ready' : 'Plan + ZIP ready';
    } else {
      dom.step1Sub.textContent = 'Drop clips, folder, or .zip';
      dom.step2Sub.textContent = 'Check every -2s transition';
      dom.step3Sub.textContent = 'Clips, WAV slices & ZIP';
    }

    dom.loadSummary.textContent =
      state.parts.length +
      ' clip' +
      (state.parts.length === 1 ? '' : 's') +
      (state.totalDuration > 0 ? ' · ' + fmtTime(state.totalDuration) : '');
    dom.clipsCountBadge.textContent = state.parts.length + ' part' + (state.parts.length === 1 ? '' : 's');
  }

  // ---------------- Timeline & Waveform Rendering ----------------
  function renderTimeline() {
    const total = Math.max(0.01, state.totalDuration || 0);
    dom.timeRuler.innerHTML = '';
    dom.clipBlocksTrack.innerHTML = '';
    dom.cutMarkersTrack.innerHTML = '';

    if (state.parts.length === 0) {
      dom.timelineSummary.textContent = 'Load Shorts or a workspace to inspect transition cuts and audio waveform';
      updatePlayheadUI();
      drawWaveform();
      return;
    }

    const cutsCount = Math.max(0, state.parts.length - 1);
    dom.timelineSummary.textContent =
      state.parts.length + ' clips · ' + cutsCount + ' transition cut' + (cutsCount === 1 ? '' : 's') + ' · Total ' + fmtPrecise(total);

    // Ruler ticks
    const tickCount = total >= 180 ? 6 : total >= 40 ? 5 : 4;
    for (let i = 0; i <= tickCount; i++) {
      const t = (total * i) / tickCount;
      const tick = document.createElement('span');
      tick.className = 'ruler-tick';
      tick.style.left = (i / tickCount) * 100 + '%';
      tick.textContent = fmtTime(t);
      dom.timeRuler.appendChild(tick);
    }

    // Clip blocks
    state.parts.forEach((p, idx) => {
      const dur = Number(p.duration) || 0;
      const pct = Math.max(0.5, (dur / total) * 100);
      const block = document.createElement('button');
      block.type = 'button';
      block.className = 'clip-block';
      block.dataset.idx = String(idx);
      block.style.width = pct + '%';
      const partNumSafe = Number(p.part_num) || 0;
      block.title = (p.youtube_title || p.filename || 'Part ' + partNumSafe) + ' (' + dur.toFixed(1) + 's)';
      block.innerHTML =
        '<span class="clip-block-label">P' + partNumSafe + '</span>' +
        '<span class="clip-block-dur">' + dur.toFixed(1) + 's</span>';
      block.addEventListener('click', (e) => {
        e.stopPropagation();
        seekGlobal(Number(p.verify_start) || 0, true);
      });
      dom.clipBlocksTrack.appendChild(block);

      // Cut marker for transition boundary (idx > 0)
      if (idx > 0) {
        const cutTime = Number(p.verify_start) || 0;
        const leftPct = Math.max(0, Math.min(100, (cutTime / total) * 100));
        const pin = document.createElement('button');
        pin.type = 'button';
        pin.className = 'cut-marker';
        pin.dataset.idx = String(idx);
        pin.style.left = leftPct + '%';
        pin.textContent = 'C' + idx;
        pin.title = 'Audition Cut #' + idx + ' at ' + fmtPrecise(cutTime) + ' (-2s)' + (p.youtube_title ? ' · ' + p.youtube_title : '');
        pin.addEventListener('click', (e) => {
          e.stopPropagation();
          auditionCutAt(idx);
        });
        dom.cutMarkersTrack.appendChild(pin);
      }
    });

    updatePlayheadUI();
    drawWaveform();
  }

  function drawWaveform() {
    const canvas = dom.waveformCanvas;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const cssW = Math.max(240, Math.floor(rect.width || 600));
    const cssH = Math.max(48, Math.floor(rect.height || 68));
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const styles = getComputedStyle(document.documentElement);
    const colPlayed = styles.getPropertyValue('--wave-played').trim() || '#22d3ee';
    const colIdle = styles.getPropertyValue('--wave-idle').trim() || '#64748b';
    const colCut = styles.getPropertyValue('--wave-cut').trim() || '#f59e0b';

    const peaks = state.songMedia && state.songMedia.peaks ? state.songMedia.peaks : null;
    const hasWave = Boolean(peaks && peaks.length > 0);
    dom.waveformHint.hidden = hasWave || state.parts.length > 0;

    const midY = cssH / 2;
    const totalDur = Math.max(0.01, state.totalDuration || (state.songMedia ? state.songMedia.duration : 0) || 1);
    const progressRatio = Math.max(0, Math.min(1, state.globalTime / totalDur));

    if (hasWave) {
      const songDur = state.songMedia.duration > 0 ? state.songMedia.duration : totalDur;
      const barCount = Math.min(peaks.length, Math.floor(cssW / 3));
      const barW = Math.max(1.5, cssW / barCount - 1);

      for (let b = 0; b < barCount; b++) {
        const timelineFrac = b / barCount;
        const timelineTime = timelineFrac * totalDur;
        const songFrac = Math.max(0, Math.min(0.999, timelineTime / songDur));
        const peakIdx = Math.floor(songFrac * peaks.length);
        const amp = Math.max(0.06, Math.min(1, peaks[peakIdx] || 0.08));
        const h = amp * (cssH - 12);
        const x = (b / barCount) * cssW;

        ctx.fillStyle = timelineFrac <= progressRatio ? colPlayed : colIdle;
        ctx.fillRect(x, midY - h / 2, barW, h);
      }
    } else if (state.parts.length > 0) {
      // Deterministic visual envelope when waveform audio hasn't decoded yet
      const bars = Math.floor(cssW / 4);
      for (let b = 0; b < bars; b++) {
        const frac = b / bars;
        const amp = 0.16 + 0.12 * Math.abs(Math.sin(b * 0.45));
        const h = amp * (cssH - 16);
        ctx.fillStyle = frac <= progressRatio ? colPlayed : colIdle;
        ctx.globalAlpha = 0.42;
        ctx.fillRect(b * 4, midY - h / 2, 2, h);
      }
      ctx.globalAlpha = 1;
    }

    // Draw cut marker lines across waveform
    if (state.parts.length > 1 && totalDur > 0) {
      ctx.strokeStyle = colCut;
      ctx.lineWidth = 1.5;
      for (let i = 1; i < state.parts.length; i++) {
        const x = ((Number(state.parts[i].verify_start) || 0) / totalDur) * cssW;
        ctx.beginPath();
        ctx.moveTo(x, 2);
        ctx.lineTo(x, cssH - 2);
        ctx.stroke();
      }
    }
  }

  function updatePlayheadUI() {
    const total = Math.max(0.01, state.totalDuration || 0);
    const pct = state.totalDuration > 0 ? Math.max(0, Math.min(100, (state.globalTime / total) * 100)) : 0;
    dom.playheadLine.style.left = pct + '%';
    dom.playheadTooltip.style.left = pct + '%';
    dom.timeReadout.textContent = fmtPrecise(state.globalTime) + ' / ' + fmtPrecise(state.totalDuration);

    // Highlight active part in timeline and clip cards
    let activeIdx = -1;
    for (let i = 0; i < state.parts.length; i++) {
      const s = Number(state.parts[i].verify_start) || 0;
      const e = Number(state.parts[i].verify_end) || s + (Number(state.parts[i].duration) || 0);
      if (state.globalTime >= s && (state.globalTime < e || i === state.parts.length - 1)) {
        activeIdx = i;
        break;
      }
    }
    if (activeIdx >= 0) {
      state.activePartIdx = activeIdx;
    }

    const activePart = state.parts[state.activePartIdx];
    dom.activeCutBadge.hidden = !activePart;
    if (activePart) {
      dom.activeCutBadge.textContent = 'Part ' + activePart.part_num + ' / ' + state.parts.length;
      dom.nowPlayingTitle.textContent = activePart.youtube_title || activePart.filename || ('Part ' + activePart.part_num);
      const bits = [];
      if (activePart.youtube_views !== null && activePart.youtube_views !== undefined) {
        bits.push(activePart.youtube_views.toLocaleString() + ' views');
      }
      if (activePart.codec) bits.push(activePart.codec.toUpperCase());
      if (activePart.width && activePart.height) bits.push(activePart.width + '×' + activePart.height);
      bits.push('Song ' + fmtPrecise(activePart.song_start) + ' → ' + fmtPrecise(activePart.song_end));
      dom.nowPlayingMeta.textContent = bits.join(' · ');
    }

    dom.clipBlocksTrack.querySelectorAll('.clip-block').forEach((el) => {
      el.classList.toggle('is-active', Number(el.dataset.idx) === state.activePartIdx);
    });
    dom.cutMarkersTrack.querySelectorAll('.cut-marker').forEach((el) => {
      el.classList.toggle('is-active', Number(el.dataset.idx) === state.activePartIdx);
    });
    dom.clipsList.querySelectorAll('.clip-row-card').forEach((el) => {
      el.classList.toggle('is-active', Number(el.dataset.idx) === state.activePartIdx);
    });
  }

  // ---------------- Clips & Transition Cards Rendering ----------------
  function renderClipsList() {
    dom.clipsList.innerHTML = '';
    if (state.parts.length === 0) {
      dom.clipsList.innerHTML = '<p class="empty-list-msg">No clips in sequence yet. Load MP4 Shorts or a workspace folder/zip above.</p>';
      return;
    }

    state.parts.forEach((p, idx) => {
      const card = document.createElement('article');
      card.className = 'clip-row-card';
      card.dataset.idx = String(idx);

      const titleHtml = p.youtube_title
        ? '<div class="clip-yt-title" title="' + escapeHtml(p.youtube_title) + '">' + escapeHtml(p.youtube_title) + '</div>'
        : '';

      const statsParts = [];
      if (p.youtube_views !== null && p.youtube_views !== undefined) {
        statsParts.push('<span>' + escapeHtml(p.youtube_views.toLocaleString()) + ' views</span>');
      }
      if (p.youtube_avg_duration) {
        statsParts.push('<span>Avg ' + escapeHtml(p.youtube_avg_duration) + (p.youtube_avg_pct ? ' (' + escapeHtml(p.youtube_avg_pct) + '%)' : '') + '</span>');
      }
      if (p.codec) {
        statsParts.push('<span>' + escapeHtml(p.codec.toUpperCase()) + '</span>');
      }
      const wSafe = Number(p.width) || 0;
      const hSafe = Number(p.height) || 0;
      if (wSafe && hSafe) {
        statsParts.push('<span>' + wSafe + '×' + hSafe + '</span>');
      }
      if (p.truncated) {
        statsParts.push('<span>Trimmed to song end</span>');
      }

      const partNumSafe = Number(p.part_num) || 0;
      const partUrl = resolvePartUrl(p);
      const canDownloadMp4 = Boolean(partUrl);
      const canWavSlice = Boolean(state.songMedia && state.songMedia.audioBuffer);

      card.innerHTML =
        '<div>' +
          '<div class="clip-card-head">' +
            '<span class="clip-part-badge">Part ' + partNumSafe + (idx > 0 ? ' · Cut #' + idx : '') + '</span>' +
            '<span class="clip-dur-badge">' + (Number(p.duration) || 0).toFixed(2) + 's</span>' +
          '</div>' +
          titleHtml +
          '<div class="clip-meta-lines">' +
            '<span>File: <code>' + escapeHtml(p.filename || p.source_filename) + '</code></span>' +
            '<span>Song slice: ' + fmtPrecise(p.song_start) + ' → ' + fmtPrecise(p.song_end) + '</span>' +
          '</div>' +
          (statsParts.length ? '<div class="clip-stats-row">' + statsParts.join('') + '</div>' : '') +
        '</div>' +
        '<div class="clip-actions">' +
          '<button type="button" class="btn btn-accent btn-xs" data-act="audition">' + (idx > 0 ? 'Audition (−2s)' : 'Play Start') + '</button>' +
          '<button type="button" class="btn btn-xs" data-act="jump">Solo / Jump</button>' +
          '<button type="button" class="btn btn-xs" data-act="wav"' + (canWavSlice ? '' : ' disabled') + '>WAV Slice</button>' +
          '<button type="button" class="btn btn-xs" data-act="dl"' + (canDownloadMp4 ? '' : ' disabled') + '>MP4</button>' +
        '</div>';

      card.querySelector('[data-act="audition"]').addEventListener('click', () => {
        if (idx > 0) auditionCutAt(idx);
        else seekGlobal(0, true);
      });
      card.querySelector('[data-act="jump"]').addEventListener('click', () => {
        seekGlobal(Number(p.verify_start) || 0, true);
      });
      card.querySelector('[data-act="wav"]').addEventListener('click', () => {
        downloadPartWav(p);
      });
      card.querySelector('[data-act="dl"]').addEventListener('click', () => {
        downloadSinglePartMp4(p);
      });

      dom.clipsList.appendChild(card);
    });
  }

  // ---------------- Player & Dual-Video A/B Sync Engine ----------------
  function preloadStandbyFor(idx) {
    if (state.playbackView !== 'sequence') return;
    const nextIdx = idx + 1;
    if (nextIdx >= state.parts.length) return;
    const nextUrl = resolvePartUrl(state.parts[nextIdx]);
    if (!nextUrl) return;
    const standby = getStandbyVideo();
    if (standby.dataset.loadedSrc !== nextUrl) {
      standby.dataset.loadedSrc = nextUrl;
      standby.dataset.partIdx = String(nextIdx);
      standby.src = nextUrl;
      standby.load();
    } else {
      standby.dataset.partIdx = String(nextIdx);
      if (standby.readyState >= 1 && standby.currentTime > 0.02) {
        try { standby.currentTime = 0; } catch (_) {}
      }
    }
  }

  function loadStageForPart(idx, localOffset, autoPlay) {
    state.stageLoadToken = (state.stageLoadToken || 0) + 1;
    const loadToken = state.stageLoadToken;

    const verifyUrl = resolveVerifyUrl();
    if (state.playbackView === 'verify' && verifyUrl) {
      dom.stageEmpty.hidden = true;
      state.virtualClockMode = false;
      activateSlot('A');
      const v = dom.videoA;
      if (v.dataset.loadedSrc !== verifyUrl) {
        v.dataset.loadedSrc = verifyUrl;
        v.src = verifyUrl;
        v.load();
      }
      const target = Math.max(0, state.globalTime);
      const applySeek = () => {
        if (state.stageLoadToken !== loadToken) return;
        try { v.currentTime = target; } catch (_) {}
        v.playbackRate = state.playbackRate;
        if (autoPlay) playAll();
      };
      if (v.readyState >= 1) applySeek();
      else v.addEventListener('loadedmetadata', applySeek, { once: true });
      return;
    }

    const part = state.parts[idx];
    const partUrl = resolvePartUrl(part);
    const targetLocal = Math.max(0, localOffset || 0);
    if (part) {
      state.activePartIdx = idx;
      const partStart = Number(part.verify_start) || 0;
      const nextGlobal = partStart + targetLocal;
      state.globalTime = state.totalDuration > 0 ? Math.min(state.totalDuration, nextGlobal) : nextGlobal;
    }

    if (!part || !partUrl || part.codecError) {
      // Virtual timeline + song audio mode (e.g., manifest + song without video blobs or unplayable codec)
      dom.videoA.pause();
      dom.videoB.pause();
      dom.stageEmpty.hidden = state.parts.length > 0;
      if (part && part.codecError) {
        dom.codecNotice.hidden = false;
      }
      state.virtualClockMode = true;
      if (autoPlay) playAll();
      return;
    }

    dom.stageEmpty.hidden = true;
    state.virtualClockMode = false;
    state.activePartIdx = idx;

    const currentActive = getActiveVideo();
    const standby = getStandbyVideo();

    if (standby.dataset.loadedSrc === partUrl && currentActive.dataset.loadedSrc !== partUrl) {
      activateSlot(state.activeSlot === 'A' ? 'B' : 'A');
    }

    const v = getActiveVideo();
    const inactive = getStandbyVideo();
    if (inactive && !inactive.paused) {
      inactive.pause();
    }
    v.dataset.partIdx = String(idx);
    if (v.dataset.loadedSrc !== partUrl) {
      v.dataset.loadedSrc = partUrl;
      v.src = partUrl;
      v.load();
    }

    if (v.error && v.dataset.loadedSrc === partUrl) {
      part.codecError = true;
      dom.videoA.pause();
      dom.videoB.pause();
      dom.codecNotice.hidden = false;
      state.virtualClockMode = true;
      if (autoPlay) playAll();
      return;
    }

    const applySeek = () => {
      if (state.stageLoadToken !== loadToken) return;
      if (Math.abs((v.currentTime || 0) - targetLocal) > 0.02) {
        try {
          v.currentTime = targetLocal;
        } catch (_) {}
      }
      v.playbackRate = state.playbackRate;
      preloadStandbyFor(idx);
      if (autoPlay) playAll();
    };

    if (v.readyState >= 1) {
      applySeek();
    } else {
      v.addEventListener('loadedmetadata', applySeek, { once: true });
      v.addEventListener('error', () => {
        if (state.stageLoadToken !== loadToken) return;
        if (autoPlay && !state.playing) playAll();
      }, { once: true });
    }
  }

  function seekGlobal(targetSec, autoPlay) {
    state.lastAuditionedCutIdx = null;
    const total = Math.max(0, state.totalDuration || 0);
    const clamped = Math.max(0, total > 0 ? Math.min(total, targetSec) : targetSec);
    state.globalTime = clamped;

    let idx = 0;
    let localOffset = 0;
    for (let i = 0; i < state.parts.length; i++) {
      const s = Number(state.parts[i].verify_start) || 0;
      const e = Number(state.parts[i].verify_end) || s + (Number(state.parts[i].duration) || 0);
      if (clamped >= s && (clamped < e || i === state.parts.length - 1)) {
        idx = i;
        localOffset = Math.max(0, clamped - s);
        break;
      }
    }

    state.activePartIdx = idx;

    if (usingReplacementSongAudio() && dom.songAudio.src) {
      try {
        const part = state.parts[idx];
        const songTarget = part ? (Number(part.song_start) || 0) + localOffset : clamped;
        dom.songAudio.currentTime = Math.max(0, songTarget);
      } catch (_) {}
    }

    loadStageForPart(idx, localOffset, Boolean(autoPlay));
    updatePlayheadUI();
    drawWaveform();
  }

  function auditionCutAt(partIndex) {
    if (!state.parts.length) return;
    const clampedIdx = Math.max(0, Math.min(state.parts.length - 1, partIndex));
    const cutTime = Number(state.parts[clampedIdx].verify_start) || 0;
    const jumpTo = clampedIdx > 0 ? Math.max(0, cutTime - 2.0) : 0;
    seekGlobal(jumpTo, true);
    state.lastAuditionedCutIdx = clampedIdx;
  }

  function findNextCutIndex(dir) {
    if (!state.parts.length) return 0;
    const audIdx = state.lastAuditionedCutIdx;
    const audValid =
      Number.isInteger(audIdx) &&
      audIdx >= 0 &&
      audIdx < state.parts.length &&
      state.globalTime <= (Number(state.parts[audIdx].verify_start) || 0) + 0.25;

    if (dir < 0) {
      if (audValid) {
        return Math.max(0, audIdx - 1);
      }
      for (let i = state.parts.length - 1; i >= 1; i--) {
        const cut = Number(state.parts[i].verify_start) || 0;
        if (cut < state.globalTime - 2.4) return i;
      }
      return 0;
    } else {
      if (audValid) {
        if (audIdx + 1 < state.parts.length) return audIdx + 1;
        return Math.min(1, state.parts.length - 1);
      }
      for (let i = 1; i < state.parts.length; i++) {
        const cut = Number(state.parts[i].verify_start) || 0;
        if (cut > state.globalTime + 0.25) return i;
      }
      return Math.min(1, state.parts.length - 1);
    }
  }

  function playAll() {
    const ctx = ensureAudioGraph();
    if (ctx && ctx.state === 'suspended') {
      ctx.resume().catch(() => {});
    }
    applyAudioVolumes();
    state.playing = true;
    state.lastRafTs = performance.now();
    dom.playPauseIcon.textContent = '❚❚';
    dom.playPauseLabel.textContent = 'Pause';

    if (!state.virtualClockMode) {
      const v = getActiveVideo();
      if (v.src && !v.error) {
        v.playbackRate = state.playbackRate;
        v.play().catch((err) => {
          if (
            (err && err.name === 'AbortError') ||
            !state.playing ||
            v !== getActiveVideo()
          ) {
            return;
          }
          if ((err && err.name === 'NotSupportedError') || v.error) {
            state.virtualClockMode = true;
            dom.videoA.pause();
            dom.videoB.pause();
          }
        });
      }
    }

    if (usingReplacementSongAudio() && dom.songAudio.src) {
      const part = state.parts[state.activePartIdx];
      const localOffset = part ? Math.max(0, state.globalTime - (Number(part.verify_start) || 0)) : state.globalTime;
      const expectedSong = part ? (Number(part.song_start) || 0) + localOffset : state.globalTime;
      try {
        if (Math.abs((dom.songAudio.currentTime || 0) - expectedSong) > 0.05) {
          dom.songAudio.currentTime = expectedSong;
        }
      } catch (_) {}
      dom.songAudio.playbackRate = state.playbackRate;
      dom.songAudio.play().catch(() => {});
    }

    startTickLoop();
  }

  function pauseAll() {
    state.playing = false;
    dom.playPauseIcon.textContent = '▶';
    dom.playPauseLabel.textContent = 'Play';
    dom.videoA.pause();
    dom.videoB.pause();
    dom.songAudio.pause();
    if (state.rafId) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }
    updatePlayheadUI();
    drawWaveform();
  }

  function startTickLoop() {
    if (state.rafId) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }

    const step = (now) => {
      state.rafId = null;
      if (!state.playing) return;
      const dt = Math.min(0.1, Math.max(0, (now - state.lastRafTs) / 1000));
      state.lastRafTs = now;

      if (state.virtualClockMode || (state.playbackView === 'sequence' && !getActiveVideo().src)) {
        state.globalTime += dt * state.playbackRate;
        const curPart = state.parts[state.activePartIdx];
        if (curPart) {
          const pStart = Number(curPart.verify_start) || 0;
          const pEnd = Number(curPart.verify_end) || pStart + (Number(curPart.duration) || 0);
          if (pEnd > pStart && state.globalTime >= pEnd) {
            if (state.loopClip) {
              state.globalTime = pStart;
              if (usingReplacementSongAudio() && dom.songAudio.src) {
                try { dom.songAudio.currentTime = Number(curPart.song_start) || 0; } catch (_) {}
              }
            } else if (state.activePartIdx + 1 < state.parts.length) {
              const nextIdx = state.activePartIdx + 1;
              const nextPart = state.parts[nextIdx];
              const nextStart = Number(nextPart?.verify_start) || pEnd;
              const localOff = Math.max(0, state.globalTime - nextStart);
              state.activePartIdx = nextIdx;
              if (state.playbackView === 'sequence') {
                const nextUrl = resolvePartUrl(nextPart);
                if (nextUrl && !nextPart.codecError) {
                  loadStageForPart(nextIdx, localOff, true);
                }
              }
            }
          }
        }
        if (state.totalDuration > 0 && state.globalTime >= state.totalDuration) {
          if (state.loopClip && state.parts[state.activePartIdx]) {
            const p = state.parts[state.activePartIdx];
            state.globalTime = Number(p.verify_start) || 0;
            if (usingReplacementSongAudio() && dom.songAudio.src) {
              try { dom.songAudio.currentTime = Number(p.song_start) || 0; } catch (_) {}
            }
          } else {
            state.globalTime = state.totalDuration;
            pauseAll();
            return;
          }
        }
      } else if (state.playbackView === 'verify') {
        const v = dom.videoA;
        if (v && !v.error) {
          state.globalTime = v.currentTime || 0;
        }
      } else {
        const v = getActiveVideo();
        const curPart = state.parts[state.activePartIdx];
        if (curPart && v && !v.error) {
          if (v.readyState >= 1 && !v.seeking && Number(v.dataset.partIdx) === state.activePartIdx) {
            const localTime = v.currentTime || 0;
            const partDur = Number(curPart.duration) || v.duration || 0;
            state.globalTime = (Number(curPart.verify_start) || 0) + localTime;

            // Transition to next clip cleanly or loop current clip
            if (partDur > 0 && localTime >= partDur - 0.03) {
              if (state.loopClip) {
                v.currentTime = 0;
                state.globalTime = Number(curPart.verify_start) || 0;
                if (usingReplacementSongAudio()) {
                  try { dom.songAudio.currentTime = Number(curPart.song_start) || 0; } catch (_) {}
                }
              } else if (state.activePartIdx + 1 < state.parts.length) {
                const nextIdx = state.activePartIdx + 1;
                const nextPart = state.parts[nextIdx];
                v.pause();
                state.globalTime = Number(nextPart?.verify_start) || state.globalTime;
                loadStageForPart(nextIdx, 0, true);
              } else {
                state.globalTime = state.totalDuration || ((Number(curPart.verify_start) || 0) + partDur);
                pauseAll();
                return;
              }
            }
          }
        }
      }

      if (state.lastAuditionedCutIdx !== null) {
        const audPart = state.parts[state.lastAuditionedCutIdx];
        if (!audPart || state.globalTime > (Number(audPart.verify_start) || 0) + 0.25) {
          state.lastAuditionedCutIdx = null;
        }
      }

      // Drift correction between video & replacement song (> 50 ms)
      if (usingReplacementSongAudio() && !dom.songAudio.paused && dom.songAudio.readyState >= 1) {
        const part = state.parts[state.activePartIdx];
        const expectedSong = part
          ? (Number(part.song_start) || 0) + Math.max(0, state.globalTime - (Number(part.verify_start) || 0))
          : state.globalTime;
        const drift = Math.abs((dom.songAudio.currentTime || 0) - expectedSong);
        if (drift > 0.05) {
          try {
            dom.songAudio.currentTime = expectedSong;
          } catch (_) {}
        }
      }

      updatePlayheadUI();
      drawWaveform();
      if (state.playing && !state.rafId) {
        state.rafId = requestAnimationFrame(step);
      }
    };

    state.rafId = requestAnimationFrame(step);
  }

  function handleVideoError(videoEl) {
    if (!videoEl.src) return;
    if (videoEl !== getActiveVideo()) {
      const rawIdx = videoEl.dataset.partIdx;
      const standbyIdx = rawIdx !== undefined && rawIdx !== '' ? Number(rawIdx) : NaN;
      const standbyPart = Number.isInteger(standbyIdx) ? state.parts[standbyIdx] : null;
      if (standbyPart && videoEl.dataset.loadedSrc === resolvePartUrl(standbyPart)) {
        standbyPart.codecError = true;
      }
      return;
    }
    dom.videoA.pause();
    dom.videoB.pause();
    dom.codecNotice.hidden = false;
    state.virtualClockMode = true;
    const part = state.parts[state.activePartIdx];
    if (part) part.codecError = true;
  }

  function handleVideoEnded(videoEl) {
    if (videoEl !== getActiveVideo()) return;
    if (state.virtualClockMode) return;
    if (state.playbackView === 'verify') {
      state.globalTime = state.totalDuration || videoEl.duration || state.globalTime;
      pauseAll();
      return;
    }
    if (state.loopClip) {
      seekGlobal(Number(state.parts[state.activePartIdx]?.verify_start) || 0, true);
      return;
    }
    if (state.activePartIdx + 1 < state.parts.length) {
      const nextIdx = state.activePartIdx + 1;
      state.globalTime = Number(state.parts[nextIdx]?.verify_start) || state.globalTime;
      loadStageForPart(nextIdx, 0, true);
    } else {
      state.globalTime = state.totalDuration || state.globalTime;
      pauseAll();
    }
  }

  // ---------------- File / Folder / Zip / Song / CSV Loaders ----------------
  function registerObjectUrl(blob) {
    const url = URL.createObjectURL(blob);
    state.objectUrls.push(url);
    return url;
  }

  function probeVideoMetadata(blob, objectUrl) {
    return new Promise((resolve) => {
      const v = document.createElement('video');
      v.preload = 'metadata';
      v.muted = true;
      let settled = false;
      const done = (res) => {
        if (settled) return;
        settled = true;
        v.removeAttribute('src');
        v.load();
        resolve(res);
      };
      const timer = setTimeout(() => done({ duration: 0, width: 0, height: 0, codecError: true }), 4000);
      v.addEventListener('loadedmetadata', () => {
        clearTimeout(timer);
        done({
          duration: Number.isFinite(v.duration) ? v.duration : 0,
          width: v.videoWidth || 0,
          height: v.videoHeight || 0,
          codecError: false,
        });
      }, { once: true });
      v.addEventListener('error', () => {
        clearTimeout(timer);
        done({ duration: 0, width: 0, height: 0, codecError: true });
      }, { once: true });
      v.src = objectUrl;
    });
  }

  function probeAudioDuration(objectUrl) {
    return new Promise((resolve) => {
      const a = document.createElement('audio');
      a.preload = 'metadata';
      let settled = false;
      const done = (dur) => {
        if (settled) return;
        settled = true;
        a.removeAttribute('src');
        a.load();
        resolve(dur);
      };
      const timer = setTimeout(() => done(0), 4000);
      a.addEventListener('loadedmetadata', () => {
        clearTimeout(timer);
        done(Number.isFinite(a.duration) ? a.duration : 0);
      }, { once: true });
      a.addEventListener('error', () => {
        clearTimeout(timer);
        done(0);
      }, { once: true });
      a.src = objectUrl;
    });
  }

  async function loadSongFromBlob(blob, name, opts) {
    const options = opts || {};
    const songBasename = basename(name);
    if (!blob || blob.size === 0) {
      throw new Error('Empty (0-byte) audio file: ' + (songBasename || 'audio'));
    }
    const url = registerObjectUrl(blob);
    let dur = await probeAudioDuration(url);
    let audioBuffer = null;
    let peaks = null;

    try {
      const arrayBuf = await blob.arrayBuffer();
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        const ctx = state.audioCtx || new AudioCtx();
        state.audioCtx = ctx;
        audioBuffer = await ctx.decodeAudioData(arrayBuf.slice(0));
        if (audioBuffer && audioBuffer.duration > 0) {
          dur = audioBuffer.duration;
          peaks = computeWaveformPeaks(audioBuffer, 480);
        }
      }
    } catch (_) {
      // Fallback safely when audio codec isn't decodable by Web Audio
    }

    const isUndecodable = !(dur > 0) && !audioBuffer;
    const hasExistingValidSong = Boolean(
      state.songMedia && (state.songMedia.duration > 0 || state.songMedia.audioBuffer)
    );
    if (isUndecodable && (hasExistingValidSong || options.rejectUndecodable)) {
      throw new Error('Unreadable or corrupt audio file: ' + (songBasename || 'audio'));
    }

    state.songMedia = {
      name: songBasename,
      blob,
      objectUrl: url,
      url: '',
      duration: dur,
      audioBuffer,
      peaks,
    };

    if (!options.keepStudioOverrideFalse) {
      state.hasBrowserSongOverride = true;
    }

    dom.songAudio.src = url;
    dom.songAudio.load();
    dom.activeSongDisplay.textContent =
      state.songMedia.name + (dur > 0 ? ' (' + fmtTime(dur) + ')' : ' (unreadable audio)');
    dom.waveformHint.hidden = Boolean(peaks);

    if (state.mode === 'standalone' || state.hasBrowserSongOverride) {
      recomputeStandalonePlan();
    }
    updateStepperAndButtons();
    renderTimeline();
    renderClipsList();
  }

  async function ingestCsvText(text, name) {
    const parsed = parseCsvRfc4180(text);
    Object.assign(state.csvById, parsed.byId);
    state.csvCount = Object.keys(state.csvById).length;
    dom.csvStatusText.textContent =
      'CSV loaded (' + state.csvCount + ' video stats from ' + basename(name) + ')';
    applyCsvEnrichment();
    renderTimeline();
    renderClipsList();
    updatePlayheadUI();
  }

  async function ingestManifestText(text) {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.parts)) {
      return false;
    }
    state.manifestRaw = parsed;
    if (parsed.fade_ms !== undefined && Number.isFinite(Number(parsed.fade_ms))) {
      state.fadeMs = Math.max(0, Number(parsed.fade_ms));
      dom.standaloneFadeMs.value = String(state.fadeMs);
    }
    if (parsed.playlist_url) state.playlistUrl = parsed.playlist_url;
    if (parsed.song_name && !state.songMedia) {
      dom.activeSongDisplay.textContent = parsed.song_name + ' (from manifest — load audio to play)';
    }
    state.isDemoSong = Boolean(parsed.is_demo_song);
    dom.demoBanner.hidden = !state.isDemoSong;
    return true;
  }

  async function ingestEntryList(entries) {
    // entries: [{ name, path, getBlob: async () => Blob, getText: async () => string }]
    dom.exportStatus.textContent = 'Reading ' + entries.length + ' file(s)...';

    const videoItems = [];
    const audioItems = [];
    const zipErrors = [];
    let loadedMetaInCall = false;

    for (let i = 0; i < entries.length; i++) {
      const entry = entries[i];
      const name = basename(entry.name || entry.path);
      const rel = String(entry.path || name);
      if (!name || name.startsWith('._') || rel.includes('__MACOSX/')) continue;
      const ext = getFileExt(name);

      if (ext === 'zip') {
        if (typeof JSZip !== 'function') {
          zipErrors.push(name + ': JSZip library not loaded');
          continue;
        }
        try {
          const zipBlob = await entry.getBlob();
          const zip = await JSZip.loadAsync(zipBlob);
          const subEntries = [];
          zip.forEach((zipPath, zipObj) => {
            if (zipObj.dir) return;
            const bName = basename(zipPath);
            if (!bName || bName.startsWith('._') || zipPath.includes('__MACOSX/')) return;
            subEntries.push({
              name: bName,
              path: zipPath,
              getBlob: () => zipObj.async('blob'),
              getText: () => zipObj.async('string'),
            });
          });
          const subRes = await ingestEntryList(subEntries);
          if (subRes && Array.isArray(subRes.zipErrors) && subRes.zipErrors.length) {
            zipErrors.push.apply(zipErrors, subRes.zipErrors);
          }
          if (subRes && subRes.loaded) {
            loadedMetaInCall = true;
          }
        } catch (err) {
          const msg = err && err.message ? err.message : 'invalid or corrupted archive';
          zipErrors.push(name + ': ' + msg);
        }
        continue;
      }

      if (name.toLowerCase() === 'manifest.json' || ext === 'json') {
        try {
          const txt = await entry.getText();
          if (await ingestManifestText(txt)) {
            loadedMetaInCall = true;
          }
        } catch (_) {}
        continue;
      }

      if (ext === 'csv') {
        try {
          const txt = await entry.getText();
          await ingestCsvText(txt, name);
          loadedMetaInCall = true;
        } catch (_) {}
        continue;
      }

      if (VIDEO_EXTS.has(ext)) {
        try {
          const blob = await entry.getBlob();
          videoItems.push({ name, path: rel, blob });
        } catch (err) {
          const msg = err && err.message ? err.message : 'unreadable video file';
          zipErrors.push(name + ': ' + msg);
        }
        continue;
      }

      if (AUDIO_EXTS.has(ext)) {
        try {
          const blob = await entry.getBlob();
          audioItems.push({ name, path: rel, blob });
        } catch (err) {
          const msg = err && err.message ? err.message : 'unreadable audio file';
          zipErrors.push(name + ': ' + msg);
        }
      }
    }

    // Process video items
    for (let i = 0; i < videoItems.length; i++) {
      const item = videoItems[i];
      const lowerName = item.name.toLowerCase();
      const lowerPath = item.path.toLowerCase();
      const url = registerObjectUrl(item.blob);

      if (lowerName.includes('verification') || lowerName.startsWith('3_verification')) {
        state.verifyMedia = { name: item.name, blob: item.blob, objectUrl: url };
      } else if (lowerPath.includes('2_repurposed_shorts') || /^part_\d+_/i.test(item.name)) {
        state.partMediaByName.set(item.name, { name: item.name, blob: item.blob, objectUrl: url });
      } else {
        const meta = await probeVideoMetadata(item.blob, url);
        const existingSamePathIdx = state.sourceClips.findIndex((c) => c.relPath === item.path);
        let clipFilename = item.name;
        if (existingSamePathIdx < 0) {
          const usedNames = new Set(state.sourceClips.map((c) => c.filename));
          if (usedNames.has(clipFilename)) {
            const pathSegs = String(item.path || '')
              .split(/[\\/]/)
              .filter(Boolean);
            const dirPrefix =
              pathSegs.length > 1
                ? pathSegs
                    .slice(0, -1)
                    .join('_')
                    .replace(/[^A-Za-z0-9._-]+/g, '_')
                : '';
            if (dirPrefix && !usedNames.has(dirPrefix + '__' + item.name)) {
              clipFilename = dirPrefix + '__' + item.name;
            } else {
              const dot = item.name.lastIndexOf('.');
              const stem = dot >= 0 ? item.name.slice(0, dot) : item.name;
              const extPart = dot >= 0 ? item.name.slice(dot) : '';
              let suffix = 2;
              while (usedNames.has(stem + '_' + suffix + extPart)) {
                suffix += 1;
              }
              clipFilename = stem + '_' + suffix + extPart;
            }
          }
        }
        const rec = {
          filename: clipFilename,
          source_filename: clipFilename,
          relPath: item.path,
          rawDuration: meta.duration,
          width: meta.width,
          height: meta.height,
          size_bytes: item.blob.size,
          codecError: meta.codecError,
          blob: item.blob,
          objectUrl: url,
        };
        if (existingSamePathIdx >= 0) state.sourceClips[existingSamePathIdx] = rec;
        else state.sourceClips.push(rec);
      }
    }

    // Process audio items (prefer non-sample song if multiple present; skip 0-byte/undecodable)
    if (audioItems.length > 0) {
      audioItems.sort((a, b) => {
        const aDemo = a.name.toLowerCase().includes('sample_song');
        const bDemo = b.name.toLowerCase().includes('sample_song');
        return aDemo === bDemo ? 0 : aDemo ? 1 : -1;
      });
      for (let i = 0; i < audioItems.length; i++) {
        const chosenAudio = audioItems[i];
        const matchesManifestSong = Boolean(
          state.manifestRaw &&
            state.partMediaByName.size > 0 &&
            basename(state.manifestRaw.song_name) === basename(chosenAudio.name)
        );
        try {
          await loadSongFromBlob(chosenAudio.blob, chosenAudio.name, {
            keepStudioOverrideFalse: matchesManifestSong,
            rejectUndecodable: i < audioItems.length - 1,
          });
          break;
        } catch (err) {
          zipErrors.push(err && err.message ? err.message : chosenAudio.name + ': invalid audio');
        }
      }
    }

    if (state.mode === 'standalone' || state.hasBrowserSongOverride || state.sourceClips.length > 0 || state.manifestRaw) {
      recomputeStandalonePlan();
    }

    if (state.verifyMedia && state.partMediaByName.size === 0 && state.sourceClips.length === 0) {
      state.playbackView = 'verify';
    } else if (state.verifyMedia && state.manifestRaw && !state.hasBrowserSongOverride) {
      state.playbackView = 'verify';
    } else {
      state.playbackView = 'sequence';
    }

    updateStepperAndButtons();
    renderTimeline();
    renderClipsList();

    if (state.parts.length > 0 || state.verifyMedia) {
      seekGlobal(0, false);
    }

    const loadedAny = loadedMetaInCall || videoItems.length > 0 || audioItems.length > 0;
    if (zipErrors.length > 0 && !loadedAny) {
      dom.exportStatus.textContent =
        'Could not unpack ZIP archive (' + zipErrors.join('; ') + '). Please check the file and try again.';
    } else if (zipErrors.length > 0) {
      dom.exportStatus.textContent =
        'Loaded ' + state.parts.length + ' clip(s) · Warning: could not read ' + zipErrors.join('; ');
    } else {
      dom.exportStatus.textContent =
        'Loaded ' + state.parts.length + ' clip(s)' +
        (state.songMedia ? ' · Song: ' + state.songMedia.name : '') +
        (state.csvCount ? ' · ' + state.csvCount + ' CSV stats matched' : '');
    }
    return { loaded: loadedAny, zipErrors };
  }

  function fileListToEntries(fileList) {
    const out = [];
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      out.push({
        name: f.name,
        path: f.webkitRelativePath || f.name,
        getBlob: async () => f,
        getText: () => f.text(),
      });
    }
    return out;
  }

  async function readDataTransferEntries(dataTransfer) {
    const items = dataTransfer.items;
    if (!items || !items.length || typeof items[0].webkitGetAsEntry !== 'function') {
      return fileListToEntries(dataTransfer.files || []);
    }

    const collected = [];
    async function walkEntry(entry, prefix) {
      if (!entry) return;
      if (entry.isFile) {
        await new Promise((resolve) => {
          entry.file(
            (file) => {
              collected.push({
                name: file.name,
                path: prefix ? prefix + '/' + file.name : file.name,
                getBlob: async () => file,
                getText: () => file.text(),
              });
              resolve();
            },
            () => resolve()
          );
        });
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const readBatch = () =>
          new Promise((resolve) => {
            reader.readEntries(async (batch) => {
              if (!batch || !batch.length) {
                resolve();
                return;
              }
              for (let j = 0; j < batch.length; j++) {
                await walkEntry(batch[j], prefix ? prefix + '/' + entry.name : entry.name);
              }
              await readBatch();
              resolve();
            }, () => resolve());
          });
        await readBatch();
      }
    }

    const topEntries = [];
    for (let i = 0; i < items.length; i++) {
      const entry = items[i].webkitGetAsEntry();
      if (entry) topEntries.push(entry);
    }
    if (topEntries.length === 0) {
      return fileListToEntries(dataTransfer.files || []);
    }
    for (let i = 0; i < topEntries.length; i++) {
      await walkEntry(topEntries[i], '');
    }
    return collected;
  }

  function clearStandaloneWorkspace() {
    pauseAll();
    state.objectUrls.forEach((u) => {
      try { URL.revokeObjectURL(u); } catch (_) {}
    });
    state.objectUrls = [];
    state.sourceClips = [];
    state.partMediaByName.clear();
    state.verifyMedia = null;
    state.songMedia = null;
    state.manifestRaw = null;
    state.parts = [];
    state.totalDuration = 0;
    state.globalTime = 0;
    state.lastAuditionedCutIdx = null;
    state.hasBrowserSongOverride = false;
    state.csvById = Object.create(null);
    state.csvCount = 0;

    [dom.videoA, dom.videoB].forEach((v) => {
      v.removeAttribute('src');
      delete v.dataset.loadedSrc;
      v.load();
    });
    dom.songAudio.removeAttribute('src');
    dom.songAudio.load();
    dom.stageEmpty.hidden = false;
    dom.codecNotice.hidden = true;
    dom.demoBanner.hidden = true;
    dom.activeSongDisplay.textContent = 'No song loaded';
    dom.csvStatusText.textContent = 'No CSV metadata loaded';
    dom.nowPlayingTitle.textContent = 'Waiting for media';
    dom.nowPlayingMeta.textContent = 'Load clips to inspect timing & transitions';
    dom.exportStatus.textContent = '';

    updateStepperAndButtons();
    renderTimeline();
    renderClipsList();
  }

  // ---------------- Downloads & Exports (F4) ----------------
  function triggerBlobDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  function downloadSinglePartMp4(part) {
    const url = resolvePartUrl(part);
    if (!url) return;
    if (state.mode === 'studio' && !part.objectUrl && !state.partMediaByName.has(part.filename)) {
      const dlUrl = url + (url.includes('?') ? '&' : '?') + 'download=1';
      const a = document.createElement('a');
      a.href = dlUrl;
      a.download = part.filename || 'part.mp4';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }
    const media = state.partMediaByName.get(part.filename) ||
      state.sourceClips.find((c) => c.filename === part.source_filename);
    if (media && media.blob) {
      triggerBlobDownload(media.blob, part.filename || media.name || 'clip.mp4');
    }
  }

  function downloadPartWav(part) {
    if (!state.songMedia || !state.songMedia.audioBuffer || !part) {
      dom.exportStatus.textContent = 'Load and decode a song first to slice WAV audio.';
      return;
    }
    const res = sliceAudioBufferToWav(
      state.songMedia.audioBuffer,
      Number(part.song_start) || 0,
      Number(part.song_end) || 0,
      state.fadeMs
    );
    const outName = 'Part_' + String(part.part_num).padStart(2, '0') + '_audio.wav';
    triggerBlobDownload(res.blob, outName);
    dom.exportStatus.textContent = 'Downloaded ' + outName + ' (' + res.duration.toFixed(2) + 's, ' + state.fadeMs + 'ms fade).';
  }

  function buildManifestJsonText() {
    const payload = buildManifestPayload({
      playlistUrl: state.playlistUrl || dom.playlistUrl.value || '',
      songName: state.songMedia ? state.songMedia.name : state.studio ? state.studio.songName : '',
      isDemoSong: state.isDemoSong,
      fadeMs: state.fadeMs,
      parts: state.parts,
    });
    return JSON.stringify(payload, null, 2);
  }

  async function downloadWavSlicesZip() {
    if (typeof JSZip !== 'function' || !state.songMedia || !state.songMedia.audioBuffer || !state.parts.length) {
      dom.exportStatus.textContent = 'Need both loaded clips and a decoded song to generate WAV slices.';
      return;
    }
    dom.dlWavZipBtn.disabled = true;
    dom.exportStatus.textContent = 'Rendering ' + state.parts.length + ' WAV slices (' + state.fadeMs + 'ms micro-fades)...';
    try {
      const zip = new JSZip();
      zip.file('manifest.json', buildManifestJsonText());
      const folder = zip.folder('audio_slices');
      for (let i = 0; i < state.parts.length; i++) {
        const p = state.parts[i];
        const wav = sliceAudioBufferToWav(
          state.songMedia.audioBuffer,
          Number(p.song_start) || 0,
          Number(p.song_end) || 0,
          state.fadeMs
        );
        const name = 'Part_' + String(p.part_num).padStart(2, '0') + '_audio.wav';
        folder.file(name, wav.bytes);
      }
      const zipBlob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
      triggerBlobDownload(zipBlob, 'seamless_shorts_wav_slices.zip');
      dom.exportStatus.textContent = 'Exported ' + state.parts.length + ' WAV slices + manifest.json into ZIP.';
    } catch (err) {
      dom.exportStatus.textContent = 'WAV ZIP error: ' + err.message;
    } finally {
      updateStepperAndButtons();
    }
  }

  async function downloadAllZip() {
    if (state.mode === 'studio' && !state.hasBrowserSongOverride && state.partMediaByName.size === 0 && state.sourceClips.length === 0) {
      window.location.href = 'download/all.zip';
      return;
    }
    if (typeof JSZip !== 'function') {
      dom.exportStatus.textContent = 'JSZip library not available.';
      return;
    }

    dom.dlAllZipBtn.disabled = true;
    dom.exportStatus.textContent = 'Building ZIP bundle in browser...';
    try {
      const zip = new JSZip();
      zip.file('manifest.json', buildManifestJsonText());

      if (state.songMedia && state.songMedia.audioBuffer && state.parts.length) {
        const audioDir = zip.folder('audio_slices');
        for (let i = 0; i < state.parts.length; i++) {
          const p = state.parts[i];
          const wav = sliceAudioBufferToWav(
            state.songMedia.audioBuffer,
            Number(p.song_start) || 0,
            Number(p.song_end) || 0,
            state.fadeMs
          );
          audioDir.file('Part_' + String(p.part_num).padStart(2, '0') + '_audio.wav', wav.bytes);
        }
      }

      if (state.verifyMedia && state.verifyMedia.blob) {
        zip.file(state.verifyMedia.name || '3_verification_full_playthrough.mp4', state.verifyMedia.blob);
      }

      const clipsDir = zip.folder('clips');
      for (let i = 0; i < state.parts.length; i++) {
        const p = state.parts[i];
        const partEntry = state.partMediaByName.get(p.filename) ||
          state.sourceClips.find((c) => c.filename === p.source_filename);
        if (partEntry && partEntry.blob) {
          clipsDir.file(p.filename || partEntry.filename || partEntry.name, partEntry.blob);
        }
      }

      const blob = await zip.generateAsync({ type: 'blob', compression: 'STORE' });
      triggerBlobDownload(blob, 'seamless_shorts_bundle.zip');
      dom.exportStatus.textContent = 'Downloaded seamless_shorts_bundle.zip (' + (blob.size / 1048576).toFixed(2) + ' MB).';
    } catch (err) {
      dom.exportStatus.textContent = 'ZIP export failed: ' + err.message;
    } finally {
      updateStepperAndButtons();
    }
  }

  // ---------------- Studio Backend Mode ----------------
  async function probeStudioMode() {
    const isHttp = window.location.protocol === 'http:' || window.location.protocol === 'https:';
    if (!isHttp) return false;

    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 1800) : null;
    try {
      const resp = await fetch('api/health', {
        cache: 'no-store',
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (!resp.ok) return false;
      const json = await resp.json();
      return Boolean(json && json.ok === true);
    } catch (_) {
      return false;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function activateStudioMode() {
    state.mode = 'studio';
    dom.modeBadge.textContent = 'Studio Mode';
    dom.modeBadge.dataset.mode = 'studio';
    dom.studioPanel.hidden = false;
    dom.dlToolkitBtn.hidden = false;

    refreshStudioState();
    state.pollTimerId = setInterval(() => refreshStudioState(), 1500);
  }

  async function loadStudioAudioTrack(songName) {
    if (!songName || state.studioAudioFetchedName === songName || state.hasBrowserSongOverride) return;
    state.studioAudioFetchedName = songName;
    try {
      const resp = await fetch('media/audio/' + encodeURIComponent(songName), { cache: 'no-store' });
      if (!resp.ok) return;
      const blob = await resp.blob();
      await loadSongFromBlob(blob, songName, { keepStudioOverrideFalse: true });
    } catch (_) {
      // Non-fatal if server doesn't expose /media/audio yet
    }
  }

  async function refreshStudioState(forceSelectLatest) {
    try {
      const resp = await fetch('api/state', { cache: 'no-store' });
      if (!resp.ok) return;
      const raw = await resp.json();
      const s = normalizeStudioState(raw);
      state.studio = s;

      // Tool pills
      const pills = [];
      if (s.tools.python) pills.push('<span class="tool-pill is-ok">Python ' + escapeHtml(s.tools.python) + '</span>');
      pills.push('<span class="tool-pill ' + (s.tools.ffmpeg ? 'is-ok' : 'is-warn') + '">FFmpeg: ' + (s.tools.ffmpeg ? 'Ready' : 'Missing') + '</span>');
      pills.push('<span class="tool-pill ' + (s.tools.ffprobe ? 'is-ok' : 'is-warn') + '">FFprobe: ' + (s.tools.ffprobe ? 'Ready' : 'Fallback') + '</span>');
      pills.push('<span class="tool-pill ' + (s.tools.yt_dlp ? 'is-ok' : 'is-warn') + '">yt-dlp: ' + (s.tools.yt_dlp ? 'Ready' : 'Auto') + '</span>');
      pills.push('<span class="tool-pill is-ok">Sources: ' + s.sourceCount + '</span>');
      dom.studioToolPills.innerHTML = pills.join('');

      if (state.studioFirstInit && !dom.playlistUrl.value) {
        dom.playlistUrl.value = s.playlistUrl;
        state.playlistUrl = s.playlistUrl;
      }

      // Song dropdown
      const prevChoice = dom.songSelect.value;
      dom.songSelect.innerHTML = '';
      s.audioFiles.forEach((f) => {
        const opt = document.createElement('option');
        opt.value = f.path;
        opt.dataset.name = f.name;
        opt.textContent = f.name + ' (' + ((f.size_bytes || 0) / 1048576).toFixed(1) + ' MB)';
        dom.songSelect.appendChild(opt);
      });
      const hasOption = (val) => Array.prototype.some.call(dom.songSelect.options, (o) => o.value === val);
      if (!state.studioFirstInit && !forceSelectLatest && prevChoice && hasOption(prevChoice)) {
        dom.songSelect.value = prevChoice;
      } else if (s.activeSongPath && hasOption(s.activeSongPath)) {
        dom.songSelect.value = s.activeSongPath;
      }
      if (dom.songSelect.selectedOptions[0]) {
        dom.studioSongStatus.textContent = 'Selected: ' + dom.songSelect.selectedOptions[0].textContent;
      }

      dom.skipDownload.disabled = s.sourceCount === 0;
      if (state.studioFirstInit) {
        dom.skipDownload.checked = s.sourceCount > 0;
        dom.fadeMs.value = String(Number.isFinite(Number(s.fadeMs)) ? Number(s.fadeMs) : 15);
      }

      state.isDemoSong = s.isDemoSong;
      dom.demoBanner.hidden = !s.isDemoSong;

      // Job progress & logs
      dom.runBtn.disabled = Boolean(s.job.running);
      dom.runBtn.textContent = s.job.running ? 'Processing... Please Wait' : 'Run & Sync Shorts';
      dom.stepLabel.textContent = s.job.error ? 'Error: ' + s.job.error : (s.job.step || 'Ready');
      const pct = Math.max(0, Math.min(100, Number(s.job.progress) || 0));
      dom.pctLabel.textContent = pct + '%';
      dom.progressBar.setAttribute('aria-valuenow', String(pct));
      dom.progressFill.style.width = pct + '%';

      const logsText = (s.job.logs || []).join('\n') || 'Ready.';
      if (dom.logBox.textContent !== logsText) {
        dom.logBox.textContent = logsText;
        dom.logBox.scrollTop = dom.logBox.scrollHeight;
      }

      const newVer = String(s.verifyMtime || '');
      const shouldReloadMedia =
        state.studioFirstInit ||
        (state.studioWasRunning && !s.job.running) ||
        (newVer && newVer !== state.studioVerifyVer);

      if (shouldReloadMedia && !state.hasBrowserSongOverride) {
        state.studioVerifyVer = newVer;
        state.lastAuditionedCutIdx = null;
        state.fadeMs = Number.isFinite(Number(s.fadeMs)) ? Math.max(0, Number(s.fadeMs)) : 15;
        state.parts = s.parts.map((p) => Object.assign({}, p));
        state.totalDuration = state.parts.reduce((acc, p) => acc + (Number(p.duration) || 0), 0);
        applyCsvEnrichment();

        if (s.verifyExists) {
          state.playbackView = 'verify';
        } else if (state.parts.length > 0) {
          state.playbackView = 'sequence';
        }
        updateStepperAndButtons();
        renderTimeline();
        renderClipsList();
        if (s.verifyExists || state.parts.length > 0) {
          loadStageForPart(0, 0, false);
        }
        if (s.songName && s.audioFiles.some((f) => f.name === s.songName)) {
          loadStudioAudioTrack(s.songName);
        }
      }

      state.studioWasRunning = Boolean(s.job.running);
      state.studioFirstInit = false;
    } catch (_) {
      // Ignore transient poll glitches
    }
  }

  async function uploadSongToStudio(file) {
    if (!file) return;
    dom.studioSongStatus.textContent = 'Uploading ' + file.name + '...';
    try {
      const resp = await fetch('api/upload-song', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Filename': encodeURIComponent(file.name),
        },
        body: file,
      });
      const res = await resp.json();
      if (!resp.ok || res.error) throw new Error(res.error || 'Upload failed');
      dom.studioSongStatus.textContent = 'Uploaded active song: ' + res.song_name;
      state.hasBrowserSongOverride = false;
      state.studioAudioFetchedName = '';
      await refreshStudioState(true);
    } catch (err) {
      dom.studioSongStatus.textContent = 'Upload error: ' + err.message;
    }
  }

  async function startStudioRun() {
    dom.runBtn.disabled = true;
    try {
      const payload = {
        playlist_url: dom.playlistUrl.value.trim(),
        song_path: dom.songSelect.value,
        max_downloads: parseInt(dom.maxDownloads.value || '10', 10),
        fade_ms: parseInt(dom.fadeMs.value || '15', 10),
        skip_download: dom.skipDownload.checked,
        browser_safe_h264: dom.browserSafe.checked,
        cookies_from_browser: dom.cookiesFromBrowser.value || '',
      };
      const resp = await fetch('api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        dom.stepLabel.textContent = 'Error: ' + (data.error || 'Could not start job');
        dom.runBtn.disabled = false;
      } else {
        state.hasBrowserSongOverride = false;
        refreshStudioState();
      }
    } catch (err) {
      dom.stepLabel.textContent = 'Error: ' + err.message;
      dom.runBtn.disabled = false;
    }
  }

  // ---------------- Event Wiring ----------------
  function bindEvents() {
    // Theme toggle
    const savedTheme = safeStorageGet('seamless_shorts_theme', 'dark');
    setTheme(savedTheme);
    dom.themeToggleBtn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme');
      setTheme(cur === 'light' ? 'dark' : 'light');
    });

    // Shortcuts popover
    dom.shortcutsBtn.addEventListener('click', () => {
      const open = dom.shortcutsPopover.hidden;
      dom.shortcutsPopover.hidden = !open;
      dom.shortcutsBtn.setAttribute('aria-expanded', String(open));
    });
    dom.closeShortcutsBtn.addEventListener('click', () => {
      dom.shortcutsPopover.hidden = true;
      dom.shortcutsBtn.setAttribute('aria-expanded', 'false');
    });

    // Playback mode switch
    dom.modeSequenceBtn.addEventListener('click', () => {
      if (!state.parts.length) return;
      pauseAll();
      state.playbackView = 'sequence';
      updateStepperAndButtons();
      seekGlobal(state.globalTime, false);
    });
    dom.modeVerifyBtn.addEventListener('click', () => {
      if (!resolveVerifyUrl()) return;
      pauseAll();
      state.playbackView = 'verify';
      updateStepperAndButtons();
      seekGlobal(state.globalTime, false);
    });

    // Transport buttons
    dom.playPauseBtn.addEventListener('click', () => {
      if (state.playing) pauseAll();
      else playAll();
    });
    dom.auditionCutBtn.addEventListener('click', () => {
      const targetIdx = state.activePartIdx > 0 ? state.activePartIdx : state.parts.length > 1 ? 1 : 0;
      auditionCutAt(targetIdx);
    });
    dom.prevCutBtn.addEventListener('click', () => {
      auditionCutAt(findNextCutIndex(-1));
    });
    dom.nextCutBtn.addEventListener('click', () => {
      auditionCutAt(findNextCutIndex(1));
    });
    dom.loopClipBtn.addEventListener('click', () => {
      state.loopClip = !state.loopClip;
      dom.loopClipBtn.setAttribute('aria-pressed', String(state.loopClip));
      dom.loopClipBtn.textContent = state.loopClip ? 'Loop: Current Clip' : 'Loop: Full Sequence';
    });
    dom.speedSelect.addEventListener('change', () => {
      state.playbackRate = parseFloat(dom.speedSelect.value) || 1;
      dom.videoA.playbackRate = state.playbackRate;
      dom.videoB.playbackRate = state.playbackRate;
      dom.songAudio.playbackRate = state.playbackRate;
    });
    dom.fullscreenBtn.addEventListener('click', () => {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      } else if (dom.playerStage.requestFullscreen) {
        dom.playerStage.requestFullscreen().catch(() => {});
      }
    });
    dom.dismissCodecNoticeBtn.addEventListener('click', () => {
      dom.codecNotice.hidden = true;
    });

    // Audio mixer controls
    dom.muteVideoBtn.addEventListener('click', () => {
      state.muteOriginal = !state.muteOriginal;
      applyAudioVolumes();
    });
    dom.videoVolSlider.addEventListener('input', () => {
      state.videoVol = Number(dom.videoVolSlider.value) / 100;
      if (state.videoVol > 0 && state.muteOriginal) state.muteOriginal = false;
      applyAudioVolumes();
    });
    dom.songVolSlider.addEventListener('input', () => {
      state.songVol = Number(dom.songVolSlider.value) / 100;
      applyAudioVolumes();
    });
    dom.quickSongBtn.addEventListener('click', () => dom.songInput.click());

    // Video lifecycle events
    [dom.videoA, dom.videoB].forEach((v) => {
      v.addEventListener('error', () => handleVideoError(v));
      v.addEventListener('ended', () => handleVideoEnded(v));
    });

    // Dropzone & pickers
    dom.pickFilesBtn.addEventListener('click', (e) => { e.stopPropagation(); dom.filesInput.click(); });
    dom.pickFolderBtn.addEventListener('click', (e) => { e.stopPropagation(); dom.folderInput.click(); });
    dom.pickZipBtn.addEventListener('click', (e) => { e.stopPropagation(); dom.zipInput.click(); });
    dom.pickSongBtn.addEventListener('click', (e) => { e.stopPropagation(); dom.songInput.click(); });
    dom.pickCsvBtn.addEventListener('click', (e) => { e.stopPropagation(); dom.csvInput.click(); });
    dom.dropzone.addEventListener('click', () => dom.filesInput.click());
    dom.dropzone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        dom.filesInput.click();
      }
    });

    const handleInputFiles = async (fileList) => {
      if (!fileList || !fileList.length) return;
      try {
        await ingestEntryList(fileListToEntries(fileList));
      } catch (err) {
        dom.exportStatus.textContent =
          'Could not load selected files: ' + (err && err.message ? err.message : 'unexpected error');
      }
    };

    dom.filesInput.addEventListener('change', (e) => handleInputFiles(e.target.files));
    dom.folderInput.addEventListener('change', (e) => handleInputFiles(e.target.files));
    dom.zipInput.addEventListener('change', (e) => handleInputFiles(e.target.files));
    dom.csvInput.addEventListener('change', (e) => handleInputFiles(e.target.files));
    dom.songInput.addEventListener('change', async (e) => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      try {
        await loadSongFromBlob(f, f.name, { keepStudioOverrideFalse: false });
        dom.exportStatus.textContent = 'Song loaded: ' + f.name + ' — plan offsets updated automatically.';
      } catch (err) {
        dom.exportStatus.textContent =
          'Could not load song: ' + (err && err.message ? err.message : 'unexpected error');
      }
    });

    dom.dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.dropzone.classList.add('is-dragover');
    });
    dom.dropzone.addEventListener('dragleave', () => dom.dropzone.classList.remove('is-dragover'));
    dom.dropzone.addEventListener('drop', async (e) => {
      e.preventDefault();
      dom.dropzone.classList.remove('is-dragover');
      if (!e.dataTransfer) return;
      try {
        const entries = await readDataTransferEntries(e.dataTransfer);
        if (entries.length) await ingestEntryList(entries);
      } catch (err) {
        dom.exportStatus.textContent =
          'Could not read dropped files: ' + (err && err.message ? err.message : 'unexpected error');
      }
    });

    dom.standaloneFadeMs.addEventListener('change', () => {
      state.fadeMs = Math.max(0, Number(dom.standaloneFadeMs.value) || 0);
      if (state.mode === 'standalone' || state.hasBrowserSongOverride) {
        recomputeStandalonePlan();
        renderTimeline();
        renderClipsList();
      }
    });

    dom.clearWorkspaceBtn.addEventListener('click', clearStandaloneWorkspace);

    // Interactive timeline scrubbing & hover tooltip
    let draggingTimeline = false;
    let resumeAfterScrub = false;
    let lastScrubTime = 0;
    function timeFromPointerEvent(e) {
      const rect = dom.playheadOverlay.getBoundingClientRect();
      if (!rect.width || state.totalDuration <= 0) return 0;
      const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      return frac * state.totalDuration;
    }

    dom.timelineShell.addEventListener('pointerdown', (e) => {
      if (e.target.closest('.cut-marker') || e.target.closest('.clip-block')) return;
      if (state.totalDuration <= 0) return;
      draggingTimeline = true;
      resumeAfterScrub = state.playing;
      if (resumeAfterScrub) {
        pauseAll();
      }
      if (dom.timelineShell.setPointerCapture && e.pointerId !== undefined) {
        try {
          dom.timelineShell.setPointerCapture(e.pointerId);
        } catch (_) {}
      }
      const t = timeFromPointerEvent(e);
      lastScrubTime = t;
      seekGlobal(t, false);
    });
    dom.timelineShell.addEventListener('pointermove', (e) => {
      if (state.totalDuration <= 0) return;
      const rect = dom.playheadOverlay.getBoundingClientRect();
      const frac = rect.width ? Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) : 0;
      const t = frac * state.totalDuration;
      dom.playheadTooltip.hidden = false;
      dom.playheadTooltip.style.left = (frac * 100) + '%';
      dom.playheadTooltip.textContent = fmtPrecise(t);
      if (draggingTimeline) {
        lastScrubTime = t;
        seekGlobal(t, false);
      }
    });
    const stopTimelineDrag = (e) => {
      if (!draggingTimeline) return;
      draggingTimeline = false;
      const shouldResume = resumeAfterScrub;
      resumeAfterScrub = false;
      if (e && dom.timelineShell.releasePointerCapture && e.pointerId !== undefined) {
        try {
          if (!dom.timelineShell.hasPointerCapture || dom.timelineShell.hasPointerCapture(e.pointerId)) {
            dom.timelineShell.releasePointerCapture(e.pointerId);
          }
        } catch (_) {}
      }
      const hasPointerCoords =
        e &&
        e.type === 'pointerup' &&
        Number.isFinite(e.clientX) &&
        (e.clientX !== 0 || e.clientY !== 0);
      const finalT = hasPointerCoords ? timeFromPointerEvent(e) : lastScrubTime;
      if (shouldResume || Math.abs(finalT - state.globalTime) > 1e-4) {
        seekGlobal(finalT, shouldResume);
      }
    };
    dom.timelineShell.addEventListener('pointerup', stopTimelineDrag);
    dom.timelineShell.addEventListener('pointercancel', stopTimelineDrag);
    dom.timelineShell.addEventListener('pointerleave', () => {
      if (!draggingTimeline) dom.playheadTooltip.hidden = true;
    });

    // Studio panel handlers
    dom.studioUploadSongBtn.addEventListener('click', () => dom.studioSongFileInput.click());
    dom.studioSongFileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) uploadSongToStudio(e.target.files[0]);
    });
    if (dom.studioSongDrop) {
      dom.studioSongDrop.addEventListener('dragover', (e) => {
        e.preventDefault();
        dom.studioSongDrop.classList.add('is-dragover');
      });
      dom.studioSongDrop.addEventListener('dragleave', () => {
        dom.studioSongDrop.classList.remove('is-dragover');
      });
      dom.studioSongDrop.addEventListener('drop', (e) => {
        e.preventDefault();
        dom.studioSongDrop.classList.remove('is-dragover');
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
          uploadSongToStudio(e.dataTransfer.files[0]);
        }
      });
    }
    dom.runBtn.addEventListener('click', startStudioRun);

    // Download center handlers
    dom.dlVerifyBtn.addEventListener('click', () => {
      if (state.verifyMedia && state.verifyMedia.blob) {
        triggerBlobDownload(state.verifyMedia.blob, state.verifyMedia.name || '3_verification_full_playthrough.mp4');
      } else if (state.mode === 'studio') {
        window.location.href = 'media/verify?download=1';
      }
    });
    dom.dlManifestBtn.addEventListener('click', () => {
      if (!state.parts.length) return;
      const blob = new Blob([buildManifestJsonText()], { type: 'application/json' });
      triggerBlobDownload(blob, 'manifest.json');
      dom.exportStatus.textContent = 'Exported manifest.json (' + state.parts.length + ' parts).';
    });
    dom.dlWavZipBtn.addEventListener('click', downloadWavSlicesZip);
    dom.dlAllZipBtn.addEventListener('click', downloadAllZip);

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      const tag = e.target && e.target.tagName ? e.target.tagName.toUpperCase() : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable) {
        return;
      }
      if (e.code === 'Space' || e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (!dom.playPauseBtn.disabled) {
          if (state.playing) pauseAll();
          else playAll();
        }
      } else if (e.key.toLowerCase() === 'j') {
        e.preventDefault();
        seekGlobal(state.globalTime - 5, state.playing);
      } else if (e.key.toLowerCase() === 'l') {
        e.preventDefault();
        seekGlobal(state.globalTime + 5, state.playing);
      } else if (e.key === '[') {
        e.preventDefault();
        if (state.parts.length) auditionCutAt(findNextCutIndex(-1));
      } else if (e.key === ']') {
        e.preventDefault();
        if (state.parts.length) auditionCutAt(findNextCutIndex(1));
      } else if (e.key.toLowerCase() === 'm') {
        e.preventDefault();
        state.muteOriginal = !state.muteOriginal;
        applyAudioVolumes();
      }
    });

    window.addEventListener('resize', drawWaveform);
  }

  // Expose debug/test bridge safely on window
  globalScope.SeamlessApp = {
    getState: () => state,
    ingestManifestAndCsv: async (manifestObj, csvText) => {
      if (manifestObj) await ingestManifestText(typeof manifestObj === 'string' ? manifestObj : JSON.stringify(manifestObj));
      if (csvText) await ingestCsvText(csvText, 'flowjuggle.csv');
      recomputeStandalonePlan();
      updateStepperAndButtons();
      renderTimeline();
      renderClipsList();
      if (state.parts.length) seekGlobal(0, false);
    },
    setTheme,
  };

  async function init() {
    bindEvents();
    applyAudioVolumes();
    updateStepperAndButtons();
    renderTimeline();
    renderClipsList();

    const studioReady = await probeStudioMode();
    if (studioReady) {
      activateStudioMode();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
