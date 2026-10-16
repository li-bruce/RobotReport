"""Pure tournament planners. No storage, credentials or network."""
from __future__ import annotations
import json
import random
import sys
from ..gender import category, validate_roster


STRATEGIES = ("random", "seeded", "balanced", "high_low")


def level(person):
    value = person.get("level")
    return value if type(value) is int and 1 <= value <= 9 else 0


def player_name(person):
    name = str(person.get("name") or "").strip()
    if not name or name in {str(person.get("user_id", "")), str(person.get("id", ""))}:
        name = "待完善姓名"
    proxy = '代报·' if person.get('is_proxy') or person.get('guest_index', 0) else ''
    return f"{name}（{proxy}{str(level(person))+'级' if level(person) else '无级别'}）"


def discipline_size(options):
    discipline = options.get("discipline")
    if discipline is None:
        # Explicit legacy team_size remains valid; new requests default to doubles.
        discipline = "singles" if options.get("team_size") == 1 else "doubles"
    if discipline not in ("singles", "doubles"):
        raise ValueError("比赛类型仅支持单打或双打")
    return 1 if discipline == "singles" else 2


def require_doubles(options):
    if options.get("discipline", "doubles") != "doubles":
        raise ValueError("此赛制仅支持双打")


def assigned_sides(participants, options, seed, team_size=None):
    """Seeded, reproducible assignment using approved integer levels only."""
    size = team_size or discipline_size(options)
    kind = validate_roster(participants, options, 'five_team' if size == 5 else None)
    if len(participants) % size:
        raise ValueError(f"需要完整的 {size} 人队伍；当前 {len(participants)} 人")
    strategy = options.get("assignment_strategy", "random")
    if strategy not in STRATEGIES:
        raise ValueError("未知分配策略")
    people = shuffled(participants, seed)
    if kind == 'mixed':
        men = [p for p in people if p.get('gender') == 'male']
        women = [p for p in people if p.get('gender') == 'female']
        if strategy != 'random':
            men.sort(key=level, reverse=True)
            women.sort(key=level)
        # Pair one of each gender; levels guide pairing only within that rule.
        result = [sides([man, woman], 2)[0] for man, woman in zip(men, women)]
        if strategy == 'seeded':
            result.sort(key=lambda entry: (entry['max_level'], entry['level_total']), reverse=True)
        return result
    if strategy == "random":
        return sides(people, size)
    people.sort(key=level, reverse=True)  # seeded shuffle breaks same-level ties
    count = len(people) // size
    if size == 1:
        return sides(people, 1)
    teams = [[] for _ in range(count)]
    if strategy == "seeded":
        # Each top seed belongs to a different team; lower seeds are drafted
        # in reverse order so seeds remain separated without stacking teams.
        for index, person in enumerate(people):
            row, col = divmod(index, count)
            teams[col if row % 2 == 0 else count - col - 1].append(person)
    elif strategy == "balanced":
        for person in people:
            eligible = [i for i in range(count) if len(teams[i]) < size]
            index = min(eligible, key=lambda i: (sum(level(p) for p in teams[i]), len(teams[i]), i))
            teams[index].append(person)
    else:
        # Deliberately pair strongest with weakest, then work inwards.
        for index in range(count):
            while len(teams[index]) < size:
                teams[index].append(people.pop(0 if len(teams[index]) % 2 == 0 else -1))
    result = [sides(team, size)[0] for team in teams]
    if strategy == "seeded":
        result.sort(key=lambda entry: (entry["max_level"], entry["level_total"]), reverse=True)
    return result


def assigned_groups(entrants, count, strategy):
    if strategy == "random":
        return [entrants[i::count] for i in range(count)]
    ordered = sorted(entrants, key=lambda e: (e.get("max_level", 0), e.get("level_total", 0)), reverse=True)
    groups = [[] for _ in range(count)]
    if strategy == "seeded":
        for index, entry in enumerate(ordered):
            row, col = divmod(index, count)
            groups[col if row % 2 == 0 else count-col-1].append(entry)
    else:
        capacities = [len(entrants)//count + (i < len(entrants)%count) for i in range(count)]
        for entry in ordered:
            available = [i for i in range(count) if len(groups[i]) < capacities[i]]
            index = min(available, key=lambda i: (sum(e.get("level_total", 0) for e in groups[i]), len(groups[i]), i))
            groups[index].append(entry)
    return groups

def shuffled(participants, seed):
    people = [dict(p) for p in participants]
    random.Random(seed).shuffle(people)
    return people

def sides(participants, team_size=1):
    if len(participants) % team_size:
        raise ValueError(f"需要完整的 {team_size} 人队伍；当前 {len(participants)} 人，请补齐或换赛制")
    return [{"id": "s:" + "+".join(str(p["id"]) for p in participants[i:i+team_size]),
             "name": " / ".join(player_name(p) for p in participants[i:i+team_size]),
             "level_total": sum(level(p) for p in participants[i:i+team_size]),
             "max_level": max(level(p) for p in participants[i:i+team_size]),
             "members": [str(p["id"]) for p in participants[i:i+team_size]]}
            for i in range(0, len(participants), team_size)]

def match(a=None, b=None, *, stage="league", round=1, group_id="", a_source=None, b_source=None):
    return {"side_a": a, "side_b": b, "stage": stage, "round": round,
            "group_id": group_id, "a_source": a_source, "b_source": b_source}

def source(number, result="winner"):
    return {"match": number, "result": result}

def number_matches(matches):
    for i, m in enumerate(matches, 1):
        m["number"] = i
    return matches

def round_robin(entrants, group_id="", stage="league"):
    ring = list(entrants)
    if len(ring) % 2:
        ring.append(None)
    matches = []
    for r in range(max(0, len(ring)-1)):
        for i in range(len(ring)//2):
            a, b = ring[i], ring[-i-1]
            if a and b:
                matches.append(match(a, b, stage=stage, round=r+1, group_id=group_id))
        ring = [ring[0], ring[-1], *ring[1:-1]]
    return matches

def knockout(entrants, *, offset=0, stage="knockout"):
    size = 2
    while size < len(entrants):
        size *= 2
    order = [1, 2]
    while len(order) < size:
        complement = 2 * len(order) + 1
        order = [v for s in order for v in (s, complement-s)]
    slots = [entrants[s-1] if s <= len(entrants) else None for s in order]
    matches, previous, rounds = [], [], []
    for i in range(0, size, 2):
        matches.append(match(slots[i], slots[i+1], stage=stage))
        previous.append(offset+len(matches))
    rounds.append(previous)
    r = 2
    while len(previous) > 1:
        current = []
        for i in range(0, len(previous), 2):
            matches.append(match(stage=stage, round=r, a_source=source(previous[i]), b_source=source(previous[i+1])))
            current.append(offset+len(matches))
        previous = current
        rounds.append(previous)
        r += 1
    for i, m in enumerate(matches, offset+1):
        m["number"] = i
    return matches, rounds

def group(name, entrants):
    return {"id": name, "name": f"{name}组", "members": [m for e in entrants for m in e["members"]], "entrants": entrants}

def plan_cli(builder):
    data = json.load(sys.stdin)
    result = builder(data["participants"], data.get("options", {}), int(data.get("seed", 1)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
