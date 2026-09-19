import hashlib
import hmac
import os
from datetime import datetime, timedelta


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    return salt.hex() + "$" + hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def verify_pin(pin: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest, bytes.fromhex(digest_hex))


# A 4-digit PIN has 10,000 combinations: without a brake it falls in minutes. After MAX_PIN_FAILURES wrong tries in a
# row the user is locked for LOCK_FOR. It counts against the user whose PIN is asked for (login) or who is asking (cash).
# ponytail: a kid can lock a parent out of the picker for a few minutes; that beats guessing, revisit with per-IP limits.
MAX_PIN_FAILURES = 5
LOCK_FOR = timedelta(minutes=5)


def locked(user) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.now())


def pin_failed(user) -> None:
    user.pin_failures += 1
    if user.pin_failures >= MAX_PIN_FAILURES:
        user.pin_failures, user.locked_until = 0, datetime.now() + LOCK_FOR


def pin_ok(user) -> None:
    user.pin_failures, user.locked_until = 0, None
