import uuid

from psycopg.errors import UniqueViolation

from app.context import require_user
from app.db import transaction
from app.errors import ApiError, validation
from .common import money, quantize
from openapi_server.apis.promo_codes_api_base import BasePromoCodesApi
from openapi_server.models.discount_type import DiscountType
from openapi_server.models.promo_code_create import PromoCodeCreate
from openapi_server.models.promo_code_response import PromoCodeResponse


def _response(row) -> PromoCodeResponse:
    return PromoCodeResponse(
        id=str(row["id"]),
        code=row["code"],
        discount_type=DiscountType(row["discount_type"]),
        discount_value=money(row["discount_value"]),
        min_order_amount=money(row["min_order_amount"]),
        max_uses=row["max_uses"],
        current_uses=row["current_uses"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        active=row["active"],
    )


class PromoCodesApi(BasePromoCodesApi):
    async def create_promo_code(self, promo_code_create: PromoCodeCreate) -> PromoCodeResponse:
        user = require_user()
        if user.role not in ("SELLER", "ADMIN"):
            raise ApiError(403, "ACCESS_DENIED", "Access denied")
        if promo_code_create.valid_until <= promo_code_create.valid_from:
            raise validation("valid_until", "must be later than valid_from")
        promo_id = str(uuid.uuid4())
        active = True if promo_code_create.active is None else promo_code_create.active
        try:
            with transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO promo_codes (
                        id, code, discount_type, discount_value, min_order_amount, max_uses, valid_from, valid_until, active
                    ) VALUES (%s, %s, CAST(%s AS discount_type), %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        promo_id,
                        promo_code_create.code,
                        promo_code_create.discount_type.value,
                        quantize(promo_code_create.discount_value),
                        quantize(promo_code_create.min_order_amount),
                        promo_code_create.max_uses,
                        promo_code_create.valid_from,
                        promo_code_create.valid_until,
                        active,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT id, code, discount_type::text AS discount_type, discount_value, min_order_amount,
                           max_uses, current_uses, valid_from, valid_until, active
                    FROM promo_codes WHERE id = %s
                    """,
                    (promo_id,),
                ).fetchone()
        except UniqueViolation:
            raise validation("code", "already exists") from None
        return _response(row)
