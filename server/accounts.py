import argparse
import getpass
import re

from sqlalchemy import delete, select

from .db import LoginSession, User, make_sessions, passwords


def main():
    parser = argparse.ArgumentParser(description="Управление учётными записями «Подмены»")
    parser.add_argument("action", choices=("create", "reset", "block", "unblock"))
    parser.add_argument("username")
    args = parser.parse_args()
    username = args.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@+\-]{1,79}", username):
        parser.error("Укажите логин длиной 2–80 символов (латиница, цифры, . _ @ + -).")
    with make_sessions().begin() as db:
        user = db.scalar(select(User).where(User.username == username).with_for_update())
        if (user is None) != (args.action == "create"):
            parser.error("Проверьте, существует ли такая учётная запись.")
        if args.action in ("create", "reset"):
            password = getpass.getpass("Пароль (не менее 12 символов): ")
            if not 12 <= len(password) <= 256 or password != getpass.getpass("Повторите пароль: "):
                parser.error("Пароли не совпадают или длина недопустима.")
            hashed = passwords.hash(password)
            if user is None:
                db.add(User(username=username, password_hash=hashed))
            else:
                user.password_hash = hashed
        else:
            user.active = args.action == "unblock"
        if user is not None:
            db.execute(delete(LoginSession).where(LoginSession.owner == user.id))
    print("Готово.")


if __name__ == "__main__":
    main()
