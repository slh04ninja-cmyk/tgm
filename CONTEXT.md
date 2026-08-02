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

### Fichiers modifiés
- `telegram_listener_v14.py`

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

### Exemple

```
BUY @ 4000, signal SL = 3960 (40$ de distance)
MAX_SL_USD = 15$
→ SL capé à 3985 (15$ de distance)
```

### Où le SL est capé

Le `_cap_sl()` est appelé dans **4 endroits** :
1. Ouverture d'une position zone (ZN1/ZN2)
2. Ouverture d'une position prix unique (PU1/PU2)
3. Ouverture d'une quick alert (AL-MP)
4. Fusion d'un QA avec un signal complet (`merge_quick_alert`)

### Variable .env

```
MAX_SL_USD=15.0
```

### Fichiers modifiés
- `telegram_listener_v14.py`

---

## 3. NOUVEAU : Variables de trailing

### Variables .env

```
TRAILING_STOP_USD=5.0       # Trailing stop pour P3 (distance en $)
PARTIAL_TRAIL_USD=3.0       # Trailing stop pour P4b (distance en $)
```

### Logique trailing (P3 et P4b)

```python
# Pour BUY :
new_sl = prix_actuel - TRAILING_STOP_USD
# Le SL ne descend jamais, il monte seulement

# Pour SELL :
new_sl = prix_actuel + TRAILING_STOP_USD
# Le SL ne monte jamais, il descend seulement
```

Le SL trailing ne se déplace que dans la direction favorable au trade.

---

## 4. FIX PARSER : Fallback XAUUSD par défaut

### Problème
Les signaux sans symbole explicite (ex: `SELL ENTRY 4082`, `GOLD BUY NOW`) échouaient car `_extract_symbol()` retournait `None`.

### Solution
Si aucun symbole n'est détecté, le parser utilise `XAUUSD` par défaut :
```python
if not symbol:
    symbol = "XAUUSD"
```

### Impact
Taux de parsing : 92.9% → 100% (sur 608 signaux de test)

### Fichiers modifiés
- `signal_parser_v14.py`

---

## 5. FIX PARSER : Détection des signaux à double zones

### Problème
Les signaux contenant un résultat de trade précédent + le signal actuel (ex: `Gold 4088 to 4063 ... SELL 4087 4088`) étaient mal parsés — le prix du résultat était pris comme zone.

### Solution
Fonction `_count_zone_candidates()` qui compte les paires de nombres sur la **même ligne** du texte brut (hors TPs et SL). Si > 1 paire → signal annulé comme ambigu.

### Logique
```python
# Pour chaque ligne du texte brut :
# 1. Nettoyer les séparateurs (to, and, between...)
# 2. Trouver les paires de nombres consécutifs
# 3. Exclure les TPs et SL
# 4. Si > 1 paire → signal ambigu → annulé
```

### Fichiers modifiés
- `signal_parser_v14.py`

---

## 6. FIX PARSER : Zone détectée après BUY/SELL

### Problème
Les signaux comme `GOLD BUY NOW : 4030-4025` ou `BUY 4030 4025` étaient parsés comme prix unique au lieu de zone.

### Solution
- Règle 10 modifiée : détecte `BUY <prix1> <prix2>` ou `BUY <prix1>-<prix2>` comme zone
- Règle 11 modifiée : vérifie les 2 premiers nombres après BUY/SELL, zone si 2 nombres consécutifs
- `NOW` ignoré entre BUY/SELL et les prix (dans les règles 10 et 11)

### Fichiers modifiés
- `signal_parser_v14.py`

---

## 7. FIX PARSER : @ dans les TPs ignoré

### Problème
`TP1 @ 4076` était matché par la règle 4 (`@` comme mot-clé d'entrée) → le prix du TP était pris comme entrée.

### Solution
- `@` retiré de la liste `entry_keywords` (règle 4)
- Règle 4b ajoutée : `@` comme mot-clé d'entrée, mais avec vérification que le `@` n'est pas précédé de `TP` ou `TARGET`

### Fichiers modifiés
- `signal_parser_v14.py`

---

## 8. FIX PARSER : Décimaux préservés

### Problème
`4074.00` était transformé en `407400` (le point décimal supprimé).

### Solution
Règle 10c modifiée : ne supprime que les points **non suivis de chiffres** (points parasites comme `4025...`), pas les décimaux comme `4074.00`.
```python
text = re.sub(r'(\d{4,6})\.{1,}(?!\d)', r'\1', text)
```

### Fichiers modifiés
- `signal_parser_v14.py`

---

## 9. FIX PARSER : Séparateurs de zone dans détection double zone

### Problème
`Gold 4088 to 4063` — le `to` entre les nombres empêchait la détection de la paire comme zone.

### Solution
Dans `_count_zone_candidates()`, les séparateurs de zone (`to`, `and`, `between`, etc.) sont nettoyés ligne par ligne avant la détection.

### Fichiers modifiés
- `signal_parser_v14.py`

---

## Résumé des modifications v14

| Catégorie | Fichier | Description |
|---|---|---|
| Multi-positions | `telegram_listener_v14.py` | 5 positions par signal (P1-P4b) |
| SL plafonné | `telegram_listener_v14.py` | `_cap_sl()` avec `MAX_SL_USD` |
| Trailing | `telegram_listener_v14.py` | `_manage_trailing()`, `_manage_partial_trail()` |
| BE escaladé | `telegram_listener_v14.py` | `_manage_be_scale()` avec niveaux |
| Parser fallback | `signal_parser_v14.py` | XAUUSD par défaut si pas de symbole |
| Parser double zone | `signal_parser_v14.py` | `_count_zone_candidates()` |
| Parser BUY/SELL zone | `signal_parser_v14.py` | Règles 10/11 modifiées |
| Parser @ dans TP | `signal_parser_v14.py` | `@` ignoré après TP/TARGET |
| Parser décimaux | `signal_parser_v14.py` | Points parasites vs décimaux |

---

## Fichiers du projet (v14)

| Fichier | Lignes | Description |
|---|---|---|
| `telegram_listener_v14.py` | ~2650 | Cœur du bot (multi-positions + SL capé) |
| `telegram_listener_v13.py` | ~2100 | Version précédente (conservée) |
| `signal_parser_v14.py` | ~880 | Parser amélioré |
| `signal_parser_v13.py` | ~830 | Version précédente (conservée) |
| `signal_parser.py` | ~880 | Copie de v14 (importé par le bot) |
| `bot_messages.py` | 171 | Templates de logs/alertes |
| `13.env` | — | Configuration |
| `CONTEXT.md` | — | Ce fichier |

---

## Variables .env (v14)

### Nouvelles variables

| Variable | Défaut | Description |
|---|---|---|
| `MAX_SL_USD` | 15.0 | Distance SL maximale en $ (plafonne le SL du signal) |
| `TRAILING_STOP_USD` | 5.0 | Distance trailing stop pour P3 |
| `PARTIAL_TRAIL_USD` | 3.0 | Distance trailing stop pour P4b |

### Variables existantes (inchangées)

| Variable | Défaut | Description |
|---|---|---|
| `TP_FIXED_GAIN_USD` | 10.0 | Gain cible pour P1 (TP Fixe) |
| `PNL_TRIGGER_USD` | 7.0 | Seuil BE pour P1 |
| `BE_USD` | 3.0 | Marge de sécurité SL pour P1 |
| `QUICK_ALERT_SL_OFFSET` | 10.0 | Offset SL par défaut pour les quick alerts |
| `RR_RATIO_DEFAULT` | 1.5 | Ratio risk/reward pour TP auto |

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
```

### Flux d'un signal v14

1. Réception Telegram → dédup (message_id + contenu)
2. Filtres : spam → horaire → news → P&L quotidien → conflit
3. Parsing V14 : direction, zone/prix, SL, TP(s)
   - Fallback XAUUSD si pas de symbole
   - Double zone check (annulation si ambigu)
   - SL plafonné par MAX_SL_USD
4. Validation TimesFM (optionnel)
5. Exécution : 5 positions MARKET simultanées
6. Gestion post-trade : dispatch par rôle (P1-P4b)

---

## Signaux testés et validés (v14)

| Signal | Parsing | Résultat |
|---|---|---|
| `𝗚𝗢𝗟𝗗 BUY 𝗘𝗻𝘁𝗿𝘆: 4032-4026 ✔4036 ... 🛑𝗦𝗟 4008` | ✅ zone [4026, 4032], 9 TPs orphelins | ✅ |
| `GOLD BUY NOW : 4030-4025 SL:4020 TP:4040` | ✅ zone [4025, 4030] | ✅ |
| `GOLD SELL NOW @ 4095 - 4100 SL 4105 TP 4085` | ✅ zone [4095, 4100] | ✅ |
| `**Gold Buy Now 4074.00 - 4069.00** SL:4064 TP:4082` | ✅ zone [4069, 4074] | ✅ |
| `SELL NOW: 4079-4080 TP1@4076 ... TP8@4051 SL:4090` | ✅ zone [4079, 4080], 8 TPs | ✅ |
| `Gold 4088 to 4063 ... SELL 4087.4088 TP SL` | ❌ Annulé (double zone) | ✅ Comportement attendu |
| `SELL ENTRY 4082` (pas de symbole) | ✅ XAUUSD SELL @4082 (fallback) | ✅ |
| `G9LD BUY 4050` (typo) | ✅ XAUUSD BUY @4050 (fallback) | ✅ |

---

## Token GitHub

Le token fonctionne pour les pushes. Utiliser `git remote set-url origin https://<TOKEN>@github.com/slh04ninja-cmyk/tgm.git` puis `git push origin main`.
