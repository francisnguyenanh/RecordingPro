/* ScreenCapturePro v2 — app.js
   WebSocket client + UI: Modal, Toast, Display Selector, dB Meter
*/
"use strict";

const socket = io();

// ── State ────────────────────────────────────────────────────────────
const state = {
  appState: "idle",           // 'idle' | 'recording' | 'processing'
  selectedDisplay: 1,
  selectedDisplayName: "Display 1",
  autoDetect: false,
  modalOpen: false,
  toastTimer: null,
  timerInterval: null,
  seconds: 0,
  captureMode: "display",     // "display" | "window"
  selectedWindow: null,       // {title, left, top, width, height}
};

// ── Elements ─────────────────────────────────────────────────────────
const recBtn        = document.getElementById("rec-button");
const recLabel      = document.getElementById("rec-label");
const recIcon       = recBtn.querySelector(".rec-icon");
const timerEl       = document.getElementById("timer");
// status-dot and label now inline (header still keeps them for compat)
const statusDot     = document.getElementById("status-dot") || document.createElement('span');
const statusLabel   = document.getElementById("status-label") || document.createElement('span');
const statusDotInline  = document.getElementById("status-dot-inline");
const statusLabelInline = document.getElementById("status-label-inline");
const micBar        = document.getElementById("mic-bar");
const spkBar        = document.getElementById("spk-bar");
const micDb         = document.getElementById("mic-db");
const spkDb         = document.getElementById("spk-db");
const jobSection    = document.getElementById("job-progress");
const displayGrid   = document.getElementById("display-grid");
const recDispLabel  = document.getElementById("recording-display-label");
const detectorStatus = document.getElementById("detector-status");
const autoDetectToggle = document.getElementById("auto-detect-toggle");

function _setStatusUI(newState) {
  const cls = newState === 'recording' ? 'recording' : newState === 'processing' ? 'processing' : '';
  [statusDot, statusDotInline].forEach(el => { if (el) el.className = `status-dot ${newState} w-2.5 h-2.5 rounded-full bg-[#444] inline-block transition-colors`; });
  if (statusDotInline) { statusDotInline.className = `w-2 h-2 rounded-full bg-[#444] inline-block ${cls}`; }
  const labels = { idle: 'SẴN SÀNG', recording: 'ĐANG GHI', processing: 'ĐANG XỬ LÝ' };
  const lbl = labels[newState] || newState.toUpperCase();
  if (statusLabel) statusLabel.textContent = lbl;
  if (statusLabelInline) statusLabelInline.textContent = lbl;
}

// ══════════════════════════════════════════════════════════════════════
// TIMER
// ══════════════════════════════════════════════════════════════════════
function padZ(n) { return String(n).padStart(2, "0"); }
function formatTime(s) {
  return `${padZ(Math.floor(s / 3600))}:${padZ(Math.floor((s % 3600) / 60))}:${padZ(s % 60)}`;
}
function startTimer(from = 0) {
  stopTimer();
  state.seconds = from;
  timerEl.textContent = formatTime(state.seconds);
  state.timerInterval = setInterval(() => {
    state.seconds++;
    timerEl.textContent = formatTime(state.seconds);
  }, 1000);
}
function stopTimer() {
  if (state.timerInterval) { clearInterval(state.timerInterval); state.timerInterval = null; }
}

// ══════════════════════════════════════════════════════════════════════
// METHOD 1: TAB TITLE — hiển thị thời gian ghi trên tab trình duyệt
// ══════════════════════════════════════════════════════════════════════
const BASE_TITLE = "Tomo Recording";
let titleUpdateInterval = null;

function startTitleUpdater() {
  if (titleUpdateInterval) clearInterval(titleUpdateInterval);
  titleUpdateInterval = setInterval(() => {
    document.title = `🔴 ${formatTime(state.seconds)} • ${BASE_TITLE}`;
  }, 1000);
}

function stopTitleUpdater() {
  if (titleUpdateInterval) { clearInterval(titleUpdateInterval); titleUpdateInterval = null; }
  document.title = BASE_TITLE;
}

// ══════════════════════════════════════════════════════════════════════
// METHOD 3: SILENCE DETECTION — cảnh báo khi không có âm thanh 3 phút
// NOTE: level_update gửi giá trị 0.0–1.0 (float), không phải dB
// ══════════════════════════════════════════════════════════════════════
const SILENCE_CFG = {
  THRESHOLD: 0.001,          // dưới ngưỡng này coi là im lặng (~–60 dB)
  WARNING_AFTER_S: 180,      // cảnh báo sau 3 phút im lặng liên tục
  TOAST_DURATION_MS: 15000,  // toast tự đóng sau 15 giây
};

let _lastMicLevel = 0;
let _lastSpkLevel = 0;
let _silenceStartMs = 0;        // P1: timestamp-based instead of tick
let _silenceInterval = null;
let _silenceToastTimer = null;
let _silenceNotified = false;

function startSilenceMonitoring() {
  _silenceNotified = false;
  _lastMicLevel = 0;
  _lastSpkLevel = 0;
  _silenceStartMs = Date.now();
  if (_silenceInterval) clearInterval(_silenceInterval);
  _silenceInterval = setInterval(() => {
    const isSilent = _lastMicLevel < SILENCE_CFG.THRESHOLD &&
                     _lastSpkLevel < SILENCE_CFG.THRESHOLD;
    if (isSilent) {
      // P1: Use real elapsed time (immune to tab throttling)
      const elapsedS = (Date.now() - _silenceStartMs) / 1000;
      if (elapsedS >= SILENCE_CFG.WARNING_AFTER_S && !_silenceNotified) {
        _silenceNotified = true;
        showSilenceToast();
      }
    } else {
      _silenceStartMs = Date.now();
      _silenceNotified = false;
      dismissSilenceToast();
    }
  }, 1000);
}

function stopSilenceMonitoring() {
  if (_silenceInterval) { clearInterval(_silenceInterval); _silenceInterval = null; }
  _silenceStartMs = 0;
  _silenceNotified = false;
  dismissSilenceToast();
}

function showSilenceToast() {
  const mins = Math.floor(SILENCE_CFG.WARNING_AFTER_S / 60);
  document.getElementById("silence-duration").textContent = `${mins} phút`;
  const toast = document.getElementById("silence-check-toast");
  toast.classList.remove("hidden");
  // animate timer bar
  const bar = document.getElementById("silence-timer-bar");
  bar.style.transition = "none";
  bar.style.width = "100%";
  requestAnimationFrame(() => {
    bar.style.transition = `width ${SILENCE_CFG.TOAST_DURATION_MS / 1000}s linear`;
    bar.style.width = "0%";
  });
  _silenceToastTimer = setTimeout(() => {
    dismissSilenceToast();
    stopRecording();
  }, SILENCE_CFG.TOAST_DURATION_MS);
}

function dismissSilenceToast() {
  if (_silenceToastTimer) { clearTimeout(_silenceToastTimer); _silenceToastTimer = null; }
  const toast = document.getElementById("silence-check-toast");
  if (toast) toast.classList.add("hidden");
}

// ══════════════════════════════════════════════════════════════════════
// UI STATE
// ══════════════════════════════════════════════════════════════════════
function applyState(newState, durationSeconds = 0) {
  state.appState = newState;
  _setStatusUI(newState);

  const isBusy = newState !== "idle";
  document.querySelectorAll('input[name="record-mode"]').forEach(r => r.disabled = isBusy);
  document.getElementById("opt-mic-gain").disabled = isBusy;
  document.getElementById("opt-spk-gain").disabled = isBusy;
  document.querySelectorAll('.record-mode-group, #opt-mic-gain, #opt-spk-gain').forEach(el => {
    el.style.opacity = isBusy ? '0.5' : '1';
    el.style.pointerEvents = isBusy ? 'none' : 'auto';
  });

  if (newState === "recording") {
    recBtn.classList.add("recording");
    recBtn.classList.remove("processing");
    recBtn.disabled = false;
    recLabel.textContent = "DỪNG";
    recIcon.textContent = "■";
    startTimer(durationSeconds);
    micBar.classList.add("breathing");
    spkBar.classList.add("breathing");
    recDispLabel.classList.remove("hidden");
    if (state.captureMode === "window" && state.selectedWindow) {
      recDispLabel.textContent = `● Đang ghi: ${state.selectedWindow.title}`;
    } else {
      recDispLabel.textContent = `● Đang ghi: ${state.selectedDisplayName}`;
    }
  } else if (newState === "processing") {
    recBtn.classList.remove("recording");
    recBtn.classList.add("processing");
    recBtn.disabled = true;
    recLabel.textContent = "XỬ LÝ";
    recIcon.textContent = "⟳";
    stopTimer();
    micBar.classList.remove("breathing");
    spkBar.classList.remove("breathing");
    recDispLabel.classList.add("hidden");
  } else {
    recBtn.classList.remove("recording", "processing");
    recBtn.disabled = false;
    recLabel.textContent = "GHI";
    recIcon.textContent = "●";
    stopTimer();
    timerEl.textContent = "00:00:00";
    micBar.classList.remove("breathing");
    spkBar.classList.remove("breathing");
    updateMeter("mic-bar", "mic-db", 0);
    updateMeter("spk-bar", "spk-db", 0);
    recDispLabel.classList.add("hidden");
  }
}

// ══════════════════════════════════════════════════════════════════════
// AUDIO METERS (dB display)
// ══════════════════════════════════════════════════════════════════════
function updateMeter(barId, dbId, level) {
  const bar = document.getElementById(barId);
  const dbEl = document.getElementById(dbId);
  bar.style.width = (level * 100) + "%";
  
  if (level >= 0.85) {
    bar.classList.add("red");
    bar.classList.remove("yellow", "bg-green");
  } else if (level >= 0.6) {
    bar.classList.add("yellow");
    bar.classList.remove("red", "bg-green");
  } else {
    bar.classList.add("bg-green");
    bar.classList.remove("red", "yellow");
  }

  const db = level < 0.001 ? "–∞" : (20 * Math.log10(level)).toFixed(1) + " dB";
  dbEl.textContent = db;
}

// ══════════════════════════════════════════════════════════════════════
// RECORD MODE EXTRACTOR
// ══════════════════════════════════════════════════════════════════════
function getRecordMode() {
  const val = document.querySelector('input[name="record-mode"]:checked')?.value ?? "mp4_mp3";
  if (val === "mp3_only")  return { merge_audio: false, convert_mp3: true  };
  if (val === "mp4_only")  return { merge_audio: true,  convert_mp3: false };
  return                          { merge_audio: true,  convert_mp3: true  }; // mp4_mp3
}

// ══════════════════════════════════════════════════════════════════════
// REC BUTTON
// ══════════════════════════════════════════════════════════════════════
recBtn.addEventListener("click", async () => {
  if (state.appState === "idle") await startRecording();
  else if (state.appState === "recording") await stopRecording();
});

async function startRecording() {
  startTitleUpdater();
  startSilenceMonitoring();
  try {
    const body = {
      display_index: state.selectedDisplay,
      ...getRecordMode()
    };
    // Nếu đang ở chế độ ghi cửa sổ và đã chọn cửa sổ
    if (state.captureMode === "window" && state.selectedWindow) {
      body.window_region = {
        left:   state.selectedWindow.left,
        top:    state.selectedWindow.top,
        width:  state.selectedWindow.width,
        height: state.selectedWindow.height,
        title:  state.selectedWindow.title || "",
        hwnd:   state.selectedWindow.hwnd || null,
      };
    }
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) alert("Lỗi bắt đầu ghi: " + (data.error || res.statusText));
  } catch (err) {
    alert("Không thể kết nối server: " + err.message);
  }
}

async function stopRecording() {
  stopTitleUpdater();
  stopSilenceMonitoring();
  try {
    const body = {
      ...getRecordMode(),
      mic_gain: parseInt(document.getElementById("opt-mic-gain").value) || 1,
      speaker_gain: parseInt(document.getElementById("opt-spk-gain").value) || 1,
    };
    const res = await fetch("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) alert("Lỗi dừng ghi: " + (data.error || res.statusText));
  } catch (err) {
    alert("Không thể kết nối server: " + err.message);
  }
}

// ══════════════════════════════════════════════════════════════════════
// MODAL: Phát hiện cuộc gọi
// ══════════════════════════════════════════════════════════════════════
function showModal(appName) {
  if (state.modalOpen) return;
  state.modalOpen = true;
  const icons = { "Zoom": "🎥", "Microsoft Teams": "💼", "Google Meet": "📹" };
  document.getElementById("modal-app-icon").textContent = icons[appName] || "📡";
  document.getElementById("modal-app-name").textContent = appName;
  document.getElementById("modal-display-name").textContent =
    `${state.selectedDisplayName} (${state.selectedDisplay})`;
  document.getElementById("call-modal-overlay").classList.remove("hidden");
  document.getElementById("modal-confirm-record").focus();
}

function dismissModal() {
  state.modalOpen = false;
  document.getElementById("call-modal-overlay").classList.add("hidden");
  socket.emit("dismiss_call_popup");
}

function handleOverlayClick(e) {
  if (e.target === document.getElementById("call-modal-overlay")) dismissModal();
}

function scrollToDisplaySelector() {
  document.getElementById("display-selector").scrollIntoView({ behavior: "smooth" });
}

document.getElementById("modal-confirm-record").addEventListener("click", () => {
  state.modalOpen = false;
  document.getElementById("call-modal-overlay").classList.add("hidden");
  socket.emit("confirm_record");
});
document.getElementById("modal-dismiss").addEventListener("click", dismissModal);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.modalOpen) dismissModal(); });

// ══════════════════════════════════════════════════════════════════════
// TOAST: Cuộc gọi kết thúc
// ══════════════════════════════════════════════════════════════════════
function showEndedToast(appName) {
  document.getElementById("toast-app-name").textContent = appName;
  const toast = document.getElementById("call-ended-toast");
  toast.classList.remove("hidden");
  const bar = document.getElementById("toast-timer-bar");
  bar.style.transition = "none";
  bar.style.width = "100%";
  requestAnimationFrame(() => {
    bar.style.transition = "width 10s linear";
    bar.style.width = "0%";
  });
  state.toastTimer = setTimeout(() => { hideToast(); stopRecording(); }, 10000);
}

function hideToast() {
  clearTimeout(state.toastTimer);
  document.getElementById("call-ended-toast").classList.add("hidden");
}

document.getElementById("toast-stop").addEventListener("click", () => { hideToast(); stopRecording(); });
document.getElementById("toast-continue").addEventListener("click", hideToast);

document.getElementById("silence-stop").addEventListener("click", () => { dismissSilenceToast(); stopRecording(); });
document.getElementById("silence-continue").addEventListener("click", () => {
  dismissSilenceToast();
  // reset silence counter so it can warn again after another 3 minutes
  _silenceStartMs = Date.now();
  _silenceNotified = false;
});

// ══════════════════════════════════════════════════════════════════════
// SOURCE TABS + DISPLAY SELECTOR
// ══════════════════════════════════════════════════════════════════════
const srcTabDisplay   = document.getElementById("src-tab-display");
const srcTabWindow    = document.getElementById("src-tab-window");
const srcPanelDisplay = document.getElementById("src-panel-display");
const srcPanelWindow  = document.getElementById("src-panel-window");

function switchSourceTab(mode) {
  state.captureMode = mode;
  const isWin = mode === "window";
  if (srcTabDisplay) srcTabDisplay.classList.toggle("active", !isWin);
  if (srcTabWindow)  srcTabWindow.classList.toggle("active",  isWin);
  if (srcPanelDisplay) srcPanelDisplay.classList.toggle("hidden", isWin);
  if (srcPanelWindow)  srcPanelWindow.classList.toggle("hidden", !isWin);
  if (isWin) loadWindows();
  else clearWindowPreview();
}

if (srcTabDisplay) srcTabDisplay.addEventListener("click", () => switchSourceTab("display"));
if (srcTabWindow)  srcTabWindow.addEventListener("click",  () => switchSourceTab("window"));

async function loadDisplays() {
  displayGrid.innerHTML = '<div style="color:#666;font-size:11px;font-style:italic;padding:6px 0;">Đang tải…</div>';
  try {
    const res = await fetch("/api/displays?preview=true");
    const displays = await res.json();
    renderDisplayCards(displays);
  } catch (err) {
    displayGrid.innerHTML = '<div style="color:#666;font-size:11px;">Không thể tải màn hình.</div>';
  }
}

function renderDisplayCards(displays) {
  displayGrid.innerHTML = "";
  if (!displays || displays.length === 0) {
    displayGrid.innerHTML = '<div style="color:#666;font-size:11px;font-style:italic;">Không tìm thấy màn hình.</div>';
    return;
  }
  for (const d of displays) {
    const card = document.createElement("div");
    card.className = "display-card" + (d.index === state.selectedDisplay ? " active" : "");
    card.dataset.index = d.index;

    const imgSrc = d.preview_b64
      ? `data:image/jpeg;base64,${d.preview_b64}`
      : "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

    card.innerHTML = `
      <img class="display-preview-thumb" src="${imgSrc}" alt="${escHtml(d.name)}" loading="lazy" />
      <div class="display-info">
        <div class="display-label">${escHtml(d.name)}${d.is_primary ? ' <span style="font-size:9px;color:#555;">[Primary]</span>' : ''}</div>
        <div class="display-res">${d.width}&thinsp;×&thinsp;${d.height}</div>
      </div>
    `;
    card.addEventListener("click", () => setSelectedDisplay(d.index, d.name));
    displayGrid.appendChild(card);
  }
}

function setSelectedDisplay(index, name) {
  state.selectedDisplay = index;
  state.selectedDisplayName = name || `Display ${index}`;
  document.querySelectorAll(".display-card").forEach(c => {
    c.classList.toggle("active", parseInt(c.dataset.index) === index);
  });

  if (state.appState === "recording") {
    fetch("/api/switch-display", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_index: index }),
    }).then(res => res.json()).then(data => {
      if (data.ok) recDispLabel.textContent = `● Đang ghi: ${state.selectedDisplayName}`;
    }).catch(err => console.error("Lỗi chuyển màn hình:", err));
  } else {
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_display_index: index }),
    }).catch(() => {});
  }
}

async function refreshPreviews() {
  const btn = document.getElementById("refresh-previews");
  if (btn) { btn.textContent = "↺ Đang tải…"; btn.disabled = true; }
  try {
    const res = await fetch("/api/displays/preview");
    const previews = await res.json();
    for (const p of previews) {
      const card = displayGrid.querySelector(`[data-index="${p.index}"]`);
      if (card && p.preview_b64) {
        const img = card.querySelector(".display-preview-thumb");
        if (img) img.src = `data:image/jpeg;base64,${p.preview_b64}`;
      }
    }
  } catch (err) {
    console.error("Lỗi làm mới xem trước:", err);
  } finally {
    if (btn) { btn.textContent = "↺ Làm mới"; btn.disabled = false; }
  }
}

const _rp = document.getElementById("refresh-previews");
if (_rp) _rp.addEventListener("click", refreshPreviews);

// ══════════════════════════════════════════════════════════════════════
// AUTO-DETECT TOGGLE
// ══════════════════════════════════════════════════════════════════════
autoDetectToggle.addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_detect_calls: enabled }),
    });
    updateDetectorStatus(enabled);
  } catch (err) {
    console.error("Lỗi cập nhật config:", err);
  }
});

function updateDetectorStatus(active) {
  if (active) {
    detectorStatus.textContent = "🔍 Giám sát: Bật";
    detectorStatus.classList.add("active");
  } else {
    detectorStatus.textContent = "🔍 Giám sát: Tắt";
    detectorStatus.classList.remove("active");
  }
}

// ══════════════════════════════════════════════════════════════════════
// SOCKETIO LISTENERS
// ══════════════════════════════════════════════════════════════════════
socket.on("status_update", ({ state: s, duration_seconds }) => {
  if (s === "recording" && state.appState === "recording") {
    // Chỉ đồng bộ giá trị giây, không restart interval để tránh giật
    state.seconds = duration_seconds || 0;
    timerEl.textContent = formatTime(state.seconds);
  } else {
    applyState(s, duration_seconds || 0);
  }
});

socket.on("level_update", ({ mic, speaker }) => {
  if (state.appState !== "recording") return;
  _lastMicLevel = mic || 0;
  _lastSpkLevel = speaker || 0;
  updateMeter("mic-bar", "mic-db", _lastMicLevel);
  updateMeter("spk-bar", "spk-db", _lastSpkLevel);
});

socket.on("job_progress", ({ stage, message, log_type, current_step, total_steps }) => {
  const logContainer = document.getElementById("log-container");
  const stepIndicator = document.getElementById("step-indicator");
  jobSection.classList.remove("hidden");
  
  // Cập nhật step indicator
  if (current_step && total_steps) {
    stepIndicator.textContent = `Bước ${current_step} / ${total_steps}`;
  }
  
  // Thêm log entry
  const entry = document.createElement("div");
  entry.className = `log-entry log-${log_type || 'info'}`;
  const timestamp = new Date().toLocaleTimeString("vi-VN");
  entry.textContent = `[${timestamp}] ${message || stage}`;
  logContainer.appendChild(entry);
  logContainer.scrollTop = logContainer.scrollHeight;
  
  if (stage === "done" || stage === "error") {
    setTimeout(() => {
      jobSection.classList.add("hidden");
      logContainer.innerHTML = '<div class="log-entry">⏳ Bắt đầu xử lý...</div>';
      stepIndicator.textContent = 'Bước — / —';
    }, 3000);
  }
});

socket.on("files_updated", () => loadFiles());

socket.on("call_detected", ({ app_name }) => showModal(app_name));
socket.on("call_popup_dismissed", () => {
  state.modalOpen = false;
  document.getElementById("call-modal-overlay").classList.add("hidden");
});
socket.on("call_ended", ({ app_name }) => {
  if (state.modalOpen) dismissModal();
  if (state.appState === "recording") showEndedToast(app_name);
});
socket.on("display_switched", ({ display_index }) => {
  if (state.appState === "recording") {
    recDispLabel.textContent = `● Đang ghi: ${state.selectedDisplayName}`;
  }
});

socket.on("window_switched", ({ title }) => {
  if (state.appState === "recording" && title) {
    recDispLabel.textContent = `● Đang ghi: ${title}`;
  }
});

// ══════════════════════════════════════════════════════════════════════
// FILE LIST
// ══════════════════════════════════════════════════════════════════════
async function loadFiles() {
  try {
    const res = await fetch("/api/files?per_page=200");
    const data = await res.json();
    renderFiles(data.files || []);
  } catch (err) {
    console.error("Lỗi tải danh sách file:", err);
  }
}

function renderFiles(files) {
  const tbody = document.getElementById("files-tbody");
  const empty = document.getElementById("files-empty");
  tbody.innerHTML = "";

  if (!files || files.length === 0) {
    empty.classList.remove("hidden");
    updateMergeSelects([]);
    return;
  }
  empty.classList.add("hidden");
  updateMergeSelects(files);

  // Sort by created_at desc
  files.sort((a, b) => b.created_at - a.created_at);

  for (const f of files) {
    const tr = document.createElement("tr");
    tr.className = "file-row";
    const badgeClass = f.type === "video" ? "badge-video" : f.type === "audio" ? "badge-audio" : "badge-wav";
    const badgeLabel = f.type === "video" ? "🎬 VIDEO" : f.type === "audio" ? "🎵 MP3" : "🔊 WAV";
    const dt = new Date(f.created_at * 1000);
    const dtStr = `${padZ(dt.getHours())}:${padZ(dt.getMinutes())} ${padZ(dt.getDate())}/${padZ(dt.getMonth() + 1)}`;

    tr.innerHTML = `
      <td class="file-name">${escHtml(f.name)}</td>
      <td><span class="file-badge ${badgeClass}">${badgeLabel}</span></td>
      <td>${f.size_mb} MB</td>
      <td class="file-time">${dtStr}</td>
      <td class="file-actions">
        <a class="btn-dl" href="/api/download/${encodeURIComponent(f.name)}" download="${escHtml(f.name)}">⬇ Tải</a>
        <button class="btn-del" data-name="${escHtml(f.name)}">✕ Xóa</button>
      </td>
    `;
    tr.querySelector(".btn-del").addEventListener("click", async (e) => {
      const name = e.currentTarget.dataset.name;
      if (!confirm(`Xóa file "${name}"?`)) return;
      try {
        await fetch("/api/files/" + encodeURIComponent(name), { method: "DELETE" });
      } catch (err) {
        alert("Lỗi xóa: " + err.message);
      }
    });
    tbody.appendChild(tr);
  }
}

// ══════════════════════════════════════════════════════════════════════
// MANUAL MERGE
// ══════════════════════════════════════════════════════════════════════
function updateMergeSelects(files) {
  const selVideo = document.getElementById("sel-merge-video");
  const selAudio = document.getElementById("sel-merge-audio");
  const prevVideo = selVideo.value;
  const prevAudio = selAudio.value;

  selVideo.innerHTML = '<option value="">— Chọn file video —</option>';
  selAudio.innerHTML = '<option value="">— Chọn file audio —</option>';

  const sorted = [...files].sort((a, b) => b.created_at - a.created_at);
  for (const f of sorted) {
    const opt = document.createElement("option");
    opt.value = f.name;
    opt.textContent = f.name;
    if (f.type === "video") {
      selVideo.appendChild(opt);
    } else {
      selAudio.appendChild(opt.cloneNode(true));
    }
  }

  if (prevVideo) selVideo.value = prevVideo;
  if (prevAudio) selAudio.value = prevAudio;
}

document.getElementById("btn-manual-merge").addEventListener("click", async () => {
  const video    = document.getElementById("sel-merge-video").value;
  const audio    = document.getElementById("sel-merge-audio").value;
  const offset   = parseInt(document.getElementById("inp-merge-offset").value) || 0;
  const statusEl = document.getElementById("merge-status");

  if (!video) { alert("Vui lòng chọn file video."); return; }
  if (!audio) { alert("Vui lòng chọn file audio."); return; }

  const btn = document.getElementById("btn-manual-merge");
  btn.disabled = true;
  btn.textContent = "⏳ Đang gửi…";
  statusEl.textContent = "";

  try {
    const res = await fetch("/api/merge-files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video, audio, audio_offset_ms: offset }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert("Lỗi: " + (data.error || res.statusText));
      statusEl.textContent = "✗ " + (data.error || res.statusText);
    } else {
      statusEl.textContent = "⏳ Đang xử lý… (xem tiến độ bên dưới)";
    }
  } catch (err) {
    alert("Lỗi kết nối: " + err.message);
    statusEl.textContent = "✗ " + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ GHÉP FILE";
  }
});

// ══════════════════════════════════════════════════════════════════════
// OPEN FOLDER
// ══════════════════════════════════════════════════════════════════════
document.getElementById("open-folder-btn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/open-folder", { method: "POST" });
    const d = await res.json();
    if (!d.ok) alert("Không thể mở thư mục: " + (d.error || ""));
  } catch (err) {
    alert("Lỗi: " + err.message);
  }
});

// ══════════════════════════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════════════════════════
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ════════════════// ══════════════════════════════════════════════════════
// SIMULATE CALL (Test Panel)
// ══════════════════════════════════════════════════════
document.getElementById('btn-sim-start').addEventListener('click', async () => {
  const appName = document.getElementById('sim-app-select').value;
  try {
    const res = await fetch('/api/simulate/call-start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_name: appName }),
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById('btn-sim-start').disabled = true;
      document.getElementById('btn-sim-end').disabled = false;
      document.getElementById('last-detected-app').textContent = appName;
      document.getElementById('detection-time').textContent =
        '(' + new Date().toLocaleTimeString('vi-VN') + ')';
      // SocketIO "call_detected" fires automatically → shows modal (same path as real call)
    } else {
      alert('Lỗi: ' + (data.error || 'Không thể giả lập'));
    }
  } catch (err) {
    alert('Lỗi kết nối: ' + err.message);
  }
});

document.getElementById('btn-sim-end').addEventListener('click', async () => {
  try {
    await fetch('/api/simulate/call-end', { method: 'POST' });
    document.getElementById('btn-sim-start').disabled = false;
    document.getElementById('btn-sim-end').disabled = true;
    // SocketIO "call_ended" fires automatically → shows toast if recording
  } catch (err) {
    alert('Lỗi kết nối: ' + err.message);
  }
});

// ══════════════════════════════════════════════════════
// DETECTION STATUS POLLING
// Polls /api/detector/status every 3s ONLY when test panel <details> is open
// ══════════════════════════════════════════════════════
async function pollDetectionStatus() {
  // Only poll while panel is expanded
  const panelDetails = document.querySelector('#test-panel details');
  if (!panelDetails || !panelDetails.open) return;

  try {
    const res = await fetch('/api/detector/status');
    const data = await res.json();
    const r = data.last_result;
    if (r) {
      document.getElementById('det-a').textContent = r.method_a ? '✅' : '❌';
      document.getElementById('det-b').textContent = r.method_b ? '✅' : '❌';
      document.getElementById('det-c').textContent = r.method_c ? '✅' : '❌';
      const confMap = { high: '🟢 Cao', medium: '🟡 Trung', low: '🔴 Thấp', none: '–' };
      document.getElementById('det-conf').textContent = confMap[r.confidence] || r.confidence;
    }
    // Update footer detector status
    const statusEl = document.getElementById('detector-status');
    if (data.monitoring) {
      statusEl.textContent = data.active_call
        ? `🔴 Đang trong cuộc gọi: ${data.active_call}`
        : '🟢 Giám sát: BẬT';
      statusEl.classList.add('active');
    } else {
      statusEl.textContent = '🔍 Giám sát: Tắt';
      statusEl.classList.remove('active');
    }
  } catch (_) { /* server not ready */ }
}
setInterval(pollDetectionStatus, 3000);
pollDetectionStatus();

// ══════════════════════════════════════════════════════════════════════
// GAIN SELECTS — persist on change
// ══════════════════════════════════════════════════════════════════════
document.getElementById("opt-mic-gain").addEventListener("change", (e) => {
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mic_gain: parseInt(e.target.value) }),
  }).catch(() => {});
});
document.getElementById("opt-spk-gain").addEventListener("change", (e) => {
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speaker_gain: parseInt(e.target.value) }),
  }).catch(() => {});
});
document.querySelectorAll('input[name="record-mode"]').forEach(radio => {
  radio.addEventListener("change", (e) => {
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record_mode_default: e.target.value }),
    }).catch(() => {});
  });
});

// ══════════════════════════════════════════════════════════════════════
// P3: WINDOW CAPTURE SELECTION  (2-col grid in #window-grid)
// ══════════════════════════════════════════════════════════════════════
async function loadWindows() {
  const grid = document.getElementById("window-grid");
  if (!grid) return;
  grid.innerHTML = '<div style="color:#666;font-size:11px;font-style:italic;grid-column:1/-1;padding:6px 0;">Đang tải…</div>';
  clearWindowPreview();
  try {
    const res = await fetch("/api/windows");
    const windows = await res.json();
    renderWindowGrid(windows);
    // Hidden select for backward compat (startRecording reads state.selectedWindow directly)
    const sel = document.getElementById("window-select");
    if (sel) {
      sel.innerHTML = '<option value="">—</option>';
      for (const w of windows) {
        const opt = document.createElement("option");
        opt.value = JSON.stringify(w);
        opt.textContent = w.title;
        sel.appendChild(opt);
      }
    }
  } catch (_) {
    if (grid) grid.innerHTML = '<div style="color:#e55;font-size:11px;grid-column:1/-1;">Lỗi tải danh sách cửa sổ.</div>';
  }
}

function renderWindowGrid(windows) {
  const grid = document.getElementById("window-grid");
  if (!grid) return;
  grid.innerHTML = "";
  if (!windows || windows.length === 0) {
    grid.innerHTML = '<div style="color:#666;font-size:11px;font-style:italic;grid-column:1/-1;">Không tìm thấy cửa sổ.</div>';
    return;
  }
  for (const w of windows) {
    const card = document.createElement("div");
    card.className = "window-card" + (state.selectedWindow && state.selectedWindow.hwnd === w.hwnd ? " active" : "");
    card.dataset.title = (w.title || "").toLowerCase();
    card.dataset.hwnd  = w.hwnd || "";
    card.innerHTML = `
      <div class="wc-title" title="${escHtml(w.title)}">${escHtml(w.title)}</div>
      <div class="wc-size">${w.width}×${w.height}</div>
    `;
    card.addEventListener("click", () => setSelectedWindow(w));
    grid.appendChild(card);
  }
}

function setSelectedWindow(w) {
  state.selectedWindow = w;
  // Update hidden select for compat
  const sel = document.getElementById("window-select");
  if (sel) sel.value = JSON.stringify(w) in Array.from(sel.options).map(o => o.value) ? JSON.stringify(w) : sel.value;
  // Update active class
  document.querySelectorAll(".window-card").forEach(c => {
    c.classList.toggle("active", String(c.dataset.hwnd) === String(w.hwnd));
  });
  if (state.appState === "recording") {
    // Switch window mid-recording
    fetch("/api/switch-window", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ window_region: w }),
    }).then(r => r.json()).then(d => {
      if (d.ok) recDispLabel.textContent = `● Đang ghi: ${w.title}`;
    }).catch(err => console.error("Lỗi chuyển cửa sổ:", err));
  }
  loadWindowPreview(w);
}

// Search filter
const _winSearch = document.getElementById("window-search");
if (_winSearch) {
  _winSearch.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    document.querySelectorAll(".window-card").forEach(c => {
      c.style.display = (!q || c.dataset.title.includes(q)) ? "" : "none";
    });
  });
}

// Refresh button
const btnRefreshWindows = document.getElementById("refresh-windows");
if (btnRefreshWindows) btnRefreshWindows.addEventListener("click", loadWindows);

async function loadWindowPreview(win) {
  const container = document.getElementById("window-preview-container");
  const img = document.getElementById("window-preview-img");
  const lbl = document.getElementById("window-preview-label");
  if (!container || !img) return;
  container.classList.remove("hidden");
  img.src = "";
  img.style.display = "none";
  if (lbl) lbl.textContent = "⏳ Đang tải preview...";
  try {
    const res = await fetch("/api/windows/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hwnd:   win.hwnd || null,
        title:  win.title,
        width:  win.width,
        height: win.height,
      }),
    });
    const data = await res.json();
    if (data.ok && data.preview_b64) {
      img.src = `data:image/jpeg;base64,${data.preview_b64}`;
      img.style.display = "block";
      if (lbl) lbl.textContent = win.title;
    } else {
      if (lbl) lbl.textContent = `⚠️ ${data.error || "Không lấy được preview"}`;
    }
  } catch (err) {
    if (lbl) lbl.textContent = `⚠️ Lỗi: ${err.message}`;
  }
}

function clearWindowPreview() {
  const container = document.getElementById("window-preview-container");
  const img = document.getElementById("window-preview-img");
  const lbl = document.getElementById("window-preview-label");
  if (container) container.classList.add("hidden");
  if (img) { img.src = ""; img.style.display = "none"; }
  if (lbl) lbl.textContent = "";
}



// ══════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════
(async function init() {
  // Load config defaults from HTML (set by Jinja2 via data-* or via server state fetch)
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    state.selectedDisplay = cfg.default_display_index || 1;
    state.autoDetect = !!cfg.auto_detect_calls;
    autoDetectToggle.checked = state.autoDetect;
    updateDetectorStatus(state.autoDetect);
    document.getElementById("opt-mic-gain").value = cfg.mic_gain || 1;
    document.getElementById("opt-spk-gain").value = cfg.speaker_gain || 1;
    if (cfg.record_mode_default) {
      const modeRadio = document.querySelector(`input[name="record-mode"][value="${cfg.record_mode_default}"]`);
      if (modeRadio) modeRadio.checked = true;
    }
    // P3: Load mic device selection
    const micDevSel = document.getElementById("mic-device-select");
    if (micDevSel && cfg.mic_device_index != null) {
      micDevSel.value = cfg.mic_device_index;
    }
  } catch (_) {}

  // Restore status
  try {
    const res = await fetch("/api/status");
    const d   = await res.json();
    applyState(d.state, d.duration_seconds || 0);
  } catch (_) {}

  await loadDisplays();
  await loadFiles();
  await loadAudioDevices();
})();

// ══════════════════════════════════════════════════════════════════════
// P3: KEYBOARD SHORTCUTS
// ══════════════════════════════════════════════════════════════════════
document.addEventListener("keydown", (e) => {
  // Ctrl+Shift+R → toggle recording (browser shortcut)
  if (e.ctrlKey && e.shiftKey && e.key === "R") {
    e.preventDefault();
    if (state.appState === "idle") startRecording();
    else if (state.appState === "recording") stopRecording();
  }
});

// ══════════════════════════════════════════════════════════════════════
// P3: AUDIO DEVICE SELECTION
// ══════════════════════════════════════════════════════════════════════
async function loadAudioDevices() {
  const sel = document.getElementById("mic-device-select");
  if (!sel) return;
  try {
    const res = await fetch("/api/audio-devices");
    const data = await res.json();
    sel.innerHTML = '<option value="">— Mặc định —</option>';
    for (const d of (data.devices || [])) {
      if (d.is_input) {
        const opt = document.createElement("option");
        opt.value = d.index;
        opt.textContent = d.name;
        sel.appendChild(opt);
      }
    }
    // Restore saved selection
    const cfgRes = await fetch("/api/config");
    const cfg = await cfgRes.json();
    if (cfg.mic_device_index != null) sel.value = cfg.mic_device_index;
  } catch (_) {}
}

const micDevSel = document.getElementById("mic-device-select");
if (micDevSel) {
  micDevSel.addEventListener("change", (e) => {
    const val = e.target.value === "" ? null : parseInt(e.target.value);
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mic_device_index: val }),
    }).catch(() => {});
  });
}

// ══════════════════════════════════════════════════════════════════════
// P3: SCHEDULE RECORDING
// ══════════════════════════════════════════════════════════════════════
let _scheduleCountdown = null;

const btnScheduleStart = document.getElementById("btn-schedule-start");
const btnScheduleStop  = document.getElementById("btn-schedule-stop");
const btnScheduleCancel = document.getElementById("btn-schedule-cancel");
const scheduleStatus   = document.getElementById("schedule-status");

if (btnScheduleStart) {
  btnScheduleStart.addEventListener("click", async () => {
    const mins = parseInt(document.getElementById("schedule-delay").value) || 0;
    if (mins <= 0) { alert("Nhập số phút > 0"); return; }
    try {
      await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start_after", delay_seconds: mins * 60 }),
      });
    } catch (err) { alert("Lỗi: " + err.message); }
  });
}

if (btnScheduleStop) {
  btnScheduleStop.addEventListener("click", async () => {
    const mins = parseInt(document.getElementById("schedule-delay").value) || 0;
    if (mins <= 0) { alert("Nhập số phút > 0"); return; }
    try {
      await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop_after", delay_seconds: mins * 60 }),
      });
    } catch (err) { alert("Lỗi: " + err.message); }
  });
}

if (btnScheduleCancel) {
  btnScheduleCancel.addEventListener("click", async () => {
    try {
      await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "cancel" }),
      });
      if (scheduleStatus) scheduleStatus.textContent = "";
      if (_scheduleCountdown) { clearInterval(_scheduleCountdown); _scheduleCountdown = null; }
    } catch (err) { alert("Lỗi: " + err.message); }
  });
}

socket.on("schedule_event", (data) => {
  if (_scheduleCountdown) { clearInterval(_scheduleCountdown); _scheduleCountdown = null; }
  if (!scheduleStatus) return;

  if (data.type === "scheduled_start" || data.type === "scheduled_stop") {
    let remaining = data.delay;
    const label = data.type === "scheduled_start" ? "Ghi sau" : "Dừng sau";
    scheduleStatus.textContent = `⏱ ${label}: ${Math.ceil(remaining / 60)} phút`;
    _scheduleCountdown = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(_scheduleCountdown);
        _scheduleCountdown = null;
        scheduleStatus.textContent = "";
      } else {
        const m = Math.floor(remaining / 60);
        const s = remaining % 60;
        scheduleStatus.textContent = `⏱ ${label}: ${m}:${String(s).padStart(2, "0")}`;
      }
    }, 1000);
  } else if (data.type === "auto_stop") {
    stopRecording();
    scheduleStatus.textContent = "✅ Dừng tự động theo lịch.";
    setTimeout(() => { if (scheduleStatus) scheduleStatus.textContent = ""; }, 3000);
  } else if (data.type === "started") {
    scheduleStatus.textContent = "✅ Đã bắt đầu ghi theo lịch.";
    setTimeout(() => { if (scheduleStatus) scheduleStatus.textContent = ""; }, 3000);
  } else if (data.type === "cancelled") {
    scheduleStatus.textContent = "";
  }
});
