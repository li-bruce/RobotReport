import json
import pytest
from app.db import connect
from app.member_maintenance import preview, apply
from tests.test_core import CoreFixture


class MaintenanceTests(CoreFixture):
    def prepare(self):
        from app.member_imports import ensure_schema
        with connect(self.ctx.db_path, True) as db:
            ensure_schema(db)
            db.executemany('INSERT INTO member_groups(chat_id,name,created_at) VALUES(?,?,?)', [('club', n, 'test') for n in ('一组','二组','保留标签')])
            ids={r['name']:r['id'] for r in db.execute('SELECT * FROM member_groups')}
            db.execute("UPDATE member_records SET gender='male' WHERE user_id='1001'")
            db.execute('INSERT INTO member_group_links VALUES(?,?,?)',('club','1001',ids['二组']))
            db.execute('INSERT INTO member_group_links VALUES(?,?,?)',('club','1001',ids['保留标签']))
            db.execute("INSERT INTO pre_registered_members(scope_id,name,gender,group_ids,created_by,created_at) VALUES('club','示例女会员','female','[]','test','test')")
        self.s.enroll('1001','小李',guests=2)
        return ids

    def test_preview_then_apply_preserves_members_self_and_other_tags(self):
        ids=self.prepare()
        with connect(self.ctx.db_path) as db:
            plan=preview(db);before=list(db.execute('SELECT * FROM member_records'))
            self.assertEqual(plan['summary']['guests_deleted'],2)
            self.assertEqual(db.execute('SELECT count(*) FROM registrations').fetchone()[0],3)
        with connect(self.ctx.db_path,True) as db:
            apply(db,plan['digest'],'a'*64)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(list(db.execute('SELECT * FROM member_records')),before)
            self.assertEqual({r[0] for r in db.execute("SELECT group_id FROM member_group_links WHERE user_id='1001'")},{ids['一组'],ids['保留标签']})
            self.assertEqual(json.loads(db.execute('SELECT group_ids FROM pre_registered_members').fetchone()[0]),[ids['二组']])
            self.assertEqual(db.execute('SELECT guest_index FROM registrations').fetchall()[0][0],0)
            self.assertEqual(preview(db)['summary']['members_changed'],0)
            self.assertEqual(preview(db)['summary']['guests_deleted'],0)

    def test_changed_preview_aborts_without_deletion(self):
        self.prepare()
        with connect(self.ctx.db_path) as db:plan=preview(db)
        with connect(self.ctx.db_path,True) as db:db.execute("UPDATE member_records SET gender='unknown' WHERE user_id='1001'")
        with pytest.raises(ValueError,match='重新预览'):
            with connect(self.ctx.db_path,True) as db:apply(db,plan['digest'],'b'*64)
        with connect(self.ctx.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM registrations WHERE guest_index>0').fetchone()[0],2)
            self.assertEqual(preview(db)['summary']['members_changed'],0)
