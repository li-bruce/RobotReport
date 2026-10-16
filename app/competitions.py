"""Durable badminton tournament domain service.

The HTTP/robot boundary supplies the authenticated actor and admin flag, and
must check the tournament chat/venue before invoking a command. This module
never trusts a role in a message, never calls the network, and opens only the
business database passed to its constructor. All writes use BEGIN IMMEDIATE.
"""
from __future__ import annotations

import json
import random
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from .formats import get_format, metadata
from .formats.common import knockout, match, player_name, level

TZ = ZoneInfo("Asia/Shanghai")
TERMINAL = {"completed", "archived", "cancelled"}


from .gender import normalize_gender, category as doubles_category, require_person, validate_roster, CATEGORIES


def _now():
    return datetime.now(TZ)


def _time(value=None):
    if value is None:
        return _now()
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("时间应为 ISO 日期时间") from exc
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value, fallback=None):
    return json.loads(value) if value else fallback


def _clean(value, label, limit=120, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{label}必须为文本")
    value = value.strip()
    if (required and not value) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label}长度或内容不合法")
    return value


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label}应为 {low}—{high} 的整数")
    return value


def _safe_md(value):
    return str(value).replace("<", "＜").replace(">", "＞").replace("&", "＆").replace("[", "［").replace("]", "］").replace("*", "＊").replace("`", "｀").replace("|", "｜").replace("\r", " ").replace("\n", " ")


def _table(headers, rows):
    values = [headers, ["---"] * len(headers), *rows]
    return "\n".join("| " + " | ".join(_safe_md(cell) for cell in row) + " |" for row in values)


def _participant(row):
    result = dict(row)
    result["group_ids"] = _load(result.get("group_ids"), []) if isinstance(result.get("group_ids"), str) else result.get("group_ids", [])
    result["level"] = level(result)
    result["gender"] = normalize_gender(result.get("gender"))
    if not result.get("name") or result["name"] in (result["user_id"], result["id"], f"{result['user_id']}+{result.get('guest_index', 0)}"):
        result["name"] = "待完善姓名"
    return result


SCHEMA = """
CREATE TABLE IF NOT EXISTS tour_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL,
 format TEXT NOT NULL, venue_id INTEGER NOT NULL, chat_id TEXT NOT NULL,
 date TEXT NOT NULL, keyword TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
 registration_open TEXT, registration_close TEXT, group_count INTEGER NOT NULL DEFAULT 2,
 creator TEXT NOT NULL, options TEXT NOT NULL, staff TEXT NOT NULL DEFAULT '[]',
 seed INTEGER NOT NULL, groups_json TEXT NOT NULL DEFAULT '[]', plan_json TEXT NOT NULL DEFAULT '{}',
 champion_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_tour_scope ON tour_events(venue_id,chat_id,status,date);
CREATE TABLE IF NOT EXISTS tour_participants (
 tournament_id INTEGER NOT NULL REFERENCES tour_events(id), id TEXT NOT NULL,
 user_id TEXT NOT NULL, name TEXT NOT NULL, guest_index INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'active', registered_at TEXT NOT NULL, cancelled_at TEXT,
 PRIMARY KEY(tournament_id,id)
);
CREATE TABLE IF NOT EXISTS tour_matches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER NOT NULL REFERENCES tour_events(id),
 number INTEGER NOT NULL, stage TEXT NOT NULL, round INTEGER NOT NULL, group_id TEXT NOT NULL DEFAULT '',
 side_a TEXT, side_b TEXT, a_source TEXT, b_source TEXT, status TEXT NOT NULL DEFAULT 'pending',
 scores TEXT NOT NULL DEFAULT '[]', winner TEXT, version INTEGER NOT NULL DEFAULT 0,
 scheduled_at TEXT, court TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 UNIQUE(tournament_id,number)
);
CREATE INDEX IF NOT EXISTS ix_tour_match ON tour_matches(tournament_id,stage,status);
CREATE TABLE IF NOT EXISTS tour_audit (
 id INTEGER PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tour_events(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tour_notifications (
 key TEXT PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tour_events(id),
 chat_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tour_organization (
 tournament_id INTEGER PRIMARY KEY REFERENCES tour_events(id),
 description TEXT NOT NULL DEFAULT '', rules_text TEXT NOT NULL DEFAULT '',
 meeting_instructions TEXT NOT NULL DEFAULT '', coordination TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tour_event_channels (
 tournament_id INTEGER NOT NULL REFERENCES tour_events(id), channel_id TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(tournament_id,channel_id)
);
"""


class TournamentService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tour_participants)")}
            if "level" not in columns:
                db.execute("ALTER TABLE tour_participants ADD COLUMN level INTEGER NOT NULL DEFAULT 0")
            if "group_ids" not in columns:
                db.execute("ALTER TABLE tour_participants ADD COLUMN group_ids TEXT NOT NULL DEFAULT '[]'")
            if "source_category" not in columns:
                db.execute("ALTER TABLE tour_participants ADD COLUMN source_category TEXT NOT NULL DEFAULT '正式'")
            if "is_proxy" not in columns:
                db.execute("ALTER TABLE tour_participants ADD COLUMN is_proxy INTEGER NOT NULL DEFAULT 0")
            if "gender" not in columns:
                db.execute("ALTER TABLE tour_participants ADD COLUMN gender TEXT NOT NULL DEFAULT 'unknown'")
            db.commit()

    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def formats(discipline="doubles", participant_count=None, options=None, substitute_count=0):
        return metadata(discipline, participant_count, options, substitute_count)

    @staticmethod
    def available_formats(count, discipline="doubles", options=None, substitute_count=0):
        return metadata(discipline, count, options, substitute_count)

    def _event(self, db, event_id):
        row = db.execute("SELECT * FROM tour_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise ValueError("赛事不存在")
        event = dict(row)
        for src, dest, fallback in (("options", "options", {}), ("staff", "staff", []),
                                    ("groups_json", "groups", []), ("plan_json", "plan", {}),
                                    ("champion_json", "champion", [])):
            event[dest] = _load(event.pop(src), fallback)
        if 'discipline' not in event['options']:
            # v1 used team_size=1 even for the rotating doubles templates.
            # Normalize only their option view; never touch persisted matches.
            special = event['format'] in ('five_rotation', 'five_team', 'doubles_rotation')
            event['options']['discipline'] = 'doubles' if special or event['options'].get('team_size') != 1 else 'singles'
            if special:
                event['options']['team_size'] = 5 if event['format'] == 'five_team' else 2
        event['options'].setdefault('doubles_category', 'open')
        organization = db.execute('SELECT description,rules_text,meeting_instructions,coordination FROM tour_organization WHERE tournament_id=?', (event_id,)).fetchone()
        event['organization'] = dict(organization) if organization else {'description': '', 'rules_text': '', 'meeting_instructions': '', 'coordination': ''}
        event['channel_ids'] = [r[0] for r in db.execute('SELECT channel_id FROM tour_event_channels WHERE tournament_id=? ORDER BY channel_id', (event_id,))]
        return event

    @staticmethod
    def _audit(db, event_id, actor, action, before=None, after=None):
        db.execute("INSERT INTO tour_audit(tournament_id,actor,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)",
                   (event_id, str(actor), action, _dump(before), _dump(after), _now().isoformat()))

    @staticmethod
    def _touch(db, event_id):
        db.execute("UPDATE tour_events SET updated_at=?,version=version+1 WHERE id=?", (_now().isoformat(), event_id))

    @staticmethod
    def _options(format_id, data):
        if not isinstance(data, dict):
            raise ValueError("options 必须为对象")
        allowed = {"scoring_mode", "leader_user_ids", "best_of", "points", "cap", "win_by", "team_size",
                   "advance_per_group", "rounds", "capacity", "relay_segment", "notes", "session_id",
                   "discipline", "doubles_category", "assignment_strategy", "eligibility", "planner_token", "selected_substitute_names", "organizer_name", "session_snapshot", "reservations"}
        if set(data) - allowed:
            raise ValueError("包含不支持的赛制设置：" + ",".join(sorted(set(data)-allowed)))
        opts = {"scoring_mode": "members", "leader_user_ids": [], "best_of": 1,
                "points": 21, "cap": 30, "win_by": 2, "team_size": 2,
                "discipline": "doubles", "doubles_category": "open", "assignment_strategy": "random", "eligibility": {},
                "advance_per_group": 1, "capacity": get_format(format_id).META["max_players"]}
        if format_id == "five_team":
            opts.update(points=50, cap=50, win_by=1, relay_segment=10, team_size=5)
        opts.update(data)
        if 'reservations' in opts:
            from .availability import intervals
            opts['reservations'] = intervals(opts['reservations'])
        if 'session_id' in opts and opts['session_id'] is None:
            opts.pop('session_id')
            opts.pop('session_snapshot', None)
        if "discipline" not in data and data.get("team_size") == 1:
            opts["discipline"] = "singles"
        if opts["discipline"] not in ("singles", "doubles"):
            raise ValueError("比赛类型仅支持单打或双打")
        doubles_category(opts)
        item = next(item for item in metadata(opts["discipline"], options=opts) if item["id"] == format_id)
        if not item["available"]:
            raise ValueError(item["reason"])
        expected_size = 5 if format_id == "five_team" else 1 if opts["discipline"] == "singles" else 2
        if "team_size" in data and (type(data["team_size"]) is not int or data["team_size"] != expected_size):
            raise ValueError("队伍人数与单打/双打设置不一致")
        opts["team_size"] = expected_size
        eligibility = opts["eligibility"]
        if not isinstance(eligibility, dict) or set(eligibility)-{"min_level", "max_level", "allowed_group_ids"}:
            raise ValueError("报名条件字段不合法")
        eligibility = dict(eligibility)
        for key in ("min_level", "max_level"):
            if eligibility.get(key) is not None:
                _integer(eligibility[key], 1, 9, key)
        if (eligibility.get("min_level") or 1) > (eligibility.get("max_level") or 9):
            raise ValueError("最低等级不能高于最高等级")
        groups = eligibility.get("allowed_group_ids", [])
        if not isinstance(groups, list) or len(groups) > 100:
            raise ValueError("允许的会员分组不合法")
        eligibility["allowed_group_ids"] = list(dict.fromkeys(_integer(x, 1, 2**63-1, "会员分组ID") for x in groups))
        opts["eligibility"] = eligibility
        if 'organizer_name' in opts:
            opts['organizer_name'] = _clean(opts['organizer_name'], '组织者姓名', 80)
        if 'selected_substitute_names' in opts:
            names = opts['selected_substitute_names']
            if not isinstance(names, list) or len(names) > 256:
                raise ValueError('替补补齐记录不合法')
            opts['selected_substitute_names'] = [_clean(name, '替补姓名', 100) for name in names]
        if opts["scoring_mode"] not in ("members", "leader"):
            raise ValueError("记分模式应为 members 或 leader")
        if not isinstance(opts["leader_user_ids"], list) or len(opts["leader_user_ids"]) > 50:
            raise ValueError("组长列表不合法")
        opts["leader_user_ids"] = list(dict.fromkeys(_clean(str(x), "组长ID", 80) for x in opts["leader_user_ids"]))
        for key, lo, hi in (("points", 1, 100), ("cap", 1, 150), ("win_by", 1, 2),
                            ("capacity", 2, get_format(format_id).META["max_players"]),
                            ("advance_per_group", 1, 8)):
            _integer(opts[key], lo, hi, key)
        if opts["best_of"] not in (1, 3, 5) or isinstance(opts["best_of"], bool):
            raise ValueError("best_of 仅支持1、3、5局")
        if opts["cap"] < opts["points"]:
            raise ValueError("封顶分不能小于目标分")
        if isinstance(opts["team_size"], bool) or (opts["team_size"] not in (1,2) and not (format_id == "five_team" and opts["team_size"] == 5)):
            raise ValueError("该赛制仅支持单打或固定双打")
        if "rounds" in opts:
            _integer(opts["rounds"], 1, 200, "场数")
        if "session_id" in opts:
            _integer(opts["session_id"],1,2**63-1,"报名场次ID")
        if 'session_snapshot' in opts:
            saved = opts['session_snapshot']
            if not isinstance(saved, dict) or set(saved) - {'id', 'venue_id', 'venue_name', 'date', 'name', 'start', 'end', 'slot_key'} or saved.get('id') != opts.get('session_id'):
                raise ValueError('赛事活动场次快照无效')
        if "planner_token" in opts:
            token = opts["planner_token"]
            if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                raise ValueError("策划标识不合法")
        if format_id == "five_team":
            if opts["best_of"] != 1 or opts["cap"] != opts["points"] or opts["win_by"] != 1 or opts["team_size"] != 5:
                raise ValueError("五羽伦比使用一局累计分、目标分即封顶分，不加分，固定五人队")
            _integer(opts["relay_segment"], 1, 25, "换人段分")
            if opts["points"] != opts["relay_segment"] * 5:
                raise ValueError("五羽伦比总分必须等于五个换人段分之和")
        if "notes" in opts:
            opts["notes"] = _clean(opts["notes"], "规则备注", 2000, False)
        return opts

    @staticmethod
    def _staff(value):
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError("临时人员列表不合法")
        result, seen = [], set()
        for entry in value:
            if not isinstance(entry, dict) or set(entry)-{"user_id", "role", "starts_at", "expires_at"}:
                raise ValueError("临时人员字段不合法")
            uid = _clean(str(entry.get("user_id", "")), "临时人员ID", 80)
            if not entry.get("starts_at") or not entry.get("expires_at"):
                raise ValueError("临时人员必须配置生效和失效时间")
            start, end = _time(entry["starts_at"]), _time(entry["expires_at"])
            role = entry.get("role", "scorer")
            if start >= end or role not in ('organizer', 'scorer', 'coordinator') or uid in seen:
                raise ValueError("临时人员角色、有效时间或重复ID不合法")
            seen.add(uid)
            result.append({"user_id": uid, "role": role, "starts_at": start.isoformat(), "expires_at": end.isoformat()})
        return result

    @staticmethod
    def permissions(event, actor, is_admin=False, at=None):
        """A staff appointment grants only its event role, never a club role."""
        moment = _time(at)
        roles = {s['role'] for s in event.get('staff', []) if s['user_id'] == str(actor)
                 and _time(s['starts_at']) <= moment < _time(s['expires_at'])}
        owner = bool(is_admin or str(actor) == event['creator'] or 'organizer' in roles)
        coordinator = owner or 'coordinator' in roles
        return {'view': True, 'manage': owner, 'score': owner or 'scorer' in roles,
                'correct_score': owner, 'coordinate': coordinator, 'manage_staff': owner,
                'appoint_organizer': bool(is_admin), 'manage_channels': owner,
                'role': 'administrator' if is_admin else 'organizer' if owner else
                        'scorer' if 'scorer' in roles else 'coordinator' if 'coordinator' in roles else 'participant'}

    @staticmethod
    def _approved(db, event, users):
        # The pure tournament engine also runs as independent format scripts.
        # In the application database, membership is authoritative inside the
        # same write transaction as the permission check.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='member_records'").fetchone():
            return
        for user in set(map(str, users)):
            row = db.execute('''SELECT m.status,p.name FROM member_records m LEFT JOIN profiles p
                                ON p.chat_id=m.chat_id AND p.user_id=m.user_id
                                WHERE m.chat_id=? AND m.user_id=?''', (event['chat_id'], user)).fetchone()
            if not row or row['status'] != 'approved' or not row['name'] or row['name'] == user:
                raise PermissionError('赛事职务仅可由已审核会员担任')

    def _validate_staff_change(self, db, event, value, actor, is_admin):
        staff = self._staff(value)
        self._manage(event, actor, is_admin)
        if not is_admin:
            old_owners = [s for s in event.get('staff', []) if s['role'] == 'organizer']
            new_owners = [s for s in staff if s['role'] == 'organizer']
            if sorted(old_owners, key=lambda s: s['user_id']) != sorted(new_owners, key=lambda s: s['user_id']):
                raise PermissionError('只有后台管理员可以任命或变更赛事负责人')
        self._approved(db, event, [s['user_id'] for s in staff])
        return staff

    def _manage(self, event, actor, is_admin, action="manage"):
        if not self.permissions(event, actor, is_admin)['manage']:
            raise PermissionError("请由本次赛事组织者或管理员操作")

    def _can_score(self, event, actor, is_admin):
        if not self.permissions(event, actor, is_admin)['score']:
            raise PermissionError("请由赛事负责人、有效期内记分员或管理员记分")

    def create(self, data: dict, actor: str, is_admin: bool, creation_guard=None, channel_ids=None):
        if not isinstance(data, dict):
            raise ValueError("赛事配置必须为对象")
        actor = _clean(str(actor), "操作者", 80)
        kind = data.get("kind", "daily")
        if kind not in ("daily", "large"):
            raise ValueError("赛事类型不合法")
        if kind == "large" and not is_admin:
            raise PermissionError("只有管理员可以创建大型赛事")
        creator = actor
        if data.get('organizer_user_id'):
            if not is_admin:
                raise PermissionError('只有管理员可以指定其他组织者')
            creator = _clean(str(data['organizer_user_id']), '组织者身份', 80)
        format_id = data.get("format", "round_robin")
        module = get_format(format_id)
        title = _clean(data.get("title", module.META["name"]), "赛事名称")
        venue = _integer(data.get("venue_id", 1), 1, 1000000000, "场地编号")
        chat = _clean(str(data.get("chat_id", "")), "群ID", 80)
        try:
            day = date.fromisoformat(str(data.get("date", _now().date().isoformat()))).isoformat()
        except ValueError as exc:
            raise ValueError("赛事日期不合法") from exc
        keyword = _clean(data.get("keyword", ""), "报名关键词", 50, kind == "large")
        if any(c in keyword for c in "<>[]@/\\"):
            raise ValueError("报名关键词不能含提及标记、括号或路径字符")
        options = self._options(format_id, data.get("options", {}))
        if options.get('planner_token') and kind != 'daily':
            raise ValueError('策划标识仅用于每日赛事')
        if kind == "daily" and options["scoring_mode"] == "leader" and not options["leader_user_ids"]:
            options["leader_user_ids"] = [actor]
        group_count = _integer(data.get("group_count", 2), 2, 64, "分组数")
        staff = self._staff(data.get("staff", []))
        if staff and not is_admin:
            raise PermissionError("仅管理员可以指定赛事人员")
        now = _now().isoformat()
        open_at = _time(data.get("registration_open", now)).isoformat()
        close_at = _time(data.get("registration_close", f"{day}T23:59:59+08:00")).isoformat()
        if kind == "large" and _time(open_at) >= _time(close_at):
            raise ValueError("报名开始时间必须早于结束时间")
        participants = data.get("participants", [])
        if not isinstance(participants, list):
            raise ValueError("参赛者列表不合法")
        if len(participants) > options["capacity"]:
            raise ValueError("参赛者数量超过容量")
        channel_ids = list(channel_ids or [])
        if len(channel_ids) > 100 or any(not isinstance(item, str) or not item or len(item) > 100 for item in channel_ids) or len(set(channel_ids)) != len(channel_ids):
            raise ValueError('赛事通知通道无效')
        with self._transaction() as db:
            self._approved(db, {'chat_id': chat}, [s['user_id'] for s in staff])
            if creator != actor or not is_admin:
                self._approved(db, {'chat_id': chat}, [creator])
            token = options.get('planner_token')
            if token:
                for old in db.execute('SELECT id,format,options FROM tour_events WHERE kind=? AND chat_id=? AND venue_id=? AND creator=?', ('daily', chat, venue, creator)):
                    old_options = _load(old['options'], {})
                    if old_options.get('planner_token') == token:
                        old_discipline = old_options.get('discipline', 'singles' if old_options.get('team_size') == 1 else 'doubles')
                        if (old['format'] != format_id or old_discipline != options['discipline'] or
                                old_options.get('assignment_strategy', 'random') != options['assignment_strategy'] or
                                old_options.get('doubles_category', 'open') != options['doubles_category'] or
                                old_options.get('session_id') != options.get('session_id')):
                            raise ValueError('同一策划标识不能用于不同赛事设置')
                        return self.get(old['id'])
            if creation_guard:
                refreshed = creation_guard(db, {'kind': kind, 'format': format_id, 'venue_id': venue, 'chat_id': chat,
                                                'date': day, 'options': options, 'participants': participants,
                                                'creator': creator, 'group_count': group_count})
                participants = refreshed['participants']
                if kind == 'daily':
                    options['selected_substitute_names'] = refreshed.get('selected_substitute_names', [])
                if len(participants) > options['capacity']:
                    raise ValueError('参赛者数量超过容量')
            if keyword and db.execute("SELECT 1 FROM tour_events WHERE venue_id=? AND chat_id=? AND keyword=? AND status NOT IN ('archived','completed','cancelled')",
                                      (venue,chat,keyword)).fetchone():
                raise ValueError("该群该场地已有使用此关键词的未结束赛事")
            event_id = db.execute("""INSERT INTO tour_events(title,kind,format,venue_id,chat_id,date,keyword,status,
                registration_open,registration_close,group_count,creator,options,staff,seed,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (title,kind,format_id,venue,chat,day,keyword,"closed" if kind == "daily" else "draft",
                 open_at,close_at,group_count,creator,_dump(options),_dump(staff),secrets.randbits(52),now,now)).lastrowid
            db.executemany('INSERT INTO tour_event_channels(tournament_id,channel_id,created_by,created_at) VALUES(?,?,?,?)',
                           [(event_id, channel, actor, now) for channel in channel_ids])
            ids = set()
            for index, p in enumerate(participants):
                if not isinstance(p,dict):
                    raise ValueError("参赛者应为包含 id、user_id、name 的对象")
                uid = _clean(str(p.get("user_id", p.get("id", ""))), "选手用户ID", 80)
                # Identity sequence, not current guest count; canceled indices
                # remain historical and can legitimately exceed the roster size.
                guest_index = _integer(p.get("guest_index",0),0,2**63-1,"同行编号")
                pid = _clean(str(p.get("id", uid)), "选手ID", 100) if kind == "daily" else f"{uid}:{guest_index}"
                if pid in ids:
                    raise ValueError("参赛者ID重复")
                ids.add(pid)
                name = _clean(p.get("name") or "待完善姓名", "选手姓名", 100 if guest_index or p.get('is_proxy') else 80)
                if name in (uid, pid):
                    name = "待完善姓名"
                category = p.get('source_category', '正式')
                if category not in ('正式', '替补', '赛事'):
                    raise ValueError('参赛来源不合法')
                proxy = bool(guest_index or p.get('is_proxy'))
                gender = 'unknown' if proxy else normalize_gender(p.get('gender'))
                require_person({'gender': gender}, options['doubles_category'])
                db.execute("INSERT INTO tour_participants(tournament_id,id,user_id,name,guest_index,registered_at,level,group_ids,source_category,is_proxy,gender) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (event_id,pid,uid,name,guest_index,now,0 if proxy else level(p),_dump([] if proxy else p.get("group_ids", [])),category,int(proxy),gender))
            self._audit(db,event_id,actor,"create",None,{"title":title,"kind":kind,"format":format_id,"participants":len(participants)})
            from .availability import sync_event
            sync_event(db, self._event(db,event_id), actor, _now())
            if kind == "daily":
                self._draw(db,self._event(db,event_id),actor)
                db.execute("UPDATE tour_events SET status='running' WHERE id=?",(event_id,))
        return self.get(event_id)

    def list(self, venue_id=None, chat_id=None, status=None):
        where, args = [], []
        for field, value in (("venue_id",venue_id),("chat_id",chat_id),("status",status)):
            if value is not None:
                where.append(f"{field}=?")
                args.append(value)
        query = "SELECT id FROM tour_events" + (" WHERE "+" AND ".join(where) if where else "") + " ORDER BY date DESC,id DESC"
        with closing(self._connect()) as db:
            result = []
            for row in db.execute(query,args).fetchall():
                event = self._event(db,row[0])
                event["participant_count"] = db.execute("SELECT count(*) FROM tour_participants WHERE tournament_id=? AND status='active'",(row[0],)).fetchone()[0]
                event["match_count"] = db.execute("SELECT count(*) FROM tour_matches WHERE tournament_id=?",(row[0],)).fetchone()[0]
                event["completed_matches"] = db.execute("SELECT count(*) FROM tour_matches WHERE tournament_id=? AND status IN ('completed','bye')",(row[0],)).fetchone()[0]
                event.pop("plan",None)
                result.append(event)
            return result

    def _matches(self, db, event_id):
        result = []
        for row in db.execute("SELECT * FROM tour_matches WHERE tournament_id=? ORDER BY number",(event_id,)):
            item = dict(row)
            for key in ("side_a","side_b","a_source","b_source","scores"):
                item[key] = _load(item[key], [] if key == "scores" else None)
            for side in ("a","b"):
                item[f"side_{side}_name"] = (item[f"side_{side}"] or {}).get("name", "待定")
            result.append(item)
        return result

    def get(self, tournament_id):
        with closing(self._connect()) as db:
            event = self._event(db,tournament_id)
            event["participants"] = [_participant(p) for p in db.execute("SELECT * FROM tour_participants WHERE tournament_id=? ORDER BY registered_at,id",(tournament_id,))]
            event["matches"] = self._matches(db,tournament_id)
            # Names are presentation data. Rebuild them from the participant
            # snapshot without changing persisted identities or the draw.
            names = {p["id"]: player_name(p) for p in event["participants"]}
            def rename(entry):
                if entry:
                    entry["name"] = " / ".join(names.get(pid, "待完善姓名") for pid in entry["members"])
            for group in event["groups"]:
                for entry in group["entrants"]:
                    rename(entry)
            for entry in event["champion"]:
                rename(entry)
            for item in event["matches"]:
                for side in ("a", "b"):
                    rename(item[f"side_{side}"])
                    item[f"side_{side}_name"] = (item[f"side_{side}"] or {}).get("name", "待定")
            event["standings"] = self._standings(event,event["matches"])
            event["history"] = []
            for row in db.execute("SELECT * FROM tour_audit WHERE tournament_id=? ORDER BY id",(tournament_id,)):
                entry = dict(row)
                entry["before"] = _load(entry.pop("before_json"))
                entry["after"] = _load(entry.pop("after_json"))
                event["history"].append(entry)
            event["participant_count"] = sum(p["status"] == "active" for p in event["participants"])
            event["completed_matches"] = sum(m["status"] in ("completed","bye") for m in event["matches"])
            event["match_count"] = len(event["matches"])
            event["rules"] = get_format(event["format"]).META["rules"]
            return event

    def update(self, tournament_id, data, actor, is_admin):
        if not isinstance(data,dict):
            raise ValueError("更新必须为对象")
        allowed = {"title","keyword","registration_open","registration_close","group_count","options","staff","match_schedule","expected_version"}
        if set(data)-allowed:
            raise ValueError("包含不可修改的赛事字段")
        with self._transaction() as db:
            event = self._event(db,tournament_id)
            if 'expected_version' in data and data['expected_version']!=event['version']:
                raise ValueError('赛事已更新，请刷新后重试')
            if not is_admin:
                self._approved(db, event, [actor])
            if set(data)-{'expected_version'} == {'match_schedule'}:
                if not self.permissions(event, actor, is_admin)['coordinate']:
                    raise PermissionError('请由赛事负责人或协调员安排赛程')
            else:
                self._manage(event,actor,is_admin)
            if event["status"] in ("archived", "cancelled"):
                raise ValueError("已关闭或归档赛事不可修改")
            before = {k:event.get(k) for k in data if k != "match_schedule"}
            updates = {}
            has_draw = bool(db.execute("SELECT 1 FROM tour_matches WHERE tournament_id=? LIMIT 1",(tournament_id,)).fetchone())
            for key in ("title","keyword"):
                if key in data:
                    updates[key] = _clean(data[key],key,120 if key == "title" else 50,event["kind"] == "large" or key == "title")
            if "keyword" in updates:
                if any(c in updates["keyword"] for c in "<>[]@/\\"):
                    raise ValueError("报名关键词不能含特殊提及标记")
                if updates["keyword"] and db.execute("SELECT 1 FROM tour_events WHERE id<>? AND venue_id=? AND chat_id=? AND keyword=? AND status NOT IN ('completed','archived','cancelled')",
                    (tournament_id,event["venue_id"],event["chat_id"],updates["keyword"])).fetchone():
                    raise ValueError("报名关键词已被其他未结束赛事使用")
            for key in ("registration_open","registration_close"):
                if key in data:
                    if has_draw:
                        raise ValueError("已抽签赛事不能更改报名窗口")
                    updates[key] = _time(data[key]).isoformat()
            if _time(updates.get("registration_open",event["registration_open"])) >= _time(updates.get("registration_close",event["registration_close"])):
                raise ValueError("报名开始时间必须早于结束时间")
            if "group_count" in data:
                if has_draw:
                    raise ValueError("已抽签赛事不能更改分组数")
                updates["group_count"] = _integer(data["group_count"],2,64,"分组数")
            if "options" in data:
                if not isinstance(data["options"],dict):
                    raise ValueError("options 必须为对象")
                if has_draw and set(data["options"])-{"scoring_mode","leader_user_ids","notes","reservations"}:
                    raise ValueError("已抽签后仅可调整记分权限或规则备注")
                opts = self._options(event["format"],{**event["options"],**data["options"]})
                count = db.execute("SELECT count(*) FROM tour_participants WHERE tournament_id=? AND status='active'",(tournament_id,)).fetchone()[0]
                if opts["capacity"] < count:
                    raise ValueError("容量不能小于已报名人数")
                updates["options"] = _dump(opts)
            if "staff" in data:
                updates["staff"] = _dump(self._validate_staff_change(db, event, data['staff'], actor, is_admin))
            if "match_schedule" in data:
                schedule_event = dict(event)
                if 'options' in updates:
                    schedule_event['options'] = _load(updates['options'])
                self._schedule(db,schedule_event,data["match_schedule"],actor)
            if updates:
                db.execute("UPDATE tour_events SET "+",".join(k+"=?" for k in updates)+" WHERE id=?",[*updates.values(),tournament_id])
            self._touch(db,tournament_id)
            from .availability import sync_event
            sync_event(db, self._event(db,tournament_id), actor, _now())
            self._audit(db,tournament_id,actor,"update",before,data)
        return self.get(tournament_id)

    def _schedule(self, db, event, entries, actor):
        if not isinstance(entries,list) or len(entries)>2000:
            raise ValueError("赛程设置不合法")
        for item in entries:
            if not isinstance(item,dict) or set(item)-{"match_id","scheduled_at","court"}:
                raise ValueError("赛程设置字段不合法")
            row = db.execute("SELECT * FROM tour_matches WHERE id=? AND tournament_id=?",(item.get("match_id"),event["id"])).fetchone()
            if not row:
                raise ValueError("比赛场次不存在于本赛事")
            if row["status"] in ("completed","bye"):
                raise ValueError("已结束或轮空场次不能调整时间")
            scheduled = _time(item["scheduled_at"]).isoformat() if item.get("scheduled_at") else None
            reservations = event['options'].get('reservations', [])
            if scheduled and event['kind']=='large' and reservations:
                moment=_time(scheduled).astimezone(TZ)
                if not any(r['date']==moment.date().isoformat() and r['start']<=moment.strftime('%H:%M')<r['end'] for r in reservations):
                    raise ValueError('比赛开始时间不在赛事预留时段内，请先调整场地占用')
            court = _clean(str(item.get("court","")),"球场",80,False)
            db.execute("UPDATE tour_matches SET scheduled_at=?,court=?,updated_at=? WHERE id=?",(scheduled,court,_now().isoformat(),row["id"]))
        # Only exact start-time conflicts can be established without duration.
        # Validate the complete final batch, allowing atomic time/court swaps.
        conflict = db.execute("""SELECT a.number,b.number FROM tour_matches a
            JOIN tour_events ea ON ea.id=a.tournament_id
            JOIN tour_matches b ON a.id<b.id AND a.scheduled_at=b.scheduled_at AND a.court=b.court
            JOIN tour_events eb ON eb.id=b.tournament_id
            WHERE ea.venue_id=? AND eb.venue_id=ea.venue_id AND a.scheduled_at IS NOT NULL
              AND ea.status NOT IN ('completed','archived','cancelled') AND eb.status NOT IN ('completed','archived','cancelled')
              AND a.status NOT IN ('completed','bye') AND b.status NOT IN ('completed','bye')
            LIMIT 1""",(event["venue_id"],)).fetchone()
        if conflict:
            raise ValueError("排期冲突：同一场地、同一球场名称、同一开始时间已有另一场比赛；请调整时间或球场")

    def _registration_allowed(self, event, now):
        if event["status"] != "registration":
            raise ValueError("此赛事当前不在报名阶段")
        if now < _time(event["registration_open"]):
            raise ValueError("此赛事报名尚未开始")
        if now >= _time(event["registration_close"]):
            raise ValueError("此赛事报名已结束")

    def enroll(self, tournament_id, user_id, name, guests=0, now=None, profile=None, eligibility_guard=None, guest_names=None):
        uid = _clean(str(user_id),"用户ID",80)
        name = _clean(name,"姓名",80)
        if name == uid:
            raise ValueError("请先完善真实姓名")
        profile = profile or {}
        guests = _integer(guests,0,20,"同行人数")
        if guest_names is not None:
            if not isinstance(guest_names, list) or len(guest_names) != guests or any(not isinstance(value, str) for value in guest_names):
                raise ValueError('同行姓名列表须与同行人数一致')
            guest_names = [_clean(value, '同行姓名', 80) for value in guest_names]
            if len({value.casefold() for value in guest_names}) != len(guest_names):
                raise ValueError('同行姓名不能重复')
        moment = _time(now)
        with self._transaction() as db:
            event = self._event(db,tournament_id)
            self._registration_allowed(event,moment)
            if eligibility_guard:
                profile = eligibility_guard(db, event) or profile
                name = _clean(profile.get("name", name), "姓名", 80)
            require_person(profile, event['options']['doubles_category'])
            if guests and event['options']['doubles_category'] != 'open':
                raise ValueError('性别限制赛事的参赛者须独立注册并补齐性别，不支持未认证同行代报')
            bookings = list(db.execute('SELECT * FROM tour_participants WHERE tournament_id=? AND user_id=?', (tournament_id, uid)))
            active = [row for row in bookings if row['status'] == 'active']
            current = len(active)
            existing_guests = [row for row in active if row['guest_index'] > 0]
            own_active = any(row['guest_index'] == 0 for row in active)
            if len(existing_guests) + guests > 20:
                raise ValueError('同一赛事的有效同行人员最多20人，请先取消不参加的同行人员')
            if guest_names and name.casefold() in {value.casefold() for value in guest_names}:
                raise ValueError('同行姓名不能与本人姓名相同')
            previous_index = max((row['guest_index'] for row in bookings), default=0)
            names = {row['name'].casefold() for row in existing_guests}
            additions = []
            for offset in range(guests):
                index = previous_index + offset + 1
                display = guest_names[offset] if guest_names is not None else f'{name}+{index}'
                if display.casefold() in names:
                    raise ValueError(f'同行人员“{display}”已由您报名，请勿重复代报')
                names.add(display.casefold())
                additions.append((index, display))
            total = db.execute("SELECT count(*) FROM tour_participants WHERE tournament_id=? AND status='active'",(tournament_id,)).fetchone()[0]
            if total + guests + int(not own_active) > event["options"]["capacity"]:
                raise ValueError("此赛事报名名额不足")
            for index, display in [(0, name), *additions]:
                pid = f"{uid}:{index}"
                db.execute("""INSERT INTO tour_participants(tournament_id,id,user_id,name,guest_index,registered_at,level,group_ids,gender,is_proxy)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tournament_id,id) DO UPDATE SET name=excluded.name,
                    level=excluded.level,group_ids=excluded.group_ids,gender=excluded.gender,is_proxy=excluded.is_proxy,
                    status='active',cancelled_at=NULL,registered_at=CASE WHEN tour_participants.status='active'
                    THEN tour_participants.registered_at ELSE excluded.registered_at END""",
                    (tournament_id,pid,uid,display,index,moment.isoformat(),level(profile) if index == 0 else 0,_dump(profile.get("group_ids", []) if index == 0 else []),normalize_gender(profile.get("gender")) if index == 0 else "unknown",int(index > 0)))
            self._touch(db,tournament_id)
            self._audit(db,tournament_id,uid,"enroll",{"count":current},{"count":current + guests + int(not own_active),"added_guests":guests,"name":name})
        return self.get(tournament_id)

    def cancel(self, tournament_id, user_id, now=None, eligibility_guard=None):
        moment = _time(now)
        with self._transaction() as db:
            event = self._event(db,tournament_id)
            self._registration_allowed(event,moment)
            if eligibility_guard:
                eligibility_guard(db, event)
            count = db.execute("UPDATE tour_participants SET status='cancelled',cancelled_at=? WHERE tournament_id=? AND user_id=? AND guest_index=0 AND status='active'",
                               (moment.isoformat(),tournament_id,str(user_id))).rowcount
            self._touch(db,tournament_id)
            self._audit(db,tournament_id,user_id,"cancel",{"active":count},{"active":0})
        return self.get(tournament_id)

    def cancel_guests(self, tournament_id, user_id, guest_names, now=None, eligibility_guard=None):
        uid = _clean(str(user_id), '用户ID', 80)
        if not isinstance(guest_names, list) or not 1 <= len(guest_names) <= 20 or any(not isinstance(value, str) for value in guest_names):
            raise ValueError('请提供1至20位要取消的同行姓名')
        names = [_clean(value, '同行姓名', 100) for value in guest_names]
        if len({value.casefold() for value in names}) != len(names):
            raise ValueError('取消名单中的同行姓名不能重复')
        moment = _time(now)
        with self._transaction() as db:
            event = self._event(db, tournament_id)
            self._registration_allowed(event, moment)
            if eligibility_guard:
                eligibility_guard(db, event)
            candidates = list(db.execute("SELECT * FROM tour_participants WHERE tournament_id=? AND user_id=? AND guest_index>0 AND status='active'", (tournament_id, uid)))
            targets = []
            for name in names:
                matches = [row for row in candidates if name.casefold() == row['name'].casefold()]
                if len(matches) != 1:
                    raise ValueError(f'未找到本人代报的唯一有效人员“{name}”，请核对本赛事名单')
                if matches[0]['id'] in targets:
                    raise ValueError('取消名单中重复指定了同一位同行人员')
                targets.append(matches[0]['id'])
            db.executemany("UPDATE tour_participants SET status='cancelled',cancelled_at=? WHERE tournament_id=? AND id=?", [(moment.isoformat(), tournament_id, target) for target in targets])
            self._touch(db, tournament_id)
            self._audit(db, tournament_id, uid, 'cancel_guests', {'active': len(targets)}, {'active': 0, 'participant_ids': targets})
        return self.get(tournament_id)

    def cancel_entry(self, tournament_id, participant_id, actor, is_admin=False, now=None, eligibility_guard=None):
        if not is_admin:
            raise PermissionError('需要赛事管理权限')
        participant_id = _clean(participant_id, '报名条目标识', 100)
        moment = _time(now)
        with self._transaction() as db:
            event = self._event(db, tournament_id)
            self._registration_allowed(event, moment)
            if eligibility_guard:
                eligibility_guard(db, event)
            row = db.execute('SELECT * FROM tour_participants WHERE tournament_id=? AND id=?', (tournament_id, participant_id)).fetchone()
            if not row:
                raise ValueError('赛事报名条目不存在')
            if row['status'] == 'active':
                db.execute("UPDATE tour_participants SET status='cancelled',cancelled_at=? WHERE tournament_id=? AND id=?", (moment.isoformat(), tournament_id, participant_id))
                self._touch(db, tournament_id)
                self._audit(db, tournament_id, actor, 'cancel_entry', {'participant_id': participant_id, 'user_id': row['user_id'], 'status': 'active'}, {'status': 'cancelled'})
        return self.get(tournament_id)

    def _insert_matches(self, db, event_id, matches):
        for item in matches:
            db.execute("""INSERT INTO tour_matches(tournament_id,number,stage,round,group_id,side_a,side_b,a_source,b_source,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(event_id,item["number"],item["stage"],item["round"],item.get("group_id",""),
                _dump(item.get("side_a")),_dump(item.get("side_b")),_dump(item.get("a_source")),_dump(item.get("b_source")),_now().isoformat()))

    def _draw(self, db, event, actor):
        if db.execute("SELECT 1 FROM tour_matches WHERE tournament_id=?",(event["id"],)).fetchone():
            return  # Persisted draw is immutable; retries never reshuffle.
        if event["status"] not in ("closed","draft"):
            raise ValueError("请先结束报名再抽签")
        people = [_participant(row) for row in db.execute("SELECT * FROM tour_participants WHERE tournament_id=? AND status='active' ORDER BY registered_at,id",(event["id"],))]
        validate_roster(people, event["options"], event["format"])
        module = get_format(event["format"])
        discipline = event["options"].get("discipline", "singles" if event["options"].get("team_size") == 1 else "doubles")
        available = next(item for item in metadata(discipline, len(people), {**event["options"], "group_count": event["group_count"]}) if item["id"] == event["format"])
        if not available["available"]:
            raise ValueError(available["reason"])
        plan = module.build(people,{**event["options"],"group_count":event["group_count"]},event["seed"])
        plan["assignment"] = {"strategy": event["options"].get("assignment_strategy", "random"), "seed": event["seed"],
                              "discipline": discipline, "doubles_category": event["options"].get("doubles_category", "open"), "genders": {p["id"]: p["gender"] for p in people}, "levels": {p["id"]: p["level"] for p in people}, "version": 1}
        if len(plan["matches"]) > 10000:
            raise ValueError("预计比赛超过10000场，请减少人数或分组")
        self._insert_matches(db,event["id"],plan["matches"])
        db.execute("UPDATE tour_events SET groups_json=?,plan_json=?,status='closed' WHERE id=?",
                   (_dump(plan["groups"]),_dump({k:v for k,v in plan.items() if k not in ("groups","matches")}),event["id"]))
        self._refresh(db,event["id"])
        self._audit(db,event["id"],actor,"draw",None,{"seed":event["seed"],"assignment": plan["assignment"],"groups":plan["groups"],"matches":len(plan["matches"])})

    def transition(self, tournament_id, action, actor, is_admin, eligibility_guard=None):
        if action not in {"open_registration","close_registration","draw","start","pause","resume","advance","archive","close"}:
            raise ValueError("未知赛事操作")
        with self._transaction() as db:
            event = self._event(db,tournament_id)
            if not is_admin:
                self._approved(db, event, [actor])
            self._manage(event,actor,is_admin,action)
            if action in ('draw', 'start') and eligibility_guard and not db.execute('SELECT 1 FROM tour_matches WHERE tournament_id=?', (tournament_id,)).fetchone():
                eligibility_guard(db, event)
            old = event["status"]
            if old == "archived":
                if action == "archive":
                    return self.get(tournament_id)
                raise ValueError("赛事已归档")
            if old == 'cancelled' and action not in ('close', 'archive'):
                raise ValueError('赛事已关闭，不能恢复或修改比赛')
            if action == "open_registration":
                if old == "registration":
                    return self.get(tournament_id)
                if old != "draft" or _now() >= _time(event["registration_close"]):
                    raise ValueError("仅未过期的草稿赛事可以开放报名")
                db.execute("UPDATE tour_events SET status='registration' WHERE id=?",(tournament_id,))
            elif action == "close_registration":
                if old not in ("registration","closed"):
                    raise ValueError("此赛事不在报名阶段")
                db.execute("UPDATE tour_events SET status='closed' WHERE id=?",(tournament_id,))
                self._close_notification(db,event)
            elif action == "draw":
                if old not in ("closed","running","paused"):
                    raise ValueError("请先关闭报名")
                self._draw(db,event,actor)
            elif action == "start":
                if old not in ("closed","running"):
                    raise ValueError("请先关闭报名；已暂停赛事请使用继续比赛")
                if old == "closed":
                    self._draw(db,event,actor)
                db.execute("UPDATE tour_events SET status='running' WHERE id=?",(tournament_id,))
            elif action == "pause":
                if old not in ("running","paused"):
                    raise ValueError("只有正在比赛的赛事可以暂停")
                db.execute("UPDATE tour_events SET status='paused' WHERE id=?",(tournament_id,))
            elif action == "resume":
                if old not in ("running","paused"):
                    raise ValueError("只有进行中或暂停的赛事可以继续")
                db.execute("UPDATE tour_events SET status='running' WHERE id=?",(tournament_id,))
            elif action == "advance":
                if old != "running" or event["format"] != "group_knockout":
                    raise ValueError("仅分组赛全部完成后可以执行晋级")
                self._advance_groups(db,event,allow_tie_draw=True,actor=actor)
            elif action == 'close':
                if old == 'completed':
                    raise ValueError('赛事已完赛，请直接归档')
                db.execute("UPDATE tour_events SET status='cancelled',champion_json='[]' WHERE id=?", (tournament_id,))
            elif action == "archive":
                if old not in ("completed","draft","closed","paused","cancelled"):
                    raise ValueError("请先暂停比赛或关闭报名后再归档")
                db.execute("UPDATE tour_events SET status='archived' WHERE id=?",(tournament_id,))
            self._touch(db,tournament_id)
            from .availability import sync_event
            sync_event(db,self._event(db,tournament_id),actor,_now())
            self._audit(db,tournament_id,actor,action,{"status":old},{"status":self._event(db,tournament_id)["status"]})
        return self.get(tournament_id)

    def _refresh(self, db, event_id):
        """Resolve dependency DAG, including vacancies, without inventing bye scores."""
        matches = self._matches(db,event_id)
        by_number = {m["number"]:m for m in matches}
        changed = True
        while changed:
            changed = False
            for m in matches:
                if m["status"] == "completed":
                    continue
                resolved = True
                for side in ("a","b"):
                    src = m[f"{side}_source"]
                    if not src:
                        continue
                    parent = by_number[src["match"]]
                    if parent["status"] not in ("completed","bye"):
                        resolved = False
                        continue
                    winner = parent["winner"]
                    if src["result"] == "winner":
                        value = next((parent[s] for s in ("side_a","side_b") if parent[s] and parent[s]["id"] == winner),None)
                    else:
                        value = next((parent[s] for s in ("side_a","side_b") if parent[s] and parent[s]["id"] != winner),None) if parent["status"] == "completed" else None
                    m[f"side_{side}"] = value
                new_status, new_winner = "pending", None
                if resolved:
                    if m["side_a"] and m["side_b"]:
                        new_status = "ready"
                    else:
                        new_status = "bye"
                        present = m["side_a"] or m["side_b"]
                        new_winner = present["id"] if present else None
                current = db.execute("SELECT side_a,side_b,status,winner FROM tour_matches WHERE id=?",(m["id"],)).fetchone()
                values = (_dump(m["side_a"]),_dump(m["side_b"]),new_status,new_winner)
                if tuple(current) != values:
                    db.execute("UPDATE tour_matches SET side_a=?,side_b=?,status=?,winner=? WHERE id=?",(*values,m["id"]))
                    changed = True
                m["status"], m["winner"] = new_status,new_winner

    @staticmethod
    def _validate_scores(scores, opts):
        if not isinstance(scores,list) or not 1 <= len(scores) <= opts["best_of"]:
            raise ValueError("比分必须为完整的各局比分数组")
        won = [0,0]
        needed = opts["best_of"]//2+1
        for pair in scores:
            if max(won) >= needed:
                raise ValueError("胜负已定后不能添加额外局")
            if not isinstance(pair,(list,tuple)) or len(pair) != 2:
                raise ValueError("每局必须是双方两个整数分数")
            a,b = pair
            _integer(a,0,opts["cap"],"比分")
            _integer(b,0,opts["cap"],"比分")
            high, low = max(a,b),min(a,b)
            if a == b or high < opts["points"]:
                raise ValueError("此局尚未达到获胜分数")
            if high == opts["points"]:
                valid = high-low >= opts["win_by"] or high == opts["cap"]
            elif high < opts["cap"]:
                valid = high-low == opts["win_by"]
            else:
                valid = high-low in range(1,opts["win_by"]+1)
            if not valid:
                raise ValueError("比分不符合目标分、净胜或封顶规则（应在胜负确定时结束）")
            won[0 if a>b else 1] += 1
        if max(won) != needed:
            raise ValueError("尚未完成本场所需胜局")
        return 0 if won[0]>won[1] else 1

    def score(self, tournament_id, match_id, scores, actor, is_admin, expected_version=None):
        actor = _clean(str(actor),"记分者ID",80)
        _integer(match_id,1,2**63-1,"比赛ID")
        if expected_version is not None:
            _integer(expected_version,0,2**31-1,"比分版本")
        with self._transaction() as db:
            event = self._event(db,tournament_id)
            if not is_admin:
                self._approved(db, event, [actor])
            self._can_score(event,actor,is_admin)
            can_correct = self.permissions(event, actor, is_admin)['correct_score']
            if event["status"] not in (("running","completed") if can_correct else ("running",)):
                raise ValueError("请先开始或恢复比赛后记分")
            matches = self._matches(db,tournament_id)
            target = next((m for m in matches if m["id"] == match_id),None)
            if target is None:
                raise ValueError("该场比赛不存在于本赛事")
            if expected_version is not None and expected_version != target["version"]:
                raise ValueError("比分版本已变化，请刷新后重试")
            if target["status"] not in ("ready","completed"):
                raise ValueError("此场对手尚未确定或为轮空，不能记分")
            winner_index = self._validate_scores(scores,event["options"])
            winner = target["side_a" if winner_index == 0 else "side_b"]["id"]
            if target["status"] == "completed":
                if target["scores"] == scores:
                    return self.get(tournament_id)
                if not can_correct:
                    raise PermissionError("已有比分仅赛事组织者或管理员可以修正")
                if expected_version is None:
                    raise ValueError("修正已有比分必须提供 expected_version")
                self._prepare_correction(db,event,target,matches,winner)
            db.execute("UPDATE tour_matches SET scores=?,winner=?,status='completed',version=version+1,updated_at=? WHERE id=?",
                       (_dump(scores),winner,_now().isoformat(),match_id))
            self._audit(db,tournament_id,actor,"score_correction" if target["status"] == "completed" else "score",
                        {"match_id":match_id,"number":target["number"],"scores":target["scores"],"version":target["version"]},
                        {"scores":scores,"winner":winner,"version":target["version"]+1})
            db.execute("UPDATE tour_events SET status='running',champion_json='[]' WHERE id=?",(tournament_id,))
            self._refresh(db,tournament_id)
            self._finish_or_advance(db,tournament_id)
            self._touch(db,tournament_id)
        return self.get(tournament_id)

    def _prepare_correction(self, db, event, target, matches, winner):
        # Group standings may change even when this match's winner does not.
        if event["format"] == "group_knockout" and target["stage"] == "groups":
            downstream = [m for m in matches if m["stage"] == "knockout"]
            if any(m["status"] == "completed" for m in downstream):
                raise ValueError("淘汰赛已记分，不能修改影响晋级的小组成绩；请保留原记录并由组织者处理申诉")
            if downstream:
                db.execute("DELETE FROM tour_matches WHERE tournament_id=? AND stage='knockout'",(event["id"],))
            return
        if winner == target["winner"]:
            return
        affected = {target["number"]}
        descendants = []
        changed = True
        while changed:
            changed = False
            for m in matches:
                if m["number"] in affected:
                    continue
                if any(m[s] and m[s]["match"] in affected for s in ("a_source","b_source")) or (target["stage"] == "grand_final" and m["stage"] == "reset_final"):
                    affected.add(m["number"])
                    descendants.append(m)
                    changed = True
        if any(m["status"] == "completed" for m in descendants):
            raise ValueError("后续比赛已有比分，不能更换晋级者；请先核实赛事申诉，避免覆盖历史")
        for m in descendants:
            if m["stage"] == "reset_final":
                db.execute("DELETE FROM tour_matches WHERE id=?",(m["id"],))
                plan = {**event["plan"],"final_number":target["number"]}
                db.execute("UPDATE tour_events SET plan_json=? WHERE id=?",(_dump(plan),event["id"]))
            else:
                db.execute("""UPDATE tour_matches SET side_a=CASE WHEN a_source='null' THEN side_a ELSE 'null' END,
                            side_b=CASE WHEN b_source='null' THEN side_b ELSE 'null' END,status='pending',winner=NULL,
                            version=version+1 WHERE id=?""",(m["id"],))

    def _standings(self, event, matches, groups_only=False):
        results = {}
        individual = event.get("plan",{}).get("ranking") == "individual"
        for group in event["groups"]:
            for entry in group["entrants"]:
                results[(group["id"],entry["id"])] = {**entry,"group_id":group["id"],"played":0,"won":0,"lost":0,
                    "points_for":0,"points_against":0,"games_for":0,"games_against":0}
        person_entry = {m:e for g in event["groups"] for e in g["entrants"] for m in e["members"]}
        for item in matches:
            if item["status"] != "completed" or (groups_only and item["stage"] != "groups"):
                continue
            # Group tournament standings intentionally preserve the group phase only.
            if event["format"] == "group_knockout" and item["stage"] != "groups":
                continue
            for side, other in (("side_a","side_b"),("side_b","side_a")):
                entry = item[side]
                entries = [person_entry[m] for m in entry["members"]] if individual else [entry]
                index = 0 if side == "side_a" else 1
                for competitor in entries:
                    key = (item["group_id"] or "A",competitor["id"])
                    if key not in results:
                        continue
                    row = results[key]
                    won = item["winner"] == entry["id"]
                    row["played"] += 1
                    row["won"] += int(won)
                    row["lost"] += int(not won)
                    row["points_for"] += sum(pair[index] for pair in item["scores"])
                    row["points_against"] += sum(pair[1-index] for pair in item["scores"])
                    row["games_for"] += sum(pair[index]>pair[1-index] for pair in item["scores"])
                    row["games_against"] += sum(pair[index]<pair[1-index] for pair in item["scores"])
        for row in results.values():
            row["point_diff"] = row["points_for"]-row["points_against"]
            row["game_diff"] = row["games_for"]-row["games_against"]
            row["win_rate"] = round(row["won"]/row["played"],4) if row["played"] else 0
        output = []
        for group_id in dict.fromkeys(key[0] for key in results):
            rows = [r for (g,_),r in results.items() if g == group_id]
            def rating(row):
                first = Fraction(row["won"],max(1,row["played"])) if event["format"] == "doubles_rotation" else row["won"]
                return (first,row["game_diff"],row["point_diff"])
            rows.sort(key=lambda r:(rating(r),r["id"]),reverse=True)
            previous, rank = None,0
            for i,row in enumerate(rows,1):
                key = rating(row)
                if key != previous:
                    rank = i
                row["rank"] = rank
                previous = key
            output.extend(rows)
        return output

    def _advance_groups(self, db, event, allow_tie_draw=False, actor="system"):
        matches = self._matches(db,event["id"])
        if any(m["stage"] == "knockout" for m in matches):
            return True
        if not matches or any(m["status"] != "completed" for m in matches):
            if allow_tie_draw:
                raise ValueError("小组赛尚未全部完成")
            return False
        rows = self._standings(event,matches,True)
        count = event["options"]["advance_per_group"]
        selected, tie_records = {}, []
        rng = random.Random(event["seed"]+991)
        for group in event["groups"]:
            pool = [r for r in rows if r["group_id"] == group["id"]]
            border = pool[count-1]
            tied = [r for r in pool if (r["won"],r["game_diff"],r["point_diff"]) == (border["won"],border["game_diff"],border["point_diff"])]
            better = [r for r in pool if r["rank"] < border["rank"]]
            if len(better)+len(tied)>count:
                if not allow_tie_draw:
                    plan = {**event["plan"],"awaiting_tie_draw":True}
                    db.execute("UPDATE tour_events SET plan_json=? WHERE id=?",(_dump(plan),event["id"]))
                    return False
                rng.shuffle(tied)
                tie_records.append({"group":group["id"],"candidates":[r["id"] for r in tied],"selected":[r["id"] for r in tied[:count-len(better)]]})
                pool = better+tied
            selected[group["id"]] = [{"id":r["id"],"name":r["name"],"members":r["members"]} for r in pool[:count]]
        # Alternate group ordering at successive qualification ranks.
        entrants = []
        labels = list(selected)
        for rank in range(count):
            order = labels if rank%2 == 0 else list(reversed(labels))
            entrants.extend(selected[label][rank] for label in order)
        # Deterministic cross-group draw. Standard seeded slot order alone can
        # accidentally pair A1 with A2; reject such first-round placements.
        entry_group = {entry["id"]:label for label,entries in selected.items() for entry in entries}
        for _ in range(2000):
            bracket, rounds = knockout(entrants,offset=max(m["number"] for m in matches))
            if all(not m["side_a"] or not m["side_b"] or entry_group[m["side_a"]["id"]] != entry_group[m["side_b"]["id"]]
                   for m in bracket if m["round"] == 1):
                break
            rng.shuffle(entrants)
        else:
            raise ValueError("无法生成避免同组首轮相遇的签表，请调整晋级数")
        self._insert_matches(db,event["id"],bracket)
        plan = {**event["plan"],"final_number":rounds[-1][0],"awaiting_tie_draw":False}
        db.execute("UPDATE tour_events SET plan_json=? WHERE id=?",(_dump(plan),event["id"]))
        self._refresh(db,event["id"])
        self._audit(db,event["id"],actor,"group_qualification",None,{"qualified":entrants,"tie_draw":tie_records})
        return True

    def _finish_or_advance(self, db, event_id):
        event = self._event(db,event_id)
        matches = self._matches(db,event_id)
        if event["format"] == "group_knockout":
            self._advance_groups(db,event)
            event = self._event(db,event_id)
            matches = self._matches(db,event_id)
            if not any(m["stage"] == "knockout" for m in matches):
                return
        if event["format"] == "double_elimination":
            grand = next(m for m in matches if m["stage"] == "grand_final")
            resets = [m for m in matches if m["stage"] == "reset_final"]
            if grand["status"] == "completed" and grand["winner"] == grand["side_b"]["id"] and not resets:
                reset = match(grand["side_a"],grand["side_b"],stage="reset_final",round=2)
                reset["number"] = max(m["number"] for m in matches)+1
                self._insert_matches(db,event_id,[reset])
                plan = {**event["plan"],"final_number":reset["number"]}
                db.execute("UPDATE tour_events SET plan_json=? WHERE id=?",(_dump(plan),event_id))
                self._refresh(db,event_id)
                self._audit(db,event_id,"system","reset_final",None,{"number":reset["number"]})
                return
        if not matches or any(m["status"] not in ("completed","bye") for m in matches):
            return
        if event["format"] in ("single_elimination","double_elimination","group_knockout"):
            finals = [m for m in matches if m["stage"] in ("knockout","grand_final","reset_final")]
            final = max(finals,key=lambda m:m["number"])
            champion = [s for s in (final["side_a"],final["side_b"]) if s and s["id"] == final["winner"]]
        else:
            standings = self._standings(event,matches)
            champion = [{"id":r["id"],"name":r["name"],"members":r["members"]} for r in standings if r["rank"] == 1]
        db.execute("UPDATE tour_events SET status='completed',champion_json=? WHERE id=?",(_dump(champion),event_id))
        self._audit(db,event_id,"system","completed",None,{"champion":champion})

    def resume_candidates(self, venue_id, chat_id, participants):
        current = {str(p.get("id",p.get("user_id",""))) if isinstance(p,dict) else str(p) for p in participants}
        results = []
        for item in self.list(venue_id=venue_id,chat_id=str(chat_id)):
            if item["status"] not in ("running","paused"):
                continue
            event = self.get(item["id"])
            roster = {p["id"] for p in event["participants"] if p["status"] == "active"}
            if not current.intersection(roster):
                continue
            union = current|roster
            item["overlap"] = len(current&roster)
            item["overlap_ratio"] = len(current&roster)/len(union) if union else 0
            item["same_date"] = item["date"] == _now().date().isoformat()
            item["missing_participant_ids"] = sorted(roster-current)
            item["new_participant_ids"] = sorted(current-roster)
            results.append(item)
        return sorted(results,key=lambda r:(r["overlap_ratio"],r["overlap"],r["same_date"],r["updated_at"]),reverse=True)

    def _close_notification(self, db, event):
        count = db.execute("SELECT count(*) FROM tour_participants WHERE tournament_id=? AND status='active'",(event["id"],)).fetchone()[0]
        text = f"🏸 **{_safe_md(event['title'])} · 报名结束**\n\n场地 {event['venue_id']} · 已报名 **{count} 人**\n\n请等待组织者公布分组及赛程。"
        db.execute("INSERT OR IGNORE INTO tour_notifications(key,tournament_id,chat_id,text,created_at) VALUES(?,?,?,?,?)",
                   (f"tournament:{event['id']}:registration_closed",event["id"],event["chat_id"],text,_now().isoformat()))

    def due_notifications(self, now):
        moment = _time(now)
        with self._transaction() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tour_events WHERE status='registration'")]
            for event_id in ids:
                event = self._event(db,event_id)
                if moment >= _time(event["registration_close"]):
                    db.execute("UPDATE tour_events SET status='closed' WHERE id=?",(event_id,))
                    self._touch(db,event_id)
                    self._audit(db,event_id,"system","auto_close_registration",{"status":"registration"},{"status":"closed"})
                    self._close_notification(db,event)
            # Durable notification rows deliberately repeat. The caller atomically
            # inserts them into its outbox using key UNIQUE before sending.
            return [dict(row) for row in db.execute("SELECT key,text,tournament_id,chat_id FROM tour_notifications ORDER BY created_at")]

    def render(self, tournament_id, section="schedule", actor=None):
        """Markdown template used equally by admin previews and bot notifications."""
        event = self.get(tournament_id)
        header = f"🏸 **{_safe_md(event['title'])}**\n\n场地 {event['venue_id']} · {event['date']} · {get_format(event['format']).META['name']}"
        opts = event["options"]
        discipline = opts.get("discipline", "singles" if opts.get("team_size") == 1 else "doubles")
        header += " · " + ("单打" if discipline == "singles" else CATEGORIES[opts.get("doubles_category", "open")])
        session = opts.get('session_snapshot') or {}
        if session:
            header += '\n' + ' · '.join(_safe_md(session.get(key, '')) for key in ('venue_name', 'name'))
            header += ' ' + _safe_md(session.get('start', '')) + '—' + _safe_md(session.get('end', ''))
        header += "\n组织者：" + _safe_md(opts.get('organizer_name') or '管理员')
        header += f"\n{opts['best_of']}局{opts['best_of']//2+1}胜 · {opts['points']}分 · 净胜{opts['win_by']}分 · {opts['cap']}分封顶"
        organization = event['organization']
        if section in ('brief', 'rules'):
            parts = [header, '**赛事说明**\n' + _safe_md(organization['description'] or '请关注负责人后续通知。'),
                     '**比赛规则**\n' + _safe_md(organization['rules_text'] or event['rules'])]
            if organization['meeting_instructions']:
                parts.append('**集合说明**\n' + _safe_md(organization['meeting_instructions']))
            return '\n\n'.join(parts)
        if organization['meeting_instructions'] and section in ('schedule', 'mine', 'registration'):
            header += '\n\n📍 **集合说明**\n' + _safe_md(organization['meeting_instructions'])
        if opts.get('selected_substitute_names'):
            header += "\n\n替补补齐参赛：\n\n" + _table(['姓名', '来源'], [[name, '替补'] for name in opts['selected_substitute_names']])
        if section == "registration":
            from .messages import registration_table
            sponsors = {p['user_id']: p['name'] for p in event['participants']
                        if not p.get('guest_index') and not p.get('is_proxy')}
            rows = []
            for participant in event['participants']:
                if participant['status'] != 'active':
                    continue
                row = dict(participant)
                row['sponsor_name'] = row.get('sponsor_name') or sponsors.get(row['user_id'], '')
                row['category'] = row.get('source_category') or '正式'
                if row.get('is_proxy') or row.get('guest_index'):
                    row.update(gender='unknown', level=None)
                rows.append(row)
            return header+f"\n\n🟢 **报名窗口**\n{event['registration_open']} 至 {event['registration_close']}\n\n发送 **{_safe_md(event['keyword'])}报名** 参加。\n已报名：**{event['participant_count']} 人**\n\n" + registration_table(rows)
        if section == "closed":
            return header+f"\n\n🟠 **报名已结束** · 共 {event['participant_count']} 人\n请等待分组及赛程。"
        if section == "groups":
            rows = []
            for group in event["groups"]:
                for index, entry in enumerate(group["entrants"], 1):
                    rows.append([group['name'], index, entry['name']])
            body = "\n\n🟦 **分组 / 队伍**\n\n" + _table(["组别", "队伍", "成员（等级）"], rows)
            if event["format"] == "five_team":
                names = {p["id"]: player_name(p) for p in event["participants"]}
                rows = [[group['name'], index, position, names.get(pid, "待完善姓名")]
                        for group in event['groups'] for index, entry in enumerate(group['entrants'], 1)
                        for position, pid in enumerate(entry['members'], 1)]
                body += "\n\n" + _table(["组别", "队伍", "队内编号", "成员（等级）"], rows)
                body += f"\n\n轮换组合：12 → 23 → 34 → 45 → 51；每段 {opts['relay_segment']} 分。"
            return header + body + "\n\n" + _safe_md(event["rules"])
        if section == "results":
            champion = "、".join(_safe_md(c["name"]) for c in event["champion"])
            rows = [[row['group_id'], row['rank'], row['name'], row['played'], row['won'], row['lost'], f"{row['point_diff']:+}"] for row in event['standings']]
            return header + f"\n\n🏆 **{'获胜者：'+champion if champion else '当前积分'}**\n\n" + _table(["组别", "排名", "成员（等级）", "场次", "胜", "负", "净胜分"], rows)
        if section not in ("schedule", "mine"):
            raise ValueError("未知赛事消息模板")
        own_ids = {p['id'] for p in event['participants'] if p['user_id'] == str(actor)}
        rows = []
        for item in event["matches"]:
            if section == 'mine' and not any(own_ids.intersection((item.get(side) or {}).get('members', [])) for side in ('side_a', 'side_b')):
                continue
            icon = "✅" if item["status"] == "completed" else "⏭️" if item["status"] == "bye" else "🔵" if item["status"] == "ready" else "⏳"
            def scored_side(key, index):
                name = item[key].replace(' / ', '，')
                if not item['scores']:
                    return name
                score = str(item['scores'][0][index]) if len(item['scores']) == 1 else '，'.join(f'第{i}局 {pair[index]}' for i, pair in enumerate(item['scores'], 1))
                return name + f'（{score}）'
            contest = scored_side('side_a_name', 0) + ' VS ' + scored_side('side_b_name', 1)
            schedule = f"{item['scheduled_at'] or ''} {item['court']}".strip() or "待安排"
            rows.append([f"第{item['number']}场", contest, icon, schedule])
        lines = ["\n\n📋 **" + ('我的赛程' if section == 'mine' else '对战表') + "**\n\n" + _table(["场次", "对战及比分", "状态", "时间 / 球场"], rows)]
        if section == 'mine' and not rows:
            lines.append('\n当前没有已确定由您参加的场次。')
        lines.append(f"\n记分：**{_safe_md(event['title'])}，第1场 21:19**；发送 **{_safe_md(event['title'])}，我的赛程** 查看本人安排。")
        if event.get("plan",{}).get("awaiting_tie_draw"):
            lines.append("\n🟠 **小组出线同分待定**：请组织者在管理页点击‘晋级’，执行有记录的同分抽签。")
        return header+"\n".join(lines)
