"""
シンプルなリバースプロキシ（ngrok1本で2つのMCPサーバーに振り分け）

/mcp-graph/* → localhost:8011/*
/mcp-mongo/* → localhost:3101/*

起動: python3 nginx/proxy.py
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error


class ProxyHandler(BaseHTTPRequestHandler):
    def _proxy(self):
        if self.path.startswith("/mcp-graph"):
            target = "http://localhost:8011" + self.path.removeprefix("/mcp-graph")
        elif self.path.startswith("/mcp-mongo"):
            target = "http://localhost:3101" + self.path.removeprefix("/mcp-mongo")
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found. Use /mcp-graph/* or /mcp-mongo/*")
            return

        # リクエストボディを読む
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # ヘッダーを転送
        headers = {}
        for key, value in self.headers.items():
            if key.lower() not in ("host", "transfer-encoding"):
                headers[key] = value

        try:
            req = urllib.request.Request(
                target,
                data=body,
                headers=headers,
                method=self.command,
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding",):
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"Proxy Error: {e}".encode())

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def log_message(self, format, *args):
        print(f"[proxy] {args[0]}")


if __name__ == "__main__":
    port = 9000
    print(f"Reverse proxy running on http://localhost:{port}")
    print(f"  /mcp-graph/* -> http://localhost:8011/*")
    print(f"  /mcp-mongo/* -> http://localhost:3101/*")
    HTTPServer(("", port), ProxyHandler).serve_forever()
