# Version : V16.0.0 — MARKET + LIMIT Orders & P&L Target Close

### Date : 2026-08-18

---

## 1. NOUVEAU : Système MARKET + LIMIT (remplace Multi-Positions V14)

### Principe
Chaque signal ouvre **1 ordre MARKET** (exécution immédiate) + **2 ordres LIMIT** (pullback à meilleur prix). Les LIMITs sont placés à un offset du prix actuel pour catcher un éventuel retour de prix.

### Offsets

| Ordre | Variable | Défaut | Description |
|---|---|---|---|
| LIMIT 1 | `LIMIT_OFFSET_1` | 3.0 | Offset du prix actuel |
| LIMIT 2 | `LIMIT_OFFSET_2` | 6.0 | Offset du prix actuel |

**BUY** → LIMIT en dessous du prix actuel (achat moins cher)
**SELL** → LIMIT au-dessus du prix actuel (vente plus cher)

### Lots

| Ordre | Variable | Défaut |
|---|---|---|
| MARKET | `LOT_MARKET` / `LOT_TOTAL` | 0.01 |
| LIMIT 1 | `LOT_LIMIT1` | 0.01 |
| LIMIT 2 | `LOT_LIMIT2` | 0.01 |

### Identification MT5

```
CH20-ZN-MK     ← canal 20, zone, Market
CH20-ZN-L1     ← canal 20, zone, Limit 1
CH20-ZN-L2     ← canal 20, zone, Limit 2
```

### Scénarios de remplissage

| Cas | Positions actives | Description |
|---|---|---|
| Cas 1 | MARKET seul | Prix ne revient pas → LIMITs non remplis |
| Cas 2 | MARKET + L1 | Prix revient partiellement → L1 rempli |
| Cas 3 | MARKET + L1 + L2 | Prix revient fort → les 2 LIMITs remplis |

### Configuration

```
LIMIT_ENABLED=true          # Activer les LIMIT orders
LIMIT_COUNT=2               # Nombre de LIMIT orders (0, 1 ou 2)
LIMIT_OFFSET_1=3.0          # 1er LIMIT
LIMIT_OFFSET_2=6.0          # 2ème LIMIT
LOT_LIMIT1=0.01             # Lot LIMIT 1
LOT_LIMIT2=0.01             # Lot LIMIT 2
LIMIT_EXPIRY_MIN=30         # Expiration (minutes)
```

---

## 2. NOUVEAU : TP Dynamique (TP_FIXED_GAIN_USD)

### Principe
Le TP n'est **pas celui du signal**. Il est calculé dynamiquement en fonction de `TP_FIXED_GAIN_USD` et du nombre de positions actives. Recalculé à chaque fois qu'un LIMIT se remplit.

### Formule

```
pnl_cible = TP_FIXED_GAIN_USD × multiplicateur
price_movement = pnl_cible / nb_positions
TP = average_entry ± price_movement
```

### Multiplicateur

| Cas | Positions | Multiplicateur | pnl_cible |
|---|---|---|---|
| Cas 1 | MARKET seul | 1.0 | TP_FIXED_GAIN_USD × 1 |
| Cas 2 | MARKET + L1 | TP_MULTIPE1 | TP_FIXED_GAIN_USD × TP_MULTIPE1 |
| Cas 3 | MARKET + L1 + L2 | TP_MULTIPE2 | TP_FIXED_GAIN_USD × TP_MULTIPE2 |

### Average entry pondéré

```
average_entry = (entry_MK × lot_MK + entry_L1 × lot_L1 + entry_L2 × lot_L2) / total_lot
```

### Exemple (BUY, TP_FIXED_GAIN_USD=0.5, TP_MULTIPE1=1.5)

```
MK @ 4418.02, L1 @ 4415.037 (Cas 2)

avg = (4418.02 + 4415.037) / 2 = 4416.5285
pnl_cible = 0.5 × 1.5 = 0.75$
price_movement = 0.75 / 2 = 0.375
TP = 4416.5285 + 0.375 = 4416.90

Vérification P&L (0.01 lot = 1 oz) :
  MK : (4416.90 - 4418.02) × 1 = -1.12$
  L1 : (4416.90 - 4415.037) × 1 = +1.86$
  Total = +0.75$ ✅
```

### Configuration

```
TP_FIXED_GAIN_USD=8.0          # Gain cible par position
TP_MULTIPE1=2.0                 # Multiplicateur MARKET + L1
TP_MULTIPE2=3.0                 # Multiplicateur MARKET + L1 + L2
```

---

## 3. NOUVEAU : Clôture P&L Cible (PHASE 4b)

### Principe
À chaque cycle de polling (~1s), le bot vérifie si le **P&L flottant** (depuis MT5 via `pos.profit + pos.swap`) atteint le `pnl_cible`. Si oui :
1. Ferme **toutes les positions actives**
2. Annule **tous les LIMITs en attente**
3. Envoie une alerte Telegram

### Cycle

```
_check_all()
  ├── PHASE 4  : _recalculate_tp(entry) → met à jour le TP
  ├── PHASE 4b : P&L flottant >= pnl_cible ?
  │       ├── OUI → close_all + cancel_pending_limits
  │       └── NON → continuer
  └── PHASE 5  : Si MARKET fermé → annuler LIMITs restants
```

### Important
Le bot ne ferme PAS au TP prix — il surveille le P&L flottant réel depuis MT5.

---

## 4. FIX : Filling Mode Fallback (LIMIT orders)

### Problème (V14)
Les LIMIT orders utilisaient `ORDER_FILLING_RETURN` en dur → erreur 10013 (Invalid Price) systématique sur certains brokers.

### Solution (V16)
Fallback identique au MARKET : essayer FOK → IOC → RETURN jusqu'au succès.

```
LIMIT 1 → FOK → 10013 → IOC → SUCCÈS ✅
LIMIT 2 → FOK → 10013 → IOC → SUCCÈS ✅
```

---

## 5. FIX : Résolution symbole (bridge._sym)

### Problème (V14)
MARKET utilisait `bridge._sym(symbol)` (résout les suffixes : XAUUSD → XAUUSDm).
LIMIT utilisait `mt5.symbol_info(symbol)` direct → symbole introuvable → 10013.

### Solution (V16)
LIMIT utilise maintenant `bridge._sym(symbol)` comme le MARKET.

---

## 6. FIX : Variable lot_limit → limit_lot

Le ticket LIMIT enregistrait `"lot": lot_limit` (inexistant) au lieu de `"lot": limit_lot`. Corrigé.

---

## 7. FIX : CLOSE annule les LIMITs

### Problème (V14)
Le handler CLOSE faisait `bridge.close_all()` → ferme les positions mais PAS les LIMITs en attente.

### Solution (V16)
Après `close_all()`, le bot parcourt `manager.active` et appelle `cancel_pending_limits()` pour le canal concerné.

---

## 8. SL Plafonné (MAX_SL_USD) — inchangé

Le SL est capé à `MAX_SL_USD` de l'entrée (défaut 10$). `_cap_sl()` appelé dans 4 endroits : ZN, PU, QA, Fusion.

---

## 9. Tolérance Zone — inchangé

| Variable | Défaut | Description |
|---|---|---|
| `TOLERANCE_ZN` | 1.0 | Tolérance autour de la zone |
| `TOLERANCE_PU` | 3.0 | Tolérance signaux Prix Unique |
| `TOLERANCE_MP` | 5.0 | Tolérance MARKET PRICE |

---

## 10. Architecture V16

```
Telegram (canaux configurés) → Signal Parser → MT5 Bridge → MetaTrader 5
                                        ↓
                                  1 MARKET + 2 LIMITs
                                        ↓
                                  TP Dynamique (TP_FIXED_GAIN_USD)
                                        ↓
                                  Clôture P&L Cible (ferme tout)
                                        ↓
                                  CLOSE signal → annule LIMITs
```

---

## 11. Fichiers du projet (V16)

| Fichier | Lignes | Description |
|---|---|---|
| `telegram_listener_v16.py` | ~3300 | Cœur du bot (MARKET+LIMIT, TP dynamique, P&L close) |
| `signal_parser.py` | — | Parser signaux |
| `bot_messages.py` | — | Logs, alertes |
| `bot_documentation_v16.html` | ~640 | Documentation HTML complète |
| `.env` | — | Configuration |
| `CONTEXT.md` | — | Ce fichier |

---

## 12. Variables .env (V16)

### MARKET + LIMIT

| Variable | Défaut | Description |
|---|---|---|
| `LIMIT_ENABLED` | true | Activer LIMIT orders |
| `LIMIT_COUNT` | 2 | Nombre de LIMITs |
| `LIMIT_OFFSET_1` | 3.0 | Offset LIMIT 1 |
| `LIMIT_OFFSET_2` | 6.0 | Offset LIMIT 2 |
| `LOT_MARKET` | 0.01 | Lot MARKET |
| `LOT_LIMIT1` | 0.01 | Lot LIMIT 1 |
| `LOT_LIMIT2` | 0.01 | Lot LIMIT 2 |
| `LIMIT_EXPIRY_MIN` | 30 | Expiration LIMITs (minutes) |

### TP Dynamique

| Variable | Défaut | Description |
|---|---|---|
| `TP_FIXED_GAIN_USD` | 8.0 | Gain cible par position |
| `TP_MULTIPE1` | 2.0 | Multiplicateur MARKET + L1 |
| `TP_MULTIPE2` | 3.0 | Multiplicateur MARKET + L1 + L2 |

### SL & Tolérances

| Variable | Défaut | Description |
|---|---|---|
| `MAX_SL_USD` | 10.0 | Distance SL maximale |
| `TOLERANCE_ZN` | 1.0 | Tolérance zone |
| `TOLERANCE_PU` | 3.0 | Tolérance prix unique |
| `TOLERANCE_MP` | 5.0 | Tolérance MARKET PRICE |

### Général

| Variable | Défaut | Description |
|---|---|---|
| `MAGIC_NUMBER` | 20250226 | Identifiant bot MT5 |
| `MAX_POSITIONS` | 3 | Max signaux actifs |
| `SLIPPAGE` | 20 | Slippage autorisé |
| `MAX_SPREAD_POINTS` | 50 | Spread max |

---

## 13. Commits V16

| Hash | Message |
|---|---|
| `7c405e7` | fix(v16): LIMIT orders filling fallback + TP_FIXED_GAIN_USD P&L close |
| `69c6330` | debug(v16): add detailed logging for LIMIT order failures |
| `4903c17` | fix(v16): resolve symbol for LIMIT orders (bridge._sym) |
| `a4aca1f` | fix(v16): debug log f-string escaping |
| `3721e0b` | fix(v16): CLOSE signal now also cancels pending LIMIT orders |
| `8ad7cdb` | docs(v16): HTML documentation |
