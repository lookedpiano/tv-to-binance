import os
import hashlib
import ipaddress
import logging
import requests

from utils import load_ip_file


SERVER_SECRET = os.environ.get("SERVER_SECRET")
SERVER_SECRET_HASH = os.environ.get("SERVER_SECRET_HASH")
BEFORE_REQUEST_SECRET = os.environ.get("BEFORE_REQUEST_SECRET")
BEFORE_REQUEST_SECRET_HASH = os.environ.get("BEFORE_REQUEST_SECRET_HASH")


def verify_server():
    """
    Returns True only if the configured SERVER_SECRET matches the
    expected SERVER_SECRET_HASH (sha256).
    """
    if not SERVER_SECRET or not SERVER_SECRET_HASH:
        return False

    sha256_value = hashlib.sha256(SERVER_SECRET.encode("utf-8")).hexdigest()
    return sha256_value == SERVER_SECRET_HASH

def verify_before_request_secret():
    """
    Returns True only if the BEFORE_REQUEST_SECRET's sha256 matches
    BEFORE_REQUEST_SECRET_HASH.
    """
    if not BEFORE_REQUEST_SECRET or not BEFORE_REQUEST_SECRET_HASH:
        return False

    sha256_value = hashlib.sha256(BEFORE_REQUEST_SECRET.encode("utf-8")).hexdigest()
    return sha256_value == BEFORE_REQUEST_SECRET_HASH


def is_outbound_ip_allowed() -> tuple[bool, str | None]:
    """
    Returns:
        (True, current_ip)  -> outbound IP is allowed
        (False, current_ip) -> outbound IP is not allowed
    """
    current_ip = requests.get("https://api.ipify.org", timeout=21).text.strip()
    logging.info(f"[OUTBOUND_IP] Current outbound IP: {current_ip}")

    allowed_ips = load_ip_file("config/outbound_ips.txt")
    ip_obj = ipaddress.ip_address(current_ip)

    for entry in allowed_ips:
        entry = entry.strip()
        if not entry:
            continue

        try:
            if ip_obj in ipaddress.ip_network(entry, strict=False):
                return True, current_ip
        except ValueError:
            if current_ip == entry:
                return True, current_ip

    return False, current_ip