"""Turn the snapshots stored in the database into everything the dashboard shows."""
from collections import Counter
from datetime import datetime, timedelta

SNAPSHOTS_SQL = """
SELECT s.id, s.source, s.export_date, s.imported_at,
       SUM(r.kind = 'followers') AS followers,
       SUM(r.kind = 'following') AS following,
       (SELECT COUNT(*) FROM relations a
          JOIN relations b ON b.snapshot_id = a.snapshot_id AND b.username = a.username
         WHERE a.snapshot_id = s.id AND a.kind = 'followers' AND b.kind = 'following') AS mutuals
FROM snapshots s
LEFT JOIN relations r ON r.snapshot_id = s.id
GROUP BY s.id
ORDER BY s.export_date, s.id
"""


def _load(conn, snapshot_id):
    rel = {}
    for row in conn.execute("SELECT kind, username, ts FROM relations WHERE snapshot_id = ?", (snapshot_id,)):
        rel.setdefault(row["kind"], {})[row["username"]] = row["ts"]
    return rel


def _oldest(people):
    dated = [(ts, u) for u, ts in people.items() if ts]
    return min(dated) if dated else None


def _facts(followers, following, mutuals, export_date):
    facts = []
    if oldest := _oldest(following):
        facts.append({"label": "First account you still follow", "username": oldest[1], "ts": oldest[0]})
    if oldest := _oldest(followers):
        facts.append({"label": "Your longest-standing follower", "username": oldest[1], "ts": oldest[0]})

    # A mutual "friendship" starts once both sides follow, i.e. at the later of the two follows.
    since = {u: max(followers[u], following[u]) for u in mutuals if followers[u] and following[u]}
    if oldest := _oldest(since):
        facts.append({"label": "Longest mutual", "username": oldest[1], "ts": oldest[0]})
    if since:
        they_first = sum(1 for u in since if followers[u] < following[u])
        facts.append({"label": "Mutuals where they followed first",
                      "value": f"{round(100 * they_first / len(since))}%"})

    days = Counter(datetime.fromtimestamp(ts).date() for ts in following.values() if ts)
    if days:
        day, count = days.most_common(1)[0]
        facts.append({"label": "Biggest follow spree", "value": f"{count} accounts in one day",
                      "ts": int(datetime.combine(day, datetime.min.time()).timestamp())})

    cutoff = (datetime.fromisoformat(export_date) - timedelta(days=30)).timestamp()
    recent = sum(1 for ts in followers.values() if ts and ts >= cutoff)
    facts.append({"label": "New followers in the 30 days before this export", "value": str(recent)})
    return facts


def _activity(followers, following):
    months = {}
    for kind, people in (("followers", followers), ("following", following)):
        for ts in people.values():
            if ts:
                month = datetime.fromtimestamp(ts).strftime("%Y-%m")
                months.setdefault(month, {"followers": 0, "following": 0})[kind] += 1
    return [{"month": m, **counts} for m, counts in sorted(months.items())]


def build_report(conn, snapshot_id=None):
    snapshots = [dict(r) for r in conn.execute(SNAPSHOTS_SQL)]
    if not snapshots:
        return {"snapshots": []}
    ids = [s["id"] for s in snapshots]
    idx = ids.index(snapshot_id) if snapshot_id in ids else len(ids) - 1
    current = snapshots[idx]
    previous = snapshots[idx - 1] if idx > 0 else None

    rel = _load(conn, current["id"])
    followers, following = rel.get("followers", {}), rel.get("following", {})
    ignored = {r["username"] for r in conn.execute("SELECT username FROM ignored")}

    def person(u):
        return {"username": u, "follows_you_since": followers.get(u),
                "you_follow_since": following.get(u), "ignored": u in ignored}

    def people(names):
        return [person(u) for u in sorted(names)]

    mutuals = followers.keys() & following.keys()
    not_following_back = following.keys() - followers.keys()
    fans = followers.keys() - following.keys()

    changes = None
    if previous:
        prev = _load(conn, previous["id"])
        prev_followers, prev_following = prev.get("followers", {}), prev.get("following", {})
        changes = {
            "since": previous["export_date"],
            "new_followers": people(followers.keys() - prev_followers.keys()),
            "lost_followers": people(prev_followers.keys() - followers.keys()),
            "you_followed": people(following.keys() - prev_following.keys()),
            "you_unfollowed": people(prev_following.keys() - following.keys()),
        }

    return {
        "snapshots": snapshots,
        "current": current,
        "previous": previous,
        "stats": {
            "followers": len(followers),
            "following": len(following),
            "mutuals": len(mutuals),
            "not_following_back": len(not_following_back - ignored),
            "not_following_back_ignored": len(not_following_back & ignored),
            "fans": len(fans),
            "follow_back_rate": len(mutuals) / len(following) if following else None,
        },
        "lists": {
            "not_following_back": people(not_following_back),
            "fans": people(fans),
            "mutuals": people(mutuals),
            "pending": [{"username": u, "ts": ts} for u, ts in sorted(rel.get("pending", {}).items())],
            "recently_unfollowed": [{"username": u, "ts": ts}
                                    for u, ts in sorted(rel.get("unfollowed", {}).items())],
        },
        "changes": changes,
        "activity": _activity(followers, following),
        "facts": _facts(followers, following, mutuals, current["export_date"]),
    }
