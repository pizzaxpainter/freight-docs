"""Central configuration. Edit this file (or set the environment variables noted) to tune the app."""
import os
import tempfile
from pathlib import Path

APP_TITLE = "Freight Document Processor"

# --------------------------------------------------------------------------------------
# Document categories.
# `suffix` is used in the final file name:  <HAWB>_<suffix>.pdf
# `esi_code` is shown to the operator as a reminder of the E-SI document code.
# !! The ID / DG / CITES codes below are PLACEHOLDERS - verify against the real E-SI list. !!
# --------------------------------------------------------------------------------------
CATEGORIES = {
    "CI":    {"label": "Commercial Invoice", "esi_code": "COMMIN", "suffix": "CI",    "verified": True},
    "PL":    {"label": "Packing List",       "esi_code": "PCKLST", "suffix": "PL",    "verified": True},
    "ID":    {"label": "Internal Document",  "esi_code": "INT",    "suffix": "ID",    "verified": False},
    "DG":    {"label": "Dangerous Goods",    "esi_code": "DGCL",   "suffix": "DG",    "verified": False},
    "CITES": {"label": "CITES",              "esi_code": "CITES",  "suffix": "CITES", "verified": False},
}

# --------------------------------------------------------------------------------------
# Classification rules (regex, weight). Content rules look at the text of the document,
# filename rules look at the file name only and are deliberately weaker.
# `always_confirm`: a human must confirm the category even if the match is strong.
# --------------------------------------------------------------------------------------
RULES = {
    "CI": {
        "content": [
            (r"commercial\s+invoice", 10),
            (r"\binvoice\s*(no|number|nr|#|date)\b", 5),
            (r"\binvoice\b", 2),
            (r"\bunit\s+price\b", 2),
            (r"\b(total\s+)?amount\s+due\b", 1),
        ],
        "filename": [
            (r"commercial[\s_-]*invoice", 8),
            (r"(?<![a-z])inv(?:oice)?(?![a-z])", 5),
            (r"(?<![a-z0-9])ci(?![a-z0-9])", 5),
        ],
        "always_confirm": False,
    },
    "PL": {
        "content": [
            (r"packing\s+list", 10),
            (r"packing\s+details", 8),
            (r"carton\s+(details|no|number)", 4),
            (r"\bno\.?\s+of\s+(cartons|packages|pkgs|ctns)\b", 3),
            (r"gross\s+(weight|wt)", 2),
            (r"net\s+(weight|wt)", 2),
        ],
        "filename": [
            (r"packing[\s_-]*(list|details)|packinglist", 8),
            (r"(?<![a-z0-9])(pl|pkl)(?![a-z0-9])", 5),
            (r"packing", 5),
        ],
        "always_confirm": False,
    },
    "ID": {
        "content": [
            (r"internal\s+(document|report)", 10),
            (r"billing\s+report", 8),
        ],
        "filename": [
            (r"internal", 8),
            (r"billing[\s_-]*report", 6),
        ],
        # Business rule: billing reports are only "provisionally" internal documents.
        "always_confirm": True,
    },
    "DG": {
        "content": [
            (r"dangerous\s+goods\s+(declaration|note|form)", 10),
            (r"shipper'?s\s+declaration", 10),
            (r"proper\s+shipping\s+name", 4),
            (r"\bUN\s?\d{4}\b", 3),
        ],
        "filename": [
            (r"dangerous|dgd", 8),
            (r"(?<![a-z0-9])dg(?![a-z0-9])", 6),
        ],
        "always_confirm": False,
    },
    "CITES": {
        "content": [
            (r"cites\s+(permit|certificate)", 10),
            (r"\bCITES\b", 8),
        ],
        "filename": [
            (r"cites", 8),
        ],
        "always_confirm": False,
    },
}

# Score thresholds (see classifier.py)
HIGH_MIN_SCORE = 8      # content score needed for an automatic, confident category
HIGH_MARGIN = 4         # lead over the runner-up needed for "high"
MED_MIN_SCORE = 5
MED_MARGIN = 2
FILENAME_MIN_SCORE = 5
TITLE_MULTIPLIER = 2.0  # a keyword that is a heading (short cell on one of the first lines)
TITLE_SCAN_LINES = 25
CLASSIFY_PAGES = 2      # pages of text used for whole-document classification
MAX_TEXT_PAGES = 150    # pages scanned for page-level (mixed document) detection

# --------------------------------------------------------------------------------------
# HAWB
# --------------------------------------------------------------------------------------
HAWB_ALLOWED = r"[A-Z0-9][A-Z0-9_-]{3,29}"   # hard rule: safe for file names
HAWB_TYPICAL = r"[A-Z]{3}\d{8}"              # soft rule: e.g. SIN01234567 (warning only)

# --------------------------------------------------------------------------------------
# Upload / extraction limits
# --------------------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".zip", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}
MAX_FILE_BYTES = int(os.environ.get("FDP_MAX_FILE_MB", 100)) * 1024 * 1024
MAX_EXTRACTED_BYTES = int(os.environ.get("FDP_MAX_EXTRACTED_MB", 500)) * 1024 * 1024
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_DEPTH = 3                 # ZIPs inside ZIPs
MAX_COMPRESSION_RATIO = 200       # per entry, only checked for entries > 10 MB
MAX_DOCS_PER_SESSION = 500

# --------------------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------------------
CONVERT_TIMEOUT_S = 180
OFFICE_MANY_PAGES_WARNING = 40    # converted Office files longer than this get a warning

# --------------------------------------------------------------------------------------
# Temporary storage (no database; everything lives here and is auto-deleted)
# --------------------------------------------------------------------------------------
WORK_ROOT = Path(os.environ.get("FDP_WORK_DIR", tempfile.gettempdir())) / "fdp_sessions"
RETENTION_MINUTES = int(os.environ.get("FDP_RETENTION_MINUTES", 120))   # since last activity
