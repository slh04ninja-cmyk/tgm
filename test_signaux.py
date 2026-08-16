"""
Tests des signaux BUY/SELL pour chaque type : ZN, PU, MP, QA
Vérifie la logique de zone, tolérance, et décision MARKET/LIMIT/REFUS

Usage: python test_signaux.py
"""

import os

# Simuler les configs (valeurs par défaut)
TOLERANCE_ZN = float(os.getenv("TOLERANCE_ZN", "1.0"))
TOLERANCE_PU = float(os.getenv("TOLERANCE_PU", "3.0"))
TOLERANCE_MP = float(os.getenv("TOLERANCE_MP", "5.0"))
TP_PAR_DEFAUT = float(os.getenv("TP_PAR_DEFAUT", "15.0"))
MAX_SL_USD = float(os.getenv("MAX_SL_USD", "10.0"))


def test_signal(signal_type, action, zone_low, zone_high, current, entry_price=None):
    """Simule la logique de décision pour un signal."""
    result = {"type": signal_type, "action": action, "current": current}

    if signal_type == "ZN":
        if action == "BUY":
            in_zone = zone_low <= current <= zone_high
            above_zone = zone_high < current <= zone_high + TOLERANCE_ZN
            below_zone = False
        else:
            in_zone = zone_low <= current <= zone_high
            below_zone = zone_low - TOLERANCE_ZN <= current < zone_low
            above_zone = False

        if in_zone:
            result["decision"] = "MARKET + LIMITS"
            result["zone"] = f"[{zone_low}, {zone_high}]"
        elif (action == "BUY" and above_zone) or (action == "SELL" and below_zone):
            result["decision"] = "LIMITS SEULES"
            result["zone"] = f"[{zone_low}, {zone_high}]"
        else:
            result["decision"] = "REFUSE"

    elif signal_type == "PU":
        zone_low_pu = round(entry_price - TOLERANCE_PU, 2)
        zone_high_pu = round(entry_price + TOLERANCE_PU, 2)

        if action == "BUY":
            in_zone = zone_low_pu <= current <= zone_high_pu
            above_zone = zone_high_pu < current <= zone_high_pu + TOLERANCE_ZN
            below_zone = False
        else:
            in_zone = zone_low_pu <= current <= zone_high_pu
            below_zone = zone_low_pu - TOLERANCE_ZN <= current < zone_low_pu
            above_zone = False

        if in_zone:
            result["decision"] = "MARKET + LIMITS"
            result["zone"] = f"[{zone_low_pu}, {zone_high_pu}]"
        elif (action == "BUY" and above_zone) or (action == "SELL" and below_zone):
            result["decision"] = "LIMITS SEULES"
            result["zone"] = f"[{zone_low_pu}, {zone_high_pu}]"
        else:
            result["decision"] = "REFUSE"

    elif signal_type == "MP":
        if action == "BUY":
            zone_low_mp = round(current - TOLERANCE_MP, 2)
            zone_high_mp = current
            in_zone = zone_low_mp <= current <= zone_high_mp
            above_zone = zone_high_mp < current <= zone_high_mp + TOLERANCE_ZN
            below_zone = False
        else:
            zone_low_mp = current
            zone_high_mp = round(current + TOLERANCE_MP, 2)
            in_zone = zone_low_mp <= current <= zone_high_mp
            below_zone = zone_low_mp - TOLERANCE_ZN <= current < zone_low_mp
            above_zone = False

        if in_zone:
            result["decision"] = "MARKET + LIMITS"
            result["zone"] = f"[{zone_low_mp}, {zone_high_mp}]"
        elif (action == "BUY" and above_zone) or (action == "SELL" and below_zone):
            result["decision"] = "LIMITS SEULES"
        else:
            result["decision"] = "REFUSE"

    elif signal_type == "QA":
        zone_low_qa = round(entry_price - TOLERANCE_PU, 2)
        zone_high_qa = round(entry_price + TOLERANCE_PU, 2)

        if action == "BUY":
            in_zone = zone_low_qa <= current <= zone_high_qa
            above_zone = zone_high_qa < current <= zone_high_qa + TOLERANCE_ZN
            below_zone = False
        else:
            in_zone = zone_low_qa <= current <= zone_high_qa
            below_zone = zone_low_qa - TOLERANCE_ZN <= current < zone_low_qa
            above_zone = False

        if in_zone:
            result["decision"] = "MARKET + LIMITS"
            result["zone"] = f"[{zone_low_qa}, {zone_high_qa}]"
        elif (action == "BUY" and above_zone) or (action == "SELL" and below_zone):
            result["decision"] = "LIMITS SEULES"
            result["zone"] = f"[{zone_low_qa}, {zone_high_qa}]"
        else:
            result["decision"] = "REFUSE"

    return result


def run_tests():
    passed = 0
    failed = 0

    def check(signal_type, action, zone_low, zone_high, current, expected, entry_price=None):
        nonlocal passed, failed
        r = test_signal(signal_type, action, zone_low, zone_high, current, entry_price)
        ok = r["decision"] == expected
        status = "PASS" if ok else "FAIL"
        emoji = "✅" if ok else "❌"
        zone = r.get("zone", "")
        print(f"  {emoji} [{status}] {signal_type:3s} {action:4s} prix={current} zone={zone:20s} → {r['decision']:20s} (attendu: {expected})")
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 70)
    print("TEST DES SIGNAUX — ZN, PU, MP, QA")
    print("=" * 70)

    # ── ZN BUY ──
    print("\n📍 ZN BUY — zone [3300, 3310]")
    check("ZN", "BUY", 3300, 3310, 3300, "MARKET + LIMITS")
    check("ZN", "BUY", 3300, 3310, 3305, "MARKET + LIMITS")
    check("ZN", "BUY", 3300, 3310, 3310, "MARKET + LIMITS")
    check("ZN", "BUY", 3300, 3310, 3311, "LIMITS SEULES")
    check("ZN", "BUY", 3300, 3310, 3315, "REFUSE")

    # ── ZN SELL ──
    print("\n📍 ZN SELL — zone [3300, 3310]")
    check("ZN", "SELL", 3300, 3310, 3310, "MARKET + LIMITS")
    check("ZN", "SELL", 3300, 3310, 3305, "MARKET + LIMITS")
    check("ZN", "SELL", 3300, 3310, 3300, "MARKET + LIMITS")
    check("ZN", "SELL", 3300, 3310, 3299, "LIMITS SEULES")
    check("ZN", "SELL", 3300, 3310, 3295, "REFUSE")

    # ── PU BUY ──
    print("\n📍 PU BUY — entry 3300, zone [3297, 3303]")
    check("PU", "BUY", None, None, 3297, "MARKET + LIMITS", entry_price=3300)
    check("PU", "BUY", None, None, 3299, "MARKET + LIMITS", entry_price=3300)
    check("PU", "BUY", None, None, 3300, "MARKET + LIMITS", entry_price=3300)
    check("PU", "BUY", None, None, 3303, "MARKET + LIMITS", entry_price=3300)
    check("PU", "BUY", None, None, 3304, "LIMITS SEULES", entry_price=3300)
    check("PU", "BUY", None, None, 3308, "REFUSE", entry_price=3300)

    # ── PU SELL ──
    print("\n📍 PU SELL — entry 3300, zone [3297, 3303]")
    check("PU", "SELL", None, None, 3303, "MARKET + LIMITS", entry_price=3300)
    check("PU", "SELL", None, None, 3301, "MARKET + LIMITS", entry_price=3300)
    check("PU", "SELL", None, None, 3300, "MARKET + LIMITS", entry_price=3300)
    check("PU", "SELL", None, None, 3297, "MARKET + LIMITS", entry_price=3300)
    check("PU", "SELL", None, None, 3296, "LIMITS SEULES", entry_price=3300)
    check("PU", "SELL", None, None, 3292, "REFUSE", entry_price=3300)

    # ── MP BUY ──
    print("\n📍 MP BUY — prix actuel 3308")
    check("MP", "BUY", None, None, 3308, "MARKET + LIMITS")

    # ── MP SELL ──
    print("\n📍 MP SELL — prix actuel 3308")
    check("MP", "SELL", None, None, 3308, "MARKET + LIMITS")

    # ── QA BUY ──
    print("\n📍 QA BUY — entry 3306, zone [3303, 3309]")
    check("QA", "BUY", None, None, 3303, "MARKET + LIMITS", entry_price=3306)
    check("QA", "BUY", None, None, 3305, "MARKET + LIMITS", entry_price=3306)
    check("QA", "BUY", None, None, 3306, "MARKET + LIMITS", entry_price=3306)
    check("QA", "BUY", None, None, 3309, "MARKET + LIMITS", entry_price=3306)
    check("QA", "BUY", None, None, 3310, "LIMITS SEULES", entry_price=3306)
    check("QA", "BUY", None, None, 3315, "REFUSE", entry_price=3306)

    # ── QA SELL ──
    print("\n📍 QA SELL — entry 3306, zone [3303, 3309]")
    check("QA", "SELL", None, None, 3309, "MARKET + LIMITS", entry_price=3306)
    check("QA", "SELL", None, None, 3307, "MARKET + LIMITS", entry_price=3306)
    check("QA", "SELL", None, None, 3306, "MARKET + LIMITS", entry_price=3306)
    check("QA", "SELL", None, None, 3303, "MARKET + LIMITS", entry_price=3306)
    check("QA", "SELL", None, None, 3302, "LIMITS SEULES", entry_price=3306)
    check("QA", "SELL", None, None, 3297, "REFUSE", entry_price=3306)

    # ── Résumé ──
    print("\n" + "=" * 70)
    total = passed + failed
    if failed == 0:
        print(f"✅ {passed}/{total} TESTS PASSÉS")
    else:
        print(f"❌ {failed}/{total} TESTS ÉCHOUÉS")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)
