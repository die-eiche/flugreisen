# Thailand Flugpreis-Tracker · Ahrensbök

Tägliche Preisüberwachung für eure Reise ab **Ahrensbök/Lübeck**:

- **Hinflug:** naher Abflughafen → Bangkok (BKK), frühestens 03.02.2027, bevorzugt 04.02.2027 ± einige Tage
- **Rückflug:** Phuket (HKT) → naher Abflughafen, ca. 4 Wochen später
- **Getrennte Flüge** (unterschiedliche Airlines erlaubt) – kein Open Jaw nötig
- **2 Erwachsene**, Premium Economy
- **Gesamtpreis inkl. Anreise** (Bahn nach Kopenhagen, Auto/Bahn nach Hamburg)

## Nahe Abflughäfen (kein Umweg nach München)

| Flughafen | Anreise ab Ahrensbök | ca. Kosten (2 Pers.) |
|-----------|----------------------|----------------------|
| **Lübeck (LBC)** | ~20 min Auto | ~30 € |
| **Hamburg (HAM)** | ~1 h Auto/Bahn | ~50 € |
| **Kopenhagen (CPH)** | ~6 h Bahn (Lübeck Hbf → København H, 1× Umstieg) | ~90 € |
| **Billund (BLL)** | ~3,5 h Auto | ~110 € |

**Bahn Lübeck → Kopenhagen:** EuroCity via Hamburg, Reisezeit ca. 6–6,5 h, Tickets ab ca. 45 €/Person (Super Sparpreis), kein Übernachten nötig bei Morgenabfahrt.

**Lübeck (LBC):** Kein Langstreckenangebot nach Bangkok – nur Ryanair (Europa). Für Thailand über **Hamburg** oder **Kopenhagen**.

## Dashboard

`index.html` im Browser öffnen oder via GitHub Pages deployen.

## Manueller Abruf

```bash
pip install -r requirements.txt
python fetch_prices.py
```

## Preisprognose

Nach jedem Preisabruf berechnet `predict_trend.py` eine **Wahrscheinlichkeits-Schätzung** der Preisentwicklung:

1. **Eigene Historie** – lineare Trendanalyse aus `history.json`
2. **Buchungsfenster-Modell** – Heuristik für Langstrecken (günstigste Phase typisch 8–12 Wochen vor Abflug, Anstieg in den letzten 3–4 Wochen)

Ergebnis: `data/forecast.json` mit Empfehlung (abwarten / bald buchen / jetzt buchen), 30-/60-Tage-Prognose und Prognose-Kurve im Dashboard.

```bash
python predict_trend.py
```

**Hinweis:** Schätzung ohne Garantie – wird mit jedem weiteren Tagespreis genauer (Konfidenz steigt ab ~14 Messpunkten).

## Automatische Aktualisierung

GitHub Actions: `.github/workflows/flight-prices.yml` – täglich 06:00 UTC.

> Eigenständiges Repository: [`die-eiche/flugreisen`](https://github.com/die-eiche/flugreisen) — nicht Teil von GraeberInfo.

## Konfiguration

`config.yaml`: Termine, Flughäfen, Bodenkosten, Kabinen anpassen.
