# Version : V14.0.0 — Multi-Position A/B Testing & Parser Improvements

### Date : 2026-08-02

---

## 1. NOUVEAU : 5 positions par signal (A/B Testing)

### Principe
Pour chaque signal reçu, le bot ouvre **5 positions simultanées** (même prix d'entrée, même lot 0.01$) et les gère avec des méthodes différentes pour comparer les performances.

### Méthodes

| Position | Rôle | TP | Gestion |
|---|---|---|---|
| **P1** | `tp_fixe` | TP du signal | BE classique : quand profit ≥ `PNL_TRIGGER_USD`, SL → entry ± `BE_USD` |
| **P2** | `be_scale` | entry ± 20$ | BE escaladé : SL progressif selon le profit (voir niveaux ci-dessous) |
| **P3** | `trailing` | Aucun (TP lointain) | Trailing stop de `TRAILING_STOP_USD`$ qui suit le prix |
| **P4a** | `partial_quick` | entry ± 5$ | Fermeture automatique dès +5$ de profit |
| **P4b** | `partial_trail` | Aucun (TP lointain) | Trailing stop de `PARTIAL_TRAIL_USD`$ qui suit le prix |

### Niveaux BE Escaladé (P2)

| Profit atteint | SL déplacé à |
|---|---|
| +5$ | entry (BE neutre) |
| +10$ | entry + 3$ (sécurisé) |
| +15$ | entry + 7$ (verrouillé) |

### Comment MT5 identifie chaque position

Le champ `comment` de MT5 (max 31 chars) identifie la méthode :
```
CH1-ZN1-P1    ← canal 1, zone 1, méthode TP Fixe
CH1-ZN1-P2    ← canal 1, zone 1, méthode BE Escaladé
CH1-ZN1-P3    ← canal 1, zone 1, méthode Trailing
CH1-ZN1-P4a   ← canal 1, zone 1, méthode Partial Quick
CH1-ZN1-P4b   ← canal 1, zone 1, méthode Partial Trail
```

### Types de signaux concernés

Tous les types de signaux ouvrent 5 positions :
- **ZN** (zone : prix dans la zone ou entre zone et SL)
- **PU** (prix unique : avec tolérance)
- **AL-MP** (quick alert : marché immédiat)

### Implémentation

- `_open_multi_positions()` : fonction helper qui ouvre les 5 positions
- `_check_all()` : dispatch par rôle (`t.get("role")`) vers les 5 méthodes de gestion
- `_manage_tp_fixe()`, `_manage_be_scale()`, `_manage_trailing()`, `_manage_partial_quick()`, `_manage_partial_trail()` : méthodes de gestion individuelles

---

## 2. NOUVEAU : SL plafonné (MAX_SL_USD)

### Principe
Le SL du signal est plafonné à une distance maximale en dollars. Si le SL du signal dépasse cette distance, il est capé.

Pour XAUUSD avec 0.01 lot : **1$ de prix = 1$ de P&L**.

### Logique

```python
def _cap_sl(action, entry_price, signal_sl, max_sl_usd):
    distance = abs(entry_price - signal_sl)
    if distance <= max_sl_usd:
        return signal_sl  # gardé tel quel
    # capé à max_sl_usd de l'entrée
    if action == "BUY":
        return entry_price - max_sl_usd
    else:
        return entry_price + max_sl_usd
```

### Où le SL est capé

Le `_cap_sl()` est appelé dans **4 endroits** :
1. Ouverture d'une position zone (ZN1/ZN2)
2. Ouverture d'une position prix unique (PU1/PU2)
3. Ouverture d'une quick alert (AL-MP)
4. Fusion d'un QA avec un signal complet (`merge_quick_alert`)

---

## 3. NOUVEAU : Rapport quotidien (texte + PDF)

### Principe
À `TRADING_END_HOUR`, le bot ferme toutes les positions et génère un rapport quotidien envoyé à Telegram en deux formats : message texte et fichier PDF.

### Contenu du rapport

**Résumé global :**
- P&L réalisé
- Trades / Wins / Losses / Winrate

**Par méthode (P1-P4b) :**
- Trades, Winrate, P&L, P&L moyen

**Par canal (CH{x}) :**
- Trades, Winrate, P&L
- Triés du meilleur au pire

**Clôtures :**
- TP : nombre de trades + P&L
- SL : nombre de trades + P&L

### PDF
Le PDF contient les mêmes données avec en plus le **nom du canal** (pas seulement CH{x}).

### Implémentation
- `_collect_daily_report_data()` : collecte les données avant fermeture
- `_generate_daily_report_pdf()` : génère le PDF avec fpdf2
- `_send_telegram_document()` : envoie le fichier via Telethon
- `bot_messages_v14.py` : fonctions `report_daily_summary`, `report_daily_by_method`, `report_daily_by_channel`, `report_daily_closes`, `report_daily_full`

---

## 4. NOUVEAU : Fermeture automatique à TRADING_END_HOUR

### Principe
Le bot ferme **toutes les positions** à `TRADING_END_HOUR` pour pouvoir calculer la performance du jour.

### Comportement

| Heure UTC | Action |
|---|---|
| `TRADING_START_HOUR` | `RESET JOURNALIER` → nouveau jour, flag reset |
| Plage horaire | Trading normal (signaux + gestion) |
| `TRADING_END_HOUR` | **Fermeture automatique** : toutes les positions + annulation ordres + rapport |
| Hors plage | Nouveaux signaux ignorés |

### Implémentation
- `_shutdown_end_of_day()` : ferme tout, collecte données, génère rapport, envoie PDF
- `_end_of_day_done` : flag pour éviter les fermetures répétées
- Reset automatique au début du nouveau jour de trading

---

## 5. NOUVEAU : Contrôle des logs et alertes

### Variables

| Variable | Défaut | Description |
|---|---|---|
| `LOG_TRADE_MANAGEMENT` | `true` | Logs console pour P1-P4b (BE, trailing, partial) |
| `ALERT_TRADE_MANAGEMENT` | `true` | Alertes Telegram pour P1-P4b |
| `ALERT_DAILY_PERFORMANCE` | `true` | Rapport quotidien P&L → Telegram |

### Quand `LOG_TRADE_MANAGEMENT=false`

Seuls les messages **une fois par jour** restent :
- MT5 connecté, Telegram connecté, Balance
- NEWS HIGH IMPACT (1x/jour)
- RESET JOURNALIER (1x/jour)
- DAILY-LIMIT (1x/jour)
- Banner config (1x/jour)
- SHUTDOWN

Tout le reste (signaux, clôtures, trailing, BE, fusion, conflit, news blocking...) est **exclu**.

### Implémentation
- `_log_mgmt(msg)` : wrapper qui vérifie `LOG_TRADE_MANAGEMENT` avant `log.info()`
- `_alert_mgmt(msg)` : wrapper qui vérifie `ALERT_TRADE_MANAGEMENT` avant `send_alert_sync()`

---

## 6. FIX PARSER : Fallback XAUUSD par défaut

Si aucun symbole n'est détecté, le parser utilise `XAUUSD` par défaut. Impact : 92.9% → 100% de parsing.

---

## 7. FIX PARSER : Détection des signaux à double zones

Fonction `_count_zone_candidates()` qui compte les paires de nombres sur la **même ligne** du texte brut (hors TPs et SL). Si > 1 paire → signal annulé comme ambigu.

---

## 8. FIX PARSER : Zone détectée après BUY/SELL

- Règle 10 modifiée : détecte `BUY <prix1> <prix2>` ou `BUY <prix1>-<prix2>` comme zone
- Règle 11 modifiée : vérifie les 2 premiers nombres après BUY/SELL, zone si 2 nombres consécutifs
- `NOW` ignoré entre BUY/SELL et les prix

---

## 9. FIX PARSER : @ dans les TPs ignoré

`TP1 @ 4076` n'est plus matché comme entrée. Le `@` après TP/TARGET est ignoré.

---

## 10. FIX PARSER : Décimaux préservés

`4074.00` reste `4074.00` (pas transformé en `407400`). Les points parasites (`4025...`) sont supprimés sans casser les décimaux.

---

## Fichiers du projet (v14)

| Fichier | Lignes | Description |
|---|---|---|
| `telegram_listener_v14.py` | ~2700 | Cœur du bot (multi-positions + SL capé + rapport + PDF) |
| `signal_parser_v14.py` | ~880 | Parser amélioré |
| `bot_messages_v14.py` | ~450 | Logs + alertes + rapport quotidien |
| `13.env` | — | Configuration |
| `CONTEXT.md` | — | Ce fichier |

---

## Variables .env (v14)

### Nouvelles variables

| Variable | Défaut | Description |
|---|---|---|
| `MAX_SL_USD` | 15.0 | Distance SL maximale en $ |
| `TRAILING_STOP_USD` | 5.0 | Trailing stop pour P3 |
| `PARTIAL_TRAIL_USD` | 3.0 | Trailing stop pour P4b |
| `LOG_TRADE_MANAGEMENT` | true | Logs console P1-P4b |
| `ALERT_TRADE_MANAGEMENT` | true | Alertes Telegram P1-P4b |
| `ALERT_DAILY_PERFORMANCE` | true | Rapport quotidien → Telegram |

### Variables existantes (inchangées)

| Variable | Défaut | Description |
|---|---|---|
| `TP_FIXED_GAIN_USD` | 10.0 | Gain cible pour P1 |
| `PNL_TRIGGER_USD` | 7.0 | Seuil BE pour P1 |
| `BE_USD` | 3.0 | Marge de sécurité SL pour P1 |
| `QUICK_ALERT_SL_OFFSET` | 10.0 | Offset SL par défaut |
| `RR_RATIO_DEFAULT` | 1.5 | Ratio risk/reward |

---

## Architecture v14

```
Telegram (52 canaux) → Signal Parser V14 → MT5 Bridge → MetaTrader 5 (Exness)
                              ↓                    ↓
                        Fallback XAUUSD      5 positions × signal
                        Double zone check    ↓
                        SL plafonné     ┌──── P1: TP Fixe (BE classique)
                                        ├──── P2: BE Escaladé
                                        ├──── P3: Trailing Stop
                                        ├──── P4a: Partial Quick (+5$)
                                        └──── P4b: Partial Trail (3$)
                                                ↓
                                        Fin de journée (20h UTC)
                                                ↓
                                        Rapport texte + PDF → Telegram
```

---

## Fichiers du projet

| Fichier | Description |
|---|---|
| `telegram_listener_v14.py` | Cœur du bot |
| `signal_parser_v14.py` | Parser signaux |
| `bot_messages_v14.py` | Logs, alertes, rapport |
| `telegram_listener_v13.py` | Version précédente (conservée) |
| `signal_parser_v13.py` | Version précédente (conservée) |
| `signal_parser.py` | Copie de v14 (importé par v13) |
| `bot_messages.py` | Version précédente (conservée) |
| `13.env` | Configuration |
| `CONTEXT.md` | Ce fichier |

---

## Token GitHub

Le token fonctionne pour les pushes. Utiliser `git remote set-url origin https://<TOKEN>@github.com/slh04ninja-cmyk/tgm.git` puis `git push origin main`.
