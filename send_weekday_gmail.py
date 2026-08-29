import argparse
import datetime as dt
import json
import os
from string import Template

from zimbra_client import ZimbraClient

from search_zimbra_folder import has_target_email, load_runtime_config


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "gmail.html")
CC_RECIPIENTS = ["ken.wk.ho@citictel-cpc.com"]
DRY_RUN_TEST_RECIPIENT = "ben.chan@citictel-cpc.com"
TEMPLATE_VARIABLES = {
    "{{ email_subject }}": "${email_subject}",
    "{{ g43839_alert_status }}": "${g43839_alert_status}",
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_weekday_recipients(config, current_time=None):
    current_time = current_time or dt.datetime.now().astimezone()
    weekday = current_time.weekday()
    if weekday >= 5:
        raise ValueError("No handover email is sent on weekends.")

    receiver_email = config.get("receiver_email") or []
    day_indexes = config.get("days") or []
    if weekday >= len(day_indexes):
        raise ValueError(f"Missing recipient mapping for weekday index {weekday}.")

    recipients = []
    for raw_index in day_indexes[weekday]:
        index = int(raw_index)
        try:
            address = str(receiver_email[index]).strip()
        except IndexError as exc:
            raise ValueError(f"Recipient index {index} is out of range.") from exc
        if address:
            recipients.append(address)

    if not recipients:
        raise ValueError(f"No recipients configured for weekday index {weekday}.")
    return recipients


def build_subject(current_time=None):
    current_time = current_time or dt.datetime.now().astimezone()
    return f"[Internal] Handover ({current_time:%d %b 0900})"


def render_template(subject, g43839_alert_status, template_path=TEMPLATE_PATH):
    with open(template_path, "r", encoding="utf-8") as handle:
        template_text = handle.read()
    for placeholder, variable in TEMPLATE_VARIABLES.items():
        template_text = template_text.replace(placeholder, variable)
    template = Template(template_text)
    return template.substitute(
        email_subject=subject,
        g43839_alert_status=g43839_alert_status,
    )


def build_message(current_time=None):
    current_time = current_time or dt.datetime.now().astimezone()
    config = load_config(CONFIG_PATH)
    recipients = build_weekday_recipients(config, current_time=current_time)
    subject = build_subject(current_time=current_time)
    alert_status = "Y" if has_target_email(now=current_time) else "Y*"
    html_body = render_template(subject, alert_status)
    return {
        "to": recipients,
        "cc": CC_RECIPIENTS,
        "subject": subject,
        "body": html_body,
        "alert_status": alert_status,
    }


def _send_html_message(to, cc, subject, body):
    cfg = load_runtime_config()
    with ZimbraClient({**cfg, "verify_ssl": True}) as client:
        client.send_message(to=to, cc=cc, subject=subject, html=body)


def send_message(message):
    _send_html_message(
        to=message["to"],
        cc=message["cc"],
        subject=message["subject"],
        body=message["body"],
    )


def send_dry_run_message(message):
    _send_html_message(
        to=[DRY_RUN_TEST_RECIPIENT],
        cc=[],
        subject=f"{message['subject']} [TEST]",
        body=message["body"],
    )


def main():
    parser = argparse.ArgumentParser(description="Send the weekday handover email.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Send a test email to Ben only, without cc.",
    )
    args = parser.parse_args()

    message = build_message()
    if args.dry_run:
        send_dry_run_message(message)
        print("sent test to:", DRY_RUN_TEST_RECIPIENT)
        return 0

    send_message(message)
    print("sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
