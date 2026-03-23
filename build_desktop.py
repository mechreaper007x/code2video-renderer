import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path

# --- CONFIGURATION ---
APP_NAME = "CODE2VIDEO"
ENTRY_POINT = "desktop_app.py"
DIST_DIR = Path("dist")
BUILD_DIR = Path("build")
BIN_DIR = Path("bin")

IS_WINDOWS = platform.system() == "Windows"
FFMPEG_NAME = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"

def run_command(cmd, msg):
    print(f"[*] {msg}...")
    try:
        # Use shell=True on Windows for better command resolution if needed, 
        # but here we stay with list format for safety.
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] Error during {msg}:")
        print(e.stderr)
        sys.exit(1)

def main():
    # 1. Clean up old builds
    if DIST_DIR.exists(): shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists(): shutil.rmtree(BUILD_DIR)
    if BIN_DIR.exists(): shutil.rmtree(BIN_DIR)
    BIN_DIR.mkdir()

    print(f"=== Building {APP_NAME} Distributable ({platform.system()}) ===")

    # 2. Get FFmpeg Binary
    print("[*] Locating FFmpeg...")
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    shutil.copy(ffmpeg_exe, BIN_DIR / FFMPEG_NAME)
    # Ensure it's executable on Mac/Linux
    if not IS_WINDOWS:
        os.chmod(BIN_DIR / FFMPEG_NAME, 0o755)
    print(f"    -> Copied FFmpeg to {BIN_DIR}")

    # 3. Get Playwright Chromium Binary
    print("[*] Installing/Locating Chromium...")
    # Set a local path for browsers so we know where they are
    local_browsers = BIN_DIR / "browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
    
    # Run playwright install to ensure we have the binary locally for this platform
    run_command([sys.executable, "-m", "playwright", "install", "chromium"], f"Downloading Chromium for {platform.system()}")
    
    # 4. Prepare PyInstaller Command
    # Data separator is ';' on Windows, ':' on Mac/Linux
    sep = ";" if IS_WINDOWS else ":"
    
    pyinstaller_cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir", # Using onedir for better stability with large browser binaries
        "--windowed",
        "--name", APP_NAME,
        "--add-data", f"code2video.html{sep}.",
        "--add-data", f"bin{sep}bin",
        "--collect-all", "playwright",
        "--collect-all", "webview",
        "--hidden-import", "playwright_render",
        "--hidden-import", "serve",
        ENTRY_POINT
    ]

    # macOS specific: Add icon and bundle identifier if needed
    if not IS_WINDOWS:
        pyinstaller_cmd.extend([
            "--osx-bundle-identifier", "com.code2video.app",
        ])

    print("[*] Running PyInstaller...")
    subprocess.run(pyinstaller_cmd, check=True)

    print("\n" + "="*40)
    print(f"🎉 SUCCESS! {APP_NAME} is ready.")
    if IS_WINDOWS:
        print(f"📂 Location: {DIST_DIR / APP_NAME / (APP_NAME + '.exe')}")
    else:
        print(f"📂 Location: {DIST_DIR / (APP_NAME + '.app')}")
    print("="*40)

if __name__ == "__main__":
    main()
