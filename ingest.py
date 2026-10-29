"""Import Instagram "Download your information" exports into a local SQLite database.

Drop the export (the .zip, or the extracted folder) into ./exports and run:

    python ingest.py

Each export becomes one snapshot. Exports that were already imported are skipped,
so it's safe to run as often as you like. Both the JSON and HTML export formats work.
Imported exports are deleted from ./exports unless you pass --keep-exports.
"""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
DEFAULT_EXPORTS = ROOT / "exports"
DEFAULT_DB = ROOT / "instagram.db"

# Export file name (minus any _1, _2 page suffix) -> relation kind stored in the db.
FILE_KINDS = {
    "followers": "followers",
    "following": "following",
    "pending_follow_requests": "pending",
    "recently_unfollowed_accounts": "unfollowed",
    "recently_unfollowed_profiles": "unfollowed",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    export_date  TEXT NOT NULL,
    imported_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    username    TEXT NOT NULL,
    ts          INTEGER,
    PRIMARY KEY (snapshot_id, kind, username)
);
CREATE TABLE IF NOT EXISTS ignored (
    username TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
"""

PROFILE_RE = re.compile(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]+)")
ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>.*?</a>', re.S | re.I)
DATE_RE = re.compile(
    r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})(?:,?\s+(\d{1,2}):(\d{2})\s*([ap]m)?)?", re.I
)
NAME_DATE_RE = re.compile(r"(20\d{2})-?(\d{2})-?(\d{2})")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
# Paths on instagram.com that aren't profiles, in case they show up in HTML exports.
NOT_PROFILES = {"accounts", "explore", "p", "reel", "reels", "stories", "direct", "about", "legal"}


def connect(db_path=DEFAULT_DB):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _add(found, username, ts):
    """Record a username, keeping the earliest timestamp seen for it."""
    username = (username or "").strip().lstrip("@").lower()
    if not username or username in NOT_PROFILES:
        return
    ts = int(ts) if ts else None
    if found.get(username) is None:
        found[username] = ts
    elif ts is not None:
        found[username] = min(found[username], ts)


def parse_json(data):
    """Walk any export JSON shape and pull out every entry that has string_list_data."""
    found = {}

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "string_list_data" in node:
                for entry in node["string_list_data"] or [{}]:
                    href_match = PROFILE_RE.search(entry.get("href") or "")
                    username = entry.get("value") or node.get("title") or (href_match and href_match.group(1))
                    _add(found, username, entry.get("timestamp"))
            else:
                for value in node.values():
                    walk(value)

    walk(json.loads(data))
    return found


def _parse_date(text):
    m = DATE_RE.search(text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    month, day, year = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
    hour, minute = int(m.group(4) or 0), int(m.group(5) or 0)
    if m.group(6):
        hour = hour % 12 + (12 if m.group(6).lower() == "pm" else 0)
    try:
        return int(datetime(year, month, day, hour, minute).timestamp())
    except ValueError:
        return None


def parse_html(text):
    """Each profile link in an HTML export is followed by the date it was added."""
    found = {}
    anchors = list(ANCHOR_RE.finditer(text))
    for i, anchor in enumerate(anchors):
        m = PROFILE_RE.search(anchor.group(1))
        if not m:
            continue
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
        between = re.sub(r"<[^>]+>", " ", text[anchor.end():end])
        _add(found, m.group(1), _parse_date(between))
    return found


def relation_kind(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.suffix.lower() not in (".json", ".html"):
        return None
    return FILE_KINDS.get(re.sub(r"_\d+$", "", path.stem.lower()))


def collect_files(source, recursive=True):
    """Return [(kind, name, bytes, mtime)] for every relation file in a zip or folder."""
    found = []
    if source.is_file():
        with zipfile.ZipFile(source) as zf:
            for info in zf.infolist():
                kind = relation_kind(info.filename)
                if kind and not info.is_dir():
                    mtime = datetime(*info.date_time).timestamp()
                    found.append((kind, info.filename, zf.read(info), mtime))
    else:
        paths = source.rglob("*") if recursive else source.iterdir()
        for path in paths:
            kind = relation_kind(path.name)
            if kind and path.is_file():
                found.append((kind, path.relative_to(source).as_posix(), path.read_bytes(), path.stat().st_mtime))
    return sorted(found, key=lambda f: f[1])


def discover_sources(exports_dir):
    """Every zip or folder in exports/ is one export. Loose files in exports/ count as one too."""
    if not exports_dir.exists():
        return []
    sources = [(p, True) for p in sorted(exports_dir.iterdir())
               if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() == ".zip")]
    if any(p.is_file() and relation_kind(p.name) for p in exports_dir.iterdir()):
        sources.append((exports_dir, False))
    return sources


def export_date(source, mtimes):
    """Instagram names zips like instagram-you-2024-07-21-xyz.zip; fall back to file times."""
    m = NAME_DATE_RE.search(source.name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            pass
    stamp = max(mtimes) if mtimes else datetime.now().timestamp()
    return datetime.fromtimestamp(stamp).date().isoformat()


def remove_export(source, recursive, files, log):
    """Delete an export whose data is safely in the database."""
    try:
        if source.is_file():
            source.unlink()
        elif recursive:
            shutil.rmtree(source)
        else:  # loose files sitting directly in exports/: remove just those
            for _, name, _, _ in files:
                (source / name).unlink(missing_ok=True)
        log(f"Deleted {source.name}, it's saved in the database")
    except OSError as exc:  # e.g. still open in another program; retried on the next scan
        log(f"Couldn't delete {source.name} yet ({exc})")


def ingest(db_path=DEFAULT_DB, exports_dir=DEFAULT_EXPORTS, log=print, delete_imported=True):
    """Import any new exports. Returns the number of snapshots added.

    With delete_imported, each export is removed once it's in the database (including ones
    imported earlier). Exports that can't be read or imported are always left alone.
    """
    conn = connect(db_path)
    imported = 0
    for source, recursive in discover_sources(exports_dir):
        try:
            files = collect_files(source, recursive)
        except (zipfile.BadZipFile, OSError) as exc:
            log(f"Skipped {source.name}: can't read it ({exc})")
            continue
        kinds = {f[0] for f in files}
        if not {"followers", "following"} <= kinds:
            if files:
                log(f"Skipped {source.name}: needs both followers and following files")
            continue

        digest = hashlib.sha256()
        for kind, _, data, _ in sorted(files, key=lambda f: (f[0], hashlib.sha256(f[2]).digest())):
            digest.update(kind.encode() + hashlib.sha256(data).digest())
        content_hash = digest.hexdigest()
        if conn.execute("SELECT 1 FROM snapshots WHERE content_hash = ?", (content_hash,)).fetchone():
            if delete_imported:
                remove_export(source, recursive, files, log)
            continue

        relations = {}
        try:
            for kind, name, data, _ in files:
                if name.lower().endswith(".json"):
                    parsed = parse_json(data)
                else:
                    parsed = parse_html(data.decode("utf-8", "replace"))
                bucket = relations.setdefault(kind, {})
                for username, ts in parsed.items():
                    _add(bucket, username, ts)
        except (ValueError, UnicodeDecodeError) as exc:
            log(f"Skipped {source.name}: couldn't parse {name} ({exc})")
            continue

        with conn:
            cur = conn.execute(
                "INSERT INTO snapshots (source, content_hash, export_date, imported_at) VALUES (?, ?, ?, ?)",
                (source.name, content_hash, export_date(source, [f[3] for f in files]),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.executemany(
                "INSERT INTO relations (snapshot_id, kind, username, ts) VALUES (?, ?, ?, ?)",
                [(cur.lastrowid, kind, u, ts) for kind, people in relations.items() for u, ts in people.items()],
            )
        imported += 1
        log(f"Imported {source.name}: {len(relations['followers'])} followers, "
            f"{len(relations['following'])} following")
        if delete_imported:
            remove_export(source, recursive, files, log)
    conn.close()
    return imported


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exports", type=Path, default=DEFAULT_EXPORTS, help="folder holding your exports")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database file")
    parser.add_argument("--keep-exports", action="store_true", help="don't delete exports after importing")
    args = parser.parse_args()
    count = ingest(args.db, args.exports, delete_imported=not args.keep_exports)
    print(f"{count} new snapshot(s) imported." if count else "Nothing new to import.")


if __name__ == "__main__":
    main()
