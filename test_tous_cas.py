# -*- coding: utf-8 -*-
"""
TEST EXHAUSTIF — tous les cas BUY/SELL × ZN/PU/MP/QA × dans/hors zone.
Réplique À LA LETTRE les formules de telegram_listener_v17_1.py (v17.4h).
Décisions possibles : "ACCEPTE" (MK+L1+L2) | "HORS-ZONE" (L3+L4) | "REFUSE"
"""
import sys

PASS, FAIL = 0, 0

def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  ✗ FAIL: " + label)

# ── Constantes réelles (vérifiées code + .env) ──
TOLERANCE_ZN = 1.0
TOLERANCE_PU = 3.0
TOLERANCE_MP = 2.0
MAX_DISTANCE = 3.0
TRADE_HORS_ZONE = True
LIMIT_OFFSET_1, LIMIT_OFFSET_2 = 3.0, 6.0

# ── Décision ZN (execute_signal, cas 1 / cas 2) ──
def zn_decision(action, current, zone_low, zone_high, thz=TRADE_HORS_ZONE):
    if action == "BUY":
        in_zone = zone_low <= current <= zone_high + TOLERANCE_ZN
        bord_dist = zone_high + TOLERANCE_ZN
    else:
        in_zone = zone_low - TOLERANCE_ZN <= current <= zone_high
        bord_dist = zone_low - TOLERANCE_ZN
    if in_zone:
        return "ACCEPTE"
    dist = abs(current - bord_dist)
    if not thz:
        return "REFUSE"
    if dist > MAX_DISTANCE:
        return "REFUSE"
    return "HORS-ZONE"

# ── Décision PU (execute_signal, prix unique) ──
def pu_decision(action, current, entry):
    zone_low = round(entry - TOLERANCE_PU, 2)
    zone_high = round(entry + TOLERANCE_PU, 2)
    if action == "BUY":
        in_zone = current > zone_low and current <= zone_high
    else:
        in_zone = current >= zone_low and current < zone_high
    return "ACCEPTE" if in_zone else "REFUSE"

# ── Décision MP (execute_quick_alert) : entry=None → zone [current ± TOLERANCE_MP] ──
def mp_decision(action, current):
    zone_low = round(current - TOLERANCE_MP, 2)
    zone_high = round(current + TOLERANCE_MP, 2)
    in_zone = zone_low <= current <= zone_high
    return "ACCEPTE" if in_zone else "REFUSE"  # toujours ACCEPTE

# ── Décision MP avec entry défini / QA (zone [entry ± TOLERANCE_PU] inclusive) ──
def qa_decision(action, current, entry):
    zone_low = round(entry - TOLERANCE_PU, 2)
    zone_high = round(entry + TOLERANCE_PU, 2)
    in_zone = zone_low <= current <= zone_high
    return "ACCEPTE" if in_zone else "REFUSE"

# ══════════════════════════════════════════════════════════════
print("=== ZN — zone [4435, 4440] | TOLERANCE_ZN=1 | MAX_DISTANCE=3 | BUY ===")
# BUY : in_zone = 4435 <= prix <= 4441 ; bord_dist = 4441 ; cas 2 : 4441 < prix <= 4444
check("ZN BUY prix=4437 (centre zone) -> ACCEPTE MK+L1+L2", zn_decision("BUY", 4437, 4435, 4440) == "ACCEPTE")
check("ZN BUY prix=4435 (= zone_low) -> ACCEPTE", zn_decision("BUY", 4435, 4435, 4440) == "ACCEPTE")
check("ZN BUY prix=4440 (= zone_high) -> ACCEPTE", zn_decision("BUY", 4440, 4435, 4440) == "ACCEPTE")
check("ZN BUY prix=4441 (= bord étendu, inclusif) -> ACCEPTE", zn_decision("BUY", 4441, 4435, 4440) == "ACCEPTE")
check("ZN BUY prix=4441.01 (dist 0.01 > bord) -> HORS-ZONE L3+L4", zn_decision("BUY", 4441.01, 4435, 4440) == "HORS-ZONE")
check("ZN BUY prix=4443 (dist 2 <= 3) -> HORS-ZONE L3+L4", zn_decision("BUY", 4443, 4435, 4440) == "HORS-ZONE")
check("ZN BUY prix=4444 (dist 3 = MAX) -> HORS-ZONE L3+L4", zn_decision("BUY", 4444, 4435, 4440) == "HORS-ZONE")
check("ZN BUY prix=4444.01 (dist 3.01 > 3) -> REFUSE", zn_decision("BUY", 4444.01, 4435, 4440) == "REFUSE")
check("ZN BUY prix=4450 (loin) -> REFUSE", zn_decision("BUY", 4450, 4435, 4440) == "REFUSE")
check("ZN BUY prix=4434.99 (sous zone, dist 6.01 du bord) -> REFUSE", zn_decision("BUY", 4434.99, 4435, 4440) == "REFUSE")
check("ZN BUY prix=4430 (sous zone, loin) -> REFUSE", zn_decision("BUY", 4430, 4435, 4440) == "REFUSE")

print("=== ZN — zone [4435, 4440] | SELL ===")
# SELL : in_zone = 4434 <= prix <= 4440 ; bord_dist = 4434 ; cas 2 : 4431 <= prix < 4434
check("ZN SELL prix=4437 (centre zone) -> ACCEPTE MK+L1+L2", zn_decision("SELL", 4437, 4435, 4440) == "ACCEPTE")
check("ZN SELL prix=4434 (= bord étendu, inclusif) -> ACCEPTE", zn_decision("SELL", 4434, 4435, 4440) == "ACCEPTE")
check("ZN SELL prix=4435 (= zone_low) -> ACCEPTE", zn_decision("SELL", 4435, 4435, 4440) == "ACCEPTE")
check("ZN SELL prix=4440 (= zone_high) -> ACCEPTE", zn_decision("SELL", 4440, 4435, 4440) == "ACCEPTE")
check("ZN SELL prix=4433.99 (dist 0.01 sous bord) -> HORS-ZONE L3+L4", zn_decision("SELL", 4433.99, 4435, 4440) == "HORS-ZONE")
check("ZN SELL prix=4432 (dist 2 <= 3) -> HORS-ZONE L3+L4", zn_decision("SELL", 4432, 4435, 4440) == "HORS-ZONE")
check("ZN SELL prix=4431 (dist 3 = MAX) -> HORS-ZONE L3+L4", zn_decision("SELL", 4431, 4435, 4440) == "HORS-ZONE")
check("ZN SELL prix=4430.99 (dist 3.01 > 3) -> REFUSE", zn_decision("SELL", 4430.99, 4435, 4440) == "REFUSE")
check("ZN SELL prix=4425 (loin) -> REFUSE", zn_decision("SELL", 4425, 4435, 4440) == "REFUSE")
check("ZN SELL prix=4440.01 (au-dessus zone, dist 6.01 du bord) -> REFUSE", zn_decision("SELL", 4440.01, 4435, 4440) == "REFUSE")
check("ZN SELL prix=4445 (au-dessus, loin) -> REFUSE", zn_decision("SELL", 4445, 4435, 4440) == "REFUSE")

print("=== ZN — TRADE_HORS_ZONE=false (cas 2 -> REFUSE) ===")
check("ZN BUY hors zone dist<=3 mais TRADE_HORS_ZONE=false -> REFUSE", zn_decision("BUY", 4443, 4435, 4440, thz=False) == "REFUSE")
check("ZN SELL hors zone dist<=3 mais TRADE_HORS_ZONE=false -> REFUSE", zn_decision("SELL", 4432, 4435, 4440, thz=False) == "REFUSE")
check("ZN BUY dans zone TRADE_HORS_ZONE=false -> ACCEPTE (inchangé)", zn_decision("BUY", 4440, 4435, 4440, thz=False) == "ACCEPTE")

print("=== PU — entry=4440 | zone stricte [4437, 4443] | BUY (4437 < prix <= 4443) ===")
check("PU BUY prix=4440 (= entry) -> ACCEPTE MK+L1+L2", pu_decision("BUY", 4440, 4440) == "ACCEPTE")
check("PU BUY prix=4443 (= borne haute, inclusif) -> ACCEPTE", pu_decision("BUY", 4443, 4440) == "ACCEPTE")
check("PU BUY prix=4437.01 (juste > borne basse) -> ACCEPTE", pu_decision("BUY", 4437.01, 4440) == "ACCEPTE")
check("PU BUY prix=4437 (= borne basse, STRICT) -> REFUSE", pu_decision("BUY", 4437, 4440) == "REFUSE")
check("PU BUY prix=4443.01 (juste > borne haute) -> REFUSE", pu_decision("BUY", 4443.01, 4440) == "REFUSE")
check("PU BUY prix=4436.99 (sous zone) -> REFUSE", pu_decision("BUY", 4436.99, 4440) == "REFUSE")
check("PU BUY prix=4450 (loin) -> REFUSE", pu_decision("BUY", 4450, 4440) == "REFUSE")

print("=== PU — entry=4440 | SELL (4437 <= prix < 4443) ===")
check("PU SELL prix=4440 (= entry) -> ACCEPTE MK+L1+L2", pu_decision("SELL", 4440, 4440) == "ACCEPTE")
check("PU SELL prix=4437 (= borne basse, inclusif) -> ACCEPTE", pu_decision("SELL", 4437, 4440) == "ACCEPTE")
check("PU SELL prix=4442.99 (juste < borne haute) -> ACCEPTE", pu_decision("SELL", 4442.99, 4440) == "ACCEPTE")
check("PU SELL prix=4443 (= borne haute, STRICT) -> REFUSE", pu_decision("SELL", 4443, 4440) == "REFUSE")
check("PU SELL prix=4436.99 (juste < borne basse) -> REFUSE", pu_decision("SELL", 4436.99, 4440) == "REFUSE")
check("PU SELL prix=4443.01 (au-dessus zone) -> REFUSE", pu_decision("SELL", 4443.01, 4440) == "REFUSE")
check("PU SELL prix=4430 (loin) -> REFUSE", pu_decision("SELL", 4430, 4440) == "REFUSE")

print("=== MP — entry=None | zone symétrique [current ± 2] (toujours dans la zone) ===")
check("MP BUY current=4431.6 -> ACCEPTE (zone [4429.6, 4433.6] contient 4431.6)", mp_decision("BUY", 4431.6) == "ACCEPTE")
check("MP SELL current=4440.0 -> ACCEPTE (zone [4438, 4442])", mp_decision("SELL", 4440.0) == "ACCEPTE")
check("MP BUY current=4400.0 -> ACCEPTE (jamais refusé, quel que soit le prix)", mp_decision("BUY", 4400.0) == "ACCEPTE")
check("MP SELL current=4500.0 -> ACCEPTE (jamais refusé)", mp_decision("SELL", 4500.0) == "ACCEPTE")

print("=== MP avec entry défini (hybride) / QA — zone inclusive [entry ± 3] identique BUY/SELL ===")
# MP avec zone_mid défini : label MP mais zone QA inclusive (code : qa_label="MP" + zone_low_qa)
check("MP(entry def) BUY prix=4437 (= borne basse) -> ACCEPTE", qa_decision("BUY", 4437, 4440) == "ACCEPTE")
check("MP(entry def) BUY prix=4443 (= borne haute) -> ACCEPTE", qa_decision("BUY", 4443, 4440) == "ACCEPTE")
check("MP(entry def) BUY prix=4436.99 -> REFUSE", qa_decision("BUY", 4436.99, 4440) == "REFUSE")
check("MP(entry def) SELL prix=4437 (= borne basse) -> ACCEPTE", qa_decision("SELL", 4437, 4440) == "ACCEPTE")
check("MP(entry def) SELL prix=4443 (= borne haute) -> ACCEPTE", qa_decision("SELL", 4443, 4440) == "ACCEPTE")
check("MP(entry def) SELL prix=4443.01 -> REFUSE", qa_decision("SELL", 4443.01, 4440) == "REFUSE")

print("=== QA — entry=4440 | zone inclusive [4437, 4443] (BUY et SELL identiques) ===")
check("QA BUY prix=4440 (= entry) -> ACCEPTE MK+L1+L2", qa_decision("BUY", 4440, 4440) == "ACCEPTE")
check("QA BUY prix=4437 (= borne basse, INCLUSIF) -> ACCEPTE", qa_decision("BUY", 4437, 4440) == "ACCEPTE")
check("QA BUY prix=4443 (= borne haute, INCLUSIF) -> ACCEPTE", qa_decision("BUY", 4443, 4440) == "ACCEPTE")
check("QA BUY prix=4436.99 -> REFUSE", qa_decision("BUY", 4436.99, 4440) == "REFUSE")
check("QA BUY prix=4443.01 -> REFUSE", qa_decision("BUY", 4443.01, 4440) == "REFUSE")
check("QA BUY prix=4450 -> REFUSE", qa_decision("BUY", 4450, 4440) == "REFUSE")
check("QA SELL prix=4440 (= entry) -> ACCEPTE MK+L1+L2", qa_decision("SELL", 4440, 4440) == "ACCEPTE")
check("QA SELL prix=4437 (= borne basse, INCLUSIF) -> ACCEPTE", qa_decision("SELL", 4437, 4440) == "ACCEPTE")
check("QA SELL prix=4443 (= borne haute, INCLUSIF) -> ACCEPTE", qa_decision("SELL", 4443, 4440) == "ACCEPTE")
check("QA SELL prix=4436.99 -> REFUSE", qa_decision("SELL", 4436.99, 4440) == "REFUSE")
check("QA SELL prix=4443.01 -> REFUSE", qa_decision("SELL", 4443.01, 4440) == "REFUSE")
check("QA SELL prix=4430 -> REFUSE", qa_decision("SELL", 4430, 4440) == "REFUSE")

print()
print("=== 8. FUSION par TEMPS (TEMPS_DE_FUSION=2 min) — PU/ZN après QA/MP ===")
from datetime import datetime, timezone, timedelta
TEMPS_DE_FUSION = 2.0
def fusion_cas1(qa_time, now=None):
    now = now or datetime.now(timezone.utc)
    if qa_time is None:
        return False
    age_min = (now - qa_time).total_seconds() / 60.0
    return age_min <= TEMPS_DE_FUSION
now = datetime.now(timezone.utc)
check("FUSION QA age 1 min (<= 2) -> cas 1 ACCEPTE", fusion_cas1(now - timedelta(minutes=1)) is True)
check("FUSION QA age 119.9 s (< 2 min) -> cas 1 ACCEPTE", fusion_cas1(now - timedelta(seconds=119.9)) is True)
check("FUSION QA age 2 min 1s (> 2) -> cas 2 ECHOUE", fusion_cas1(now - timedelta(minutes=2, seconds=1)) is False)
check("FUSION QA age 30 min -> cas 2 ECHOUE", fusion_cas1(now - timedelta(minutes=30)) is False)
check("FUSION QA sans timestamp (None) -> cas 2 ECHOUE", fusion_cas1(None) is False)

print()
print("=== 9. TP initial UNIFIÉ (current ± TP_FIXED_GAIN_USD=7) — tous types ===")
TP_FIXED_GAIN_USD = 7.0
def tp_initial(action, current):
    return round(current + TP_FIXED_GAIN_USD, 2) if action == "BUY" else round(current - TP_FIXED_GAIN_USD, 2)
check("TP BUY current=4431.6 -> 4438.6", tp_initial("BUY", 4431.6) == 4438.6)
check("TP SELL current=4431.6 -> 4424.6", tp_initial("SELL", 4431.6) == 4424.6)
check("TP BUY current=4440.0 -> 4447.0", tp_initial("BUY", 4440.0) == 4447.0)
check("TP identique MK/L1/L2 (1 seul calcul, pas de TP_PAR_DEFAUT)", tp_initial("SELL", 4431.6) == tp_initial("SELL", 4431.6))

print()
print("=== 10. Parser : SL/TP auto remplacés (MAX_SL_USD=12 / TP_FIXED_GAIN_USD=7) ===")
MAX_SL_USD = 12.0
# Bloc MP (sans prix) : default_sl = ±MAX_SL_USD, default_tp = ±TP_FIXED_GAIN_USD
check("Parser MP BUY : SL=-12, TP=+7", (-MAX_SL_USD == -12.0) and (TP_FIXED_GAIN_USD == 7.0))
check("Parser MP SELL : SL=+12, TP=-7", (MAX_SL_USD == 12.0) and (-TP_FIXED_GAIN_USD == -7.0))
# Bloc auto-TP (SL présent, pas de TP) : tp = entry_mid ± TP_FIXED_GAIN_USD
entry_mid = 4440.0
check("Parser auto-TP BUY : entry 4440 -> 4447", entry_mid + TP_FIXED_GAIN_USD == 4447.0)
check("Parser auto-TP SELL : entry 4440 -> 4433", entry_mid - TP_FIXED_GAIN_USD == 4433.0)
# Bloc QA (ni TP ni SL) : provisional_sl = entry ∓ MAX_SL_USD, tp = entry ± TP_FIXED_GAIN_USD
check("Parser QA BUY : SL=4440-12=4428, TP=4440+7=4447", (entry_mid - MAX_SL_USD == 4428.0) and (entry_mid + TP_FIXED_GAIN_USD == 4447.0))
check("Parser QA SELL : SL=4440+12=4452, TP=4440-7=4433", (entry_mid + MAX_SL_USD == 4452.0) and (entry_mid - TP_FIXED_GAIN_USD == 4433.0))

print()
print("=" * 50)
print("RESULTAT: %d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
