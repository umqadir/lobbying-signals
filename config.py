"""Configuration for lobbying signal detection system."""

import os
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "filings.db"

# LLM Configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# SOPR Data Source
SOPR_BASE_URL = "https://lda.senate.gov/system/public/"
SOPR_FILING_TYPES = ["LD2"]  # Quarterly activity reports

# Anomaly Detection Thresholds
RECORD_MIN_HISTORY = 1_000_000      # $1M minimum historical activity to flag records
SPIKE_YOY_THRESHOLD = 1.0           # 100% YoY growth triggers spike
SPIKE_MIN_BASELINE = 500_000        # $500K minimum baseline for spike detection
CONCENTRATION_THRESHOLD = 0.30      # 30% market share triggers concentration alert
CONCENTRATION_PRIOR_MAX = 0.10      # Must have been below 10% prior
NEW_ENTRANT_MIN_SPEND = 100_000     # $100K minimum for new entrant to be notable
COORDINATED_SURGE_MIN_CLIENTS = 3   # Minimum unrelated clients for coordinated surge

# Issue Taxonomy (LLM classifies into these categories)
ISSUE_TAXONOMY = {
    "trade": ["tariffs", "export_controls", "trade_agreements", "sanctions", "customs"],
    "healthcare": ["drug_pricing", "medicare", "medicaid", "fda_approval", "telehealth", "aca"],
    "tech": ["ai_regulation", "privacy", "antitrust", "content_moderation", "crypto", "cybersecurity"],
    "energy": ["renewables", "oil_gas", "nuclear", "grid_infrastructure", "ev", "carbon"],
    "finance": ["banking_regulation", "securities", "consumer_finance", "insurance", "tax"],
    "defense": ["procurement", "military_policy", "veterans", "intel_community"],
    "agriculture": ["farm_subsidies", "food_safety", "trade_ag", "biofuels"],
    "transportation": ["aviation", "rail", "shipping", "infrastructure", "autonomous_vehicles"],
    "environment": ["epa_regulation", "clean_water", "clean_air", "superfund", "endangered_species"],
    "labor": ["minimum_wage", "unions", "osha", "employment_law", "immigration_labor"],
}

# Flatten for quick lookup
ALL_ISSUE_LABELS = []
for category, subcategories in ISSUE_TAXONOMY.items():
    ALL_ISSUE_LABELS.extend(subcategories)

# API Configuration
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
