"""
audio_engine.py — Ghi âm Mic và Loopback (speaker) đồng thời.
"""
import logging
import threading
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
BLOCKSIZE = 1024
OUTPUT_DIR = Path.home() / "Videos" / "ScreenCapturePro"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FLUSH_INTERVAL_SECONDS = 60  # Định kỳ flush xuống đĩa mỗi 60 giây


def _rms_level(data: np.ndarray) -> float:
    """Tính RMS (0.0–1.0) từ mảng int16."""
    if data.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
    # int16 max = 32768; nhân 10 để khuếch đại, clamp 1.0
    return min(rms / 32768.0 * 10.0, 1.0)


class AudioEngine:
    """Ghi mic + loopback speaker, tính mức tín hiệu real-time."""

    def __init__(self, session_id: str, output_dir: Path = None, mic_device: int = None):
        self.session_id = session_id
        self._output_dir: Path = output_dir or OUTPUT_DIR
        self._mic_device = mic_device  # P3: None = default, int = specific device
        self.mic_frames: list[bytes] = []
        self.speaker_frames: list[bytes] = []
        self.mic_level: float = 0.0
        self.speaker_level: float = 0.0
        self.start_timestamp: float | None = None
        self.recording = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        # Thông số thực tế của luồng loopback (có thể khác SAMPLE_RATE/CHANNELS)
        self._speaker_sr: int = SAMPLE_RATE
        self._speaker_ch: int = CHANNELS
        # Flush-to-disk state (tránh tích lũy RAM không giới hạn)
        self._mic_wf: wave.Wave_write | None = None
        self._spk_wf: wave.Wave_write | None = None
        self._mic_wav_path: Path | None = None
        self._spk_wav_path: Path | None = None
        self._flush_lock = threading.Lock()
        self._flush_stop = threading.Event()
        self.seg_idx = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        import time
        self.recording = True
        self.seg_idx = 0
        self.start_timestamp = time.perf_counter()
        self._mic_wav_path = self._output_dir / f"{self.session_id}_mic_{self.seg_idx}.wav"
        self._spk_wav_path = self._output_dir / f"{self.session_id}_speaker_{self.seg_idx}.wav"
        self._flush_stop.clear()

        t_mic = threading.Thread(target=self._record_mic, daemon=True, name="mic-recorder")
        t_spk = threading.Thread(target=self._record_loopback, daemon=True, name="spk-recorder")
        t_flush = threading.Thread(target=self._flush_loop, daemon=True, name="audio-flush")
        self._threads = [t_mic, t_spk, t_flush]
        t_mic.start()
        t_spk.start()
        t_flush.start()
        logger.info("[AudioEngine] Đã bắt đầu ghi âm (session=%s)", self.session_id)

    # ------------------------------------------------------------------
    def roll_segment(self) -> dict:
        """Kế thúc segment hiện tại, chuyển sang đoạn mới và tự động chuyển wave file."""
        with self._flush_lock:
            mic_data = self.mic_frames
            self.mic_frames = []
            spk_data = self.speaker_frames
            self.speaker_frames = []

            if mic_data and self._mic_wav_path is not None:
                if self._mic_wf is None:
                    self._mic_wf = wave.open(str(self._mic_wav_path), "wb")
                    self._mic_wf.setnchannels(CHANNELS)
                    self._mic_wf.setsampwidth(2)
                    self._mic_wf.setframerate(SAMPLE_RATE)
                self._mic_wf.writeframes(b"".join(mic_data))
            if spk_data and self._spk_wav_path is not None:
                if self._spk_wf is None:
                    self._spk_wf = wave.open(str(self._spk_wav_path), "wb")
                    self._spk_wf.setnchannels(self._speaker_ch)
                    self._spk_wf.setsampwidth(2)
                    self._spk_wf.setframerate(self._speaker_sr)
                self._spk_wf.writeframes(b"".join(spk_data))

            old_mic = str(self._mic_wav_path) if self._mic_wav_path else None
            old_spk = str(self._spk_wav_path) if self._spk_wav_path else None

            if self._mic_wf is not None:
                self._mic_wf.close()
                self._mic_wf = None
            if self._spk_wf is not None:
                self._spk_wf.close()
                self._spk_wf = None

            self.seg_idx += 1
            self._mic_wav_path = self._output_dir / f"{self.session_id}_mic_{self.seg_idx}.wav"
            self._spk_wav_path = self._output_dir / f"{self.session_id}_speaker_{self.seg_idx}.wav"
            
            import time
            self.start_timestamp = time.perf_counter()

            return {"mic": old_mic, "speaker": old_spk}

    # ------------------------------------------------------------------
    def _flush_loop(self) -> None:
        """Định kỳ flush dữ liệu âm thanh xuống đĩa để giới hạn RAM."""
        while not self._flush_stop.wait(FLUSH_INTERVAL_SECONDS):
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """Ghi các frame đang chờ xuống file WAV. Giữ wave.Wave_write mở giữa các lần flush."""
        with self._flush_lock:
            mic_data = self.mic_frames
            self.mic_frames = []
            spk_data = self.speaker_frames
            self.speaker_frames = []

            if mic_data and self._mic_wav_path is not None:
                if self._mic_wf is None:
                    self._mic_wf = wave.open(str(self._mic_wav_path), "wb")
                    self._mic_wf.setnchannels(CHANNELS)
                    self._mic_wf.setsampwidth(2)
                    self._mic_wf.setframerate(SAMPLE_RATE)
                self._mic_wf.writeframes(b"".join(mic_data))

            if spk_data and self._spk_wav_path is not None:
                if self._spk_wf is None:
                    self._spk_wf = wave.open(str(self._spk_wav_path), "wb")
                    self._spk_wf.setnchannels(self._speaker_ch)
                    self._spk_wf.setsampwidth(2)
                    self._spk_wf.setframerate(self._speaker_sr)
                self._spk_wf.writeframes(b"".join(spk_data))

    # ------------------------------------------------------------------
    def _record_mic(self) -> None:
        try:
            import sounddevice as sd

            def callback(indata, frames, time_info, status):
                if status:
                    logger.warning("[Mic] %s", status)
                if not self.recording:
                    raise sd.CallbackAbort
                chunk = indata.copy()
                self.mic_frames.append(chunk.tobytes())
                self.mic_level = _rms_level(chunk)

            device_kwarg = {} if self._mic_device is None else {"device": self._mic_device}
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCKSIZE,
                callback=callback,
                **device_kwarg,
            ):
                while self.recording:
                    import time
                    time.sleep(0.05)
        except Exception as exc:
            logger.error("[AudioEngine] Lỗi ghi mic: %s", exc)

    # ------------------------------------------------------------------
    def _record_loopback(self) -> None:
        """Thử PyAudioWPatch trước, sau đó soundcard, nếu không được thì bỏ qua."""
        if self._try_wasapi_loopback():
            return
        if self._try_soundcard_loopback():
            return
        logger.warning("[AudioEngine] Không tìm thấy thiết bị loopback — "
                       "audio của members trong call sẽ không được ghi. "
                       "Hãy cài PyAudioWPatch hoặc soundcard.")

    def _try_wasapi_loopback(self) -> bool:
        try:
            import pyaudiowpatch as pyaudio  # type: ignore
            import time

            pa = pyaudio.PyAudio()
            try:
                # Tìm thiết bị loopback bằng generator API của PyAudioWPatch
                loopback_device = None
                try:
                    wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                    default_output_idx = wasapi_info["defaultOutputDevice"]
                    default_speakers = pa.get_device_info_by_index(default_output_idx)
                    default_name = default_speakers.get("name", "")

                    # Ưu tiên dùng generator API (chính xác hơn)
                    if hasattr(pa, "get_loopback_device_info_generator"):
                        for dev in pa.get_loopback_device_info_generator():
                            if default_name in dev.get("name", ""):
                                loopback_device = dev
                                break
                        # fallback: lấy loopback đầu tiên
                        if loopback_device is None:
                            for dev in pa.get_loopback_device_info_generator():
                                loopback_device = dev
                                break
                    else:
                        # fallback manual search
                        for i in range(pa.get_device_count()):
                            dev = pa.get_device_info_by_index(i)
                            if dev.get("isLoopbackDevice"):
                                if default_name and default_name in dev.get("name", ""):
                                    loopback_device = dev
                                    break
                        if loopback_device is None:
                            for i in range(pa.get_device_count()):
                                dev = pa.get_device_info_by_index(i)
                                if dev.get("isLoopbackDevice"):
                                    loopback_device = dev
                                    break
                except Exception as inner:
                    logger.debug("[AudioEngine] WASAPI query lỗi: %s", inner)

                if loopback_device is None:
                    logger.warning("[AudioEngine] Không tìm thấy WASAPI loopback device.")
                    return False

                sr = int(loopback_device["defaultSampleRate"])
                ch = int(loopback_device.get("maxInputChannels", 0))
                if ch == 0:
                    ch = CHANNELS  # fallback khi device không báo đúng
                ch = min(ch, CHANNELS)
                logger.info("[AudioEngine] Loopback device: %s | sr=%d, ch=%d",
                            loopback_device.get('name'), sr, ch)
                self._speaker_sr = sr
                self._speaker_ch = ch

                def callback(in_data, frame_count, time_info, status):
                    if not self.recording:
                        return (None, pyaudio.paAbort)
                    arr = np.frombuffer(in_data, dtype=np.int16)
                    self.speaker_frames.append(in_data)
                    self.speaker_level = _rms_level(arr)
                    return (None, pyaudio.paContinue)

                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=sr,
                    input=True,
                    input_device_index=int(loopback_device["index"]),
                    frames_per_buffer=BLOCKSIZE,
                    stream_callback=callback,
                )
                stream.start_stream()
                while self.recording and stream.is_active():
                    time.sleep(0.05)
                stream.stop_stream()
                stream.close()
                return True
            finally:
                pa.terminate()
        except Exception as exc:
            logger.debug("[AudioEngine] PyAudioWPatch không khả dụng: %s", exc)
            return False

    def _try_soundcard_loopback(self) -> bool:
        try:
            import soundcard as sc  # type: ignore
            import time

            loopback = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
            with loopback.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as mic:
                while self.recording:
                    data = mic.record(numframes=BLOCKSIZE)
                    arr = (data * 32767).astype(np.int16)
                    self.speaker_frames.append(arr.tobytes())
                    self.speaker_level = _rms_level(arr)
            return True
        except Exception as exc:
            logger.debug("[AudioEngine] soundcard loopback không khả dụng: %s", exc)
            return False

    # ------------------------------------------------------------------
    def stop(self) -> dict:
        """Dừng ghi, flush cuối cùng, đóng file WAV, trả về dict đường dẫn."""
        self.recording = False
        self._flush_stop.set()
        for t in self._threads:
            t.join(timeout=3)

        # Flush toàn bộ frame còn lại sau khi thread ghi đã dừng
        self._flush_to_disk()

        result: dict = {}
        with self._flush_lock:
            if self._mic_wf is not None:
                self._mic_wf.close()
                self._mic_wf = None
                result["mic"] = str(self._mic_wav_path)

            if self._spk_wf is not None:
                self._spk_wf.close()
                self._spk_wf = None
                result["speaker"] = str(self._spk_wav_path)

        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _save_wav(path: Path, frames: list[bytes],
                  sr: int = SAMPLE_RATE, ch: int = CHANNELS) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(ch)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(sr)
            wf.writeframes(b"".join(frames))
