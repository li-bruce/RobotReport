"""双败淘汰：胜者组、败者组、总决赛与必要的重置决赛。"""
from .common import assigned_sides, group, knockout, match, plan_cli, source
META = {"id": "double_elimination", "name": "双败淘汰", "min_players": 2, "max_players": 128,
        "description": "累计两败才出局，胜者组冠军保留一次失败容错。",
        "rules": "首败进入败者组；败者组再负出局。败者组冠军若赢总决赛，追加重置决赛。俱乐部组织模板。"}
def build(participants, options, seed):
    entrants = assigned_sides(participants, options, seed)
    if len(entrants) < 2:
        raise ValueError("双败赛至少需要两名选手或两对搭档")
    matches, wb = knockout(entrants, stage="winners")
    def add(a, b, r):
        matches.append(match(stage="losers", round=r, a_source=a, b_source=b))
        return len(matches)
    if len(wb) == 1:
        lb_final = source(wb[0][0], "loser")
    else:
        previous = [add(source(wb[0][i], "loser"), source(wb[0][i+1], "loser"), 1)
                    for i in range(0, len(wb[0]), 2)]
        lr = 2
        for r in range(1, len(wb)):
            drops = list(reversed(wb[r]))
            previous = [add(source(m), source(drops[i], "loser"), lr) for i, m in enumerate(previous)]
            lr += 1
            if r < len(wb)-1:
                previous = [add(source(previous[i]), source(previous[i+1]), lr)
                            for i in range(0, len(previous), 2)]
                lr += 1
        lb_final = source(previous[0])
    matches.append(match(stage="grand_final", a_source=source(wb[-1][0]), b_source=lb_final))
    for i, m in enumerate(matches, 1):
        m["number"] = i
    return {"groups": [group("A", entrants)], "matches": matches, "ranking": "sides", "final_number": len(matches)}
if __name__ == "__main__":
    plan_cli(build)
