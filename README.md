# Mitarbeiterbefragung – anonyme papierbasierte Befragung mit Scan-Auswertung

Eigenständiges Tool. Läuft **komplett lokal und CPU-only** –
kein GPU-ML, keine Cloud, keine personenbezogenen Daten.

## Kernidee

1. **Formular generieren:** zweiseitiger A4-Bogen – Seite 1 mit bis zu 30 Ankreuz-/Skalenfragen,
   Seite 2 mit einem seitenfüllenden Freitextfeld. Je Seite vier schwarze Eckmarken (Fiducials)
   + QR-Code (Stapel/Abteilung + Seitennummer). Kein Namensfeld – die Zuordnung Person → Bogen
   existiert nirgendwo im System.
2. **Scans einlesen:** OpenCV findet die Eckmarken → Perspektivkorrektur → Bubble-Erkennung
   über Tinten-Dichte an den Kreispositionen, die aus dem Fragebogen des Stapels berechnet
   werden (voll deterministisch, template-basiert). Jede Antwort kommt mit Confidence-Wert;
   unsichere Antworten landen in der Review-Queue.
3. **Review:** Mensch prüft unsichere Erkennungen und tippt den Freitext von Seite 2 ab.
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
- Upload-Limit: max. 100 MB je Upload (Summe aller Dateien), max. 1000 Seiten. Größere
  Scan-Stapel in mehreren Teilen hochladen.
- Die Speicherung erfolgt vollständig lokal im Container/Server – es gibt keine
  externen API-Aufrufe. DSGVO-Anforderung „Daten verlassen das Haus nicht" ist
  damit per Architektur erfüllt.

## Ablauf einer Befragung

1. **Fragebogen** (Tab „Fragebogen“): Fragen, Kopfzeile, Akzentfarbe und Logo festlegen,
   mit „Vorschau-PDF“ prüfen, speichern.
2. **Stapel** (Tab „Stapel“): pro Abteilung einen Stapel anlegen, PDF **beidseitig (Duplex)**
   so oft drucken, wie die Abteilung Mitarbeitende hat, und auslegen.
3. **Scannen:** jedes ausgefüllte Blatt duplex als **eine PDF mit beiden Seiten** scannen und
   im Tab „Scans“ alle Dateien auf einmal hochladen (Mehrfachauswahl). Seite 2 wird nur der
   Seite 1 direkt davor **in derselben Datei** zugeordnet; eine Datei darf auch mehrere Blätter
   (S1, S2, S1, S2 …) enthalten.
4. **Review:** unsichere Kreuze prüfen, Freitext von Seite 2 abtippen, finalisieren.
5. **Auswertung:** je Abteilung, erst ab n ≥ k finalisierten Bögen.

Ohne Drucker/Scanner: „Demo-Bogen“ in der Stapel-Liste erzeugt einen synthetisch ausgefüllten
zweiseitigen Bogen, der durch die echte Erkennung läuft.

## Fragen festlegen

Im Tab „Fragebogen“ legst du Titel, Freitext-Frage und bis zu 30 Fragen fest (Typ Skala oder Auswahl, je 2–5 Antwortoptionen; „Keine Angabe“ wird automatisch ergänzt). Die Fragen liegen in `data/spec.json`; „Auf Standard zurücksetzen“ stellt die mitgelieferten 25 Fragen wieder her. Änderungen gelten nur für neu angelegte Stapel: Jeder Stapel speichert seinen Fragebogen als Snapshot (`data/templates/<stack>.json`), Scans und Auswertung nutzen diesen.

## Bogen-Design, Logo, PDF-Import

- **Zweiseitiger Bogen** (Duplex drucken): Seite 1 = Fragen als Tabellen (je Gruppe gleicher
  Antwortsätze eine schwarze Kopfzeile, „Keine Angabe“ immer rechts), Seite 2 = Freitext.
  Beide Seiten tragen Eckmarken + QR-Code (nur Stapel-ID + Seitennummer, keine Bogen-Nummer).
- **Design-Felder** im Tab „Fragebogen“: Organisation (z. B. „BETRIEBSRAT“), Untertitel
  („Organisation · Ort“), Hinweistext (max. 2 Zeilen), Akzentfarbe (gestrichelte Linien).
  Passt ein Fragebogen nicht auf Seite 1 (zu viele Fragen/Antwortsätze), lehnt das Speichern
  ihn ab. 30 Fragen mit einem Antwortsatz passen.
- **Logo:** PNG/JPEG bis 2 MB hochladen (`POST /api/logo`); wird neu kodiert, verkleinert und
  beim Anlegen eines Stapels fest ins PDF eingebettet. „Vorschau-PDF“ zeigt den gespeicherten Stand.
- **PDF-Import:** `POST /api/scans` nimmt viele Bilder und/oder PDFs in einem Upload
  (typisch: eine PDF je Blatt mit beiden Seiten). Seite 2 wird der unmittelbar vorangehenden
  Seite 1 desselben Stapels **in derselben Datei** zugeordnet. Fehlerhafte Seiten werden
  einzeln gemeldet, der Rest wird trotzdem importiert.
- Altbestand: Stapel ohne `layout_version` im Snapshot werden weiter im alten Raster gelesen.

## Tests

```bash
uv run pytest -q
```

Der Round-Trip-Test generiert synthetisch ausgefüllte Bögen (inkl. Rotation und JPEG-Artefakten),
liest sie wieder ein und prüft sie gegen den Ground Truth – das validiert die komplette
geometrische Kette ohne echten Scanner.

Änderungen: siehe [CHANGELOG.md](CHANGELOG.md).
