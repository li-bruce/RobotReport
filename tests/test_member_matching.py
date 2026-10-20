"""Duplicate confirmation never grants membership or joins platform accounts."""
import json
import unittest
from dataclasses import replace
from datetime import timedelta
import pytest
from app.db import connect
from app.member_imports import MemberImports
from tests import test_member_imports as fixtures


class MatchingTests(unittest.TestCase):
    setUp=fixtures.MemberImportTests.setUp
    member=fixtures.MemberImportTests.member

    def preview(self,text):
        return self.imports.preview(text.encode())

    def choose(self,preview,targets):
        return self.imports.resolve(preview['token'],[
            {'source_key':row['source_key'],'target':target}
            for row,target in zip([r for r in preview['rows'] if r['status']=='match'],targets)])

    def test_name_only_reimport_needs_confirmation_and_preserves_original(self):
        first=self.preview('姓名,性别\n张三,男\n')
        self.imports.commit(first['token'])
        before=self.imports.list()
        preview=self.preview('真实姓名,自评等级\n张三,9\n新会员,2\n')
        assert preview['token'] and not preview['can_commit'] and preview['match_count']==1
        row=preview['rows'][0]
        assert row['matches'][0]['evidence']==[{'field':'name','label':'姓名','value':'张三'}]
        with pytest.raises(ValueError,match='待确认'):self.imports.commit(preview['token'])
        confirmed=self.choose(preview,[row['matches'][0]['target']])
        assert confirmed['can_commit'] and confirmed['create_count']==1 and confirmed['skip_count']==1
        assert self.imports.commit(confirmed['token'])['imported']==1
        assert next(r for r in self.imports.list() if r['name']=='张三')==before[0]
        assert self.manager.list()==[]

    def test_student_number_email_and_name_are_candidates_but_common_columns_are_not(self):
        first=self.preview('姓名,学号,邮箱,性别,等级,组别\n张三,2026001,student@example.test,男,3,甲组\n')
        self.imports.commit(first['token'])
        preview=self.preview('姓名,学生编号,电子邮件,性别,等级,组别\n改名,2026001,STUDENT@example.test,女,5,乙组\n新人,,,男,3,甲组\n')
        assert preview['rows'][0]['status']=='match'
        assert {r['field'] for r in preview['rows'][0]['matches'][0]['evidence']}=={'student_number','email'}
        assert preview['rows'][1]['status']=='create'
        assert 'student@example.test' not in json.dumps(preview)
        assert set(preview['mappings'][0]['identity_columns'])=={'student_number','email'}

    def test_same_name_can_be_explicitly_kept_as_separate_people_in_batch(self):
        preview=self.preview('姓名,性别\n同名,男\n同名,女\n')
        assert preview['match_count']==1
        confirmed=self.choose(preview,['new'])
        assert self.imports.commit(confirmed['token'])['imported']==2
        again=self.preview('姓名,备注\n同名,新表\n')
        assert len(again['rows'][0]['matches'])==2
        target=again['rows'][0]['matches'][1]['target']
        confirmed=self.choose(again,[target])
        assert self.imports.commit(confirmed['token'])['skipped']==1

    def test_member_candidate_does_not_bind_import_or_modify_account(self):
        self.member().apply('张三',gender='male')
        self.manager.review('alice','approved')
        before=self.member().get()
        preview=self.preview('姓名,工号\n张三,E001\n')
        candidate=preview['rows'][0]['matches'][0]
        assert candidate['target']=='member:alice'
        confirmed=self.choose(preview,[candidate['target']])
        assert self.imports.commit(confirmed['token'])['imported']==0
        assert self.member().get()==before and self.imports.list()==[]
        other=self.member('other').apply('张三',gender='male')
        assert other['status']=='pending'

    def test_confirmation_is_scoped_and_commit_rechecks_changed_candidate(self):
        self.imports.create({'name':'张三'})
        preview=self.preview('姓名,性别\n张三,男\n')
        row=preview['rows'][0]
        choice={'source_key':row['source_key'],'target':row['matches'][0]['target']}
        for context in (replace(self.admin,actor='other'),replace(self.admin,scope_id='elsewhere')):
            with pytest.raises(ValueError):MemberImports(context,self.clock).resolve(preview['token'],[choice])
        with pytest.raises(PermissionError):MemberImports(self.member().ctx,self.clock).resolve(preview['token'],[choice])
        with pytest.raises(ValueError):self.imports.resolve(preview['token'],[{**choice,'target':'prereg:9999'}])
        self.imports.resolve(preview['token'],[choice])
        with connect(self.admin.db_path,True) as db:
            db.execute("UPDATE pre_registered_members SET name='改名' WHERE name='张三'")
        with pytest.raises(ValueError,match='重新预览'):self.imports.commit(preview['token'])
        assert len(self.imports.list())==1

    def test_batch_confirm_and_idempotent_commit_do_not_add_duplicates(self):
        first=self.preview('姓名,学号\n张三,1\n李四,2\n')
        self.imports.commit(first['token'])
        preview=self.preview('姓名,学号\n张三,1\n李四,2\n新甲,3\n')
        confirmed=self.choose(preview,[r['matches'][0]['target'] for r in preview['rows'] if r['status']=='match'])
        result=self.imports.commit(confirmed['token'])
        assert (result['imported'],result['skipped'])==(1,2)
        assert self.imports.commit(confirmed['token'])['already_committed']
        with pytest.raises(ValueError):self.choose(confirmed,['new'])
        assert len(self.imports.list())==3

    def test_pending_token_expires_and_untrusted_extra_columns_do_not_grant_roles(self):
        self.imports.create({'name':'张三'})
        preview=self.preview('姓名,角色,管理员,性别,等级\n张三,club_admin,true,男,3\n')
        assert preview['rows'][0]['matches'][0]['evidence'][0]['field']=='name'
        self.current+=timedelta(minutes=21)
        with pytest.raises(ValueError):self.choose(preview,['new'])
        assert self.manager.list()==[]
