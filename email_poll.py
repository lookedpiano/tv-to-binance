import json
import time
import logging
import threading
import email
from datetime import datetime
from zoneinfo import ZoneInfo

from email_fetcher import fetch_all_matching_emails, extract_alert_payload, send_to_webhook
from binance_data import get_redis

from config._settings import (
    ENABLE_EMAIL_POLL,
)

TZ = ZoneInfo("Europe/Zurich")
POLL_INTERVAL = 3593 * 5   # approx. 5 hours


def _email_poll_loop():
    logging.info("[EMAIL POLL] Background email checker started.")
    r = get_redis()

    while True:
        try:
            logging.info("[EMAIL POLL] Fetching all matching emails...")
            emails = fetch_all_matching_emails()

            for email_item in emails:
                text = email_item.get("text", "")
                payload = extract_alert_payload(text)
                if not payload:
                    continue

                # Extract email date → normalize to YYYY-MM-DD
                email_dt = email.utils.parsedate_to_datetime(email_item["date"])
                date_str = email_dt.strftime("%Y-%m-%d")

                # Check if this day was already processed
                last_day = r.get("larsson_alert_last_day")
                if last_day == date_str:
                    logging.info(f"[EMAIL POLL] Skipping duplicate for {date_str}")
                    continue

                # Store new daily alert
                record = {
                    "timestamp": email_dt.timestamp(),
                    "date": date_str,
                    "subject": email_item["subject"],
                    "payload": payload
                }

                r.rpush("larsson_alerts", json.dumps(record))
                r.set("larsson_alert_last_day", date_str)

                logging.info(f"[EMAIL POLL] Stored new alert for {date_str}")

        except Exception as e:
            logging.exception(f"[EMAIL POLL] Error during email check: {e}")

        logging.info(f"[EMAIL POLL] Sleeping for {POLL_INTERVAL/3600:.1f} hours...")
        time.sleep(POLL_INTERVAL)


def start_email_polling_thread():
    """Start background thread for periodic email polling only if ENABLE_EMAIL_POLL is set."""
    if not ENABLE_EMAIL_POLL:
        return

    t = threading.Thread(target=_email_poll_loop, daemon=True, name="EmailPollingThread")
    t.start()
    logging.info("[EMAIL POLL] Started email polling thread (ENABLE_EMAIL_POLL active).")
