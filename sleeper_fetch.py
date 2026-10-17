#!/usr/bin/env python3
"""Download Sleeper NFL player data and write a compact injuries.json.

The /players/nfl endpoint is ~15 MB and Sleeper asks that it be called at
most once per day, so ticker.py runs this as a separate process on a timer.

Sleeper only reports each player's current status, so every fetch is compared
against the previous one (saved in history.json) to spot players trending up
(e.g. Out -> Questionable, Questionable -> healthy) or down.
"""
import json
import os
import sys
import tempfile
import time
import unicodedata
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
OUTPUT_PATH = os.path.join(APP_DIR, "injuries.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
STATE_URL = "https://api.sleeper.app/v1/state/nfl"
USER_AGENT = "sleeper-led-ticker/1.0"

PLAYER_FIELDS = (
    "full_name", "first_name", "last_name", "position", "team", "injury_status",
    "injury_body_part", "injury_notes", "search_rank", "news_updated",
)

# Lower is healthier. None means no injury designation (healthy/active).
SEVERITY = {None: 0, "Questionable": 1, "Doubtful": 2, "Out": 3}
WORST_SEVERITY = 4  # IR, PUP, Sus, NA, DNR, COV, anything else


def severity(status):
    return SEVERITY.get(status, WORST_SEVERITY)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_history():
    try:
        with open(HISTORY_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"statuses": {}, "trends": {}}


def write_json_atomic(path, obj):
    # Atomic replace so the ticker never reads a half-written file.
    fd, tmp = tempfile.mkstemp(dir=APP_DIR, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.chmod(tmp, 0o644)  # mkstemp makes it owner-only; let non-root --preview read it
    os.replace(tmp, path)


def make_player_filter(data_cfg):
    """JSON object_hook that keeps only fantasy-relevant players, trimmed.

    It runs on every object as it's parsed, so the thousands of irrelevant
    players are dropped immediately. That keeps peak memory low on the Pi.
    """
    positions = set(data_cfg["positions"])
    max_rank = data_cfg["max_search_rank"]

    def hook(d):
        if "injury_status" not in d:
            return d
        relevant = (
            d.get("position") in positions
            and d.get("team")
            and (d.get("search_rank") or 9_999_999) <= max_rank
        )
        return {k: d.get(k) for k in PLAYER_FIELDS} if relevant else None

    return hook


def http_get_json(url, timeout=60, object_hook=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp, object_hook=object_hook)


def ascii_text(s):
    # BDF fonts only cover ASCII; turn accented names into plain letters.
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def update_history(players, history, now, trend_hours):
    """Compare current statuses to the last fetch and record up/down trends."""
    prev = history.get("statuses", {})
    trends = {
        pid: t for pid, t in history.get("trends", {}).items()
        if now - t["at"] < trend_hours * 3600
    }
    statuses = {}
    for pid, p in players.items():
        if not p:
            continue
        status = p.get("injury_status") or None
        statuses[pid] = status
        # Players with no previous snapshot (first run, newly relevant) get no trend.
        if pid in prev and severity(status) != severity(prev[pid]):
            trends[pid] = {
                "direction": "up" if severity(status) < severity(prev[pid]) else "down",
                "from": prev[pid],
                "to": status,
                "at": now,
            }
    trends = {pid: t for pid, t in trends.items() if pid in statuses}
    return {"statuses": statuses, "trends": trends}


def build_items(players, trends, data_cfg):
    statuses = set(data_cfg["statuses"])

    items = []
    for pid, p in players.items():
        if not p:
            continue
        status = p.get("injury_status") or None
        trend = trends.get(pid)
        # Healthy players only show up if they just recovered.
        if status not in statuses and not trend:
            continue
        detail = " - ".join(x for x in (p.get("injury_body_part"), p.get("injury_notes")) if x)
        items.append({
            "name": ascii_text(p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()),
            "position": p["position"],
            "team": p["team"],
            "status": status,
            "detail": ascii_text(detail) if status else "",
            "rank": p.get("search_rank") or 9_999_999,
            "updated": p.get("news_updated") or 0,
            "trend": trend,
        })

    # Status changes lead the ticker, newest first.
    trending = [i for i in items if i["trend"]]
    trending.sort(key=lambda i: (i["trend"]["at"], i["updated"]), reverse=True)
    rest = [i for i in items if not i["trend"]]
    if data_cfg.get("sort") == "rank":
        rest.sort(key=lambda i: i["rank"])
    else:
        rest.sort(key=lambda i: i["updated"], reverse=True)
    return (trending + rest)[: data_cfg["max_items"]]


def main():
    cfg = load_config()
    data_cfg = cfg["data"]
    print("Fetching Sleeper state...", flush=True)
    state = http_get_json(STATE_URL)
    print("Fetching Sleeper players (~15 MB)...", flush=True)
    players = http_get_json(PLAYERS_URL, timeout=180, object_hook=make_player_filter(data_cfg))

    now = time.time()
    history = update_history(players, load_history(), now, data_cfg.get("trend_hours", 72))
    items = build_items(players, history["trends"], data_cfg)

    write_json_atomic(HISTORY_PATH, history)
    write_json_atomic(OUTPUT_PATH, {
        "fetched_at": now,
        "season": state.get("season"),
        "week": state.get("display_week") or state.get("week"),
        "season_type": state.get("season_type"),
        "items": items,
    })
    ups = sum(1 for i in items if i["trend"] and i["trend"]["direction"] == "up")
    downs = sum(1 for i in items if i["trend"] and i["trend"]["direction"] == "down")
    print(f"Wrote {len(items)} items ({ups} up, {downs} down) to {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
