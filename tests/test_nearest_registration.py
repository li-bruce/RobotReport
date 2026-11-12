import asyncio
from datetime import timedelta
from dataclasses import replace
from app.commands import Commands
from app.service import Service
from tests.test_external_substitute import ExternalSubstituteTests


class NearestRegistrationTests(ExternalSubstituteTests):
    def test_bare_command_uses_external_after_primary_ends(self):
        self.now=self.now.replace(hour=21)
        reply=asyncio.run(Commands(self.a,self.clock).run('报名'))
        self.assertIn('正式',reply)
        self.assertEqual(self.active()[0]['user_id'],'1001')
        self.assertTrue(self.active()[0]['substitute_only'])
        snapshot=Commands(self.a,self.clock).snapshot('报名')
        self.assertEqual(snapshot[1]['session_id'],self.target['id'])

    def test_second_group_bot_can_automatically_join_nearer_external_first_session(self):
        self.platform.update(self.admin,self.b.id,{'allow_external_substitute':True,'external_slot_keys':[self.slots[0]['slot_key']]})
        reply=asyncio.run(Commands(self.b,self.clock).run('报名'))
        self.assertIn('第1场',reply);self.assertIn('正式',reply)

    def test_auto_can_choose_selected_external_venue_but_explicit_venue_is_respected(self):
        venue=self.all.save_venue({'name':'午间场地'})
        rules=self.all.save_slots(venue['id'],[{'name':'近场','weekdays':[0],'start':'16:30','end':'17:30'}])
        self.platform.update(self.admin,self.a.id,{'venue_ids':[1],'external_slot_keys':[rules[0]['slot_key']]})
        reply=asyncio.run(Commands(self.a,self.clock).run('报名'))
        self.assertIn('午间场地',reply);self.assertIn('正式',reply)
        explicit=asyncio.run(Commands(self.a,self.clock).run('场地1 报名'))
        self.assertIn('正式',explicit)

    def test_all_ended_sessions_do_not_fall_through_to_tomorrow(self):
        self.now=self.now.replace(hour=23)
        with self.assertRaises(ValueError):asyncio.run(Commands(self.a,self.clock).run('报名'))
        self.assertEqual(self.all.registrations()['registrations'],[])
