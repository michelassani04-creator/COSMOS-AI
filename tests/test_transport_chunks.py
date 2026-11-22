import http.server
import threading
import time

from cosmos_ai.llm.http import UrllibTransport


def test_get_reads_whole_body_sent_in_pieces():
    payload = ("<a>" + "x" * 300_000 + "</a>").encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for i in range(0, len(payload), 20_000):
                self.wfile.write(payload[i : i + 20_000])
                self.wfile.flush()
                time.sleep(0.01)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        response = UrllibTransport().get(f"http://127.0.0.1:{server.server_port}/", {}, 5)
    finally:
        server.shutdown()
    assert len(response.body) == len(payload)


def test_get_decompresses_gzip_and_caps_size():
    import gzip

    plain = b"<a>" + b"y" * 100_000 + b"</a>"
    body = gzip.compress(plain)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        response = UrllibTransport().get(f"http://127.0.0.1:{server.server_port}/", {}, 5)
    finally:
        server.shutdown()
    assert response.body.encode() == plain


def test_rss_source_retries_once_after_network_error():
    from cosmos_ai.llm.http import HttpResponse, TransportError
    from cosmos_ai.news import RssNewsSource

    xml = "<rss><channel><item><title>T</title><link>https://x/1</link></item></channel></rss>"

    class Flaky:
        calls = 0

        def get(self, url, headers, timeout):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise TransportError("erreur réseau : BrokenPipeError")
            return HttpResponse(200, {}, xml)

    items = RssNewsSource("S", "https://x/feed", transport=Flaky()).fetch()
    assert Flaky.calls == 2 and items[0].title == "T"
