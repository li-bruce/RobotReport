"""Initialize an administrator password without placing it in shell history."""
import argparse
import getpass
from pathlib import Path
from app.security import Platform
from app.password_auth import PasswordAuth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='state/local')
    args = parser.parse_args()
    manager = PasswordAuth(Platform(Path(args.state)))
    if manager.initialized():
        raise SystemExit('密码已初始化；请登录后台修改密码。')
    password = getpass.getpass('Initial administrator password: ')
    if password != getpass.getpass('Repeat password: '):
        raise SystemExit('两次密码不一致，未修改。')
    manager.set_initial(password)
    print('管理员密码初始化完成。')


if __name__ == '__main__':
    main()
