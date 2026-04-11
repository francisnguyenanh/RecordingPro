"""
app.py — ScreenCapturePro v2
Flask backend: Config, CallDetector, DisplayManager, Multi-display recording.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from flask import Flask, jsonify, render_template, request, send_file
from flask_socketio import SocketIO

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Flask + SocketIO ──────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "screencapturepro-v2-secret"
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# ── Paths ─────────────────────────────────────────────────────────────
OUTPUT_DIR = Path.home() / "Videos" / "ScreenCapturePro"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = Path(__file__).parent / "config.json"

# ── Default config ────────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "default_display_index": 1,
    "auto_detect_calls": False,
    "auto_record_delay_seconds": 5,
    "auto_stop_on_call_end": True,
    "record_mode_default": "mp4_mp3",
    "mic_gain": 1,
    "speaker_gain": 1,
    "output_dir": None,
    "mic_device_index": None,
    "global_hotkey_enabled": True,
    "schedule_delay_minutes": 0,
}

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open() as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception as exc:
            logger.warning("[Config] Không đọc được config.json: %s", exc)
    return cfg


def save_config(updates: dict) -> None:
    cfg = load_config()
    cfg.update(updates)
    try:
        with CONFIG_PATH.open("w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("[Config] Không ghi được config.json: %s", exc)


# ── Global state (thread-safe) ────────────────────────────────────────
_state_lock = threading.Lock()
current_session: Optional[object] = None
app_state: Literal["idle", "recording", "processing"] = "idle"
_recording_start_time: float = 0.0
popup_pending: bool = False    # Đang hiển thị modal hỏi người dùng
_last_detected_app: Optional[str] = None  # P3: Tên app vừa detect được
_schedule_timer: Optional[threading.Timer] = None
_schedule_lock = threading.Lock()

# ── CallDetector & DisplayManager singletons ─────────────────────────
from recorder.call_detector import CallDetector
from recorder.display_manager import DisplayManager

_display_manager = DisplayManager()
_detector: Optional[CallDetector] = None


# ══════════════════════════════════════════════════════════════════════
# CALL DETECTOR CALLBACKS
# ══════════════════════════════════════════════════════════════════════

def _on_call_start(app_name: str) -> None:
    global popup_pending, _last_detected_app
    with _state_lock:
        if popup_pending:
            logger.debug("[Detector] Popup đang mở, bỏ qua sự kiện trùng lặp.")
            return
        popup_pending = True
        _last_detected_app = app_name
    logger.info("[Detector] Phát hiện cuộc gọi: %s → emit call_detected", app_name)
    socketio.emit("call_detected", {"app_name": app_name})


def _on_call_end(app_name: str) -> None:
    global popup_pending
    with _state_lock:
        pending = popup_pending
        state = app_state
        popup_pending = False

    if pending:
        # Popup chưa được trả lời → tự đóng
        socketio.emit("call_popup_dismissed", {"reason": "call_ended_before_answer"})
        logger.info("[Detector] Cuộc gọi kết thúc trước khi người dùng trả lời popup.")

    if state == "recording":
        socketio.emit("call_ended", {"app_name": app_name})
        logger.info("[Detector] Cuộc gọi %s kết thúc trong khi đang ghi.", app_name)


def _start_detector() -> None:
    global _detector
    if _detector is None:
        _detector = CallDetector(
            on_call_start=_on_call_start,
            on_call_end=_on_call_end,
        )
    _detector.start_monitoring()


def _stop_detector() -> None:
    global _detector
    if _detector is not None:
        _detector.stop_monitoring()


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Recording
# ══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    cfg = load_config()
    return render_template("index.html", output_dir=str(OUTPUT_DIR), config=cfg)


@app.route("/api/start", methods=["POST"])
def api_start():
    global current_session, app_state, _recording_start_time

    with _state_lock:
        if app_state != "idle":
            return jsonify({"ok": False, "error": "Đang bận: " + app_state}), 409

        data = request.get_json(silent=True) or {}
        display_index = int(data.get("display_index", 1))

        cfg = load_config()
        mic_device = cfg.get("mic_device_index")

        # Window region capture (optional)
        window_region = None
        if data.get("window_region"):
            wr = data["window_region"]
            try:
                window_region = {
                    "left":   int(wr["left"]),
                    "top":    int(wr["top"]),
                    "width":  int(wr["width"]),
                    "height": int(wr["height"]),
                    "title":  str(wr.get("title", "")),
                }
                if window_region["width"] <= 0 or window_region["height"] <= 0:
                    window_region = None
            except (KeyError, ValueError, TypeError):
                window_region = None

        from recorder.session import RecordingSession
        session = RecordingSession(display_index=display_index, output_dir=OUTPUT_DIR,
                                   mic_device=mic_device, window_region=window_region)
        try:
            session.start()
        except Exception as exc:
            logger.exception("[API/start] Lỗi bắt đầu session")
            return jsonify({"ok": False, "error": str(exc)}), 500

        current_session = session
        app_state = "recording"
        _recording_start_time = time.monotonic()

    socketio.start_background_task(_level_emitter)
    _emit_status()
    return jsonify({"ok": True, "session_id": session.session_id})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global current_session, app_state

    with _state_lock:
        if app_state != "recording" or current_session is None:
            return jsonify({"ok": False, "error": "Không có phiên đang ghi."}), 409
        session = current_session
        app_state = "processing"

    _emit_status()
    data = request.get_json(silent=True) or {}
    merge_audio = bool(data.get("merge_audio", True))
    convert_mp3 = bool(data.get("convert_mp3", True))
    mic_gain = max(0.1, float(data.get("mic_gain", 1)))
    speaker_gain = max(0.1, float(data.get("speaker_gain", 1)))
    job_id = session.session_id

    def _do_stop():
        global current_session, app_state
        try:
            # Tính tổng số steps: 
            # 1: Dừng video
            # 2: Dừng audio
            # 3: Bắt đầu hậu xử lý / Đợi xử lý phân đoạn
            # 4: Nối phân đoạn (Concat)
            # (+1 nếu convert_mp3): Xuất MP3
            total_steps = 4
            if convert_mp3:
                total_steps += 1
            total_steps += 1  # Step 5/6: Hoàn tất
            
            current_step = 0
            
            # Step 1: Dừng video
            current_step += 1
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "info",
                "message": "🔴 Dừng ghi video...", "log_type": "info",
                "current_step": current_step, "total_steps": total_steps,
            })
            
            # Step 2: Dừng audio
            current_step += 1
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "info",
                "message": "🔴 Dừng ghi audio...", "log_type": "info",
                "current_step": current_step, "total_steps": total_steps,
            })
            
            # Step 3: Hậu xử lý
            current_step += 1
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "info",
                "message": "⏳ Bắt đầu hậu xử lý...", "log_type": "info",
                "current_step": current_step, "total_steps": total_steps,
            })
            
            session.stop(
                merge_audio=merge_audio, convert_mp3=convert_mp3,
                mic_gain=mic_gain, speaker_gain=speaker_gain,
                current_step_ref={"step": current_step, "total": total_steps},
            )
            
            # Final Step: Hoàn tất
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "done",
                "message": "✅ Xử lý hoàn tất!", "log_type": "success",
                "current_step": total_steps, "total_steps": total_steps,
            })
        except Exception as exc:
            logger.exception("[API/stop] Lỗi hậu xử lý")
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "error",
                "message": f"❌ Lỗi: {exc}", "log_type": "error",
                "current_step": 0, "total_steps": 0,
            })
        finally:
            with _state_lock:
                current_session = None
                app_state = "idle"
            _emit_status()

    socketio.start_background_task(_do_stop)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/status", methods=["GET"])
def api_status():
    with _state_lock:
        state = app_state
        dur = int(time.monotonic() - _recording_start_time) if state == "recording" else 0
        sid = current_session.session_id if current_session else None
    return jsonify({"state": state, "duration_seconds": dur, "session_id": sid})


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Files
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/files", methods=["GET"])
def api_files():
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    except (ValueError, TypeError):
        page, per_page = 1, 50

    files = []
    for p in sorted(OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.suffix.lower() not in (".mp4", ".mp3", ".wav"):
            continue
        stat = p.stat()
        ext = p.suffix.lower()
        ftype = "video" if ext == ".mp4" else ("audio" if ext == ".mp3" else "wav")
        files.append({
            "name": p.name,
            "type": ftype,
            "size_mb": round(stat.st_size / 1_048_576, 2),
            "created_at": int(stat.st_mtime),
            "download_url": f"/api/download/{p.name}",
        })

    total = len(files)
    start = (page - 1) * per_page
    return jsonify({
        "files": files[start : start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@app.route("/api/download/<name>", methods=["GET"])
def api_download(name: str):
    path = OUTPUT_DIR / name
    try:
        path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Đường dẫn không hợp lệ."}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"error": "File không tồn tại."}), 404
    return send_file(str(path), as_attachment=True)


@app.route("/api/files/<name>", methods=["DELETE"])
def api_delete(name: str):
    path = OUTPUT_DIR / name
    if not path.exists() or not path.is_file():
        return jsonify({"error": "File không tồn tại."}), 404
    path.unlink()
    socketio.emit("files_updated", {})
    return jsonify({"ok": True})


@app.route("/api/merge-files", methods=["POST"])
def api_merge_files():
    """Ghép thủ công 1 file video + 1 file audio thành file _merged.mp4."""
    import subprocess as _sp
    data = request.get_json(silent=True) or {}
    video_name = data.get("video", "").strip()
    audio_name = data.get("audio", "").strip()
    offset_ms  = float(data.get("audio_offset_ms", 0))

    if not video_name or not audio_name:
        return jsonify({"ok": False, "error": "Cần chọn cả file video và audio."}), 400

    # Security: ngăn path traversal
    if any(c in video_name + audio_name for c in ("/", "\\", "..")):
        return jsonify({"ok": False, "error": "Tên file không hợp lệ."}), 400

    video_path = OUTPUT_DIR / video_name
    audio_path = OUTPUT_DIR / audio_name

    try:
        video_path.resolve().relative_to(OUTPUT_DIR.resolve())
        audio_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "Đường dẫn file không hợp lệ."}), 400

    if not video_path.is_file():
        return jsonify({"ok": False, "error": f"Không tìm thấy: {video_name}"}), 404
    if not audio_path.is_file():
        return jsonify({"ok": False, "error": f"Không tìm thấy: {audio_name}"}), 404

    job_id = f"manual_merge_{int(time.monotonic() * 1000)}"

    def _do_merge():
        import sys as _sys
        _flags = {"creationflags": _sp.CREATE_NO_WINDOW} if _sys.platform == "win32" else {}
        from recorder.session import _find_ffmpeg
        try:
            ffmpeg = _find_ffmpeg()
        except FileNotFoundError as exc:
            socketio.emit("job_progress", {"job_id": job_id, "stage": "error",
                                           "message": str(exc), "log_type": "error",
                                           "current_step": 0, "total_steps": 0})
            return

        stem = video_path.stem
        if stem.endswith("_video"):
            stem = stem[:-6]
        out_name = f"{stem}_merged.mp4"
        out_path  = str(OUTPUT_DIR / out_name)
        offset_s  = offset_ms / 1000.0

        # Step 1/2
        socketio.emit("job_progress", {
            "job_id": job_id, "stage": "info",
            "message": f"🎬 Đang ghép {video_name} + {audio_name}…", "log_type": "info",
            "current_step": 1, "total_steps": 2,
        })

        cmd = [ffmpeg, "-y", "-i", str(video_path)]
        if offset_s > 0:
            cmd += ["-ss", f"{offset_s:.4f}", "-i", str(audio_path)]
        elif offset_s < 0:
            cmd += ["-itsoffset", f"{-offset_s:.4f}", "-i", str(audio_path)]
        else:
            cmd += ["-i", str(audio_path)]

        cmd += [
            "-filter_complex", "[1:a]aresample=async=1000[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            out_path,
        ]

        proc = _sp.run(cmd, capture_output=True, **_flags)
        if proc.returncode == 0:
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "done",
                "message": f"✅ Ghép xong → {out_name}", "log_type": "success",
                "current_step": 2, "total_steps": 2,
            })
            socketio.emit("files_updated", {})
        else:
            err_tail = proc.stderr.decode(errors="replace")[-200:]
            logger.error("[merge-files] ffmpeg lỗi:\n%s", err_tail)
            socketio.emit("job_progress", {
                "job_id": job_id, "stage": "error",
                "message": f"❌ FFmpeg lỗi: {err_tail[-100:]}", "log_type": "error",
                "current_step": 0, "total_steps": 0,
            })

    socketio.start_background_task(_do_merge)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    try:
        import platform
        if platform.system() == "Windows":
            os.startfile(str(OUTPUT_DIR))
        elif platform.system() == "Darwin":
            import subprocess
            subprocess.Popen(["open", str(OUTPUT_DIR)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(OUTPUT_DIR)])
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "path": str(OUTPUT_DIR)})


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Config
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def api_post_config():
    updates = request.get_json(silent=True) or {}
    save_config(updates)

    # Áp dụng auto_detect ngay lập tức
    if "auto_detect_calls" in updates:
        if updates["auto_detect_calls"]:
            _start_detector()
        else:
            _stop_detector()

    return jsonify({"ok": True, "config": load_config()})


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Displays
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/displays", methods=["GET"])
def api_displays():
    include_preview = request.args.get("preview", "false").lower() == "true"
    displays = _display_manager.get_displays(include_preview=include_preview)
    return jsonify([
        {
            "index": d.index,
            "name": d.name,
            "width": d.width,
            "height": d.height,
            "is_primary": d.is_primary,
            "preview_b64": d.preview_b64 if include_preview else "",
        }
        for d in displays
    ])


@app.route("/api/displays/preview", methods=["GET"])
def api_displays_preview():
    displays = _display_manager.refresh_previews()
    return jsonify([
        {"index": d.index, "preview_b64": d.preview_b64}
        for d in displays
    ])


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Detector
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/detector/status", methods=["GET"])
def api_detector_status():
    if _detector is None:
        return jsonify({
            "monitoring": False, "active_call": None,
            "last_result": None,
        })
    return jsonify(_detector.get_status())


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Simulate / Test
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/switch-display", methods=["POST"])
def api_switch_display():
    """Chuyển màn hình đang ghi ngay trong khi recording đang chạy."""
    global current_session
    with _state_lock:
        if app_state != "recording" or current_session is None:
            return jsonify({"ok": False, "error": "Không có phiên đang ghi."}), 409
        data = request.get_json(silent=True) or {}
        new_index = int(data.get("display_index", 1))
        current_session.switch_display(new_index)

    save_config({"default_display_index": new_index})
    socketio.emit("display_switched", {"display_index": new_index})
    return jsonify({"ok": True, "display_index": new_index})


@app.route("/api/simulate/call-start", methods=["POST"])
def api_simulate_call_start():
    """
    Trigger a fake call_detected event for UI testing.
    Body: { "app_name": "Zoom" | "Microsoft Teams" | "Google Meet" }
    Reuses same on_call_start callback → identical to real detection.
    """
    global _detector
    data = request.get_json(silent=True) or {}
    app_name = data.get("app_name", "Zoom")

    # Ensure detector exists (create on-demand for simulate, even if monitoring off)
    if _detector is None:
        _detector = CallDetector(
            on_call_start=_on_call_start,
            on_call_end=_on_call_end,
        )

    ok = _detector.simulate_call_start(app_name)
    if not ok:
        return jsonify({"error": "Đang có cuộc gọi đang hoạt động"}), 409
    return jsonify({"ok": True, "app_name": app_name})


@app.route("/api/simulate/call-end", methods=["POST"])
def api_simulate_call_end():
    """
    Trigger a fake call_ended event for UI testing.
    Fires on_call_end callback → same as real detection.
    """
    global _detector
    if _detector is None:
        return jsonify({"error": "Không có cuộc gọi nào đang chạy"}), 409

    ok = _detector.simulate_call_end()
    if not ok:
        return jsonify({"error": "Không có cuộc gọi nào đang chạy"}), 409
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════
# SOCKETIO EVENTS
# ══════════════════════════════════════════════════════════════════════

@socketio.on("connect")
def on_connect():
    _emit_status()


@socketio.on("confirm_record")
def on_confirm_record():
    """Client xác nhận muốn bắt đầu ghi sau khi phát hiện cuộc gọi."""
    global popup_pending
    with _state_lock:
        popup_pending = False
        state = app_state

    if state != "idle":
        logger.warning("[Socket] confirm_record nhận được nhưng app_state=%s", state)
        return

    cfg = load_config()
    display_index = int(cfg.get("default_display_index", 1))

    # P3: truyền tên app detected cho auto-naming
    _start_session(display_index=display_index, detected_app=_last_detected_app)


@socketio.on("dismiss_call_popup")
def on_dismiss_call_popup():
    global popup_pending
    with _state_lock:
        popup_pending = False
    logger.info("[Socket] Người dùng bỏ qua popup cuộc gọi.")


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Audio Devices (P3)
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/audio-devices", methods=["GET"])
def api_audio_devices():
    """Liệt kê thiết bị âm thanh để user chọn mic/speaker."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            result.append({
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": d["default_samplerate"],
                "is_input": d["max_input_channels"] > 0,
                "is_output": d["max_output_channels"] > 0,
            })
        di, do = sd.default.device
        return jsonify({"devices": result, "default_input": di, "default_output": do})
    except Exception as exc:
        logger.warning("[AudioDevices] %s", exc)
        return jsonify({"devices": [], "error": str(exc)})


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Windows List (P3 — region capture)
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/windows", methods=["GET"])
def api_windows():
    """Liệt kê cửa sổ đang mở để chọn ghi theo cửa sổ."""
    try:
        import pygetwindow as gw
        windows = []
        for w in gw.getAllWindows():
            if w.title and w.visible and w.width > 100 and w.height > 100:
                windows.append({
                    "title": w.title,
                    "left": w.left, "top": w.top,
                    "width": w.width, "height": w.height,
                })
        return jsonify(windows)
    except Exception as exc:
        logger.warning("[Windows] %s", exc)
        return jsonify([])


# ══════════════════════════════════════════════════════════════════════
# ROUTES — Schedule (P3)
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/schedule", methods=["POST"])
def api_schedule():
    """Hẹn giờ bắt đầu hoặc dừng ghi."""
    global _schedule_timer
    data = request.get_json(silent=True) or {}
    action = data.get("action")  # "start_after" | "stop_after" | "cancel"
    delay_seconds = int(data.get("delay_seconds", 0))

    if action == "cancel":
        with _schedule_lock:
            if _schedule_timer:
                _schedule_timer.cancel()
                _schedule_timer = None
        socketio.emit("schedule_event", {"type": "cancelled"})
        return jsonify({"ok": True, "message": "Đã hủy hẹn giờ."})

    if action == "start_after" and delay_seconds > 0:
        def _scheduled_start():
            global _schedule_timer
            cfg = load_config()
            _start_session(display_index=cfg.get("default_display_index", 1))
            with _schedule_lock:
                _schedule_timer = None
            socketio.emit("schedule_event", {"type": "started"})

        with _schedule_lock:
            if _schedule_timer:
                _schedule_timer.cancel()
            _schedule_timer = threading.Timer(delay_seconds, _scheduled_start)
            _schedule_timer.daemon = True
            _schedule_timer.start()
        socketio.emit("schedule_event", {"type": "scheduled_start", "delay": delay_seconds})
        return jsonify({"ok": True, "message": f"Hẹn ghi sau {delay_seconds}s"})

    if action == "stop_after" and delay_seconds > 0:
        def _scheduled_stop():
            global _schedule_timer
            with _schedule_lock:
                _schedule_timer = None
            # Emit signal to trigger stop from client side
            socketio.emit("schedule_event", {"type": "auto_stop"})

        with _schedule_lock:
            if _schedule_timer:
                _schedule_timer.cancel()
            _schedule_timer = threading.Timer(delay_seconds, _scheduled_stop)
            _schedule_timer.daemon = True
            _schedule_timer.start()
        socketio.emit("schedule_event", {"type": "scheduled_stop", "delay": delay_seconds})
        return jsonify({"ok": True, "message": f"Hẹn dừng sau {delay_seconds}s"})

    return jsonify({"ok": False, "error": "Action không hợp lệ."}), 400


# ══════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════

def _start_session(display_index: int = 1, detected_app: str = None,
                   window_region: dict = None) -> None:
    global current_session, app_state, _recording_start_time
    with _state_lock:
        if app_state != "idle":
            return
        cfg = load_config()
        mic_device = cfg.get("mic_device_index")
        from recorder.session import RecordingSession
        session = RecordingSession(display_index=display_index, output_dir=OUTPUT_DIR,
                                   detected_app=detected_app, mic_device=mic_device,
                                   window_region=window_region)
        try:
            session.start()
        except Exception as exc:
            logger.exception("[Internal] Lỗi bắt đầu session")
            return
        current_session = session
        app_state = "recording"
        _recording_start_time = time.monotonic()

    socketio.start_background_task(_level_emitter)
    _emit_status()


def _emit_status() -> None:
    with _state_lock:
        state = app_state
        dur = int(time.monotonic() - _recording_start_time) if state == "recording" else 0
        sid = current_session.session_id if current_session else None
    socketio.emit("status_update", {
        "state": state,
        "duration_seconds": dur,
        "session_id": sid,
    })


def _level_emitter() -> None:
    tick = 0
    while True:
        with _state_lock:
            state = app_state
            session = current_session
        if state != "recording" or session is None:
            break
        mic_lv = getattr(getattr(session, "audio", None), "mic_level", 0.0)
        spk_lv = getattr(getattr(session, "audio", None), "speaker_level", 0.0)
        socketio.emit("level_update", {"mic": round(mic_lv, 3), "speaker": round(spk_lv, 3)})
        tick += 1
        if tick % 50 == 0:  # mỗi 10 giây (50 × 0.2s) đồng bộ lại thời gian
            _emit_status()
        socketio.sleep(0.2)
    socketio.emit("level_update", {"mic": 0.0, "speaker": 0.0})


# ══════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════

def _on_startup() -> None:
    cfg = load_config()
    if cfg.get("auto_detect_calls"):
        logger.info("[Startup] auto_detect_calls=True → khởi động CallDetector")
        _start_detector()
    logger.info("[Startup] OUTPUT_DIR: %s", OUTPUT_DIR)


if __name__ == "__main__":
    _on_startup()
    logger.info("ScreenCapturePro v2 đang khởi động tại http://127.0.0.1:5010")
    #socketio.run(app, host="127.0.0.1", port=5004)
    socketio.run(app, host="127.0.0.1", port=5010, debug=True)