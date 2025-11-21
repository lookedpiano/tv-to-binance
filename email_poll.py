# email_poll.py
import time
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from email_fetcher import fetch_latest_guru_email, extract_alert_payload, send_to_webhook
from binance_data import get_redis

from config._settings import (
    ENABLE_EMAIL_POLL,
)

TZ = ZoneInfo("Europe/Zurich")
POLL_INTERVAL = 3600 * 6   # 6 hours


def _email_poll_loop():
    """Background loop: check Outlook inbox every 6 hours."""
    logging.info("[EMAIL POLL] Background email checker started.")

    while True:
        try:
            logging.info("[EMAIL POLL] Checking for today's email from guru...")
            email_data = fetch_latest_guru_email()

            if email_data:
                alert_text = extract_alert_payload(email_data.get("text", ""))

                if alert_text:
                    r = get_redis()
                    r.set("larsson_alert_latest", alert_text)
                    logging.info("[EMAIL POLL] Stored alert in Redis.")

                logging.info("[EMAIL POLL] Extracted alert payload:")
                logging.info(alert_text)

                # Store to Redis or send to webhook etc.
                #send_to_webhook(email_data)
            else:
                logging.info("[EMAIL POLL] No matching email found.")

        except Exception as e:
            logging.exception(f"[EMAIL POLL] Error during email check: {e}")

        logging.info(f"[EMAIL POLL] Sleeping for {POLL_INTERVAL/3600:.1f} hours...")
        time.sleep(POLL_INTERVAL)


def start_email_polling_thread():
    """Start background thread for periodic email polling only if ENABLE_EMAIL_POLL is set."""
    if not ENABLE_EMAIL_POLL:
        #logging.info("[EMAIL POLL] Skipping — ENABLE_EMAIL_POLL not set.")
        return

    t = threading.Thread(target=_email_poll_loop, daemon=True, name="EmailPollingThread")
    t.start()
    logging.info("[EMAIL POLL] Started email polling thread (ENABLE_EMAIL_POLL active).")
