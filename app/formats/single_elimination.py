"""单淘汰：固定签表，败者出局，人数不足二次幂时轮空。"""
from .common import assigned_sides, group, knockout, plan_cli
META = {"id": "single_elimination", "name": "单淘汰", "min_players": 2, "max_players": 256,
        "description": "最快决出冠军；支持单打与固定双打以及轮空。",
        "rules": "每场负者出局，胜者依签表晋级；轮空不计胜场和得失分。"}
def build(participants, options, seed):
    entrants = assigned_sides(participants, options, seed)
    if len(entrants) < 2:
        raise ValueError("淘汰赛至少需要两名选手或两对搭档")
    matches, rounds = knockout(entrants)
    return {"groups": [group("A", entrants)], "matches": matches, "ranking": "sides", "final_number": rounds[-1][0]}
if __name__ == "__main__":
    plan_cli(build)
