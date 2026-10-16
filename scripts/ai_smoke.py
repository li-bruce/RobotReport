"""Explicit, tiny real-provider interpretation check; no business writes or messaging."""
import asyncio
import json
from pathlib import Path
import time
from datetime import timedelta
from app.ai import interpret
from app.db import now


async def main():
    secret = json.loads(Path('state/secrets.local.json').read_text(encoding='utf-8-sig'))['siliconflow']
    config = dict(secret, enabled=True, model='deepseek-ai/DeepSeek-V3.2')
    context = {'bot_id':'synthetic-local-test', 'chat_id':'synthetic-group', 'user_id':'synthetic-user',
               'today':now().date().isoformat(), 'venue_id':1, 'is_admin':False,
               'formats':['round_robin'], 'allowed_venue_ids':[1], 'scoreable_tournament_ids':[], 'resumable_tournament_ids':[]}
    tomorrow=(now()+timedelta(days=1)).date().isoformat()
    report=[]
    for label,text in [('read','请查看今天场地1的报名名单'),('proposal',f'{tomorrow} 20:30 我想报名，带一位朋友')]:
        start=time.perf_counter()
        result=await interpret(text,context,config)
        report.append({'case':label,'model':config['model'],'seconds':round(time.perf_counter()-start,2),
                       **{k:result.get(k) for k in ('command','intent','requires_confirmation','error')}})
    Path('test-results').mkdir(exist_ok=True)
    Path('test-results/ai-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    assert report[0]['error'] is None and report[0]['intent']=='Roster'
    assert report[1]['error'] is None and report[1]['intent']=='Register' and report[1]['requires_confirmation'] is True
    assert f'{tomorrow} 20:30 报名+1' in report[1]['command'] and '接龙' not in report[1]['command']


if __name__=='__main__':
    asyncio.run(main())
