import datetime as dt
import os
import sys
import urllib.parse

from zimbra_client import ZimbraClient


TARGET_FOLDER_NAME = "G43839 Alert (Please Ignore)"
SEARCH_LIMIT = 250


def load_env(path):
    data = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def normalize_host(raw_host):
    value = (raw_host or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme:
        return parsed.netloc.rstrip("/")
    return value.rstrip("/").removeprefix("https://").removeprefix("http://")


def get_time_window(now=None):
    now = now or dt.datetime.now().astimezone()
    today = now.date()
    start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(18, 0), tzinfo=now.tzinfo)
    end = dt.datetime.combine(today, dt.time(9, 0), tzinfo=now.tzinfo)
    return start, end


def load_runtime_config():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env = load_env(env_path)
    return {
        "email": env.get("SEND_EMAIL_USER", "").strip(),
        "password": env.get("SEND_EMAIL_PASSWORD", "").strip(),
        "host": normalize_host(env.get("SEND_EMAIL_HOST", "")),
    }


def has_target_email(now=None, folder_name=TARGET_FOLDER_NAME):
    cfg = load_runtime_config()
    if not cfg["email"] or not cfg["password"] or not cfg["host"]:
        return False

    start, end = get_time_window(now=now)
    with ZimbraClient({**cfg, "verify_ssl": True}) as client:
        folder_id = next(
            (folder.id for folder in client.list_folders() if folder.name == folder_name),
            "",
        )
        if not folder_id:
            return False

        messages = client.search_messages(folder_id=folder_id, limit=SEARCH_LIMIT).messages
        return any(
            message.date is not None and start <= message.date <= end
            for message in messages
        )


def main():
    try:
        print("success" if has_target_email() else "can't")
        return 0
    except Exception:
        print("can't")
        return 1


if __name__ == "__main__":
    sys.exit(main())
