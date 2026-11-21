from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id (argon2-cffi's default profile) per the project's security
# requirements. Never store or log plaintext passwords.
_hasher = PasswordHasher()

# A real hash of a throwaway value, used to burn the same CPU on a login for
# an account that does not exist as one that does. Computed once at import so
# the cost of generating it is not itself a timing signal.
_DUMMY_HASH = _hasher.hash("not-a-real-password-placeholder")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        # A corrupt or truncated stored hash must read as "wrong password",
        # never as an unhandled 500 that distinguishes it from a real miss.
        return False


def dummy_verify() -> None:
    """Spend one Argon2 verification without checking anything.

    Called on the "no such account" branch of login so that branch takes the
    same wall-clock time as a genuine password check. Without it, response
    latency tells an attacker which identifiers are registered.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "definitely-not-the-password")
    except Exception:  # noqa: BLE001, S110 - the mismatch IS the expected outcome
        pass


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash predates the current Argon2 cost parameters.

    Call this after a successful verify and re-hash in place if it returns
    true, so raising cost settings actually strengthens existing accounts
    rather than only new ones.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False
