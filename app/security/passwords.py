from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Argon2id (argon2-cffi's default profile) per the project's security requirements.
# Never store or log plaintext passwords.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
