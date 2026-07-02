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
    weekly_pct = (slope * 7 / y_mean * 100) if y_mean else 0.0

    if weekly_pct < -0.8:
        direction = "falling"
    elif weekly_pct > 0.8:
        direction = "rising"
    else:
        direction = "stable"

    return TrendStats(slope, round(weekly_pct, 2), n, direction)


def history_for_route(
    history: list[dict],
    origin: str,
    cabin: str,
) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for h in history:
        if h.get("origin") != origin or h.get("cabin") != cabin:
            continue
        if "price_eur_total" not in h or "date" not in h:
            continue
        try:
            rows.append((date.fromisoformat(h["date"]), float(h["price_eur_total"])))
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


def project_price(
    current: float,
    trend: TrendStats,
    days_ahead: int,
    days_to_dep_now: int,
) -> dict[str, float]:
    """Erwarteter, optimistischer und pessimistischer Preis."""
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

    spread = 0.04 + (0.02 if trend.data_points < 5 else 0.01)
    return {
        "expected": round(expected, 2),
        "low": round(expected * (1 - spread), 2),
        "high": round(expected * (1 + spread), 2),
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
    origin: str,
    origin_label: str,
    cabin: str,
    departure: str,
    current_price: float,
    history: list[dict],
    as_of: date,
) -> dict:
    dtd = days_until(departure, as_of)
    hist = history_for_route(history, origin, cabin)
    trend = linear_trend(hist)
    curve_now = booking_curve_factor(dtd)

    f30 = project_price(current_price, trend, 30, dtd)
    f60 = project_price(current_price, trend, 60, dtd)
    at_dep = project_price(current_price, trend, dtd, dtd)

    rec_code, rec_text = recommend(dtd, trend, curve_now, booking_curve_factor(max(dtd - 30, 0)))

    change_30 = round((f30["expected"] - current_price) / current_price * 100, 1)
    change_to_dep = round((at_dep["expected"] - current_price) / current_price * 100, 1)

    return {
        "origin": origin,
        "origin_label": origin_label,
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


def run_forecast(as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    cfg = load_config()
    history = load_json(HISTORY_FILE, [])
    latest = load_json(LATEST_FILE, {})
    combinations = latest.get("combinations") or []

    # Primäres Abflugdatum aus Config
    dep_date = cfg["date_pairs"][0]["departure"]

    routes: list[dict] = []
    for combo in combinations:
        if combo.get("cabin") != "PREMIUM_ECONOMY":
            continue
        routes.append(
            analyze_route(
                origin=combo["origin"],
                origin_label=combo.get("origin_label", combo["origin"]),
                cabin=combo["cabin"],
                departure=combo.get("departure", dep_date),
                current_price=float(combo["price_eur_total"]),
                history=history,
                as_of=as_of,
            )
        )

    routes.sort(key=lambda r: r["current_price_eur"])

    best = routes[0] if routes else None
    forecast = {
        "generated_at": as_of.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "sources": [
                "Eigene Preishistorie (tägliche Tracker-Daten, lineare Regression)",
                "Buchungsfenster-Heuristik für Langstrecken (EU→Asien, 8–16 Wo. vor Abflug)",
            ],
            "disclaimer": (
                "Schätzung ohne Garantie. Keine Finanzberatung. "
                "Tatsächliche Preise hängen von Verfügbarkeit, Sales und Airline ab."
            ),
            "model_version": "1.0",
        },
        "summary": _build_summary(best, as_of, dep_date),
        "routes": routes,
    }
    return forecast


def _build_summary(best: dict | None, as_of: date, dep_date: str) -> dict:
    if not best:
        return {
            "headline_de": "Noch zu wenig Daten für eine Prognose.",
            "recommendation_de": "Nach einigen Tagen Preistracking wird die Prognose genauer.",
        }
    dtd = days_until(dep_date, as_of)
    f = best["forecast"]
    return {
        "headline_de": (
            f"Günstigste Option: {best['origin_label']} – aktuell "
            f"{best['current_price_eur']:,.0f} €".replace(",", ".")
        ),
        "expected_in_30_days": f["in_30_days"]["expected"],
        "expected_change_30d_pct": f["change_30d_pct"],
        "expected_at_departure": f["at_departure_if_wait"]["expected"],
        "recommendation": best["recommendation"],
        "recommendation_de": best["recommendation_de"],
        "confidence": best["confidence"],
        "days_until_departure": dtd,
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

    print(f"Prognose gespeichert → {FORECAST_FILE}")
    if forecast.get("summary"):
        print(f"Empfehlung: {forecast['summary'].get('recommendation_de', '–')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
