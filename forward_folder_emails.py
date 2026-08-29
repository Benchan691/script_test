#!/usr/bin/env python3
"""Watch a Zimbra folder and forward new messages as-is to configured recipients."""

import argparse
import html
import json
import os
import sys
import urllib.parse

from zimbra_client import Recipient, ZimbraClient
from zimbra_client.mail import build_send_message_request


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


def _normalize_address_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(addr).strip() for addr in value if str(addr).strip()]


def validate_app_config(config):
    folder_id = str(config.get("folder_id") or "").strip()
    if not folder_id:
        raise ValueError("config.json: folder_id is required")

    recipients = _normalize_address_list(config.get("receiver_email") or [])
    if not recipients:
        raise ValueError("config.json: receiver_email must contain at least one address")

    cc_recipients = _normalize_address_list(config.get("cc") or [])
    return folder_id, recipients, cc_recipients


def _forward_message_as_is(client, message_id, recipients, cc_recipients):
    message = client.get_message(message_id)
    original_subject = (message.subject or "").strip()
    if original_subject.lower().startswith("fwd:"):
        subject = original_subject
    else:
        subject = f"Fwd: {original_subject}" if original_subject else "Fwd:"

    original_html = (message.body_html or "").strip()
    original_text = (message.body_text or "").strip()
    if original_html:
        content_html = original_html
    elif original_text:
        content_html = html.escape(original_text).replace("\n", "<br>\n")
    else:
        content_html = ""

    intro_html = (
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 10pt;">\n'
        "Dear Cloudfall,<br>\n"
        "\n"
        "Please check, thanks.<br>\n"
        "\n"
        "Best regards,<br>\n"
        "Security Services Delivery and Operation<br>\n"
        "CITIC Telecom International CPC Limited<br>\n"
        "中信國際電訊(信息技術)有限公司<br>\n"
        "<br>\n"
        "20/F, AXA Tower, Landmark East, 100 How Ming Street, Kwun Tong, Kowloon, Hong Kong<br>\n"
        "D: (852) 2331 8930&nbsp;&nbsp;&nbsp;F: (852) 2811 2853\n"
        "</div>\n"
    )
    body = (
        f"{intro_html}"
        '<hr style="border:none;border-top:1px solid #000;margin:12px 0;">\n'
        f"{content_html}"
    )

    attached_message_parts = []
    for attachment in message.attachments:
        if not attachment.part:
            raise RuntimeError("Source attachment did not include a MIME part id")
        attached_message_parts.append((message.id, attachment.part))

    request = build_send_message_request(
        to=tuple(Recipient(email=address, type="t") for address in recipients),
        cc=tuple(Recipient(email=address, type="c") for address in cc_recipients),
        subject=subject,
        html=body,
        original_id=message.id,
        reply_type="w",
        attached_message_parts=tuple(attached_message_parts),
    )
    client.request(request)


def run(dry_run=False):
    app_config = load_config()
    folder_id, recipients, cc_recipients = validate_app_config(app_config)
    zimbra_cfg = load_runtime_config()
    forwarded_ids = load_forwarded_ids()

    with ZimbraClient({**zimbra_cfg, "verify_ssl": True}) as client:
        message_ids = [
            message.id
            for message in client.search_messages(
                folder_id=folder_id,
                limit=SEARCH_LIMIT,
                sort_by="dateAsc",
            ).messages
            if message.id
        ]

        to_forward = [mid for mid in message_ids if mid not in forwarded_ids]
        skipped = len(message_ids) - len(to_forward)

        if dry_run:
            print(f"dry-run: would forward {len(to_forward)}, skip {skipped}")
            print(f"to: {', '.join(recipients)}")
            print(f"cc: {', '.join(cc_recipients) if cc_recipients else '(none)'}")
            for mid in to_forward:
                print(f"  {mid}")
            return 0

        forwarded = 0
        for mid in to_forward:
            try:
                _forward_message_as_is(client, mid, recipients, cc_recipients)
            except Exception as exc:
                print(f"failed to forward {mid}: {exc}", file=sys.stderr)
                save_forwarded_ids(forwarded_ids)
                print(f"forwarded {forwarded}, skipped {skipped}, failed on {mid}")
                return 1
            forwarded_ids.add(mid)
            forwarded += 1
            save_forwarded_ids(forwarded_ids)

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
