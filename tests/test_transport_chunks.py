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
