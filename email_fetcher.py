# email_fetcher.py
import imaplib
import email
from email.header import decode_header
import datetime
import requests
import os
import logging

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
OUTLOOK_USER = os.environ.get("OUTLOOK_USER")
LL_PRO_3_ALERT_SUBJECT = os.environ.get("LL_PRO_3_ALERT_SUBJECT")
WEBHOOK_URL = "https://yourserver.com/webhooks/from-outlook"


def _decode(value):
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = ""
    for part, enc in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += part
    return result


def fetch_all_matching_emails():
    """
    Return ALL emails from the INBOX that match:
     - FROM = OUTLOOK_USER
     - SUBJECT = LL_PRO_3_ALERT_SUBJECT
    """
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=7)).strftime("%d-%b-%Y")  # look back 1 week

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    search_criteria = f'(FROM "{OUTLOOK_USER}" SUBJECT "{LL_PRO_3_ALERT_SUBJECT}" SINCE "{since}")'
    status, data = mail.search(None, search_criteria)

    if status != "OK":
        logging.warning(f"[EMAIL POLL] IMAP search failed: {status} {data}")
        mail.logout()
        return []

    ids = data[0].split()
    results = []

    for msg_id in ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        item = {
            "from": _decode(msg.get("From")),
            "subject": _decode(msg.get("Subject")),
            "date": msg.get("Date"),
            "text": "",
            "html": "",
        }

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        item["text"] += payload.decode(errors="ignore")
                elif ctype == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        item["html"] += payload.decode(errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                ctype = msg.get_content_type()
                if ctype == "text/plain":
                    item["text"] = payload.decode(errors="ignore")
                elif ctype == "text/html":
                    item["html"] = payload.decode(errors="ignore")

        results.append(item)

    mail.logout()
    return results


def extract_alert_payload(text: str) -> str:
    """
    Extracts everything between:
        'Hi jimmy,' and '// Larsson Line Pro'
    Normalizes indentation and removes empty lines.
    """
    if not text:
        return ""

    start_marker = "Hi jimmy,"
    end_marker = "// Larsson Line Pro"

    start_index = text.find(start_marker)
    if start_index == -1:
        return ""

    start_index += len(start_marker)

    end_index = text.find(end_marker, start_index)
    if end_index == -1:
        end_index = len(text)

    # Extract and normalize
    section = text[start_index:end_index]

    # Strip whitespace + remove blank lines
    lines = [line.strip() for line in section.splitlines() if line.strip()]

    return "\n".join(lines)


def send_to_webhook(payload):
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        logging.info(f"[EMAIL POLL] Webhook response: {r.status_code}")
    except Exception as e:
        logging.error(f"[EMAIL POLL] Webhook error: {e}")
