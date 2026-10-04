from pydantic import EmailStr, Field, field_validator

from app.schemas.common import CamelModel
from app.schemas.user import UserResponse


class SignUpRequest(CamelModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    mobile_number: str = Field(pattern=r"^[6-9]\d{9}$")
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str
    accepted_terms: bool

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v

    @field_validator("accepted_terms")
    @classmethod
    def must_accept_terms(cls, v: bool):
        if not v:
            raise ValueError("You must accept the Terms & Conditions")
        return v


class VerifyOtpRequest(CamelModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class LoginRequest(CamelModel):
    identifier: str = Field(min_length=3)
    password: str
    remember_me: bool = False


class LoginResponse(CamelModel):
    access_token: str
    user: UserResponse


class RefreshResponse(CamelModel):
    access_token: str
