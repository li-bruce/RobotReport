"""Shared, explicit gender/category validation; never infer gender from a name."""
GENDERS = {'male': '男', 'female': '女', 'unknown': '未填写'}
CATEGORIES = {'open': '不限双打', 'mens': '男双', 'womens': '女双', 'mixed': '混双'}


def normalize_gender(value, required=False):
    aliases = {'男': 'male', '女': 'female', '未填写': 'unknown', '': 'unknown'}
    value = aliases.get(value, value) if isinstance(value, str) else 'unknown' if value is None else value
    if not isinstance(value, str) or value not in GENDERS or (required and value == 'unknown'):
        raise ValueError('请选择性别：男或女')
    return value


def category(options):
    value = options.get('doubles_category', 'open')
    if not isinstance(value, str) or value not in CATEGORIES:
        raise ValueError('双打类型须为不限双打、男双、女双或混双')
    if options.get('discipline') == 'singles' and value != 'open':
        raise ValueError('单打不能设置双打类型')
    return value


def eligible(person, kind):
    gender = normalize_gender(person.get('gender'))
    return kind == 'open' or (gender in ('male', 'female') if kind == 'mixed' else gender == ('male' if kind == 'mens' else 'female'))


def require_person(person, kind):
    if eligible(person, kind):
        return
    if normalize_gender(person.get('gender')) == 'unknown':
        raise ValueError('参加男双、女双或混双前请补齐性别；代报人员须先独立注册并补齐资料')
    raise ValueError('性别不符合' + CATEGORIES[kind] + '参赛要求')


def validate_roster(participants, options, format_id=None):
    kind = category(options)
    if kind == 'mixed' and format_id in ('five_rotation', 'five_team'):
        raise ValueError('五人转及五羽伦比的五人轮换不能保证每队一男一女，请选择混双轮转或固定双打赛制')
    for person in participants:
        require_person(person, kind)
    if kind == 'mixed':
        men = sum(p.get('gender') == 'male' for p in participants)
        women = sum(p.get('gender') == 'female' for p in participants)
        if format_id == 'doubles_rotation':
            if min(men, women) < 2:
                raise ValueError('混双轮转至少需要2男2女')
        elif men != women or men < 2:
            raise ValueError('固定混双需要男女数量相等，且至少2男2女')
    return kind
