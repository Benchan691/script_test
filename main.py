#!/usr/bin/env python3
"""Watch Zimbra folders and forward unread messages as-is to configured recipients."""

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


def _normalize_id_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_address_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(addr).strip() for addr in value if str(addr).strip()]


def _recipient_group(config, key):
    group = config.get(key)
    if not isinstance(group, dict):
        group = {}
    return (
        _normalize_address_list(group.get("to") or []),
        _normalize_address_list(group.get("cc") or []),
    )


def validate_app_config(config, *, require_test=False):
    folder_ids = _normalize_id_list(config.get("folder_id"))
    if not folder_ids:
        raise ValueError("config.json: folder_id is required")

    official_to, official_cc = _recipient_group(config, "official")
    if not official_to:
        raise ValueError("config.json: official.to must contain at least one address")

    test_to, test_cc = _recipient_group(config, "test")
    if require_test and not test_to:
        raise ValueError("config.json: test.to must contain at least one address")

    return folder_ids, official_to, official_cc, test_to, test_cc


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


def _collect_messages(client, folder_ids, *, dry_run):
    to_forward = []
    for folder_id in folder_ids:
        if dry_run:
            result = client.search_messages(
                folder_id=folder_id,
                limit=1,
                sort_by="dateAsc",
            )
        else:
            result = client.search_messages(
                query="is:unread",
                folder_id=folder_id,
                limit=SEARCH_LIMIT,
                sort_by="dateAsc",
            )
        for message in result.messages:
            if message.id:
                to_forward.append((folder_id, message.id))
    return to_forward


def run(dry_run=False, test=False):
    if dry_run and test:
        raise ValueError("--dry-run and --test cannot be used together")

    app_config = load_config()
    folder_ids, official_to, official_cc, test_to, test_cc = validate_app_config(
        app_config,
        require_test=dry_run or test,
    )
    zimbra_cfg = load_runtime_config()
    use_test = dry_run or test
    recipients = test_to if use_test else official_to
    cc_recipients = test_cc if use_test else official_cc
    mode_label = "dry-run" if dry_run else ("test" if test else "official")

    with ZimbraClient({**zimbra_cfg, "verify_ssl": True}) as client:
        to_forward = _collect_messages(client, folder_ids, dry_run=dry_run)
        print(f"{mode_label}: forwarding {len(to_forward)}")
        print(f"to: {', '.join(recipients)}")
        print(f"cc: {', '.join(cc_recipients) if cc_recipients else '(none)'}")

        forwarded = 0
        for folder_id, mid in to_forward:
            try:
                _forward_message_as_is(client, mid, recipients, cc_recipients)
            except Exception as exc:
                print(f"failed to forward {folder_id} {mid}: {exc}", file=sys.stderr)
                print(f"forwarded {forwarded}, failed on {folder_id} {mid}")
                return 1
            if not use_test:
                try:
                    client.mark_read(mid)
                except Exception as exc:
                    print(f"failed to mark read {folder_id} {mid}: {exc}", file=sys.stderr)
                    print(f"forwarded {forwarded + 1}, failed to mark read {folder_id} {mid}")
                    return 1
            forwarded += 1
            print(f"  {folder_id} {mid}")

        print(f"forwarded {forwarded}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Forward unread Zimbra folder emails as-is.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Send the first (oldest) message from each folder to test recipients. Does not mark read.",
    )
    mode.add_argument(
        "--test",
        action="store_true",
        help="Forward all unread messages to test recipients. Does not mark read.",
    )
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run, test=args.test)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
