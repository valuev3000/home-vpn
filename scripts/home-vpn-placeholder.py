#!/usr/bin/env python3
"""Tiny static placeholder server intended for an edge/Xray fallback."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = os.getenv("PLACEHOLDER_BIND", "127.0.0.1")
PORT = int(os.getenv("PLACEHOLDER_PORT", "8181"))
HTML_FILE = os.getenv("PLACEHOLDER_HTML", "/etc/home-vpn/placeholder.html")


class Handler(BaseHTTPRequestHandler):
    def send_page(self, body=True):
        try:
            with open(HTML_FILE, "rb") as source: content = source.read()
        except OSError:
            content = b"<!doctype html><meta charset=utf-8><title>Service</title><h1>Service is online</h1>"
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content))); self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.end_headers()
        if body: self.wfile.write(content)

    def do_GET(self): self.send_page()
    def do_HEAD(self): self.send_page(False)
    def log_message(self, *_): pass


if __name__ == "__main__": ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
