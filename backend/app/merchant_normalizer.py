"""Deterministic merchant normalization layer for SlothAudit.

Maps noisy, variable statement descriptions and merchant names to stable,
canonical merchant identifiers using deterministic alias lookup and
noise-stripping heuristics.

Design Principles:
- Deterministic & Exact: No unconditional fuzzy matching that could accidentally
  merge distinct entities (e.g. 'Sloth Cafe' vs 'Sloth Fitness').
- Noise Stripping: Removes common payment rails (UPI, POS, CARD, NACH),
  terminal IDs, location suffixes (BLR, IN, MUMBAI), and billing noise (AUTOPAY, RECURRING).
- Safe Fallback: Unknown merchants receive clean title-cased normalization
  rather than being dropped or mangled.
- Idempotent: normalize_merchant(normalize_merchant(x)) == normalize_merchant(x).
"""

import re
from typing import Dict, List, Optional, Pattern, Tuple

# Precompiled Payment Prefix & Channel Patterns (including optional terminal/pos numbers)
PAYMENT_PREFIX_REGEX = re.compile(
    r"^(?:(?:UPI|POS|CARD|ACH|NEFT|IMPS|RTGS|NACH|E-MANDATE|AUTOPAY|BILLDESK|RAZORPAY|PAYTM|PYMT|PAYMENT)[-/_\s]*(?:\d+[-/_\s]+)?)+",
    re.IGNORECASE,
)

# Trailing Reference Numbers, Transaction IDs, and Dates
TRAILING_REF_REGEX = re.compile(
    r"(?:[-/_\s]+(?:REF|TXN|UTR|ID|NO|INV|ORDER|BILL)[#:\s]*[A-Z0-9]+|[-/_\s]+\d{6,}|[-/_\s]+\d{2}/\d{4})+$",
    re.IGNORECASE,
)

# Common Noise Suffix Tokens (stripped from the end of merchant names)
NOISE_SUFFIX_TOKENS = {
    "in",
    "ind",
    "blr",
    "bom",
    "mumbai",
    "delhi",
    "pune",
    "bangalore",
    "hyd",
    "online",
    "store",
    "retail",
    "direct",
    "subscription",
    "subscriptions",
    "subs",
    "sub",
    "autopay",
    "auto-pay",
    "auto-debit",
    "autodebit",
    "recurring",
    "payment",
    "pmt",
    "monthly",
    "annual",
    "pvt",
    "ltd",
}

# Registered Canonical Merchants for Fictional Benchmark Scenarios
# Ordered from most specific multi-token patterns to single-token roots.
KNOWN_CANONICAL_MERCHANTS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"\bstreamflix\b", re.IGNORECASE), "StreamFlix"),
    (re.compile(r"\bmusicpulse\b", re.IGNORECASE), "MusicPulse"),
    (re.compile(r"\bcloudvault\b", re.IGNORECASE), "CloudVault"),
    (re.compile(r"\bgymfit\b", re.IGNORECASE), "GymFit"),
    (re.compile(r"\bcodehost\b", re.IGNORECASE), "CodeHost"),
    (re.compile(r"\bdevtoolbox\b", re.IGNORECASE), "DevToolbox"),
    (re.compile(r"\bkirana\s+fresh\b", re.IGNORECASE), "Kirana Fresh"),
    (re.compile(r"\bsloth\s+cafe\b", re.IGNORECASE), "Sloth Cafe"),
    (re.compile(r"\bquickcab\b", re.IGNORECASE), "QuickCab"),
    (re.compile(r"\bbookhaven\b", re.IGNORECASE), "BookHaven"),
    (re.compile(r"\belectronics\s+hub\b", re.IGNORECASE), "Electronics Hub"),
    (re.compile(r"\bmetro\s+transit\b", re.IGNORECASE), "Metro Transit"),
    (re.compile(r"\bpharmhealth\b", re.IGNORECASE), "PharmHealth"),
    (re.compile(r"\bcitypower\b", re.IGNORECASE), "CityPower"),
    (re.compile(r"\bdinefine\b", re.IGNORECASE), "DineFine"),
    (re.compile(r"\boldnews\b", re.IGNORECASE), "OldNews"),
]


def _clean_tokens(text: str) -> str:
    """Strip punctuation and collapse whitespace, treating hyphens and slashes as word boundaries."""
    # Replace special separator characters with space
    cleaned = re.sub(r"[-*_/#\\|]+", " ", text)
    # Remove remaining punctuation
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    # Ensure there is at least one alphanumeric character
    if not re.search(r"\w", cleaned):
        return ""
    # Collapse whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_merchant(raw_merchant: Optional[str]) -> str:
    """Normalize a raw merchant name or description to a canonical identifier.

    Rules applied:
      1. Empty / None fallback -> 'Unknown Merchant'.
      2. Check against explicit canonical registry (exact root token matches).
      3. Strip common banking/payment prefixes (UPI-, POS, CARD, etc.) and terminal IDs.
      4. Strip trailing reference IDs, invoice numbers, and billing dates.
      5. Strip noise suffix words (e.g. 'IN', 'BLR', 'AUTOPAY', 'RECURRING').
      6. Return clean title-cased merchant name.

    Idempotent: normalize_merchant(normalize_merchant(x)) == normalize_merchant(x).
    """
    if raw_merchant is None:
        return "Unknown Merchant"

    text = raw_merchant.strip()
    if not text:
        return "Unknown Merchant"

    # Step 1: Check known canonical merchant patterns first (handles variations cleanly)
    for pattern, canonical_name in KNOWN_CANONICAL_MERCHANTS:
        if pattern.search(text):
            return canonical_name

    # Step 2: Strip payment prefixes
    cleaned = PAYMENT_PREFIX_REGEX.sub("", text).strip()

    # Step 3: Strip trailing reference numbers and dates
    cleaned = TRAILING_REF_REGEX.sub("", cleaned).strip()

    # Step 4: Tokenize and clean punctuation
    cleaned = _clean_tokens(cleaned)
    if not cleaned:
        return "Unknown Merchant"

    # Step 5: Check canonical registry again after prefix/ref stripping
    for pattern, canonical_name in KNOWN_CANONICAL_MERCHANTS:
        if pattern.search(cleaned):
            return canonical_name

    # Step 6: Strip trailing noise tokens (iterative from right)
    tokens = cleaned.split()
    while len(tokens) > 1 and tokens[-1].lower() in NOISE_SUFFIX_TOKENS:
        tokens.pop()

    candidate = " ".join(tokens).strip()
    if not candidate:
        return "Unknown Merchant"

    # Step 7: Title case fallback
    return candidate.title()
