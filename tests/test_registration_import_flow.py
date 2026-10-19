import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import TZ, connect


class RegistrationImportFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.clock=lambda:datetime(2026,10,19,10,0,tzinfo=TZ)
        self.token='csv-flow-isolated-administrator-token'
        self.app=create_app(Path(self.tmp.name),self.token,start_scheduler=False,live=False,clock=self.clock)
        self.client=TestClient(self.app)
        self.headers={'Authorization':'Bearer '+self.token}
        self.platform=self.app.state.platform
        self.principal=self.platform.authenticate(self.token)
        self.bot=self.platform.create(self.principal,{'name':'测试助手'})
        self.channel=self.platform.save_channel(self.principal,self.bot['id'],{'name':'一群','chat_id':'one','robot_key':'test-one'})
        self.second_bot=self.platform.create(self.principal,{'name':'二群助手'})
        self.second=self.platform.save_channel(self.principal,self.second_bot['id'],{'name':'二群','chat_id':'two','robot_key':'test-two'})
        self.call('PATCH','/api/config',{'public_base_url':'http://127.0.0.1:9874'})
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
    def call(self,method,path,data=None,status=200):
        response=self.client.request(method,path,json=data,headers=self.headers)
        self.assertEqual(response.status_code,status,response.text)
        return response.json()
    def preview(self,content):
        response=self.client.post('/api/member-imports/preview',headers=self.headers,files={'file':('members.csv',content.encode('utf-8-sig'),'text/csv')})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def sim(self,user,text,channel=None,status=200):
        return self.call('POST','/api/simulator',{'user_id':user,'content':text,'channel_id':(channel or self.channel)['id']},status)
    def csv(self):return '姓名,手机号,自评级别,组别,性别\n登记姓名,13800000001,2,周一组,男\n'
    def test_csv_import_phone_binding_and_already_registered_skip(self):
        template=self.client.get('/api/member-imports/template',headers=self.headers)
        self.assertEqual(template.status_code,200)
        self.assertIn('姓名',template.content.decode('utf-8-sig'))
        preview=self.preview(self.csv())
        self.assertTrue(preview['can_commit']);self.assertEqual(preview['create_count'],1)
        self.assertNotIn('13800000001',str(preview))
        self.assertEqual(self.call('GET','/api/members'),[])
        committed=self.call('POST','/api/member-imports/commit',{'token':preview['token']})
        self.assertEqual(committed['imported'],1)
        self.assertTrue(self.call('POST','/api/member-imports/commit',{'token':preview['token']})['already_committed'])
        records=self.call('GET','/api/member-imports/records')
        self.assertEqual(records[0]['status'],'unbound')
        start=self.sim('alice','注册')['reply']
        reference=start.split('/register#')[1].split(')')[0]
        response=self.client.post('/public/registration/'+reference,json={'gender': 'male', 'name':'本次填写名','phone':'+86 138-0000-0001','level':3})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['registration_result'],'linked')
        self.assertIn('关联',result['message']);self.assertNotIn('13800000001',str(result))
        self.assertNotIn('command',result)
        self.assertEqual(self.client.get('/public/registration/'+reference).json(),result)
        self.assertEqual(self.client.post('/public/registration/'+reference,json={'gender': 'male', 'name':'不得覆盖'}).json(),result)
        member=self.call('GET','/api/members/alice')
        self.assertEqual((member['name'],member['status'],member['level']),('登记姓名','approved',2))
        self.assertEqual(member['roles'],[])
        self.assertEqual([g['name'] for g in member['groups']],['周一组'])
        self.assertIn('登记姓名',self.sim('alice','报名',self.second)['reply'])
        before=self.call('GET','/api/registration-applications')
        reply=self.sim('alice','注册',self.second)['reply']
        self.assertIn('已注册',reply);self.assertNotIn('/register#',reply)
        self.assertIn('已注册',self.sim('alice','注册 改名 1级 13900000001')['reply'])
        self.assertEqual(self.call('GET','/api/members/alice')['name'],'登记姓名')
        self.assertEqual(self.call('GET','/api/registration-applications'),before)
        self.assertEqual(self.call('GET','/api/member-imports/records')[0]['status'],'linked')
        again=self.preview(self.csv());self.assertEqual(again['skip_count'],1)
    def test_name_never_matches_and_bound_phone_cannot_be_taken(self):
        preview=self.preview(self.csv());self.call('POST','/api/member-imports/commit',{'token':preview['token']})
        self.sim('same-name','注册 登记姓名 男')
        self.assertEqual(self.call('GET','/api/members/same-name')['status'],'pending')
        self.assertEqual(self.call('GET','/api/member-imports/records')[0]['status'],'unbound')
        self.assertIn('关联',self.sim('owner','注册 任意姓名 男 13800000001')['reply'])
        self.sim('other','注册 另一个人 男 13800000001',status=403)
        self.assertNotEqual(self.call('GET','/api/members/other')['status'],'approved')
        self.assertEqual(self.call('GET','/api/member-imports/records')[0]['linked_user_id'],'owner')
    def test_csv_permissions_limits_and_invalid_rows(self):
        for path in ('/api/member-imports/template','/api/member-imports/records'):
            self.assertEqual(self.client.get(path).status_code,401)
        grant=self.platform.grant(self.principal,[self.bot['id']])
        denied=self.client.post('/api/member-imports/preview',headers={'Authorization':'Bearer '+grant['token']},files={'file':('a.csv',self.csv())})
        self.assertEqual(denied.status_code,403)
        preview=self.preview('姓名,手机号\n甲,不是号码\n乙,13800000002\n')
        self.assertFalse(preview['can_commit']);self.assertTrue(preview['errors'])
        self.assertEqual(self.call('GET','/api/member-imports/records'),[])
        oversized=self.client.post('/api/member-imports/preview',headers=self.headers,files={'file':('huge.csv',b'a'*(2*1024*1024+1),'text/csv')})
        self.assertEqual(oversized.status_code,400)


if __name__=='__main__':unittest.main()
