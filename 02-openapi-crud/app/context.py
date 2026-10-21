from contextvars import ContextVar
from dataclasses import dataclass

from app.errors import ApiError


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    role: str


current_user: ContextVar[CurrentUser | None] = ContextVar("current_user", default=None)


def require_user() -> CurrentUser:
    user = current_user.get()
    if user is None:
        raise ApiError(401, "TOKEN_INVALID", "Access token is missing or invalid")
    return user
