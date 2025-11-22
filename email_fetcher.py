import imaplib
import email
from email.header import decode_header
import datetime
import os
import logging

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
OUTLOOK_USER = os.environ.get("OUTLOOK_USER")
ALERT_SUBJECT_KEYWORD = "Larsson Line Pro 3 Alert"


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


def fetch_all_alert_emails():
    """
    Fetch and return all matching alert emails:
    - from OUTLOOK_USER
    - subject contains 'Larsson Line Pro 3 Alert'
    - within last 7 days
    """
    since_days = 7
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=since_days)).strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    search_criteria = f'(FROM "{OUTLOOK_USER}" SINCE "{since}")'
    status, data = mail.search(None, search_criteria)

    if status != "OK":
        logging.warning(f"[EMAIL] Search failed: {status} {data}")
        mail.logout()
        return []

    ids = data[0].split()
    alerts = []

    for msg_id in ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        sender = _decode(msg.get("From", ""))
        subject = _decode(msg.get("Subject", ""))
        date = _decode(msg.get("Date", ""))

        logging.info("----- EMAIL -----")
        logging.info(f"FROM:    {sender}")
        logging.info(f"SUBJECT: {subject}")
        logging.info(f"DATE:    {date}")
        logging.info("-----------------")

        # Filter again because Gmail search is weak
        if OUTLOOK_USER.lower() not in sender.lower():
            continue

        if ALERT_SUBJECT_KEYWORD.lower() not in subject.lower():
            continue  # skip irrelevant mail

        item = {
            "from": sender,
            "subject": subject,
            "date": msg.get("Date"),
            "date2": date,
            "text": "",
            "html": "",
        }

        # Extract body
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

        alerts.append(item)

    mail.logout()
    return alerts

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
