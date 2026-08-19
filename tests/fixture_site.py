import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# 1x1 transparent PNG bytes
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00"
    b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FixtureRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # Suppress standard HTTP request logging in test runs
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if not path:
            path = "/"

        if path == "/ok.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG_BYTES)))
            self.end_headers()
            self.wfile.write(_PNG_BYTES)
            return

        if path in {"/missing", "/failed.png"}:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return

        html = ""
        if path == "/clean":
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Clean Page</title>
    <meta name="description" content="A clean test page.">
</head>
<body>
    <h1>Main Header</h1>
    <p>Everything is valid here.</p>
    <img src="/ok.png" alt="Valid image">
</body>
</html>"""
        elif path == "/seo":
            html = """<!DOCTYPE html>
<html>
<head>
</head>
<body>
    <h1>SEO Issue Page</h1>
    <a href="/seo/a">Page A</a>
    <a href="/seo/b">Page B</a>
</body>
</html>"""
        elif path == "/seo/a":
            html = """<!DOCTYPE html>
<html>
<head>
</head>
<body>
    <h1>SEO Issue Page A</h1>
</body>
</html>"""
        elif path == "/seo/b":
            html = """<!DOCTYPE html>
<html>
<head>
</head>
<body>
    <h1>SEO Issue Page B</h1>
</body>
</html>"""
        elif path in {"/js", "/js/a", "/js/b"}:
            sub = path.replace("/js", "") or " Root"
            html = f"""<!DOCTYPE html>
<html>
<head>
    <title>JavaScript Issue Page{sub}</title>
    <meta name="description" content="JS error page">
</head>
<body>
    <h1>JavaScript Error</h1>
    <a href="/js/a">Subpage A</a>
    <a href="/js/b">Subpage B</a>
    <script>
        console.error("shared widget failure");
    </script>
</body>
</html>"""
        elif path == "/link-resource":
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Broken Link & Resource</title>
    <meta name="description" content="Link and resource errors">
</head>
<body>
    <h1>Broken Link and Resource</h1>
    <a href="/missing">Missing Link</a>
    <img src="/failed.png" alt="Broken image">
</body>
</html>"""
        elif path == "/mixed":
            html = """<!DOCTYPE html>
<html>
<head>
    <meta name="description" content="Mixed issues page">
</head>
<body>
    <h1>Mixed Issues</h1>
    <a href="/mixed/a">Mixed A</a>
    <a href="/mixed/b">Mixed B</a>
    <script>
        console.error("seed-only error");
    </script>
</body>
</html>"""
        elif path == "/mixed/a":
            html = """<!DOCTYPE html>
<html>
<head>
    <meta name="description" content="Mixed A">
</head>
<body>
    <h1>Mixed A</h1>
</body>
</html>"""
        elif path == "/mixed/b":
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Mixed B Clean</title>
    <meta name="description" content="Mixed B clean description">
</head>
<body>
    <h1>Mixed B</h1>
</body>
</html>"""
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FixtureSite:
    """Threaded HTTP server providing reproducible test scenarios."""

    def __init__(self, port: int = 0) -> None:
        self._requested_port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> "FixtureSite":
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", self._requested_port),
            FixtureRequestHandler,
        )
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def url(self, path: str = "/") -> str:
        if self._server is None:
            raise RuntimeError("FixtureSite is not started")
        host, port = self._server.server_address
        norm_path = path if path.startswith("/") else f"/{path}"
        return f"http://{host}:{port}{norm_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local fixture test server.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind to (default: 8765)")
    args = parser.parse_args()

    with FixtureSite(port=args.port) as site:
        print(f"Fixture site running at: {site.url('/')}")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping fixture server...")


if __name__ == "__main__":
    main()
