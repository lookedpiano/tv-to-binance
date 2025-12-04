import json
import time
import logging
import threading
import email
from zoneinfo import ZoneInfo

from email_fetcher import fetch_all_alert_emails, extract_alert_payload
from security import verify_server

TZ = ZoneInfo("Europe/Zurich")
POLL_INTERVAL = 3593 * 4   # 4 hours


def _email_poll_loop():
    logging.info("[EMAIL POLL] Background email checker started.")

    from binance_data import get_redis  # Lazy import to avoid circular import
    r = get_redis()

    while True:
        try:
            logging.info("[EMAIL POLL] Scanning emails...")
            emails = fetch_all_alert_emails()

            if not emails:
                logging.info("[EMAIL POLL] No alert emails found.")
                time.sleep(POLL_INTERVAL)
                continue

            for msg in emails:
                payload = extract_alert_payload(msg.get("text", ""))
                if not payload:
                    continue

                # Parse date
                dt = email.utils.parsedate_to_datetime(msg["date"])
                date_str = dt.strftime("%Y-%m-%d")

                record = {
                    "timestamp": dt.timestamp(),
                    "date": date_str,
                    "subject": msg["subject"],
                    "payload": payload,
                }

                # Overwrite same day always
                key = f"larsson_alert:{date_str}"
                r.set(key, json.dumps(record))

                logging.debug(f"[EMAIL POLL] Saved alert for {date_str}: {msg['subject']}")

            logging.info(f"[EMAIL POLL] Processed {len(emails)} alert emails.")

        except Exception as e:
            logging.exception(f"[EMAIL POLL] Error: {e}")

        logging.info(f"[EMAIL POLL] Sleeping {POLL_INTERVAL/3600:.1f} hours…")
        time.sleep(POLL_INTERVAL)


def start_email_polling_thread():
    if not verify_server():
        logging.info("[EMAIL POLL] Not main server — skipping.")
        return

    t = threading.Thread(target=_email_poll_loop, daemon=True)
    t.start()
    logging.info("[EMAIL POLL] Email polling thread started.")
