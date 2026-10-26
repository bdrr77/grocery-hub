"""Compare only current, verified quantities; never treat unknown prices as zero."""
import time


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
