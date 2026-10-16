"""五人转：5场、每人4场/轮空1场、每对搭档恰好一次。"""
from ..gender import validate_roster
from .common import group, match, number_matches, plan_cli, shuffled, sides, require_doubles
META = {"id": "five_rotation", "name": "五人转", "min_players": 5, "max_players": 5,
        "description": "5个人轮换双打搭档，各打一场后轮空；按个人成绩排名。",
        "rules": "固定5人、5场：每人上场4次，轮空1次；每对搭档合作1次。胜场、局净胜、分净胜排名，完全相同并列。俱乐部公平轮转模板。"}
def build(participants, options, seed):
    require_doubles(options)
    validate_roster(participants, options, 'five_rotation')
    if options.get("assignment_strategy", "random") != "random":
        raise ValueError("五人转使用固定公平轮转，仅支持随机排位")
    if len(participants) != 5:
        raise ValueError("五人转需要恰好5人；其他人数可选择双打轮转")
    players = shuffled(participants, seed)
    rounds = [((1,4),(2,3)), ((2,0),(3,4)), ((3,1),(4,0)), ((4,2),(0,1)), ((0,3),(1,2))]
    matches = []
    for r, (a, b) in enumerate(rounds, 1):
        matches.append(match(sides([players[i] for i in a], 2)[0], sides([players[i] for i in b], 2)[0], round=r, group_id="A"))
    return {"groups": [group("A", sides(players))], "matches": number_matches(matches), "ranking": "individual"}
if __name__ == "__main__":
    plan_cli(build)
