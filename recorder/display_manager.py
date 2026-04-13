"""
display_manager.py — Liệt kê màn hình và cung cấp thumbnail base64.
"""
import base64
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import mss
import numpy as np

logger = logging.getLogger(__name__)

THUMB_W = 640
THUMB_H = 360
JPEG_QUALITY = 85


@dataclass
class DisplayInfo:
    index: int              # 1-based (mss monitors[1], monitors[2], …)
    name: str               # "Display 1 (Chính)", "Display 2", …
    width: int
    height: int
    left: int
    top: int
    is_primary: bool
    preview_b64: str = field(default="", repr=False)  # data:image/jpeg;base64,…


class DisplayManager:
    """Liệt kê màn hình và chụp thumbnail."""

    # ------------------------------------------------------------------
    def get_displays(self, include_preview: bool = True) -> List[DisplayInfo]:
        """Trả về danh sách DisplayInfo cho mỗi màn hình (bỏ monitors[0])."""
        results: List[DisplayInfo] = []
        try:
            with mss.mss() as sct:
                monitors = sct.monitors  # monitors[0] = all screens combined

                for i, mon in enumerate(monitors[1:], start=1):
                    is_primary = (mon["left"] == 0 and mon["top"] == 0)
                    label = f"Display {i}"
                    if is_primary:
                        label += " (Chính)"

                    preview = ""
                    if include_preview:
                        preview = self._capture_preview(sct, mon)

                    results.append(DisplayInfo(
                        index=i,
                        name=label,
                        width=mon["width"],
                        height=mon["height"],
                        left=mon["left"],
                        top=mon["top"],
                        is_primary=is_primary,
                        preview_b64=preview,
                    ))
        except Exception as exc:
            logger.error("[DisplayManager] Lỗi liệt kê màn hình: %s", exc)

        # Fallback: nếu mss lỗi hoặc không có monitor, trả về một entry giả
        if not results:
            results.append(DisplayInfo(
                index=1, name="Display 1 (Chính)",
                width=1920, height=1080, left=0, top=0,
                is_primary=True, preview_b64=""
            ))

        return results

    # ------------------------------------------------------------------
    def get_display_by_index(self, index: int) -> Optional[dict]:
        """Trả về mss monitor dict cho VideoEngine, hoặc None nếu không tìm thấy."""
        try:
            with mss.mss() as sct:
                if 1 <= index < len(sct.monitors):
                    return dict(sct.monitors[index])
        except Exception as exc:
            logger.error("[DisplayManager] Lỗi lấy monitor[%d]: %s", index, exc)
        return None

    # ------------------------------------------------------------------
    def refresh_previews(self) -> List[DisplayInfo]:
        """Chụp lại thumbnail cho tất cả màn hình."""
        return self.get_displays(include_preview=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _capture_preview(sct: mss.mss, monitor: dict) -> str:
        """Chụp ảnh màn hình, resize → 320×180, encode JPEG base64."""
        try:
            img = sct.grab(monitor)
            frame = np.array(img, dtype=np.uint8)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Resize giữ tỉ lệ khung hình, fit trong 320×180
            h, w = frame.shape[:2]
            scale = min(THUMB_W / w, THUMB_H / h)
            nw, nh = int(w * scale), int(h * scale)
            thumb = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)

            ok, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception as exc:
            logger.warning("[DisplayManager] Lỗi chụp thumbnail: %s", exc)
        return ""
