# Flight-Tracker aktivieren (einmalig)

GitHub Pages kann vom Cursor-Agenten **nicht automatisch** aktiviert werden – dafür braucht es **Admin-Rechte** am Repository.

## Dauerhafte Lösung (2 Minuten, einmalig)

1. Repository-Einstellungen → **Pages** öffnen  
   *(z. B. `https://github.com/die-eiche/flugreisen/settings/pages` – nur sichtbar als Besitzer)*

2. **Build and deployment:**
   - Source: **GitHub Actions**

3. Speichern – der Workflow **„Thailand Flug-Tracker Dashboard (GitHub Pages)“** deployt dann automatisch bei jedem Push auf `main`.

4. Nach 1–2 Minuten ist das Dashboard erreichbar unter der Pages-URL des Repositories (z. B.):
   **`https://die-eiche.github.io/flugreisen/`**

Falls der Workflow fehlschlägt: unter **Actions** den letzten „Thailand Flug-Tracker Dashboard“-Lauf prüfen.

## Tägliche Preis-Updates

Unter **Actions** → **„Tägliche Flugpreis-Aktualisierung“** → **Run workflow** (einmalig manuell starten, danach täglich automatisch).

## Auslagerung aus GraeberInfo

Erledigt: Der Tracker liegt im eigenen Repository **`die-eiche/flugreisen`**. In `graeberinfo` gibt es den Ordner `thailand-flug-tracker` nicht mehr.
