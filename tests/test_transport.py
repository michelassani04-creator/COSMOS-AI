"""Le vrai transport HTTP, testé contre un serveur local (aucun accès à Internet)."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cosmos_ai.llm.http import TransportError, UrllibTransport


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.received.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )
        time.sleep(self.server.delay)
        status, headers, payload = self.server.reply
        data = payload.encode()
        try:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:  # le client a abandonné (test du délai d'attente)
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "127.0.0.1")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.received, srv.delay, srv.reply = [], 0.0, (200, {}, "{}")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def url(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}/v1/test"


def test_success_sends_json_and_headers(server):
    server.reply = (200, {"X-Custom": "oui"}, '{"ok": true}')
    r = UrllibTransport().post_json(url(server), {"x-goog-api-key": "k"}, {"a": 1}, 5)
    assert (r.status, r.body) == (200, '{"ok": true}')
    assert r.headers["x-custom"] == "oui"

    got = server.received[0]
    received = {k.lower(): v for k, v in got["headers"].items()}  # HTTP : casse indifférente
    assert got["body"] == {"a": 1}
    assert received["content-type"] == "application/json"
    assert received["x-goog-api-key"] == "k"
    assert received["user-agent"].startswith("cosmos-ai")


def test_http_errors_are_returned_not_raised(server):
    server.reply = (429, {"Retry-After": "9"}, '{"error": "limite"}')
    r = UrllibTransport().post_json(url(server), {}, {}, 5)
    assert r.status == 429
    assert r.headers["retry-after"] == "9"
    assert "limite" in r.body


def test_timeout_is_reported_as_timeout(server):
    server.delay = 1.0
    with pytest.raises(TransportError) as info:
        UrllibTransport().post_json(url(server), {}, {}, 0.2)
    assert info.value.timeout is True


def test_connection_refused_is_a_network_error(monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "127.0.0.1")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(TransportError) as info:
        UrllibTransport().post_json(f"http://127.0.0.1:{port}/", {}, {}, 2)
    assert info.value.timeout is False
