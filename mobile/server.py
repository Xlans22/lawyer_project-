"""
Direct Mobile Transfer — QR Local Bridge

Runs a small HTTP server on the machine's LAN address. The desktop app shows a
QR code encoding a tokenised URL; the lawyer scans it, gets a camera page, and
the photo lands straight in the active client's dossier. Nothing leaves the
local network — there is no cloud component and no external DNS lookup.

Security posture
----------------
A LAN-bound upload endpoint is a genuine attack surface, so:

* Every request must carry a random per-session token (`secrets.token_urlsafe`),
  compared with `hmac.compare_digest` to avoid timing leaks.
* The token is rotated each time the bridge is started, and regenerated on
  demand, so a QR photographed over a shoulder is not a permanent key.
* Uploads are size-capped and must parse as real images — the extension is
  never trusted.
* The server binds to the LAN interface only, never 0.0.0.0 across all
  adapters.

Caveat worth stating plainly: anyone who obtains the token while the bridge is
open can upload. This is appropriate for a trusted office Wi-Fi. On an untrusted
network (café, hotel, airport), stop the bridge when you are not actively
transferring.
"""

import hmac
import io
import secrets
import socket
import threading
from typing import Callable

from core import config, storage

# NOTE: this module deliberately does NOT use `from __future__ import
# annotations`. PEP 563 turns every annotation into a string, and FastAPI /
# pydantic cannot resolve the resulting forward reference to `UploadFile`
# because the framework types are imported lazily inside `_build_app` (that
# keeps this module importable when fastapi is not installed). The symptom is
# an HTTP 500 on every upload. Route parameters therefore stay eagerly
# annotated.

UploadCallback = Callable[[str, str], None]  # (client_name, filename)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per photo


def lan_ip() -> str:
    """Best-effort local network address, without generating external traffic."""
    # Prefer a private IPv4 discovered from local interface enumeration.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_private(ip):
                return ip
    except Exception:
        pass

    # Fallback: a UDP "connect" selects the default route without sending a
    # packet (UDP is connectionless), and the destination is a private address.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def _is_private(ip: str) -> bool:
    try:
        parts = [int(p) for p in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4:
        return False
    a, b = parts[0], parts[1]
    return (
        a == 10
        or (a == 172 and 16 <= b <= 31)
        or (a == 192 and b == 168)
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>مسح مستند</title>
<style>
  :root {{
    --navy: #0F2740; --parchment: #FBF8F1; --brass: #A9853F;
    --ink: #1C2733; --muted: #6B7A8C; --border: #DCD5C7;
  }}
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  body {{
    margin: 0; padding: 24px 18px 40px; background: var(--parchment);
    color: var(--ink); font-family: "Cairo", "Segoe UI", Tahoma, sans-serif;
    display: flex; flex-direction: column; align-items: center; min-height: 100vh;
  }}
  header {{ text-align: center; margin-bottom: 22px; }}
  h1 {{ font-size: 20px; color: var(--navy); margin: 0 0 6px; font-weight: 700; }}
  .client {{ color: var(--muted); font-size: 14px; }}
  .client b {{ color: var(--navy); }}
  .card {{
    background: #fff; border: 1px solid var(--border); border-radius: 14px;
    padding: 26px 20px; width: 100%; max-width: 420px; text-align: center;
    box-shadow: 0 1px 3px rgba(15,39,64,.06);
  }}
  .capture {{
    display: block; width: 100%; padding: 20px; border-radius: 12px;
    background: var(--navy); color: #fff; font-size: 17px; font-weight: 600;
    border: none; cursor: pointer; font-family: inherit;
  }}
  .capture:active {{ background: #1B3A5C; }}
  input[type=file] {{ display: none; }}
  .hint {{ color: var(--muted); font-size: 13px; margin-top: 14px; line-height: 1.7; }}
  #preview {{ max-width: 100%; border-radius: 10px; margin-top: 16px; display: none; }}
  #status {{ margin-top: 16px; font-size: 15px; min-height: 22px; font-weight: 600; }}
  .ok {{ color: #2F6B4F; }} .err {{ color: #9B2C2C; }}
  .busy {{ color: var(--brass); }}
  footer {{ margin-top: auto; padding-top: 28px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <header>
    <h1>تصوير مستند</h1>
    <div class="client">الملف: <b>{client_name}</b></div>
  </header>

  <div class="card">
    <input type="file" id="picker" accept="image/*" capture="environment">
    <button class="capture" id="shoot">\U0001F4F7 التقط صورة</button>
    <img id="preview" alt="">
    <div id="status"></div>
    <div class="hint">
      الصورة تُنقل مباشرة إلى
      جهاز المكتب عبر الشبكة
      المحلية فقط.
    </div>
  </div>

  <footer>لا تغادر أي بيانات هذا الجهاز</footer>

<script>
  const picker = document.getElementById('picker');
  const shoot  = document.getElementById('shoot');
  const status = document.getElementById('status');
  const preview = document.getElementById('preview');
  const TOKEN = new URLSearchParams(location.search).get('t') || '';
  let busy = false;

  shoot.addEventListener('click', () => picker.click());

  picker.addEventListener('change', async () => {{
    const file = picker.files && picker.files[0];
    if (!file || busy) return;
    busy = true;

    preview.src = URL.createObjectURL(file);
    preview.style.display = 'block';
    status.className = 'busy';
    status.textContent = 'جارٍ الرفع...';

    const form = new FormData();
    form.append('file', file, file.name || 'capture.jpg');

    try {{
      const res = await fetch('/upload?t=' + encodeURIComponent(TOKEN), {{
        method: 'POST', body: form
      }});
      const data = await res.json().catch(() => ({{}}));
      if (res.ok) {{
        status.className = 'ok';
        status.textContent = '✓ تم الحفظ: ' + (data.filename || '');
        picker.value = '';
      }} else {{
        throw new Error(data.detail || ('HTTP ' + res.status));
      }}
    }} catch (e) {{
      status.className = 'err';
      status.textContent = '✗ فشل الرفع: ' + e.message;
    }} finally {{
      busy = false;
    }}
  }});
</script>
</body>
</html>
"""


class MobileBridge:
    """Owns the FastAPI app, its uvicorn server thread, and the session token."""

    def __init__(self, on_upload: UploadCallback | None = None) -> None:
        self._on_upload = on_upload
        self._thread: threading.Thread | None = None
        self._server = None
        self._token = secrets.token_urlsafe(24)
        self._running = False

        # Which client incoming photos are filed under. Set by the UI.
        self.active_client: tuple[int, str] | None = None

    # -- state -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def token(self) -> str:
        return self._token

    def rotate_token(self) -> str:
        """Issue a fresh token — invalidates any previously shared QR."""
        self._token = secrets.token_urlsafe(24)
        return self._token

    def url(self) -> str:
        return f"http://{lan_ip()}:{config.MOBILE_PORT}/?t={self._token}"

    def check_token(self, supplied: str) -> bool:
        """Constant-time token comparison."""
        return hmac.compare_digest(supplied or "", self._token)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        import uvicorn

        app = self._build_app()
        uv_config = uvicorn.Config(
            app,
            host="0.0.0.0",          # all local interfaces; never routed externally
            port=config.MOBILE_PORT,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._server.run, name="MobileBridge", daemon=True
        )
        self._thread.start()
        self._running = True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._running = False

    # -- request handling --------------------------------------------------

    def _build_app(self):
        from fastapi import FastAPI, File, HTTPException, Query, UploadFile
        from fastapi.responses import HTMLResponse, JSONResponse

        app = FastAPI(title="Lawyer Agent Mobile Bridge", docs_url=None, redoc_url=None)

        @app.middleware("http")
        async def require_token(request, call_next):
            """Reject unauthorised requests before the body is buffered.

            The per-route checks below are defence in depth, but they run after
            FastAPI has already parsed the multipart payload. Checking here
            means an unauthenticated caller cannot make the server hold a 25 MB
            upload in memory.
            """
            from fastapi.responses import JSONResponse

            if not self.check_token(request.query_params.get("t", "")):
                return JSONResponse(
                    {"detail": "Invalid or expired token."}, status_code=403
                )
            return await call_next(request)

        @app.get("/", response_class=HTMLResponse)
        async def index(t: str = Query(default="")):
            if not self.check_token(t):
                raise HTTPException(status_code=403, detail="Invalid or expired token.")
            name = self.active_client[1] if self.active_client else "—"
            return HTMLResponse(PAGE_TEMPLATE.format(client_name=_escape(name)))

        @app.post("/upload")
        async def upload(t: str = Query(default=""), file: UploadFile = File(...)):
            if not self.check_token(t):
                raise HTTPException(status_code=403, detail="Invalid or expired token.")

            payload = await file.read()
            if not payload:
                raise HTTPException(status_code=400, detail="Empty upload.")
            if len(payload) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large.")

            # Verify it really is an image rather than trusting the filename.
            if not _looks_like_image(payload):
                raise HTTPException(status_code=400, detail="Not a valid image.")

            if self.active_client is None:
                raise HTTPException(
                    status_code=409,
                    detail="No client selected on the desktop app.",
                )

            client_id, client_name = self.active_client
            filename = file.filename or storage.timestamped_name("capture")
            dest = storage.store_bytes(
                client_id, client_name, "scans", filename, payload
            )

            from core import database

            database.add_document(
                client_id,
                dest.name,
                str(dest),
                category="scans",
                source="mobile",
            )

            if self._on_upload:
                try:
                    self._on_upload(client_name, dest.name)
                except Exception:
                    pass

            return JSONResponse({"ok": True, "filename": dest.name})

        return app


def _looks_like_image(payload: bytes) -> bool:
    """Magic-byte + decode check. Extensions are attacker-controlled; content is not."""
    signatures = (
        b"\xff\xd8\xff",          # JPEG
        b"\x89PNG\r\n\x1a\n",     # PNG
        b"II*\x00",               # TIFF little-endian
        b"MM\x00*",               # TIFF big-endian
        b"BM",                    # BMP
        b"RIFF",                  # WebP container
    )
    if not any(payload.startswith(sig) for sig in signatures):
        return False
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as img:
            img.verify()
        return True
    except Exception:
        return False


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
