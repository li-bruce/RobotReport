"""积分赛：固定个人或双打搭档单循环。"""
from .common import assigned_sides, group, number_matches, plan_cli, round_robin
META = {"id": "round_robin", "name": "积分赛", "min_players": 2, "max_players": 128,
        "description": "随机排位后进行单循环；可选单打或固定双打。",
        "rules": "每对对手交手一次；胜场、局净胜、分净胜依次排序；完全相同并列。默认一局21分，20平需领先2分，30封顶。"}
def build(participants, options, seed):
    entrants = assigned_sides(participants, options, seed)
    if len(entrants) < 2:
        raise ValueError("循环赛至少需要两名选手或两对搭档")
    return {"groups": [group("A", entrants)], "matches": number_matches(round_robin(entrants, "A")), "ranking": "sides"}
if __name__ == "__main__":
    plan_cli(build)
