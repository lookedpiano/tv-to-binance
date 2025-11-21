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

EMAIL_USER = "blackwhalevoices@gmail.com"
EMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD")
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


def fetch_latest_guru_email():
    """
    Fetch the latest email from guru@ctolarsson.com
    whose subject contains 'Pro 3 Alert'.
    """
    # Optional: only look at emails since yesterday to avoid scanning the whole inbox
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=1)).strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    logging.info(f"[DEBUG] EMAIL_PASS length = {len(EMAIL_PASS) if EMAIL_PASS else 'None'}")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")

    # This matches:
    #  - from guru@ctolarsson.com
    #  - subject contains "Pro 3 Alert"
    #  - received SINCE yesterday
    #search_criteria = f'(FROM "guru@ctolarsson.com" SUBJECT "Pro 3 Alert" SINCE "{since}")'
    search_criteria = f'(FROM "jimmy.friedrich@hotmail.ch" SUBJECT "Larsson Line Pro 3 Alert" SINCE "{since}")'
    status, data = mail.search(None, search_criteria)

    if status != "OK":
        logging.warning(f"[EMAIL POLL] IMAP search failed: {status} {data}")
        mail.logout()
        return None

    ids = data[0].split()
    if not ids:
        logging.info("[EMAIL POLL] No matching emails found (guru + 'Pro 3 Alert').")
        mail.logout()
        return None

    # Take the newest matching email
    latest_id = ids[-1]
    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    if status != "OK":
        logging.warning(f"[EMAIL POLL] IMAP fetch failed: {status} {msg_data}")
        mail.logout()
        return None

    msg = email.message_from_bytes(msg_data[0][1])

    email_data = {
        "from": _decode(msg.get("From")),
        "subject": _decode(msg.get("Subject")),
        "date": msg.get("Date"),
        "text": "",
        "html": "",
    }

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")

            if "attachment" in disp.lower():
                continue

            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    email_data["text"] += payload.decode(errors="ignore")
            elif ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    email_data["html"] += payload.decode(errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            if msg.get_content_type() == "text/html":
                email_data["html"] = payload.decode(errors="ignore")
            else:
                email_data["text"] = payload.decode(errors="ignore")

    mail.logout()
    return email_data


def send_to_webhook(payload):
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        logging.info(f"[EMAIL POLL] Webhook response: {r.status_code}")
    except Exception as e:
        logging.error(f"[EMAIL POLL] Webhook error: {e}")
