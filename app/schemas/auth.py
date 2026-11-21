import unicodedata

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import CamelModel
from app.schemas.user import UserResponse

# Argon2 happily hashes a megabyte of text, and doing so burns CPU on an
# unauthenticated endpoint. Real passwords are nowhere near this long.
MAX_PASSWORD_LENGTH = 128

# Rejecting the handful of passwords that actually show up in credential
# stuffing is worth far more than a symbol-class rule, which mostly pushes
# people toward "Password1!".
_COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "passw0rd",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "qwertyuiop",
    "letmein1",
    "iloveyou",
    "admin123",
    "welcome1",
    "welcome123",
    "abc12345",
    "football",
    "monkey123",
    "nexpro123",
    "changeme",
    "secret123",
}


def validate_password_strength(password: str) -> str:
    """Shared password policy.

    Normalizes to NFKC first so visually identical passwords hash
    identically regardless of how the client encoded them, then applies a
    length floor, a simple variety requirement, and a common-password
    blocklist.
    """
    normalized = unicodedata.normalize("NFKC", password)

    if len(normalized) < 10:
        raise ValueError("Use at least 10 characters.")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    if normalized.lower() in _COMMON_PASSWORDS:
        raise ValueError("That password is too common. Please choose something less guessable.")
    if normalized.isdigit():
        raise ValueError("Add letters, not just numbers.")

    classes = sum(
        [
            any(c.islower() for c in normalized),
            any(c.isupper() for c in normalized),
            any(c.isdigit() for c in normalized),
            any(not c.isalnum() for c in normalized),
        ]
    )
    if classes < 3:
        raise ValueError("Mix upper case, lower case, and numbers or symbols.")

    return normalized


class SignUpRequest(CamelModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr = Field(max_length=254)
    mobile_number: str = Field(pattern=r"^[6-9]\d{9}$")
    password: str = Field(min_length=10, max_length=MAX_PASSWORD_LENGTH)
    confirm_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    accepted_terms: bool

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, v: str) -> str:
        # Collapse whitespace and strip control characters so the stored name
        # cannot smuggle newlines into logs or exported reports.
        cleaned = " ".join(v.split())
        cleaned = "".join(c for c in cleaned if unicodedata.category(c)[0] != "C")
        if len(cleaned) < 2:
            raise ValueError("Enter your full name")
        return cleaned

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info):
        if "password" in info.data and unicodedata.normalize("NFKC", v) != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v

    @field_validator("accepted_terms")
    @classmethod
    def must_accept_terms(cls, v: bool):
        if not v:
            raise ValueError("You must accept the Terms & Conditions")
        return v


class VerifyOtpRequest(CamelModel):
    email: EmailStr = Field(max_length=254)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class LoginRequest(CamelModel):
    # Bounded so an unauthenticated caller cannot make us hash or index
    # arbitrarily large strings.
    identifier: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, v: str) -> str:
        return v.strip()


class LoginResponse(CamelModel):
    access_token: str
    user: UserResponse


class RefreshResponse(CamelModel):
    access_token: str
