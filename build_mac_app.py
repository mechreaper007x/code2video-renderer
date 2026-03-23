import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_NAME = "CODE2VIDEO"
ENTRY_POINT = "desktop_app.py"
DIST_DIR = Path("dist")
BUILD_DIR = Path("build")
BIN_DIR = Path("bin")
PNG_ICON = Path("assets") / "code2video.png"
ICNS_ICON = Path("assets") / "code2video.icns"


def run_command(cmd: list[str], msg: str) -> None:
    print(f"[*] {msg}...")
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"[!] Error during {msg}:")
        print(exc.stderr)
        raise SystemExit(1) from exc


def ensure_macos() -> None:
    if sys.platform != "darwin":
        print("build_mac_app.py must be run on macOS.")
        raise SystemExit(1)


def ensure_dir_clean(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def build_icns() -> Path | None:
    if not PNG_ICON.exists():
        return None

    iconutil = shutil.which("iconutil")
    sips = shutil.which("sips")
    if not iconutil or not sips:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        iconset_dir = Path(temp_dir) / "code2video.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)
        sizes = [16, 32, 128, 256, 512]
        for size in sizes:
            run_command(
                [
                    sips,
                    "-z",
                    str(size),
                    str(size),
                    str(PNG_ICON),
                    "--out",
                    str(iconset_dir / f"icon_{size}x{size}.png"),
                ],
                f"Rendering icon {size}x{size}",
            )
            retina_size = size * 2
            run_command(
                [
                    sips,
                    "-z",
                    str(retina_size),
                    str(retina_size),
                    str(PNG_ICON),
                    "--out",
                    str(iconset_dir / f"icon_{size}x{size}@2x.png"),
                ],
                f"Rendering icon {size}x{size}@2x",
            )

        run_command(
            [iconutil, "-c", "icns", str(iconset_dir), "-o", str(ICNS_ICON)],
            "Building macOS icon",
        )
    return ICNS_ICON


def copy_runtime_assets(app_bundle: Path) -> None:
    resources_dir = app_bundle / "Contents" / "Resources"
    bundled_bin_dir = resources_dir / "bin"
    if bundled_bin_dir.exists():
        shutil.rmtree(bundled_bin_dir)
    shutil.copytree(BIN_DIR, bundled_bin_dir)

    ffmpeg_path = bundled_bin_dir / "ffmpeg"
    if ffmpeg_path.exists():
        os.chmod(ffmpeg_path, 0o755)


def main() -> int:
    ensure_macos()

    ensure_dir_clean(DIST_DIR)
    ensure_dir_clean(BUILD_DIR)
    ensure_dir_clean(BIN_DIR)
    BIN_DIR.mkdir()

    print(f"=== Building {APP_NAME} macOS App ===")

    print("[*] Locating FFmpeg...")
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    bundled_ffmpeg = BIN_DIR / "ffmpeg"
    shutil.copy(ffmpeg_exe, bundled_ffmpeg)
    os.chmod(bundled_ffmpeg, 0o755)
    print(f"    -> Copied FFmpeg to {BIN_DIR}")

    print("[*] Installing/Locating Chromium...")
    local_browsers = BIN_DIR / "browsers"
    local_browsers.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
    run_command(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        "Downloading Chromium for macOS",
    )

    icon_file = build_icns()
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name",
        APP_NAME,
        "--add-data",
        "code2video.html:.",
        "--collect-all",
        "playwright",
        "--collect-all",
        "webview",
        "--hidden-import",
        "playwright_render",
        "--hidden-import",
        "serve",
        "--osx-bundle-identifier",
        "com.code2video.app",
        ENTRY_POINT,
    ]
    if icon_file and icon_file.exists():
        pyinstaller_cmd.extend(["--icon", str(icon_file)])

    print("[*] Running PyInstaller...")
    subprocess.run(pyinstaller_cmd, check=True)

    app_bundle = DIST_DIR / f"{APP_NAME}.app"
    if not app_bundle.exists():
        print(f"[!] Expected app bundle was not created: {app_bundle}")
        raise SystemExit(1)

    print("[*] Copying runtime assets into app bundle...")
    copy_runtime_assets(app_bundle)

    archive_base = DIST_DIR / f"{APP_NAME}-macos"
    shutil.make_archive(str(archive_base), "zip", root_dir=DIST_DIR, base_dir=f"{APP_NAME}.app")

    print("\n" + "=" * 40)
    print(f"SUCCESS! {APP_NAME} macOS app is ready.")
    print(f"Location: {DIST_DIR / (APP_NAME + '.app')}")
    print(f"Zip: {archive_base}.zip")
    print("=" * 40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
