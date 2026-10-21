import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from psycopg.errors import UniqueViolation

from app.db import transaction
from app.errors import ApiError, validation
from openapi_server.apis.auth_api_base import BaseAuthApi
from openapi_server.models.login_request import LoginRequest
from openapi_server.models.refresh_request import RefreshRequest
from openapi_server.models.register_request import RegisterRequest
from openapi_server.models.token_pair import TokenPair
from openapi_server.models.user_response import UserResponse
from openapi_server.models.user_role import UserRole


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def _access_ttl() -> int:
    return int(os.environ.get("JWT_ACCESS_TTL_SECONDS", "1200"))


def _refresh_ttl() -> int:
    return int(os.environ.get("JWT_REFRESH_TTL_SECONDS", str(14 * 24 * 3600)))


def _encode(user_id: str, email: str, role: str, typ: str, jti: str, ttl: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "role": role,
            "typ": typ,
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(seconds=ttl),
        },
        _secret(),
        algorithm="HS256",
    )


def _issue(connection, user_id: str, email: str, role: str) -> TokenPair:
    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_refresh_ttl())
    connection.execute(
        "INSERT INTO refresh_tokens (jti, user_id, expires_at) VALUES (%s, %s, %s)",
        (jti, user_id, expires_at),
    )
    return TokenPair(
        access_token=_encode(user_id, email, role, "access", str(uuid.uuid4()), _access_ttl()),
        refresh_token=_encode(user_id, email, role, "refresh", jti, _refresh_ttl()),
        expires_in=_access_ttl(),
        token_type="Bearer",
    )


class AuthApi(BaseAuthApi):
    async def register(self, register_request: RegisterRequest) -> UserResponse:
        user_id = str(uuid.uuid4())
        email = register_request.email.strip().lower()
        role = register_request.role or UserRole.USER
        password_hash = bcrypt.hashpw(register_request.password.encode(), bcrypt.gensalt()).decode()
        try:
            with transaction() as connection:
                connection.execute(
                    "INSERT INTO users (id, email, password_hash, role) VALUES (%s, %s, %s, CAST(%s AS user_role))",
                    (user_id, email, password_hash, role.value),
                )
        except UniqueViolation:
            raise validation("email", "already registered") from None
        return UserResponse(id=user_id, email=email, role=role)

    async def login(self, login_request: LoginRequest) -> TokenPair:
        email = login_request.email.strip().lower()
        with transaction() as connection:
            row = connection.execute(
                "SELECT id, email, role::text AS role, password_hash FROM users WHERE email = %s",
                (email,),
            ).fetchone()
            if row is None or not bcrypt.checkpw(login_request.password.encode(), row["password_hash"].encode()):
                raise ApiError(401, "TOKEN_INVALID", "Invalid email or password")
            return _issue(connection, str(row["id"]), row["email"], row["role"])

    async def refresh(self, refresh_request: RefreshRequest) -> TokenPair:
        try:
            payload = jwt.decode(refresh_request.refresh_token, _secret(), algorithms=["HS256"])
        except jwt.PyJWTError:
            raise ApiError(401, "REFRESH_TOKEN_INVALID", "Refresh token is invalid") from None
        if payload.get("typ") != "refresh" or not payload.get("sub") or not payload.get("jti"):
            raise ApiError(401, "REFRESH_TOKEN_INVALID", "Refresh token is invalid")
        try:
            jti = str(uuid.UUID(str(payload["jti"])))
            user_id = str(uuid.UUID(str(payload["sub"])))
        except ValueError:
            raise ApiError(401, "REFRESH_TOKEN_INVALID", "Refresh token is invalid") from None
        with transaction() as connection:
            row = connection.execute(
                "SELECT expires_at FROM refresh_tokens WHERE jti = %s AND user_id = %s FOR UPDATE",
                (jti, user_id),
            ).fetchone()
            now = datetime.now(timezone.utc)
            if row is None or row["expires_at"] <= now:
                raise ApiError(401, "REFRESH_TOKEN_INVALID", "Refresh token is invalid")
            connection.execute("DELETE FROM refresh_tokens WHERE jti = %s", (jti,))
            return _issue(connection, user_id, payload.get("email") or "", str(payload.get("role")))
