# Mitarbeiterbefragung – anonyme papierbasierte Befragung mit Scan-Auswertung

Eigenständiges Tool. Läuft **komplett lokal und CPU-only** –
kein GPU-ML, keine Cloud, keine personenbezogenen Daten.

## Kernidee

1. **Formular generieren:** A4-PDF mit 20–30 Ankreuz-/Skalenfragen + 1 Freitextfeld.
   Vier schwarze Eckmarken (Fiducials) + QR-Code (Stapel/Abteilung). Kein Namensfeld – die
   Zuordnung Person → Bogen existiert nirgendwo im System.
2. **Scans einlesen:** OpenCV findet die Eckmarken → Perspektivkorrektur → Bubble-Erkennung
   über Tinten-Dichte in festen Kreispositionen (voll deterministisch, template-basiert).
   Jede Antwort kommt mit Confidence-Wert; unsichere Antworten landen in der Review-Queue.
3. **Review:** Mensch prüft unsichere Erkennungen, tippt das **eine** Freitextfeld ab.
4. **Auswertung:** Aggregation **nur auf Abteilungsebene** mit k-Anonymität:
   Gruppen mit n < k (Standard: 5) werden nicht ausgewertet – sonst wäre Re-Identifikation
   durch Ausschluss möglich.

## Datenschutz-Design

- Kein Feld für Name/Kennung auf dem Bogen.
- Abteilungszuordnung nur über den QR-Code des *Stapels* (Stapel wird pro Abteilung gedruckt
  und ausgelegt) – niemals über Personenmerkmale.
- Freitext wird pro Abteilung nur bei n ≥ k angezeigt (Schreibstil-Risiko).
- Antworten werden mit zufälliger UUID statt fortlaufender ID gespeichert.

## Start (Entwicklung)

```bash
uv run uvicorn app.main:app --port 8765
# UI:        http://localhost:8765/
# API-Docs:  http://localhost:8765/docs
```

Ohne weitere Konfiguration läuft die App offen (nur localhost-Browserzugriff).

## Server-Betrieb (LAN/Docker)

Die App ist ein gewöhnlicher Webdienst (FastAPI + statisches Frontend) und läuft
auf jedem Server mit Python 3.12+ oder Docker.

### Mit Login-Schutz (empfohlen, sobald die App im LAN hängt)

Setzt man `APP_PASSWORD` (oder sicherer: `APP_PASSWORD_HASH`), schützt ein
Session-Cookie-Login die gesamte App. Hash erzeugen und als Umgebungsvariable setzen:

```bash
python -c "import os,hashlib; s=os.urandom(16).hex(); print('APP_PASSWORD_HASH=scrypt$16384$8$1$'+s+'$'+hashlib.scrypt(b'MEIN_PASSWORT',salt=bytes.fromhex(s),n=16384,r=8,p=1).hex())"
```

Lokal testen mit Login:

```bash
APP_USER=admin APP_PASSWORD=... uv run uvicorn app.main:app --host 0.0.0.0 --port 8765
```

### Docker / Docker Compose

```bash
cp .env.example .env       # Passwort/Hash eintragen
docker compose up -d --build
# → http://<server-ip>:8765
```

- Daten (SQLite, Scans, Stapel-PDFs) liegen im Volume `./data` → Backup = Ordner sichern.
- `restart: unless-stopped` hält den Dienst über Neustarts hinweg am Leben.
- Für HTTPS: Reverse-Proxy vorschalten (z. B. Caddy/nginx) – die App selbst bleibt HTTP.

### Betriebs-Hinweise

- SQLite läuft im WAL-Modus (parallele Leser + Schreiber im Team sind unkritisch).
- Upload-Limit: 25 MB pro Scan.
- Die Speicherung erfolgt vollständig lokal im Container/Server – es gibt keine
  externen API-Aufrufe. DSGVO-Anforderung „Daten verlassen das Haus nicht" ist
  damit per Architektur erfüllt.

## Tests

```bash
uv run pytest -q
```

Der Round-Trip-Test generiert synthetisch ausgefüllte Bögen (inkl. Rotation und JPEG-Artefakten),
liest sie wieder ein und prüft sie gegen den Ground Truth – das validiert die komplette
geometrische Kette ohne echten Scanner.
