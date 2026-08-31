import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "app.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "claude-haiku-4-5-20251001")
REVIEWER_EMAIL = os.environ.get("REVIEWER_EMAIL", "reviewer@example.com")

SEVERITY_LEVELS = ["Critical", "High", "Medium", "Low"]
COMPONENTS = [
    "Auth",
    "Billing",
    "Search",
    "Notifications",
    "API",
    "Frontend",
    "Database",
    "Performance",
    "Infra",
    "Mobile",
]

# Number of past corrections retrieved as few-shot exemplars per triage call.
RETRIEVAL_TOP_K = 3
RETRIEVAL_MIN_SIMILARITY = 0.05

# Timestamp before any real corrections could exist; used as the baseline
# prompt version's correction_cutoff so it starts with zero exemplars (true
# zero-shot).
EPOCH_CUTOFF = "1970-01-01T00:00:00.000000Z"
