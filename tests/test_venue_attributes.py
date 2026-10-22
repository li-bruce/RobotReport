"""Venue annotations survive edits/migrations without changing bookings or access."""
from pathlib import Path
import sqlite3
import tempfile
from fastapi.testclient import TestClient

from app.db import connect, initialize
from app.main import create_app
from app.service import Service
from tests.test_core import CoreFixture, TOKEN


class VenueAttributeTests(CoreFixture):
    def test_create_roundtrip_preserve_and_clear(self):
        attrs = [{'label': ' 运动类型 ', 'value': ' 羽毛球、篮球 '}, {'label': '室内外', 'value': '室内'}]
        venue = self.s.save_venue({'name': '综合体育馆', 'attributes': attrs})
        expected = [{'label': '运动类型', 'value': '羽毛球、篮球'}, attrs[1]]
        self.assertEqual(venue['attributes'], expected)
        self.s.save_venue({'description': '三楼', 'active': False}, venue['id'])
        other = Service(self.platform.context(self.admin, self.bots[1]['id']), self.clock)
        saved = next(v for v in other.venues() if v['id'] == venue['id'])
        self.assertEqual(saved['attributes'], expected)
        self.assertFalse(saved['active'])
        self.assertEqual(self.s.save_venue({'attributes': []}, venue['id'])['attributes'], [])

    def test_invalid_attributes_reject_whole_update(self):
        old = self.s.venue(1)
        bad = [None, '室内', {}, [1], [{'label': 'a'}], [{'label':'a','value':'b','extra':True}],
               [{'label': '', 'value': 'v'}], [{'label': 'a', 'value': 42}],
               [{'label': 'a'*25, 'value':'v'}], [{'label':'a','value':'v'*121}],
               [{'label':'a','value':'one\ntwo'}], [{'label':'a','value':'v\x00'}],
               [{'label':'a','value':'v'}, {'label':'Ａ','value':'v'}],
               [{'label':str(i),'value':'v'} for i in range(21)]]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.s.save_venue({'name':'must-not-save','attributes':value},1)
            self.assertEqual(self.s.venue(1), old)

    def test_member_cannot_write_annotations(self):
        with self.assertRaises(PermissionError):
            Service(self.member,self.clock).save_venue({'attributes':[{'label':'x','value':'y'}]},1)
        self.assertEqual(self.s.venue(1)['attributes'], [])

    def test_annotation_does_not_change_sessions_or_registration(self):
        self.s.enroll('1001','小李')
        before = self.s.registrations()
        with connect(self.s.path) as db:
            rows = {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('slots','sessions','registrations')}
        self.s.save_venue({'attributes':[{'label':'运动类型','value':'篮球'},{'label':'等级','value':'自由填写的说明'}]},1)
        with connect(self.s.path) as db:
            self.assertEqual(rows,{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in rows})
        self.assertEqual(self.s.registrations()['registrations'],before['registrations'])

    def test_old_schema_migrates_twice_without_replacing_venue(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'legacy.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE venues(id INTEGER PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1)")
                db.execute("INSERT INTO venues VALUES(7,'原场地','原位置',0)")
            initialize(path);initialize(path)
            with connect(path) as db:
                old = dict(db.execute('SELECT * FROM venues WHERE id=7').fetchone())
                self.assertEqual(old,{'id':7,'name':'原场地','description':'原位置','active':0,'attributes':'[]'})

    def test_http_contract_and_auth(self):
        app=create_app(self.platform.root,start_scheduler=False,live=False,clock=self.clock)
        with TestClient(app) as client:
            self.assertEqual(client.patch('/api/venues/1',json={'attributes':[]}).status_code,401)
            headers={'Authorization':'Bearer '+TOKEN}
            value=[{'label':'配套设施','value':'储物柜、淋浴'}]
            response=client.patch('/api/venues/1',headers=headers,json={'attributes':value})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['attributes'],value)
            self.assertEqual(client.get('/api/venues',headers=headers).json()[0]['attributes'],value)
            rejected=client.patch('/api/venues/1',headers=headers,json={'attributes':[{'label':'重复','value':'1'},{'label':'重复','value':'2'}]})
            self.assertEqual(rejected.status_code,400,rejected.text)
            self.assertEqual(client.get('/api/venues',headers=headers).json()[0]['attributes'],value)
