"""Stable commodity-sector taxonomy for cross-sectional research."""

from __future__ import annotations

import re

UNCLASSIFIED_SECTOR = "未分类"
_RETURN_INDEX_PRODUCT = re.compile(r"^([A-Za-z]+)6666$")

SECTOR_PRODUCTS = {
    "贵金属": frozenset({"ag", "au", "pd", "pt"}),
    "有色金属": frozenset({"ad", "al", "ao", "bc", "cu", "ni", "pb", "sn", "zn"}),
    "新能源材料": frozenset({"lc", "ps", "si"}),
    "黑色": frozenset({"hc", "i", "j", "jm", "rb", "sf", "sm", "ss", "wr", "zc"}),
    "能源化工": frozenset({
        "br", "bu", "bz", "eb", "eg", "fu", "l", "lu", "ma", "nr", "pf",
        "pg", "pl", "pp", "pr", "px", "ru", "sc", "sh", "ta", "ur", "v",
    }),
    "油脂油料": frozenset({"a", "b", "m", "oi", "p", "pk", "rm", "rs", "y"}),
    "谷物": frozenset({"c", "cs", "jr", "lr", "pm", "ri", "rr", "wh"}),
    "软商品": frozenset({"ap", "cf", "cj", "cy", "sr"}),
    "畜牧": frozenset({"jd", "lh"}),
    "林产建材": frozenset({"bb", "fb", "fg", "lg", "op", "sa", "sp"}),
    "航运": frozenset({"ec"}),
}

PRODUCT_SECTORS = {
    product: sector
    for sector, products in SECTOR_PRODUCTS.items()
    for product in products
}


def return_index_product(code: str) -> str | None:
    """Extract a normalized product prefix from an official 6666 index code."""
    match = _RETURN_INDEX_PRODUCT.fullmatch(str(code).strip())
    return match.group(1).lower() if match else None


def commodity_sector(code: str) -> str:
    """Return the stable sector label, preserving unknown products as unclassified."""
    product = return_index_product(code)
    if product is None:
        return UNCLASSIFIED_SECTOR
    return PRODUCT_SECTORS.get(product, UNCLASSIFIED_SECTOR)
