# CONTEXT.md — Modifications du Bot

## Version : V10.1.1 — Alert Timeout + News Filter

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

---

## 2. FIX #8 : News Filter — Impact configurable + Warning unique

### Problème
Le filtre news ne matchait que `impact == "high"`. L'API FF Calendar retourne les événements USD avec impact "Low" ou "Medium" certaines semaines → 0 news filtrées sur 69 events reçus. Le warning se répétait toutes les 30 min.

### Correction
- Nouveau paramètre `NEWS_MIN_IMPACT` dans `.env` (défaut: `high`)
- Quand `NEWS_MIN_IMPACT=high`, les événements "Medium" sont aussi inclus
- Warning `0 news filtrées` loggé une seule fois, puis downgrade en debug
- Banner de démarrage affiche le niveau d'impact minimum

---

## Version : V10.1.0 — Market Price + Merge Price + Fusion Tolérance

### Date : 2026-07-18

(Voir historique précédent)
