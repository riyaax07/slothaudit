"""Deterministic synthetic transaction data generator for SlothAudit.

Generates realistic but completely fictional financial statement transactions
designed to test recurring payment detection, price hikes, duplicate subscriptions,
one-offs, refunds, and ambiguous edge cases.
"""

import csv
from datetime import date
from pathlib import Path
import random
from typing import List, Tuple

from backend.app.schemas import Transaction


def generate_demo_transactions(seed: int = 42) -> List[Transaction]:
    """Generate a deterministic list of synthetic transactions across 13 months (Oct 2023 - Oct 2024).

    Scenarios Covered:
      1. Monthly Subscriptions:
         - StreamFlix (649 INR/mo, on the 5th)
         - MusicPulse (179 INR/mo, on the 14th)
         - CloudVault (130 INR/mo, on the 21st)
         - GymFit Core (1500 INR/mo, on the 1st)
      2. Annual Subscription:
         - CodeHost Pro Annual (3599 INR/yr, Oct 15 2023 and Oct 15 2024)
      3. Price Increase:
         - DevToolbox Pro: 499 INR/mo for 5 months, jumps to 699 INR/mo starting June 2024
      4. Duplicate Subscriptions:
         - StreamFlix Basic (199 INR/mo on the 18th) running concurrently with StreamFlix Premium (649 INR)
      5. One-Off Purchases (Non-recurring controls):
         - Groceries, cafe coffee, cab rides, books, electronics, transit recharge, pharmacy
      6. Refund / Reversal:
         - BookHaven return refund (-899 INR, is_refund=True)
      7. Merchant Description Variations:
         - Multiple raw descriptions for the same canonical merchant (e.g. StreamFlix and MusicPulse)
      8. Ambiguous Cases:
         - CityPower Utility: Monthly cadence but variable usage amounts (1140 to 1520 INR)
         - DineFine Restaurant: Coincidental twin purchases 31 days apart (750 INR), never repeated
         - OldNews Magazine: Inactive/cancelled subscription (active Jan-Mar 2024, then stops)
    """
    rng = random.Random(seed)
    raw_records: List[Tuple[date, float, str, str, str, bool]] = []

    # --------------------------------------------------------------------------
    # 1. Monthly Recurring Subscriptions
    # --------------------------------------------------------------------------
    streamflix_descriptions = [
        ("STREAMFLIX INTERNET BLR", "StreamFlix Blr"),
        ("STREAMFLIX DIGITAL SUBS", "StreamFlix Digital"),
        ("STREAMFLIX MUMBAI IN", "StreamFlix Mumbai"),
        ("STREAMFLIX MONTHLY AUTO-PAY", "StreamFlix AutoPay"),
    ]
    musicpulse_descriptions = [
        ("MUSICPULSE MEDIA IN", "MusicPulse Media"),
        ("MUSICPULSE SUB AUTO-DEBIT", "MusicPulse Debit"),
        ("MUSICPULSE STREAMING", "MusicPulse Stream"),
    ]

    for month in range(1, 11):  # Jan 2024 to Oct 2024
        # StreamFlix Premium (649 INR on 5th)
        desc_raw, merch_raw = streamflix_descriptions[month % len(streamflix_descriptions)]
        raw_records.append((
            date(2024, month, 5),
            649.00,
            desc_raw,
            merch_raw,
            "StreamFlix",
            False,
        ))

        # MusicPulse Family (179 INR on 14th)
        m_desc, m_raw = musicpulse_descriptions[month % len(musicpulse_descriptions)]
        raw_records.append((
            date(2024, month, 14),
            179.00,
            m_desc,
            m_raw,
            "MusicPulse",
            False,
        ))

        # CloudVault 2TB (130 INR on 21st)
        raw_records.append((
            date(2024, month, 21),
            130.00,
            "CLOUDVAULT STORAGE 2TB AUTO-DEBIT",
            "CloudVault Storage",
            "CloudVault",
            False,
        ))

        # GymFit Core Club (1500 INR on 1st)
        raw_records.append((
            date(2024, month, 1),
            1500.00,
            "GYMFIT INDIRANAGAR BLR RECURRING",
            "GymFit Indiranagar",
            "GymFit",
            False,
        ))

    # --------------------------------------------------------------------------
    # 2. Annual Subscription (Oct 2023 & Oct 2024)
    # --------------------------------------------------------------------------
    raw_records.append((
        date(2023, 10, 15),
        3599.00,
        "CODEHOST CLOUD HOSTING ANNUAL PRO",
        "CodeHost Cloud",
        "CodeHost",
        False,
    ))
    raw_records.append((
        date(2024, 10, 15),
        3599.00,
        "CODEHOST CLOUD HOSTING ANNUAL RENEWAL",
        "CodeHost Cloud",
        "CodeHost",
        False,
    ))

    # --------------------------------------------------------------------------
    # 3. Price Increase Scenario (DevToolbox Pro)
    # --------------------------------------------------------------------------
    # Months 1-5 (Jan-May 2024): 499.00 INR
    for m in range(1, 6):
        raw_records.append((
            date(2024, m, 10),
            499.00,
            "DEVTOOLBOX PRO DEVELOPER TOOLS",
            "DevToolbox",
            "DevToolbox",
            False,
        ))
    # Months 6-10 (Jun-Oct 2024): 699.00 INR (Price increase)
    for m in range(6, 11):
        raw_records.append((
            date(2024, m, 10),
            699.00,
            "DEVTOOLBOX PRO REVISED SUBSCRIPTION",
            "DevToolbox Pro",
            "DevToolbox",
            False,
        ))

    # --------------------------------------------------------------------------
    # 4. Duplicate Subscription (StreamFlix Basic running alongside Premium)
    # --------------------------------------------------------------------------
    for m in range(3, 11):  # March 2024 - Oct 2024
        raw_records.append((
            date(2024, m, 18),
            199.00,
            "STREAMFLIX BASIC PLAN AUTOPAY",
            "StreamFlix Basic",
            "StreamFlix",
            False,
        ))

    # --------------------------------------------------------------------------
    # 5. Non-Recurring One-Off Purchases
    # --------------------------------------------------------------------------
    one_offs = [
        (date(2024, 1, 12), 1240.50, "KIRANA FRESH STORE GROCERIES", "Kirana Fresh", "Kirana Fresh", False),
        (date(2024, 2, 18), 320.00, "SLOTH CAFE COFFEE & PASTRIES", "Sloth Cafe", "Sloth Cafe", False),
        (date(2024, 3, 22), 415.00, "QUICKCAB AIRPORT DROP", "QuickCab", "QuickCab", False),
        (date(2024, 4, 5), 899.00, "BOOKHAVEN ONLINE PYTHON GUIDE", "BookHaven Online", "BookHaven", False),
        (date(2024, 6, 15), 1450.00, "ELECTRONICS HUB USB-C DOCK", "Electronics Hub", "Electronics Hub", False),
        (date(2024, 7, 28), 500.00, "METRO TRANSIT SMART CARD TOPUP", "Metro Transit", "Metro Transit", False),
        (date(2024, 9, 2), 680.00, "PHARMHEALTH DIRECT FIRST AID", "PharmHealth Direct", "PharmHealth", False),
    ]
    raw_records.extend(one_offs)

    # --------------------------------------------------------------------------
    # 6. Refund / Reversal
    # --------------------------------------------------------------------------
    raw_records.append((
        date(2024, 4, 12),
        -899.00,
        "BOOKHAVEN RETURN REFUND PROCESSED",
        "BookHaven Online",
        "BookHaven",
        True,
    ))

    # --------------------------------------------------------------------------
    # 7. Ambiguous Cases
    # --------------------------------------------------------------------------
    # Case A: Utility Bill (monthly cadence, variable consumption amount)
    utility_amounts = [1140.00, 1380.00, 1290.00, 1520.00, 1420.00, 1310.00, 1490.00, 1360.00, 1280.00, 1410.00]
    for idx, amt in enumerate(utility_amounts, start=1):
        day = 10 + (idx % 8)  # Irregular day between 10th and 17th
        raw_records.append((
            date(2024, idx, day),
            amt,
            f"CITYPOWER ELECTRICITY UTILITY BILL {idx:02d}/2024",
            "CityPower Utility",
            "CityPower",
            False,
        ))

    # Case B: False Recurrence (Coincidental twin one-offs)
    raw_records.append((
        date(2024, 3, 10),
        750.00,
        "DINEFINE RESTAURANT DINNER",
        "DineFine Restaurant",
        "DineFine",
        False,
    ))
    raw_records.append((
        date(2024, 4, 10),
        750.00,
        "DINEFINE RESTAURANT TEAM LUNCH",
        "DineFine Restaurant",
        "DineFine",
        False,
    ))

    # Case C: Cancelled / Zombie Subscription (active Jan-Mar, then ceases)
    for m in range(1, 4):
        raw_records.append((
            date(2024, m, 25),
            299.00,
            "OLDNEWS DIGITAL MAGAZINE MONTHLY",
            "OldNews Magazine",
            "OldNews",
            False,
        ))

    # --------------------------------------------------------------------------
    # Sort deterministically by date, then description
    # --------------------------------------------------------------------------
    raw_records.sort(key=lambda r: (r[0], r[2], r[1]))

    transactions: List[Transaction] = []
    for idx, (txn_date, amount, desc, merch_raw, merch_canon, is_ref) in enumerate(raw_records, start=1):
        txn_id = f"TXN-{idx:04d}"
        t = Transaction(
            txn_id=txn_id,
            date=txn_date,
            amount=amount,
            currency="INR",
            raw_description=desc,
            merchant_raw=merch_raw,
            merchant_canonical=merch_canon,
            is_refund=is_ref,
            source_row=idx + 1,  # +1 accounting for CSV header line
        )
        transactions.append(t)

    return transactions


def write_demo_csv(filepath: str | Path = "data/demo_transactions.csv", seed: int = 42) -> List[Transaction]:
    """Generate deterministic synthetic transactions and write them to a CSV file.

    Returns the list of generated Transaction schema instances.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    transactions = generate_demo_transactions(seed=seed)

    fieldnames = [
        "txn_id",
        "date",
        "amount",
        "currency",
        "raw_description",
        "merchant_raw",
        "merchant_canonical",
        "is_refund",
        "source_row",
    ]

    with open(path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in transactions:
            writer.writerow({
                "txn_id": t.txn_id,
                "date": t.date.isoformat(),
                "amount": f"{t.amount:.2f}",
                "currency": t.currency,
                "raw_description": t.raw_description,
                "merchant_raw": t.merchant_raw,
                "merchant_canonical": t.merchant_canonical,
                "is_refund": str(t.is_refund).lower(),
                "source_row": t.source_row,
            })

    return transactions


if __name__ == "__main__":
    txns = write_demo_csv()
    print(f"Generated {len(txns)} deterministic transactions in data/demo_transactions.csv")
