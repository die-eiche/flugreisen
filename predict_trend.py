#!/usr/bin/env python3
"""Preisprognose: historischer Trend + Buchungsfenster-Heuristik für Langstrecken.

Quellen / Methodik (transparent im Output dokumentiert):
1. Eigene Preishistorie (history.json) – lineare Trendschätzung
2. Buchungsfenster-Modell für internationale Langstreckenflüge
   (typisches Muster: günstigste Phase ca. 8–16 Wochen vor Abflug,
    deutlicher Anstieg in den letzten 3–4 Wochen; vgl. Branchenstudien
    von Hopper/ARC zu saisonalen Buchungskurven)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
LATEST_FILE = DATA_DIR / "latest.json"
FORECAST_FILE = DATA_DIR / "forecast.json"
CONFIG_FILE = ROOT / "config.yaml"

# Relativer Preisindex zum optimalen Buchungszeitpunkt (1.0 = Referenz)
# Tage vor Abflug → erwarteter Multiplikator (Mittelwert-Schätzung)
BOOKING_CURVE: list[tuple[int, float]] = [
    (200, 1.02),
    (150, 1.00),
    (120, 0.98),
    (90, 0.96),   # oft Beginn der günstigsten Phase
    (70, 0.95),   # Sweet Spot
    (56, 0.96),
    (42, 1.00),
    (28, 1.06),
    (21, 1.10),
    (14, 1.15),
    (7, 1.22),
    (0, 1.30),
]


@dataclass
class TrendStats:
    slope_per_day: float
    weekly_pct: float
    data_points: int
    direction: str  # falling | stable | rising


def load_config() -> dict:
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def days_until(target: str, as_of: date | None = None) -> int:
    as_of = as_of or date.today()
    dep = date.fromisoformat(target)
    return max((dep - as_of).days, 0)


def booking_curve_factor(days_before: int) -> float:
    """Interpolierter Preisfaktor relativ zum optimalen Buchungsfenster."""
    points = sorted(BOOKING_CURVE, key=lambda x: x[0], reverse=True)
    if days_before >= points[0][0]:
        return points[0][1]
    if days_before <= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        d_hi, f_hi = points[i]
        d_lo, f_lo = points[i + 1]
        if d_lo <= days_before <= d_hi:
            t = (days_before - d_lo) / (d_hi - d_lo)
            return f_lo + t * (f_hi - f_lo)
    return 1.0


def linear_trend(dated_prices: list[tuple[date, float]]) -> TrendStats:
    if len(dated_prices) < 2:
        return TrendStats(0.0, 0.0, len(dated_prices), "stable")

    base = dated_prices[0][0]
    xs = [(d - base).days for d, _ in dated_prices]
    ys = [p for _, p in dated_prices]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0.0

    # Bei wenigen Messpunkten Trend stark dämpfen (Ausreißer / Methodenwechsel)
    if y_mean and n < 14:
        max_weekly_pct = 0.03 if n < 5 else 0.05
        max_slope = y_mean * max_weekly_pct / 7
        slope = max(-max_slope, min(max_slope, slope))
    if n < 3:
        slope = 0.0

    weekly_pct = (slope * 7 / y_mean * 100) if y_mean else 0.0

    if weekly_pct < -0.8:
        direction = "falling"
    elif weekly_pct > 0.8:
        direction = "rising"
    else:
        direction = "stable"

    return TrendStats(slope, round(weekly_pct, 2), n, direction)


ALLOWED_ORIGINS = {"HAM", "CPH", "FRA"}
ORIGIN_LABELS = {"HAM": "Hamburg", "CPH": "Kopenhagen", "FRA": "Frankfurt"}
DIRECTION_LABELS = {
    "outbound": "Hinflug",
    "inbound": "Rückflug",
}


def _route_price(row: dict) -> float | None:
    for key in ("price_eur_per_person", "price_eur_total"):
        if key in row:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def history_for_leg(
    history: list[dict],
    direction: str,
    from_code: str,
    to_code: str,
    cabin: str,
) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for h in history:
        price = _route_price(h)
        if price is None or "date" not in h:
            continue

        if h.get("record_type") == "leg" or h.get("direction"):
            if h.get("direction") != direction:
                continue
            if h.get("from") != from_code or h.get("to") != to_code:
                continue
            if h.get("cabin") != cabin:
                continue
            leg_price = price
        else:
            if h.get("cabin") != cabin:
                continue
            origin = h.get("origin")
            if origin not in ALLOWED_ORIGINS:
                continue
            if direction == "outbound":
                if origin != from_code or to_code != "BKK":
                    continue
                leg_price = h.get("outbound_price_eur")
            elif direction == "inbound":
                if origin != to_code or from_code != "HKT":
                    continue
                leg_price = h.get("inbound_price_eur")
            else:
                continue
            if leg_price is None:
                continue
            try:
                leg_price = float(leg_price)
            except (TypeError, ValueError):
                continue
            price = leg_price

        try:
            rows.append((date.fromisoformat(h["date"]), price))
        except (ValueError, TypeError):
            continue

    by_day: dict[str, float] = {}
    for d, p in rows:
        key = d.isoformat()
        by_day[key] = min(by_day.get(key, p), p)
    return sorted((date.fromisoformat(k), v) for k, v in by_day.items())


def history_for_route(
    history: list[dict],
    origin: str,
    cabin: str,
) -> list[tuple[date, float]]:
    """Abwärtskompatibel – Kombinations-Historie."""
    rows: list[tuple[date, float]] = []
    for h in history:
        if h.get("origin") != origin or h.get("cabin") != cabin:
            continue
        if h.get("origin") not in ALLOWED_ORIGINS:
            continue
        if h.get("record_type") == "leg":
            continue
        price = _route_price(h)
        if price is None or "date" not in h:
            continue
        try:
            rows.append((date.fromisoformat(h["date"]), price))
        except (ValueError, TypeError):
            continue
    # pro Tag nur günstigsten Preis
    by_day: dict[str, float] = {}
    for d, p in rows:
        key = d.isoformat()
        by_day[key] = min(by_day.get(key, p), p)
    return sorted((date.fromisoformat(k), v) for k, v in by_day.items())


def recommend(
    days_to_dep: int,
    trend: TrendStats,
    curve_now: float,
    curve_future: float,
) -> tuple[str, str]:
    """Empfehlungscode und deutscher Text."""
    # Kurve: sind wir über oder unter dem erwarteten Optimum?
    if days_to_dep > 90 and trend.direction == "falling":
        return "wait", "Preise fallen noch – abwarten lohnt sich (ca. 8–12 Wochen vor Abflug erneut prüfen)."
    if days_to_dep > 70 and curve_now > 1.02:
        return "wait", "Ihr seid noch früh dran; der günstigste Buchungszeitraum liegt typischerweise 8–12 Wochen vor Abflug."
    if days_to_dep <= 28:
        return "buy_now", "Weniger als 4 Wochen bis Abflug – Preise steigen in dieser Phase üblicherweise deutlich. Bald buchen."
    if days_to_dep <= 42 and trend.direction != "falling":
        return "buy_soon", "Buchungsfenster schließt sich – in den nächsten 1–2 Wochen buchen empfohlen."
    if trend.direction == "rising" and trend.weekly_pct > 1.5:
        return "buy_soon", "Aufwärtstrend erkennbar – nicht zu lange warten."
    if curve_future < curve_now * 0.97:
        return "wait", "Modell erwartet leicht fallende Preise im optimalen Buchungsfenster."
    if days_to_dep <= 70:
        return "buy_soon", "Ihr befindet euch im typischen Sweet-Spot – gute Preise sind jetzt wahrscheinlich."
    return "monitor", "Preise beobachten; täglicher Tracker zeigt, ob sich Warten lohnt."


def confidence_level(trend: TrendStats) -> str:
    if trend.data_points >= 14:
        return "high"
    if trend.data_points >= 5:
        return "medium"
    return "low"


MIN_PRICE_EUR = 50.0


def _price_bounds(current: float) -> tuple[float, float]:
    """Untere/obere Plausibilitätsgrenze relativ zum aktuellen Preis."""
    floor = max(MIN_PRICE_EUR, current * 0.55)
    ceiling = current * 2.2
    return floor, ceiling


def _clamp_price(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def project_price(
    current: float,
    trend: TrendStats,
    days_ahead: int,
    days_to_dep_now: int,
) -> dict[str, float]:
    """Erwarteter, optimistischer und pessimistischer Preis."""
    floor, ceiling = _price_bounds(current)

    # Historischer Trend
    trend_price = current + trend.slope_per_day * days_ahead

    # Buchungskurven-Anpassung
    future_days_to_dep = max(days_to_dep_now - days_ahead, 0)
    factor_now = booking_curve_factor(days_to_dep_now)
    factor_future = booking_curve_factor(future_days_to_dep)
    curve_adjust = factor_future / factor_now if factor_now else 1.0
    curve_price = current * curve_adjust

    # Gewichtung: wenig Historie → mehr Kurve
    w_hist = min(trend.data_points / 14, 1.0) * 0.6
    w_curve = 1.0 - w_hist
    expected = w_hist * trend_price + w_curve * curve_price
    expected = _clamp_price(expected, floor, ceiling)

    # Unsicherheitsband: nach unten begrenzt, nach oben Buchungsfenster berücksichtigen
    spread_down = 0.05 + (0.04 if trend.data_points < 5 else 0.02)
    spread_up = 0.06 + (0.05 if trend.data_points < 5 else 0.03)
    if future_days_to_dep <= 90:
        spread_up += 0.04
    if curve_adjust > 1.02:
        spread_up += min(0.12, (curve_adjust - 1.0) * 0.5)

    low = _clamp_price(expected * (1 - spread_down), floor, ceiling)
    high = _clamp_price(expected * (1 + spread_up), floor, ceiling)
    if low > expected:
        low = _clamp_price(expected * 0.95, floor, ceiling)
    if high < expected:
        high = _clamp_price(expected * 1.05, floor, ceiling)

    return {
        "expected": round(expected, 2),
        "low": round(low, 2),
        "high": round(high, 2),
    }


def build_projection_curve(
    current_price: float,
    trend: TrendStats,
    days_to_dep: int,
    as_of: date,
    horizon_days: int = 90,
) -> list[dict]:
    curve: list[dict] = []
    step = 7
    for offset in range(0, horizon_days + 1, step):
        d = as_of + timedelta(days=offset)
        proj = project_price(current_price, trend, offset, days_to_dep)
        curve.append(
            {
                "date": d.isoformat(),
                "price_expected": proj["expected"],
                "price_low": proj["low"],
                "price_high": proj["high"],
                "type": "forecast" if offset > 0 else "actual",
            }
        )
    return curve


def analyze_route(
    direction: str,
    origin: str,
    origin_label: str,
    route_label: str,
    from_code: str,
    to_code: str,
    cabin: str,
    departure: str,
    current_price: float,
    history: list[dict],
    as_of: date,
) -> dict:
    dtd = days_until(departure, as_of)
    hist = history_for_leg(history, direction, from_code, to_code, cabin)
    trend = linear_trend(hist)
    curve_now = booking_curve_factor(dtd)

    f30 = project_price(current_price, trend, 30, dtd)
    f60 = project_price(current_price, trend, 60, dtd)
    at_dep = project_price(current_price, trend, dtd, dtd)

    rec_code, rec_text = recommend(dtd, trend, curve_now, booking_curve_factor(max(dtd - 30, 0)))

    change_30 = round((f30["expected"] - current_price) / current_price * 100, 1)
    change_to_dep = round((at_dep["expected"] - current_price) / current_price * 100, 1)

    return {
        "direction": direction,
        "direction_label": DIRECTION_LABELS.get(direction, direction),
        "origin": origin,
        "origin_label": origin_label,
        "route_label": route_label,
        "from": from_code,
        "to": to_code,
        "cabin": cabin,
        "departure": departure,
        "days_until_departure": dtd,
        "current_price_eur": current_price,
        "trend": {
            "direction": trend.direction,
            "weekly_change_pct": trend.weekly_pct,
            "data_points": trend.data_points,
        },
        "booking_window": {
            "factor_now": round(curve_now, 3),
            "phase": _phase_label(dtd),
            "optimal_days_before": "56–90",
        },
        "forecast": {
            "in_30_days": f30,
            "in_60_days": f60,
            "at_departure_if_wait": at_dep,
            "change_30d_pct": change_30,
            "change_to_departure_pct": change_to_dep,
        },
        "recommendation": rec_code,
        "recommendation_de": rec_text,
        "confidence": confidence_level(trend),
        "projection_curve": build_projection_curve(current_price, trend, dtd, as_of),
    }


def _phase_label(days: int) -> str:
    if days > 120:
        return "sehr früh (Preise können noch schwanken)"
    if days > 70:
        return "früh – Sweet Spot nähert sich"
    if days > 42:
        return "Sweet Spot (typisch günstigste Phase)"
    if days > 21:
        return "Buchungsfenster schließt sich"
    return "spät – Preisanstieg wahrscheinlich"


def _best_legs_by_route(legs: list[dict], cabin: str = "PREMIUM_ECONOMY") -> dict[tuple, dict]:
    best: dict[tuple, dict] = {}
    for leg in legs:
        if leg.get("cabin") != cabin:
            continue
        direction = leg.get("direction")
        if direction not in DIRECTION_LABELS:
            continue
        airport = leg["from"] if direction == "outbound" else leg["to"]
        if airport not in ALLOWED_ORIGINS:
            continue
        if direction == "outbound" and leg.get("to") != "BKK":
            continue
        if direction == "inbound" and leg.get("from") != "HKT":
            continue
        key = (direction, airport)
        price = _route_price(leg)
        if price is None:
            continue
        current = best.get(key)
        if not current or price < _route_price(current):
            best[key] = leg
    return best


def _leg_route_label(leg: dict) -> str:
    return f"{leg['from']}→{leg['to']}"


def _leg_origin_label(leg: dict) -> str:
    airport = leg["from"] if leg["direction"] == "outbound" else leg["to"]
    city = ORIGIN_LABELS.get(airport, airport)
    if leg["direction"] == "outbound":
        return f"{city} → Bangkok (BKK)"
    return f"Phuket (HKT) → {city}"


def run_forecast(as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    cfg = load_config()
    history = load_json(HISTORY_FILE, [])
    latest = load_json(LATEST_FILE, {})
    legs = latest.get("legs") or []

    routes: list[dict] = []
    for (_direction, _airport), leg in sorted(_best_legs_by_route(legs).items()):
        price = _route_price(leg)
        if price is None:
            continue
        routes.append(
            analyze_route(
                direction=leg["direction"],
                origin=leg["from"] if leg["direction"] == "outbound" else leg["to"],
                origin_label=_leg_origin_label(leg),
                route_label=_leg_route_label(leg),
                from_code=leg["from"],
                to_code=leg["to"],
                cabin=leg["cabin"],
                departure=leg["date"],
                current_price=price,
                history=history,
                as_of=as_of,
            )
        )

    routes.sort(key=lambda r: (r["direction"], r["current_price_eur"]))

    outbound = [r for r in routes if r["direction"] == "outbound"]
    inbound = [r for r in routes if r["direction"] == "inbound"]
    best = outbound[0] if outbound else (inbound[0] if inbound else None)
    forecast = {
        "generated_at": as_of.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "sources": [
                "Eigene Preishistorie je Einzelstrecke (tägliche Tracker-Daten, lineare Regression)",
                "Getrennte Prognosen für Hinflug (HAM/CPH→BKK) und Rückflug (HKT→HAM/CPH/FRA)",
                "Buchungsfenster-Heuristik für Langstrecken (EU→Asien, 8–16 Wo. vor Abflug)",
            ],
            "disclaimer": (
                "Schätzung ohne Garantie. Keine Finanzberatung. "
                "Tatsächliche Preise hängen von Verfügbarkeit, Sales und Airline ab."
            ),
            "model_version": "1.2",
        },
        "summary": _build_summary(best, outbound, inbound, as_of),
        "routes": routes,
        "outbound_routes": outbound,
        "inbound_routes": inbound,
    }
    return forecast


def _build_summary(
    best: dict | None,
    outbound: list[dict],
    inbound: list[dict],
    as_of: date,
) -> dict:
    if not best:
        return {
            "headline_de": "Noch zu wenig Daten für eine Prognose.",
            "recommendation_de": "Nach einigen Tagen Preistracking wird die Prognose genauer.",
        }

    best_out = outbound[0] if outbound else None
    best_in = inbound[0] if inbound else None
    parts = []
    if best_out:
        parts.append(
            f"Hinflug {best_out['route_label']}: {best_out['current_price_eur']:,.0f} €".replace(",", ".")
        )
    if best_in:
        parts.append(
            f"Rückflug {best_in['route_label']}: {best_in['current_price_eur']:,.0f} €".replace(",", ".")
        )

    f = best["forecast"]
    return {
        "headline_de": " · ".join(parts) if parts else best["origin_label"],
        "best_outbound": best_out["route_label"] if best_out else None,
        "best_inbound": best_in["route_label"] if best_in else None,
        "expected_in_30_days": f["in_30_days"]["expected"],
        "expected_change_30d_pct": f["change_30d_pct"],
        "expected_at_departure": f["at_departure_if_wait"]["expected"],
        "recommendation": best["recommendation"],
        "recommendation_de": best["recommendation_de"],
        "confidence": best["confidence"],
        "days_until_departure": best["days_until_departure"],
    }


def main() -> int:
    forecast = run_forecast()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FORECAST_FILE.open("w", encoding="utf-8") as f:
        json.dump(forecast, f, indent=2, ensure_ascii=False)

    if LATEST_FILE.exists():
        latest = load_json(LATEST_FILE, {})
        latest["forecast"] = forecast
        with LATEST_FILE.open("w", encoding="utf-8") as f:
            json.dump(latest, f, indent=2, ensure_ascii=False)

    print(f"Prognose gespeichert -> {FORECAST_FILE}")
    if forecast.get("summary"):
        print(f"Empfehlung: {forecast['summary'].get('recommendation_de', '–')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
