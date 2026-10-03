"""
Simple OAuth callback server for Shopee authorization.
Run this before opening the Shopee auth URL.
It will capture shop_id and code from the redirect and print them clearly.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import webbrowser

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        shop_id = params.get("shop_id", ["NOT FOUND"])[0]
        code    = params.get("code",    ["NOT FOUND"])[0]

        print("\n" + "="*60)
        print("✅ SHOPEE CALLBACK RECEIVED!")
        print("="*60)
        print(f"  shop_id : {shop_id}")
        print(f"  code    : {code}")
        print("="*60)
        print("\nCopy these values — you'll need them next.")
        print("You can now close this server with Ctrl+C.\n")

        # Send a nice response page to the browser
        html = f"""<!DOCTYPE html>
<html>
<head><title>Shopee Auth Success</title>
<style>body{{font-family:sans-serif;text-align:center;padding:50px;background:#f0f9f0}}
h1{{color:#2e7d32}}pre{{background:#fff;padding:20px;border-radius:8px;display:inline-block;text-align:left;font-size:16px}}</style>
</head>
<body>
<h1>✅ Authorization Successful!</h1>
<pre>shop_id : {shop_id}
code    : {code}</pre>
<p>Copy these values, then close this tab.</p>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # suppress default request logs


if __name__ == "__main__":
    port = 8888
    print(f"🚀 Callback server running on http://localhost:{port}")
    print("Waiting for Shopee redirect...")
    print("(Keep this running, then open the Shopee auth URL in your browser)\n")
    server = HTTPServer(("", port), CallbackHandler)
    server.serve_forever()
