import json
import tempfile
import unittest
from pathlib import Path

from app.members import Members
from app.security import Platform
from scripts.bootstrap import bootstrap


class BootstrapV2Tests(unittest.TestCase):
    def test_demo_has_shared_members_and_independent_roles_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = bootstrap(root, demo=True)
            saved = credentials.read_text(encoding='utf-8')
            token = json.loads(saved)['admin_token']
            platform = Platform(root)
            principal = platform.authenticate(token)
            bots = platform.list(principal)
            self.assertEqual(len(bots), 2)
            contexts = [platform.context(principal, bot['id']) for bot in bots]
            self.assertEqual(contexts[0].db_path, contexts[1].db_path)
            self.assertNotEqual(contexts[0].transport_db_path, contexts[1].transport_db_path)
            self.assertNotEqual(contexts[0].chat_id, contexts[1].chat_id)
            for context in contexts:
                admin = Members(context).get('demo-admin')
                member = Members(context).get('demo-member')
                self.assertEqual(admin['status'], 'approved')
                self.assertEqual(admin['roles'], ['club_admin'])
                self.assertEqual(member['status'], 'approved')
                self.assertEqual(member['roles'], [])
            self.assertTrue(all(not bot['configured'] for bot in bots))
            self.assertEqual(bootstrap(root, demo=True), credentials)
            self.assertEqual(credentials.read_text(encoding='utf-8'), saved)
            self.assertEqual(len(Platform(root).list(principal)), 2)


if __name__ == '__main__':
    unittest.main()
