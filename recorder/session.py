"""
session.py — v2: Điều phối RecordingSession với hỗ trợ chọn màn hình.
"""
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

    def __init__(self, display_index: int = 1):
        self.session_id: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.audio = AudioEngine(self.session_id)
        self.video = VideoEngine(self.session_id, display_index=display_index)
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
             mic_gain: float = 1.0, speaker_gain: float = 1.0) -> dict:
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
        )


# ══════════════════════════════════════════════════════════════════════
def _find_ffmpeg() -> str:
    import shutil
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    common = [
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for p in common:
        if Path(p).exists():
            return p
    raise FileNotFoundError("Không tìm thấy ffmpeg — hãy cài đặt và thêm vào PATH.")


def _concat_video_segments(ffmpeg: str, session_id: str, paths: list[str]) -> str:
    """Nối nhiều segment video thành một file bằng FFmpeg concat demuxer."""
    out_path = str(OUTPUT_DIR / f"{session_id}_video.mp4")
    list_file = OUTPUT_DIR / f"{session_id}_concat.txt"
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
) -> dict:
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
        _emit("job_progress", {
            "job_id": session_id, "stage": "concat",
            "message": f"Đang nối {len(valid_paths)} segment video...", "percent": 5,
        })
        video_path = _concat_video_segments(ffmpeg, session_id, valid_paths)
    elif valid_paths:
        _emit("job_progress", {
            "job_id": session_id, "stage": "saving",
            "message": "Đang lưu và hoàn thiện file video...", "percent": 10,
        })
        # Rename single segment to canonical name
        canonical = str(OUTPUT_DIR / f"{session_id}_video.mp4")
        if valid_paths[0] != canonical:
            Path(valid_paths[0]).rename(canonical)
        video_path = canonical
    else:
        logger.error("[PostProcess] Không có file video nào tồn tại.")
        _emit("job_progress", {"job_id": session_id, "stage": "error",
                                "message": "Không có file video.", "percent": 0})
        return result

    # ── Bước A: Ghép audio vào video ──────────────────────────────────
    merged_path: Optional[str] = None
    if merge_audio and (has_mic or has_spk):
        _emit("job_progress", {
            "job_id": session_id, "stage": "merging",
            "message": "Đang trộn audio vào video...", "percent": 10
        })
        merged_path = str(OUTPUT_DIR / f"{session_id}_final.mp4")
        cmd = [ffmpeg, "-y", "-i", video_path]

        # offset_s > 0: audio started before video → trim leading audio with -ss
        # offset_s < 0: audio started after video → delay audio with -itsoffset
        if has_mic:
            if offset_s > 0:
                cmd += ["-ss", f"{offset_s:.4f}", "-i", mic_wav]
            elif offset_s < 0:
                cmd += ["-itsoffset", f"{-offset_s:.4f}", "-i", mic_wav]
            else:
                cmd += ["-i", mic_wav]
        if has_spk:
            if offset_s > 0:
                cmd += ["-ss", f"{offset_s:.4f}", "-i", spk_wav]
            elif offset_s < 0:
                cmd += ["-itsoffset", f"{-offset_s:.4f}", "-i", spk_wav]
            else:
                cmd += ["-i", spk_wav]

        if has_mic and has_spk:
            cmd += [
                "-filter_complex",
                f"[1:a]aresample=async=1000,volume={mic_vol}[m];[2:a]aresample=async=1000,volume={spk_vol}[s];[m][s]amix=inputs=2:duration=longest:dropout_transition=0[a]",
                "-map", "0:v", "-map", "[a]",
            ]
        elif has_mic:
            cmd += ["-filter_complex", f"[1:a]aresample=async=1000,volume={mic_vol}[m]", "-map", "0:v", "-map", "[m]"]
        else:
            cmd += ["-filter_complex", f"[1:a]aresample=async=1000,volume={spk_vol}[s]", "-map", "0:v", "-map", "[s]"]

        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            merged_path,
        ]

        _emit("job_progress", {"job_id": session_id, "stage": "merging",
                                "message": "FFmpeg đang ghép...", "percent": 30})
        proc = subprocess.run(cmd, capture_output=True, **_POPEN_FLAGS)
        if proc.returncode == 0:
            result["merged"] = merged_path
            _emit("job_progress", {"job_id": session_id, "stage": "merging",
                                    "message": "Ghép video hoàn tất!", "percent": 60})
        else:
            logger.error("[PostProcess] ffmpeg merge lỗi:\n%s", proc.stderr.decode(errors="replace"))
            merged_path = None

    # ── Bước B: Chuyển audio sang MP3 ─────────────────────────────────
    if convert_mp3 and (has_mic or has_spk):
        _emit("job_progress", {"job_id": session_id, "stage": "converting",
                                "message": "Đang chuyển đổi sang MP3...", "percent": 70})
        mp3_path = str(OUTPUT_DIR / f"{session_id}_audio.mp3")
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
        proc_mp3 = subprocess.run(cmd_mp3, capture_output=True, **_POPEN_FLAGS)
        if proc_mp3.returncode == 0:
            result["audio_mp3"] = mp3_path
            _emit("job_progress", {"job_id": session_id, "stage": "converting",
                                    "message": "Chuyển MP3 hoàn tất!", "percent": 90})
        else:
            logger.error("[PostProcess] ffmpeg mp3 lỗi:\n%s", proc_mp3.stderr.decode(errors="replace"))

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
                            "message": "Xử lý hoàn tất!", "percent": 100})
    _emit("files_updated", {})
    logger.info("[PostProcess] Kết quả: %s", result)
    return result
