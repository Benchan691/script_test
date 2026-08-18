import datetime as dt
import html
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


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


def local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def soap_request(host, body_xml, auth_token=""):
    header = f"<authToken>{html.escape(auth_token)}</authToken>" if auth_token else ""
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Header><context xmlns="urn:zimbra">{header}</context></soap:Header>
  <soap:Body>{body_xml}</soap:Body>
</soap:Envelope>
"""
    request = urllib.request.Request(
        f"https://{host}/service/soap",
        data=envelope.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return ET.fromstring(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Zimbra SOAP request failed ({exc.code}): {detail or exc.reason}") from exc


def zimbra_login(host, email, password):
    root = soap_request(
        host,
        f"""<AuthRequest xmlns="urn:zimbraAccount">
  <account by="name">{html.escape(email)}</account>
  <password>{html.escape(password)}</password>
</AuthRequest>""",
    )
    for elem in root.iter():
        if local_name(elem.tag) == "authToken" and elem.text:
            return elem.text
    raise RuntimeError("Zimbra login failed: auth token not found")


def get_folder_id(host, token, target_name):
    root = soap_request(
        host,
        '<GetFolderRequest xmlns="urn:zimbraMail" visible="1"><folder l="1" recursive="1"/></GetFolderRequest>',
        token,
    )
    for elem in root.iter():
        if local_name(elem.tag) == "folder" and elem.get("name") == target_name:
            return elem.get("id", "")
    return ""


def get_time_window(now=None):
    now = now or dt.datetime.now().astimezone()
    today = now.date()
    start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(18, 0), tzinfo=now.tzinfo)
    end = dt.datetime.combine(today, dt.time(9, 0), tzinfo=now.tzinfo)
    return start, end


def list_messages_in_folder(host, token, folder_id, limit):
    query = html.escape(f"inid:{folder_id}")
    root = soap_request(
        host,
        f"""<SearchRequest xmlns="urn:zimbraMail" types="message" sortBy="dateDesc" limit="{int(limit)}">
  <query>{query}</query>
</SearchRequest>""",
        token,
    )
    messages = []
    for elem in root.iter():
        if local_name(elem.tag) != "m":
            continue
        stamp = elem.get("d")
        if not stamp:
            continue
        try:
            received_at = dt.datetime.fromtimestamp(int(stamp) / 1000, tz=dt.timezone.utc).astimezone()
        except ValueError:
            continue
        messages.append(received_at)
    return messages


def has_messages_in_window(host, token, folder_name, start, end):
    folder_id = get_folder_id(host, token, folder_name)
    if not folder_id:
        return False
    for received_at in list_messages_in_folder(host, token, folder_id, SEARCH_LIMIT):
        if start <= received_at <= end:
            return True
    return False


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
    email = cfg["email"]
    password = cfg["password"]
    host = cfg["host"]

    if not email or not password or not host:
        return False

    token = zimbra_login(host, email, password)
    start, end = get_time_window(now=now)
    return has_messages_in_window(host, token, folder_name, start, end)


def main():
    try:
        print("success" if has_target_email() else "can't")
        return 0
    except Exception:
        print("can't")
        return 1


if __name__ == "__main__":
    sys.exit(main())
