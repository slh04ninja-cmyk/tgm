# CONTEXT.md — Modifications du Bot

## Version : V10.1.1 — Alert Timeout + News Filter + SL Merge

### Date : 2026-07-20

---

## 1. FIX #7 : Telegram Alert Timeout (CRITIQUE)

### Problème
`send_alert_sync()` bloquait le thread pendant 15s à chaque envoi d'alerte. Toutes les alertes Telegram timeout → notifications jamais reçues. Le blocage de 15s retardait la boucle de trading (ordres placés avec 15s de retard).

### Correction
- Timeout réduit à 8s par tentative
- Retry automatique (2 tentatives, 2s entre chaque)
- Total max bloquant : 10s au lieu de 15s
- Erreurs non-récupérables ne retry pas

### Fichiers modifiés
- `telegram_listener_v10.py` : fonction `send_alert_sync()`

---

## 2. FIX #8 : News Filter — Impact configurable + Log 1h avant NY

### Problème
Le filtre news ne matchait que `impact == "high"`. L'API FF Calendar retourne les événements USD avec impact "Low" ou "Medium" certaines semaines → 0 news filtrées sur 69 events reçus. Le warning se répétait toutes les 30 min.

### Correction
- Nouveau paramètre `NEWS_MIN_IMPACT` dans `.env` (défaut: `high`)
- Quand `NEWS_MIN_IMPACT=high`, les événements "Medium" sont aussi inclus
- Log des news chargées **1h avant l'ouverture NY** (12:30 UTC) + détails des événements
- Fetch silencieux le reste du temps (cache mis à jour sans log)

### Configuration `.env`
```
NEWS_MIN_IMPACT=high
```

### Fichiers modifiés
- `telegram_listener_v10.py` : `_fetch_news()`, banner de config
- `.env` : ajout `NEWS_MIN_IMPACT=high`

---

## 3. SL_PLUS_PROCHE — Correction du SL lors de la fusion (Merge)

### Problème
Lors de la fusion (merge) d'un Quick Alert avec un signal complet, le SL du signal complet était appliqué directement **sans appliquer SL_PLUS_PROCHE**. Résultat : le SL pouvait être beaucoup trop large (15 pts au lieu de 6).

### Exemple concret (CH5 — AMELIA GOLD TRADER)
```
QA Market Price : SELL @4012.335, SL=4022.335 (provisoire, 10 pts)
Signal fusion   : SELL 4029, SL=4040 (du signal complet)

AVANT fix :  MG SL = 4040 (18 pts de risque !)
APRÈS fix :  MG SL = 4020 + 6 = 4026 (SL_PLUS_PROCHE respecté)
             MP-MKT SL = 4026 (même SL que MG)
```

### Règle SL_PLUS_PROCHE
```
MG est la position la plus proche du SL
→ SL = MG_entry + SL_PLUS_PROCHE (6 pts)
→ Le même SL est appliqué à TOUTES les positions du trade (MG + MP-MKT/LMT)
```

### Corrections appliquées

#### 3.1 `_place_merge_limit()` — Calcul du SL
- Le SL est maintenant calculé depuis l'entrée du merge limit : `merge_sl = limit_price + SL_PLUS_PROCHE`
- `full_signal["sl"]` est mis à jour avant `place_limit_order()`
- Retourne `(ticket, merge_sl)` au lieu de juste `ticket`

#### 3.2 Merge PO-OV (position ouverte)
- `_place_merge_limit()` appelé en premier pour obtenir `merge_sl`
- `bridge.modify_sl_tp(qa_ticket, merge_sl, ...)` applique le SL du merge sur le MP-MKT

#### 3.3 Merge LMT-RP (limit rempli)
- `_place_merge_limit()` → `merge_sl`
- `bridge.modify_sl_tp(resolved_pos.ticket, merge_sl, ...)` sur la position remplie

#### 3.4 Merge LMT-PDN (limit pending)
- `_place_merge_limit()` → `merge_sl`
- `bridge.modify_pending_order(qa_ticket, merge_sl, ...)` sur l'ordre pending

### Fichiers modifiés
- `telegram_listener_v10.py` : `_place_merge_limit()`, `merge_quick_alert()`

---

## 4. Prix Unique S2 — SL_PRIX_UNIQUE non appliqué

### Problème
L'ajustement `SL_PRIX_UNIQUE` modifiait la variable `sl` mais pas `signal["sl"]`. La fonction `place_limit_order()` lit `signal.get("sl", 0)` directement → le SL non ajusté était envoyé à MT5.

### Correction
Ajout de `signal["sl"] = sl` après l'ajustement SL_PRIX_UNIQUE.

### Fichiers modifiés
- `telegram_listener_v10.py` : `execute_signal()` section Prix Unique

---

## 5. Règles SL — Résumé universel

| Type de position | Règle SL | Exemple |
|------------------|----------|---------|
| Quick Alert (provisoire) | `entry + QUICK_ALERT_SL_OFFSET (10)` | @4012 → SL @4022 |
| Prix Unique | `min(signal_sl, entry + SL_PRIX_UNIQUE)` | @4013, SL signal=4027 → SL @4023 |
| CAS 1/2a/2b | `adjust_sl_to_nearest_entry(SL_PLUS_PROCHE)` | Position la plus proche + 6 pts |
| Merge MG | `merge_entry + SL_PLUS_PROCHE` | @4020 → SL @4026 |
| Merge MP-MKT/LMT | Même SL que MG | @4012 → SL @4026 |

---

## Version : V10.1.0 — Market Price + Merge Price + Fusion Tolérance

### Date : 2026-07-18

### Fonctionnalités
1. **Market Price** — Exécution MARKET sans prix (NOW, MARKET, MKT, IMMEDIATELY)
2. **Merge Price** — Prix d'edge via BUY MORE / SELL MORE / ADD MORE
3. **Fusion Tolérance** — Configurable via `FUSION_TOLERANCE` dans `.env`
4. **Annulation QA** — Si fusion échouée (hors tolérance), QA annulé
5. **Alertes SL/TP provisoire** — Si SL/TP touché avant fusion
6. **Surveillance QA-LMT** — Check continu SL/TP provisoire
7. **Messages fusion** — PO-OV, LMT-RP, LMT-PDN

### Labels MT5
| Commentaire | Signification |
|-------------|---------------|
| `CH{num}-MP-MKT` | Market Price MARKET |
| `CH{num}-AL-MKT` | Quick Alert MARKET |
| `CH{num}-AL-LMT` | Quick Alert LIMIT |
| `CH{num}-MG` | Merge limit |
