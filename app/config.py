"""Zentrale Pfade und Konstanten."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
SCANS_DIR = DATA_DIR / "scans"
SPECS_DIR = DATA_DIR / "specs"      # inhaltsadressierte Spec-Snapshots: {fingerprint}.json
DB_PATH = DATA_DIR / "app.db"
SPEC_PATH = DATA_DIR / "spec.json"  # aktive Fragen; fehlt → default_spec()
LOGO_PATH = DATA_DIR / "logo.png"   # optionales Logo (beim Upload neu kodiert)

# k-Anonymität: Auswertung einer Abteilung erst ab n >= K_DEFAULT Antworten
K_DEFAULT = 5

for _d in (DATA_DIR, TEMPLATES_DIR, SCANS_DIR, SPECS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
