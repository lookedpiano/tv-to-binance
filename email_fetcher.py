# email_fetcher.py
import imaplib
import email
from email.header import decode_header
import datetime
import requests
import os
import logging

IMAP_SERVER = "outlook.office365.com"
IMAP_PORT = 993

EMAIL_USER = "jimmy.friedrich@hotmail.ch"
EMAIL_PASS = os.environ.get("OUTLOOK_APP_PASSWORD")  # use an App Password if you have 2FA
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
    """Fetch today's email from guru@ctolarsson.com via IMAP."""
    today = datetime.date.today().strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")

    search = f'(FROM "guru@ctolarsson.com" ON "{today}")'
    status, data = mail.search(None, search)

    if status != "OK":
        mail.logout()
        return None

    ids = data[0].split()
    if not ids:
        mail.logout()
        return None

    latest_id = ids[-1]
    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    if status != "OK":
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
            if ctype == "text/plain":
                email_data["text"] += (part.get_payload(decode=True).decode(errors="ignore"))
            elif ctype == "text/html":
                email_data["html"] += (part.get_payload(decode=True).decode(errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            email_data["text"] = payload.decode(errors="ignore")

    mail.logout()
    return email_data


def send_to_webhook(payload):
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        logging.info(f"[EMAIL POLL] Webhook response: {r.status_code}")
    except Exception as e:
        logging.error(f"[EMAIL POLL] Webhook error: {e}")
