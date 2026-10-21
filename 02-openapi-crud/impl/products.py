import uuid

from app.context import require_user
from app.db import transaction
from app.errors import ApiError
from .common import money, parse_uuid, quantize
from openapi_server.apis.products_api_base import BaseProductsApi
from openapi_server.models.product_create import ProductCreate
from openapi_server.models.product_page import ProductPage
from openapi_server.models.product_response import ProductResponse
from openapi_server.models.product_status import ProductStatus
from openapi_server.models.product_update import ProductUpdate

SELECT = """
SELECT id, name, description, price, stock, category, status::text AS status, seller_id, created_at, updated_at
FROM products
"""


def _response(row) -> ProductResponse:
    return ProductResponse(
        id=str(row["id"]),
        name=row["name"],
        description=row["description"],
        price=money(row["price"]),
        stock=row["stock"],
        category=row["category"],
        status=ProductStatus(row["status"]),
        seller_id=str(row["seller_id"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _require_writer(role: str) -> None:
    if role not in ("SELLER", "ADMIN"):
        raise ApiError(403, "ACCESS_DENIED", "Access denied")


def _require_owner(role: str, user_id: str, seller_id: str) -> None:
    if role == "ADMIN" or user_id == seller_id:
        return
    raise ApiError(403, "ACCESS_DENIED", "Access denied")


class ProductsApi(BaseProductsApi):
    async def create_product(self, product_create: ProductCreate) -> ProductResponse:
        user = require_user()
        _require_writer(user.role)
        product_id = str(uuid.uuid4())
        with transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO products (id, name, description, price, stock, category, status, seller_id)
                VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS product_status), %s)
                RETURNING created_at, updated_at
                """,
                (
                    product_id,
                    product_create.name,
                    product_create.description,
                    quantize(product_create.price),
                    product_create.stock,
                    product_create.category,
                    product_create.status.value,
                    user.id,
                ),
            ).fetchone()
        return ProductResponse(
            id=product_id,
            name=product_create.name,
            description=product_create.description,
            price=money(product_create.price),
            stock=product_create.stock,
            category=product_create.category,
            status=product_create.status,
            seller_id=user.id,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_product(self, id: str) -> ProductResponse:
        product_id = parse_uuid(id)
        with transaction() as connection:
            row = connection.execute(SELECT + " WHERE id = %s", (product_id,)).fetchone()
        if row is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "Product not found")
        return _response(row)

    async def list_products(self, page, size, status, category) -> ProductPage:
        page_number = 0 if page is None else page
        page_size = 20 if size is None else size
        where = ["1 = 1"]
        params: list = []
        if status is not None:
            where.append("status = CAST(%s AS product_status)")
            params.append(status.value)
        if category is not None:
            where.append("category = %s")
            params.append(category)
        clause = " WHERE " + " AND ".join(where)
        with transaction() as connection:
            total = connection.execute("SELECT count(*) AS count FROM products" + clause, params).fetchone()["count"]
            rows = connection.execute(
                SELECT + clause + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                [*params, page_size, page_number * page_size],
            ).fetchall()
        return ProductPage(
            items=[_response(row) for row in rows],
            total_elements=total,
            page=page_number,
            size=page_size,
        )

    async def update_product(self, id: str, product_update: ProductUpdate) -> ProductResponse:
        user = require_user()
        _require_writer(user.role)
        product_id = parse_uuid(id)
        with transaction() as connection:
            current = connection.execute(
                "SELECT seller_id FROM products WHERE id = %s FOR UPDATE",
                (product_id,),
            ).fetchone()
            if current is None:
                raise ApiError(404, "PRODUCT_NOT_FOUND", "Product not found")
            _require_owner(user.role, user.id, str(current["seller_id"]))
            connection.execute(
                """
                UPDATE products
                SET name = %s, description = %s, price = %s, stock = %s, category = %s, status = CAST(%s AS product_status)
                WHERE id = %s
                """,
                (
                    product_update.name,
                    product_update.description,
                    quantize(product_update.price),
                    product_update.stock,
                    product_update.category,
                    product_update.status.value,
                    product_id,
                ),
            )
            row = connection.execute(SELECT + " WHERE id = %s", (product_id,)).fetchone()
        return _response(row)

    async def delete_product(self, id: str) -> ProductResponse:
        user = require_user()
        _require_writer(user.role)
        product_id = parse_uuid(id)
        with transaction() as connection:
            current = connection.execute(
                "SELECT seller_id FROM products WHERE id = %s FOR UPDATE",
                (product_id,),
            ).fetchone()
            if current is None:
                raise ApiError(404, "PRODUCT_NOT_FOUND", "Product not found")
            _require_owner(user.role, user.id, str(current["seller_id"]))
            connection.execute("UPDATE products SET status = 'ARCHIVED' WHERE id = %s", (product_id,))
            row = connection.execute(SELECT + " WHERE id = %s", (product_id,)).fetchone()
        return _response(row)
