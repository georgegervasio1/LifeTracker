"""Trade suggestions for an ESPN fantasy league, priced with FantasyCalc's public trade values.

FantasyCalc (fantasycalc.com) publishes crowd-sourced redraft trade values from real trades:
GET https://api.fantasycalc.com/values/current?isDynasty=false&numQbs=<1|2>&numTeams=<n>&ppr=<0|0.5|1>
Each row: {player: {name, position, maybeTeam, espnId, ...}, value, overallRank, positionRank, trend30Day}.

How a trade is judged (all pure functions, tested offline):
  - Every player gets a FantasyCalc value (matched by ESPN id, then by name + position). K and D/ST have no values.
  - A team's strength = the value of its best legal starting lineup (the league's own QB/RB/WR/TE/FLEX/OP slots)
    plus a small share of its best bench players (depth for byes and injuries).
  - A suggestion must be close to fair on FantasyCalc value, make your lineup better, and leave theirs no worse,
    which is what makes a trade worth proposing.
"""
import itertools
import re
import threading
import time
import unicodedata

import requests

FC_URL = "https://api.fantasycalc.com/values/current"
FC_TTL = 6 * 3600
_cache, _lock = {}, threading.Lock()

# ESPN lineup slot id -> positions that can fill it
SLOT_POS = {0: ["QB"], 2: ["RB"], 3: ["RB", "WR"], 4: ["WR"], 5: ["WR", "TE"], 6: ["TE"], 7: ["QB", "RB", "WR", "TE"],
            23: ["RB", "WR", "TE"]}
DEFAULT_SLOTS = {0: 1, 2: 2, 4: 2, 6: 1, 23: 1}
TRADEABLE = {"QB", "RB", "WR", "TE"}
BENCH_WEIGHT = 0.15          # how much a bench player counts toward strength
BENCH_DEPTH = 4              # best N bench players counted
FAIR = 0.12                  # sides within 12% of each other on value
POOL = 10                    # top players per side considered


class ValuesError(Exception):
    pass


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


# ---------------------------------------------------------------- league format -> FantasyCalc params
def league_format(settings):
    """(numQbs, ppr, lineup slot counts) from ESPN mSettings."""
    counts = {}
    for k, v in ((settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}).items():
        try:
            if int(v) > 0 and int(k) in SLOT_POS:
                counts[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    counts = counts or dict(DEFAULT_SLOTS)
    num_qbs = 2 if counts.get(0, 0) >= 2 or counts.get(7, 0) >= 1 else 1
    ppr = 0.0
    for it in (settings.get("scoringSettings") or {}).get("scoringItems") or []:
        if it.get("statId") == 53:
            ppr = float(((it.get("pointsOverrides") or {}).get("16")) or it.get("points") or 0)
    ppr = min((0, 0.5, 1), key=lambda x: abs(x - ppr))
    return num_qbs, ppr, counts


def fetch_values(num_qbs, num_teams, ppr):
    num_teams = max(8, min(14, int(num_teams or 10)))       # FantasyCalc offers 8-14 team leagues
    params = {"isDynasty": "false", "numQbs": num_qbs, "numTeams": num_teams, "ppr": ppr}
    key = tuple(sorted(params.items()))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        r = requests.get(FC_URL, params=params, timeout=15, headers={"User-Agent": "LifeTracker/1.0"})
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            raise ValueError("not a list")
    except Exception as e:
        if hit:
            return hit[1]
        raise ValuesError(f"FantasyCalc didn't answer ({type(e).__name__}). Try again in a minute.")
    with _lock:
        _cache[key] = (now + FC_TTL, rows)
    return rows


def value_index(rows):
    by_espn, by_name = {}, {}
    for r in rows or []:
        p = r.get("player") or {}
        rec = {"value": float(r.get("value") or 0), "rank": r.get("overallRank"), "pos_rank": r.get("positionRank"),
               "trend": r.get("trend30Day")}
        if p.get("espnId"):
            by_espn[str(p["espnId"])] = rec
        by_name.setdefault((norm_name(p.get("name")), p.get("position")), rec)
    return by_espn, by_name


def price(players, idx):
    """Attach FantasyCalc value to each roster player (copy). Unknown = 0 and flagged."""
    by_espn, by_name = idx
    out = []
    for p in players:
        rec = by_espn.get(str(p.get("id"))) or by_name.get((norm_name(p.get("name")), p.get("pos")))
        out.append({**p, "value": round(rec["value"]) if rec else 0, "rank": rec["rank"] if rec else None,
                    "trend": rec["trend"] if rec else None, "priced": bool(rec)})
    return out


# ---------------------------------------------------------------- team strength
def best_lineup(players, slots):
    """Greedy fill: most specific slots first (QB, RB, WR, TE), then flexible ones (FLEX, OP)."""
    pool = sorted([p for p in players if p.get("pos") in TRADEABLE and p.get("slot") != "IR"], key=lambda p: -p["value"])
    used, starters = set(), []
    order = sorted(slots, key=lambda s: len(SLOT_POS[s]))
    for s in order:
        for _ in range(slots[s]):
            pick = next((p for p in pool if p["id"] not in used and p["pos"] in SLOT_POS[s]), None)
            if pick:
                used.add(pick["id"]); starters.append(pick)
    bench = [p for p in pool if p["id"] not in used]
    return starters, bench


def strength(players, slots):
    st, bench = best_lineup(players, slots)
    return sum(p["value"] for p in st) + BENCH_WEIGHT * sum(p["value"] for p in bench[:BENCH_DEPTH])


def needs(players, slots):
    """Positions where the starting lineup is weakest relative to the bench (for the explanation)."""
    st, bench = best_lineup(players, slots)
    by = {}
    for p in st:
        by.setdefault(p["pos"], []).append(p["value"])
    spare = {}
    for p in bench:
        spare.setdefault(p["pos"], []).append(p["value"])
    return by, spare


# ---------------------------------------------------------------- suggestions
def evaluate(mine, theirs, give_ids, get_ids, slots):
    give = [p for p in mine if p["id"] in give_ids]
    get = [p for p in theirs if p["id"] in get_ids]
    my_after = [p for p in mine if p["id"] not in give_ids] + get
    their_after = [p for p in theirs if p["id"] not in get_ids] + give
    vg, vr = sum(p["value"] for p in give), sum(p["value"] for p in get)
    my_gain = strength(my_after, slots) - strength(mine, slots)
    their_gain = strength(their_after, slots) - strength(theirs, slots)
    top = max(vg, vr) or 1
    return {"give": give, "get": get, "value_give": round(vg), "value_get": round(vr),
            "value_diff": round(vr - vg), "fair_pct": round(abs(vr - vg) / top * 100, 1),
            "fair": abs(vr - vg) / top <= FAIR,
            "my_gain": round(my_gain), "their_gain": round(their_gain)}


def _why(t, mine, theirs, slots):
    gpos = sorted({p["pos"] for p in t["give"]}); rpos = sorted({p["pos"] for p in t["get"]})
    if gpos != rpos:
        return f"You turn {'/'.join(gpos)} depth into {'/'.join(rpos)} help; they get the reverse."
    if len(t["give"]) > len(t["get"]):
        return "You consolidate two pieces into one stronger starter; they get depth."
    if len(t["get"]) > len(t["give"]):
        return "You split one player into more starting depth; they consolidate."
    return "Both lineups come out a little stronger at the same position."


def suggest(mine, partners, slots, limit_per=3, limit=12):
    """mine: priced players; partners: {team_id: priced players}. Returns trades sorted by how good they are for both."""
    my_pool = sorted([p for p in mine if p["pos"] in TRADEABLE and p["priced"]], key=lambda p: -p["value"])[:POOL]
    out = []
    for tid, theirs in partners.items():
        th_pool = sorted([p for p in theirs if p["pos"] in TRADEABLE and p["priced"]], key=lambda p: -p["value"])[:POOL]
        found = []
        for ng, nr in ((1, 1), (2, 1), (1, 2), (2, 2)):
            for give in itertools.combinations(my_pool, ng):
                vg = sum(p["value"] for p in give)
                for get in itertools.combinations(th_pool, nr):
                    vr = sum(p["value"] for p in get)
                    if abs(vr - vg) / (max(vg, vr) or 1) > FAIR:
                        continue
                    t = evaluate(mine, theirs, {p["id"] for p in give}, {p["id"] for p in get}, slots)
                    if t["my_gain"] <= max(50, 0.02 * vg) or t["their_gain"] < 0:
                        continue
                    t["team_id"] = tid
                    t["score"] = t["my_gain"] + 0.5 * t["their_gain"]
                    found.append(t)
        found.sort(key=lambda t: -t["score"])
        picked, seen = [], set()
        for t in found:                      # don't repeat the same headline player for one partner
            k = tuple(sorted(p["id"] for p in t["get"]))
            if k in seen:
                continue
            seen.add(k); t["why"] = _why(t, mine, theirs, slots); picked.append(t)
            if len(picked) >= limit_per:
                break
        out += picked
    out.sort(key=lambda t: -t["score"])
    for t in out:
        t.pop("score", None)
    return out[:limit] if limit else out
