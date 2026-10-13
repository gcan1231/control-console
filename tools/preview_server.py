"""Read-only local preview of the fictional demo and two fixed diagrams."""
import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
DIAGRAMS = {
    "/diagrams/architecture.html": ROOT / ".archify/architecture-console-20261013-142225/architecture.html",
    "/diagrams/workflow.html": ROOT / ".archify/workflow-console-20261013-142225/workflow.html",
}
EXTENSIONS = {".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}


def resolve_request(request_path):
    path = urlsplit(request_path).path
    # Repeated encoding is rejected rather than decoded into another route.
    path = unquote(path, errors="strict")
    if "%" in path or "\\" in path or "\x00" in path:
        return None
    if not path.startswith("/") or any(part in {".", ".."} or part.startswith(".") for part in path.split("/") if part):
        return None
    if path in DIAGRAMS:
        return DIAGRAMS[path]
    relative = "index.html" if path == "/" else path[1:]
    target = (DEMO / relative).resolve()
    if not target.is_relative_to(DEMO.resolve()) or target.suffix.lower() not in EXTENSIONS:
        return None
    return target


class PreviewHandler(BaseHTTPRequestHandler):
    def serve_file(self, head=False):
        try:
            target = resolve_request(self.path)
            if target is None or not target.is_file():
                self.send_error(404, "Static file not found")
                return
            content = target.read_bytes()
        except (ValueError, UnicodeError, OSError):
            self.send_error(404, "Static file not found")
            return
        content_type = {".js": "text/javascript", ".mjs": "text/javascript"}.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(content)

    def do_GET(self):
        self.serve_file()

    def do_HEAD(self):
        self.serve_file(head=True)

    def do_POST(self):
        self.send_error(405, "Read-only preview; GET and HEAD only")


def main():
    parser = argparse.ArgumentParser(description="Local read-only demo preview")
    parser.add_argument("--port", type=int, default=8860)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    except OSError as error:
        parser.exit(1, f"Cannot bind 127.0.0.1:{args.port}: {error}\nChoose another --port; existing processes are left untouched.\n")
    print(f"Demo: http://127.0.0.1:{args.port}/?workspace=demo", flush=True)
    print("Read-only fictional demo. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
