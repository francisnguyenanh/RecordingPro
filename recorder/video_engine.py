"""
video_engine.py — v2: Ghi màn hình với hỗ trợ chọn màn hình (display_index).
Hỗ trợ chuyển đổi màn hình ghi ngay trong khi đang ghi (multi-segment).
"""
import logging
import threading
import time
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

TARGET_FPS = 30
FRAME_INTERVAL = 1.0 / TARGET_FPS
OUTPUT_DIR = Path.home() / "Videos" / "ScreenCapturePro"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class VideoEngine:
    """Ghi màn hình thành file MP4 với hỗ trợ chọn màn hình cụ thể.
    Hỗ trợ chuyển màn hình live qua switch_display() — tạo các segment riêng biệt
    và ghép lại trong bước hậu xử lý.
    Hỗ trợ capture theo vùng cửa sổ (window region) qua tham số region.
    """

    def __init__(self, session_id: str, display_index: int = 1, output_dir=None,
                 region: dict | None = None):
        """
        display_index: 1-based (giống mss monitors[1]).
        Đối với dxcam: output_idx = display_index - 1 (0-based).
        region: dict {left, top, width, height} để capture theo cửa sổ cụ thể.
                Nếu có, ưu tiên hơn display_index.
        """
        self.session_id = session_id
        self._output_dir = output_dir or OUTPUT_DIR
        self.display_index = display_index
        self.region: dict | None = region     # window region capture
        self.start_timestamp: float | None = None
        self.frame_count = 0
        self.recording = False
        self._thread: threading.Thread | None = None
        self._pending_display: int | None = None   # set by switch_display()
        
        self.current_seg_idx = 0
        self._seg_frame_count = 0
        self._rollover_request = threading.Event()
        self._rollover_done = threading.Event()
        self._completed_segment: dict | None = None

    # ------------------------------------------------------------------
    def switch_display(self, new_index: int) -> None:
        """Yêu cầu chuyển màn hình đang ghi. Có hiệu lực sau frame hiện tại."""
        if new_index != self.display_index:
            self._pending_display = new_index
            logger.info(
                "[VideoEngine] Yêu cầu chuyển màn hình: #%d → #%d",
                self.display_index, new_index,
            )

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.recording = True
        self.frame_count = 0
        self.current_seg_idx = 0
        self._seg_frame_count = 0
        self._rollover_request.clear()
        self._rollover_done.clear()
        self._completed_segment = None
        self.start_timestamp = None
        self._pending_display = None
        
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="video-recorder"
        )
        self._thread.start()
        if self.region:
            logger.info(
                "[VideoEngine] Bắt đầu ghi vùng cửa sổ %dx%d@(%d,%d) (session=%s)",
                self.region.get("width"), self.region.get("height"),
                self.region.get("left", 0), self.region.get("top", 0),
                self.session_id,
            )
        else:
            logger.info(
                "[VideoEngine] Bắt đầu ghi màn hình #%d (session=%s)",
                self.display_index, self.session_id,
            )

    # ------------------------------------------------------------------
    def roll_segment(self) -> dict:
        """Kích hoạt việc đóng file hiện tại và mở file mới ở frame tiếp theo."""
        self._rollover_done.clear()
        self._rollover_request.set()
        self._rollover_done.wait(timeout=5.0)
        res = self._completed_segment
        self._completed_segment = None
        if not res:
            res = {
                "video": str(self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"),
                "frame_count": self._seg_frame_count
            }
        return res

    # ------------------------------------------------------------------
    def _capture_loop(self) -> None:
        """Vòng lặp ngoài: xử lý nhiều segment khi chuyển màn hình."""
        # Nếu có region (window capture), dùng mss với region
        if self.region:
            self._record_segments_region()
            return

        while self.recording:
            path = self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"

            # Thử dxcam trước; nếu không khả dụng, dùng mss
            result = self._record_segment_dxcam(path, self.display_index)
            if result is None:
                result = self._record_segment_mss(path, self.display_index)

            if self._pending_display is not None:
                self.display_index = self._pending_display
                self._pending_display = None
                # Không break, chỉ loop lại để mở dxcam với display mới
            else:
                break

        logger.info(
            "[VideoEngine] Ghi xong: %d frames tổng.",
            self.frame_count,
        )

    # ------------------------------------------------------------------
    def _record_segments_region(self) -> None:
        """Capture theo vùng cửa sổ (window region) bằng mss, hỗ trợ multi-segment."""
        import mss  # type: ignore
        import numpy as np

        r = self.region
        mss_monitor = {
            "left": int(r.get("left", 0)),
            "top": int(r.get("top", 0)),
            "width": int(r.get("width", 1280)),
            "height": int(r.get("height", 720)),
        }
        w, h = mss_monitor["width"], mss_monitor["height"]
        # Đảm bảo width/height chẵn (yêu cầu của VideoWriter H.264)
        w = w if w % 2 == 0 else w - 1
        h = h if h % 2 == 0 else h - 1
        mss_monitor["width"] = w
        mss_monitor["height"] = h

        writer: cv2.VideoWriter | None = None

        def _open_writer(path: Path) -> cv2.VideoWriter:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            return cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))

        try:
            with mss.mss() as sct:
                path = self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"
                writer = _open_writer(path)
                self.start_timestamp = time.perf_counter()
                self._seg_frame_count = 0
                next_deadline = time.perf_counter()

                while self.recording:
                    if self._rollover_request.is_set():
                        if writer is not None:
                            writer.release()
                        self._completed_segment = {
                            "video": str(path),
                            "frame_count": self._seg_frame_count,
                        }
                        self.current_seg_idx += 1
                        path = self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"
                        writer = _open_writer(path)
                        self.start_timestamp = time.perf_counter()
                        self._seg_frame_count = 0
                        next_deadline = time.perf_counter()
                        self._rollover_request.clear()
                        self._rollover_done.set()

                    img = sct.grab(mss_monitor)
                    frame = np.array(img, dtype=np.uint8)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    # Resize nếu cần đảm bảo đúng kích thước
                    if frame.shape[1] != w or frame.shape[0] != h:
                        frame = cv2.resize(frame, (w, h))
                    writer.write(frame)
                    self.frame_count += 1
                    self._seg_frame_count += 1

                    next_deadline += FRAME_INTERVAL
                    sleep_t = next_deadline - time.perf_counter()
                    if sleep_t > 0:
                        time.sleep(sleep_t)
                    elif sleep_t < -FRAME_INTERVAL:
                        missed = int(-sleep_t / FRAME_INTERVAL)
                        for _ in range(missed):
                            writer.write(frame)
                            self.frame_count += 1
                            self._seg_frame_count += 1
                        next_deadline += missed * FRAME_INTERVAL
                        if (time.perf_counter() - next_deadline) > FRAME_INTERVAL:
                            next_deadline = time.perf_counter()
        except Exception as exc:
            logger.error("[VideoEngine] Window region capture lỗi: %s", exc)
        finally:
            if writer is not None:
                writer.release()

    # ------------------------------------------------------------------
    def _record_segment_dxcam(
        self, path: Path, display_index: int
    ) -> bool | None:
        """
        Ghi một segment bằng dxcam.
        Trả về True = dừng do switch, False = dừng ghi bình thường.
        Trả về None nếu dxcam không khả dụng.
        """
        writer: cv2.VideoWriter | None = None
        try:
            import dxcam  # type: ignore

            camera = dxcam.create(
                device_idx=0,
                output_idx=display_index - 1,
                output_color="BGR",
            )
            camera.start(target_fps=TARGET_FPS)

            first_frame = True
            next_deadline: float = 0.0
            
            while self.recording:
                frame = camera.get_latest_frame()
                if frame is None:
                    time.sleep(0.001)
                    continue

                if first_frame:
                    h, w = frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))
                    self.start_timestamp = time.perf_counter()
                    self._seg_frame_count = 0
                    next_deadline = time.perf_counter()
                    first_frame = False

                if self._rollover_request.is_set():
                    if writer is not None:
                        writer.release()
                    
                    self._completed_segment = {
                        "video": str(path),
                        "frame_count": self._seg_frame_count
                    }
                    
                    self.current_seg_idx += 1
                    path = self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"
                    
                    if self._pending_display is not None:
                        writer = None
                        self._rollover_request.clear()
                        self._rollover_done.set()
                        camera.stop()
                        del camera
                        return True  # stop camera to re-init
                        
                    writer = cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))
                    self.start_timestamp = time.perf_counter()
                    self._seg_frame_count = 0
                    next_deadline = time.perf_counter()
                    
                    self._rollover_request.clear()
                    self._rollover_done.set()

                writer.write(frame)
                self.frame_count += 1
                self._seg_frame_count += 1
                
                next_deadline += FRAME_INTERVAL
                sleep_t = next_deadline - time.perf_counter()
                if sleep_t > 0:
                    time.sleep(sleep_t)
                elif sleep_t < -FRAME_INTERVAL:
                    missed = int(-sleep_t / FRAME_INTERVAL)
                    for _ in range(missed):
                        writer.write(frame)
                        self.frame_count += 1
                        self._seg_frame_count += 1
                    next_deadline += missed * FRAME_INTERVAL
                    if (time.perf_counter() - next_deadline) > FRAME_INTERVAL:
                        next_deadline = time.perf_counter()
                        
            camera.stop()
            del camera
            return False

        except ImportError:
            return None  # dxcam chưa cài
        except Exception as exc:
            logger.debug("[VideoEngine] dxcam lỗi (%s), chuyển sang mss.", exc)
            return None
        finally:
            if writer is not None:
                writer.release()

    # ------------------------------------------------------------------
    def _record_segment_mss(
        self, path: Path, display_index: int
    ) -> bool:
        """
        Ghi một segment bằng mss (fallback).
        Trả về True = dừng do switch, False = dừng ghi bình thường.
        """
        writer: cv2.VideoWriter | None = None
        try:
            import mss  # type: ignore
            import numpy as np

            with mss.mss() as sct:
                monitors = sct.monitors
                if display_index < len(monitors):
                    monitor = monitors[display_index]
                else:
                    logger.warning(
                        "[VideoEngine] display_index=%d vượt quá số màn hình, dùng primary.",
                        display_index,
                    )
                    monitor = monitors[1]

                w, h = monitor["width"], monitor["height"]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))

                self.start_timestamp = time.perf_counter()
                self._seg_frame_count = 0
                next_deadline = time.perf_counter()
                
                while self.recording:
                    if self._rollover_request.is_set():
                        if writer is not None:
                            writer.release()
                        
                        self._completed_segment = {
                            "video": str(path),
                            "frame_count": self._seg_frame_count
                        }
                        
                        self.current_seg_idx += 1
                        path = self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"
                        
                        if self._pending_display is not None:
                            writer = None
                            self._rollover_request.clear()
                            self._rollover_done.set()
                            return True
                            
                        writer = cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))
                        self.start_timestamp = time.perf_counter()
                        self._seg_frame_count = 0
                        next_deadline = time.perf_counter()
                        
                        self._rollover_request.clear()
                        self._rollover_done.set()

                    img = sct.grab(monitor)
                    frame = np.array(img, dtype=np.uint8)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    writer.write(frame)
                    self.frame_count += 1
                    self._seg_frame_count += 1

                    next_deadline += FRAME_INTERVAL
                    sleep_t = next_deadline - time.perf_counter()
                    if sleep_t > 0:
                        time.sleep(sleep_t)
                    elif sleep_t < -FRAME_INTERVAL:
                        missed = int(-sleep_t / FRAME_INTERVAL)
                        for _ in range(missed):
                            writer.write(frame)
                            self.frame_count += 1
                            self._seg_frame_count += 1
                        next_deadline += missed * FRAME_INTERVAL
                        if (time.perf_counter() - next_deadline) > FRAME_INTERVAL:
                            next_deadline = time.perf_counter()

        except Exception as exc:
            logger.error("[VideoEngine] mss segment lỗi: %s", exc)
        finally:
            if writer is not None:
                writer.release()

        return False

    # ------------------------------------------------------------------
    def stop(self) -> dict:
        """Dừng ghi. Trả về thông tin phân đoạn cuối cùng."""
        self.recording = False
        self._rollover_done.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("[VideoEngine] Đã dừng ghi màn hình.")
        
        return {
            "video": str(self._output_dir / f"{self.session_id}_v{self.current_seg_idx}.mp4"),
            "frame_count": self._seg_frame_count
        }
