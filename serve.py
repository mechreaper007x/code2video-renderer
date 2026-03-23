#!/usr/bin/env python3
"""
CODE2VIDEO Backend Assistant
- Serves the frontend
- Handles Playwright + FFmpeg video rendering
- Auto-opens the tool in your browser
"""

import os
import time
import json
import webbrowser
import subprocess
import sys
import tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    print("ERROR: imageio-ffmpeg is required.")
    print("Please run: pip install imageio-ffmpeg")
    exit(1)

ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("CODE2VIDEO_CORS_ORIGIN", "*").split(",") if origin.strip()]


class CODE2VIDEOServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class CODE2VIDEOHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        cors_origin = self._cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if cors_origin != "*":
                self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        if self._origin_forbidden():
            self.send_error(403, "Origin not allowed")
            return
        self.send_response(204)
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(301)
                self.send_header('Location', '/code2video.html')
                self.end_headers()
                return
            if self.path == '/api/config.js':
                return self._send_runtime_config()
            if self.path == '/health':
                return self._send_json({'ok': True})
            return super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"  [SERVER] GET error: {exc}")
            self._safe_text_error('Backend request failed.', 500)

    def do_POST(self):
        try:
            if self._origin_forbidden():
                self.send_error(403, "Origin not allowed")
                return
            if self.path == '/render':
                self._handle_render()
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"  [SERVER] POST error: {exc}")
            self._safe_text_error('Backend render failed.', 500)

    def _handle_render(self):
        content_length = int(self.headers['Content-Length'])
        payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
        python_renderer = Path(__file__).with_name('playwright_render.py')
        node_renderer = Path(__file__).with_name('playwright_render.mjs')

        if not python_renderer.exists() and not node_renderer.exists():
            self._send_text_error('No Playwright renderer script is available.', 500)
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'render-input.json')
            out_filepath = os.path.join(tmpdir, 'output.mp4')

            with open(input_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)

            env = os.environ.copy()
            env['FFMPEG_EXE'] = FFMPEG_EXE
            commands = []
            if python_renderer.exists():
                commands.append([sys.executable, str(python_renderer), input_path, out_filepath])
            if node_renderer.exists():
                commands.append(['node', str(node_renderer), input_path, out_filepath])

            result = None
            render_errors = []
            for cmd in commands:
                try:
                    result = subprocess.run(
                        cmd,
                        cwd=Path(__file__).parent,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=600
                    )
                except FileNotFoundError:
                    render_errors.append(f'Missing runtime for command: {" ".join(cmd[:2])}')
                    continue
                except subprocess.TimeoutExpired:
                    self._send_text_error('Playwright render timed out.', 504)
                    return

                if result.stderr.strip():
                    print(result.stderr.strip())

                if result.returncode == 0 and os.path.exists(out_filepath):
                    break

                render_errors.append(result.stderr.strip() or f'Renderer failed for command: {" ".join(cmd[:2])}')

            if not result or result.returncode != 0 or not os.path.exists(out_filepath):
                message = '\n'.join(err for err in render_errors if err) or 'Playwright rendering failed.'
                self._send_text_error(message, 500)
                return

            self._send_file(out_filepath)

    def _send_file(self, filepath):
        mime = 'video/mp4'
        filename = f'code2video_{int(time.time())}.mp4'

        with open(filepath, 'rb') as f:
            data = f.read()

        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.send_header('X-Render-Filename', filename)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return
        print(f"  [SERVER] OK Sent temporary render: {filename}")

    def _send_json(self, payload, status=200):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_text_error(self, message, status=500):
        data = message.encode('utf-8', errors='replace')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _safe_text_error(self, message, status=500):
        try:
            self._send_text_error(message, status)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_runtime_config(self):
        api_base = os.environ.get("CODE2VIDEO_API_BASE", "").rstrip("/")
        payload = f"window.CODE2VIDEO_API_BASE = {json.dumps(api_base)};\n".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _cors_origin(self):
        request_origin = self.headers.get("Origin")
        if "*" in ALLOWED_ORIGINS:
            return "*"
        if request_origin and request_origin in ALLOWED_ORIGINS:
            return request_origin
        return None

    def _origin_forbidden(self):
        request_origin = self.headers.get("Origin")
        return bool(request_origin and "*" not in ALLOWED_ORIGINS and request_origin not in ALLOWED_ORIGINS)

    def log_message(self, fmt, *args):
        if "200 -" in fmt % args: return 
        print(f"  {self.address_string()} -> {fmt % args}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = CODE2VIDEOServer((host, port), CODE2VIDEOHandler)
    url = f"http://localhost:{port}"
    
    print(f"\n  CODE2VIDEO ENGINE ACTIVE (Backend FFmpeg Mode)")
    print(f"  ----------------------------------------------")
    print(f"  URL -> {url}")
    print(f"  Renders are streamed to the browser and discarded after response.")
    
    if os.environ.get("CODE2VIDEO_NO_BROWSER") != "1":
        webbrowser.open(url)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
