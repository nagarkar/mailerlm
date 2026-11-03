"""The review server: the web app on this Mac's loopback address, fronted by `tailscale serve`.

Standard library only (metalm coding.md, local review apps). `GET /` and `GET /static/<file>` serve
the page files from `mailerlm/web`; `GET /brand/<file>` serves the app icons and wordmarks copied
from metalm's `brand/mailerlm` into `mailerlm/web/brand` (metalm ui.md#brand); `GET /health` reports the start time; `GET /image/<campaign>/<name>`
serves an uploaded picture; `POST /api/mailerlm` takes `{method, params}` and calls the typed API
function of that name; `PUT /upload/<campaign>/<name>` stores one picture.

Never imports: cli (enforced by the import contract "surfaces on top" in pyproject.toml).

Decisions:
- The server binds the loopback address only and answers only Host headers that name this machine
  (loopback or `*.ts.net`), else 421. Enforced by: tests/test_server.py::test_a_foreign_host_gets_421. (owner)
- Every POST and PUT passes one write check: header `X-Mailerlm`, Content-Type, Origin (when sent) equal
  to Host, and a size limit; there is no CORS, no cookie and no token, and OPTIONS is never answered.
  Enforced by: tests/test_server.py::test_the_write_check_rejects_a_bad_request. (owner)
- The JSON body is at most 64 KB. A picture upload is a raw `PUT` of at most 8 MB, the one exception,
  because a picture does not fit in 64 KB; it passes the same header and Origin checks. (owner)
- Errors answer `{error: message}`: 400 for a bad request, 404 for a missing thing, and for anything
  unexpected only the exception's type, never stored text. Enforced by:
  tests/test_server.py::test_an_unexpected_error_names_only_its_type. (owner)
- One lock serialises every write, so two taps never interleave. (owner)
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from mailerlm import api
from mailerlm.campaigns.repository import MAX_IMAGE_BYTES
from mailerlm.errors import MailerlmError, NotFoundError

APP = "mailerlm"
PORT = 8782
MAX_BODY_BYTES = 64 * 1024
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
WEB_DIRECTORY = Path(__file__).parent / "web"
METHODS = (
    "overview",
    "lists_show",
    "lists_add",
    "lists_edit",
    "lists_delete",
    "contacts_find",
    "contacts_import_text",
    "contacts_add",
    "contacts_edit",
    "contacts_delete",
    "contacts_tag",
    "contacts_untag",
    "contacts_join",
    "contacts_leave",
    "contacts_do_not_mail",
    "settings_get",
    "settings_save",
    "templates_list",
    "templates_get",
    "templates_draft_save",
    "templates_draft_discard",
    "templates_publish",
    "templates_ai_edit",
    "templates_thread_clear",
    "templates_duplicate",
    "templates_preview",
    "templates_reset",
    "templates_delete",
    "campaigns_list",
    "campaigns_starters",
    "campaigns_get",
    "campaigns_create",
    "campaigns_save",
    "campaigns_duplicate",
    "campaigns_delete",
    "campaigns_image_remove",
    "campaigns_check",
    "campaigns_preview",
    "sends_plan",
    "sends_test",
    "sends_start",
    "sends_resume",
    "sends_status",
    "sends_list",
    "sends_poll",
    "replies_check",
    "replies_list",
    "replies_decide",
    "replies_undo",
)
UNLOCKED = frozenset(
    {"templates_ai_edit", "replies_check"}
)  # a slow model call must not hold the one write lock
_STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
_BRAND = {
    "favicon.ico": "image/x-icon",
    "favicon-32.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "logo.png": "image/png",
    "logo-wordmark-light.png": "image/png",
    "logo-wordmark-dark.png": "image/png",
}
_IMAGE_PATH = re.compile(r"^/(image|upload)/([^/]+)/([^/]+)$")
write_lock = threading.Lock()


def host_is_this_machine(host_header: str) -> bool:
    if host_header.startswith("[") and "]" in host_header:
        name = host_header[: host_header.index("]") + 1]
    else:
        name = host_header.rsplit(":", 1)[0]
    return name in LOOPBACK_HOSTS or name.endswith(".ts.net")


def call(method: object, params: object) -> object:
    """Dispatch one API call by name; only the listed methods are reachable."""
    if method == "health":
        return {"status": "ok"}
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError("unknown method")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    function = getattr(api, method)
    parameters = inspect.signature(function).parameters
    unknown = sorted(set(params) - set(parameters))
    if unknown:
        raise ValueError(f"unknown parameter '{unknown[0]}' for {method}")
    arguments = {
        key: tuple(value)
        if isinstance(value, list) and "tuple" in str(parameters[key].annotation)
        else value
        for key, value in params.items()
    }
    try:
        return function(**arguments)
    except TypeError as error:
        raise ValueError(f"bad parameters for {method}") from error


def make_server(host: str, port: int, started_at: str) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(f"the server binds the loopback address only, not {host!r}")

    class Handler(BaseHTTPRequestHandler):
        server_version = "mailerlm"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # no request line (it could carry a path) is written anywhere

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: object) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        def _this_machine(self) -> bool:
            if host_is_this_machine(self.headers.get("Host", "")):
                return True
            self._json(421, {"error": "this server answers requests for this machine only"})
            return False

        def _write_check(self, content_type_ok: bool, limit: int) -> int | None:
            """The one write check; returns the body length, or None after answering an error."""
            if not content_type_ok:
                self._json(415, {"error": "unsupported Content-Type"})
            elif self.headers.get(f"X-{APP.capitalize()}") is None:
                self._json(403, {"error": f"the X-{APP.capitalize()} header is missing"})
            elif (origin := self.headers.get("Origin")) is not None and origin.split("://", 1)[
                -1
            ] != self.headers.get("Host"):
                self._json(403, {"error": "Origin does not match Host"})
            else:
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if length < 0:
                    self._json(400, {"error": "Content-Length is required"})
                elif length > limit:
                    self._json(413, {"error": f"the body is over {limit // 1024} KB"})
                else:
                    return length
            return None

        def do_GET(self) -> None:  # noqa: N802
            if not self._this_machine():
                return
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {"status": "ok", "started_at": started_at})
            elif path in ("/", "/index.html"):
                self._send(
                    200, (WEB_DIRECTORY / "index.html").read_bytes(), "text/html; charset=utf-8"
                )
            elif path.startswith("/static/") and path[8:] in _STATIC:
                self._send(200, (WEB_DIRECTORY / path[8:]).read_bytes(), _STATIC[path[8:]])
            elif path.startswith("/brand/") and path[7:] in _BRAND:
                self._send(200, (WEB_DIRECTORY / "brand" / path[7:]).read_bytes(), _BRAND[path[7:]])
            elif path == "/favicon.ico":
                self._send(
                    200, (WEB_DIRECTORY / "brand" / "favicon.ico").read_bytes(), "image/x-icon"
                )
            elif (match := _IMAGE_PATH.match(path)) and match[1] == "image":
                found = api.campaigns_image_data(unquote(match[2]), unquote(match[3]))
                if found is None:
                    self._json(404, {"error": "not found"})
                else:
                    self._send(200, found[0], found[1])
            else:
                self._json(404, {"error": "not found"})

        def do_PUT(self) -> None:  # noqa: N802
            if not self._this_machine():
                return
            match = _IMAGE_PATH.match(self.path)
            if not match or match[1] != "upload":
                self._json(404, {"error": "not found"})
                return
            length = self._write_check(
                self.headers.get("Content-Type", "").startswith("image/"), MAX_IMAGE_BYTES
            )
            if length is None:
                return
            data = self.rfile.read(length)
            self._answer(
                lambda: api.campaigns_image_add(unquote(match[2]), unquote(match[3]), data)
            )

        def do_POST(self) -> None:  # noqa: N802
            if not self._this_machine():
                return
            if self.path != f"/api/{APP}":
                self._json(404, {"error": "not found"})
                return
            length = self._write_check(
                self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json",
                MAX_BODY_BYTES,
            )
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("the body must be a JSON object")
            except (ValueError, UnicodeDecodeError) as error:
                self._json(400, {"error": str(error)})
                return
            self._answer(
                lambda: call(body.get("method"), body.get("params", {})),
                locked=body.get("method") not in UNLOCKED,
            )

        def _answer(self, work, locked: bool = True) -> None:
            try:
                if locked:
                    with write_lock:
                        result = work()
                else:
                    result = work()
            except NotFoundError as error:
                self._json(404, {"error": str(error)})
            except (ValueError, MailerlmError, UnicodeDecodeError) as error:
                self._json(400, {"error": str(error)})
            except Exception as error:  # noqa: BLE001 - the type alone is answered
                self._json(500, {"error": type(error).__name__})
            else:
                self._json(200, {"result": result})

    class Server(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True

    return Server((host, port), Handler)
