# Version : V12.0.0 — Code Cleanup & Bug Fixes

### Date : 2026-07-29

---

## 1. FIX CRITIQUE : NameError variable `orders` non définie

### Problème
Après le nettoyage des pending orders (v11→v12), la variable `orders` a été supprimée de `execute_signal()` mais une référence restait à la ligne 1727 :
```python
if not orders and not tickets:  # ← NameError: name 'orders' is not defined
```

### Impact
**Crash du bot** au premier signal PU (prix unique) où le MARKET échoue.

### Solution
```python
if not tickets:  # ← simplifié, orders était toujours []
```

### Fichiers modifiés
- `telegram_listener_v12.py`

---

## 2. FIX : Crash TypeError dans le filtre distance TP (Quick Alert)

### Problème
Dans `execute_quick_alert()`, si le parser retourne `is_quick_alert=True` avec `entry_price=None` et `is_market_price=False`, le code accédait à `abs(default_tp - entry_price)` → **TypeError**.

### Solution
Ajout d'un guard avant le filtre :
```python
if entry_price is None:
    log.warning(f"Quick Alert ignorée — entry_price manquant | {symbol} {action}")
    return
```

### Fichiers modifiés
- `telegram_listener_v12.py`

---

## 3. FIX : P&L incorrect dans `_close_all_positions()`

### Problème
Le `history_deals_get()` était appelé immédiatement après `close_position()`. MT5 peut avoir un délai de propagation → le deal de clôture n'est pas encore visible → P&L=0.

### Solution
Ajout de `time.sleep(0.3)` entre `close_position()` et `history_deals_get()`.

### Fichiers modifiés
- `telegram_listener_v12.py`

---

## 4. FIX : Doublons alertes Telegram

### Problème
Les alertes étaient envoyées en double car :
- `_alert_dedup_cache` utilisait `hash()` Python (non-deterministe entre sessions)
- TTL de seulement 10s
- Clé incluait le nom du canal → le même signal du canal et du groupe de discussion avait des clés différentes

### Solution
- Clé de dédup basée sur le contenu normalisé (pas `hash()`)
- Suppression de la ligne `Canal: ...` pour capter les doublons canal+discussion group
- TTL augmenté à 30s
- Eviction FIFO bornée (deque) au lieu de dict + clear()

### Fichiers modifiés
- `telegram_listener_v12.py`

---

## 5. FIX : Doublons handler (message dedup)

### Problème
`_seen_msg_ids` était un `set` vidée entièrement (`clear()`) à 2000 entrées → les anciens message IDs étaient oubliés → doublons possibles.

### Solution
- `_seen_msg_ids`改为 `dict` (id → timestamp) avec TTL
- Eviction partielle (supprime les 1000 plus anciens) au lieu de `clear()` brutal
- Max augmenté à 5000

### Fichiers modifiés
- `telegram_listener_v12.py`

---

## 6. NETTOYAGE : Suppression du code mort (-452 lignes)

### Méthodes supprimées de MT5Bridge
- `place_limit_order()` — jamais appelée (v11 = MARKET only)
- `modify_pending_order()` — jamais appelée

### Méthodes supprimées de TradeManager
- `update_pending_orders_sl()` — plus de pending orders
- `_cancel_pending_orders_for_entry()` — plus de pending orders
- `_get_tp_trigger()` — utilisée uniquement par `_check_pending_only_expiry`
- `_check_pending_only_expiry()` — plus de pending orders
- `_resolve_order()` — résolvait les ordres remplis

### Boucles mortes supprimées
- Pending orders dans `_check_all()` (~100 lignes)
- `_check_pending_only_expiry()` call dans `_check_all()`
- `for o in entry.get("orders", [])` dans `check_conflict()`
- `for o in entry.get("orders", [])` dans `NewsManager._close_all()`

### Config/champs morts supprimés
- `SL_PRIX_UNIQUE` — jamais utilisé
- `expiry` dans entry dicts — jamais vérifié
- `orders` dans entry dicts — toujours `[]`
- `is_limit` dans quick_alerts — toujours `False`
- `update_pending_orders_sl()` call dans SL_MOVE handler

### bot_messages.py nettoyé (17 fonctions mortes)
- `log_close_header`, `log_close_detail`, `log_daily_pnl_after_close`
- `log_be_header`, `log_order_filled`, `log_tp_trigger`, `log_expiration`
- `log_annule`, `log_order_placed_dual`
- `log_tp_fixed_*` (4 fonctions), `alert_tp_fixed`
- `log_merge`, `log_merge_limit_*` (2 fonctions)
- `log_daily_pnl_recovered`

---

## 7. FIX : TP sans mot-clé (signal_parser.py)

### Problème
Les TP écrits sans mot-clé `TP` (ex: `✔️4030`, `✔️.4040`) n'étaient pas détectés.

### Solution
- Nouvelle fonction `_extract_orphan_tps()` : cherche les nombres entre la zone et le SL qui sont dans la direction du trade
- Ajout dans `_parse_trade()` : si `_extract_all_tps()` retourne vide, tente `_extract_orphan_tps()`
- `_extract_orphan_tps()` exclut les valeurs dans [zone_low, zone_high] et le SL

### Fichiers modifiés
- `signal_parser.py`

---

## 8. FIX : Nombres avec point devant (`.4040`)

### Problème
`.4040` (avec point devant) n'était pas parsé comme un nombre valide.

### Solution
Dans `normalize_text()`, ajout d'une regex qui nettoie les points devant les nombres :
```python
text = re.sub(r'\.(\d{4,6})', r' \1', text)  # .4040 → 4040
```

### Fichiers modifiés
- `signal_parser.py`

---

## 9. FIX : MORE SELL/BUY → zone_high/zone_low (signal_parser.py)

### Problème
`MORE SELL 4050` était stocké comme `merge_price` mais le signal restait un prix unique (`is_single_price=True`). Le signal `SELL 4045 MORE SELL 4050` devrait être une zone [4045, 4050].

### Solution
Dans `_parse_trade()`, après extraction du `merge_price` :
```python
if is_single and merge_price is not None:
    zone_low = min(zone_low, merge_price)
    zone_high = max(zone_high, merge_price)
    is_single = False
```

### Comportement
| Signal | Résultat | MORE = |
|---|---|---|
| SELL 4045 MORE SELL 4050 | zone [4045, 4050] | zone_high |
| BUY 4045 MORE BUY 4040 | zone [4040, 4045] | zone_low |

### Fichiers modifiés
- `signal_parser.py`

---

## Résumé des modifications

| Catégorie | Fichier | Lignes |
|---|---|---|
| Bug fixes (v12) | `telegram_listener_v12.py` | — |
| Code mort | `telegram_listener_v12.py` | -361 |
| Code mort | `bot_messages.py` | -91 |
| Parser fixes | `signal_parser.py` | +49 |
| Documentation | `bot_documentation_v12.html` | +974 |
| Config | `.env` | nettoyé |
| **Total** | | **-452 + 49 = -403 net** |

---

## Fichiers du projet

| Fichier | Lignes | Description |
|---|---|---|
| `telegram_listener_v12.py` | 2422 | Cœur du bot (v12 nettoyé) |
| `telegram_listener_v11.py` | 2758 | Version originale (conservée) |
| `signal_parser.py` | 792 | Parse les signaux |
| `bot_messages.py` | 171 | Templates de logs/alertes |
| `.env` | — | Configuration |
| `CONTEXT.md` | — | Ce fichier |
| `bot_documentation_v12.html` | 974 | Documentation HTML complète |
| `bot_documentation_v11.html` | — | Documentation v11 (référence) |

---

## Architecture (inchangée depuis v11)

```
Telegram (18 canaux) → Signal Parser → MT5 Bridge → MetaTrader 5 (Exness)
                ↑                                    ↓
         TimesFM Validator                    Alertes Telegram (@TrdReport)
                ↑                                    ↓
         News Manager                        Performance Tracker
```

### Flux d'un signal
1. Réception Telegram → dédup (message_id + contenu)
2. Filtres : spam → horaire → news → P&L quotidien → conflit
3. Parsing : direction, zone/prix, SL, TP(s)
4. Validation TimesFM (optionnel)
5. Exécution : ZN1/ZN2 ou PU1/PU2 → MARKET
6. Gestion post-trade : BE → SL/TP auto

### Paramètres clés
- `SINGLE_POSITION_MODE=true` — 1 seul MARKET par signal
- `TP_FIXED_GAIN_USD=10` — TP fixe à 10$ de l'entrée
- `PNL_TRIGGER_USD=7` — BE déclenché à 7$ de profit
- `BE_USD=3` — SL à 3$ de l'entrée (côté défavorable)
- `FUSION_TOLERANCE=3` — Tolérance pour matcher QA + signal complet

---

## Signaux testés et validés

| Signal | Parsing | Exécution |
|---|---|---|
| `GOLD BUY @4067-4060 SL BREAKOUT 4057 TP OPEN` | ✅ zone [4060, 4067], TP auto 4073.25 | ✅ ZN1/ZN2 |
| `GOLD BUY Entry: 4028-4022 ✔️4030 ✔️.4040 🛑 SL 4002` | ✅ zone [4022, 4028], TPs [4030, 4040] | ✅ ZN1/ZN2 |
| `XAUUSD SELL 4052/4057 TP1-TP7 SL 4072` | ✅ zone [4052, 4057], 7 TPs | ✅ ZN1/ZN2 |
| `XAUUSD SELL NOW 4079/83 TARGET1-8 SL 4095` | ✅ zone [4079, 4083], 8 TPs | ✅ ZN1/ZN2 |
| `XAUUSD SELL 4045 MORE SELL 4050 TP 4042 TP 4035 SL 4060` | ✅ zone [4045, 4050], TPs [4042, 4035] | ✅ ZN1/ZN2 |

---

## Token GitHub

Le token fonctionne pour les pushes. Utiliser `git remote set-url origin https://<TOKEN>@github.com/slh04ninja-cmyk/tgm.git` puis `git push origin main`.
