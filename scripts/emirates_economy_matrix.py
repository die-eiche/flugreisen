#!/usr/bin/env python3
"""Emirates Economy-Preise: Raster HAM↔BKK (Hin Jan, Rück März 2027)."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fli.core import (  # noqa: E402
    build_flight_segments,
    normalize_date,
    parse_airlines,
    parse_cabin_class,
    resolve_airport,
)
from fli.models import FlightSearchFilters, PassengerInfo  # noqa: E402
from fli.search import SearchFlights  # noqa: E402

OUTBOUND_DATES = [
    "2027-01-25",  # Mo
    "2027-01-26",  # Di
    "2027-01-27",  # Mi
    "2027-01-28",  # Do
    "2027-01-29",  # Fr
    "2027-01-30",  # Sa
    "2027-01-31",  # So
]
RETURN_DATES = [
    "2027-03-01",  # Mo
    "2027-03-02",  # Di
    "2027-03-03",  # Mi
    "2027-03-04",  # Do
    "2027-03-05",  # Fr
    "2027-03-06",  # Sa
    "2027-03-07",  # So
]

# Priorität laut Nutzerraster
PRIORITY = {
    ("2027-01-26", "2027-03-02"): "star",
    ("2027-01-26", "2027-03-03"): "star",
    ("2027-01-27", "2027-03-02"): "star",
    ("2027-01-27", "2027-03-03"): "star",
    ("2027-01-25", "2027-03-02"): "good",
    ("2027-01-25", "2027-03-03"): "good",
    ("2027-01-26", "2027-03-01"): "good",
    ("2027-01-26", "2027-03-04"): "good",
    ("2027-01-27", "2027-03-01"): "good",
    ("2027-01-27", "2027-03-04"): "good",
    ("2027-01-28", "2027-03-02"): "good",
    ("2027-01-28", "2027-03-03"): "good",
}

OUT_FILE = ROOT / "data" / "emirates-economy-matrix-2027-01.json"
DELAY = 1.2
ADULTS = 1
CABIN = "ECONOMY"
AIRLINE = "EK"


def _airline_code(leg) -> str:
    airline = getattr(leg, "airline", None)
    if airline is None:
        return ""
    name = getattr(airline, "name", "") or str(airline)
    return name.removeprefix("_")[:2] if name else ""


def _airlines_from_item(item) -> set[str]:
    airlines: set[str] = set()
    if isinstance(item, tuple):
        legs = item
    else:
        legs = getattr(item, "legs", []) or [item]
    for leg in legs:
        code = _airline_code(leg)
        if code:
            airlines.add(code)
    return airlines


def _price_from_item(item) -> float:
    if isinstance(item, tuple):
        return sum(getattr(leg, "price", 0) or 0 for leg in item)
    return getattr(item, "price", 0) or 0


def _serialize_legs(item) -> list[dict]:
    flights = item if isinstance(item, tuple) else (item,)
    out: list[dict] = []
    for flight in flights:
        segs = []
        for leg in getattr(flight, "legs", []) or []:
            dep = getattr(leg, "departure_datetime", None)
            arr = getattr(leg, "arrival_datetime", None)
            segs.append(
                {
                    "airline": _airline_code(leg),
                    "flight_number": getattr(leg, "flight_number", None),
                    "from": str(getattr(getattr(leg, "departure_airport", None), "name", "") or "").removeprefix("_"),
                    "to": str(getattr(getattr(leg, "arrival_airport", None), "name", "") or "").removeprefix("_"),
                    "departure": dep.isoformat() if dep else None,
                    "arrival": arr.isoformat() if arr else None,
                    "duration_minutes": getattr(leg, "duration", None),
                    "aircraft": getattr(leg, "aircraft", None),
                }
            )
        out.append(
            {
                "price": getattr(flight, "price", None),
                "duration_minutes": getattr(flight, "duration", None),
                "stops": getattr(flight, "stops", None),
                "segments": segs,
            }
        )
    return out


def search_round_trip(dep: str, ret: str) -> dict:
    segments, trip_type = build_flight_segments(
        resolve_airport("HAM"),
        resolve_airport("BKK"),
        normalize_date(dep),
        return_date=normalize_date(ret),
    )
    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(adults=ADULTS),
        flight_segments=segments,
        seat_type=parse_cabin_class(CABIN),
        airlines=parse_airlines([AIRLINE]),
    )
    client = SearchFlights()
    data = client.search(
        filters,
        top_n=8,
        currency="EUR",
        language="de",
        country="DE",
    )
    if not data:
        return {"ok": False, "error": "no_results"}

    best_price = float("inf")
    best_item = None
    best_airlines: set[str] = set()

    for item in data[:12]:
        price = _price_from_item(item)
        if not price:
            continue
        airlines = _airlines_from_item(item)
        if AIRLINE not in airlines:
            continue
        if price < best_price:
            best_price = price
            best_item = item
            best_airlines = airlines

    if best_item is None:
        return {"ok": False, "error": "no_emirates"}

    return {
        "ok": True,
        "price_eur": round(best_price, 2),
        "airlines": sorted(best_airlines),
        "flights": _serialize_legs(best_item),
    }


def ordered_pairs() -> list[tuple[str, str]]:
    """★ zuerst, dann ●, dann Rest."""
    stars, goods, rest = [], [], []
    for dep in OUTBOUND_DATES:
        for ret in RETURN_DATES:
            key = (dep, ret)
            prio = PRIORITY.get(key)
            if prio == "star":
                stars.append(key)
            elif prio == "good":
                goods.append(key)
            else:
                rest.append(key)
    return stars + goods + rest


def fmt_eur(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}".replace(",", ".")


def print_matrix(cells: dict[tuple[str, str], dict]) -> None:
    out_labels = ["Mo 25.1.", "Di 26.1.", "Mi 27.1.", "Do 28.1.", "Fr 29.1.", "Sa 30.1.", "So 31.1."]
    ret_labels = ["Mo 1.3.", "Di 2.3.", "Mi 3.3.", "Do 4.3.", "Fr 5.3.", "Sa 6.3.", "So 7.3."]

    header = "| Hinflug HAM→BKK ↓ / Rückflug BKK→HAM → | " + " | ".join(ret_labels) + " |"
    sep = "| -------------------------------------- | " + " | ".join(["------:"] * 7) + " |"
    print()
    print(header)
    print(sep)
    for i, dep in enumerate(OUTBOUND_DATES):
        row = [f"**{out_labels[i]}**"]
        for ret in RETURN_DATES:
            cell = cells.get((dep, ret), {})
            if cell.get("ok"):
                price = fmt_eur(cell["price_eur"])
                mark = PRIORITY.get((dep, ret))
                if mark == "star":
                    row.append(f"**{price}**")
                else:
                    row.append(price)
            else:
                row.append("—")
        print("| " + " | ".join(row) + " |")
    print()


def main() -> int:
    cells: dict[tuple[str, str], dict] = {}
    pairs = ordered_pairs()
    print(
        f"Emirates Economy HAM↔BKK · {len(pairs)} Termine · "
        f"{datetime.now(timezone.utc).isoformat()}"
    )

    for i, (dep, ret) in enumerate(pairs, 1):
        label = PRIORITY.get((dep, ret), "other")
        try:
            result = search_round_trip(dep, ret)
            cells[(dep, ret)] = result
            if result.get("ok"):
                print(f"[{i}/{len(pairs)}] ✓ {dep}→{ret} ({label}): {result['price_eur']:.0f} €")
            else:
                print(f"[{i}/{len(pairs)}] ✗ {dep}→{ret} ({label}): {result.get('error')}")
        except Exception as exc:  # noqa: BLE001
            cells[(dep, ret)] = {"ok": False, "error": str(exc)}
            print(f"[{i}/{len(pairs)}] ✗ {dep}→{ret} ({label}): {exc}")
        time.sleep(DELAY)

    prices = [c["price_eur"] for c in cells.values() if c.get("ok")]
    snapshot = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "airline": "EK",
        "cabin": CABIN,
        "route": "HAM→BKK / BKK→HAM",
        "passengers": ADULTS,
        "currency": "EUR",
        "outbound_dates": OUTBOUND_DATES,
        "return_dates": RETURN_DATES,
        "cells": [
            {
                "departure": dep,
                "return": ret,
                "priority": PRIORITY.get((dep, ret), "other"),
                **cells[(dep, ret)],
            }
            for dep in OUTBOUND_DATES
            for ret in RETURN_DATES
        ],
        "summary": {
            "ok_count": len(prices),
            "fail_count": len(cells) - len(prices),
            "min_eur": min(prices) if prices else None,
            "max_eur": max(prices) if prices else None,
            "avg_eur": round(sum(prices) / len(prices), 2) if prices else None,
            "cheapest": (
                min(
                    (
                        {
                            "departure": dep,
                            "return": ret,
                            "price_eur": cells[(dep, ret)]["price_eur"],
                        }
                        for dep, ret in cells
                        if cells[(dep, ret)].get("ok")
                    ),
                    key=lambda x: x["price_eur"],
                )
                if prices
                else None
            ),
        },
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print_matrix(cells)
    s = snapshot["summary"]
    print(
        f"OK {s['ok_count']}/{len(cells)} · "
        f"min {fmt_eur(s['min_eur'])} € · max {fmt_eur(s['max_eur'])} € · "
        f"Ø {fmt_eur(s['avg_eur'])} €"
    )
    if s.get("cheapest"):
        c = s["cheapest"]
        print(f"Günstigste: {c['departure']} → {c['return']}: {fmt_eur(c['price_eur'])} €")
    print(f"Gespeichert: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
