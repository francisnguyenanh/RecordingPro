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
const progressFill  = document.getElementById("progress-fill");
const progressPct   = document.getElementById("progress-pct");
const progressStage = document.getElementById("progress-stage");
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
// UI STATE
// ══════════════════════════════════════════════════════════════════════
function applyState(newState, durationSeconds = 0) {
  state.appState = newState;
  statusDot.className = `status-dot ${newState}`;
  const labels = { idle: "SẴN SÀNG", recording: "ĐANG GHI", processing: "ĐANG XỬ LÝ" };
  statusLabel.textContent = labels[newState] || newState.toUpperCase();

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
  bar.className = "level-bar" + (
    state.appState === "recording" ? " breathing" : ""
  ) + (level >= 0.85 ? " red" : level >= 0.6 ? " yellow" : "");
  const db = level < 0.001 ? "–∞" : (20 * Math.log10(level)).toFixed(1) + " dB";
  dbEl.textContent = db;
}

// ══════════════════════════════════════════════════════════════════════
// REC BUTTON
// ══════════════════════════════════════════════════════════════════════
recBtn.addEventListener("click", async () => {
  if (state.appState === "idle") await startRecording();
  else if (state.appState === "recording") await stopRecording();
});

async function startRecording() {
  try {
    const body = {
      display_index: state.selectedDisplay,
      merge_audio: document.getElementById("opt-merge").checked,
      convert_mp3: document.getElementById("opt-mp3").checked,
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
  try {
    const body = {
      merge_audio: document.getElementById("opt-merge").checked,
      convert_mp3: document.getElementById("opt-mp3").checked,
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
  applyState(s, duration_seconds || 0);
});

socket.on("level_update", ({ mic, speaker }) => {
  if (state.appState !== "recording") return;
  updateMeter("mic-bar", "mic-db", mic || 0);
  updateMeter("spk-bar", "spk-db", speaker || 0);
});

socket.on("job_progress", ({ stage, message, percent }) => {
  jobSection.classList.remove("hidden");
  progressFill.style.width = (percent || 0) + "%";
  progressPct.textContent  = (percent || 0) + "%";
  progressStage.textContent = message || stage;
  if (stage === "done" || stage === "error") {
    setTimeout(() => {
      jobSection.classList.add("hidden");
      progressFill.style.width = "0%";
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
    const files = await res.json();
    renderFiles(files);
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
    return;
  }
  empty.classList.add("hidden");

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
