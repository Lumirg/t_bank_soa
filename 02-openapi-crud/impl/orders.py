import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.context import require_user
from app.db import transaction
from app.errors import ApiError, validation
from .common import details, money, parse_uuid, quantize
from openapi_server.apis.orders_api_base import BaseOrdersApi
from openapi_server.models.order_create import OrderCreate
from openapi_server.models.order_item_response import OrderItemResponse
from openapi_server.models.order_response import OrderResponse
from openapi_server.models.order_status import OrderStatus
from openapi_server.models.order_transition import OrderTransition
from openapi_server.models.order_update import OrderUpdate

FORWARD = {
    "CREATED": "PAYMENT_PENDING",
    "PAYMENT_PENDING": "PAID",
    "PAID": "SHIPPED",
    "SHIPPED": "COMPLETED",
}
HUNDRED = Decimal("100")
SEVENTY = Decimal("0.70")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rate_limit_minutes() -> int:
    return int(os.environ.get("ORDER_RATE_LIMIT_MINUTES", "1"))


def _require_buyer(role: str) -> None:
    if role not in ("USER", "ADMIN"):
        raise ApiError(403, "ACCESS_DENIED", "Access denied")


def _require_owner(role: str, user_id: str, owner_id: str) -> None:
    if role == "ADMIN" or user_id == owner_id:
        return
    raise ApiError(403, "ORDER_OWNERSHIP_VIOLATION", "Order belongs to another user")


def _unique_lines(items) -> list[tuple[str, int]]:
    lines = []
    seen = set()
    for item in items:
        product_id = parse_uuid(item.product_id, "product_id")
        if product_id in seen:
            raise validation("items", "product_id must be unique in the order")
        seen.add(product_id)
        lines.append((product_id, item.quantity))
    return lines


def _lock_products(connection, product_ids: list[str]) -> dict[str, dict]:
    locked = {}
    for product_id in sorted(set(product_ids)):
        row = connection.execute(
            """
            SELECT id, price, stock, status::text AS status
            FROM products
            WHERE id = %s
            FOR UPDATE
            """,
            (product_id,),
        ).fetchone()
        if row is not None:
            locked[str(row["id"])] = row
    return locked


def _ensure_sellable(lines, products) -> None:
    missing = [product_id for product_id, _ in lines if product_id not in products]
    inactive = [
        product_id
        for product_id, _ in lines
        if product_id in products and products[product_id]["status"] != "ACTIVE"
    ]
    if missing:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "Product not found", details("product_ids", missing))
    if inactive:
        raise ApiError(409, "PRODUCT_INACTIVE", "Product is not active", details("product_ids", inactive))


def _ensure_stock(lines, products) -> None:
    shortages = []
    for product_id, quantity in lines:
        available = products[product_id]["stock"]
        if available < quantity:
            shortages.append({"product_id": product_id, "requested": quantity, "available": available})
    if shortages:
        raise ApiError(409, "INSUFFICIENT_STOCK", "Insufficient stock", details("items", shortages))


def _subtotal(lines: list[tuple[str, int, Decimal]]) -> Decimal:
    total = sum((price * quantity for _, quantity, price in lines), Decimal("0"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _discount(promo, subtotal: Decimal) -> Decimal:
    if promo["discount_type"] == "PERCENTAGE":
        raw = (subtotal * promo["discount_value"] / HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cap = (subtotal * SEVENTY).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return min(raw, cap)
    return min(quantize(promo["discount_value"]), subtotal)


def _promo_select() -> str:
    return """
    SELECT id, code, discount_type::text AS discount_type, discount_value, min_order_amount,
           max_uses, current_uses, valid_from, valid_until, active
    FROM promo_codes
    """


def _lock_user(connection, user_id: str) -> None:
    row = connection.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
    if row is None:
        raise ApiError(401, "TOKEN_INVALID", "Access token is invalid")


def _enforce_rate_limit(connection, user_id: str, operation_type: str) -> None:
    minutes = _rate_limit_minutes()
    if minutes <= 0:
        return
    row = connection.execute(
        """
        SELECT created_at
        FROM user_operations
        WHERE user_id = %s AND operation_type = CAST(%s AS operation_type)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id, operation_type),
    ).fetchone()
    if row is not None and row["created_at"] + timedelta(minutes=minutes) > _now():
        raise ApiError(429, "ORDER_LIMIT_EXCEEDED", "Order operation rate limit exceeded")


def _record(connection, user_id: str, operation_type: str) -> None:
    connection.execute(
        "INSERT INTO user_operations (id, user_id, operation_type) VALUES (%s, %s, CAST(%s AS operation_type))",
        (str(uuid.uuid4()), user_id, operation_type),
    )


def _lock_order(connection, order_id: str) -> dict:
    row = connection.execute(
        """
        SELECT id, user_id, status::text AS status, promo_code_id
        FROM orders
        WHERE id = %s
        FOR UPDATE
        """,
        (order_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
    return row


def _load_lines(connection, order_id: str) -> list[tuple[str, int, Decimal]]:
    rows = connection.execute(
        "SELECT product_id, quantity, price_at_order FROM order_items WHERE order_id = %s",
        (order_id,),
    ).fetchall()
    return [(str(row["product_id"]), row["quantity"], row["price_at_order"]) for row in rows]


def _insert_items(connection, order_id: str, lines: list[tuple[str, int, Decimal]]) -> None:
    for product_id, quantity, price in lines:
        connection.execute(
            "INSERT INTO order_items (id, order_id, product_id, quantity, price_at_order) VALUES (%s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), order_id, product_id, quantity, price),
        )


def _reserve(connection, lines, products) -> list[tuple[str, int, Decimal]]:
    priced = []
    for product_id, quantity in lines:
        connection.execute(
            "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
            (quantity, product_id, quantity),
        )
        priced.append((product_id, quantity, quantize(products[product_id]["price"])))
    return priced


def _apply_new_promo(connection, code: str | None, subtotal: Decimal):
    if not code:
        return None, Decimal("0.00"), subtotal
    promo = connection.execute(_promo_select() + " WHERE code = %s FOR UPDATE", (code,)).fetchone()
    now = _now()
    if (
        promo is None
        or not promo["active"]
        or promo["current_uses"] >= promo["max_uses"]
        or now < promo["valid_from"]
        or now > promo["valid_until"]
    ):
        raise ApiError(422, "PROMO_CODE_INVALID", "Promo code is not valid")
    if subtotal < promo["min_order_amount"]:
        raise ApiError(422, "PROMO_CODE_MIN_AMOUNT", "Order total is below the promo code minimum")
    discount = _discount(promo, subtotal)
    updated = connection.execute(
        "UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = %s AND current_uses < max_uses",
        (promo["id"],),
    ).rowcount
    if updated == 0:
        raise ApiError(422, "PROMO_CODE_INVALID", "Promo code is not valid")
    return promo["id"], discount, subtotal - discount


def _reprice(connection, promo_code_id, subtotal: Decimal):
    if promo_code_id is None:
        return None, subtotal, Decimal("0.00")
    promo = connection.execute(_promo_select() + " WHERE id = %s FOR UPDATE", (promo_code_id,)).fetchone()
    now = _now()
    if promo is None or not promo["active"] or now < promo["valid_from"] or now > promo["valid_until"]:
        raise ApiError(422, "PROMO_CODE_INVALID", "Promo code is not valid")
    if subtotal < promo["min_order_amount"]:
        connection.execute(
            "UPDATE promo_codes SET current_uses = current_uses - 1 WHERE id = %s AND current_uses > 0",
            (promo["id"],),
        )
        return None, subtotal, Decimal("0.00")
    discount = _discount(promo, subtotal)
    return promo["id"], subtotal - discount, discount


def _load(connection, order_id: str) -> OrderResponse:
    row = connection.execute(
        """
        SELECT o.id, o.user_id, o.status::text AS status, p.code AS promo_code,
               o.total_amount, o.discount_amount, o.created_at, o.updated_at
        FROM orders o
        LEFT JOIN promo_codes p ON p.id = o.promo_code_id
        WHERE o.id = %s
        """,
        (order_id,),
    ).fetchone()
    items = connection.execute(
        """
        SELECT product_id, quantity, price_at_order
        FROM order_items
        WHERE order_id = %s
        ORDER BY product_id
        """,
        (order_id,),
    ).fetchall()
    return OrderResponse(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        status=OrderStatus(row["status"]),
        promo_code=row["promo_code"],
        total_amount=money(row["total_amount"]),
        discount_amount=money(row["discount_amount"]),
        items=[
            OrderItemResponse(
                product_id=str(item["product_id"]),
                quantity=item["quantity"],
                price_at_order=money(item["price_at_order"]),
            )
            for item in items
        ],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class OrdersApi(BaseOrdersApi):
    async def create_order(self, order_create: OrderCreate) -> OrderResponse:
        user = require_user()
        _require_buyer(user.role)
        lines = _unique_lines(order_create.items)
        with transaction() as connection:
            _lock_user(connection, user.id)
            _enforce_rate_limit(connection, user.id, "CREATE_ORDER")
            active = connection.execute(
                """
                SELECT count(*) AS count
                FROM orders
                WHERE user_id = %s AND status IN ('CREATED', 'PAYMENT_PENDING')
                """,
                (user.id,),
            ).fetchone()["count"]
            if active:
                raise ApiError(409, "ORDER_HAS_ACTIVE", "User already has an active order")
            products = _lock_products(connection, [product_id for product_id, _ in lines])
            _ensure_sellable(lines, products)
            _ensure_stock(lines, products)
            priced = _reserve(connection, lines, products)
            subtotal = _subtotal(priced)
            promo_id, discount, total = _apply_new_promo(connection, order_create.promo_code, subtotal)
            order_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO orders (id, user_id, status, promo_code_id, total_amount, discount_amount)
                VALUES (%s, %s, 'CREATED', %s, %s, %s)
                """,
                (order_id, user.id, promo_id, total, discount),
            )
            _insert_items(connection, order_id, priced)
            _record(connection, user.id, "CREATE_ORDER")
            return _load(connection, order_id)

    async def update_order(self, id: str, order_update: OrderUpdate) -> OrderResponse:
        user = require_user()
        _require_buyer(user.role)
        order_id = parse_uuid(id)
        lines = _unique_lines(order_update.items)
        with transaction() as connection:
            _lock_user(connection, user.id)
            order = _lock_order(connection, order_id)
            _require_owner(user.role, user.id, str(order["user_id"]))
            if order["status"] != "CREATED":
                raise ApiError(409, "INVALID_STATE_TRANSITION", "Order cannot be updated in the current status")
            _enforce_rate_limit(connection, user.id, "UPDATE_ORDER")
            previous = _load_lines(connection, order_id)
            product_ids = [product_id for product_id, _, _ in previous] + [product_id for product_id, _ in lines]
            _lock_products(connection, product_ids)
            for product_id, quantity, _ in previous:
                connection.execute(
                    "UPDATE products SET stock = stock + %s WHERE id = %s",
                    (quantity, product_id),
                )
            products = _lock_products(connection, product_ids)
            _ensure_sellable(lines, products)
            _ensure_stock(lines, products)
            priced = _reserve(connection, lines, products)
            promo_id, total, discount = _reprice(connection, order["promo_code_id"], _subtotal(priced))
            connection.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
            _insert_items(connection, order_id, priced)
            connection.execute(
                """
                UPDATE orders
                SET promo_code_id = %s, total_amount = %s, discount_amount = %s
                WHERE id = %s
                """,
                (promo_id, total, discount, order_id),
            )
            _record(connection, user.id, "UPDATE_ORDER")
            return _load(connection, order_id)

    async def cancel_order(self, id: str) -> OrderResponse:
        user = require_user()
        _require_buyer(user.role)
        order_id = parse_uuid(id)
        with transaction() as connection:
            _lock_user(connection, user.id)
            order = _lock_order(connection, order_id)
            _require_owner(user.role, user.id, str(order["user_id"]))
            if order["status"] not in ("CREATED", "PAYMENT_PENDING"):
                raise ApiError(409, "INVALID_STATE_TRANSITION", "Order cannot be canceled in the current status")
            lines = _load_lines(connection, order_id)
            _lock_products(connection, [product_id for product_id, _, _ in lines])
            for product_id, quantity, _ in lines:
                connection.execute(
                    "UPDATE products SET stock = stock + %s WHERE id = %s",
                    (quantity, product_id),
                )
            if order["promo_code_id"] is not None:
                connection.execute(
                    "UPDATE promo_codes SET current_uses = current_uses - 1 WHERE id = %s AND current_uses > 0",
                    (order["promo_code_id"],),
                )
            connection.execute("UPDATE orders SET status = 'CANCELED' WHERE id = %s", (order_id,))
            return _load(connection, order_id)

    async def transition_order(self, id: str, order_transition: OrderTransition) -> OrderResponse:
        user = require_user()
        _require_buyer(user.role)
        order_id = parse_uuid(id)
        with transaction() as connection:
            _lock_user(connection, user.id)
            order = _lock_order(connection, order_id)
            _require_owner(user.role, user.id, str(order["user_id"]))
            target = str(order_transition.status)
            if target != FORWARD.get(order["status"]):
                raise ApiError(409, "INVALID_STATE_TRANSITION", "Status transition is not allowed")
            connection.execute(
                "UPDATE orders SET status = CAST(%s AS order_status) WHERE id = %s",
                (target, order_id),
            )
            return _load(connection, order_id)

    async def get_order(self, id: str) -> OrderResponse:
        user = require_user()
        if user.role == "SELLER":
            raise ApiError(403, "ACCESS_DENIED", "Access denied")
        order_id = parse_uuid(id)
        with transaction() as connection:
            row = connection.execute(
                "SELECT id, user_id FROM orders WHERE id = %s",
                (order_id,),
            ).fetchone()
            if row is None:
                raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
            _require_owner(user.role, user.id, str(row["user_id"]))
            return _load(connection, order_id)
