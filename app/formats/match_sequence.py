"""Incremental social doubles: round-robin teams, then rotate partners.

Circle factorization covers all partnerships for a stable roster. Each factor
is a complete team round robin. First appearances take priority over coverage,
gender compatibility and approved skill; no gender or skill value is invented.
Completed games are input, not mutable output. No third-party solver is needed.
"""
from itertools import combinations
import random
from .common import group, level, match, number_matches, plan_cli, sides
from ..gender import validate_roster

META = {"id": "match_sequence", "name": "对战序列", "min_players": 2, "max_players": 40,
        "description": "单打或双打；固定搭档完成队伍循环后换搭档，支持活动中加入和退出。",
        "rules": "默认双打，至少4人；单打至少2人。优先安排尚未上场的成员，再按固定队伍循环、换搭档，兼顾同类双打、水平和出场次数；奇数人数轮休。加入或退出只重排未完成对局，已完成对局保留。"}


def team_key(entry):
    return tuple(sorted(map(str, entry['members'])))


def contest_key(item):
    return tuple(sorted((team_key(item['side_a']), team_key(item['side_b']))))


def team_kind(entry, people):
    genders = sorted(people[p].get('gender', 'unknown') for p in entry['members'])
    return {('female', 'female'): 0, ('female', 'male'): 1,
            ('male', 'male'): 2}.get(tuple(genders), 3)


def _factors(players, seed):
    ring = list(players)
    random.Random(seed).shuffle(ring)
    if len(ring) % 2:
        ring.append(None)
    for _ in range(len(ring)-1):
        yield [(ring[i], ring[-i-1]) for i in range(len(ring)//2)]
        ring = [ring[0], ring[-1], *ring[1:-1]]


def _order(games, played, people, discipline, first_only=False):
    """Serve first appearances before any other soft scheduling preference."""
    remaining, loads = list(games), dict(played)
    ordered, last = [], set()
    while remaining:
        def priority(game):
            a, b = game['side_a'], game['side_b']
            ids = a['members']+b['members']
            kinds = (team_kind(a, people), team_kind(b, people))
            gender = (0, kinds[0]) if kinds[0] == kinds[1] and kinds[0] != 3 else (1, 3)
            if discipline == 'singles':
                gender = (0, 0)
            return (-sum(loads[p] == 0 for p in ids), max(loads[p] for p in ids),
                    len(last.intersection(ids)), gender,
                    abs(a['level_total']-b['level_total']), sum(loads[p] for p in ids))
        game = min(remaining, key=priority)
        ids = set(game['side_a']['members']+game['side_b']['members'])
        if first_only and not any(loads[p] == 0 for p in ids):
            break
        remaining.remove(game)
        ordered.append(game)
        last = ids
        for pid in ids:
            loads[pid] += 1
    return ordered


def _first_appearance_cost(games, played, people, discipline):
    """Avoid resting unplayed members; pack their first games at the front."""
    waiting = {pid for pid, count in played.items() if count == 0}
    if not waiting:
        return (0, ())
    profile = []
    for game in _order(games, played, people, discipline, first_only=True):
        ids = set(game['side_a']['members']+game['side_b']['members'])
        profile.append(-len(waiting & ids))
        waiting -= ids
    return (len(waiting), tuple(profile))


def build(participants, options, seed, history=(), previous=None):
    """Return one full batch; completed history drives future partner rotation."""
    discipline = options.get('discipline', 'doubles')
    players = {str(p['id']): dict(p, id=str(p['id'])) for p in participants}
    minimum = 2 if discipline == 'singles' else 4
    if len(players) > 40:
        raise ValueError('对战序列最多40人')
    groups = [group('A', sides(list(players.values())))]
    base = {'groups': groups, 'matches': [], 'ranking': 'individual',
            'sequence': {'version': 1, 'waiting': len(players) < minimum, 'batch': (previous or {}).get('batch', 0)+1}}
    if len(players) < minimum:
        return base
    if options.get('doubles_category') == 'mixed' and sum(p.get('gender') == 'male' for p in participants) != sum(p.get('gender') == 'female' for p in participants):
        base['sequence']['waiting'] = True
        return base
    validate_roster(participants, options, 'match_sequence')
    done = [m for m in history if m.get('status') == 'completed' and m.get('side_a') and m.get('side_b')]
    completed = {contest_key(m) for m in done}
    played = {p: 0 for p in players}
    partnered = {}
    for game in done:
        for key in ('side_a', 'side_b'):
            team = game[key]
            for pid in team['members']:
                if pid in played:
                    played[pid] += 1
            for pair in combinations(sorted(team['members']), 2):
                partnered[pair] = partnered.get(pair, 0)+1
    if discipline == 'singles':
        teams = sides(list(players.values()))
    else:
        kind = options.get('doubles_category', 'open')
        candidates = []
        # One circle factorization guarantees every partnership exactly once
        # for a stable open roster; soft priorities choose the next factor.
        for pairs in _factors(list(players.values()), seed):
            teams = [sides([a, b], 2)[0] for a, b in pairs if a and b]
            if kind == 'mixed' and any(team_kind(t, players) != 1 for t in teams):
                continue
            keys = [team_key(t) for t in teams]
            # A factor stays available until every opponent in it is played.
            remaining = [match(a, b) for a, b in combinations(teams, 2)
                         if tuple(sorted((team_key(a), team_key(b)))) not in completed]
            if not remaining:
                continue
            seen = sum(key in partnered for key in keys)
            same = sum(team_kind(m['side_a'], players) == team_kind(m['side_b'], players)
                       and team_kind(m['side_a'], players) != 3 for m in remaining)
            gap = sum(abs(m['side_a']['level_total']-m['side_b']['level_total']) for m in remaining)
            rest = sum(played[p['id']] for a, b in pairs for p in (a or b,) if not a or not b)
            first = _first_appearance_cost(remaining, played, players, discipline)
            candidates.append(((first, seen, -same/len(remaining), gap/len(remaining), -rest), teams))
        if not candidates:
            # Strict mixed doubles uses a bipartite factorization instead of
            # relying on arbitrary circle order to happen to pair opposite sexes.
            if kind == 'mixed':
                men = [p for p in players.values() if p.get('gender') == 'male']
                women = [p for p in players.values() if p.get('gender') == 'female']
                for offset in range(len(women)):
                    teams = [sides([man, women[(i+offset) % len(women)]], 2)[0] for i, man in enumerate(men)]
                    remaining = [match(a, b) for a, b in combinations(teams, 2)
                                 if tuple(sorted((team_key(a), team_key(b)))) not in completed]
                    if remaining:
                        first = _first_appearance_cost(remaining, played, players, discipline)
                        candidates.append(((first, sum(team_key(t) in partnered for t in teams), 0, 0, 0), teams))
            if not candidates:
                return base
        teams = min(candidates, key=lambda candidate: candidate[0])[1]
    remaining = [match(a, b, group_id='A') for a, b in combinations(teams, 2)
                 if tuple(sorted((team_key(a), team_key(b)))) not in completed]
    ordered = _order(remaining, played, players, discipline)
    base['matches'] = number_matches(ordered)
    base['sequence'].update(teams=teams, resting=[p for p in players if all(p not in t['members'] for t in teams)])
    return base


if __name__ == '__main__':
    plan_cli(build)
