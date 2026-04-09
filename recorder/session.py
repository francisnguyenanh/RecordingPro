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


SEGMENT_DURATION_SECONDS = 300


class RecordingSession:
    """Quản lý một phiên ghi — audio trước, video sau, đồng bộ offset."""

    def __init__(self, display_index: int = 1, output_dir: Path = None):
        self._output_dir: Path = output_dir or OUTPUT_DIR
        self.session_id: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.audio = AudioEngine(self.session_id, output_dir=self._output_dir)
        self.video = VideoEngine(self.session_id, display_index=display_index, output_dir=self._output_dir)
        self.sync_offset_ms: float = 0.0
        self.is_recording: bool = False
        
        self.chunk_idx = 0
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ChunkProc")
        self.futures = []
        self._stop_event = threading.Event()
        self._timer_thread: Optional[threading.Thread] = None

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
        self._stop_event.clear()
        
        self._timer_thread = threading.Thread(target=self._segment_timer, daemon=True, name="session-timer")
        self._timer_thread.start()
        
        logger.info(
            "[Session] Bắt đầu session=%s, display=%d, sync_offset=%.1fms",
            self.session_id,
            self.video.display_index,
            self.sync_offset_ms,
        )

    # ------------------------------------------------------------------
    def _segment_timer(self) -> None:
        """Thread liên tục kiểm tra theo SEGMENT_DURATION_SECONDS để trigger rollover."""
        while not self._stop_event.wait(SEGMENT_DURATION_SECONDS):
            if not self.is_recording:
                break
            self._do_rollover()

    # ------------------------------------------------------------------
    def _do_rollover(self) -> None:
        """Thực hiện lưu đoạn hiện tại và submit cho ThreadPoolExecutor gộp lại ngay lập tức."""
        logger.info("[Session] Rollover chunk %d", self.chunk_idx)
        vi = self.video.roll_segment()
        time.sleep(0.05)
        ai = self.audio.roll_segment()
        
        chunk_offset_ms = self.sync_offset_ms
        
        # update sync offset cho đoạn mới
        if self.video.start_timestamp and self.audio.start_timestamp:
            self.sync_offset_ms = (self.video.start_timestamp - self.audio.start_timestamp) * 1000

        f = self.executor.submit(
            _process_chunk, self.session_id, self.chunk_idx, vi, ai, chunk_offset_ms, self._output_dir
        )
        self.futures.append(f)
        self.chunk_idx += 1

    # ------------------------------------------------------------------
    def switch_display(self, new_index: int) -> None:
        """Chuyển màn hình ghi. Kích hoạt rollover sớm để tách segment."""
        logger.info("[Session] Chuyển màn hình ghi → #%d", new_index)
        self.video.switch_display(new_index)
        self._do_rollover()

    # ------------------------------------------------------------------
    def stop(self, merge_audio: bool = True, convert_mp3: bool = True,
             mic_gain: float = 1.0, speaker_gain: float = 1.0,
             current_step_ref: dict = None) -> dict:
        """Dừng ghi âm/video và join các worker trả về kết quả cuối."""
        self.is_recording = False
        self._stop_event.set()
        if self._timer_thread:
            self._timer_thread.join(timeout=3)

        vi = self.video.stop()   # 1. Video dừng
        time.sleep(0.05)
        ai = self.audio.stop()   # 2. Audio dừng
        
        logger.info("[Session] Đã dừng session=%s", self.session_id)

        # Queue chunk cuối
        if vi and ai:
            f = self.executor.submit(
                _process_chunk, self.session_id, self.chunk_idx, vi, ai, self.sync_offset_ms, self._output_dir
            )
            self.futures.append(f)

        res = _final_post_process(
            session_id=self.session_id,
            futures=self.futures,
            merge_audio=merge_audio,
            convert_mp3=convert_mp3,
            mic_gain=mic_gain,
            speaker_gain=speaker_gain,
            current_step_ref=current_step_ref,
            output_dir=self._output_dir,
        )
        self.executor.shutdown(wait=False)
        return res


# ══════════════════════════════════════════════════════════════════════
def _find_ffmpeg() -> str:
    """Tìm ffmpeg theo thứ tự ưu tiên."""
    import sys
    import shutil

    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidates = []
    project_root = Path(__file__).parent.parent
    candidates.append(project_root / "bin" / exe)

    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "bin" / exe)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "bin" / exe)

    for path in candidates:
        if path.exists():
            return str(path)

    found = shutil.which("ffmpeg")
    if found:
        return found

    hardcoded = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for p in hardcoded:
        if Path(p).exists():
            return p

    raise FileNotFoundError(
        "Không tìm thấy ffmpeg.\n"
        "Hãy đặt ffmpeg.exe vào thư mục bin/ trong project, "
        "hoặc cài ffmpeg và thêm vào PATH."
    )


def _get_wav_duration(wav_path: str) -> float:
    """Lấy duration (giây) của WAV bằng cách đọc header."""
    import wave
    try:
        with wave.open(str(wav_path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception as exc:
        logger.debug("[WAVDuration] %s: %s", wav_path, exc)
    return 0.0


def _process_chunk(session_id: str, chunk_idx: int, vi: dict, ai: dict, offset_ms: float, output_dir: Path) -> str | None:
    """Xử lý đồng bộ ngay lập tức 1 segment (video + audio) bằng ffmpeg -> trả về chunk path."""
    ffmpeg = _find_ffmpeg()
    video_path = vi.get("video")
    video_frame_count = vi.get("frame_count", 0)
    if not video_path or not Path(video_path).exists():
        return None

    mic_wav = ai.get("mic")
    spk_wav = ai.get("speaker")
    has_mic = mic_wav and Path(mic_wav).exists()
    has_spk = spk_wav and Path(spk_wav).exists()
    chunk_path = str(output_dir / f"{session_id}_chunk_{chunk_idx}.mp4")

    _emit("job_progress", {
        "job_id": session_id, "stage": "info",
        "message": f"⚙️ Đang xử lý phân đoạn {chunk_idx + 1}...", "log_type": "info",
        "current_step": 0, "total_steps": 0,
    })

    actual_fps = 30.0
    if video_frame_count > 0 and (has_mic or has_spk):
        audio_ref = mic_wav if has_mic else spk_wav
        if audio_ref and Path(audio_ref).exists():
            wav_dur = _get_wav_duration(audio_ref)
            if wav_dur > 1.0:
                actual_fps = max(15.0, min(60.0, video_frame_count / wav_dur))
                
    offset_s = offset_ms / 1000.0
    cmd = [ffmpeg, "-y", "-r", f"{actual_fps:.4f}", "-i", video_path]
    
    if has_mic:
        if offset_s > 0: cmd += ["-ss", f"{offset_s:.4f}", "-i", mic_wav]
        elif offset_s < 0: cmd += ["-itsoffset", f"{-offset_s:.4f}", "-i", mic_wav]
        else: cmd += ["-i", mic_wav]
    if has_spk:
        if offset_s > 0: cmd += ["-ss", f"{offset_s:.4f}", "-i", spk_wav]
        elif offset_s < 0: cmd += ["-itsoffset", f"{-offset_s:.4f}", "-i", spk_wav]
        else: cmd += ["-i", spk_wav]

    if has_mic and has_spk:
        cmd += [
            "-filter_complex",
            "[1:a]aresample=async=1000,volume=1.0[m];[2:a]aresample=async=1000,volume=1.0[s];[m][s]amix=inputs=2:duration=longest:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
        ]
    elif has_mic:
        cmd += ["-filter_complex", "[1:a]aresample=async=1000,volume=1.0[m]", "-map", "0:v", "-map", "[m]"]
    elif has_spk:
        cmd += ["-filter_complex", "[1:a]aresample=async=1000,volume=1.0[s]", "-map", "0:v", "-map", "[s]"]
    else:
        cmd += ["-c:v", "copy"]

    if has_mic or has_spk:
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", chunk_path
        ]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30", chunk_path
        ]

    proc = subprocess.run(cmd, capture_output=True, **_POPEN_FLAGS)
    if proc.returncode != 0:
        logger.error("[ChunkProc] Chunk %d lỗi:\n%s", chunk_idx, proc.stderr.decode(errors="replace"))
        return None

    # Dọn dẹp chunk raw files
    for p in [video_path, mic_wav, spk_wav]:
        if p and Path(p).exists():
            try:
                Path(p).unlink()
            except Exception:
                pass

    return chunk_path


def _final_post_process(
    session_id: str,
    futures: list,
    merge_audio: bool,
    convert_mp3: bool,
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

    _emit("job_progress", {
        "job_id": session_id, "stage": "info",
        "message": "⏳ Đang đợi xử lý các phân đoạn...", "log_type": "info",
        "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
    })

    chunk_paths = []
    for f in concurrent.futures.as_completed(futures):
        res = f.result()
        if res and Path(res).exists():
            chunk_paths.append(res)
            
    import re
    def get_chunk_idx(p):
        m = re.search(r'_chunk_(\d+)\.mp4$', str(p))
        return int(m.group(1)) if m else 0
    chunk_paths.sort(key=get_chunk_idx)
    
    if not chunk_paths:
        _emit("job_progress", {"job_id": session_id, "stage": "error",
                                "message": "❌ Không có file video nào được xử lý.", "log_type": "error",
                                "current_step": 0, "total_steps": 0})
        logger.error("[PostProcess] Không có chunk.")
        return result

    current_step_ref["step"] += 1
    _emit("job_progress", {
        "job_id": session_id, "stage": "info",
        "message": f"🔗 Đang nối {len(chunk_paths)} phân đoạn lại...", "log_type": "info",
        "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
    })

    final_mp4 = str(out_dir / f"{session_id}_final.mp4")
    list_file = out_dir / f"{session_id}_concat.txt"
    try:
        with list_file.open("w", encoding="utf-8") as f:
            for p in chunk_paths:
                escaped = p.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", final_mp4]
        proc = subprocess.run(cmd, capture_output=True, **_POPEN_FLAGS)
        if proc.returncode != 0:
            logger.error("[PostProcess] concat lỗi:\n%s", proc.stderr.decode(errors="replace"))
        else:
            result["merged"] = final_mp4
            result["video"] = final_mp4
            for p in chunk_paths:
                try: Path(p).unlink()
                except: pass
    finally:
        try: list_file.unlink()
        except: pass

    # Optional: MP3 conversion from the final mp4
    if convert_mp3 and result["merged"]:
        current_step_ref["step"] += 1
        _emit("job_progress", {
            "job_id": session_id, "stage": "info",
            "message": "🎵 Đang xuất ra file MP3...", "log_type": "info",
            "current_step": current_step_ref["step"], "total_steps": current_step_ref["total"],
        })
        mp3_path = str(out_dir / f"{session_id}_audio.mp3")
        # Extract audio
        cmd_mp3 = [
            ffmpeg, "-y", "-i", final_mp4,
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", mp3_path
        ]
        proc = subprocess.run(cmd_mp3, capture_output=True, **_POPEN_FLAGS)
        if proc.returncode == 0:
            result["audio_mp3"] = mp3_path

    _emit("job_progress", {"job_id": session_id, "stage": "done",
                            "message": "✨ Xử lý hoàn tất!", "log_type": "success",
                            "current_step": current_step_ref["total"], "total_steps": current_step_ref["total"]})
    _emit("files_updated", {})
    return result
