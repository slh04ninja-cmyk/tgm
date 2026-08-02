# GZL TradingBot V14.0.0 — Documentation

## Vue d'ensemble

La v14 introduit :
- **Multi-positions A/B Testing** (5 positions par signal)
- **SL plafonné** (MAX_SL_USD)
- **Rapport quotidien** (texte + PDF)
- **Fermeture automatique** à TRADING_END_HOUR
- **Contrôle des logs** (3 variables booléennes)
- **Parser amélioré** (fallback, double zone, BUY NOW zones)

---

## 1. Multi-Positions (A/B Testing)

Pour chaque signal (ZN, PU, AL-MP), le bot ouvre **5 positions MARKET** au même prix, même lot (0.01), et les gère différemment.

| Position | Rôle | TP | Gestion |
|---|---|---|---|
| P1 | tp_fixe | TP du signal | BE classique (PNL_TRIGGER_USD → BE_USD) |
| P2 | be_scale | +20$ | BE escaladé (+5$→entry, +10$→+3$, +15$→+7$) |
| P3 | trailing | ∞ | Trailing TRAILING_STOP_USD$ |
| P4a | partial_quick | +5$ | Fermeture à +5$ |
| P4b | partial_trail | ∞ | Trailing PARTIAL_TRAIL_USD$ |

Comment MT5 : `CH1-ZN1-P1`, `CH1-ZN1-P2`, `CH1-ZN1-P3`, `CH1-ZN1-P4a`, `CH1-ZN1-P4b`

---

## 2. SL Plafonné

Le SL est capé à MAX_SL_USD$ de l'entrée. Appliqué dans 4 endroits : ZN, PU, AL-MP, fusion.

```
BUY @ 4000, signal SL = 3960 (40$)  → SL capé à 3985 (15$)
BUY @ 4000, signal SL = 3990 (10$)  → SL gardé à 3990
```

---

## 3. Rapport quotidien

À TRADING_END_HOUR, le bot envoie :
1. **Message Telegram** : résumé texte
2. **Fichier PDF** : tableaux détaillés avec noms des canaux

Contenu :
- Résumé global (P&L, trades, winrate)
- Par méthode (P1-P4b)
- Par canal (CH1, CH2... avec nom dans le PDF)
- Clôtures (TP/SL)

---

## 4. Fermeture automatique

Le bot ferme toutes les positions à TRADING_END_HOUR. Le flag `_end_of_day_done` évite les fermetures répétées. Reset automatique au nouveau jour.

---

## 5. Contrôle des logs

```bash
LOG_TRADE_MANAGEMENT=true    # Logs P1-P4b en console
ALERT_TRADE_MANAGEMENT=true  # Alertes Telegram P1-P4b
ALERT_DAILY_PERFORMANCE=true # Rapport quotidien
```

Quand `LOG_TRADE_MANAGEMENT=false`, seuls les messages 1x/jour restent (connexion, news, reset, daily limit, shutdown).

---

## 6. Parser V14

- **Fallback XAUUSD** : signaux sans symbole → XAUUSD par défaut
- **Double zone** : annulation si plusieurs paires de nombres sur la même ligne
- **BUY NOW zones** : `BUY NOW 4030-4025` → zone [4025, 4030]
- **@ dans TPs** : `TP1 @ 4076` n'est plus pris comme entrée
- **Décimaux** : `4074.00` préservé (pas 407400)

---

## 7. Variables .env

```bash
# Multi-positions
MAX_SL_USD=15.0
TRAILING_STOP_USD=5.0
PARTIAL_TRAIL_USD=3.0

# Logs & Alertes
LOG_TRADE_MANAGEMENT=true
ALERT_TRADE_MANAGEMENT=true
ALERT_DAILY_PERFORMANCE=true

# Existant
TP_FIXED_GAIN_USD=10.0
PNL_TRIGGER_USD=7.0
BE_USD=3
TRADING_START_HOUR=2
TRADING_END_HOUR=20
```

---

## 8. Fichiers

| Fichier | Description |
|---|---|
| telegram_listener_v14.py | Cœur du bot |
| signal_parser_v14.py | Parser signaux |
| bot_messages_v14.py | Logs, alertes, rapport |
| 13.env | Configuration |

---

## 9. Flux complet

```
Signal Telegram
    ↓
Parser V14 (fallback XAUUSD, double zone check)
    ↓
SL capé (MAX_SL_USD)
    ↓
5 positions MARKET (P1-P4b)
    ↓
TradeManager._check_all() → dispatch par rôle
    ↓
Fin de journée → fermeture + rapport texte + PDF → Telegram
```
