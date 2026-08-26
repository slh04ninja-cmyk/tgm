# AI Agent Prompt — CopyTrading Bot + Android App

## Overview

Ce document decrit l'architecture du projet CopyTrading pour un agent AI qui doit travailler sur ce codebase.

### Le Bot (tgm)
Bot Telegram de copy trading qui lit les signaux de 108 canaux Telegram et execute les ordres sur MetaTrader 5 (Exness). Le bot tourne sur un serveur Windows VPS.

### L'App Android (CopyTrading)
Application Kotlin qui se connecte a l'API REST du bot (bot_api.py) pour afficher le dashboard, les positions, les performances et les logs en temps reel.

---

## Connexion au Serveur Windows

- **IP** : `38.247.138.124`
- **Port API** : `8000`
- **User** : `Administrator`
- **Hostname** : `vps-mt5`
- **Dossier bot** : `C:\TradingBot\`
- **Pas de SSH** — acces uniquement via l'API REST ou RDP

### Endpoints API principaux

| Endpoint | Methode | Description |
|---|---|---|
| `/api/status` | GET | Etat du bot + MT5 |
| `/api/dashboard` | GET | P&L quotidien, balance, equity |
| `/api/positions` | GET | Positions ouvertes |
| `/api/trades` | GET | Historique deals (params: days, from_date, to_date) |
| `/api/config` | GET/PUT | Variables .env |
| `/api/config/raw` | GET/PUT | Contenu brut du .env |
| `/api/logs` | GET | Logs du bot |
| `/api/file` | POST | Upload un fichier sur le serveur |
| `/api/file/read` | GET | Lire un fichier du serveur |
| `/api/exec` | POST | Executer une commande shell |
| `/api/bot/start` | POST | Demarrer le bot (telegram listener) |
| `/api/bot/stop` | POST | Arreter le bot |
| `/api/restart` | POST | Redemarrer uvicorn (recharge bot_api.py) |
| `/api/positions/close-all` | POST | Fermer toutes les positions |

**Authentification** : Header `Authorization: Bearer <API_TOKEN>`

### Upload + Restart workflow

```bash
# 1. Upload le fichier
curl -X POST -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' \
  -d '{"path": "bot_api.py", "content": "..."}' http://38.247.138.124:8000/api/file

# 2. Redemarrer uvicorn
curl -X POST -H 'Authorization: Bearer <TOKEN>' http://38.247.138.124:8000/api/restart
```

---

## Repos GitHub

### tgm (Bot principal)
- **Repo** : `slh04ninja-cmyk/tgm` (prive)
- **Branche** : `main`
- **Fichiers cles** :
  - `telegram_listener_v17_1.py` — bot principal (~4200 lignes)
  - `signal_parser_v15.py` — parser de signaux
  - `bot_messages_v15.py` — messages/alertes Telegram
  - `bot_api.py` — API REST FastAPI (uvicorn)
- **Dossier local** : `/data/data/com.termux/files/home/tgm/`

### CopyTrading (App Android)
- **Repo** : `slh04ninja-cmyk/CopyTrading` (prive)
- **Branche** : `main`
- **Build** : GitHub Actions → APK
- **Dossier local** : `/data/data/com.termux/files/home/CopyTrading/`
- **Structure** :
  - `app/src/main/java/com/copytrading/` — code Kotlin
  - `app/src/main/res/layout/` — layouts XML
  - `.github/workflows/` — CI build APK

### CT (Copie isolee)
- **Repo** : `slh04ninja-cmyk/CT` (prive)
- **Usage** : test isole pour install.bat + wizard
- **App renommee** : CopyTrading2
- **Dossier serveur** : `C:\TradingBot2\`

### Token de push
- **Compte** : `slh04ninja-cmyk`
- **Token** : utilise pour `git push` — stocke dans la config git locale

---

## Architecture du Bot

### Fichiers du bot (4 fichiers)

1. **telegram_listener_v17_1.py** (~4200 lignes)
   - Boucle principale async (Telethon)
   - Reception des signaux depuis 108 canaux Telegram
   - Parsing → decision (accepter/refuser/fusionner)
   - Execution des ordres MK/L1/L2 sur MT5
   - Deleted message tracker (1 min interval)
   - Rapport quotidien PDF + XLSX
   - Logs unifies : `== CH{num}-{mode} | {action} | PE={entry} | PA={current} ==`

2. **signal_parser_v15.py**
   - Classe `SignalParser` — parse les messages texte en signaux structures
   - Log level DEBUG (silencieux)

3. **bot_messages_v15.py**
   - Fonctions de logging : `log_signal_detected()`, `log_refuse()`, etc.
   - Format unifie sans accents

4. **bot_api.py**
   - Serveur FastAPI (uvicorn, port 8000)
   - Endpoints REST pour l'app Android
   - Gestion MT5 (connexion, ordres, historique)
   - Endpoint `/api/restart` pour recharger le code

### Variables d'environnement (.env)

| Variable | Description | Defaut |
|---|---|---|
| `TRADING_START_HOUR` | Heure debut trading UTC | 3 |
| `TRADING_END_HOUR` | Heure fin trading UTC | 20 |
| `MAX_SL_USD` | Stop loss max en USD | 10.0 |
| `LIMIT_OFFSET_1` | Offset L1 en USD | 3.0 |
| `LIMIT_OFFSET_2` | Offset L2 en USD | 6.0 |
| `DAILY_PROFIT_LIMIT` | Limite P&L quotidien | 200.0 |
| `API_TOKEN` | Token auth API | (generer) |

### Format des commentaires MT5

`CH{canal}-{signal}-{ordre}`

- **Canal** : CH5, CH3, CH60, etc.
- **Signal** : ZN (Zone), PU (Purge), MP (Momentum), QA (Quick Alert), AL (Alert)
- **Ordre** : MK (Market), L1 (Limit 1), L2 (Limit 2)

Exemple : `CH5-ZN-MK` = Canal 5, signal Zone, ordre Market

### Channels.txt

Format : `Canal_N : -100XXXXXXXXXX # NomDuCanal`
- 108 canaux configures
- `CHANNEL_NUM_MAP` : mapping canal → numero

---

## Architecture de l'App Android

### Stack
- **Langage** : Kotlin
- **UI** : XML layouts, Material3
- **HTTP** : OkHttp + Gson
- **Build** : GitHub Actions → APK

### Fichiers principaux

- `MainActivity.kt` — activite principale, dashboard, performance, positions, config, logs
- `ApiClient.kt` — client HTTP pour l'API du bot
- `ApiModels.kt` — data classes (Trade, Position, Dashboard, etc.)
- `DateRangePickerDialog.kt` — picker de dates custom
- `activity_main.xml` — layout principal

### Panels de l'app

1. **Dashboard (Overview)** — P&L quotidien, floating, balance, equity, winrate
2. **Performance** — 3 tableaux :
   - Par Canal (CH) — avec expandable detail (PF, RR, MD)
   - Par Signal (ZN, PU, MP, QA, AL)
   - Par Session (heure UTC 00h-23h)
3. **Positions** — positions ouvertes avec badges CH/signal/ordre
4. **Config** — edition des variables .env
5. **Logs** — affichage des logs du bot

### Design
- Dark theme : `--bg-primary: #0F0F1A`, `--bg-secondary: #1A1A2E`
- Accent : `#6C63FF`
- Font : Inter
- Material Icons Round
- Pas d'emojis dans l'UI

---

## Contraintes

- **Pas de SSH** sur le serveur Windows
- **Pas de adb** — travail sur telephone Android
- **Sous-agents** casses (DaemonThreadPoolExecutor) — modifications manuelles
- **Variables .env** non reloaded dynamiquement — redemarrage necessaire
- **P&L quotidien** reset a 3h UTC (TRADING_START_HOUR)
- **Dashboard** reset a zero hors plage trading (20h-3h UTC)
- **Sous-agents max 3** en parallele
- **Reponses courtes** — minimiser le temps de reponse
- **Francais** — langue principale
