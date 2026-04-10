"""
tray.py — ScreenCapturePro v3 main entry point.
Run: python tray.py  (or pythonw tray.py for no console)

Starts Flask server + CallDetector in background threads,
then creates a System Tray icon (pystray) for 24/7 operation.
"""
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"


# ══════════════════════════════════════════════════════════════════════
# ICON GENERATION
# ══════════════════════════════════════════════════════════════════════

def create_tray_icon(state: str = "idle_off") -> Image.Image:
    """
    Generate tray icon programmatically (uses assets/icon.png if available).
    state options:
      "idle_off"  → grey circle on dark bg   (monitoring off)
      "idle_on"   → green circle on dark bg  (monitoring on)
      "recording" → red circle + white dot   (recording in progress)
    """
    assets_icon = BASE_DIR / "assets" / "icon.png"
    if assets_icon.exists() and state == "idle_off":
        try:
            return Image.open(assets_icon).convert("RGBA")
        except Exception:
            pass

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        "idle_off":  "#555555",
        "idle_on":   "#39ff14",
        "recording": "#ff3c3c",
    }
    color = colors.get(state, "#555555")

    # Dark background square
    draw.rectangle([0, 0, size, size], fill="#1a1a1a")
    # Colored circle
    margin = 8
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)
    # Small white rec dot if recording
    if state == "recording":
        draw.ellipse([4, 4, 18, 18], fill="#ffffff")

    return img


# ══════════════════════════════════════════════════════════════════════
# FLASK STARTUP
# ══════════════════════════════════════════════════════════════════════

def start_flask():
    """Run Flask+SocketIO in a background daemon thread."""
    # Must import here (after sys.path is set) to avoid circular issues
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from app import socketio, app, _on_startup
    _on_startup()
    socketio.run(app, host="127.0.0.1", port=5010, debug=False, use_reloader=False)


def wait_for_flask(timeout: float = 8.0) -> bool:
    """Poll until Flask is ready, return True if successful."""
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get("http://127.0.0.1:5010/api/status", timeout=0.5)
            return True
        except Exception:
            time.sleep(0.1)
    return False


# ══════════════════════════════════════════════════════════════════════
# TRAY APPLICATION
# ══════════════════════════════════════════════════════════════════════

class TrayApp:
    def __init__(self):
        self.icon: pystray.Icon | None = None
        self.flask_thread: threading.Thread | None = None
        self._browser_opened = False

    # ── Public actions ─────────────────────────────────────────────
    def open_browser(self) -> None:
        webbrowser.open("http://127.0.0.1:5010")

    def toggle_autodetect(self, icon, item) -> None:
        """Toggle auto_detect_calls in config and notify Flask server."""
        import requests
        cfg = self._read_config()
        new_val = not cfg.get("auto_detect_calls", False)
        cfg["auto_detect_calls"] = new_val
        self._write_config(cfg)
        try:
            requests.post(
                "http://127.0.0.1:5010/api/config",
                json={"auto_detect_calls": new_val},
                timeout=2,
            )
        except Exception:
            pass
        self._refresh_icon()

    def quit(self) -> None:
        if self.icon:
            self.icon.stop()
        sys.exit(0)

    # ── Menu builder ───────────────────────────────────────────────
    def _build_menu(self) -> pystray.Menu:
        import requests
        state_label = "Đang khởi động…"
        auto_label  = "Tự động phát hiện"
        try:
            r = requests.get("http://127.0.0.1:5010/api/status", timeout=1)
            data = r.json()
            state_label = ("🔴 Đang ghi…" if data.get("state") == "recording"
                           else "⚪ Đang chờ")

            cfg = self._read_config()
            auto_enabled = cfg.get("auto_detect_calls", False)
            auto_label = ("✅ Tự động: BẬT" if auto_enabled else "⬜ Tự động: TẮT")
        except Exception:
            pass

        return pystray.Menu(
            pystray.MenuItem("ScreenCapturePro v3", lambda i, item: None, enabled=False),
            pystray.MenuItem(state_label, lambda i, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🌐 Mở giao diện", lambda i, item: self.open_browser()),
            pystray.MenuItem(auto_label, self.toggle_autodetect),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Thoát", lambda i, item: self.quit()),
        )

    # ── Icon updater ───────────────────────────────────────────────
    def _get_icon_state(self) -> str:
        import requests
        try:
            r = requests.get("http://127.0.0.1:5010/api/status", timeout=1)
            data = r.json()
            if data.get("state") == "recording":
                return "recording"
            cfg = self._read_config()
            return "idle_on" if cfg.get("auto_detect_calls") else "idle_off"
        except Exception:
            return "idle_off"

    def _refresh_icon(self) -> None:
        if self.icon:
            self.icon.icon = create_tray_icon(self._get_icon_state())

    # ── Config helpers ─────────────────────────────────────────────
    @staticmethod
    def _read_config() -> dict:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @staticmethod
    def _write_config(cfg: dict) -> None:
        CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Main run ───────────────────────────────────────────────────
    def run(self) -> None:
        # 1. Start Flask in background thread
        self.flask_thread = threading.Thread(
            target=start_flask, daemon=True, name="flask-server"
        )
        self.flask_thread.start()

        # 2. Wait until Flask is ready
        if not wait_for_flask(timeout=10):
            print("[Tray] Flask không khởi động được trong 10s — thoát.")
            sys.exit(1)

        # 3. Open browser on first launch
        self.open_browser()

        # 4. Create pystray icon with dynamic menu
        self.icon = pystray.Icon(
            "ScreenCapturePro",
            create_tray_icon("idle_off"),
            "ScreenCapturePro v3",
            menu=pystray.Menu(lambda: self._build_menu().items),
        )

        # 5. Background thread: refresh icon every 3s
        def _icon_updater():
            while True:
                time.sleep(3)
                self._refresh_icon()
                # Update tooltip with recording time (Method 2)
                try:
                    r = requests.get("http://127.0.0.1:5010/api/status", timeout=1)
                    data = r.json()
                    if data.get("state") == "recording":
                        secs = int(data.get("duration_seconds", 0))
                        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
                        self.icon.title = f"🔴 REC {h:02d}:{m:02d}:{s:02d} — Tomo Recording"
                    else:
                        self.icon.title = "Tomo Recording"
                except Exception:
                    pass

        threading.Thread(target=_icon_updater, daemon=True, name="tray-updater").start()

        # 6. Run tray icon (blocks until quit)
        self.icon.run()


# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    TrayApp().run()
