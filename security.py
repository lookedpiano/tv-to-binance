import os
import hashlib
import logging
from flask import request, jsonify

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
