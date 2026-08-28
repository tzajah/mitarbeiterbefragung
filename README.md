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

## Tests

```bash
uv run pytest -q
```

Der Round-Trip-Test generiert synthetisch ausgefüllte Bögen (inkl. Rotation und JPEG-Artefakten),
liest sie wieder ein und prüft sie gegen den Ground Truth – das validiert die komplette
geometrische Kette ohne echten Scanner.
