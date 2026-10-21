#!/usr/bin/env python3
"""Прогон контракта против поднятого API. Запуск: python3 scripts/e2e.py"""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8080"


def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode()
            payload = json.loads(raw) if raw else None
            return response.status, payload, response.headers
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        payload = json.loads(raw) if raw else None
        return error.code, payload, error.headers


def expect(status, code, actual_status, payload, label):
    error_code = None if not isinstance(payload, dict) else payload.get("error_code")
    if actual_status != status or (code is not None and error_code != code):
        raise SystemExit(f"FAIL {label}: expected {status} {code}, got {actual_status} {payload}")
    print(f"OK   {label}")


def expired_access_token():
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({
        "sub": "00000000-0000-0000-0000-000000000001",
        "role": "USER",
        "email": "user@example.com",
        "typ": "access",
        "jti": "00000000-0000-0000-0000-000000000002",
        "iat": int(time.time()) - 120,
        "exp": int(time.time()) - 60,
    }).encode())
    secret = b"marketplace-homework-secret-key-32b-min"
    signature = b64(hmac.new(secret, header + b"." + payload, hashlib.sha256).digest())
    return (header + b"." + payload + b"." + signature).decode()


def main():
    status, payload, _ = call("GET", "/health")
    expect(200, None, status, payload, "health")

    status, payload, _ = call("POST", "/auth/register", {"email": "seller@example.com", "password": "password1", "role": "SELLER"})
    expect(201, None, status, payload, "register seller")
    status, payload, _ = call("POST", "/auth/register", {"email": "user@example.com", "password": "password1", "role": "USER"})
    expect(201, None, status, payload, "register user")
    status, payload, _ = call("POST", "/auth/register", {"email": "other@example.com", "password": "password1", "role": "USER"})
    expect(201, None, status, payload, "register other user")
    status, payload, _ = call("POST", "/auth/register", {"email": "user@example.com", "password": "password1"})
    expect(400, "VALIDATION_ERROR", status, payload, "duplicate email")

    status, seller, _ = call("POST", "/auth/login", {"email": "seller@example.com", "password": "password1"})
    expect(200, None, status, seller, "login seller")
    status, user, headers = call("POST", "/auth/login", {"email": "user@example.com", "password": "password1"})
    expect(200, None, status, user, "login user")
    if "X-Request-Id" not in headers:
        raise SystemExit("FAIL login has no X-Request-Id")
    status, other, _ = call("POST", "/auth/login", {"email": "other@example.com", "password": "password1"})
    expect(200, None, status, other, "login other")
    status, payload, _ = call("POST", "/auth/login", {"email": "user@example.com", "password": "wrong-pass"})
    expect(401, "TOKEN_INVALID", status, payload, "bad password")
    status, refreshed, _ = call("POST", "/auth/refresh", {"refresh_token": user["refresh_token"]})
    expect(200, None, status, refreshed, "refresh")
    status, payload, _ = call("POST", "/auth/refresh", {"refresh_token": user["refresh_token"]})
    expect(401, "REFRESH_TOKEN_INVALID", status, payload, "refresh reuse")
    status, payload, _ = call("GET", "/products")
    expect(401, "TOKEN_INVALID", status, payload, "missing token")
    status, payload, _ = call("GET", "/products", token="not-a-token")
    expect(401, "TOKEN_INVALID", status, payload, "garbage token")
    status, payload, _ = call("GET", "/products", token=expired_access_token())
    expect(401, "TOKEN_EXPIRED", status, payload, "expired token")

    seller_token = seller["access_token"]
    user_token = refreshed["access_token"]
    other_token = other["access_token"]

    status, payload, _ = call("POST", "/products", {"name": "", "price": 10, "stock": 1, "category": "books", "status": "ACTIVE"}, user_token)
    expect(400, "VALIDATION_ERROR", status, payload, "blank name")
    status, payload, _ = call("POST", "/products", {"name": "Book", "price": 0, "stock": 1, "category": "books", "status": "ACTIVE"}, seller_token)
    expect(400, "VALIDATION_ERROR", status, payload, "price zero")
    status, payload, _ = call("POST", "/products", {"name": "Nope", "price": 10, "stock": 1, "category": "books", "status": "ACTIVE"}, user_token)
    expect(403, "ACCESS_DENIED", status, payload, "user cannot create product")

    status, book, _ = call("POST", "/products", {"name": "Book", "description": "A novel", "price": 100, "stock": 5, "category": "books", "status": "ACTIVE"}, seller_token)
    expect(201, None, status, book, "create book")
    status, pen, _ = call("POST", "/products", {"name": "Pen", "price": 20, "stock": 2, "category": "stationery", "status": "ACTIVE"}, seller_token)
    expect(201, None, status, pen, "create pen")
    status, hidden, _ = call("POST", "/products", {"name": "Draft", "price": 15, "stock": 4, "category": "books", "status": "INACTIVE"}, seller_token)
    expect(201, None, status, hidden, "create inactive")

    status, page, _ = call("GET", "/products?page=0&size=10&category=books&status=ACTIVE", token=user_token)
    expect(200, None, status, page, "list products")
    if page["totalElements"] != 1 or page["items"][0]["id"] != book["id"]:
        raise SystemExit(f"FAIL filter {page}")
    print("OK   filter category and status")

    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": book["id"], "quantity": 2}]}, user_token)
    expect(201, None, status, payload, "create order")
    order = payload
    if order["status"] != "CREATED" or order["total_amount"] != 200 or order["items"][0]["price_at_order"] != 100:
        raise SystemExit(f"FAIL order snapshot {order}")
    print("OK   price snapshot and total")
    status, seen, _ = call("GET", f"/products/{book['id']}", token=user_token)
    expect(200, None, status, seen, "get product")
    if seen["stock"] != 3:
        raise SystemExit(f"FAIL stock reserved {seen}")
    print("OK   stock reserved")
    status, _, _ = call("PUT", f"/products/{book['id']}", {"name": "Book", "price": 999, "stock": seen["stock"], "category": "books", "status": "ACTIVE"}, seller_token)
    expect(200, None, status, _, "seller changes price")
    status, unchanged, _ = call("GET", f"/orders/{order['id']}", token=user_token)
    expect(200, None, status, unchanged, "get order after price change")
    if unchanged["items"][0]["price_at_order"] != 100:
        raise SystemExit(f"FAIL snapshot changed {unchanged}")
    print("OK   snapshot ignores later price")
    status, _, _ = call("PUT", f"/products/{book['id']}", {"name": "Book", "price": 100, "stock": seen["stock"], "category": "books", "status": "ACTIVE"}, seller_token)
    expect(200, None, status, _, "seller restores price")

    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": book["id"], "quantity": 1}]}, user_token)
    expect(429, "ORDER_LIMIT_EXCEEDED", status, payload, "create rate limit")
    status, payload, _ = call("GET", f"/orders/{order['id']}", token=other_token)
    expect(403, "ORDER_OWNERSHIP_VIOLATION", status, payload, "foreign order")
    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": book["id"], "quantity": 1}]}, seller_token)
    expect(403, "ACCESS_DENIED", status, payload, "seller cannot order")

    status, updated, _ = call("PUT", f"/orders/{order['id']}", {"items": [{"product_id": book["id"], "quantity": 1}, {"product_id": pen["id"], "quantity": 1}]}, user_token)
    expect(200, None, status, updated, "update order")
    if updated["total_amount"] != 120:
        raise SystemExit(f"FAIL updated total {updated}")
    status, seen, _ = call("GET", f"/products/{book['id']}", token=user_token)
    if seen["stock"] != 4:
        raise SystemExit(f"FAIL book stock after update {seen}")
    status, seen, _ = call("GET", f"/products/{pen['id']}", token=user_token)
    if seen["stock"] != 1:
        raise SystemExit(f"FAIL pen stock after update {seen}")
    print("OK   stock replaced")
    status, payload, _ = call("PUT", f"/orders/{order['id']}", {"items": [{"product_id": book["id"], "quantity": 1}]}, user_token)
    expect(429, "ORDER_LIMIT_EXCEEDED", status, payload, "update rate limit")

    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": hidden["id"], "quantity": 1}]}, other_token)
    expect(409, "PRODUCT_INACTIVE", status, payload, "inactive product")
    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": pen["id"], "quantity": 99}]}, other_token)
    expect(409, "INSUFFICIENT_STOCK", status, payload, "insufficient stock")
    if "items" not in payload.get("details", {}):
        raise SystemExit(f"FAIL stock details {payload}")

    status, promo, _ = call(
        "POST",
        "/promo-codes",
        {
            "code": "SALE10",
            "discount_type": "PERCENTAGE",
            "discount_value": 80,
            "min_order_amount": 50,
            "max_uses": 2,
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_until": "2030-01-01T00:00:00Z",
        },
        seller_token,
    )
    expect(201, None, status, promo, "create promo")
    status, payload, _ = call("POST", "/promo-codes", {"code": "NOPECODE", "discount_type": "FIXED_AMOUNT", "discount_value": 10, "min_order_amount": 0, "max_uses": 1, "valid_from": "2020-01-01T00:00:00Z", "valid_until": "2030-01-01T00:00:00Z"}, user_token)
    expect(403, "ACCESS_DENIED", status, payload, "user cannot create promo")

    status, canceled, _ = call("POST", f"/orders/{order['id']}/cancel", token=user_token)
    expect(200, None, status, canceled, "cancel order")
    if canceled["status"] != "CANCELED":
        raise SystemExit(f"FAIL cancel status {canceled}")
    status, seen, _ = call("GET", f"/products/{book['id']}", token=user_token)
    status_pen, seen_pen, _ = call("GET", f"/products/{pen['id']}", token=user_token)
    if seen["stock"] != 5 or seen_pen["stock"] != 2:
        raise SystemExit(f"FAIL stock restored book={seen['stock']} pen={seen_pen['stock']}")
    print("OK   stock restored")

    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": book["id"], "quantity": 1}], "promo_code": "SALE10"}, other_token)
    expect(201, None, status, payload, "order with capped percent")
    if payload["discount_amount"] != 70 or payload["total_amount"] != 30:
        raise SystemExit(f"FAIL 70 percent cap {payload}")
    print("OK   discount capped at 70")
    promo_order = payload
    status, moved, _ = call("POST", f"/orders/{promo_order['id']}/transition", {"status": "PAYMENT_PENDING"}, other_token)
    expect(200, None, status, moved, "to payment pending")
    status, payload, _ = call("POST", f"/orders/{promo_order['id']}/transition", {"status": "SHIPPED"}, other_token)
    expect(409, "INVALID_STATE_TRANSITION", status, payload, "skip transition")
    status, moved, _ = call("POST", f"/orders/{promo_order['id']}/transition", {"status": "PAID"}, other_token)
    expect(200, None, status, moved, "to paid")
    status, payload, _ = call("POST", f"/orders/{promo_order['id']}/cancel", token=other_token)
    expect(409, "INVALID_STATE_TRANSITION", status, payload, "cancel paid")

    status, archived, _ = call("DELETE", f"/products/{hidden['id']}", token=seller_token)
    expect(200, None, status, archived, "soft delete")
    if archived["status"] != "ARCHIVED":
        raise SystemExit(f"FAIL archive {archived}")
    status, payload, _ = call("DELETE", f"/products/{book['id']}", token=other_token)
    expect(403, "ACCESS_DENIED", status, payload, "user cannot archive")

    status, buyer, _ = call("POST", "/auth/register", {"email": "buyer@example.com", "password": "password1", "role": "USER"})
    expect(201, None, status, buyer, "register buyer")
    status, buyer_login, _ = call("POST", "/auth/login", {"email": "buyer@example.com", "password": "password1"})
    buyer_token = buyer_login["access_token"]
    status, cheap, _ = call("POST", "/products", {"name": "Cup", "price": 10, "stock": 10, "category": "home", "status": "ACTIVE"}, seller_token)
    expect(201, None, status, cheap, "create cup")
    promo_body = {
        "discount_type": "PERCENTAGE",
        "discount_value": 10,
        "min_order_amount": 50,
        "max_uses": 5,
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_until": "2030-01-01T00:00:00Z",
    }
    status, _, _ = call("POST", "/promo-codes", {"code": "MIN50", **promo_body}, seller_token)
    expect(201, None, status, _, "create min promo")
    status, _, _ = call("POST", "/promo-codes", {"code": "OLD10", **promo_body, "valid_until": "2020-02-01T00:00:00Z"}, seller_token)
    expect(201, None, status, _, "create expired promo")
    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": cheap["id"], "quantity": 1}], "promo_code": "OLD10"}, buyer_token)
    expect(422, "PROMO_CODE_INVALID", status, payload, "expired promo")
    status, payload, _ = call("POST", "/orders", {"items": [{"product_id": cheap["id"], "quantity": 1}], "promo_code": "MIN50"}, buyer_token)
    expect(422, "PROMO_CODE_MIN_AMOUNT", status, payload, "promo minimum")
    status, promoted, _ = call("POST", "/orders", {"items": [{"product_id": cheap["id"], "quantity": 5}], "promo_code": "MIN50"}, buyer_token)
    expect(201, None, status, promoted, "promo applied")
    if promoted["discount_amount"] != 5 or promoted["total_amount"] != 45:
        raise SystemExit(f"FAIL percent discount {promoted}")
    print("OK   ten percent discount")
    status, dropped, _ = call("PUT", f"/orders/{promoted['id']}", {"items": [{"product_id": cheap["id"], "quantity": 1}]}, buyer_token)
    expect(200, None, status, dropped, "update drops promo")
    if dropped.get("promo_code") is not None or dropped["discount_amount"] != 0 or dropped["total_amount"] != 10:
        raise SystemExit(f"FAIL promo was not removed {dropped}")
    print("OK   promo removed below minimum")

    print("ALL PASSED")


if __name__ == "__main__":
    main()
