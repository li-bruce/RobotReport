"""Create an isolated demo or local platform without printing credentials."""
import argparse
import json
import secrets
from pathlib import Path
from app.security import Platform
from app.service import Service
from app.members import Members
from app.people import People


def bootstrap(root, demo=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    credentials = root / 'admin.local.json'
    if credentials.exists():
        token = json.loads(credentials.read_text(encoding='utf-8'))['admin_token']
    else:
        token = secrets.token_urlsafe(36)
        credentials.write_text(json.dumps({'admin_token': token}), encoding='utf-8')
        try:
            credentials.chmod(0o600)
        except OSError:
            pass
    platform = Platform(root, token)
    principal = platform.authenticate(token)
    if demo and not platform.list(principal):
        platform.create(principal, {'name':'羽协本地演示', 'chat_id':'demo-group-a', 'robot_key':'demo-only-robot-key-a'})
        platform.create(principal, {'name':'第二机器人 · 共享后台演示', 'chat_id':'demo-group-b', 'robot_key':'demo-only-robot-key-b'})
        context = platform.context(principal)
        Service(context).update_config({'public_base_url':'http://127.0.0.1:9874'})
        members = Members(context)
        for user_id, name in [('demo-admin', '演示管理员'), ('demo-member', '演示球友')]:
            members.touch(name, user_id, source='admin')
            members.review(user_id, 'approved')
        People(context).set_roles('demo-admin', ['club_admin'])
    return credentials


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='state/local')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    path = bootstrap(args.state, args.demo)
    print('Initialized. Credentials are saved only in: ' + str(path))
