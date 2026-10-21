"""Патчит сгенерированные маршруты под реальный FastAPI runtime."""

import re
import sys
from pathlib import Path

CREATE_ROUTES = {
    "auth_api.py": '@router.post(\n    "/auth/register",\n',
    "products_api.py": '@router.post(\n    "/products",\n',
    "orders_api.py": '@router.post(\n    "/orders",\n',
    "promo_codes_api.py": '@router.post(\n    "/promo-codes",\n',
}

PAGE_QUERY = (
    'page: Annotated[Optional[Annotated[int, Field(strict=True, ge=0)]], '
    'Field(description="Номер страницы, начиная с 0")] = '
    'Query(0, description="Номер страницы, начиная с 0", alias="page", ge=0),'
)
SIZE_QUERY = (
    'size: Annotated[Optional[Annotated[int, Field(le=100, strict=True, ge=1)]], '
    'Field(description="Размер страницы")] = '
    'Query(20, description="Размер страницы", alias="size", ge=1, le=100),'
)
PAGE_FIXED = 'page: Optional[int] = Query(0, description="Номер страницы, начиная с 0", ge=0),'
SIZE_FIXED = 'size: Optional[int] = Query(20, description="Размер страницы", ge=1, le=100),'


def main() -> None:
    root = Path(sys.argv[1])
    for filename, needle in CREATE_ROUTES.items():
        path = root / "apis" / filename
        text = path.read_text()
        replacement = needle + "    status_code=201,\n"
        if needle not in text:
            raise SystemExit(f"{filename}: route not found")
        if needle + "    status_code=201,\n" not in text:
            text = text.replace(needle, replacement, 1)
        text = text.replace('= Body(None, description="")', '= Body(..., description="")')
        path.write_text(text)

    products = root / "apis" / "products_api.py"
    text = products.read_text()
    if PAGE_QUERY not in text or SIZE_QUERY not in text:
        # allow already-patched or slightly reformatted generator output
        text = re.sub(
            r"page: Annotated\[Optional\[Annotated\[int, Field\(strict=True, ge=0\)]].*?= Query\(0,[^\n]+",
            PAGE_FIXED,
            text,
            count=1,
            flags=re.S,
        )
        text = re.sub(
            r"size: Annotated\[Optional\[Annotated\[int, Field\(le=100, strict=True, ge=1\)]].*?= Query\(20,[^\n]+",
            SIZE_FIXED,
            text,
            count=1,
            flags=re.S,
        )
    else:
        text = text.replace(PAGE_QUERY, PAGE_FIXED).replace(SIZE_QUERY, SIZE_FIXED)
    if "page: Optional[int] = Query(0" not in text or "size: Optional[int] = Query(20" not in text:
        raise SystemExit("products_api.py: failed to relax page/size query validation")
    products.write_text(text)


if __name__ == "__main__":
    main()
