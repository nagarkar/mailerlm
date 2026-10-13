"""The web server: host and write checks, dispatch, error bodies, uploads, page files."""

import http.client
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from mailerlm import api, server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def running(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    httpd = server.make_server("127.0.0.1", 0, "2026-10-11T00:00:00+00:00")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def post(port, payload, headers=None):
    base = {"Content-Type": "application/json", "X-Mailerlm": "1"}
    base.update(headers or {})
    status, data = request(port, "POST", "/api/mailerlm", json.dumps(payload), base)
    return status, json.loads(data)


def test_health_and_the_page_files(running):
    status, data = request(running, "GET", "/health")
    assert status == 200 and json.loads(data)["started_at"]
    assert b"mailerlm" in request(running, "GET", "/")[1]
    assert request(running, "GET", "/static/app.js")[0] == 200
    assert request(running, "GET", "/static/nope.js")[0] == 404


def test_a_foreign_host_gets_421(running):
    assert request(running, "GET", "/health", headers={"Host": "evil.example.com"})[0] == 421
    assert request(running, "GET", "/health", headers={"Host": "imac.tail0000.ts.net"})[0] == 200


def test_the_write_check_rejects_a_bad_request(running):
    body = json.dumps({"method": "overview"})
    assert (
        request(running, "POST", "/api/mailerlm", body, {"Content-Type": "application/json"})[0]
        == 403
    )
    assert (
        request(
            running,
            "POST",
            "/api/mailerlm",
            body,
            {"Content-Type": "text/plain", "X-Mailerlm": "1"},
        )[0]
        == 415
    )
    headers = {
        "Content-Type": "application/json",
        "X-Mailerlm": "1",
        "Origin": "http://evil.example.com",
    }
    assert request(running, "POST", "/api/mailerlm", body, headers)[0] == 403
    big = json.dumps({"method": "overview", "params": {"x": "a" * 70000}})
    assert (
        request(
            running,
            "POST",
            "/api/mailerlm",
            big,
            {"Content-Type": "application/json", "X-Mailerlm": "1"},
        )[0]
        == 413
    )
    assert (
        request(
            running,
            "POST",
            "/api/mailerlm",
            "[1]",
            {"Content-Type": "application/json", "X-Mailerlm": "1"},
        )[0]
        == 400
    )
    assert request(running, "OPTIONS", "/api/mailerlm")[0] != 200


def test_a_call_returns_a_result_a_bad_call_400_a_missing_thing_404(running):
    status, body = post(running, {"method": "overview"})
    assert status == 200 and body["result"]["contacts"] == 0
    assert post(running, {"method": "nope"})[0] == 400
    assert post(running, {"method": "campaigns_get", "params": {"name": "ghost"}})[0] == 404
    assert post(running, {"method": "lists_add", "params": {"nope": 1}})[0] == 400
    status, body = post(running, {"method": "lists_add", "params": {"name": ""}})
    assert status == 400 and "needs a name" in body["error"]


def test_a_list_param_becomes_a_tuple_where_the_api_wants_one(running):
    post(running, {"method": "lists_add", "params": {"name": "a"}})
    status, body = post(
        running,
        {
            "method": "contacts_import_text",
            "params": {"text": "ada@example.com", "list_name": "a", "tags": ["x", "y"]},
        },
    )
    assert status == 200 and body["result"]["tags_added"] == 2


def test_an_unexpected_error_names_only_its_type(running, monkeypatch):
    def boom():
        raise RuntimeError("secret contact text")

    monkeypatch.setattr(api, "overview", boom)
    status, body = post(running, {"method": "overview"})
    assert status == 500 and body == {"error": "RuntimeError"}


def test_every_listed_method_is_a_typed_api_function():
    for name in server.METHODS:
        assert callable(getattr(api, name)), name


def test_an_image_is_uploaded_and_served_and_a_bad_upload_is_rejected(running):
    import io

    from PIL import Image

    post(running, {"method": "campaigns_create", "params": {"name": "c", "starter": "update"}})
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buffer, "PNG")
    headers = {"Content-Type": "image/png", "X-Mailerlm": "1"}
    status, data = request(running, "PUT", "/upload/c/pic.png", buffer.getvalue(), headers)
    assert status == 200 and json.loads(data)["result"]["image"]["mime"] == "image/png"
    status, data = request(running, "GET", "/image/c/pic.png")
    assert status == 200 and data == buffer.getvalue()
    assert (
        request(running, "PUT", "/upload/c/pic.png", b"x", {"Content-Type": "image/png"})[0] == 403
    )
    assert (
        request(
            running,
            "PUT",
            "/upload/c/pic.png",
            b"x",
            {"Content-Type": "text/plain", "X-Mailerlm": "1"},
        )[0]
        == 415
    )
    assert request(running, "PUT", "/upload/c/bad.png", b"not an image", headers)[0] == 400
    assert request(running, "PUT", "/upload/ghost/pic.png", buffer.getvalue(), headers)[0] == 404


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_parses():
    out = subprocess.run(
        ["node", "--check", str(ROOT / "src/mailerlm/web/app.js")], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr


def test_the_server_binds_loopback_only():
    with pytest.raises(ValueError, match="loopback"):
        server.make_server("0.0.0.0", 0, "now")


def test_every_method_the_page_calls_is_reachable():
    import re

    script = (ROOT / "src/mailerlm/web/app.js").read_text()
    called = set(re.findall(r'api\("([a-z_]+)"', script))
    assert called, "the page calls no methods?"
    assert called <= set(server.METHODS) | {"overview"}, sorted(called - set(server.METHODS))
