#!/usr/bin/env python3

import base64
import os
import sys
import platform
from pathlib import Path

from serve import server_url, start_server, stop_server

IS_WINDOWS = platform.system() == "Windows"
FFMPEG_NAME = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"

def resolve_resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        # For macOS .app, _MEIPASS points to Contents/Resources
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).resolve().parent / relative_path


class DesktopApi:
    def __init__(self):
        self.window = None

    def bind_window(self, window) -> None:
        self.window = window

    def save_video(self, filename: str, base64_data: str):
        if self.window is None:
            raise RuntimeError("Desktop window is not ready.")

        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("pywebview is not available.") from exc

        selected = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
            file_types=("MP4 video (*.mp4)", "All files (*.*)"),
        )
        if not selected:
            return {"saved": False}

        target = selected[0] if isinstance(selected, (list, tuple)) else selected
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(base64.b64decode(base64_data))
        return {"saved": True, "path": str(target_path)}


def main() -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for desktop mode.")
        print("Install it with: pip install -r desktop_requirements.txt")
        return 1

    # --- CROSS-PLATFORM PACKAGING LOGIC ---
    # Configure Playwright to use the bundled browser
    bundled_browsers = resolve_resource_path("bin/browsers")
    if bundled_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)
    
    # Configure FFmpeg to use the bundled binary
    bundled_ffmpeg = resolve_resource_path(f"bin/{FFMPEG_NAME}")
    if bundled_ffmpeg.exists():
        os.environ["FFMPEG_EXE"] = str(bundled_ffmpeg)
        # Ensure executable permission on Unix
        if not IS_WINDOWS:
            try:
                os.chmod(bundled_ffmpeg, 0o755)
            except Exception:
                pass
    # ---------------------------------------

    os.environ.setdefault("CODE2VIDEO_NO_BROWSER", "1")
    server, _thread = start_server(host="127.0.0.1", port=0)
    url = server_url(server, public_host="127.0.0.1")
    stopped = False

    def shutdown() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        stop_server(server)

    api = DesktopApi()
    window = webview.create_window(
        "CODE2VIDEO",
        url,
        js_api=api,
        width=1480,
        height=960,
        min_size=(1100, 720),
        text_select=True,
    )
    api.bind_window(window)

    try:
        window.events.closed += shutdown
    except Exception:
        pass

    try:
        webview.start(debug=os.environ.get("CODE2VIDEO_DEBUG") == "1")
    finally:
        shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
