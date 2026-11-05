"""
NBA game log fetcher for Termux, v4 (ESPN source, no pip installs).

NBA.com blocks requests from this phone, so this version pulls
every current player's game log from ESPN's public data instead.

Usage:
    python fetch_nba.py            # current season
    python fetch_nba.py 2025-26    # a specific season

On GitHub (or with --repo) it writes data/nba_gamelogs_<season>.csv and
data/latest.json for the hosted screener. On your phone it saves to Downloads.
"""

import csv
import datetime
import json
import os
import sys
import time
import urllib.request

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_t = datetime.date.today()
_y = _t.year if _t.month >= 8 else _t.year - 1
SEASON = _args[0] if _args else f"{_y}-{str(_y + 1)[2:]}"
REPO_MODE = "--repo" in sys.argv or os.environ.get("GITHUB_ACTIONS") == "true"
ESPN_SEASON = int(SEASON[:4]) + 1          # ESPN labels 2025-26 as 2026
CHECK_PLAYER = "Buzelis"
PAUSE = 0.3
HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"}

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{}/roster"
LOG_URL = ("https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/"
           "athletes/{}/gamelog?season={}")

FIELDS = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "POSITION", "GAME_ID", "GAME_DATE",
          "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
          "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS"]


def get(url, timeout=20):
    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 3:
                raise e
            time.sleep(2)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def split_made(x):
    try:
        a, b = str(x).split("-")
        return int(a), int(b)
    except ValueError:
        return 0, 0


def downloads_folder():
    d = os.path.expanduser("~/storage/downloads")
    return d if os.path.isdir(d) else os.getcwd()


def get_players():
    data = get(TEAMS_URL)
    teams = [t["team"] for t in data["sports"][0]["leagues"][0]["teams"]]
    players = {}
    for t in teams:
        roster = get(ROSTER_URL.format(t["id"]))
        athletes = roster.get("athletes", [])
        if athletes and "items" in athletes[0]:            # grouped format
            athletes = [a for g in athletes for a in g["items"]]
        for a in athletes:
            players[a["id"]] = (a.get("fullName") or a.get("displayName", ""),
                                t.get("abbreviation", ""),
                                (a.get("position") or {}).get("abbreviation", ""))
        time.sleep(PAUSE)
    return players


def local_date(iso):
    """ESPN gives UTC times; a 10:30 PM Eastern tip is already the next day in UTC.
    Shifting back 6 hours gives the US calendar date for every NBA tip time."""
    try:
        t = datetime.datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
        return (t - datetime.timedelta(hours=6)).date().isoformat()
    except Exception:
        return iso[:10]


def parse_log(pid, name, data, roster_team="", pos=""):
    labels = data.get("labels", [])
    meta = data.get("events", {})
    rows = []
    for st in data.get("seasonTypes", []):
        if "regular" not in st.get("displayName", "").lower():
            continue
        for cat in st.get("categories", []):
            for ev in cat.get("events", []):
                stats = dict(zip(labels, ev.get("stats", [])))
                if not num(stats.get("MIN")):
                    continue  # did not play
                m = meta.get(str(ev.get("eventId")), {})
                fgm, fga = split_made(stats.get("FG"))
                tpm, tpa = split_made(stats.get("3PT"))
                ftm, fta = split_made(stats.get("FT"))
                opp = (m.get("opponent") or {}).get("abbreviation", "")
                team = (m.get("team") or {}).get("abbreviation", "") or roster_team
                rows.append({
                    "PLAYER_ID": pid, "PLAYER_NAME": name,
                    "TEAM_ABBREVIATION": team, "POSITION": pos, "GAME_ID": ev.get("eventId"),
                    "GAME_DATE": local_date(m.get("gameDate") or ""),
                    "MATCHUP": f"{team} {m.get('atVs', '')} {opp}".strip(),
                    "WL": m.get("gameResult", ""),
                    "MIN": num(stats.get("MIN")), "FGM": fgm, "FGA": fga,
                    "FG3M": tpm, "FG3A": tpa, "FTM": ftm, "FTA": fta,
                    "REB": num(stats.get("REB")), "AST": num(stats.get("AST")),
                    "STL": num(stats.get("STL")), "BLK": num(stats.get("BLK")),
                    "TOV": num(stats.get("TO")), "PF": num(stats.get("PF")),
                    "PTS": num(stats.get("PTS")),
                })
    return rows


def main():
    print("Testing ESPN connection...")
    try:
        players = get_players()
    except Exception as e:
        sys.exit(f"  Failed: {e}\nESPN didn't respond either. Send Claude this message.")
    print(f"  Connected. Found {len(players)} players on current rosters.")
    print(f"Pulling {SEASON} game logs (takes a few minutes)...")

    all_rows, debug_saved = [], False
    for i, (pid, (name, rteam, pos)) in enumerate(players.items(), 1):
        try:
            data = get(LOG_URL.format(pid, ESPN_SEASON))
            rows = parse_log(pid, name, data, rteam, pos)
            all_rows.extend(rows)
            if not debug_saved and not rows and data.get("seasonTypes"):
                # Save one raw sample in case ESPN's format differs from expected
                with open(os.path.join(downloads_folder(), "espn_debug.json"), "w") as f:
                    json.dump(data, f)
                debug_saved = True
        except Exception:
            pass  # rookies or players with no games
        if i % 50 == 0:
            print(f"  {i}/{len(players)} players done, {len(all_rows)} games so far")
        time.sleep(PAUSE)

    if not all_rows:
        sys.exit("No game rows parsed. If espn_debug.json is in Downloads, "
                 "tell Claude and it can fix the parser.")

    out_dir = "data" if REPO_MODE else downloads_folder()
    os.makedirs(out_dir, exist_ok=True)
    fname = f"nba_gamelogs_{SEASON}.csv"
    path = os.path.join(out_dir, fname)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} game rows for "
          f"{len({r['PLAYER_ID'] for r in all_rows})} players")
    print(f"File: {path}")
    if REPO_MODE:
        with open(os.path.join(out_dir, "latest.json"), "w") as f:
            json.dump({"file": fname, "season": SEASON, "rows": len(all_rows),
                       "updated": datetime.datetime.utcnow().isoformat() + "Z"}, f)

    pts = [r["PTS"] for r in all_rows if CHECK_PLAYER.lower() in r["PLAYER_NAME"].lower()]
    if pts:
        g = len(pts)
        for line in (10, 15, 20):
            hits = sum(1 for p in pts if p >= line)
            print(f"{CHECK_PLAYER}: {line}+ pts in {hits}/{g} games ({hits / g:.0%})")
        print(f"{CHECK_PLAYER}: under 8 pts in {sum(1 for p in pts if p < 8)} games")


if __name__ == "__main__":
    main()
