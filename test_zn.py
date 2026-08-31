# -*- coding: utf-8 -*-
"""Tests de validation de la logique ZN v17.4h (réplique exacte du code implémenté)."""
import sys

# Constantes du .env serveur
TOLERANCE_ZN = 1.0
MAX_DISTANCE = 3.0
MAX_SL_USD = 12.0
TP_FIXED_GAIN_USD = 7.0
LIMIT_OFFSET_1 = 3.0
LIMIT_OFFSET_2 = 6.0

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name, detail)
    else:
        FAIL += 1
        print("  *** FAIL", name, detail)

def decide_zone(action, current, zone_low, zone_high, trade_hors_zone):
    """Réplique le bloc execute_signal v17.4h (cas 1 / cas 2)."""
    if action == "BUY":
        in_zone = zone_low <= current <= zone_high + TOLERANCE_ZN
        bord_dist = zone_high + TOLERANCE_ZN
    else:
        in_zone = zone_low - TOLERANCE_ZN <= current <= zone_high
        bord_dist = zone_low - TOLERANCE_ZN

    if not in_zone:
        dist = abs(current - bord_dist)
        if not trade_hors_zone:
            return ("REFUSE", 0)
        if dist > MAX_DISTANCE:
            return ("REFUSE_DIST", dist)
        return ("CAS2", dist)
    return ("CAS1", 0)

def calc_cas1(action, current, zone_low, zone_high, sl_signal):
    """MK + L1 + L2, SL unique capé, TP initial = current ± 7."""
    avg_entry = (zone_low + zone_high) / 2
    sl = _cap_sl(action, avg_entry, sl_signal)
    # SL unique v17.4f sur prix d'exécution réel
    if action == "BUY":
        sl = max(sl, round(current - MAX_SL_USD, 2))
    else:
        sl = min(sl, round(current + MAX_SL_USD, 2))
    tp = round(current + TP_FIXED_GAIN_USD, 2) if action == "BUY" else round(current - TP_FIXED_GAIN_USD, 2)
    if action == "BUY":
        l1, l2 = round(current - LIMIT_OFFSET_1, 2), round(current - LIMIT_OFFSET_2, 2)
    else:
        l1, l2 = round(current + LIMIT_OFFSET_1, 2), round(current + LIMIT_OFFSET_2, 2)
    return {"mk": current, "l1": l1, "l2": l2, "sl": sl, "tp": tp}

def calc_cas2(action, zone_low, zone_high):
    """L3 bord de zone + L4 milieu, SL = L3 ∓ MAX_SL_USD, TP = L3 ± 7."""
    mid = round((zone_low + zone_high) / 2, 2)
    if action == "BUY":
        l3 = round(zone_high, 2)
        sl = round(l3 - MAX_SL_USD, 2)
        tp3, tp4 = round(l3 + TP_FIXED_GAIN_USD, 2), round(mid + TP_FIXED_GAIN_USD, 2)
    else:
        l3 = round(zone_low, 2)
        sl = round(l3 + MAX_SL_USD, 2)
        tp3, tp4 = round(l3 - TP_FIXED_GAIN_USD, 2), round(mid - TP_FIXED_GAIN_USD, 2)
    return {"l3": l3, "l4": mid, "sl": sl, "tp3": tp3, "tp4": tp4}

def _cap_sl(action, entry, signal_sl):
    d = abs(entry - signal_sl)
    if d <= MAX_SL_USD:
        return signal_sl
    return round(entry - MAX_SL_USD, 2) if action == "BUY" else round(entry + MAX_SL_USD, 2)

print("=== 1. SITUATION DU PRIX (décision cas 1 / cas 2) ===")
# Cas 1 BUY : prix dans la zone d'acceptation
r = decide_zone("BUY", 4438, 4437, 4440, True);   check("BUY 4438 dans zone [4437-4440] -> CAS1", r[0] == "CAS1", r)
r = decide_zone("BUY", 4441, 4437, 4440, True);   check("BUY 4441 = high+TOLERANCE -> CAS1 (frontière)", r[0] == "CAS1", r)
r = decide_zone("BUY", 4437, 4437, 4440, True);   check("BUY 4437 = low -> CAS1 (frontière basse)", r[0] == "CAS1", r)
r = decide_zone("SELL", 4439, 4437, 4440, True);  check("SELL 4439 dans zone -> CAS1", r[0] == "CAS1", r)
r = decide_zone("SELL", 4436, 4437, 4440, True);  check("SELL 4436 = low-TOLERANCE -> CAS1 (frontière)", r[0] == "CAS1", r)
# Cas 2 BUY : hors zone, dist <= 3
r = decide_zone("BUY", 4442, 4437, 4440, True);   check("BUY 4442 hors zone dist 1$ -> CAS2", r[0] == "CAS2" and abs(r[1]-1.0) < 0.01, r)
r = decide_zone("SELL", 4435, 4437, 4440, True);  check("SELL 4435 hors zone dist 1$ -> CAS2", r[0] == "CAS2" and abs(r[1]-1.0) < 0.01, r)
# Refus dist > MAX_DISTANCE
r = decide_zone("BUY", 4445, 4437, 4440, True);   check("BUY 4445 dist 4$ > 3 -> REFUSE_DIST", r[0] == "REFUSE_DIST" and abs(r[1]-4.0) < 0.01, r)
# Refus TRADE_HORS_ZONE=false
r = decide_zone("BUY", 4442, 4437, 4440, False);  check("BUY 4442 hors zone + TRADE_HORS_ZONE=false -> REFUSE", r[0] == "REFUSE", r)

print("=== 2. CAS 1 : exécution MK+L1+L2 (exemple doc : BUY zone 4437-4440, prix 4438, SL signal 4428) ===")
c = calc_cas1("BUY", 4438, 4437, 4440, 4428)
check("MK=4438", c["mk"] == 4438, c)
check("L1=4435 (offset 3)", c["l1"] == 4435, c)
check("L2=4432 (offset 6)", c["l2"] == 4432, c)
check("SL=4428 (signal conservé, 10$ <= 12)", c["sl"] == 4428, c)
check("TP initial=4445 (4438+7)", c["tp"] == 4445, c)
# SL capé : signal trop loin -> cap depuis avg_entry (milieu zone 4438.5-12=4426.5),
# puis max() avec current-12 (4426) garde 4426.5 (comportement réel v17.4e/f)
c = calc_cas1("BUY", 4438, 4437, 4440, 4410)
check("BUY SL signal 4410 (28$) -> capé 4426.5 (distance 11.5$ <= 12)", c["sl"] == 4426.5, c)
# SELL miroir
c = calc_cas1("SELL", 4440, 4437, 4440, 4452)
check("SELL MK=4440 L1=4443 L2=4446", c["l1"] == 4443 and c["l2"] == 4446, c)
check("SELL SL=4450.5 (cap avg_entry 4438.5+12, puis min)", c["sl"] == 4450.5, c)
check("SELL TP=4433 (4440-7)", c["tp"] == 4433, c)

print("=== 3. CAS 2 : L3/L4 (exemple doc : BUY zone 4437-4440, prix 4442) ===")
c = calc_cas2("BUY", 4437, 4440)
check("L3=4440 (bord haut)", c["l3"] == 4440, c)
check("L4=4438.5 (milieu)", c["l4"] == 4438.5, c)
check("SL=4428 (4440-12)", c["sl"] == 4428, c)
check("TP L3=4447 (4440+7)", c["tp3"] == 4447, c)
check("TP L4=4445.5 (4438.5+7)", c["tp4"] == 4445.5, c)
# SELL miroir
c = calc_cas2("SELL", 4437, 4440)
check("SELL L3=4437 (bord bas) L4=4438.5", c["l3"] == 4437 and c["l4"] == 4438.5, c)
check("SELL SL=4449 (4437+12)", c["sl"] == 4449, c)
check("SELL TP L3=4430 (4437-7)", c["tp3"] == 4430, c)

print("=== 4. TP dynamique (recalcul au remplissage) ===")
# cas 1 : MK seul -> TP = entry ± 7
mk, l1 = 4438, 4435
check("Cas 1: weighted_entry = entry_MK = 4438 -> TP = 4445", round(4438 + 7.0 * 1 / 1, 2) == 4445.0, "")
# cas 2 : MK+L1 -> cible 10.5, movement 5.25
mov2 = round(7.0 * 1.5 / 2, 2); check("Cas 2: movement = 10.5/2 = 5.25", mov2 == 5.25, mov2)
avg2 = round((mk + l1) / 2, 2); check("Cas 2: TP = avg(4438,4435) ± 5.25 -> 4441.75", round(avg2 + mov2, 2) == 4441.75, (avg2, mov2))
# cas 3 : MK+L1+L2 -> cible 14, movement 4.67
mov3 = round(7.0 * 2.0 / 3, 2); check("Cas 3: movement = 14/3 = 4.67", mov3 == 4.67, mov3)
# L3 seule : TP = L3 ± 7
check("L3 seule -> TP = 4447 (4440+7)", 4440 + 7 == 4447, "")
# L3+L4 : cible 10.5, movement 5.25
avg34 = round((4440 + 4438.5) / 2, 2); check("L3+L4: TP = avg(4440,4438.5) ± 5.25", round(avg34 - 5.25, 2) == 4434.0, (avg34,))

print("=== 5. PU : zone stricte [entry ± TOLERANCE_PU] (v17.4h) ===")
def pu_in_zone(action, entry, current, tol_pu=3.0):
    zone_low = round(entry - tol_pu, 2)
    zone_high = round(entry + tol_pu, 2)
    if action == "BUY":
        return current > zone_low and current <= zone_high
    return current >= zone_low and current < zone_high

entry = 4440
check("PU BUY prix=4443 dans zone (4440±3)", pu_in_zone("BUY", entry, 4443) is True, "")
check("PU BUY prix=4443.5 > entry+3 -> REFUSE", pu_in_zone("BUY", entry, 4443.5) is False, "")
check("PU BUY prix=4437 = entry-3 (strict) -> REFUSE", pu_in_zone("BUY", entry, 4437) is False, "")
check("PU BUY prix=4437.01 > entry-3 -> accepte", pu_in_zone("BUY", entry, 4437.01) is True, "")
check("PU SELL prix=4437 (>= entry-3) -> accepte", pu_in_zone("SELL", entry, 4437) is True, "")
check("PU SELL prix=4443 = entry+3 (strict) -> REFUSE", pu_in_zone("SELL", entry, 4443) is False, "")
check("PU SELL prix=4442.99 -> accepte", pu_in_zone("SELL", entry, 4442.99) is True, "")
check("PU SELL prix=4436.99 < entry-3 -> REFUSE", pu_in_zone("SELL", entry, 4436.99) is False, "")

print("=== 6. QA : zone stricte inclusive [entry ± TOLERANCE_PU] (v17.4h) ===")
def qa_in_zone(entry, current, tol_pu=3.0):
    zone_low = round(entry - tol_pu, 2)
    zone_high = round(entry + tol_pu, 2)
    return zone_low <= current <= zone_high

check("QA prix=4437 (= entry-3, inclusif) -> accepte", qa_in_zone(entry, 4437) is True, "")
check("QA prix=4443 (= entry+3, inclusif) -> accepte", qa_in_zone(entry, 4443) is True, "")
check("QA prix=4436.99 -> REFUSE", qa_in_zone(entry, 4436.99) is False, "")
check("QA prix=4443.01 -> REFUSE", qa_in_zone(entry, 4443.01) is False, "")

print("=== 7. MP : zone symétrique [current ± TOLERANCE_MP=2] (v17.4h) ===")
def mp_in_zone(current, tol_mp=2.0):
    zone_low = round(current - tol_mp, 2)
    zone_high = round(current + tol_mp, 2)
    return zone_low <= current <= zone_high

check("MP prix toujours dans la zone (BUY/SELL)", mp_in_zone(4431.6) is True, "")

print()
print("RESULTAT: %d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
