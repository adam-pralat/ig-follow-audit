"""Serve the Follow Check dashboard on localhost.

    python server.py            # imports anything new in ./exports, then opens the dashboard
    python server.py --port 9000 --no-browser

While it runs, ./exports is watched: drop a new export in and it's imported automatically.
"""
import argparse
import json
import threading
import time
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ingest
from report import build_report

WEB_DIR = Path(__file__).resolve().parent / "web"
WATCH_INTERVAL = 3  # seconds between checks of the exports folder

# The watcher thread and the Rescan button can both trigger an import; run one at a time.
ingest_lock = threading.Lock()


def run_ingest(db_path, exports_dir, delete_imported, log=print):
    with ingest_lock:
        return ingest.ingest(db_path, exports_dir, log=log, delete_imported=delete_imported)


def fingerprint(exports_dir):
    """Name, size and modified time of everything under exports/, to notice changes cheaply."""
    entries = []
    for path in exports_dir.rglob("*"):
        try:
            stat = path.stat()
        except OSError:  # removed or renamed mid-scan
            continue
        entries.append((str(path), stat.st_size, stat.st_mtime_ns))
    return sorted(entries)


def watch_exports(exports_dir, seen, do_ingest):
    """Import whenever exports/ changes, once it has stopped changing (so zips are fully written)."""
    candidate = seen
    while True:
        time.sleep(WATCH_INTERVAL)
        current = fingerprint(exports_dir)
        if current != candidate:  # still being written, check again next tick
            candidate = current
            continue
        if current != seen:
            do_ingest(log=lambda msg: print(f"[watch] {msg}", flush=True))
            seen = current


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, db_path, do_ingest, **kwargs):
        self.db_path = db_path
        self.do_ingest = do_ingest
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/status":
            conn = ingest.connect(self.db_path)
            try:
                latest = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]
            finally:
                conn.close()
            return self.send_json({"latest": latest})
        if url.path == "/api/report":
            snapshot = parse_qs(url.query).get("snapshot", [None])[0]
            conn = ingest.connect(self.db_path)
            try:
                return self.send_json(build_report(conn, int(snapshot) if snapshot else None))
            finally:
                conn.close()
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/ingest":
            messages = []
            count = self.do_ingest(log=messages.append)
            return self.send_json({"imported": count, "messages": messages})
        if url.path == "/api/ignore":
            body = self.read_json()
            username = str(body.get("username", "")).strip().lower()
            if not username:
                return self.send_json({"error": "username required"}, 400)
            conn = ingest.connect(self.db_path)
            with conn:
                if body.get("ignored", True):
                    conn.execute("INSERT OR IGNORE INTO ignored (username, added_at) VALUES (?, ?)",
                                 (username, datetime.now(timezone.utc).isoformat(timespec="seconds")))
                else:
                    conn.execute("DELETE FROM ignored WHERE username = ?", (username,))
            conn.close()
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open the dashboard automatically")
    parser.add_argument("--exports", type=Path, default=ingest.DEFAULT_EXPORTS)
    parser.add_argument("--db", type=Path, default=ingest.DEFAULT_DB)
    parser.add_argument("--keep-exports", action="store_true", help="don't delete exports after importing")
    args = parser.parse_args()

    args.exports.mkdir(exist_ok=True)
    do_ingest = partial(run_ingest, args.db, args.exports, not args.keep_exports)
    # Fingerprint before the first import, so anything that lands in between still gets picked up.
    seen = fingerprint(args.exports)
    do_ingest()
    threading.Thread(target=watch_exports, args=(args.exports, seen, do_ingest), daemon=True).start()

    handler = partial(Handler, db_path=args.db, do_ingest=do_ingest)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}"
    print(f"Follow Check running at {url}  (Ctrl+C to stop)")
    print(f"Watching {args.exports} for new exports")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
