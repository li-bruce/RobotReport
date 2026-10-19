import io
import json
from dataclasses import replace
import pytest
import unittest
from openpyxl import Workbook
from app.member_imports import MemberImports
from app.members import Members
from app.db import connect
import tests.test_member_imports as fixtures


def workbook(rows, other=None):
    book=Workbook();book.active.title='会员名单'
    for row in rows:book.active.append(row)
    if other:
        sheet=book.create_sheet('另表')
        for row in other:sheet.append(row)
    output=io.BytesIO();book.save(output);book.close();return output.getvalue()


class SheetTests(unittest.TestCase):
    setUp=fixtures.MemberImportTests.setUp
    member=fixtures.MemberImportTests.member

    def test_xlsx_alias_header_offset_numeric_phone_extra_columns_and_preview(self):
        data=workbook([['协会名册'],[],['序号','真实姓名（必填）','手机号码','男女','自评等级','所属组别','管理员'],[1,'张三',13800000001,'男',3,'一组','club_admin']])
        preview=self.imports.preview(data,'roster.xlsx')
        assert preview['can_commit'] and preview['rows'][0]['level']==3
        assert preview['mappings'][0]['header_row']==3
        assert preview['mappings'][0]['ignored']==['序号','管理员']
        assert '13800000001' not in json.dumps(preview)
        self.imports.commit(preview['token'])
        member=self.member().apply('输入名',9,'13800000001',gender='male')
        assert member['status']=='approved' and member['name']=='张三' and member['level']==3 and not member['roles']

    def test_missing_fields_pending_then_admin_approval_and_repeated_submission(self):
        preview=self.imports.preview(workbook([['手机号码'],[13800000001]]),'x.xlsx')
        assert preview['can_commit']
        self.imports.commit(preview['token'])
        member=self.member().apply('补全姓名',7,'13800000001',gender='female')
        assert member['status']=='pending' and member['name']=='补全姓名'
        assert member['level'] is None and member['requested_level']==7
        assert member['roles']==[]
        with pytest.raises(PermissionError):self.member('other').apply('他人',phone='13800000001',gender='male')
        self.manager.review('alice','approved');self.manager.review_level('alice',True,7)
        assert self.member().get()['level']==7

    def test_missing_phones_multiple_people_not_name_matched_and_manual_completion(self):
        content='姓名,备注\n同名,甲\n同名,乙\n'.encode()
        preview=self.imports.preview(content);assert preview['create_count']==2
        self.imports.commit(preview['token'])
        assert self.imports.preview(content)['skip_count']==2
        self.member().apply('同名',gender='male')
        assert len(self.imports.list('unbound'))==2
        record=self.imports.list()[0]
        self.imports.update(record['id'],{'phone':'13800000001'})
        applied=self.member().apply('同名',phone='13800000001',gender='male')
        assert applied['status']=='pending'
        assert len(self.imports.list('linked'))==1

    def test_multisheet_mapping_duplicates_and_formula_errors(self):
        preview=self.imports.preview(workbook([['姓名','电话'],['甲',13800000001]],[['name','mobile'],['乙',13900000002]]),'two.xlsx')
        assert preview['create_count']==2 and len(preview['mappings'])==2
        with pytest.raises(ValueError,match='同类列'):
            self.imports.preview(workbook([['姓名','手机','电话'],['甲',13800000001,13800000001]]),'duplicate.xlsx')
        result=self.imports.preview(workbook([['姓名','电话'],['=HYPERLINK("http://example.invalid")',13800000001]]),'formula.xlsx')
        assert not result['can_commit'] and '公式' in result['errors'][0]['message']

    def test_internal_ids_and_roles_ignored_and_no_phone_leak(self):
        self.imports.create({'name':'预登记甲','gender':'female'})
        assert len(self.imports.list())==1
        assert self.manager.list()==[]
        with pytest.raises(ValueError):self.imports.create({'name':'x','user_id':'forged'})
        with pytest.raises(PermissionError):MemberImports(self.member().ctx,self.clock).create({'name':'x'})

    def test_legacy_phone_schema_migration_preserves_every_old_value(self):
        from app.member_imports import ensure_schema
        with connect(self.admin.db_path,True) as db:
            db.execute('DROP TABLE IF EXISTS pre_registered_members')
            db.execute('''CREATE TABLE pre_registered_members(id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_id TEXT NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL,level INTEGER,
                group_ids TEXT NOT NULL DEFAULT '[]',linked_user_id TEXT,created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,linked_at TEXT,gender TEXT NOT NULL DEFAULT 'unknown',UNIQUE(scope_id,phone))''')
            db.execute("INSERT INTO pre_registered_members(scope_id,name,phone,created_by,created_at) VALUES('club','原有名册','13800000009','admin','2026-10-01')")
            old=dict(db.execute('SELECT * FROM pre_registered_members').fetchone())
            ensure_schema(db)
            migrated=dict(db.execute('SELECT * FROM pre_registered_members').fetchone())
            assert all(migrated[key]==value for key,value in old.items())
            assert migrated['review_required']==0
        preview=self.imports.preview('姓名\n新甲\n新乙\n'.encode())
        assert self.imports.commit(preview['token'])['imported']==2

    def test_completion_does_not_resurrect_same_second_previous_application(self):
        self.member().apply('原申请',gender='male')
        preview=self.imports.preview('姓名,手机号\n预登记,13800000001\n'.encode())
        self.imports.commit(preview['token'])
        self.member().apply('补全申请',phone='13800000001',gender='female')
        with connect(self.admin.db_path) as db:
            rows=db.execute('SELECT name,status FROM membership_applications ORDER BY id').fetchall()
            assert [tuple(r) for r in rows]==[('原申请','superseded'),('预登记','pending')]


