import hashlib
import hmac
import os


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    return salt.hex() + "$" + hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def verify_pin(pin: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
