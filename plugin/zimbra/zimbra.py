import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def zimbra_host(cfg):
    return str(cfg.get("zimbra_host") or cfg.get("host") or "").strip()


def zimbra_email(cfg):
    return str(cfg.get("zimbra_email") or cfg.get("email") or "").strip()


def zimbra_password(cfg):
    return str(cfg.get("zimbra_password") or cfg.get("password") or "").strip()


def require_zimbra_config(cfg):
    missing = []
    if not zimbra_host(cfg):
        missing.append("ZIMBRA_HOST")
    if not zimbra_email(cfg):
        missing.append("ZIMBRA_EMAIL")
    if not zimbra_password(cfg):
        missing.append("ZIMBRA_PASSWORD")
    if missing:
        raise ValueError("Missing transfer config: " + ", ".join(missing))


def _local_name(tag):
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


def zimbra_login(cfg):
    host = zimbra_host(cfg)
    account = html.escape(zimbra_email(cfg))
    password = html.escape(zimbra_password(cfg))
    root = soap_request(
        host,
        f"""<AuthRequest xmlns="urn:zimbraAccount">
  <account by="name">{account}</account>
  <password>{password}</password>
</AuthRequest>""",
    )
    token = next((elem.text for elem in root.iter() if _local_name(elem.tag) == "authToken"), "")
    if not token:
        raise RuntimeError("Zimbra login failed: auth token not found")
    return token


def upload_attachment(host, token, filename, data, content_type="application/octet-stream"):
    boundary = "----codex-zimbra-upload"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"https://{host}/service/upload?fmt=raw",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": f"ZM_AUTH_TOKEN={token}",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8", errors="replace")
    match = re.search(r'["\']?aid["\']?\s*[:=]\s*["\']([^"\']+)["\']', text)
    if match:
        return match.group(1)
    quoted = re.findall(r"'([^']+)'", text)
    if len(quoted) >= 2:
        return quoted[-1]
    raise RuntimeError(f"Zimbra upload failed: attachment id not found in response {text[:300]}")


def zimbra_move_message(host, token, message_id, folder_id):
    soap_request(
        host,
        (
            f'<MsgActionRequest xmlns="urn:zimbraMail">'
            f'<action id="{html.escape(message_id)}" op="move" l="{html.escape(str(folder_id))}"/>'
            f"</MsgActionRequest>"
        ),
        token,
    )


def _normalize_recipients(value):
    if isinstance(value, (list, tuple, set)):
        return [str(addr).strip() for addr in value if str(addr).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def zimbra_send_email(cfg, to, subject, body, attachments=None, folder_id=None, cc=None, content_type="text/plain"):
    require_zimbra_config(cfg)
    host = zimbra_host(cfg)
    token = zimbra_login(cfg)
    attach_ids = []
    for item in attachments or []:
        attach_ids.append(
            upload_attachment(
                host,
                token,
                item["filename"],
                item["data"],
                item.get("content_type", "application/octet-stream"),
            )
        )

    attach_xml = "".join(f'<attach aid="{html.escape(aid)}"/>' for aid in attach_ids)
    subject_text = str(subject or "").strip()
    recipients = _normalize_recipients(to)
    if not recipients:
        raise ValueError("Missing email recipient")
    to_xml = "".join(f'<e t="t" a="{html.escape(addr)}"/>' for addr in recipients)
    cc_xml = "".join(f'<e t="c" a="{html.escape(addr)}"/>' for addr in _normalize_recipients(cc))
    soap_request(
        host,
        f"""<SendMsgRequest xmlns="urn:zimbraMail">
  <m>
    {to_xml}
    {cc_xml}
    <su>{html.escape(subject_text)}</su>
    <mp ct="{html.escape(content_type)}"><content>{html.escape(str(body or ""))}</content></mp>
    {attach_xml}
  </m>
</SendMsgRequest>""",
        token,
    )

    dest = str(folder_id or "").strip()
    if not dest or dest == "2":
        return

    # Self-transfer mail lands in Inbox; move it into the configured receive folder.
    for attempt in range(8):
        for message_id in zimbra_search(host, token, "2", 20):
            message = zimbra_get_message(host, token, message_id)
            if message and (message.get("subject") or "").strip() == subject_text:
                zimbra_move_message(host, token, message_id, dest)
                return
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Transfer sent but message not found in Inbox to move to folder {dest}")


def zimbra_search(host, token, folder_id, limit, sort_by="dateDesc"):
    query = html.escape(f"inid:{folder_id}")
    sort = html.escape(str(sort_by or "dateDesc"))
    root = soap_request(
        host,
        f"""<SearchRequest xmlns="urn:zimbraMail" types="message" sortBy="{sort}" limit="{int(limit)}">
  <query>{query}</query>
</SearchRequest>""",
        token,
    )
    return [elem.get("id", "") for elem in root.iter() if _local_name(elem.tag) == "m" and elem.get("id")]


def zimbra_forward_as_is(cfg, message_id, to, cc=None):
    """Forward a message from the logged-in SOC account as HTML with Fwd: subject."""
    require_zimbra_config(cfg)
    host = zimbra_host(cfg)
    token = zimbra_login(cfg)
    recipients = _normalize_recipients(to)
    if not recipients:
        raise ValueError("Missing email recipient")
    msg_id = str(message_id or "").strip()
    if not msg_id:
        raise ValueError("Missing message id")

    message = zimbra_get_message(host, token, msg_id)
    if not message:
        raise RuntimeError(f"Message not found: {msg_id}")
    original_subject = (message.get("subject") or "").strip()
    if original_subject.lower().startswith("fwd:"):
        subject = original_subject
    else:
        subject = f"Fwd: {original_subject}" if original_subject else "Fwd:"

    original_html = (message.get("body_html") or "").strip()
    original_text = (message.get("body") or "").strip()
    if original_html:
        content_html = original_html
    elif original_text:
        content_html = html.escape(original_text).replace("\n", "<br>\n")
    else:
        content_html = ""

    intro_html = (
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 10pt;">\n'
        "Dear Cloudfall,<br>\n"
        "Please check, thanks.<br>\n"
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

    to_xml = "".join(f'<e t="t" a="{html.escape(addr)}"/>' for addr in recipients)
    cc_xml = "".join(f'<e t="c" a="{html.escape(addr)}"/>' for addr in _normalize_recipients(cc))
    root = soap_request(
        host,
        f"""<SendMsgRequest xmlns="urn:zimbraMail">
  <m>
    {to_xml}
    {cc_xml}
    <su>{html.escape(subject)}</su>
    <mp ct="text/html"><content>{html.escape(body)}</content></mp>
  </m>
</SendMsgRequest>""",
        token,
    )
    if next((e for e in root.iter() if _local_name(e.tag) == "Fault"), None) is not None:
        raise RuntimeError("Zimbra SendMsg forward returned a SOAP fault")
    return root


def _extract_message_bodies(msg_elem):
    plain_parts = []
    html_parts = []
    for elem in msg_elem.iter():
        if _local_name(elem.tag) != "mp":
            continue
        if elem.get("filename") or elem.get("cd") == "attachment":
            continue
        content_elem = next((child for child in list(elem) if _local_name(child.tag) == "content"), None)
        if content_elem is None or not (content_elem.text or "").strip():
            continue
        ct = (elem.get("ct") or "").lower()
        text = content_elem.text
        if ct.startswith("text/plain"):
            plain_parts.append(text)
        elif ct.startswith("text/html"):
            html_parts.append(text)
    return {
        "text": "\n".join(plain_parts).strip(),
        "html": "\n".join(html_parts).strip(),
    }


def zimbra_get_message(host, token, message_id):
    root = soap_request(
        host,
        f'<GetMsgRequest xmlns="urn:zimbraMail"><m id="{html.escape(message_id)}" html="1" needExp="1"/></GetMsgRequest>',
        token,
    )
    msg = next((elem for elem in root.iter() if _local_name(elem.tag) == "m" and elem.get("id") == message_id), None)
    if msg is None:
        return None

    subject_elem = next((elem for elem in msg.iter() if _local_name(elem.tag) == "su"), None)
    addresses = []
    attachments = []
    for elem in msg.iter():
        name = _local_name(elem.tag)
        if name == "e":
            addresses.append({"type": elem.get("t", ""), "email": elem.get("a", "")})
        elif name == "mp" and (elem.get("filename") or elem.get("cd") == "attachment"):
            attachments.append(
                {
                    "filename": elem.get("filename", ""),
                    "part": elem.get("part", ""),
                    "content_type": elem.get("ct", ""),
                }
            )

    bodies = _extract_message_bodies(msg)
    return {
        "id": message_id,
        "subject": (subject_elem.text if subject_elem is not None else "") or "",
        "from": next((a["email"] for a in addresses if a["type"] == "f"), ""),
        "to": [a["email"] for a in addresses if a["type"] == "t"],
        "body": bodies["text"],
        "body_html": bodies["html"],
        "attachments": attachments,
    }


def download_attachment(cfg, token, message_id, part):
    host = zimbra_host(cfg)
    account = urllib.parse.quote(zimbra_email(cfg), safe="")
    query = urllib.parse.urlencode({"id": message_id, "part": part})
    request = urllib.request.Request(
        f"https://{host}/home/{account}/?{query}",
        headers={"Cookie": f"ZM_AUTH_TOKEN={token}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def zimbra_delete_message(host, token, message_id):
    soap_request(
        host,
        f'<MsgActionRequest xmlns="urn:zimbraMail"><action id="{html.escape(message_id)}" op="delete"/></MsgActionRequest>',
        token,
    )
