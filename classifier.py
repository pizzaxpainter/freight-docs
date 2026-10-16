"""Rules-based document classifier. Content beats filename; unsure means 'Needs Review'."""
from __future__ import annotations

import re
from dataclasses import dataclass

import settings as S

_COMPILED = {
    cat: {
        kind: [(re.compile(p, re.I), w) for p, w in rule[kind]]
        for kind in ("content", "filename")
    }
    for cat, rule in S.RULES.items()
}


@dataclass
class Result:
    category: str | None
    confidence: str          # high | medium | low | none
    needs_review: bool
    reason: str


def _title_cells(text: str) -> list[str]:
    cells = []
    for line in text.splitlines()[: S.TITLE_SCAN_LINES]:
        for cell in re.split(r"\s{3,}", line.strip()):
            if cell:
                cells.append(cell)
    return cells


def content_scores(text: str) -> dict[str, float]:
    """Score every category against the document text. Heading-like matches count double."""
    cells = _title_cells(text)
    scores = {}
    for cat, kinds in _COMPILED.items():
        total = 0.0
        for rx, weight in kinds["content"]:
            if not rx.search(text):
                continue
            is_title = any(
                len(c) <= 45 and (m := rx.search(c)) and (m.end() - m.start()) >= 0.75 * len(c)
                for c in cells
            )
            total += weight * (S.TITLE_MULTIPLIER if is_title else 1.0)
        scores[cat] = total
    return scores


def filename_scores(filename: str) -> dict[str, float]:
    scores = {}
    for cat, kinds in _COMPILED.items():
        scores[cat] = float(sum(w for rx, w in kinds["filename"] if rx.search(filename)))
    return scores


def _top_two(scores: dict[str, float]) -> tuple[str, float, float]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, second = ranked[0], ranked[1][1] if len(ranked) > 1 else 0.0
    return best[0], best[1], second


def _label(cat: str) -> str:
    return S.CATEGORIES[cat]["label"]


def classify(text: str, filename: str) -> Result:
    """Classify a whole document from the text of its first pages plus its filename."""
    c_cat, c_best, c_second = _top_two(content_scores(text))
    f_cat, f_best, _ = _top_two(filename_scores(filename))
    confirm = S.RULES.get(c_cat, {}).get("always_confirm", False)

    if c_best >= S.HIGH_MIN_SCORE and c_best - c_second >= S.HIGH_MARGIN:
        reason = f"Content identifies this as {_label(c_cat)}."
        if f_best >= S.FILENAME_MIN_SCORE and f_cat != c_cat:
            reason += f" (The filename suggests {_label(f_cat)}; content takes priority.)"
        if confirm:
            reason += " Please confirm - this type needs a human decision."
        return Result(c_cat, "high", confirm, reason)

    if c_best >= S.MED_MIN_SCORE and c_best - c_second >= S.MED_MARGIN:
        return Result(c_cat, "medium", True,
                      f"Probably {_label(c_cat)}, but the match is weak. Please confirm.")

    if f_best >= S.FILENAME_MIN_SCORE:
        return Result(f_cat, "low", True,
                      f"Only the filename suggests {_label(f_cat)}; the content was not conclusive. Please check.")

    return Result(None, "none", True, "Could not identify the document type. Please choose a category.")


def classify_pages(page_texts: list[str]) -> list[str | None]:
    """Confident per-page category (content only), or None for pages without a clear signal."""
    out = []
    for t in page_texts:
        cat, best, second = _top_two(content_scores(t))
        out.append(cat if best >= S.HIGH_MIN_SCORE and best - second >= S.HIGH_MARGIN else None)
    return out
