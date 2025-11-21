# email_poll.py
import time
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from email_fetcher import fetch_latest_guru_email, send_to_webhook

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
                logging.info(f"[EMAIL POLL] Email found — subject: {email_data.get('subject')}")
                send_to_webhook(email_data)
            else:
                logging.info("[EMAIL POLL] No matching email found at this time.")

        except Exception as e:
            logging.exception(f"[EMAIL POLL] Error during email check: {e}")

        logging.info(f"[EMAIL POLL] Sleeping {POLL_INTERVAL/3600:.1f} hours...")
        time.sleep(POLL_INTERVAL)


def start_email_polling_thread():
    """Start background thread for periodic email polling."""
    t = threading.Thread(target=_email_poll_loop, daemon=True, name="EmailPollingThread")
    t.start()
    logging.info("[EMAIL POLL] Started email polling thread.")
