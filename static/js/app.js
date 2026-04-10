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
};

// ── Elements ─────────────────────────────────────────────────────────
const recBtn        = document.getElementById("rec-button");
const recLabel      = document.getElementById("rec-label");
const recIcon       = recBtn.querySelector(".rec-icon");
const timerEl       = document.getElementById("timer");
const statusDot     = document.getElementById("status-dot");
const statusLabel   = document.getElementById("status-label");
const micBar        = document.getElementById("mic-bar");
const spkBar        = document.getElementById("spk-bar");
const micDb         = document.getElementById("mic-db");
const spkDb         = document.getElementById("spk-db");
const jobSection    = document.getElementById("job-progress");
const displayGrid   = document.getElementById("display-grid");
const recDispLabel  = document.getElementById("recording-display-label");
const detectorStatus = document.getElementById("detector-status");
const autoDetectToggle = document.getElementById("auto-detect-toggle");

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
    document.title = `🔴 REC ${formatTime(state.seconds)} • ${BASE_TITLE}`;
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
let _silenceSeconds = 0;
let _silenceInterval = null;
let _silenceToastTimer = null;
let _silenceNotified = false;

function startSilenceMonitoring() {
  _silenceSeconds = 0;
  _silenceNotified = false;
  _lastMicLevel = 0;
  _lastSpkLevel = 0;
  if (_silenceInterval) clearInterval(_silenceInterval);
  _silenceInterval = setInterval(() => {
    const isSilent = _lastMicLevel < SILENCE_CFG.THRESHOLD &&
                     _lastSpkLevel < SILENCE_CFG.THRESHOLD;
    if (isSilent) {
      _silenceSeconds++;
      if (_silenceSeconds >= SILENCE_CFG.WARNING_AFTER_S && !_silenceNotified) {
        _silenceNotified = true;
        showSilenceToast();
      }
    } else {
      _silenceSeconds = 0;
      _silenceNotified = false;
      dismissSilenceToast();
    }
  }, 1000);
}

function stopSilenceMonitoring() {
  if (_silenceInterval) { clearInterval(_silenceInterval); _silenceInterval = null; }
  _silenceSeconds = 0;
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
  statusDot.className = `status-dot ${newState}`;
  const labels = { idle: "SẴN SÀNG", recording: "ĐANG GHI", processing: "ĐANG XỬ LÝ" };
  statusLabel.textContent = labels[newState] || newState.toUpperCase();

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
    recDispLabel.textContent = `● Đang ghi: ${state.selectedDisplayName}`;
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
  _silenceSeconds = 0;
  _silenceNotified = false;
});

// ══════════════════════════════════════════════════════════════════════
// DISPLAY SELECTOR
// ══════════════════════════════════════════════════════════════════════
async function loadDisplays() {
  displayGrid.innerHTML = '<div class="display-card-loading">Đang tải danh sách màn hình…</div>';
  try {
    const res = await fetch("/api/displays?preview=true");
    const displays = await res.json();
    renderDisplayCards(displays);
  } catch (err) {
    displayGrid.innerHTML = '<div class="display-card-loading">Không thể tải màn hình.</div>';
  }
}

function renderDisplayCards(displays) {
  displayGrid.innerHTML = "";
  if (!displays || displays.length === 0) {
    displayGrid.innerHTML = '<div class="display-card-loading">Không tìm thấy màn hình.</div>';
    return;
  }
  for (const d of displays) {
    const card = document.createElement("div");
    card.className = "display-card" + (d.index === state.selectedDisplay ? " active" : "");
    card.dataset.index = d.index;

    const imgSrc = d.preview_b64
      ? `data:image/jpeg;base64,${d.preview_b64}`
      : "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"; // 1px transparent

    card.innerHTML = `
      <img class="display-preview" src="${imgSrc}" alt="${escHtml(d.name)}" loading="lazy" />
      <div class="display-label">${escHtml(d.name)}</div>
      <div class="display-res">${d.width} × ${d.height}</div>
    `;
    card.addEventListener("click", () => setSelectedDisplay(d.index, d.name));
    displayGrid.appendChild(card);
  }
}

function setSelectedDisplay(index, name) {
  state.selectedDisplay = index;
  state.selectedDisplayName = name || `Display ${index}`;
  // Update active class
  document.querySelectorAll(".display-card").forEach(c => {
    c.classList.toggle("active", parseInt(c.dataset.index) === index);
  });

  if (state.appState === "recording") {
    // Chuyển màn hình ngay trong khi đang ghi
    fetch("/api/switch-display", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_index: index }),
    }).then(res => res.json()).then(data => {
      if (data.ok) {
        recDispLabel.textContent = `\u25CF Đang ghi: ${state.selectedDisplayName}`;
      } else {
        console.error("Lỗi chuyển màn hình:", data.error);
      }
    }).catch(err => console.error("Lỗi chuyển màn hình:", err));
  } else {
    // Persist to config khi không ghi
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_display_index: index }),
    }).catch(() => {});
  }
}

async function refreshPreviews() {
  document.getElementById("refresh-previews").textContent = "↺ Đang tải…";
  document.getElementById("refresh-previews").disabled = true;
  try {
    const res = await fetch("/api/displays/preview");
    const previews = await res.json();
    for (const p of previews) {
      const card = displayGrid.querySelector(`[data-index="${p.index}"]`);
      if (card && p.preview_b64) {
        const img = card.querySelector(".display-preview");
        if (img) img.src = `data:image/jpeg;base64,${p.preview_b64}`;
      }
    }
  } catch (err) {
    console.error("Lỗi làm mới xem trước:", err);
  } finally {
    document.getElementById("refresh-previews").textContent = "↺ Làm mới xem trước";
    document.getElementById("refresh-previews").disabled = false;
  }
}

document.getElementById("refresh-previews").addEventListener("click", refreshPreviews);

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
  // Cập nhật label khi server xác nhận chuyển màn hình
  if (state.appState === "recording") {
    recDispLabel.textContent = `\u25CF Đang ghi: ${state.selectedDisplayName}`;
  }
});

// ══════════════════════════════════════════════════════════════════════
// FILE LIST
// ══════════════════════════════════════════════════════════════════════
async function loadFiles() {
  try {
    const res = await fetch("/api/files");
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
  } catch (_) {}

  // Restore status
  try {
    const res = await fetch("/api/status");
    const d   = await res.json();
    applyState(d.state, d.duration_seconds || 0);
  } catch (_) {}

  await loadDisplays();
  await loadFiles();
})();
