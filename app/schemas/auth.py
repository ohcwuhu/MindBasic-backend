from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class RegisterIn(ApiModel):
    phone: str = Field(pattern=r"^1\d{10}$", max_length=20)
    password: str = Field(min_length=8, max_length=64)
    nickname: str = Field(min_length=1, max_length=20)
    gender: Literal["boy", "girl"] = "girl"
    privacy_agreed: bool
    service_agreed: bool


class LoginIn(ApiModel):
    phone: str = Field(pattern=r"^1\d{10}$", max_length=20)
    password: str = Field(min_length=1, max_length=64)


class UserOut(ApiModel):
    id: int
    phone: str
    email: str | None = None
    nickname: str
    avatarUrl: str | None = None
    gender: Literal["boy", "girl"]
    role: Literal["USER", "COACH", "ADMIN"]
    isDisabled: bool
    agreementVersion: str | None = None
    agreementAcceptedAt: str | None = None
    createdAt: str


class AuthOut(ApiModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    user: UserOut


class TokenOut(ApiModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int


class UserPatchIn(ApiModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=20)
    avatarUrl: str | None = Field(default=None, max_length=512)
    gender: Literal["boy", "girl"] | None = None


class ChangePasswordIn(ApiModel):
    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=64)


class EmailCodeIn(ApiModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    purpose: Literal["LOGIN", "RESET", "BIND"]


class EmailLoginIn(ApiModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    code: str = Field(pattern=r"^\d{6}$")


class EmailBindIn(ApiModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    code: str = Field(pattern=r"^\d{6}$")
    purpose: Literal["BIND"]


class ResetPasswordIn(ApiModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=64)
