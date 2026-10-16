"""分组循环接单淘汰；小组末位同分时要求明确抽签。"""
from .common import assigned_sides, assigned_groups, group, number_matches, plan_cli, round_robin
META = {"id": "group_knockout", "name": "分组淘汰", "min_players": 4, "max_players": 256,
        "description": "先分组循环，再按各组名次交叉进入淘汰赛。",
        "rules": "组内按胜场、局净胜、分净胜排名；影响出线的完全同分需组织者执行晋级抽签。淘汰阶段败者出局。"}
def build(participants, options, seed):
    entrants = assigned_sides(participants, options, seed)
    count = int(options.get("group_count", 2))
    advance = int(options.get("advance_per_group", 1))
    if not 2 <= count <= len(entrants)//2:
        raise ValueError("分组数量需至少2组，且每组至少2名选手/队伍")
    if not 1 <= advance < len(entrants)//count:
        raise ValueError("每组晋级数必须小于最小组人数")
    groups, matches = [], []
    pools = assigned_groups(entrants, count, options.get("assignment_strategy", "random"))
    for i in range(count):
        label = chr(65+i) if i < 26 else f"G{i+1}"
        members = pools[i]
        groups.append(group(label, members))
        matches.extend(round_robin(members, label, "groups"))
    return {"groups": groups, "matches": number_matches(matches), "ranking": "sides"}
if __name__ == "__main__":
    plan_cli(build)
