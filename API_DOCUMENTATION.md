# CopyTrading Bot API — Documentation Complete

## Connexion

- **URL** : `http://38.247.138.124:8000`
- **Authentification** : Header `Authorization: Bearer <API_TOKEN>`
- **Format** : JSON (`Content-Type: application/json`)

## Endpoints

### 1. Status du serveur

```bash
GET /api/status
```

Retourne l'etat du bot et du compte MT5.

**Reponse :**
```json
{
  "bot": {
    "status": "running",
    "pid": 12224,
    "uptime_seconds": 267,
    "last_error": ""
  },
  "mt5": {
    "connected": true,
    "account": {
      "login": 262342460,
      "server": "Exness-MT5Trial16",
      "balance": 1093.97,
      "equity": 1094.38,
      "margin": 27.93,
      "free_margin": 1066.45,
      "profit": 0.41,
      "currency": "USD",
      "leverage": 500
    }
  },
  "server_time": "2026-08-24T12:17:37Z"
}
```

---

### 2. Dashboard (resume trading)

```bash
GET /api/dashboard
```

Retourne les statistiques du jour en cours.

**Reponse :**
```json
{
  "daily_pnl": 15.50,
  "floating_pnl": 1.73,
  "total_pnl": 17.23,
  "balance": 1093.97,
  "equity": 1094.38,
  "trades": 8,
  "wins": 5,
  "losses": 3,
  "winrate": 62.5,
  "open_positions": [...],
  "open_count": 3,
  "daily_limit": false,
  "limit_pct": 45.2,
  "trading_hours": true,
  "timestamp": "2026-08-24T12:00:00Z"
}
```

---

### 3. Positions ouvertes

```bash
GET /api/positions
```

Retourne toutes les positions ouvertes dans MT5 (bot + manuelles).

**Reponse :**
```json
{
  "positions": [
    {
      "ticket": 1746486354,
      "symbol": "XAUUSDm",
      "type": "BUY",
      "volume": 0.01,
      "open_price": 4658.63,
      "current_price": 4658.18,
      "sl": 4646.89,
      "tp": 4660.47,
      "profit": -0.45,
      "swap": 0.0,
      "comment": "CH3-MP-MK",
      "magic": 20250226,
      "bot_opened": true,
      "time": "2026-08-24T11:45:18Z"
    }
  ],
  "count": 3
}
```

---

### 4. Historique des trades

```bash
GET /api/trades?days=7
GET /api/trades?from_date=2026-08-01&to_date=2026-08-24
```

Retourne l'historique des deals MT5.

**Parametres :**
- `days` (int, defaut=7) : nombre de jours a remonter
- `from_date` (str, optionnel) : date debut format YYYY-MM-DD
- `to_date` (str, optionnel) : date fin format YYYY-MM-DD

**Reponse :**
```json
{
  "trades": [
    {
      "ticket": 123456,
      "symbol": "XAUUSDm",
      "type": "BUY",
      "volume": 0.01,
      "price": 4658.63,
      "profit": 5.20,
      "comment": "CH3-MP-MK",
      "time": "2026-08-24T11:45:18Z",
      "magic": 20250226
    }
  ],
  "count": 684,
  "total_pnl": 150.50
}
```

---

### 5. Demarrer / Arreter le bot

```bash
POST /api/bot/start
POST /api/bot/stop
```

Demarre ou arrete le processus `telegram_listener_v17_1.py`.

**Reponse (start) :**
```json
{"status": "started", "pid": 12224}
```

**Reponse (stop) :**
```json
{"status": "stopped"}
```

---

### 6. Fermer une position

```bash
POST /api/positions/{ticket}/close
```

Ferme une position specifique par son ticket MT5.

**Exemple :**
```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  http://38.247.138.124:8000/api/positions/1746486354/close
```

**Reponse :**
```json
{"status": "closed", "ticket": 1746486354, "profit": 5.20}
```

---

### 7. Fermer toutes les positions

```bash
POST /api/positions/close-all
```

Ferme toutes les positions ouvertes.

**Reponse :**
```json
{
  "closed": [1746486354, 1746486420],
  "failed": [],
  "total": 2
}
```

---

### 8. Configuration (.env)

```bash
GET /api/config
```

Retourne les variables du fichier .env (mots de passe masques).

**Reponse :**
```json
{
  "config": {
    "MAGIC_NUMBER": "20250226",
    "MAX_SL_USD": "10.0",
    "DAILY_PROFIT_LIMIT": "50.0",
    "API_TOKEN": "***"
  },
  "file": "C:\\TradingBot\\.env"
}
```

```bash
PUT /api/config
```

Met a jour des variables .env.

**Body :**
```json
{
  "values": {
    "MAX_SL_USD": "15.0",
    "DAILY_PROFIT_LIMIT": "75.0"
  }
}
```

**Reponse :**
```json
{"status": "ok", "updated": ["MAX_SL_USD", "DAILY_PROFIT_LIMIT"]}
```

---

### 9. Configuration brute (.env raw)

```bash
GET /api/config/raw
```

Retourne le contenu brut du .env.

```bash
PUT /api/config/raw
```

Ecrase completement le .env.

**Body :**
```json
{"content": "MAGIC_NUMBER=20250226\nMAX_SL_USD=10.0\n..."}
```

---

### 10. Logs

```bash
GET /api/logs?lines=100
```

Retourne les dernieres lignes du fichier `bot_trading.log`.

**Parametres :**
- `lines` (int, defaut=100) : nombre de lignes a retourner

**Reponse :**
```json
{
  "logs": ["2026-08-24 12:00:00 [INFO] Bot started...", "..."],
  "total_lines": 5420,
  "returned": 100
}
```

**WebSocket logs en temps reel :**
```
ws://38.247.138.124:8000/ws/logs
```

---

### 11. Executer une commande shell

```bash
POST /api/exec
```

Execute une commande Windows sur le serveur (timeout 30s).

**Body :**
```json
{"command": "dir C:\\TradingBot", "cwd": "C:\\TradingBot"}
```

**Reponse :**
```json
{
  "stdout": " Volume in drive C is Windows...",
  "stderr": "",
  "returncode": 0
}
```

**Exemples :**
```bash
# Lister les fichiers
{"command": "dir C:\\TradingBot"}

# Voir les processus Python
{"command": "tasklist /FI \"IMAGENAME eq python.exe\""}

# Executer un script Python
{"command": "python check_pending.py", "cwd": "C:\\TradingBot"}

# Voir la taille d'un fichier
{"command": "forfiles /P C:\\TradingBot /M *.py /C \"cmd /c echo @fname @fsize\""}
```

---

### 12. Lire un fichier

```bash
GET /api/file?path=telegram_listener_v17_1.py
```

Lit un fichier du serveur (limite au dossier `C:\TradingBot\`).

**Reponse :**
```json
{
  "path": "C:\\TradingBot\\telegram_listener_v17_1.py",
  "content": "#!/usr/bin/env python3\n...",
  "size": 194896
}
```

---

### 13. Ecrire un fichier

```bash
POST /api/file
```

Ecrit/cree un fichier sur le serveur.

**Body :**
```json
{
  "path": "check_pending.py",
  "content": "import MetaTrader5 as mt5\n..."
}
```

**Reponse :**
```json
{"status": "ok", "path": "C:\\TradingBot\\check_pending.py", "size": 549}
```

---

### 14. Lister les fichiers

```bash
GET /api/files?path=
```

Liste les fichiers d'un dossier.

**Reponse :**
```json
{
  "path": "C:\\TradingBot",
  "files": [
    {"name": "telegram_listener_v17_1.py", "is_dir": false, "size": 194896},
    {"name": "bot_api.py", "is_dir": false, "size": 25000},
    {"name": "venv", "is_dir": true, "size": 0}
  ]
}
```

---

## Informations techniques

### Serveur
- **OS** : Windows Server (VPS)
- **IP** : 38.247.138.124
- **API Port** : 8000
- **Dossier bot** : `C:\TradingBot\`
- **Fichier principal** : `telegram_listener_v17_1.py`
- **Logs** : `C:\TradingBot\bot_trading.log`

### MetaTrader 5
- **Compte** : 262342460
- **Serveur** : Exness-MT5Trial16
- **Magic Number** : 20250226
- **Symbole** : XAUUSDm (Gold)

### Format des commentaires MT5
Les positions du bot ont un commentaire au format :
```
CH{numero_canal}-{type_signal}-{type_ordre}
```

Exemples :
- `CH3-MP-MK` : Canal 3, signal MP, ordre Market
- `CH94-ZN-L1` : Canal 94, signal ZN, ordre Limit 1
- `CH45-ZN-L2` : Canal 45, signal ZN, ordre Limit 2

**Types de signaux :** ZN, PU, MP, QA, AL
**Types d'ordres :** MK (Market), L1, L2, L3... (Limit)

### Channels.txt
Fichier de configuration des canaux Telegram :
```
Canal_1 : -100XXXXXXXXXX # NomDuCanal
Canal_2 : -100XXXXXXXXXX # NomDuCanal
```

---

## Exemples curl complets

```bash
# Status
curl -H "Authorization: Bearer TOKEN" http://38.247.138.124:8000/api/status

# Positions
curl -H "Authorization: Bearer TOKEN" http://38.247.138.124:8000/api/positions

# Trades des 30 derniers jours
curl -H "Authorization: Bearer TOKEN" "http://38.247.138.124:8000/api/trades?days=30"

# Logs (50 dernieres lignes)
curl -H "Authorization: Bearer TOKEN" "http://38.247.138.124:8000/api/logs?lines=50"

# Changer MAX_SL_USD a 15
curl -X PUT -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"values": {"MAX_SL_USD": "15.0"}}' \
  http://38.247.138.124:8000/api/config

# Executer une commande
curl -X POST -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"command": "tasklist /FI \"IMAGENAME eq python.exe\""}' \
  http://38.247.138.124:8000/api/exec

# Lire un fichier
curl -H "Authorization: Bearer TOKEN" \
  "http://38.247.138.124:8000/api/file?path=.env"

# Ecrire un fichier
curl -X POST -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"path": "test.py", "content": "print(\"hello\")"}' \
  http://38.247.138.124:8000/api/file

# Fermer position 1746486354
curl -X POST -H "Authorization: Bearer TOKEN" \
  http://38.247.138.124:8000/api/positions/1746486354/close

# Arreter le bot
curl -X POST -H "Authorization: Bearer TOKEN" \
  http://38.247.138.124:8000/api/bot/stop

# Demarrer le bot
curl -X POST -H "Authorization: Bearer TOKEN" \
  http://38.247.138.124:8000/api/bot/start
```
