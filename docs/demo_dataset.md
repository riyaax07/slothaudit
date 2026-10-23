# SlothAudit Synthetic Demo Dataset (`data/demo_transactions.csv`)

This document describes the design, scenarios, conventions, and limitations of the deterministic synthetic transaction dataset used for SlothAudit development and automated testing.

---

## 🔒 Privacy & Fictional Data Notice
* **100% Synthetic**: All merchant names, descriptions, account records, and transaction numbers in this dataset are entirely fictional.
* **Zero Real PII**: No real personal identifiable information, bank account numbers, or real user records are used or stored.

---

## 📊 Dataset Overview
* **Seed**: `42` (Deterministic across runs).
* **Time Range**: October 15, 2023 to October 24, 2024 (~13 months).
* **Currency**: `INR` (Indian Rupee, 2-decimal precision).
* **Sign Convention**:
  * **Positive (`+amount`)**: Debit, expense, or subscription charge (money debited from account).
  * **Negative (`-amount`)**: Credit, refund, or reversal (money credited to account).

---

## 🧪 Scenarios Covered

| Scenario Category | Scenario Details | Target Merchant(s) | Expected Behavior / Audit Significance |
| :--- | :--- | :--- | :--- |
| **Monthly Subscriptions** | Fixed cadence, exact same day and amount across 10 months. | • `StreamFlix` (₹649 on 5th)<br>• `MusicPulse` (₹179 on 14th)<br>• `CloudVault` (₹130 on 21st)<br>• `GymFit` (₹1500 on 1st) | Benchmark for high-confidence monthly recurrence detection. |
| **Annual Subscription** | 365-day recurrence cycle (Oct 2023 & Oct 2024). | • `CodeHost` (₹3599/yr) | Verifies annual cadence detection across calendar years. |
| **Price Increase** | Fixed subscription where price stepped up after 5 months. | • `DevToolbox`<br>(₹499 Jan–May → ₹699 Jun–Oct) | Engine should identify continuous subscription despite fee hike, flagging the +₹200 (+40%) price bump. |
| **Duplicate Subscriptions** | Overlapping subscriptions from the same provider on different dates/plans. | • `StreamFlix Premium` (₹649 on 5th)<br>• `StreamFlix Basic` (₹199 on 18th) | Should trigger a duplicate/overlapping service audit warning. |
| **Ordinary One-Offs** | Random, non-recurring expenses. | • `Kirana Fresh` (Groceries)<br>• `Sloth Cafe` (Coffee)<br>• `QuickCab` (Ride)<br>• `Electronics Hub`<br>• `PharmHealth` | Control group: must NOT be flagged as recurring. |
| **Refund / Reversal** | Product return resulting in an account credit. | • `BookHaven` (₹899 purchase on Apr 5, -₹899 refund on Apr 12) | Tests sign convention and `is_refund=True` filtering. |
| **Description Variations** | Different raw narrations mapping to one canonical merchant. | • `StreamFlix`<br>(`STREAMFLIX INTERNET BLR`, `STREAMFLIX DIGITAL SUBS`, `STREAMFLIX MUMBAI IN`) | Tests canonical merchant grouping against raw statement entropy. |
| **Ambiguous: Variable Utility** | Monthly bill with fluctuating amounts and minor date drift. | • `CityPower Utility`<br>(₹1140 to ₹1520, 10th–17th) | Challenges naive strict-equality matchers; tests tolerance thresholds. |
| **Ambiguous: Coincidental Twin** | Two one-off dining expenses 31 days apart at the exact same amount (₹750). | • `DineFine Restaurant`<br>(Mar 10 & Apr 10) | Tests minimum recurrence threshold (avoiding false alarms on 2-occurrence coincidences). |
| **Ambiguous: Cancelled Subscription** | Monthly charges that abruptly stopped after 3 months. | • `OldNews Magazine`<br>(Jan, Feb, Mar 2024) | Tests inactive/churned subscription detection ("zombie" vs cancelled). |

---

## ⚠️ Known Limitations
1. **Single Currency**: Currently restricted to INR. Multi-currency foreign exchange rates and conversion fees are not modeled in this initial synthetic batch.
2. **Simplified Tax/Fees**: Transactions do not split out GST/VAT line items.
3. **Fixed Seed**: The dataset is deterministic. Dynamic generation with different seeds can be produced by calling `generate_demo_transactions(seed=<int>)`.
