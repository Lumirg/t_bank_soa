# Marketplace API

Домашнее задание 2. Команды ниже запускаются из этой папки.

Стек: Python 3.12, FastAPI, PostgreSQL, Flyway. Контракт — `src/main/resources/openapi/marketplace.yaml`. На сборке образа из него генерируются модели и маршруты (`openapi-generator`, `python-fastapi`). Бизнес-код в `impl/` реализует сгенерированные базовые классы. Ручных HTTP DTO нет.

## Запуск

Нужны Docker и Docker Compose v2.

```bash
docker compose up --build -d
curl -i http://localhost:8080/health
```

Ожидаемый ответ: `200 OK` и `{"status":"ok"}`.

Остановка и сброс базы:

```bash
docker compose down
```

Повторный прогон сценария:

```bash
python3 scripts/e2e.py
```

Кодогенерация отдельно (результат в `generated/`, каталог в `.gitignore`):

```bash
./scripts/generate.sh
```

## Что внутри

| Часть | Где |
| --- | --- |
| Контракт | `src/main/resources/openapi/marketplace.yaml` |
| Миграция Flyway | `src/main/resources/db/migration/V1__init.sql` |
| Реализации API | `impl/auth.py`, `impl/products.py`, `impl/orders.py`, `impl/promo.py` |
| JWT | `overrides/security_api.py`, выдача в `impl/auth.py` |
| Ошибки | `app/errors.py` |
| Лог каждого запроса | `app/logging.py` |
| Точка входа | `app/main.py` |

`created_at` заполняет `DEFAULT now()`. `updated_at` на `products` и `orders` переписывает триггер `set_updated_at`. На `products.status` есть индекс `idx_products_status`. `DELETE /products/{id}` ставит `ARCHIVED`.

Лимит частоты заказа — `ORDER_RATE_LIMIT_MINUTES` (в compose `1`). Ноль выключает проверку.

Процентная скидка не больше 70% суммы позиций: если `discount_value` больше 70, скидка срезается до 70%.

Access token — 20 минут, refresh — 14 дней. Refresh хранится в `refresh_tokens` и при обмене заменяется. Роль — claim `role`. При регистрации роль можно передать явно, иначе `USER`.

Переходы `CREATED → PAYMENT_PENDING → PAID → SHIPPED → COMPLETED` — `POST /orders/{id}/transition`. Отмена — только `POST /orders/{id}/cancel` из `CREATED` или `PAYMENT_PENDING`.

Postgres с хоста — порт `5433`.

## Сценарий для защиты

```bash
curl -s -X POST localhost:8080/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"seller@example.com","password":"password1","role":"SELLER"}'
curl -s -X POST localhost:8080/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","password":"password1","role":"USER"}'

seller_token=$(curl -s -X POST localhost:8080/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"seller@example.com","password":"password1"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
user_token=$(curl -s -X POST localhost:8080/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","password":"password1"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -X POST localhost:8080/products -H "Authorization: Bearer $seller_token" -H 'Content-Type: application/json' \
  -d '{"name":"Book","price":100.00,"stock":5,"category":"books","status":"ACTIVE"}'

curl -s -D - -X POST localhost:8080/orders -H "Authorization: Bearer $user_token" -H 'Content-Type: application/json' \
  -d '{"items":[{"product_id":"ПОДСТАВИТЬ","quantity":2}]}'
```

В ответе есть `X-Request-Id`. В `docker compose logs api` — JSON-лог с `request_id`, `method`, `endpoint`, `status_code`, `duration_ms`, `user_id`, `timestamp`. У мутаций в логе есть тело, `password` заменён на `***`.

Таблицы:

```bash
docker compose exec postgres psql -U marketplace -d marketplace -c '\d products'
docker compose exec postgres psql -U marketplace -d marketplace -c 'SELECT id, name, stock, status FROM products;'
docker compose exec postgres psql -U marketplace -d marketplace -c 'SELECT id, status, total_amount, discount_amount FROM orders;'
docker compose exec postgres psql -U marketplace -d marketplace -c 'SELECT product_id, quantity, price_at_order FROM order_items;'
```
