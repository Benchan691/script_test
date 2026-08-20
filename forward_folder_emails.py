#!/usr/bin/env python3
"""Watch a Zimbra folder and forward new messages as-is to configured recipients."""

import argparse
import json
import os
import sys
import urllib.parse

from plugin.zimbra import (
    require_zimbra_config,
    zimbra_forward_as_is,
    zimbra_host,
    zimbra_login,
    zimbra_search,
)


BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "forwarded_ids.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")
SEARCH_LIMIT = 250


def load_env(path):
    data = {}
    if not os.path.exists(path):
        return data
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


def load_runtime_config():
    env = load_env(ENV_PATH)
    return {
        "email": env.get("SEND_EMAIL_USER", "").strip(),
        "password": env.get("SEND_EMAIL_PASSWORD", "").strip(),
        "host": normalize_host(env.get("SEND_EMAIL_HOST", "")),
    }


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_forwarded_ids(path=STATE_PATH):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    ids = data.get("ids") if isinstance(data, dict) else data
    if not isinstance(ids, list):
        return set()
    return {str(item).strip() for item in ids if str(item).strip()}


def save_forwarded_ids(ids, path=STATE_PATH):
    payload = {"ids": sorted(str(item) for item in ids if str(item).strip())}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def validate_app_config(config):
    folder_id = str(config.get("folder_id") or "").strip()
    if not folder_id:
        raise ValueError("config.json: folder_id is required")

    receivers = config.get("receiver_email") or []
    if isinstance(receivers, str):
        receivers = [receivers]
    recipients = [str(addr).strip() for addr in receivers if str(addr).strip()]
    if not recipients:
        raise ValueError("config.json: receiver_email must contain at least one address")

    return folder_id, recipients


def run(dry_run=False):
    app_config = load_config()
    folder_id, recipients = validate_app_config(app_config)
    zimbra_cfg = load_runtime_config()
    require_zimbra_config(zimbra_cfg)

    host = zimbra_host(zimbra_cfg)
    token = zimbra_login(zimbra_cfg)
    message_ids = [
        mid for mid in zimbra_search(host, token, folder_id, SEARCH_LIMIT, sort_by="dateAsc") if mid
    ]

    forwarded_ids = load_forwarded_ids()
    to_forward = [mid for mid in message_ids if mid not in forwarded_ids]
    skipped = len(message_ids) - len(to_forward)

    # #region agent log
    try:
        import time as _time
        with open("/Users/chankokpan/Documents/script/.cursor/debug-3516ad.log", "a", encoding="utf-8") as _dbg:
            _dbg.write(json.dumps({"sessionId": "3516ad", "hypothesisId": "D_E", "location": "forward_folder_emails.py:run", "message": "run inventory", "data": {"folderId": folder_id, "found": len(message_ids), "toForward": len(to_forward), "skipped": skipped, "stateCount": len(forwarded_ids), "recipientCount": len(recipients), "dryRun": dry_run, "messageIds": message_ids[:20], "toForwardIds": to_forward[:20]}, "timestamp": int(_time.time() * 1000)}) + "\n")
    except Exception:
        pass
    # #endregion

    if dry_run:
        print(f"dry-run: would forward {len(to_forward)}, skip {skipped}")
        for mid in to_forward:
            print(f"  {mid}")
        return 0

    forwarded = 0
    for mid in to_forward:
        try:
            zimbra_forward_as_is(zimbra_cfg, mid, recipients)
        except Exception as exc:
            # #region agent log
            try:
                import time as _time
                with open("/Users/chankokpan/Documents/script/.cursor/debug-3516ad.log", "a", encoding="utf-8") as _dbg:
                    _dbg.write(json.dumps({"sessionId": "3516ad", "hypothesisId": "A_B", "location": "forward_folder_emails.py:run", "message": "forward exception", "data": {"messageId": mid, "errorType": type(exc).__name__, "error": str(exc)[:800]}, "timestamp": int(_time.time() * 1000)}) + "\n")
            except Exception:
                pass
            # #endregion
            print(f"failed to forward {mid}: {exc}", file=sys.stderr)
            save_forwarded_ids(forwarded_ids)
            print(f"forwarded {forwarded}, skipped {skipped}, failed on {mid}")
            return 1
        forwarded_ids.add(mid)
        forwarded += 1
        save_forwarded_ids(forwarded_ids)
        # #region agent log
        try:
            import time as _time
            with open("/Users/chankokpan/Documents/script/.cursor/debug-3516ad.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(json.dumps({"sessionId": "3516ad", "hypothesisId": "D", "location": "forward_folder_emails.py:run", "message": "marked forwarded", "data": {"messageId": mid, "statePath": STATE_PATH, "stateCount": len(forwarded_ids)}, "timestamp": int(_time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion

    print(f"forwarded {forwarded}, skipped {skipped}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Forward new Zimbra folder emails as-is.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List messages that would be forwarded; do not send or update state.",
    )
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
