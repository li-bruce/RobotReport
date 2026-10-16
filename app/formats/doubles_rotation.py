"""多人数双打轮转：优先安排出场较少者，降低重复搭档次数。"""
import random
from ..gender import validate_roster
from .common import group, match, number_matches, plan_cli, shuffled, sides, require_doubles, level, STRATEGIES
META = {"id": "doubles_rotation", "name": "双打轮转", "min_players": 4, "max_players": 40,
        "description": "适合4—40人的俱乐部活动；轮换搭档，按个人成绩排名。",
        "rules": "每轮选出场较少的4人，优先减少重复搭档；默认场数等于人数。按胜率、局净胜、分净胜排名。此为俱乐部均衡算法，并非统一竞赛规则。"}
def build(participants, options, seed):
    require_doubles(options)
    kind = validate_roster(participants, options, 'doubles_rotation')
    strategy = options.get("assignment_strategy", "random")
    if strategy not in STRATEGIES:
        raise ValueError("未知分配策略")
    players = shuffled(participants, seed)
    if len(players) < 4:
        raise ValueError("双打轮转至少需要4人")
    count = int(options.get("rounds", len(players)))
    if not 1 <= count <= 200:
        raise ValueError("双打轮转场数应在1—200之间")
    rng = random.Random(seed)
    played = {str(p["id"]): 0 for p in players}
    partners, matches = {}, []
    for r in range(count):
        pool = list(players)
        rng.shuffle(pool)
        pool.sort(key=lambda p: played[str(p["id"])])
        chosen = ([p for p in pool if p.get('gender') == 'male'][:2] + [p for p in pool if p.get('gender') == 'female'][:2]) if kind == 'mixed' else pool[:4]
        variants = [((0,1),(2,3)), ((0,2),(1,3)), ((0,3),(1,2))]
        if kind == 'mixed':
            variants = [v for v in variants if all({chosen[i]['gender'] for i in pair} == {'male', 'female'} for pair in v)]
        rng.shuffle(variants)
        def cost(v):
            repeats = sum(partners.get(tuple(sorted(str(chosen[i]["id"]) for i in pair)), 0) for pair in v)
            strengths = [sum(level(chosen[i]) for i in pair) for pair in v]
            if strategy == "balanced":
                return (repeats, abs(strengths[0]-strengths[1]))
            if strategy == "high_low":
                return (repeats, -sum(abs(level(chosen[pair[0]])-level(chosen[pair[1]])) for pair in v))
            if strategy == "seeded":
                top = sorted(range(4), key=lambda i: level(chosen[i]), reverse=True)[:2]
                together = any(all(i in pair for i in top) for pair in v)
                return (repeats, together, abs(strengths[0]-strengths[1]))
            return (repeats, 0)
        a, b = min(variants, key=cost)
        for pair in (a,b):
            key = tuple(sorted(str(chosen[i]["id"]) for i in pair))
            partners[key] = partners.get(key,0)+1
        for p in chosen:
            played[str(p["id"])] += 1
        matches.append(match(sides([chosen[i] for i in a],2)[0], sides([chosen[i] for i in b],2)[0], round=r+1, group_id="A"))
    return {"groups": [group("A", sides(players))], "matches": number_matches(matches), "ranking": "individual"}
if __name__ == "__main__":
    plan_cli(build)
