#!/usr/bin/env python3
"""Täglicher Flugpreis-Abruf: getrennte Hin-/Rückflüge inkl. Bodenkosten ab Ahrensbök."""

from __future__ import annotations

import itertools
import json
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from fli.core import (
    build_flight_segments,
    build_multi_city_segments,
    normalize_date,
    parse_airlines,
    parse_cabin_class,
    resolve_airport,
)
from fli.models import FlightSearchFilters, PassengerInfo
from fli.search import SearchFlights

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
LATEST_FILE = DATA_DIR / "latest.json"
CONFIG_FILE = ROOT / "config.yaml"

ALLOWED_ORIGINS = {"HAM", "CPH"}

AEROLOPA_AIRLINE = {
    "SQ": "sq",
    "CX": "cx",
    "TG": "tg",
    "LH": "lh",
    "AF": "af",
    "KL": "kl",
    "TK": "tk",
    "QR": "qr",
    "EK": "ek",
    "EY": "ey",
    "AY": "ay",
    "SK": "sk",
    "BA": "ba",
    "LX": "lx",
    "OS": "os",
}


def load_config() -> dict:
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _airline_code(leg) -> str:
    airline = getattr(leg, "airline", None)
    if airline is None:
        return ""
    name = getattr(airline, "name", "") or str(airline)
    return name.removeprefix("_")[:2] if name else ""


def _airport_code(airport) -> str:
    if airport is None:
        return ""
    name = getattr(airport, "name", "") or str(airport)
    return name.removeprefix("_")


def format_duration(minutes: int | None) -> str | None:
    if not minutes:
        return None
    hours, mins = divmod(int(minutes), 60)
    if mins:
        return f"{hours}h {mins}min"
    return f"{hours}h"


def build_google_flights_url(
    origin: str,
    destination: str,
    travel_date: str,
    currency: str,
    language: str,
    country: str,
) -> str:
    query = urllib.parse.quote(f"Flights to {destination} from {origin} on {travel_date}")
    return (
        f"https://www.google.com/travel/flights"
        f"?q={query}&curr={currency}&hl={language}&gl={country}"
    )


def build_aircraft_review_url(airline_code: str, aircraft: str | None) -> str:
    """AeroLOPA: maßstabsgetreue Sitzpläne und Kabinenbewertungen."""
    slug = AEROLOPA_AIRLINE.get(airline_code)
    if slug:
        return f"https://www.aerolopa.com/{slug}"
    return "https://www.aerolopa.com/"


def _serialize_flight_details(
    flight,
    currency: str,
    language: str,
    country: str,
) -> dict:
    segments: list[dict] = []
    aircraft_all: list[str] = []
    primary_airline = ""

    for leg in getattr(flight, "legs", []) or []:
        dep_dt = getattr(leg, "departure_datetime", None)
        arr_dt = getattr(leg, "arrival_datetime", None)
        airline = _airline_code(leg)
        if airline and not primary_airline:
            primary_airline = airline
        aircraft = getattr(leg, "aircraft", None)
        if aircraft:
            aircraft_all.append(aircraft)
        segments.append(
            {
                "airline": airline,
                "flight_number": getattr(leg, "flight_number", None),
                "from": _airport_code(getattr(leg, "departure_airport", None)),
                "to": _airport_code(getattr(leg, "arrival_airport", None)),
                "departure_time": dep_dt.strftime("%H:%M") if dep_dt else None,
                "arrival_time": arr_dt.strftime("%H:%M") if arr_dt else None,
                "departure_datetime": dep_dt.isoformat() if dep_dt else None,
                "arrival_datetime": arr_dt.isoformat() if arr_dt else None,
                "duration_minutes": getattr(leg, "duration", None),
                "duration": format_duration(getattr(leg, "duration", None)),
                "aircraft": aircraft,
            }
        )

    primary_aircraft = aircraft_all[0] if aircraft_all else None
    origin = segments[0]["from"] if segments else ""
    destination = segments[-1]["to"] if segments else ""
    travel_date = (
        segments[0]["departure_datetime"][:10]
        if segments and segments[0].get("departure_datetime")
        else None
    )

    return {
        "departure_time": segments[0]["departure_time"] if segments else None,
        "arrival_time": segments[-1]["arrival_time"] if segments else None,
        "duration_minutes": getattr(flight, "duration", None),
        "duration": format_duration(getattr(flight, "duration", None)),
        "stops": getattr(flight, "stops", 0),
        "aircraft": primary_aircraft,
        "aircraft_all": list(dict.fromkeys(aircraft_all)),
        "segments": segments,
        "booking_url": (
            build_google_flights_url(
                origin, destination, travel_date, currency, language, country
            )
            if origin and destination and travel_date
            else None
        ),
        "aircraft_review_url": build_aircraft_review_url(primary_airline, primary_aircraft),
    }


def _airlines_from_result(item) -> set[str]:
    airlines: set[str] = set()
    if isinstance(item, tuple):
        legs = item
    else:
        legs = getattr(item, "legs", []) or [item]
    for leg in legs:
        for seg in getattr(leg, "segments", []) or []:
            code = getattr(seg, "airline_code", "") or ""
            if code:
                airlines.add(code[:2])
    return airlines


def _price_from_result(item) -> float:
    if isinstance(item, tuple):
        return sum(getattr(leg, "price", 0) or 0 for leg in item)
    return getattr(item, "price", 0) or 0


def search_one_way(
    origin: str,
    destination: str,
    travel_date: str,
    cabin: str,
    adults: int,
    airline_code: str | None,
    currency: str,
    language: str,
    country: str,
    direction: str,
) -> dict | None:
    segments, trip_type = build_flight_segments(
        resolve_airport(origin),
        resolve_airport(destination),
        normalize_date(travel_date),
    )
    airlines = parse_airlines([airline_code]) if airline_code else None

    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(adults=adults),
        flight_segments=segments,
        seat_type=parse_cabin_class(cabin),
        airlines=airlines,
    )

    client = SearchFlights()
    data = client.search(
        filters,
        top_n=5,
        currency=currency,
        language=language,
        country=country,
    )
    if not data:
        return None

    best_price = float("inf")
    best_airlines: set[str] = set()
    best_flight = None

    for item in data[:10]:
        price = _price_from_result(item)
        if not price:
            continue
        airlines_found = _airlines_from_result(item)
        if airline_code and airline_code not in airlines_found:
            continue
        if price < best_price:
            best_price = price
            best_airlines = airlines_found
            best_flight = item if not isinstance(item, tuple) else item[0]

    if best_price == float("inf") or best_flight is None:
        return None

    details = _serialize_flight_details(
        best_flight, currency, language, country
    )

    return {
        "type": "leg",
        "direction": direction,
        "from": origin,
        "to": destination,
        "date": travel_date,
        "cabin": cabin,
        "airline_filter": airline_code,
        "airlines": sorted(best_airlines),
        "price_eur_total": round(best_price, 2),
        "price_eur_per_person": round(best_price / adults, 2),
        **details,
    }


def search_open_jaw(
    origin: str,
    departure: str,
    return_date: str,
    cabin: str,
    adults: int,
    airline_code: str | None,
    currency: str,
    language: str,
    country: str,
) -> dict | None:
    parsed_legs = [
        (resolve_airport(origin), resolve_airport("BKK"), normalize_date(departure)),
        (resolve_airport("HKT"), resolve_airport(origin), normalize_date(return_date)),
    ]
    segments, trip_type = build_multi_city_segments(legs=parsed_legs)
    airlines = parse_airlines([airline_code]) if airline_code else None

    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(adults=adults),
        flight_segments=segments,
        seat_type=parse_cabin_class(cabin),
        airlines=airlines,
    )

    client = SearchFlights()
    data = client.search(
        filters,
        top_n=5,
        currency=currency,
        language=language,
        country=country,
    )
    if not data:
        return None

    best_price = float("inf")
    best_airlines: set[str] = set()

    for item in data[:8]:
        if not isinstance(item, tuple):
            continue
        total = _price_from_result(item)
        airlines_found = _airlines_from_result(item)
        if airline_code and airline_code not in airlines_found:
            continue
        if total < best_price:
            best_price = total
            best_airlines = airlines_found

    if best_price == float("inf"):
        return None

    return {
        "type": "open_jaw",
        "origin": origin,
        "route": f"{origin}→BKK / HKT→{origin}",
        "departure": departure,
        "return": return_date,
        "cabin": cabin,
        "airline_filter": airline_code,
        "airlines": sorted(best_airlines),
        "price_eur_total": round(best_price, 2),
        "price_eur_per_person": round(best_price / adults, 2),
    }


def build_combinations(
    legs: list[dict],
    origins_cfg: list[dict],
    adults: int,
    date_pair: dict,
) -> list[dict]:
    """Kombiniert getrennte Hin- und Rückflüge inkl. Bodenkosten."""
    outbound = [l for l in legs if l.get("direction") == "outbound"]
    inbound = [l for l in legs if l.get("direction") == "inbound"]
    if not outbound or not inbound:
        return []

    origin_map = {o["code"]: o for o in origins_cfg}
    combos: list[dict] = []

    for out_leg, in_leg in itertools.product(outbound, inbound):
        out_origin = out_leg["from"]
        in_dest = in_leg["to"]
        if out_origin != in_dest:
            continue
        if out_leg["cabin"] != in_leg["cabin"]:
            continue

        ground = origin_map.get(out_origin, {})
        ground_pp = float(ground.get("ground_from_home_eur", 0))
        ground_total = round(ground_pp * adults, 2)
        flight_total = out_leg["price_eur_per_person"] + in_leg["price_eur_per_person"]
        grand_total = round(flight_total + ground_total, 2)

        combos.append(
            {
                "type": "combination",
                "origin": out_origin,
                "origin_label": ground.get("label", out_origin),
                "departure": date_pair["departure"],
                "return": date_pair["return"],
                "cabin": out_leg["cabin"],
                "outbound_airlines": out_leg["airlines"],
                "inbound_airlines": in_leg["airlines"],
                "outbound_price_eur": out_leg["price_eur_per_person"],
                "inbound_price_eur": in_leg["price_eur_per_person"],
                "outbound_departure_time": out_leg.get("departure_time"),
                "outbound_duration": out_leg.get("duration"),
                "outbound_aircraft": out_leg.get("aircraft"),
                "outbound_aircraft_review_url": out_leg.get("aircraft_review_url"),
                "outbound_booking_url": out_leg.get("booking_url"),
                "inbound_departure_time": in_leg.get("departure_time"),
                "inbound_duration": in_leg.get("duration"),
                "inbound_aircraft": in_leg.get("aircraft"),
                "inbound_aircraft_review_url": in_leg.get("aircraft_review_url"),
                "inbound_booking_url": in_leg.get("booking_url"),
                "ground_mode": ground.get("ground_mode", "Auto/Bahn"),
                "ground_hours": ground.get("ground_hours"),
                "ground_cost_eur": ground_total,
                "overnight_needed": ground.get("overnight_needed", False),
                "price_eur_total": grand_total,
                "price_eur_per_person": grand_total,
                "route": (
                    f"Zuhause → {out_origin} → BKK  |  "
                    f"HKT → {in_dest} → Zuhause"
                ),
            }
        )

    combos.sort(key=lambda c: c["price_eur_total"])
    return combos


def append_history(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(encoding="utf-8") as f:
            history = json.load(f)
    history.append(entry)
    with HISTORY_FILE.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def main() -> int:
    cfg = load_config()
    adults = int(cfg.get("passengers", 2))
    currency = cfg.get("currency", "EUR")
    language = cfg.get("language", "de")
    country = cfg.get("country", "DE")
    origins_cfg = [
        o for o in (cfg.get("flight_origins") or [])
        if o.get("code") in ALLOWED_ORIGINS
    ]
    if not origins_cfg:
        origins_cfg = [
            {"code": c, "label": c, "ground_from_home_eur": 0}
            for c in cfg.get("origins", [])
            if c in ALLOWED_ORIGINS
        ]
    origin_codes = [o["code"] for o in origins_cfg]
    date_pairs = cfg["date_pairs"]
    cabins = cfg["cabins"]
    preferred = cfg.get("preferred_airlines", [])
    search_all = cfg.get("search_all_airlines", True)
    search_mode = cfg.get("search_mode", "separate")
    compute_combos = cfg.get("compute_combinations", True)
    delay = cfg.get("request_delay_seconds", 1.5)

    airline_filters: list[str | None] = []
    if search_all:
        airline_filters.append(None)
    airline_filters.extend(preferred)

    today = date.today().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()
    legs: list[dict] = []
    open_jaw_results: list[dict] = []
    combinations: list[dict] = []

    for pair in date_pairs:
        dep = pair["departure"]
        ret = pair["return"]
        pair_legs: list[dict] = []

        if search_mode in ("separate", "both"):
            for origin in origin_codes:
                for cabin in cabins:
                    for airline in airline_filters:
                        for direction, fro, to in [
                            ("outbound", origin, "BKK"),
                            ("inbound", "HKT", origin),
                        ]:
                            travel_date = dep if direction == "outbound" else ret
                            try:
                                row = search_one_way(
                                    origin=fro,
                                    destination=to,
                                    travel_date=travel_date,
                                    cabin=cabin,
                                    adults=adults,
                                    airline_code=airline,
                                    currency=currency,
                                    language=language,
                                    country=country,
                                    direction=direction,
                                )
                                if row:
                                    legs.append(row)
                                    pair_legs.append(row)
                                    print(
                                        f"✓ {direction} {fro}→{to} {cabin} "
                                        f"{airline or 'ANY'}: {row['price_eur_total']} EUR"
                                    )
                            except Exception as exc:  # noqa: BLE001
                                print(f"✗ {direction} {fro}→{to} {cabin} {airline}: {exc}")
                            time.sleep(delay)

        if search_mode in ("open_jaw", "both"):
            for origin in origin_codes:
                for cabin in cabins:
                    for airline in airline_filters:
                        try:
                            row = search_open_jaw(
                                origin=origin,
                                departure=dep,
                                return_date=ret,
                                cabin=cabin,
                                adults=adults,
                                airline_code=airline,
                                currency=currency,
                                language=language,
                                country=country,
                            )
                            if row:
                                open_jaw_results.append(row)
                                print(
                                    f"✓ open_jaw {origin} {cabin} "
                                    f"{airline or 'ANY'}: {row['price_eur_total']} EUR"
                                )
                        except Exception as exc:  # noqa: BLE001
                            print(f"✗ open_jaw {origin} {cabin} {airline}: {exc}")
                        time.sleep(delay)

        if compute_combos and pair_legs:
            combos = build_combinations(pair_legs, origins_cfg, adults, pair)
            combinations.extend(combos)

    combinations.sort(key=lambda c: c["price_eur_total"])

    snapshot = {
        "fetched_at": today,
        "fetched_at_utc": fetched_at,
        "home_location": cfg.get("home_location", "Ahrensbök / Lübeck"),
        "route_description": (
            "Getrennte Flüge: Europa → Bangkok (BKK), Phuket (HKT) → Europa "
            "(inkl. Bodenkosten ab Zuhause)"
        ),
        "passengers": adults,
        "preferred_airlines": preferred,
        "search_mode": search_mode,
        "legs": legs,
        "combinations": combinations,
        "open_jaw": open_jaw_results,
        # Abwärtskompatibel für Dashboard-Tabelle
        "results": combinations or open_jaw_results or legs,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LATEST_FILE.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    for row in combinations[:20]:
        append_history(
            {
                "date": today,
                "fetched_at_utc": fetched_at,
                "record_type": "combination",
                **row,
                "price_eur_total": row["price_eur_per_person"],
            }
        )

    leg_keys: set[tuple] = set()
    for leg in legs:
        if leg.get("from") not in ALLOWED_ORIGINS and leg.get("to") not in ALLOWED_ORIGINS:
            continue
        key = (leg["direction"], leg["from"], leg["to"], leg["cabin"], leg.get("airline_filter"))
        if key in leg_keys:
            continue
        leg_keys.add(key)
        append_history(
            {
                "date": today,
                "fetched_at_utc": fetched_at,
                "record_type": "leg",
                "direction": leg["direction"],
                "from": leg["from"],
                "to": leg["to"],
                "origin": leg["from"] if leg["direction"] == "outbound" else leg["to"],
                "route_label": f"{leg['from']}→{leg['to']}",
                "travel_date": leg["date"],
                "cabin": leg["cabin"],
                "airline_filter": leg.get("airline_filter"),
                "airlines": leg.get("airlines", []),
                "price_eur_per_person": leg["price_eur_per_person"],
                "price_eur_total": leg["price_eur_per_person"],
            }
        )

    print(f"\n{len(legs)} Einzelflüge, {len(combinations)} Kombinationen → {LATEST_FILE}")

    try:
        from predict_trend import run_forecast

        forecast = run_forecast()
        snapshot["forecast"] = forecast
        with LATEST_FILE.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"Prognose aktualisiert → {DATA_DIR / 'forecast.json'}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ Prognose übersprungen: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
