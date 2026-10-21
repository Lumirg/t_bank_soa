import uuid
from decimal import Decimal, ROUND_HALF_UP

from app.errors import ApiError, validation


def parse_uuid(value: str, field: str = "id") -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise validation(field, "must be a UUID") from None


def quantize(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money(value):
    amount = quantize(value)
    if amount == amount.to_integral():
        return int(amount)
    return float(amount)


def details(key: str, value) -> dict:
    return {key: value}
