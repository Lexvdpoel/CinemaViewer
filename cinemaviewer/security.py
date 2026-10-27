"""Password protection for the settings screen.

Only a salted PBKDF2 hash is stored in the config. While no hash is set, the default
password applies.
"""

import hashlib
import hmac
import os

DEFAULT_PASSWORD = "1a2b3c4d5e"
_ITERATIONS = 200_000


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    if not stored:
        return hmac.compare_digest(password.encode("utf-8"), DEFAULT_PASSWORD.encode("utf-8"))
    try:
        _algo, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)
