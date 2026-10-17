"""Central configuration. Paths can be overridden with SB_* environment variables."""
from __future__ import annotations

import os
from pathlib import Path

PRODUCT_NAME = "ScamBreaker-Z"
BANK_NAME = "Demo Bank"  # fictional bank used in all demo screens

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = Path(os.environ.get("SB_WORK_DIR", ROOT / "work"))
DATA_DIR = Path(os.environ.get("SB_DATA_DIR", WORK_DIR / "data"))
MODEL_DIR = Path(os.environ.get("SB_MODEL_DIR", WORK_DIR / "models"))
AUDIT_DIR = Path(os.environ.get("SB_AUDIT_DIR", WORK_DIR / "audit"))
SECRETS_DIR = Path(os.environ.get("SB_SECRETS_DIR", WORK_DIR / "secrets"))
RESULTS_DIR = Path(os.environ.get("SB_RESULTS_DIR", ROOT / "docs" / "results"))
KNOWLEDGE_DIR = ROOT / "knowledge"
SCENARIO_DIR = ROOT / "scenarios"

SEED = 20261017

# Feature engine
WINDOW_MIN = 24 * 60  # rolling window for velocity / graph features
SEQ_LEN = 8  # sender history length fed to the LSTM (7 previous + current)
PAYEE_MEMORY = 64  # payees remembered per account (bounded memory)

# Time split (0-based day index from the first day in the data)
TRAIN_DAYS = (0, 5)
VAL_DAYS = (6, 7)
TEST_DAYS = (8, 99)

# Friction budgets: max share of legitimate digital retail transfers that may receive
# at least this level of friction. Thresholds are fitted on validation days.
BUDGET_NUDGE = 0.01
BUDGET_HOLD = 0.002
BUDGET_BLOCK = 0.0005

PAYMENT_FORMATS = ("ACH", "Bitcoin", "Cash", "Cheque", "Credit Card", "Reinvestment", "Wire")
DIGITAL_FORMATS = ("ACH", "Bitcoin", "Wire")  # app-initiated transfers that carry a session

TYPOLOGIES = ("gov_impersonation", "investment", "job_task", "romance", "ecommerce")
TYPOLOGY_LABELS = {
    "gov_impersonation": "Government official impersonation",
    "investment": "Investment scam",
    "job_task": "Job / task scam",
    "romance": "Fake friend / romance scam",
    "ecommerce": "E-commerce scam",
}

SGD_PER_USD = 1.33  # fixed demo display rate (amounts are modelled in USD)

DECISIONS = ("ALLOW", "NUDGE", "HOLD", "BLOCK")


def ensure_dirs() -> None:
    for d in (WORK_DIR, DATA_DIR, MODEL_DIR, AUDIT_DIR, SECRETS_DIR, RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
