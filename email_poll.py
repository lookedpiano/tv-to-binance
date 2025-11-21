import json
import os
import hashlib
import time
import logging
import threading
import email
from datetime import datetime
from zoneinfo import ZoneInfo
from email_fetcher import fetch_all_matching_emails, extract_alert_payload

TZ = ZoneInfo("Europe/Zurich")
POLL_INTERVAL = 3593 * 5   # approx. 5 hours

EMAIL_POLL_SERVER_SECRET = os.environ.get("EMAIL_POLL_SERVER_SECRET")
EXPECTED_SHA256 = "6f4d51761cebdf73fece9c0f7e3b3d7aff75ba6812421a551eb8b082227d112e"


def _email_poll_loop():
    logging.info("[EMAIL POLL] Background email checker started.")

    from binance_data import get_redis  # Lazy import to avoid circular import
    r = get_redis()

    while True:
        try:
            logging.info("[EMAIL POLL] Fetching all matching emails...")
            emails = fetch_all_matching_emails()

            if not emails:
                logging.info("[EMAIL POLL] No matching emails found.")
                time.sleep(POLL_INTERVAL)
                continue

            # Process newest matching email only
            email_item = emails[-1]

            text = email_item.get("text", "")
            payload = extract_alert_payload(text)

            if not payload:
                logging.info("[EMAIL POLL] No payload extracted from email.")
                time.sleep(POLL_INTERVAL)
                continue

            # Extract normalized date
            email_dt = email.utils.parsedate_to_datetime(email_item["date"])
            date_str = email_dt.strftime("%Y-%m-%d")

            # Build record
            record = {
                "timestamp": email_dt.timestamp(),
                "date": date_str,
                "subject": email_item["subject"],
                "payload": payload
            }

            # Overwrite record for that date
            r.set(f"larsson_alert:{date_str}", json.dumps(record))
            r.set("larsson_alert_last_day", date_str)

            logging.info(f"[EMAIL POLL] Overwrote alert for {date_str}")

        except Exception as e:
            logging.exception(f"[EMAIL POLL] Error during email check: {e}")

        logging.info(f"[EMAIL POLL] Sleeping for approx. {POLL_INTERVAL/3600:.1f} hours...")
        time.sleep(POLL_INTERVAL)


def start_email_polling_thread():
    if not should_start_email_poll():
        logging.info("[EMAIL POLL] Skipped — not the main server.")
        return

    t = threading.Thread(target=_email_poll_loop, daemon=True, name="EmailPollingThread")
    t.start()
    logging.info("[EMAIL POLL] Started email polling thread - authorized as main server.")


def should_start_email_poll():
    secret = EMAIL_POLL_SERVER_SECRET
    if not secret:
        return False

    sha256_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return sha256_hash == EXPECTED_SHA256
