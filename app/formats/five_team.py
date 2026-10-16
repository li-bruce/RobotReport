"""五羽伦比：五人一队的轮换追逐赛，团队累计分数。"""
from .common import assigned_sides, group, number_matches, plan_cli, round_robin, require_doubles
META = {"id": "five_team", "name": "五羽伦比", "min_players": 10, "max_players": 160,
        "description": "每队5人，固定编号轮换搭档，团队50分追逐赛；与五人转不同。",
        "rules": "默认每队5人、50分封顶，每10分进入下一段；组合12、23、34、45、51。按团队总比分登记；可配置75/100分及换人段分。支持不限、全男或全女阵容；五人环形轮换不支持每对一男一女的混双。"}
def build(participants, options, seed):
    require_doubles(options)
    entrants = assigned_sides(participants, options, seed, team_size=5)
    if len(entrants) < 2:
        raise ValueError("五羽伦比至少10人，每队5人；总人数须为5的倍数")
    for i, entry in enumerate(entrants, 1):
        entry["name"] = f"第{i}队（{entry['name']}）"
        entry["rotation"] = [[entry["members"][a], entry["members"][b]] for a, b in ((0,1),(1,2),(2,3),(3,4),(4,0))]
    return {"groups": [group("A", entrants)], "matches": number_matches(round_robin(entrants, "A")), "ranking": "sides"}
if __name__ == "__main__":
    plan_cli(build)
