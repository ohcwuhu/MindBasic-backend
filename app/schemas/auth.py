from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class RegisterIn(ApiModel):
    phone: str = Field(pattern=r"^1\d{10}$", max_length=20)
    password: str = Field(min_length=8, max_length=64)
    nickname: str = Field(min_length=1, max_length=20)
    privacy_agreed: bool


class LoginIn(ApiModel):
    phone: str = Field(pattern=r"^1\d{10}$", max_length=20)
    password: str = Field(min_length=1, max_length=64)


class UserOut(ApiModel):
    id: int
    phone: str
    nickname: str
    avatarUrl: str | None = None
    role: Literal["USER", "COACH", "ADMIN"]
    isDisabled: bool
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


class ChangePasswordIn(ApiModel):
    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=64)
