"""Unit tests for SlothAudit deterministic merchant normalizer.

Verifies:
- Case, whitespace, and punctuation normalization
- Alias resolution for synthetic benchmark merchants
- Payment prefix and reference number cleanup
- Idempotency guarantees
- Discrimination of unknown and similar-but-distinct merchants
- Safe handling of empty or malformed inputs
- 100% consistency against all 83 demo dataset transactions
"""

import csv
from pathlib import Path
import pytest

from backend.app.merchant_normalizer import normalize_merchant


def test_case_and_whitespace_normalization():
    """Verify that case, tabs, multiple spaces, and extraneous punctuation are cleaned."""
    assert normalize_merchant("  acme   widgets  ") == "Acme Widgets"
    assert normalize_merchant("ZENITH_CORP") == "Zenith Corp"
    assert normalize_merchant("APEX-LOGISTICS-LTD") == "Apex Logistics"
    assert normalize_merchant("METRO   SUPERMARKET  ") == "Metro Supermarket"


def test_equivalent_aliases_known_merchants():
    """Verify that all known variations map to the correct canonical name."""
    streamflix_variants = [
        "StreamFlix",
        "StreamFlix Blr",
        "StreamFlix Digital",
        "StreamFlix Mumbai",
        "StreamFlix AutoPay",
        "StreamFlix Basic",
        "STREAMFLIX INTERNET BLR",
        "STREAMFLIX DIGITAL SUBS",
        "STREAMFLIX MUMBAI IN",
        "STREAMFLIX MONTHLY AUTO-PAY",
        "STREAMFLIX BASIC PLAN AUTOPAY",
    ]
    for variant in streamflix_variants:
        assert normalize_merchant(variant) == "StreamFlix"

    musicpulse_variants = [
        "MusicPulse",
        "MusicPulse Media",
        "MusicPulse Debit",
        "MusicPulse Stream",
        "MUSICPULSE MEDIA IN",
        "MUSICPULSE SUB AUTO-DEBIT",
        "MUSICPULSE STREAMING",
    ]
    for variant in musicpulse_variants:
        assert normalize_merchant(variant) == "MusicPulse"


def test_payment_prefix_and_reference_cleanup():
    """Verify stripping of UPI, POS, CARD prefixes and trailing transaction IDs."""
    assert normalize_merchant("UPI-Zomato-REF12345678") == "Zomato"
    assert normalize_merchant("POS 4912 CAFE DE PARIS BLR") == "Cafe De Paris"
    assert normalize_merchant("CARD PYMT SWIGGY INDIA REF#998877") == "Swiggy India"
    assert normalize_merchant("AUTOPAY/JIO FIBER/INV987654") == "Jio Fiber"
    assert normalize_merchant("NEFT-RENTAL HOUSING SERVICES-UTR99881122") == "Rental Housing Services"
    assert normalize_merchant("RAZORPAY*URBAN COMPANY") == "Urban Company"


def test_idempotency():
    """Verify that normalizing an already normalized merchant returns the identical string."""
    test_inputs = [
        "StreamFlix",
        "MusicPulse",
        "Sloth Cafe",
        "Acme Cloud Hosting",
        "Zomato",
        "STREAMFLIX DIGITAL SUBS",
        "UPI-SWIGGY-123456",
        "Cafe De Paris",
    ]
    for s in test_inputs:
        first_pass = normalize_merchant(s)
        second_pass = normalize_merchant(first_pass)
        assert first_pass == second_pass, f"Failed idempotency on {s!r}: {first_pass!r} != {second_pass!r}"


def test_unknown_merchants_remain_distinguishable():
    """Verify that distinct unknown merchants are preserved as separate entities."""
    m1 = normalize_merchant("Alpha Technologies Private Limited")
    m2 = normalize_merchant("Beta Consulting Services LLC")
    m3 = normalize_merchant("Gamma Analytics Corp")

    assert m1 == "Alpha Technologies Private Limited"
    assert m2 == "Beta Consulting Services Llc"
    assert m3 == "Gamma Analytics Corp"
    assert len({m1, m2, m3}) == 3


def test_similar_merchants_not_merged():
    """Verify that merchants sharing common words are NOT merged together."""
    # Sloth Cafe vs Sloth Fitness Gym
    assert normalize_merchant("Sloth Cafe Coffee") == "Sloth Cafe"
    assert normalize_merchant("Sloth Fitness Gym") == "Sloth Fitness Gym"
    assert normalize_merchant("Sloth Cafe") != normalize_merchant("Sloth Fitness Gym")

    # CloudVault vs CodeHost vs CloudFlare
    assert normalize_merchant("CloudVault Storage") == "CloudVault"
    assert normalize_merchant("CodeHost Cloud") == "CodeHost"
    assert normalize_merchant("CloudFlare DNS") == "Cloudflare Dns"
    assert len({
        normalize_merchant("CloudVault Storage"),
        normalize_merchant("CodeHost Cloud"),
        normalize_merchant("CloudFlare DNS"),
    }) == 3

    # StreamFlix vs StreamPod
    assert normalize_merchant("StreamFlix") == "StreamFlix"
    assert normalize_merchant("StreamPod Audio") == "Streampod Audio"
    assert normalize_merchant("StreamFlix") != normalize_merchant("StreamPod Audio")


def test_empty_and_invalid_inputs():
    """Verify safe fallback for None, empty string, and whitespace/punctuation."""
    assert normalize_merchant(None) == "Unknown Merchant"
    assert normalize_merchant("") == "Unknown Merchant"
    assert normalize_merchant("     ") == "Unknown Merchant"
    assert normalize_merchant("---***///") == "Unknown Merchant"


def test_all_synthetic_demo_merchants_normalized_consistently():
    """Verify that all raw merchant and description variants in demo_transactions.csv map to canonical."""
    demo_path = Path("data/demo_transactions.csv")
    assert demo_path.exists()

    with open(demo_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected_canonical = row["merchant_canonical"]
            raw_merchant = row["merchant_raw"]
            raw_desc = row["raw_description"]

            norm_from_merchant = normalize_merchant(raw_merchant)
            norm_from_desc = normalize_merchant(raw_desc)

            assert norm_from_merchant == expected_canonical, (
                f"Row {row['source_row']}: merchant_raw {raw_merchant!r} "
                f"normalized to {norm_from_merchant!r}, expected {expected_canonical!r}"
            )
            assert norm_from_desc == expected_canonical, (
                f"Row {row['source_row']}: raw_description {raw_desc!r} "
                f"normalized to {norm_from_desc!r}, expected {expected_canonical!r}"
            )
