"""Independent, deterministic schedule generators selected from an explicit allowlist."""
import importlib
from ..gender import category, eligible, validate_roster
FORMAT_IDS = ("round_robin", "five_rotation", "five_team", "single_elimination",
              "double_elimination", "group_knockout", "doubles_rotation")
def get_format(format_id):
    if format_id not in FORMAT_IDS:
        raise ValueError("未知赛制，请使用帮助中列出的赛制")
    return importlib.import_module(f"{__name__}.{format_id}")
def metadata(discipline="doubles", participant_count=None, options=None, substitute_count=0):
    if discipline not in ("singles", "doubles"):
        raise ValueError("比赛类型仅支持单打或双打")
    options = options or {}
    kind = category({**options, 'discipline': discipline})
    result = []
    for key in FORMAT_IDS:
        item = dict(get_format(key).META)
        special = key in ("five_rotation", "five_team", "doubles_rotation")
        item["disciplines"] = ["doubles"] if special else ["singles", "doubles"]
        item["discipline"] = discipline
        item['doubles_category'] = kind
        item["player_step"] = 5 if key == "five_team" else 1 if special or discipline == "singles" else 2
        item["assignment_strategies"] = ["random"] if key == "five_rotation" else ["random", "seeded", "balanced", "high_low"]
        if not special:
            size = 1 if discipline == "singles" else 2
            item["min_players"] *= size
            if key == "group_knockout":
                item["min_players"] = max(item["min_players"], int(options.get("group_count", 2)) * (int(options.get("advance_per_group", 1))+1) * size)
        reason = ""
        if discipline not in item["disciplines"]:
            reason = "此赛制仅支持双打"
        elif participant_count is not None:
            if not item["min_players"] <= participant_count <= item["max_players"]:
                reason = f"需 {item['min_players']}—{item['max_players']} 人，当前 {participant_count} 人"
            elif participant_count % item["player_step"]:
                reason = f"人数须为 {item['player_step']} 的倍数"
        strategy = options.get("assignment_strategy", "random")
        if strategy not in item["assignment_strategies"]:
            reason = "五人转使用固定公平轮转，仅支持随机排位" if key == "five_rotation" else "未知分配策略"
        selected_count = participant_count
        if reason and participant_count is not None and substitute_count > 0 and discipline in item['disciplines'] and strategy in item['assignment_strategies']:
            candidate = max(participant_count, item['min_players'])
            candidate += (-candidate) % item['player_step']
            if candidate <= item['max_players'] and candidate <= participant_count + substitute_count:
                selected_count, reason = candidate, ''
        if kind == 'mixed' and key in ('five_rotation', 'five_team'):
            reason = '五人轮换无法保证每队一男一女，请选择混双轮转或固定双打赛制'
        if 'participants' in options:
            all_formal, all_subs = options['participants'], options.get('substitutes', [])
            formal = [p for p in all_formal if eligible(p, kind)]
            subs = [p for p in all_subs if eligible(p, kind)]
            selected = list(formal)
            if discipline in item['disciplines'] and strategy in item['assignment_strategies'] and not (kind == 'mixed' and key in ('five_rotation', 'five_team')):
                reason = ''
                # Keep every eligible formal participant; add the earliest
                # substitutes needed for a valid roster, without changing signups.
                while True:
                    men = sum(p.get('gender') == 'male' for p in selected)
                    women = sum(p.get('gender') == 'female' for p in selected)
                    wanted = None
                    if kind == 'mixed' and key != 'doubles_rotation' and men != women:
                        wanted = 'male' if men < women else 'female'
                    elif kind == 'mixed' and min(men, women) < 2:
                        wanted = 'male' if men < 2 else 'female'
                    enough = len(selected) >= item['min_players'] and len(selected) % item['player_step'] == 0
                    if wanted is None and enough:
                        break
                    addition = next((p for p in subs if wanted is None or p.get('gender') == wanted), None)
                    if addition is None:
                        break
                    selected.append(addition); subs.remove(addition)
                if not item['min_players'] <= len(selected) <= item['max_players']:
                    reason = f"需 {item['min_players']}—{item['max_players']} 名符合性别要求的参赛者，当前可选 {len(selected)} 人"
                elif len(selected) % item['player_step']:
                    reason = f"人数须为 {item['player_step']} 的倍数"
                else:
                    try:
                        validate_roster(selected, {**options, 'discipline': discipline}, key)
                    except ValueError as error:
                        reason = str(error)
            selected_count = len(selected)
            item['selected_ids'] = [p['id'] for p in selected] if not reason else []
            item['excluded_count'] = len(all_formal) + len(all_subs) - len(formal) - len([p for p in all_subs if eligible(p, kind)])
            item['gender_counts'] = {g: sum(p.get('gender', 'unknown') == g for p in selected) for g in ('male', 'female', 'unknown')}
            participant_count = len(formal)
        item["available"], item["reason"] = not bool(reason), reason
        item['selected_count'] = selected_count if not reason else None
        item['substitutes_needed'] = max(0, selected_count - participant_count) if not reason and participant_count is not None else 0
        result.append(item)
    return result


def available_formats(count, discipline="doubles", options=None, substitute_count=0):
    """All formats with explicit availability/reason for planning prompts."""
    return metadata(discipline, count, options, substitute_count)
