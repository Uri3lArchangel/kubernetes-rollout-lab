"""Small versioned HTTP service for rollout experiments."""
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(host, port, release, ready):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/livez":
                status, body = 200, {"alive": True}
            elif self.path == "/readyz":
                status, body = (200 if ready else 503), {"ready": ready}
            elif self.path == "/version":
                status, body = 200, {"release": release}
            else:
                status, body = 404, {"error": "not found"}
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    with make_server("0.0.0.0", 8080, os.getenv("RELEASE", "stable"),
                     os.getenv("READY", "true") == "true") as server:
        signal.signal(signal.SIGTERM,
                      lambda *_: threading.Thread(target=server.shutdown).start())
        server.serve_forever()
