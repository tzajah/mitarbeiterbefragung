# Changelog

## 2026-10-29 – Fragebogen-Editor, neues Bogen-Design, PDF-Import

### Neu
- **Fragebogen-Editor** (Tab „Fragebogen“): Titel, Freitext-Frage und bis zu 30 Fragen
  (Skala/Auswahl, 2–5 Optionen) in der App festlegen; Zurücksetzen auf Standard.
- **Fragebogen pro Stapel:** Jeder Stapel speichert beim Anlegen einen Snapshot seines
  Fragebogens; Scan, Review und Auswertung nutzen immer den Bogen des Stapels.
- **Design im Stil der BR-Niederschrift:** Kopfzeile (Organisation, Untertitel, Logo),
  Akzentlinien in wählbarer Farbe, Fragen als Tabellen mit schwarzer Kopfzeile je Antwortsatz,
  Fußzeile mit Seitenzahl und Vertraulichkeitshinweis.
- **Logo-Upload** (PNG/JPEG bis 2 MB) und **Vorschau-PDF**.
- **Zweiseitiger Bogen:** Seite 1 Fragen, Seite 2 seitenfüllendes Freitextfeld. QR-Code trägt
  die Seitennummer; Seite 2 wird ihrer Seite 1 über die Reihenfolge im Upload zugeordnet
  (keine bogenindividuelle Nummer). Review zeigt Seite 2 zum Abtippen.
- **PDF-Import:** viele Scan-Dateien (PDF/Bild, typisch eine PDF je Blatt mit beiden Seiten)
  auf einmal hochladen; Seite 2 wird nur innerhalb derselben Datei zugeordnet;
  Ergebnis bzw. Fehler pro Datei und Seite.

### Geändert
- Formular-Layout wird aus dem Fragebogen berechnet (`compute_layout`) statt festem Raster;
  Fragebögen, die nicht auf eine Seite passen, werden beim Speichern abgelehnt.
- Upload-Limit: max. 100 MB je Upload (Summe aller Dateien), max. 1000 Seiten.
- Gespeicherte Scans sind immer JPEG.

### Kompatibilität
- Bereits angelegte Stapel (ohne `layout_version`) werden weiter mit dem alten Raster gelesen.

## 2026-08-28 – Erste Version

- Explizite „Keine Angabe“-Spalte je Frage (#4)
- Server-Modus: optionale Session-Auth, WAL-SQLite, Docker/Compose, Login-Seite (#3)
- Demo-Modus: synthetischer Bogen läuft durch die echte Erkennung (#2)
- Security-Hardening: Pfad-Traversal-Schutz, Doppel-Finalisierung blockiert,
  Korrektur-Validierung, k-Clamp, Upload-Limit (#1)
- API, Web-Frontend und k-anonyme Auswertung
- Formular-Generator und CPU-Scan-Pipeline mit Roundtrip-Tests
