"""
session.py — v2: Điều phối RecordingSession với hỗ trợ chọn màn hình.
"""
import concurrent.futures
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .audio_engine import AudioEngine, OUTPUT_DIR
from .video_engine import VideoEngine

logger = logging.getLogger(__name__)

# Ẩn cửa sổ console khi gọi FFmpeg trên Windows
_POPEN_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32" else {}
)


def _emit(event: str, data: dict) -> None:
    """Emit SocketIO an toàn (tránh import vòng tròn)."""
    try:
        from app import socketio  # type: ignore
        socketio.emit(event, data)
    except Exception as exc:
        logger.debug("[Session] emit(%s) thất bại: %s", event, exc)


class RecordingSession:
    """Quản lý một phiên ghi — audio trước, video sau, đồng bộ offset."""

    def __init__(self, display_index: int = 1, output_dir: Path = None):
        self._output_dir: Path = output_dir or OUTPUT_DIR
        self.session_id: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.audio = AudioEngine(self.session_id, output_dir=self._output_dir)
        self.video = VideoEngine(self.session_id, display_index=display_index, output_dir=self._output_dir)
        self.sync_offset_ms: float = 0.0
        self.is_recording: bool = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Khởi động audio trước, đợi 50ms, rồi đến video."""
        self.audio.start()          # 1. Audio trước
        time.sleep(0.05)            # 2. Ổn định 50ms
        self.video.start()          # 3. Video sau

        # 4. Đợi video bắt đầu ghi frame đầu tiên
        while self.video.start_timestamp is None:
            time.sleep(0.001)

        # 5. Tính độ lệch đồng bộ
        self.sync_offset_ms = (
            (self.video.start_timestamp - self.audio.start_timestamp) * 1000
        )
        self.is_recording = True
        logger.info(
            "[Session] Bắt đầu session=%s, display=%d, sync_offset=%.1fms",
            self.session_id,
            self.video.display_index,
            self.sync_offset_ms,
        )

    # ------------------------------------------------------------------
    def switch_display(self, new_index: int) -> None:
        """Chuyển màn hình ghi ngay trong khi đang ghi."""
        self.video.switch_display(new_index)
        logger.info("[Session] Chuyển màn hình ghi → #%d", new_index)

    # ------------------------------------------------------------------
    def stop(self, merge_audio: bool = True, convert_mp3: bool = True,
             mic_gain: float = 1.0, speaker_gain: float = 1.0,
             current_step_ref: dict = None) -> dict:
        """Dừng video → đợi 50ms → dừng audio → hậu xử lý."""
        video_paths = self.video.stop()   # 1. Video trước (list of segment paths)
        time.sleep(0.05)                  # 2. Đợi
        audio_paths = self.audio.stop()   # 3. Audio sau
        self.is_recording = False
        logger.info("[Session] Đã dừng session=%s", self.session_id)

        return _post_process(
            session_id=self.session_id,
            video_paths=video_paths,
            audio_paths=audio_paths,
            merge_audio=merge_audio,
            convert_mp3=convert_mp3,
            offset_ms=self.sync_offset_ms,
            mic_gain=mic_gain,
            speaker_gain=speaker_gain,
            current_step_ref=current_step_ref,
            output_dir=self._output_dir,
        )


# ══════════════════════════════════════════════════════════════════════
def _find_ffmpeg() -> str:
    """
    Tìm ffmpeg theo thứ tự ưu tiên:
    1. Bundled binary: <project_root>/bin/ffmpeg.exe  (Windows)
                       <project_root>/bin/ffmpeg       (Linux/Mac)
    2. Bundled binary khi đóng gói PyInstaller: sys._MEIPASS/bin/ffmpeg.exe
    3. ffmpeg trong PATH hệ thống (shutil.which)
    4. Các đường dẫn cứng thường gặp
    Raise FileNotFoundError nếu không tìm thấy ở đâu cả.
    """
    import sys
    import shutil

    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    # 1. Bundled trong project: thư mục bin/ cạnh app.py (hoặc cạnh .exe khi PyInstaller)
    candidates = []

    # Thư mục chứa file đang chạy (session.py → recorder/ → project root)
    project_root = Path(__file__).parent.parent
    candidates.append(project_root / "bin" / exe)

    # Khi build bằng PyInstaller --onefile, file được giải nén ra sys._MEIPASS
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "bin" / exe)

    # Khi build bằng PyInstaller --onedir
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "bin" / exe)

    for path in candidates:
        if path.exists():
            logger.info("[FFmpeg] Dùng bundled binary: %s", path)
            return str(path)

    # 2. PATH hệ thống
    found = shutil.which("ffmpeg")
    if found:
        logger.info("[FFmpeg] Dùng ffmpeg từ PATH: %s", found)
        return found

    # 3. Đường dẫn cứng fallback
    hardcoded = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for p in hardcoded:
        if Path(p).exists():
            logger.info("[FFmpeg] Dùng hardcoded path: %s", p)
            return p

    raise FileNotFoundError(
        "Không tìm thấy ffmpeg.\n"
        "Hãy đặt ffmpeg.exe vào thư mục bin/ trong project, "
        "hoặc cài ffmpeg và thêm vào PATH."
    )


def _get_media_duration(ffmpeg: str, file_path: str) -> float:
    """Lấy duration (giây) của file media bằng cách đọc header FFmpeg."""
    import re
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(file_path)],
            capture_output=True, timeout=30, **_POPEN_FLAGS
        )
        stderr = proc.stderr.decode(errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
        if m:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mn * 60 + s
    except Exception as exc:
        logger.debug("[Duration] Lỗi lấy duration %s: %s", file_path, exc)
    return 0.0


def _apply_drift_correction(
    ffmpeg: str,
    video_path: str,
    mic_wav: Optional[str],
    spk_wav: Optional[str],
    out_dir: Path,
) -> tuple[str, Optional[str], Optional[str]]:
    """
    Đo drift thực tế giữa video và audio rồi chia sai số 50-50:
    - Video: setpts để kéo dài/rút ngắn timestamp
    - Audio: atempo để tăng/giảm tốc độ phát
    Trả về (video_path, mic_wav, spk_wav) — có thể là path mới hoặc gốc nếu không cần sửa.
    """
    audio_ref = mic_wav if mic_wav and Path(mic_wav).exists() else spk_wav
    if not audio_ref:
        return video_path, mic_wav, spk_wav

    video_dur = _get_media_duration(ffmpeg, video_path)
    audio_dur = _get_media_duration(ffmpeg, audio_ref)

    if video_dur <= 0 or audio_dur <= 0:
        logger.warning("[DriftCorrection] Không lấy được duration, bỏ qua correction.")
        return video_path, mic_wav, spk_wav

    drift_ms = (video_dur - audio_dur) * 1000
    logger.info(
        "[DriftCorrection] Video=%.3fs, Audio=%.3fs, Drift=%.1fms",
        video_dur, audio_dur, drift_ms,
    )

    if abs(drift_ms) < 50:
        logger.debug("[DriftCorrection] Drift=%.1fms < 50ms, không cần sửa.", drift_ms)
        return video_path, mic_wav, spk_wav

    # Tính tốc độ mục tiêu (midpoint 50-50)
    t_mid = (video_dur + audio_dur) / 2.0
    pts_mult = t_mid / video_dur      # > 1 = slow video down, < 1 = speed up
    atempo = audio_dur / t_mid         # > 1 = speed audio up, < 1 = slow down

    # Clamp atempo vào [0.5, 2.0] (giới hạn của FFmpeg atempo filter)
    atempo = max(0.5, min(2.0, atempo))
    pts_mult = max(0.5, min(2.0, pts_mult))

    logger.info(
        "[DriftCorrection] Applying: video_pts_mult=%.4f, audio_atempo=%.4f",
        pts_mult, atempo,
    )

    stem_v = Path(video_path).stem
    video_corrected = str(out_dir / f"{stem_v}_dc.mp4")

    cmd_v = [
        ffmpeg, "-y", "-i", str(video_path),
        "-filter:v", f"setpts={pts_mult:.6f}*PTS",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
        "-an",
        video_corrected,
    ]
    proc_v = subprocess.run(cmd_v, capture_output=True, **_POPEN_FLAGS)
    if proc_v.returncode != 0:
        err = proc_v.stderr.decode(errors="replace")[-200:]
        logger.error("[DriftCorrection] Video correction failed: %s", err)
        return video_path, mic_wav, spk_wav

    def _correct_audio(wav_path: Optional[str]) -> Optional[str]:
        if not wav_path or not Path(wav_path).exists():
            return wav_path
        stem_a = Path(wav_path).stem
        corrected = str(out_dir / f"{stem_a}_dc.wav")
        cmd_a = [
            ffmpeg, "-y", "-i", str(wav_path),
            "-filter:a", f"atempo={atempo:.4f}",
            "-c:a", "pcm_s16le",
            corrected,
        ]
        proc_a = subprocess.run(cmd_a, capture_output=True, **_POPEN_FLAGS)
        if proc_a.returncode != 0:
            err = proc_a.stderr.decode(errors="replace")[-200:]
            logger.error("[DriftCorrection] Audio correction failed: %s", err)
            return wav_path
        return corrected

    new_mic = _correct_audio(mic_wav)
    new_spk = _correct_audio(spk_wav)

    logger.info("[DriftCorrection] ✅ Correction applied: drift=%.1fms", drift_ms)
    return video_corrected, new_mic, new_spk


def _concat_video_segments(ffmpeg: str, session_id: str, paths: list[str], output_dir: Path = None) -> str:
    """Nối nhiều segment video thành một file bằng FFmpeg concat demuxer."""
    out_dir = output_dir or OUTPUT_DIR
    out_path = str(out_dir / f"{session_id}_video.mp4")
    list_file = out_dir / f"{session_id}_concat.txt"
    try:
        with list_file.open("w", encoding="utf-8") as f:
            for p in paths:
                # Escape single quotes in path for ffconcat format
                escaped = p.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        cmd = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, **_POPEN_FLAGS)
        if proc.returncode != 0:
            logger.error(
                "[PostProcess] concat lỗi:\n%s", proc.stderr.decode(errors="replace")
            )
            return paths[0]  # fallback: chỉ dùng segment đầu

        # Xoá các segment gốc
        for p in paths:
            try:
                Path(p).unlink()
            except Exception:
                pass
        return out_path
    finally:
        try:
            list_file.unlink()
        except Exception:
            pass


def _post_process(
    session_id: str,
    video_paths: list[str],
    audio_paths: dict,
    merge_audio: bool,
    convert_mp3: bool,
    offset_ms: float,
    mic_gain: float = 1.0,
    speaker_gain: float = 1.0,
    current_step_ref: dict = None,
    output_dir: Path = None,
) -> dict:
    if current_step_ref is None:
        current_step_ref = {"step": 3, "total": 3}

    out_dir = output_dir or OUTPUT_DIR
    result = {"video": None, "audio_mp3": None, "merged": None}
    ffmpeg = _find_ffmpeg()
    mic_wav: Optional[str] = audio_paths.get("mic")
    spk_wav: Optional[str] = audio_paths.get("speaker")
    has_mic = mic_wav and Path(mic_wav).exists()
    has_spk = spk_wav and Path(spk_wav).exists()
    offset_s = offset_ms / 1000.0

    # Các mức volume thực tế sau khi nhân gain
    mic_vol = round(0.7 * mic_gain, 4)
    spk_vol = round(1.0 * speaker_gain, 4)

    # ── Bước 0: Nối segment nếu có chuyển màn hình ────────────────────
    valid_paths = [p for p in video_paths if Path(p).exists()]
    if len(valid_paths) > 1:
        current_step_ref["step"] += 1
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": f"🔗 Đang nối {len(valid_paths)} segment video...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        video_path = _concat_video_segments(ffmpeg, session_id, valid_paths, out_dir)
    elif valid_paths:
        current_step_ref["step"] += 1
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "💾 Đang lưu và hoàn thiện file video...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        canonical = str(out_dir / f"{session_id}_video.mp4")
        if valid_paths[0] != canonical:
            Path(valid_paths[0]).rename(canonical)
        video_path = canonical
    else:
        logger.error("[PostProcess] Không có file video nào tồn tại.")
        _emit("job_progress", {"job_id": session_id, "stage": "error",
                                "message": "❌ Không có file video.", "log_type": "error",
                                "current_step": 0, "total_steps": 0})
        return result

    # ── Drift Correction: Đo và chia sai số AV 50-50 ─────────────────
    if merge_audio and (has_mic or has_spk):
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "⚖️ Đang đo và hiệu chỉnh AV drift...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        orig_mic, orig_spk = mic_wav, spk_wav
        video_path, mic_wav, spk_wav = _apply_drift_correction(
            ffmpeg, video_path,
            mic_wav if has_mic else None,
            spk_wav if has_spk else None,
            out_dir,
        )
        has_mic = mic_wav is not None and Path(mic_wav).exists()
        has_spk = spk_wav is not None and Path(spk_wav).exists()

    # ── Build lệnh ghép audio vào video ───────────────────────────────
    merged_path: Optional[str] = None
    cmd_merge: Optional[list] = None
    if merge_audio and (has_mic or has_spk):
        current_step_ref["step"] += 1
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "🎬 Đang trộn audio vào video...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        merged_path = str(out_dir / f"{session_id}_final.mp4")
        cmd_merge = [ffmpeg, "-y", "-i", video_path]
        if has_mic:
            if offset_s > 0:
                cmd_merge += ["-ss", f"{offset_s:.4f}", "-i", mic_wav]
            elif offset_s < 0:
                cmd_merge += ["-itsoffset", f"{-offset_s:.4f}", "-i", mic_wav]
            else:
                cmd_merge += ["-i", mic_wav]
        if has_spk:
            if offset_s > 0:
                cmd_merge += ["-ss", f"{offset_s:.4f}", "-i", spk_wav]
            elif offset_s < 0:
                cmd_merge += ["-itsoffset", f"{-offset_s:.4f}", "-i", spk_wav]
            else:
                cmd_merge += ["-i", spk_wav]
        if has_mic and has_spk:
            cmd_merge += [
                "-filter_complex",
                f"[1:a]aresample=async=1000,volume={mic_vol}[m];[2:a]aresample=async=1000,volume={spk_vol}[s];[m][s]amix=inputs=2:duration=longest:dropout_transition=0[a]",
                "-map", "0:v", "-map", "[a]",
            ]
        elif has_mic:
            cmd_merge += ["-filter_complex", f"[1:a]aresample=async=1000,volume={mic_vol}[m]", "-map", "0:v", "-map", "[m]"]
        else:
            cmd_merge += ["-filter_complex", f"[1:a]aresample=async=1000,volume={spk_vol}[s]", "-map", "0:v", "-map", "[s]"]
        cmd_merge += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            merged_path,
        ]

    # ── Build lệnh chuyển audio sang MP3 ──────────────────────────────
    mp3_path: Optional[str] = None
    cmd_mp3: Optional[list] = None
    if convert_mp3 and (has_mic or has_spk):
        current_step_ref["step"] += 1
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "🎵 Đang chuyển đổi sang MP3...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        mp3_path = str(out_dir / f"{session_id}_audio.mp3")
        cmd_mp3 = [ffmpeg, "-y"]
        if has_mic:
            cmd_mp3 += ["-i", mic_wav]
        if has_spk:
            cmd_mp3 += ["-i", spk_wav]
        if has_mic and has_spk:
            cmd_mp3 += [
                "-filter_complex",
                f"[0:a]volume={mic_vol}[m];[1:a]volume={spk_vol}[s];[m][s]amix=inputs=2:duration=longest:dropout_transition=0[a]",
                "-map", "[a]",
            ]
        elif has_mic:
            cmd_mp3 += ["-filter_complex", f"[0:a]volume={mic_vol}[a]", "-map", "[a]"]
        else:
            cmd_mp3 += ["-filter_complex", f"[0:a]volume={spk_vol}[a]", "-map", "[a]"]
        cmd_mp3 += ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", mp3_path]

    # ── Thực thi FFmpeg: song song nếu cả hai, tuần tự nếu một ────────
    def _run_popen(cmd: list) -> tuple[int, bytes]:
        """Chạy FFmpeg, trả về (returncode, stderr_bytes). An toàn với pipe buffer."""
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_POPEN_FLAGS
        )
        _, err = proc.communicate()
        return proc.returncode, err or b""

    def _emit_ffmpeg_error(err_bytes: bytes, step: dict) -> None:
        err_msg = err_bytes.decode(errors="replace")[-300:].strip()
        _emit("job_progress", {
            "job_id": session_id, "stage": "error",
            "message": f"❌ FFmpeg lỗi:\n{err_msg}", "log_type": "error",
            "current_step": step["step"], "total_steps": step["total"],
        })

    if cmd_merge and cmd_mp3:
        # Khởi chạy song song — cả hai đọc/ghi file khác nhau
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "⚙️ FFmpeg đang chạy song song (merge + mp3)...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_m = pool.submit(_run_popen, cmd_merge)
            fut_p = pool.submit(_run_popen, cmd_mp3)
            rc_m, err_m = fut_m.result()
            rc_p, err_p = fut_p.result()

        if rc_m == 0:
            result["merged"] = merged_path
            _emit("job_progress", {"job_id": session_id, "stage": "info",
                                    "message": "✅ Ghép video hoàn tất!", "log_type": "success",
                                    "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"]})
        else:
            logger.error("[PostProcess] ffmpeg merge lỗi:\n%s", err_m.decode(errors="replace"))
            _emit_ffmpeg_error(err_m, current_step_ref)
            merged_path = None

        if rc_p == 0:
            result["audio_mp3"] = mp3_path
            _emit("job_progress", {"job_id": session_id, "stage": "info",
                                    "message": "✅ Chuyển MP3 hoàn tất!", "log_type": "success",
                                    "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"]})
        else:
            logger.error("[PostProcess] ffmpeg mp3 lỗi:\n%s", err_p.decode(errors="replace"))
            _emit_ffmpeg_error(err_p, current_step_ref)

    elif cmd_merge:
        _emit("job_progress", {"job_id": session_id, "stage": "info",
                                "message": "⚙️ FFmpeg đang ghép...", "log_type": "info",
                                "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"]})
        rc_m, err_m = _run_popen(cmd_merge)
        if rc_m == 0:
            result["merged"] = merged_path
            _emit("job_progress", {"job_id": session_id, "stage": "info",
                                    "message": "✅ Ghép video hoàn tất!", "log_type": "success",
                                    "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"]})
        else:
            logger.error("[PostProcess] ffmpeg merge lỗi:\n%s", err_m.decode(errors="replace"))
            _emit_ffmpeg_error(err_m, current_step_ref)
            merged_path = None

    elif cmd_mp3:
        rc_p, err_p = _run_popen(cmd_mp3)
        if rc_p == 0:
            result["audio_mp3"] = mp3_path
            _emit("job_progress", {"job_id": session_id, "stage": "info",
                                    "message": "✅ Chuyển MP3 hoàn tất!", "log_type": "success",
                                    "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"]})
        else:
            logger.error("[PostProcess] ffmpeg mp3 lỗi:\n%s", err_p.decode(errors="replace"))
            _emit_ffmpeg_error(err_p, current_step_ref)

    # ── Bước C: Dọn dẹp ───────────────────────────────────────────────
    for wav in [mic_wav, spk_wav]:
        if wav and Path(wav).exists():
            try:
                Path(wav).unlink()
            except Exception:
                pass

    if merged_path and Path(merged_path).exists() and Path(video_path).exists():
        try:
            Path(video_path).unlink()
        except Exception:
            pass
    else:
        result["video"] = video_path

    _emit("job_progress", {"job_id": session_id, "stage": "done",
                            "message": "✨ Xử lý hoàn tất!", "log_type": "success",
                            "current_step": current_step_ref["total"], "total_steps": current_step_ref["total"]})
    _emit("files_updated", {})
    logger.info("[PostProcess] Kết quả: %s", result)
    return result
