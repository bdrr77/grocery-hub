"""Compare only current, verified quantities; never treat unknown prices as zero."""
import time
import re
from decimal import Decimal, ROUND_HALF_UP


def pack_pricing(size, price_cents):
    """Only normalize unambiguous pack labels; shopping quantity is independent."""
    result = {"size": size or "", "unit_price_cents": None, "unit_price_unit": None}
    label = (size or "").lower().strip().replace("\u00a0", " ")
    label = re.sub(r"^(?:(?:le|la|les)\s+)?(?:pot|bouteille|paquet|boîte|boite|sachet|barquette|lot)\s+de\s+", "", label)
    match = re.fullmatch(r"(?:(\d+)\s*[x×*]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|mg|ml|cl|dl|l|pièces?|pieces?|unités?|unites?|pcs?|œufs|oeufs)", label)
    if not match or not isinstance(price_cents, int) or price_cents < 0:
        return result
    count, amount, unit = match.groups()
    quantity = Decimal(count or "1") * Decimal(amount.replace(",", "."))
    factors = {"kg": ("kg", "1"), "g": ("kg", ".001"), "mg": ("kg", ".000001"), "l": ("L", "1"), "dl": ("L", ".1"), "cl": ("L", ".01"), "ml": ("L", ".001")}
    basis, factor = factors.get(unit, ("pièce", "1"))
    quantity *= Decimal(factor)
    if quantity > 0:
        result.update(unit_price_cents=int((Decimal(price_cents) / quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), unit_price_unit=basis)
    return result


def product_card(job, snapshot):
    product = next((p for p in job.get("candidates", []) if p["id"] == job["product_id"]), {})
    line = next((p for p in (snapshot or {}).get("data", {}).get("lines", []) if p["id"] == job["product_id"]), {})
    return pack_pricing(line.get("size") or product.get("size", ""), job["price_cents"])


def summarize(snapshot, jobs, fingerprint, connected, now=None):
    now = time.time() if now is None else now
    valid = bool(snapshot and snapshot["fingerprint"] == fingerprint)
    fresh = bool(valid and connected and now - snapshot["fetched_at"] <= 300)
    data = snapshot["data"] if valid else {}
    lines = data.get("lines", [])
    known = 0
    priced = 0
    selected_ids = {job["product_id"] for job in jobs if job["product_id"]}
    for job in jobs:
        matched = [line for line in lines if line["id"] == job["product_id"]]
        if fresh and job["status"] == "synced" and matched and sum(line["quantity"] for line in matched) == job["quantity"] and all(isinstance(line.get("line_total_cents"), int) for line in matched):
            known += sum(line["line_total_cents"] for line in matched)
            priced += 1
    complete = bool(fresh and jobs and priced == len(jobs))
    return {
        "currency":"EUR", "fresh":fresh, "fetched_at":snapshot["fetched_at"] if valid else None,
        "list_complete":complete, "list_total_cents":known if complete else None,
        "known_list_total_cents":known if fresh else None, "priced_items":priced, "item_count":len(jobs),
        "missing_items":len(jobs)-priced, "cart_total_cents":data.get("total_cents") if fresh else None,
        "deposit_cents":data.get("deposit_cents") if fresh else None,
        "fees_cents":data.get("fees_cents") if fresh else None,
        "discount_cents":data.get("discount_cents") if fresh else None,
        "other_cart_items":sum(1 for line in lines if line["id"] not in selected_ids) if valid else None,
    }
