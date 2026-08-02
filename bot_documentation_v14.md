# GZL TradingBot V14.0.0 — Documentation

## Vue d'ensemble

La v14 introduit le système de **multi-positions A/B Testing** et le **SL plafonné**.
Le bot ouvre désormais 5 positions simultanées par signal pour comparer 5 méthodes de gestion des trades.

---

## 1. Multi-Positions (A/B Testing)

### Principe

Pour chaque signal reçu (ZN, PU ou AL-MP), le bot ouvre **5 positions MARKET** au même prix d'entrée, avec le même lot (0.01), et les gère différemment.

### Les 5 méthodes

#### P1 — TP Fixe (méthode actuelle)
- **TP** : celui fourni par le signal
- **BE** : quand profit ≥ `PNL_TRIGGER_USD` (défaut 7$)
- **SL** : déplacé à entry ± `BE_USD` (défaut 3$) côté défavorable
- **TP** : modifié à entry ± `TP_FIXED_GAIN_USD` (défaut 10$)

#### P2 — BE Escaladé
- **TP** : entry ± 20$
- **SL progressif** selon le profit :

| Profit | SL déplacé à |
|---|---|
| +5$ | entry (BE neutre) |
| +10$ | entry + 3$ |
| +15$ | entry + 7$ |

#### P3 — Trailing Stop
- **TP** : aucun (TP lointain dans MT5)
- **SL** : trailing de `TRAILING_STOP_USD`$ (défaut 5$) qui suit le prix
- Le SL ne se déplace que dans la direction favorable

#### P4a — Partial Quick
- **TP** : entry ± 5$
- **Comportement** : fermeture automatique dès +5$ de profit

#### P4b — Partial Trail
- **TP** : aucun (TP lointain dans MT5)
- **SL** : trailing de `PARTIAL_TRAIL_USD`$ (défaut 3$) qui suit le prix

### Identification dans MT5

Le champ `comment` de chaque position contient la méthode :
```
CH1-ZN1-P1     → P1 TP Fixe
CH1-ZN1-P2     → P2 BE Escaladé
CH1-ZN1-P3     → P3 Trailing
CH1-ZN1-P4a    → P4a Partial Quick
CH1-ZN1-P4b    → P4b Partial Trail
```

### Types de signaux

| Type | Description | 5 positions ? |
|---|---|---|
| ZN1 | Prix dans la zone | ✅ |
| ZN2 | Prix entre zone et SL | ✅ |
| PU1 | Prix unique (entre entry et SL) | ✅ |
| PU2 | Prix unique (dans tolérance) | ✅ |
| AL1 | Quick alert prix favorable | ✅ |
| AL2 | Quick alert prix en tolérance | ✅ |
| MP | Quick alert marché (pas de prix) | ✅ |

---

## 2. SL Plafonné (MAX_SL_USD)

### Principe

Le SL est plafonné à une distance maximale en dollars depuis l'entrée.
Pour XAUUSD 0.01 lot : **1$ de prix = 1$ de P&L**.

### Variable

```
MAX_SL_USD=15.0
```

### Comportement

```
BUY @ 4000, signal SL = 3960 (distance 40$)
MAX_SL_USD = 15$
→ SL capé à 3985 (distance 15$)

BUY @ 4000, signal SL = 3990 (distance 10$)
MAX_SL_USD = 15$
→ SL gardé à 3990 (distance 10$ < 15$)
```

### Où le SL est capé

1. Ouverture ZN (zone)
2. Ouverture PU (prix unique)
3. Ouverture AL-MP (quick alert)
4. Fusion QA → signal complet

---

## 3. Variables .env (v14)

### Nouvelles variables

```bash
# SL plafonné
MAX_SL_USD=15.0             # Distance SL max en $

# Trailing
TRAILING_STOP_USD=5.0       # Trailing pour P3
PARTIAL_TRAIL_USD=3.0       # Trailing pour P4b
```

### Variables existantes (inchangées)

```bash
# TP Fixe (P1)
TP_FIXED_GAIN_USD=10.0      # Gain cible
PNL_TRIGGER_USD=7.0         # Seuil BE
BE_USD=3                    # Marge sécurité SL

# Quick Alert
QUICK_ALERT_SL_OFFSET=10.0  # Offset SL par défaut
RR_RATIO_DEFAULT=1.5        # Ratio risk/reward
```

---

## 4. Parser V14 — Améliorations

### 4.1 Fallback XAUUSD

Si aucun symbole (GOLD, XAUUSD, XAU...) n'est détecté dans le signal, le parser utilise XAUUSD par défaut.

**Avant** : `SELL ENTRY 4082` → échec (pas de symbole)
**Après** : `SELL ENTRY 4082` → XAUUSD SELL @4082

### 4.2 Double zone annulée

Si le texte contient plusieurs paires de nombres sur la même ligne (hors TPs/SL), le signal est annulé comme ambigu.

**Exemple** : `Gold 4088 to 4063 ... SELL 4087 4088` → annulé (2 zones détectées)

### 4.3 Zone après BUY/SELL

Les signaux `BUY NOW 4030-4025` ou `BUY 4030 4025` sont maintenant détectés comme zone [4025, 4030].

`NOW` est ignoré entre BUY/SELL et les prix.

### 4.4 @ dans les TPs ignoré

`TP1 @ 4076` n'est plus matché comme entrée. Le `@` après TP/TARGET est ignoré.

### 4.5 Décimaux préservés

`4074.00` reste `4074.00` (pas transformé en `407400`). Les points parasites (`4025...`) sont supprimés sans casser les décimaux.

---

## 5. Fusion QA → Signal Complet

### Flux

```
Phase 1: GOLD BUY NOW
  → Entry = 4000 (prix marché)
  → SL = 3990 (10$)
  → TP = 4015 (15$)
  → 5 positions ouvertes (P1-P4b)

Phase 2: GOLD BUY 4000 SL 3090 TP 4030 (signal complet)
  → SL = 3090 → distance 910$ >> MAX_SL_USD(15$)
  → SL capé à 3985
  → TP = 4030
  → 5 positions mises à jour
```

Le SL est plafonné **aussi** lors de la fusion.

---

## 6. Fichiers du projet

| Fichier | Description |
|---|---|
| `telegram_listener_v14.py` | Cœur du bot (multi-positions + SL capé) |
| `signal_parser_v14.py` | Parser amélioré |
| `signal_parser.py` | Copie de v14 (importé par le bot) |
| `bot_messages.py` | Templates de logs/alertes |
| `13.env` | Configuration |
| `CONTEXT.md` | Historique des modifications |

---

## 7. Architecture

```
Telegram (52 canaux)
    ↓
Signal Parser V14
    ↓ (fallback XAUUSD, double zone check, SL capé)
MT5 Bridge
    ↓
5 positions MARKET simultanées
    ├─ P1: TP Fixe (BE classique)
    ├─ P2: BE Escaladé
    ├─ P3: Trailing Stop
    ├─ P4a: Partial Quick (+5$)
    └─ P4b: Partial Trail (3$)
    ↓
TradeManager._check_all()
    ↓ (dispatch par rôle)
Gestion individuelle par méthode
```
