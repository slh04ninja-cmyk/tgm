# Version : V11.0.0 — SINGLE_POSITION_MODE

### Date : 2026-07-25

---

## 1. SINGLE_POSITION_MODE — Un seul MARKET par signal

### Problème
Les signaux avec zone (CAS1, C2-S1, C2-S2) plaçaient 2 positions (MARKET + LIMIT), doublant le risque. Les signaux QA+Fusion ajoutaient une 2ème position via le merge. La majorité des pertes venaient de ces stratégies multi-positions.

### Solution
Mode position unique : tous les signaux exécutent un seul MARKET. Plus de LIMIT, plus de pending orders, plus de merge avec 2ème position.

### Paramètres `.env`
```
SINGLE_POSITION_MODE=true
```

### Fichiers modifiés
- `telegram_listener_v11.py`

---

## 2. Signaux Zone → Prix Unique (midian)

### Problème
Les signaux zone (BUY 4000 4010) perdaient systématiquement car le prix quittait la zone avant l'exécution, ou le LIMIT ne se remplissait jamais.

### Solution
Convertir le signal zone en Prix Unique avec entry = midian de la zone (4005).
Appliquer la logique ZN1/ZN2 :
- **ZN1** : prix entre entry et SL → MARKET `CH{x}-ZN1`
- **ZN2** : prix entre entry et entry ± `ZN_PRICE_TOLERANCE` → MARKET `CH{x}-ZN2`
- Sinon : annulé

### Paramètres `.env`
```
ZN_PRICE_TOLERANCE=3.0
```

---

## 3. Prix Unique — PU1/PU2

### Problème
Les anciens scénarios S1/S2/S3 étaient complexes et le S2 (LIMIT) perdait.

### Solution
- **PU1** : prix entre entry et SL → MARKET `CH{x}-PU1`
- **PU2** : prix entre entry et entry ± `PU_PRICE_TOLERANCE` → MARKET `CH{x}-PU2`
- Sinon : annulé

### Paramètres `.env`
```
PU_PRICE_TOLERANCE=3.0
```

---

## 4. Quick Alert — AL-MP uniquement

### Problème
Les QA avec prix (SELL 4000) se transformaient en LIMIT qui ne se remplissaient pas ou se faisaient stopper.

### Solution
- **QA sans prix** (GOLD BUY NOW) → MARKET immédiat `CH{x}-AL-MP`
- **QA avec prix** (SELL 4000) → vérifier `|current - entry| ≤ QA_PRICE_TOLERANCE` → MARKET `CH{x}-AL-MP` ou annulé

### Paramètres `.env`
```
QA_PRICE_TOLERANCE=3.0
```

---

## 5. Fusion — SL/TP mis à jour

### Problème
Le merge ajoutait une 2ème position (LIMIT) avec SL_TOTAL, doublant le risque.

### Solution
Si QA actif : mettre à jour SL et TP avec ceux du signal complet. Pas de 2ème position.
Si QA fermé (SL/TP touché) : signal complet ignoré.

---

## 6. BE — SL @ entry + TP @ entry ± TP_FIXED_GAIN_USD

### Problème
Le bot fermait manuellement les positions quand le profit atteignait TP_FIXED_GAIN_USD. Le SL était au prix d'entrée mais le TP restait celui du signal (trop loin).

### Solution
Quand BE activé (profit ≥ PNL_TRIGGER_USD) :
- SL → entry price
- TP → entry ± TP_FIXED_GAIN_USD (en points de prix)
- MT5 ferme automatiquement quand le prix atteint le TP

### Paramètres `.env`
```
PNL_TRIGGER_USD=7.0
TP_FIXED_GAIN_USD=10.0
```

---

## 7. Nettoyage du code

### Fonctions devenues no-ops
- `_check_pending_only_expiry` — pas d'orders pending
- `_cancel_pending_orders_for_entry` — rien à annuler
- `update_pending_orders_sl` — rien à modifier

### Rôles BE simplifiés
```python
_be_allowed_roles = {"market_single", "quick_market"}
```

---

## Résumé des paramètres `.env` ajoutés

```ini
SINGLE_POSITION_MODE=true
TP_DISTANCE_MIN_RATIO=0.3
QA_PRICE_TOLERANCE=3.0
PU_PRICE_TOLERANCE=3.0
ZN_PRICE_TOLERANCE=3.0
```

## Commentaires MT5

| Type | Commentaire |
|---|---|
| QA sans prix | `CH{x}-AL-MP` |
| QA avec prix | `CH{x}-AL-MP` |
| PU1 | `CH{x}-PU1` |
| PU2 | `CH{x}-PU2` |
| ZN1 | `CH{x}-ZN1` |
| ZN2 | `CH{x}-ZN2` |
