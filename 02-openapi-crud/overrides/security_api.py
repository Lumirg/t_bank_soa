import os

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.context import CurrentUser, current_user
from app.errors import ApiError
from openapi_server.models.extra_models import TokenModel

bearer_auth = HTTPBearer(auto_error=False)


async def get_token_bearerAuth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> TokenModel:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise ApiError(401, "TOKEN_INVALID", "Access token is missing or invalid")
    try:
        payload = jwt.decode(credentials.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "Access token is expired") from None
    except jwt.PyJWTError:
        raise ApiError(401, "TOKEN_INVALID", "Access token is invalid") from None
    if payload.get("typ") != "access" or not payload.get("sub") or not payload.get("role"):
        raise ApiError(401, "TOKEN_INVALID", "Access token is invalid")
    user = CurrentUser(id=str(payload["sub"]), email=payload.get("email") or "", role=str(payload["role"]))
    current_user.set(user)
    request.state.user_id = user.id
    return TokenModel(sub=user.id)
