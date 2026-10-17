#!/usr/bin/env python3
"""Seed trend history from each player's last game, so arrows show right away.

Sleeper (and ESPN) only keep a player's current injury status, so normally
trends need two fetches. This uses Sleeper's weekly stats as the "before":
  - played in their team's last game, now has a designation  -> down
  - missed it, now Questionable/Doubtful (or healthy starter) -> up
Only those clear signals are seeded; everyone else keeps their current
baseline. Then a normal fetch runs and computes the trends.

Run on the Pi:  sudo python3 seed_from_last_game.py
"""
import json
import urllib.request

import sleeper_fetch as sf

STATS_URL = "https://api.sleeper.com/stats/nfl/{season}/{week}?season_type={season_type}&{positions}"
LOOKBACK_WEEKS = 4  # covers byes and Thursday games
HEALTHY_STARTER_RANK = 150  # below this, "didn't play" is often just a backup


def main():
    cfg = sf.load_config()["data"]
    state = sf.http_get_json(sf.STATE_URL)
    season, week = state["season"], state["week"]
    positions = "&".join(f"position[]={p}" for p in cfg["positions"])

    # Each team's most recent game week, and who played in it.
    team_last_week, played = {}, {}
    for w in range(week, max(0, week - LOOKBACK_WEEKS), -1):
        url = STATS_URL.format(season=season, week=w, season_type=state["season_type"], positions=positions)
        played[w] = set()
        for row in sf.http_get_json(url):
            team = row.get("team")
            if team:
                team_last_week[team] = max(team_last_week.get(team, 0), w)
            if (row.get("stats") or {}).get("gp", 0) > 0:
                played[w].add(row["player_id"])

    players = sf.http_get_json(sf.PLAYERS_URL, timeout=180, object_hook=sf.make_player_filter(cfg))
    history = sf.load_history()
    seeded = []
    for pid, p in players.items():
        if not p or p["team"] not in team_last_week:
            continue
        status = p.get("injury_status") or None
        rank = p.get("search_rank") or 9_999_999
        if pid in played[team_last_week[p["team"]]]:
            before = None if status else "skip"
        elif status in ("Questionable", "Doubtful") or (status is None and rank <= HEALTHY_STARTER_RANK):
            before = "Out"
        else:
            before = "skip"
        if before != "skip":
            history["statuses"][pid] = before
            seeded.append(p["full_name"])
    del players

    sf.write_json_atomic(sf.HISTORY_PATH, history)
    print(f"Seeded last-game status for {len(seeded)} players")
    sf.main()


if __name__ == "__main__":
    main()
