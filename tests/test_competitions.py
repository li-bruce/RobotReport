import concurrent.futures
import json
import random
import sqlite3
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.competitions import TournamentService
from app.formats import get_format

TZ = ZoneInfo("Asia/Shanghai")

def people(n):
    return [{"id":f"u{i}:0","user_id":f"u{i}","name":f"球友{i}"} for i in range(n)]


class TournamentTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(__file__).resolve().parent / ".tmp-competitions"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.path = Path(self.tmp.name)/"business.sqlite3"
        self.service = TournamentService(self.path)

    def tearDown(self):
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(self.scratch.resolve()))
        self.tmp.cleanup()

    def daily(self, format_id="round_robin", n=4, **fields):
        if format_id not in ("five_rotation", "five_team", "doubles_rotation"):
            fields['options'] = {'discipline': 'singles', **fields.get('options', {})}
        return self.service.create({"title":"测试赛事","kind":"daily","format":format_id,
            "venue_id":1,"chat_id":"chat1","participants":people(n),**fields},"u0",False)

    def large(self, **fields):
        now = datetime.now(TZ)
        return self.service.create({"title":"大型活动","keyword":"秋季杯","kind":"large","format":"single_elimination",
            "venue_id":1,"chat_id":"chat1","registration_open":(now-timedelta(days=1)).isoformat(),
            "registration_close":(now+timedelta(days=1)).isoformat(),**fields},"admin",True)

    def finish(self,event,winner_b=False,reset=False):
        event_id = event["id"]
        for _ in range(2000):
            event = self.service.get(event_id)
            if event["status"] == "completed":
                return event
            ready = [m for m in event["matches"] if m["status"] == "ready"]
            if not ready and event["plan"].get("awaiting_tie_draw"):
                self.service.transition(event_id,"advance","admin",True)
                continue
            self.assertTrue(ready, f"赛事卡住: {event['format']} {event['plan']}")
            target = ready[0]
            reverse = winner_b or (reset and target["stage"] == "grand_final")
            points = event["options"]["points"]
            scores = [[0,points] if reverse else [points,0]] * (event["options"]["best_of"]//2+1)
            self.service.score(event_id,target["id"],scores,"u0",event["kind"] == "large")
        self.fail("赛事未在合理场数内完成")

    def test_seven_formats_and_cli_contract(self):
        self.assertGreaterEqual(len(self.service.formats()),7)
        for key,n in (("round_robin",4),("five_rotation",5),("five_team",10),("single_elimination",7),
                      ("double_elimination",5),("group_knockout",8),("doubles_rotation",7)):
            with self.subTest(format=key):
                module = get_format(key)
                options = {} if key in ('five_rotation', 'five_team', 'doubles_rotation') else {'discipline': 'singles'}
                first = module.build(people(n),options,42)
                self.assertEqual(first,module.build(people(n),options,42))
                self.assertTrue(first["matches"])
                event = self.finish(self.daily(key,n))
                self.assertTrue(event["champion"])
                self.assertEqual(event["status"],"completed")

    def test_each_format_runs_as_independent_python_module(self):
        for key,n in (("round_robin",4),("five_rotation",5),("five_team",10),("single_elimination",7),
                      ("double_elimination",5),("group_knockout",8),("doubles_rotation",7)):
            result = subprocess.run([sys.executable,"-m",f"app.formats.{key}"],
                input=json.dumps({"participants":people(n),"options":{} if key in ('five_rotation', 'five_team', 'doubles_rotation') else {'discipline': 'singles'},"seed":42}),
                cwd=Path(__file__).resolve().parent.parent,text=True,encoding="utf-8",capture_output=True,
                env={**os.environ,"PYTHONUTF8":"1"},timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)["matches"])

    def test_five_rotation_balances_every_partner_and_rest(self):
        for seed in range(15):
            plan = get_format("five_rotation").build(people(5),{},seed)
            appearances,partners,opponents = Counter(),Counter(),Counter()
            for m in plan["matches"]:
                a,b = m["side_a"]["members"],m["side_b"]["members"]
                self.assertEqual(len(set(a+b)),4)
                appearances.update(a+b)
                partners.update([tuple(sorted(a)),tuple(sorted(b))])
                opponents.update(tuple(sorted((x,y))) for x in a for y in b)
            self.assertEqual(set(appearances.values()),{4})
            self.assertEqual(len(partners),10)
            self.assertEqual(set(partners.values()),{1})
            self.assertEqual(set(opponents.values()),{2})
        for invalid in (4,6,10):
            with self.assertRaises(ValueError):
                self.daily("five_rotation",invalid)

    def test_round_robin_has_exact_pairs_and_no_double_round(self):
        for n in range(2,14):
            plan = get_format("round_robin").build(people(n),{'discipline': 'singles'},7)
            self.assertEqual(len(plan["matches"]),n*(n-1)//2)
            pairs,round_players = set(),{}
            for m in plan["matches"]:
                ids = [m["side_a"]["id"],m["side_b"]["id"]]
                pairs.add(tuple(sorted(ids)))
                used = round_players.setdefault(m["round"],set())
                self.assertFalse(used.intersection(ids))
                used.update(ids)
            self.assertEqual(len(pairs),len(plan["matches"]))

    def test_single_elimination_byes_every_size(self):
        for n in range(2,18):
            with self.subTest(n=n):
                event = self.finish(self.daily("single_elimination",n))
                played = [m for m in event["matches"] if m["status"] == "completed"]
                self.assertEqual(len(played),n-1)
                losses = Counter((m["side_b"] if m["winner"] == m["side_a"]["id"] else m["side_a"])["id"] for m in played)
                self.assertEqual(len(event["champion"]),1)
                self.assertNotIn(event["champion"][0]["id"],losses)
                self.assertEqual(set(losses.values()),{1})
                self.assertTrue(all(m["scores"] == [] for m in event["matches"] if m["status"] == "bye"))

    def test_double_elimination_two_losses_and_reset_every_size(self):
        for n in range(2,14):
            for force_reset in (False,True):
                with self.subTest(n=n,reset=force_reset):
                    event = self.finish(self.daily("double_elimination",n),reset=force_reset)
                    played = [m for m in event["matches"] if m["status"] == "completed"]
                    self.assertEqual(len(played),2*n-1 if force_reset else 2*n-2)
                    losses = Counter((m["side_b"] if m["winner"] == m["side_a"]["id"] else m["side_a"])["id"] for m in played)
                    champion_id = event["champion"][0]["id"]
                    self.assertEqual(losses[champion_id],int(force_reset))
                    self.assertTrue(all(count==2 for uid,count in losses.items() if uid != champion_id))
                    resets = [m for m in played if m["stage"] == "reset_final"]
                    self.assertEqual(len(resets),int(force_reset))

    def test_double_elimination_random_winners(self):
        rng = random.Random(12)
        for n in (3,5,6,9,11):
            event = self.daily("double_elimination",n)
            while event["status"] != "completed":
                ready = [m for m in event["matches"] if m["status"] == "ready"]
                self.assertTrue(ready)
                target = rng.choice(ready)
                scores = [[21,17]] if rng.random()<0.5 else [[17,21]]
                event = self.service.score(event["id"],target["id"],scores,"u0",False)
                self.assertLess(len(event["matches"]),4*n)
            losses = Counter()
            for m in event["matches"]:
                if m["status"] == "completed":
                    loser = m["side_b"] if m["winner"] == m["side_a"]["id"] else m["side_a"]
                    losses[loser["id"]] += 1
            champion = event["champion"][0]["id"]
            self.assertLess(losses[champion],2)
            self.assertTrue(all(v==2 for k,v in losses.items() if k!=champion))

    def test_group_tie_requires_recorded_draw(self):
        event = self.daily("group_knockout",6,group_count=2)
        # Three-way cycle with identical game and point differences in each group.
        for group in event["groups"]:
            entrants = [e["id"] for e in group["entrants"]]
            beats = {(entrants[0],entrants[1]),(entrants[1],entrants[2]),(entrants[2],entrants[0])}
            for m in [m for m in event["matches"] if m["group_id"] == group["id"]]:
                scores = [[21,19]] if (m["side_a"]["id"],m["side_b"]["id"]) in beats else [[19,21]]
                self.service.score(event["id"],m["id"],scores,"u0",False)
        event = self.service.get(event["id"])
        self.assertTrue(event["plan"]["awaiting_tie_draw"])
        self.assertEqual(event["status"],"running")
        self.assertTrue(all(s["rank"]==1 for s in event["standings"]))
        with self.assertRaises(PermissionError):
            self.service.transition(event["id"],"advance","stranger",False)
        event = self.service.transition(event["id"],"advance","u0",False)
        records = [h for h in event["history"] if h["action"] == "group_qualification"]
        self.assertEqual(len(records[0]["after"]["tie_draw"]),2)
        self.assertTrue(self.finish(event)["champion"])

    def test_group_qualifiers_do_not_meet_same_group_first_round(self):
        for groups in (2,3,4,5):
            event = self.daily("group_knockout",groups*3,group_count=groups,options={"advance_per_group":2})
            event = self.finish(event)
            mapping = {e["id"]:g["id"] for g in event["groups"] for e in g["entrants"]}
            for m in event["matches"]:
                if m["stage"] == "knockout" and m["round"] == 1 and m["side_a"] and m["side_b"]:
                    self.assertNotEqual(mapping[m["side_a"]["id"]],mapping[m["side_b"]["id"]])

    def test_group_qualification_is_idempotent_after_restart(self):
        event = self.finish(self.daily("group_knockout",8))
        fresh = TournamentService(self.path).get(event["id"])
        self.assertEqual(event["matches"],fresh["matches"])
        self.assertEqual(len([h for h in fresh["history"] if h["action"] == "group_qualification"]),1)

    def test_daily_organizer_permissions_override_legacy_member_and_leader_modes(self):
        event = self.daily()
        with self.assertRaises(PermissionError):
            self.service.score(event["id"],event["matches"][0]["id"],[[21,12]],"group-member-not-player",False)
        event = self.daily(options={"scoring_mode":"leader","leader_user_ids":["leader"]})
        target = event["matches"][0]
        with self.assertRaises(PermissionError):
            self.service.score(event["id"],target["id"],[[21,12]],"leader",False)
        self.service.score(event["id"],target["id"],[[21,12]],"u0",False)
        with self.assertRaises(PermissionError):
            self.service.score(event["id"],target["id"],[[21,13]],"leader",False,expected_version=1)
        changed = self.service.score(event["id"],target["id"],[[21,13]],"u0",False,expected_version=1)
        self.assertEqual(changed["matches"][0]["version"],2)

    def test_large_staff_start_and_expiry_enforced(self):
        now = datetime.now(TZ)
        event = self.large(participants=people(4),staff=[
            {"user_id":"valid","starts_at":(now-timedelta(hours=1)).isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat()},
            {"user_id":"future","starts_at":(now+timedelta(hours=1)).isoformat(),"expires_at":(now+timedelta(hours=2)).isoformat()},
            {"user_id":"expired","starts_at":(now-timedelta(hours=2)).isoformat(),"expires_at":(now-timedelta(hours=1)).isoformat()}])
        self.service.transition(event["id"],"open_registration","admin",True)
        self.service.transition(event["id"],"close_registration","admin",True)
        event = self.service.transition(event["id"],"start","admin",True)
        target = next(m for m in event["matches"] if m["status"] == "ready")
        for actor in ("ordinary","u0","future","expired"):
            with self.assertRaises(PermissionError):
                self.service.score(event["id"],target["id"],[[21,10]],actor,False)
        self.service.score(event["id"],target["id"],[[21,10]],"valid",False)
        with self.assertRaises(PermissionError):
            self.service.transition(event["id"],"pause","valid",False)

    def test_registration_window_capacity_guests_and_cancellation_history(self):
        event = self.large(options={"capacity":4})
        now = datetime.now(TZ)
        with self.assertRaises(ValueError):
            self.service.enroll(event["id"],"one","一号",1)
        self.service.transition(event["id"],"open_registration","admin",True)
        with self.assertRaises(ValueError):
            self.service.enroll(event["id"],"one","一号",now=now-timedelta(days=2))
        event = self.service.enroll(event["id"],"one","一号",2)
        self.assertEqual(event["participant_count"],3)
        self.assertEqual({p["name"] for p in event["participants"]},{"一号","一号+1","一号+2"})
        event = self.service.enroll(event["id"],"one","一号",0)
        self.assertEqual(event["participant_count"],3)
        with self.assertRaises(ValueError):
            self.service.enroll(event["id"],"two","二号",1)
        event = self.service.enroll(event["id"],"one","一号",0)
        self.assertEqual(event["participant_count"],3)
        self.assertEqual(len(event["participants"]),3)
        event = self.service.cancel(event["id"],"one")
        self.assertEqual(event["participant_count"],2)
        event = self.service.cancel_guests(event["id"],"one",['一号+1','一号+2'])
        self.assertEqual(event["participant_count"],0)
        self.assertTrue(any(h["action"] == "cancel" for h in event["history"]))
        with self.assertRaises(ValueError):
            self.service.enroll(event["id"],"late","迟到",now=now+timedelta(days=2))

    def test_close_notification_persists_without_lost_delivery(self):
        event = self.large()
        self.service.transition(event["id"],"open_registration","admin",True)
        now = datetime.now(TZ)+timedelta(days=2)
        notices = self.service.due_notifications(now)
        self.assertEqual(len(notices),1)
        self.assertEqual(self.service.get(event["id"])["status"],"closed")
        self.assertEqual(notices,TournamentService(self.path).due_notifications(now))
        self.assertEqual(len([h for h in self.service.get(event["id"])["history"] if h["action"] == "auto_close_registration"]),1)

    def test_pause_restart_resume_preserves_draw_scores_and_scope(self):
        exact = self.daily("single_elimination",7)
        self.daily("single_elimination",7,venue_id=2)
        self.daily("single_elimination",7,chat_id="other")
        self.daily("single_elimination",4)
        target = next(m for m in exact["matches"] if m["status"] == "ready")
        exact = self.service.score(exact["id"],target["id"],[[21,10]],"u0",False)
        paused = self.service.transition(exact["id"],"pause","u0",False)
        fresh = TournamentService(self.path)
        with self.assertRaises(ValueError):
            fresh.score(exact["id"],next(m["id"] for m in paused["matches"] if m["status"] == "ready"),[[21,10]],"u0",False)
        candidates = fresh.resume_candidates(1,"chat1",people(7))
        self.assertEqual(len(candidates),2)
        self.assertEqual(candidates[0]["id"],exact["id"])
        resumed = fresh.transition(exact["id"],"resume","u0",False)
        self.assertEqual(paused["matches"],resumed["matches"])
        redrawn = fresh.transition(exact["id"],"draw","u0",False)
        self.assertEqual(resumed["matches"],redrawn["matches"])

    def test_score_validation_deuce_cap_best_of_and_finished_games(self):
        opts = {"points":21,"cap":30,"win_by":2,"best_of":1}
        for pair in ((21,0),(21,19),(22,20),(29,27),(30,28),(30,29),(29,30)):
            TournamentService._validate_scores([list(pair)],opts)
        for scores in ([[21,20]],[[22,19]],[[30,20]],[[20,18]],[[30,30]],[[31,29]],[[True,21]],[],[[21,19],[21,19]]):
            with self.assertRaises(ValueError,msg=str(scores)):
                TournamentService._validate_scores(scores,opts)
        opts["best_of"] = 3
        self.assertEqual(TournamentService._validate_scores([[21,19],[18,21],[21,0]],opts),0)
        for scores in ([[21,19]],[[21,19],[21,18],[21,10]]):
            with self.assertRaises(ValueError):
                TournamentService._validate_scores(scores,opts)

    def test_admin_correction_before_downstream_changes_participants(self):
        event = self.daily("single_elimination",4)
        target = event["matches"][0]
        event = self.service.score(event["id"],target["id"],[[21,10]],"u0",False)
        event = self.service.score(event["id"],target["id"],[[10,21]],"admin",True,expected_version=1)
        child = next(m for m in event["matches"] if m["round"] == 2)
        source_side = "a" if child["a_source"]["match"] == target["number"] else "b"
        self.assertEqual(child[f"side_{source_side}"]["id"],target["side_b"]["id"])
        self.assertEqual(event["matches"][0]["version"],2)
        event = self.finish(event)
        with self.assertRaises(ValueError):
            self.service.score(event["id"],target["id"],[[21,10]],"admin",True,expected_version=2)
        # A correction preserving winner does not change downstream competitors.
        event = self.service.score(event["id"],target["id"],[[11,21]],"admin",True,expected_version=2)
        self.assertEqual(event["status"],"completed")
        self.assertEqual(len([h for h in event["history"] if h["action"] == "score_correction"]),2)

    def test_concurrent_scores_no_silent_overwrite(self):
        event = self.daily()
        target = event["matches"][0]
        def write(index):
            try:
                TournamentService(self.path).score(event["id"],target["id"],[[21,10+index]],"u0",False,expected_version=0)
                return "ok"
            except ValueError:
                return "conflict"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write,(0,1)))
        self.assertEqual(sorted(results),["conflict","ok"])
        result = self.service.get(event["id"])
        self.assertEqual(result["matches"][0]["version"],1)
        self.assertEqual(len([h for h in result["history"] if h["action"] == "score"]),1)

    def test_downstream_version_changes_on_winner_correction(self):
        event = self.daily("single_elimination",4)
        first,second = event["matches"][:2]
        self.service.score(event["id"],first["id"],[[21,10]],"u0",False)
        event = self.service.score(event["id"],second["id"],[[21,10]],"u0",False)
        final = event["matches"][-1]
        self.assertEqual(final["status"],"ready")
        self.service.score(event["id"],first["id"],[[10,21]],"admin",True,expected_version=1)
        with self.assertRaises(ValueError):
            self.service.score(event["id"],final["id"],[[21,10]],"u0",False,expected_version=final["version"])

    def test_reset_final_removed_when_admin_corrects_grand_final(self):
        event = self.daily("double_elimination",4)
        while True:
            target = next(m for m in event["matches"] if m["status"] == "ready")
            if target["stage"] == "grand_final":
                event = self.service.score(event["id"],target["id"],[[10,21]],"u0",False)
                break
            event = self.service.score(event["id"],target["id"],[[21,10]],"u0",False)
        self.assertEqual(event["status"],"running")
        self.assertEqual(event["matches"][-1]["stage"],"reset_final")
        event = self.service.score(event["id"],target["id"],[[21,10]],"admin",True,expected_version=1)
        self.assertEqual(event["status"],"completed")
        self.assertFalse(any(m["stage"] == "reset_final" for m in event["matches"]))
        self.assertEqual(event["plan"]["final_number"],target["number"])

    def test_group_correction_replaces_unplayed_bracket_with_new_ids(self):
        event = self.daily("group_knockout",4)
        target = event["matches"][0]
        for m in event["matches"]:
            self.service.score(event["id"],m["id"],[[21,10]],"u0",False)
        event = self.service.get(event["id"])
        old_final = event["matches"][-1]
        event = self.service.score(event["id"],target["id"],[[10,21]],"admin",True,expected_version=1)
        new_final = event["matches"][-1]
        self.assertGreater(new_final["id"],old_final["id"])
        with self.assertRaises(ValueError):
            self.service.score(event["id"],old_final["id"],[[21,10]],"u0",False,expected_version=0)

    def test_configurable_three_game_fifteen_point_match(self):
        event = self.daily(n=2,options={"points":15,"cap":21,"best_of":3})
        target = event["matches"][0]
        event = self.service.score(event["id"],target["id"],[[15,13],[20,21],[21,19]],"u0",False)
        self.assertEqual(event["status"],"completed")
        self.assertEqual(event["champion"][0]["id"],target["side_a"]["id"])

    def test_cross_tournament_match_and_database_isolation(self):
        one,two = self.daily(),self.daily()
        with self.assertRaises(ValueError):
            self.service.score(one["id"],two["matches"][0]["id"],[[21,10]],"admin",True)
        other = TournamentService(Path(self.tmp.name)/"other-bot.sqlite3")
        self.assertEqual(other.list(),[])
        with self.assertRaises(ValueError):
            other.get(one["id"])

    def test_five_team_is_not_five_rotation(self):
        for n in (5,9,11,16):
            with self.assertRaises(ValueError):
                self.daily("five_team",n)
        event = self.daily("five_team",15)
        self.assertEqual(len(event["matches"]),3)
        self.assertTrue(all(len(e["members"])==5 for e in event["groups"][0]["entrants"]))
        self.assertEqual(event["options"]["points"],50)
        event = self.finish(event)
        self.assertTrue(all(max(m["scores"][0]) == 50 for m in event["matches"]))
        with self.assertRaises(ValueError):
            self.daily("five_team",10,options={"points":75})
        adapted = self.daily("five_team",10,options={"points":75,"cap":75,"relay_segment":15})
        self.assertEqual(adapted["options"]["points"],75)

    def test_rotation_balances_appearances(self):
        for n in range(4,22):
            plan = get_format("doubles_rotation").build(people(n),{},13)
            appearances = Counter()
            for m in plan["matches"]:
                ids = m["side_a"]["members"]+m["side_b"]["members"]
                self.assertEqual(len(set(ids)),4)
                appearances.update(ids)
            self.assertEqual(set(appearances.values()),{4})

    def test_input_bounds_keyword_and_draw_immutability(self):
        for data in ({"format":"../../evil"},{"kind":"large","keyword":"比赛"},{"venue_id":False},
                     {"options":{"points":True}},{"options":{"team_size":True}},
                     {"options":{"scoring_mode":"admin"}},{"options":{"shell":"rm"}}):
            with self.assertRaises((ValueError,PermissionError)):
                self.daily(**data)
        event = self.large()
        with self.assertRaises(ValueError):
            self.large()
        event = self.daily()
        with self.assertRaises(ValueError):
            self.service.update(event["id"],{"options":{"points":15}},"admin",True)
        with self.assertRaises(PermissionError):
            self.service.update(event["id"],{"title":"抢占"},"stranger",False)
        with self.assertRaises(ValueError):
            self.service.update(event["id"],{"staff":[{"user_id":"x"}]},"admin",True)

    def test_schedule_changes_audited_and_markup_escaped(self):
        event = self.daily(title="<at user_id=all>bad</at>")
        target = event["matches"][0]
        event = self.service.update(event["id"],{"match_schedule":[{"match_id":target["id"],
            "scheduled_at":"2026-12-01T19:00:00+08:00","court":"2号球场"}]},"admin",True)
        self.assertEqual(event["matches"][0]["court"],"2号球场")
        self.assertNotIn("<at",self.service.render(event["id"]))
        self.assertIn("2号球场",self.service.render(event["id"]))
        self.assertEqual(event["history"][-1]["action"],"update")

    def test_schedule_exact_conflicts_rollback_and_venue_isolation(self):
        one,two,other = self.daily(),self.daily(),self.daily(venue_id=2)
        at = "2026-12-01T19:00:00+08:00"
        def schedule(event,index=0,court="1号球场"):
            return self.service.update(event["id"],{"match_schedule":[{"match_id":event["matches"][index]["id"],"scheduled_at":at,"court":court}]},"admin",True)
        schedule(one)
        with self.assertRaises(ValueError):
            schedule(one,1)
        self.assertIsNone(self.service.get(one["id"])["matches"][1]["scheduled_at"])
        with self.assertRaises(ValueError):
            schedule(two)
        self.assertIsNone(self.service.get(two["id"])["matches"][0]["scheduled_at"])
        self.assertEqual(schedule(other)["matches"][0]["court"],"1号球场")
        self.assertEqual(schedule(two,court="2号球场")["matches"][0]["court"],"2号球场")


if __name__ == "__main__":
    unittest.main()
