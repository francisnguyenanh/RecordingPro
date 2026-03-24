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
    """

    def __init__(self, session_id: str, display_index: int = 1):
        """
        display_index: 1-based (giống mss monitors[1]).
        Đối với dxcam: output_idx = display_index - 1 (0-based).
        """
        self.session_id = session_id
        self.display_index = display_index
        self.start_timestamp: float | None = None
        self.frame_count = 0
        self.recording = False
        self._thread: threading.Thread | None = None
        self._pending_display: int | None = None   # set by switch_display()
        self._segment_paths: list[str] = []        # all recorded segment paths

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
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="video-recorder"
        )
        self._thread.start()
        logger.info(
            "[VideoEngine] Bắt đầu ghi màn hình #%d (session=%s)",
            self.display_index, self.session_id,
        )

    # ------------------------------------------------------------------
    def _capture_loop(self) -> None:
        """Vòng lặp ngoài: xử lý nhiều segment khi chuyển màn hình."""
        seg_idx = 0
        while self.recording:
            path = OUTPUT_DIR / f"{self.session_id}_v{seg_idx}.mp4"
            self._segment_paths.append(str(path))

            # Thử dxcam trước; nếu không khả dụng, dùng mss
            result = self._record_segment_dxcam(path, self.display_index, seg_idx)
            if result is None:
                result = self._record_segment_mss(path, self.display_index, seg_idx)

            # result=True → dừng vì switch; False → dừng vì recording=False
            if self._pending_display is not None:
                self.display_index = self._pending_display
                self._pending_display = None
                seg_idx += 1
                logger.info(
                    "[VideoEngine] Segment %d xong, chuyển sang màn hình #%d",
                    seg_idx - 1, self.display_index,
                )
            else:
                break

        logger.info(
            "[VideoEngine] Ghi xong: %d segment(s), %d frames tổng.",
            len(self._segment_paths), self.frame_count,
        )

    # ------------------------------------------------------------------
    def _record_segment_dxcam(
        self, path: Path, display_index: int, seg_idx: int
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
            while self.recording and self._pending_display is None:
                frame = camera.get_latest_frame()
                if frame is None:
                    time.sleep(0.001)
                    continue

                if first_frame:
                    h, w = frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(str(path), fourcc, TARGET_FPS, (w, h))
                    if seg_idx == 0:
                        self.start_timestamp = time.perf_counter()
                    first_frame = False

                writer.write(frame)
                self.frame_count += 1

            camera.stop()
            del camera
            logger.info(
                "[VideoEngine] dxcam segment %d: display=#%d, frames=%d",
                seg_idx, display_index, self.frame_count,
            )
            return self._pending_display is not None

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
        self, path: Path, display_index: int, seg_idx: int
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

                if seg_idx == 0:
                    self.start_timestamp = time.perf_counter()

                while self.recording and self._pending_display is None:
                    t_start = time.perf_counter()

                    img = sct.grab(monitor)
                    frame = np.array(img, dtype=np.uint8)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    writer.write(frame)
                    self.frame_count += 1

                    elapsed = time.perf_counter() - t_start
                    sleep_t = FRAME_INTERVAL - elapsed
                    if sleep_t > 0:
                        time.sleep(sleep_t)

            logger.info(
                "[VideoEngine] mss segment %d: display=#%d, frames=%d",
                seg_idx, display_index, self.frame_count,
            )
        except Exception as exc:
            logger.error("[VideoEngine] mss segment %d lỗi: %s", seg_idx, exc)
        finally:
            if writer is not None:
                writer.release()

        return self._pending_display is not None

    # ------------------------------------------------------------------
    def stop(self) -> list[str]:
        """Dừng ghi. Trả về danh sách đường dẫn các segment video."""
        self.recording = False
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("[VideoEngine] Đã dừng ghi màn hình.")
        return list(self._segment_paths)
