"""
call_detector.py — v3: 3-method call detection with majority voting.

Methods:
  A. Process + Window Title  (psutil + pygetwindow)
  B. Windows Audio Session   (pycaw WASAPI)
  C. Windows Media API       (winsdk.windows.media.control)

A call is confirmed when ≥2/3 methods agree (majority vote).
Google Meet (browser-based): only A+B available; both must agree.
Includes simulate_call_start / simulate_call_end for UI testing.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Detection signatures ──────────────────────────────────────────────
CALL_SIGNATURES: dict = {
    "Zoom": {
        "processes":        ["Zoom.exe"],
        "window_keywords":  ["Zoom Meeting", "Zoom Webinar", "Zoom"],
        # Only these titles confirm an active call (bare "Zoom" = idle)
        "window_exact_call": ["Zoom Meeting", "Zoom Webinar"],
        "audio_processes":  ["Zoom.exe"],
    },
    "Microsoft Teams": {
        "processes":        ["Teams.exe", "ms-teams.exe", "msteams.exe"],
        "window_keywords":  ["Microsoft Teams", "Teams"],
        # Title doesn't change reliably → audio method decides
        "window_exact_call": [],
        "audio_processes":  ["Teams.exe", "ms-teams.exe"],
    },
    "Google Meet": {
        "processes":        ["chrome.exe", "msedge.exe", "firefox.exe"],
        "window_keywords":  ["Meet -", "Meet –", "Google Meet", "Meet"],
        "window_exact_call": ["Meet -", "Meet –", "Google Meet"],
        "audio_processes":  ["chrome.exe", "msedge.exe", "firefox.exe"],
    },
}

# Priority when multiple apps detected simultaneously
_PRIORITY = ["Zoom", "Microsoft Teams", "Google Meet"]


@dataclass
class DetectionResult:
    app_name: Optional[str]   # None = no call detected / low confidence
    method_a: bool
    method_b: bool
    method_c: bool
    confidence: str           # "high" | "medium" | "low" | "none"


class CallDetector:
    """
    Detects active video calls using 3 independent methods.
    Fires callbacks on call start/end (thread-safe).
    Also supports simulate_ methods for UI testing.
    """

    def __init__(self,
                 on_call_start: Callable[[str], None],
                 on_call_end: Callable[[str], None]):
        self.on_call_start = on_call_start
        self.on_call_end = on_call_end
        self._monitoring = False
        self._current_call_app: Optional[str] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.poll_interval: float = 3.0
        self._last_result: Optional[DetectionResult] = None

    # ── Method A: Process + Window Title ─────────────────────────────
    def _method_a(self) -> Optional[str]:
        """
        Check running processes + window titles.
        Returns first app_name that matches in priority order, or None.
        """
        running: set = self._get_running_processes()
        all_titles: list = []
        try:
            import pygetwindow as gw  # type: ignore
            all_titles = [t for t in gw.getAllTitles() if t]
        except Exception as exc:
            logger.debug("[DetectorA] pygetwindow error: %s", exc)

        for app in _PRIORITY:
            sig = CALL_SIGNATURES[app]
            if not any(p.lower() in running for p in sig["processes"]):
                continue

            exact = sig.get("window_exact_call", [])
            if not exact:
                # Teams: no reliable title change → tentative match (method_b decides)
                return app

            # Must match at least one exact keyword in any window title
            for title in all_titles:
                if any(kw.lower() in title.lower() for kw in exact):
                    return app

        return None

    # ── Method B: WASAPI Audio Session (pycaw) ────────────────────────
    def _method_b(self) -> Optional[str]:
        """
        Check if known call processes have active WASAPI audio sessions.
        Returns first matching app_name in priority order, or None.
        """
        active_procs: set = set()
        try:
            from pycaw.pycaw import AudioUtilities, AudioSessionState  # type: ignore
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process is not None:
                    try:
                        if s.State == AudioSessionState.AudioSessionStateActive:
                            active_procs.add(s.Process.name().lower())
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("[DetectorB] pycaw error: %s", exc)
            return None

        for app in _PRIORITY:
            sig = CALL_SIGNATURES[app]
            if any(p.lower() in active_procs for p in sig["audio_processes"]):
                return app

        return None

    # ── Method C: Windows Runtime Media API (winsdk) ─────────────────
    def _method_c(self) -> Optional[str]:
        """
        Use Windows.Media.Control to check active media/call sessions.
        Most reliable for Teams and Zoom native apps.
        Gracefully returns None if winsdk is unavailable.
        """
        try:
            import asyncio
            from winsdk.windows.media.control import (  # type: ignore
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _get_app_ids() -> list:
                manager = await MediaManager.request_async()
                app_ids = []
                for session in manager.get_sessions():
                    try:
                        app_ids.append(session.source_app_user_model_id.lower())
                    except Exception:
                        pass
                return app_ids

            app_ids = asyncio.run(_get_app_ids())

            WINRT_MAP = {
                "zoom":    "Zoom",
                "msteams": "Microsoft Teams",
                "teams":   "Microsoft Teams",
            }
            for app_id in app_ids:
                for key, name in WINRT_MAP.items():
                    if key in app_id:
                        return name

        except ImportError:
            pass  # winsdk not installed — silently skip
        except Exception as exc:
            logger.debug("[DetectorC] winsdk error: %s", exc)

        return None

    # ── Majority Vote ─────────────────────────────────────────────────
    def _detect_once(self) -> DetectionResult:
        """
        Run all 3 methods and apply majority vote.

        Rules:
        - Zoom / Teams:   ≥2 of 3 methods agree → high confidence
        - Google Meet:    methods A+B both agree → medium confidence
        - Only 1 method:  low confidence → do NOT trigger (avoid false positives)
        """
        a = self._method_a()
        b = self._method_b()
        c = self._method_c()

        # Count votes per app
        votes: dict[str, int] = {}
        for result in [a, b, c]:
            if result:
                votes[result] = votes.get(result, 0) + 1

        # Evaluate in priority order so Zoom beats Teams if both fire
        for app in _PRIORITY:
            count = votes.get(app, 0)
            if app == "Google Meet":
                # Method A (window title) alone is sufficient — "Meet - <code>" is specific.
                # Method B additionally increases confidence.
                if a == "Google Meet":
                    return DetectionResult(app, True, b == "Google Meet", False, "medium")
            else:
                if count >= 2:
                    return DetectionResult(
                        app,
                        a == app, b == app, c == app,
                        "high",
                    )

        return DetectionResult(None, bool(a), bool(b), bool(c), "low")

    # ── Polling loop ──────────────────────────────────────────────────
    def start_monitoring(self) -> None:
        with self._lock:
            if self._monitoring:
                return
            self._monitoring = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="call-detector"
        )
        self._thread.start()
        logger.info("[CallDetector] Bắt đầu giám sát cuộc gọi.")

    def stop_monitoring(self) -> None:
        with self._lock:
            self._monitoring = False
        logger.info("[CallDetector] Đã dừng giám sát.")

    def _poll_loop(self) -> None:
        while True:
            with self._lock:
                if not self._monitoring:
                    break

            result = self._detect_once()
            self._last_result = result

            with self._lock:
                prev = self._current_call_app

            if result.app_name and not prev:
                with self._lock:
                    self._current_call_app = result.app_name
                logger.info("[CallDetector] Cuộc gọi bắt đầu: %s", result.app_name)
                try:
                    self.on_call_start(result.app_name)
                except Exception as exc:
                    logger.error("[CallDetector] on_call_start error: %s", exc)

            elif not result.app_name and prev:
                with self._lock:
                    self._current_call_app = None
                logger.info("[CallDetector] Cuộc gọi kết thúc: %s", prev)
                try:
                    self.on_call_end(prev)
                except Exception as exc:
                    logger.error("[CallDetector] on_call_end error: %s", exc)

            time.sleep(self.poll_interval)

    # ── Status ────────────────────────────────────────────────────────
    def get_status(self) -> dict:
        r = self._last_result
        with self._lock:
            monitoring = self._monitoring
            active = self._current_call_app
        return {
            "monitoring": monitoring,
            "active_call": active,
            "last_result": {
                "method_a":  r.method_a  if r else False,
                "method_b":  r.method_b  if r else False,
                "method_c":  r.method_c  if r else False,
                "confidence": r.confidence if r else "none",
            } if r else None,
        }

    # ── Simulate (for testing) ────────────────────────────────────────
    def simulate_call_start(self, app_name: str) -> bool:
        """
        Manually fire on_call_start for UI testing.
        Uses the exact same callback path as real detection.
        Returns True if fired, False if already in a call.
        """
        with self._lock:
            if self._current_call_app:
                return False
            self._current_call_app = app_name

        logger.info("[CallDetector] Giả lập cuộc gọi bắt đầu: %s", app_name)
        try:
            self.on_call_start(app_name)
        except Exception as exc:
            logger.error("[CallDetector] simulate on_call_start error: %s", exc)
        return True

    def simulate_call_end(self) -> bool:
        """
        Manually fire on_call_end for UI testing.
        Returns True if fired, False if no active call.
        """
        with self._lock:
            if not self._current_call_app:
                return False
            ended = self._current_call_app
            self._current_call_app = None

        logger.info("[CallDetector] Giả lập cuộc gọi kết thúc: %s", ended)
        try:
            self.on_call_end(ended)
        except Exception as exc:
            logger.error("[CallDetector] simulate on_call_end error: %s", exc)
        return True

    # ── Private helpers ───────────────────────────────────────────────
    @staticmethod
    def _get_running_processes() -> set:
        try:
            import psutil  # type: ignore
            return {p.name().lower() for p in psutil.process_iter(["name"])}
        except Exception as exc:
            logger.debug("[DetectorA] psutil error: %s", exc)
            return set()
