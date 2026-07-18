# CONTEXT.md — Modifications du Bot

## Version : V10.1.0 — Market Price + Merge Price + Fusion Tolérance

### Date : 2026-07-18

---

## 1. Market Price — Signal sans prix

### Description
Permet d'exécuter un ordre MARKET immédiat quand le signal ne contient **aucun prix**, uniquement une direction et un mot-clé d'exécution.

### Exemples de signaux supportés
- `GOLD BUY NOW`
- `BUY XAUUSD NOW`
- `SELL GOLD MARKET`
- `BUY BTCUSD IMMEDIATELY`
- `SELL EURUSD MKT`
- `BUY XAGUSD IMMEDIATE`

### Mots-clés détectés
`NOW`, `MARKET`, `MKT`, `IMMEDIATELY`, `IMMEDIATE`, `INSTANT`, `OPEN`

### Règle
Le signal ne doit contenir **aucun prix** (nombre à 4-6 chiffres). Si un prix est présent, il est traité comme un Quick Alert classique.

### Fichiers modifiés

#### `signal_parser.py`
- Nouveau champ `is_market_price: bool = False` dans `TradeSignal` + `to_dict()`
- Nouvelle méthode `_is_market_now()` — détecte les mots-clés marché sans prix
- Bloc dans `_parse_trade()` — crée un signal avec `entry=None`, SL/TP en offsets relatifs

#### `telegram_listener_v10.py`
- `execute_quick_alert()` — résout les offsets relatifs en prix absolus (`entry=current`)
- Commentaire MT5 : `CH{num}-MP-MKT`
- Label alerte : `QA-MP`

### Message Telegram
```
⚡ BUY XAUUSD | QA-MP
━━━━━━━━━━━━━━━━━━
MP : @3883.0 | Lot: 0.01
Ticket : #400
TP: 3898.0 | SL: 3873.0
Canal: @fxGzl
```

---

## 2. Merge Price — Prix d'edge (BUY MORE / SELL MORE)

### Description
Le signal complet peut contenir un **prix de merge** supplémentaire via les mots-clés `BUY MORE`, `SELL MORE` ou `ADD MORE`. Ce prix représente l'**edge** où un ordre LIMIT supplémentaire sera placé.

### Exemple de signal
```
XAUUSD BUY 3885       ← prix principal (fusion ±FUSION_TOLERANCE)
BUY MORE 3880         ← merge_price (edge = LIMIT order)
TP 3890 / 3895 / 3900 / 3905
SL 3860
```

### Fichiers modifiés

#### `signal_parser.py`
- Nouveau champ `merge_price: Optional[float]` dans `TradeSignal` + `to_dict()`
- Nouvelle fonction `_extract_merge_price()` — détecte `BUY MORE {prix}` / `SELL MORE {prix}` / `ADD MORE {prix}`
- Intégrée dans `_parse_trade()`

#### `telegram_listener_v10.py`
- `_place_merge_limit()` — si `merge_price` existe, l'utiliser comme prix LIMIT au lieu de `zone_low`

---

## 3. Fusion Tolérance configurable

### Description
La tolérance de fusion entre un Quick Alert et un signal complet est maintenant configurable via `FUSION_TOLERANCE` dans `.env`.

### Configuration
```
FUSION_TOLERANCE=3    # points (défaut: 3)
```

### Logique
```
zone_low - FUSION_TOLERANCE <= qa_price <= zone_high + FUSION_TOLERANCE
```

### Fichiers modifiés

#### `.env`
- `FUSION_TOLERANCE=3`

#### `telegram_listener_v10.py`
- Variable globale `FUSION_TOLERANCE` chargée depuis `.env`
- Check fusion utilise `FUSION_TOLERANCE` au lieu de `2` (hardcodé)

---

## 4. Annulation QA (Market Price) si fusion échouée

### Description
Si le QA Market Price est **hors tolérance** de fusion, le bot :
1. Annule la position QA
2. Exécute le signal complet normalement (CAS1, CAS2 a/b)

### Message Telegram
```
⚠️ FUSION ÉCHOUÉE | BUY
━━━━━━━━━━━━━━━━━━
QA annulé (hors ±3)
P&L : -5.40 $
Signal complet exécuté
Canal: @fxGzl
```

### Fichiers modifiés
#### `telegram_listener_v10.py`
- `is_market_price` stocké dans le dict du QA
- Logique d'annulation dans le handler principal
- P&L calculé avant fermeture de la position

---

## 5. Alertes SL/TP touché avant fusion

### Description
Si le SL ou TP provisoire du QA est touché **avant** l'arrivée du signal de fusion, le bot envoie une alerte Telegram.

### Messages Telegram

**SL touché**
```
❌ QA SL TOUCHÉ | BUY
━━━━━━━━━━━━━━━━━━
QA : #400
P&L : -5.40 $
Signal complet ignoré
Canal: @fxGzl
```

**TP touché**
```
✅ QA TP TOUCHÉ | BUY
━━━━━━━━━━━━━━━━━━
QA : #400
P&L : +8.20 $
Signal complet ignoré
Canal: @fxGzl
```

### Fichiers modifiés
#### `telegram_listener_v10.py`
- Alertes ajoutées dans `merge_quick_alert()` pour les cas SL/TP touché (QA-LMT et QA-MKT/MP)
- P&L calculé depuis `history_deals_get()`

---

## 6. Surveillance SL/TP provisoire sur QA-LMT

### Description
Pour les Quick Alert LIMIT (QA-LMT), le bot surveille le prix en continu dans `_check_all()`. Si le prix touche le SL ou TP provisoire, l'ordre pending est **annulé** et retiré de `_quick_alerts`.

Si un signal de fusion arrive ensuite, il est traité comme un **signal normal avec zone** (CAS1, CAS2 a/b).

### Messages Telegram

**SL provisoire touché**
```
❌ QA-LMT SL TOUCHÉ | BUY
━━━━━━━━━━━━━━━━━━
QA-LMT : #400 annulé
SL provisoire : @3873
Prix actuel : @3872.5
Canal: @fxGzl
```

**TP provisoire touché**
```
✅ QA-LMT TP TOUCHÉ | BUY
━━━━━━━━━━━━━━━━━━
QA-LMT : #400 annulé
TP provisoire : @3898
Prix actuel : @3898.5
Canal: @fxGzl
```

### Fichiers modifiés
#### `telegram_listener_v10.py`
- `TradeManager.__init__()` accepte `quick_alerts_ref`
- `_check_all()` — surveillance SL/TP provisoire sur pending orders
- Annulation du pending + retrait de `_quick_alerts` + alerte Telegram

---

## 7. Messages Telegram — Fusion réussie

### Description
Trois cas de fusion réussie avec alertes Telegram distinctes.

### Messages

**PO-OV (Position Ouverte)**
```
✅ FUSION RÉUSSIE | PO-OV
━━━━━━━━━━━━━━━━━━
QA : #400
MERGE LMT: @3880
Ticket : #401
SL: 3860 | TPf: 3905
Canal: @fxGzl
```

**LMT-RP (Limit Rempli)**
```
✅ FUSION RÉUSSIE | LMT-RP
━━━━━━━━━━━━━━━━━━
QA : #400
MERGE REMPLI : @3880
Ticket : #401
SL: 3860 | TPf: 3905
Canal: @fxGzl
```

**LMT-PDN (Limit Pending)**
```
✅ FUSION RÉUSSIE | LMT-PDN
━━━━━━━━━━━━━━━━━━
QA-LMT : #400
MERGE LMT: @3880
Ticket : #401
SL: 3860 | TPf: 3905
Canal: @fxGzl
```

---

## 8. Labels MT5 et alertes

### Commentaires MT5
| Commentaire | Signification |
|-------------|---------------|
| `CH{num}-MP-MKT` | Market Price (ordre marché sans prix) |
| `CH{num}-MP-LMT` | Market Price LIMIT (inatteignable en pratique) |
| `CH{num}-AL-MKT` | Quick Alert MARKET (avec prix) |
| `CH{num}-AL-LMT` | Quick Alert LIMIT (avec prix) |
| `CH{num}-MG` | Merge limit (fusion) |

### Labels Telegram
| Label | Signification |
|-------|---------------|
| `QA-MP` | Market Price exécuté |
| `QA-MKT` | Quick Alert MARKET exécuté |
| `QA-LMT` | Quick Alert LIMIT placé |
| `PO-OV` | Fusion réussie — position ouverte |
| `LMT-RP` | Fusion réussie — limit rempli |
| `LMT-PDN` | Fusion réussie — pending modifié |

---

## 9. Résumé des fichiers modifiés

| Fichier | Lignes | Contenu |
|---------|--------|---------|
| `.env` | +1 | `FUSION_TOLERANCE=3` |
| `signal_parser.py` | +61 | `is_market_price`, `merge_price`, `_is_market_now()`, `_extract_merge_price()` |
| `telegram_listener_v10.py` | +213/-18 | Market Price, FUSION_TOLERANCE, merge_price, annulation QA, SL/TP provisoire, alertes P&L |
| `bot_documentation_v9-1.html` | +60 | Sections Market Price, Merge Price, config |

**Total : 317 lignes ajoutées, 18 supprimées**

---

## 10. Points d'attention

1. **`CH{num}-MP-LMT`** : code présent mais jamais atteint (entry=current → toujours MARKET zone). Peut être nettoyé.
2. **`BUY MORE` sans symbole** : le parser ne résout pas le symbole si le message n'en contient pas. Le symbole doit être présent dans le texte.
3. **Tolérance asymétrique** : le code utilise `±FUSION_TOLERANCE` (symétrique). Si une asymétrie est souhaitée, ajouter `FUSION_TOLERANCE_UP` / `FUSION_TOLERANCE_DOWN`.
