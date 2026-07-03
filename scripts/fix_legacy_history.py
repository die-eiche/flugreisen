#!/usr/bin/env python3
"""Korrigiert 2-Personen-Preise in der Historie vor Umstellung auf 1 Person."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "history.json"
ALLOWED = {"HAM", "CPH"}
LEGACY_UNTIL = "2026-07-02"


def main() -> None:
    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    new_legs: list[dict] = []

    for entry in history:
        d = entry.get("date", "")
        is_leg = entry.get("record_type") == "leg"
        if d <= LEGACY_UNTIL and not is_leg and (
            entry.get("type") == "combination" or entry.get("ground_cost_eur") is not None
        ):
            for key in ("outbound_price_eur", "inbound_price_eur"):
                if entry.get(key) is not None:
                    entry[key] = round(float(entry[key]) / 2, 2)
            ob = float(entry.get("outbound_price_eur") or 0)
            ib = float(entry.get("inbound_price_eur") or 0)
            ground = float(entry.get("ground_cost_eur") or 0)
            entry["price_eur_per_person"] = round(ob + ib + ground, 2)
            entry["price_eur_total"] = entry["price_eur_per_person"]

            origin = entry.get("origin")
            if origin in ALLOWED:
                cabin = entry.get("cabin")
                fetched = entry.get("fetched_at_utc")
                if ob:
                    new_legs.append(
                        {
                            "date": d,
                            "fetched_at_utc": fetched,
                            "record_type": "leg",
                            "direction": "outbound",
                            "from": origin,
                            "to": "BKK",
                            "origin": origin,
                            "route_label": f"{origin}→BKK",
                            "travel_date": entry.get("departure"),
                            "cabin": cabin,
                            "airline_filter": None,
                            "airlines": entry.get("outbound_airlines") or [],
                            "price_eur_per_person": ob,
                            "price_eur_total": ob,
                        }
                    )
                if ib:
                    new_legs.append(
                        {
                            "date": d,
                            "fetched_at_utc": fetched,
                            "record_type": "leg",
                            "direction": "inbound",
                            "from": "HKT",
                            "to": origin,
                            "origin": origin,
                            "route_label": f"HKT→{origin}",
                            "travel_date": entry.get("return"),
                            "cabin": cabin,
                            "airline_filter": None,
                            "airlines": entry.get("inbound_airlines") or [],
                            "price_eur_per_person": ib,
                            "price_eur_total": ib,
                        }
                    )

    existing = {
        (e.get("date"), e.get("direction"), e.get("from"), e.get("to"), e.get("cabin"))
        for e in history
        if e.get("record_type") == "leg"
    }
    added = 0
    for leg in new_legs:
        key = (leg["date"], leg["direction"], leg["from"], leg["to"], leg["cabin"])
        if key not in existing:
            history.append(leg)
            existing.add(key)
            added += 1

    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"history updated: {len(history)} entries, {added} leg records added")


if __name__ == "__main__":
    main()
