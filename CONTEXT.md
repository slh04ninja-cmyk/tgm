# Version : V11.1.1 — Bug Fixes

### Date : 2026-07-28

---

## 1. FIX : Messages Telegram dupliqués

### Problème
Les alertes de rapport trading (ouverture, clôture) étaient envoyées en double.

### Cause
Le mécanisme de dédoublonnage utilisait `hash(contenu + canal)` mais pas le `message_id` Telegram. En cas de reconnexion Telethon ou de livraison via canal + discussion group liée, le même message pouvait être traité deux fois.

### Solution
- Ajout de `_seen_msg_ids` basé sur `event.message_id` (fiable côté Telegram)
- Double vérification : message_id + hash(contenu+canal)
- TTL augmenté de 60s → 120s

### Fichiers modifiés
- `telegram_listener_v11.py`

---

## 2. FIX : Fusion — SL/TP mis à jour au lieu d'annuler

### Problème
Quand un signal complet arrivait et que le prix du QA existant dépassait ±3$ (FUSION_TOLERANCE), le bot :
1. Fermait la position QA existante (avec perte potentielle)
2. Exécutait le signal complet comme nouvelle position

Résultat : perte inutile + double position.

### Solution
Au lieu de fermer et ré-exécuter :
- Si QA actif et hors tolérance → mettre à jour SL/TP avec ceux du signal complet
- Si QA déjà fermé → ignorer le signal complet
- Aucune nouvelle position n'est créée

### Paramètres
Aucun changement. `FUSION_TOLERANCE` reste pour la détection de distance.

### Fichiers modifiés
- `telegram_listener_v11.py`

---

## 3. FIX : Signaux Zone — Suppression du midian

### Problème
Les signaux zone (BUY 4000 4010) étaient convertis en prix unique via le midian (4005). Cela pouvait :
- Placer un ordre à un prix qui n'est dans aucune zone valide
- Perdre l'information de la zone réelle

### Solution
Suppression du calcul du midian. Logique directe :
- **ZN1** : prix dans la zone [zone_low, zone_high] → MARKET
- **ZN2** : prix entre la zone et SL (meilleur prix) → MARKET
- Sinon → annulé

### Exemple
Signal : BUY 4000 4010, SL 3990
- Prix 4005 (dans zone) → MARKET ZN1 ✅
- Prix 3995 (entre SL et zone) → MARKET ZN2 ✅
- Prix 4020 (hors zone) → Annulé ❌

### Paramètres
Aucun changement. `ZN_PRICE_TOLERANCE` n'est plus utilisé pour les zones.

### Fichiers modifiés
- `telegram_listener_v11.py`

---

## 4. FIX : QA avec prix — Tolérance directionnelle

### Problème
Pour un signal BUY 4000, si le prix baissait à 3990 (meilleur prix), le bot annulait car `|3990-4000| = 10 > 3.0`. C'est absurde — un meilleur prix devrait être accepté.

### Solution
La tolérance ne s'applique que dans le sens *défavorable* :
- **BUY** : prix ≤ entry → MARKET (meilleur ou égal, pas de limite de distance)
- **BUY** : prix > entry + 3$ → Annulé (prix monté contre le signal)
- **SELL** : prix ≥ entry → MARKET (meilleur ou égal)
- **SELL** : prix < entry - 3$ → Annulé (prix baissé contre le signal)

### Exemples
| Signal | Prix actuel | Résultat | Raison |
|---|---|---|---|
| BUY 4000 | 3990 | ✅ MARKET | Prix favorable (moins cher) |
| BUY 4000 | 4000 | ✅ MARKET | Prix exact |
| BUY 4000 | 4002 | ✅ MARKET | Dans tolérance |
| BUY 4000 | 4004 | ❌ Annulé | > 3$ défavorable |
| SELL 4000 | 4010 | ✅ MARKET | Prix favorable (plus cher) |
| SELL 4000 | 3997 | ❌ Annulé | > 3$ défavorable |

### Paramètres
Aucun changement. `QA_PRICE_TOLERANCE=3.0` reste le seuil.

### Fichiers modifiés
- `telegram_listener_v11.py`

---

## Résumé des modifications

| Bug | Fichier | Lignes modifiées |
|---|---|---|
| #1 Duplication | `telegram_listener_v11.py` | Handler dédoublonnage |
| #2 Fusion | `telegram_listener_v11.py` | Section fusion (au lieu de close+re-execute) |
| #3 Zone | `telegram_listener_v11.py` | Bloc zone signals (suppression midian) |
| #4 QA tolérance | `telegram_listener_v11.py` | Vérification tolérance directionnelle |
