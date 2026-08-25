# Rapport de Projet — Installation Automatisee CopyTrading

**Date :** 2026-08-25
**Objectif :** Permettre a un client debutant d'installer et configurer le bot de trading en 5 minutes, sans connaissance technique.

---

## Vue d'ensemble

Le client recoit un fichier `install.bat` + une application Android. Apres 3 etapes simples, le bot est operationnel.

```
Client                    Serveur Windows                  App Android
  |                            |                              |
  |-- double-clique .bat ----->|                              |
  |                            |-- installe Python            |
  |                            |-- telecharge les fichiers    |
  |                            |-- genere token               |
  |                            |-- detecte IP                 |
  |                            |-- genere QR code             |
  |                            |-- lance bot_api.py           |
  |<--- QR code affiche -------|                              |
  |                                                        |
  |--- scanne QR code ------------------------------------->|
  |                            |<---- connexion API ---------|
  |                            |                              |
  |                            |<---- config MT5 -------------|
  |                            |<---- config Telegram --------|
  |                            |<---- demarrer bot -----------|
  |                            |                              |
  |                    BOT OPERATIONNEL                       |
```

---

## Phase 1 — Preparation (Developpeur)

### Etape 1.1 — Organiser les fichiers du bot

Les 4 fichiers Python necessaires :

| Fichier | Role |
|---|---|
| `telegram_listener_v17_1.py` | Bot principal (ecoute Telegram, trade MT5) |
| `signal_parser_v15.py` | Parsing des signaux des canaux |
| `bot_messages_v15.py` | Messages et alertes Telegram |
| `bot_api.py` | Serveur API (communique avec l'app Android) |

**Action :** Verifier que ces 4 fichiers sont dans le repo GitHub.

### Etape 1.2 — Creer un GitHub Release

Le .bat telecharge les fichiers depuis un **GitHub Release** (pas depuis le repo brut, car le repo peut etre prive).

```bash
# Creer un release avec les fichiers
gh release create v17.1 \
  telegram_listener_v17_1.py \
  signal_parser_v15.py \
  bot_messages_v15.py \
  bot_api.py \
  --title "CopyTrading Bot v17.1" \
  --notes "Version initiale"
```

Le .bat telecharge depuis :
```
https://github.com/slh04ninja-cmyk/CopyTrading/releases/download/v17.1/telegram_listener_v17_1.py
```

### Etape 1.3 — Generer le QR code

Le .bat genere un QR code contenant les informations de connexion :

```
copytrading://connect?ip=38.247.138.124&port=8000&token=aB3xK9mN...
```

**Librairie Python pour QR code :**
```bat
pip install qrcode
```

**Generation du QR :**
```python
import qrcode
qr = qrcode.make(f"copytrading://connect?ip={ip}&port={port}&token={token}")
qr.save("setup_qr.png")
```

**Affichage du QR dans le terminal Windows :**
```python
# Ou afficher en texte dans le terminal
qr.print_ascii(invert=True)
```

### Etape 1.4 — Deep Link Android

L'app Android doit reconnaitre le scheme `copytrading://` :

```xml
<!-- AndroidManifest.xml -->
<intent-filter>
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="copytrading" android:host="connect" />
</intent-filter>
```

---

## Phase 2 — Script d'installation (install.bat)

### Etape 2.1 — Structure du script

```bat
@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   CopyTrading Bot — Installation
echo ========================================
echo.

REM 1. Verifier si Python est installe
python --version >nul 2>&1
if errorlevel 1 (
    echo [1/6] Installation de Python...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe' -OutFile '%TEMP%\python_installer.exe'"
    "%TEMP%\python_installer.exe" /quiet InstallAllUsers=1 PrependPath=1
    del "%TEMP%\python_installer.exe"
    echo Python installe.
) else (
    echo [1/6] Python deja installe.
)

REM 2. Creer le dossier du bot
echo [2/6] Creation du dossier C:\TradingBot...
mkdir C:\TradingBot 2>nul
cd C:\TradingBot

REM 3. Telecharger les fichiers
echo [3/6] Telechargement des fichiers...
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/slh04ninja-cmyk/CopyTrading/releases/download/v17.1/telegram_listener_v17_1.py' -OutFile 'telegram_listener_v17_1.py'"
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/slh04ninja-cmyk/CopyTrading/releases/download/v17.1/signal_parser_v15.py' -OutFile 'signal_parser_v15.py'"
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/slh04ninja-cmyk/CopyTrading/releases/download/v17.1/bot_messages_v15.py' -OutFile 'bot_messages_v15.py'"
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/slh04ninja-cmyk/CopyTrading/releases/download/v17.1/bot_api.py' -OutFile 'bot_api.py'"

REM 4. Installer les dependances Python
echo [4/6] Installation des dependances...
pip install fastapi uvicorn[standard] telethon MetaTrader5 fpdf2 openpyxl qrcode

REM 5. Generer le token et configurer le .env
echo [5/6] Generation du token...
python -c "
import secrets, socket
token = secrets.token_urlsafe(32)
port = 8000
for p in range(8000, 8010):
    try:
        s = socket.socket()
        s.bind(('', p))
        s.close()
        port = p
        break
    except:
        continue
with open('.env', 'w') as f:
    f.write(f'API_TOKEN={token}\n')
    f.write(f'API_HOST=0.0.0.0\n')
    f.write(f'API_PORT={port}\n')
    f.write(f'MAGIC_NUMBER=20250226\n')
    f.write(f'DEMO_MODE=true\n')
print(f'TOKEN={token}')
print(f'PORT={port}')
"

REM 6. Lancer le serveur API
echo [6/6] Demarrage du serveur API...
start /B python bot_api.py

REM 7. Detecter l'IP publique
for /f "tokens=*" %%i in ('powershell -Command "(Invoke-WebRequest -Uri 'https://api.ipify.org').Content"') do set PUBLIC_IP=%%i

REM 8. Generer et afficher le QR code
python -c "
import qrcode
ip = '%PUBLIC_IP%'
port = '%PORT%'
token = '%TOKEN%'
data = f'copytrading://connect?ip={ip}&port={port}&token={token}'
qr = qrcode.QRCode(border=1)
qr.add_data(data)
qr.make(fit=True)
qr.print_ascii(invert=True)
print()
print(f'IP: {ip}')
print(f'Port: {port}')
print(f'Token: {token}')
"

echo.
echo ========================================
echo   Installation terminee !
echo   Scannez le QR code avec l'application
echo   ou entrez les informations manuellement
echo ========================================
pause
```

### Etape 2.2 — Tester le script

**Tester sur un VPS Windows vierge :**
1. Lancer un VPS Windows frais
2. Copier `install.bat` sur le bureau
3. Double-cliquer
4. Verifier que :
   - Python est installe
   - Les 4 fichiers sont telecharges dans `C:\TradingBot\`
   - Le `.env` est cree avec le token
   - Le serveur API demarre
   - Le QR code s'affiche
   - L'IP publique est correcte

---

## Phase 3 — Application Android (Wizard)

### Etape 3.1 — Ecran de bienvenue

```
┌─────────────────────────────┐
│                             │
│     CopyTrading             │
│     Bot de Trading Auto     │
│                             │
│  [Image/logo]               │
│                             │
│  Installez le bot sur votre │
│  serveur Windows, puis      │
│  scannez le QR code.        │
│                             │
│     [ Commencer ]           │
│                             │
└─────────────────────────────┘
```

### Etape 3.2 — Scan QR ou saisie manuelle

```
┌─────────────────────────────┐
│                             │
│  Connexion au serveur       │
│                             │
│  [ Scanner QR Code ]        │
│                             │
│  ── ou manuellement ──      │
│                             │
│  ┌───────────────────────┐  │
│  │ IP du serveur         │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ Port                  │  │
│  │ 8000                  │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ Token API             │  │
│  └───────────────────────┘  │
│                             │
│       [ Tester ]            │
│                             │
└─────────────────────────────┘
```

**Bouton "Tester" :**
- Appelle `GET /api/status` avec le token
- Si HTTP 200 → "Connecte !" en vert
- Si HTTP 401 → "Token invalide" en rouge
- Si timeout → "Serveur injoignable" en rouge

### Etape 3.3 — Configuration MetaTrader 5

```
┌─────────────────────────────┐
│                             │
│  Configuration MT5          │
│  ● ─── ○ ─── ○ ─── ○       │
│                             │
│  ┌───────────────────────┐  │
│  │ Login MT5             │  │
│  │ 262342460             │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ Mot de passe MT5      │  │
│  │ ****************      │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ Serveur MT5           │  │
│  │ Exness-MT5Trial16     │  │
│  └───────────────────────┘  │
│                             │
│       [ Suivant ]           │
│                             │
└─────────────────────────────┘
```

**Action :** Envoie les valeurs au serveur via `PUT /api/config` :
```json
{
  "values": {
    "MT5_LOGIN": "262342460",
    "MT5_PASSWORD": "***",
    "MT5_SERVER": "Exness-MT5Trial16"
  }
}
```

### Etape 3.4 — Configuration Telegram

```
┌─────────────────────────────┐
│                             │
│  Configuration Telegram     │
│  ● ─── ● ─── ○ ─── ○       │
│                             │
│  ┌───────────────────────┐  │
│  │ API ID                │  │
│  │ 12345678              │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ API Hash              │  │
│  │ abcdef123456...       │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │ Numero de telephone   │  │
│  │ +33 6 12 34 56 78     │  │
│  └───────────────────────┘  │
│                             │
│  [ Obtenir API ID/Hash ]    │
│                             │
│       [ Suivant ]           │
│                             │
└─────────────────────────────┘
```

**Bouton "Obtenir API ID/Hash" :** Ouvre https://my.telegram.org dans le navigateur.

**Action :** Envoie les valeurs au serveur via `PUT /api/config` :
```json
{
  "values": {
    "TG_API_ID": "12345678",
    "TG_API_HASH": "abcdef123456...",
    "TG_PHONE": "+33612345678"
  }
}
```

### Etape 3.5 — Verification Telegram (code SMS)

```
┌─────────────────────────────┐
│                             │
│  Verification Telegram      │
│  ● ─── ● ─── ● ─── ○       │
│                             │
│  Telegram a envoye un code  │
│  a votre numero +336...78   │
│                             │
│  ┌───────────────────────┐  │
│  │ Code de verification  │  │
│  │ 1 2 3 4 5             │  │
│  └───────────────────────┘  │
│                             │
│       [ Verifier ]          │
│                             │
└─────────────────────────────┘
```

**Nouveaux endpoints a creer dans bot_api.py :**

```python
@app.post("/api/telegram/connect")
def telegram_connect(req: TelegramConnectRequest):
    """Demarre la connexion Telegram avec le numero de telephone."""
    # Lance Telethon, appelle client.send_code_request(phone)
    # Stocke le phone dans une variable globale
    # Retourne {"status": "code_sent"}
```

```python
@app.post("/api/telegram/verify")
def telegram_verify(req: TelegramVerifyRequest):
    """Verifie le code SMS et cree la session."""
    # Appelle client.sign_in(phone, code)
    # Si succes, la session est sauvegardee
    # Retourne {"status": "connected", "username": "..."}
```

**Nouveaux modeles Pydantic :**
```python
class TelegramConnectRequest(BaseModel):
    phone: str

class TelegramVerifyRequest(BaseModel):
    phone: str
    code: str
```

### Etape 3.6 — Lancement

```
┌─────────────────────────────┐
│                             │
│  Pret !                     │
│  ● ─── ● ─── ● ─── ●       │
│                             │
│  Resume de la configuration │
│                             │
│  Serveur: 38.247.138.124    │
│  MT5: 262342460             │
│  Telegram: @username        │
│  Mode: DEMO                 │
│                             │
│  [ Demarrer le bot ]        │
│                             │
└─────────────────────────────┘
```

**Action :** Appelle `POST /api/bot/start`.

**Si succes :** Ecran vert "Bot demarre !", bouton "Ouvrir le Dashboard".

---

## Phase 4 — Endpoints API a ajouter

### 4.1 — Endpoints Telegram (nouveaux)

| Endpoint | Methode | Description |
|---|---|---|
| `/api/telegram/connect` | POST | Demarre la connexion Telegram (envoie le code) |
| `/api/telegram/verify` | POST | Verifie le code SMS |
| `/api/telegram/status` | GET | Verifie si Telegram est connecte |

### 4.2 — Endpoints existants utilises par le wizard

| Endpoint | Methode | Utilise pour |
|---|---|---|
| `/api/status` | GET | Tester la connexion |
| `/api/config` | PUT | Configurer MT5, Telegram |
| `/api/config/raw` | PUT | Configurer le .env complet |
| `/api/bot/start` | POST | Demarrer le bot |
| `/api/bot/stop` | POST | Arreter le bot |

---

## Phase 5 — Securite

### 5.1 — Token genere aleatoirement

- Le token est genere par le .bat sur le serveur du client
- Jamais dans le code source
- Jamais dans le repo GitHub
- Unique par installation

### 5.2 — Middleware d'authentification

```python
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public_paths = ["/", "/docs", "/openapi.json"]
    if request.url.path in public_paths:
        return await call_next(request)
    if not API_TOKEN:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        return JSONResponse(status_code=401, content={"detail": "Token invalide"})
    return await call_next(request)
```

### 5.3 — WebSocket authentifie

```python
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=4001, reason="Non autorise")
        return
    await websocket.accept()
    # ...
```

---

## Phase 6 — Fichiers a creer/modifier

### 6.1 — Fichiers a creer

| Fichier | Description |
|---|---|
| `install.bat` | Script d'installation Windows |
| `install_qr.py` | Generation du QR code (appele par le .bat) |

### 6.2 — Fichiers Android a modifier

| Fichier | Modification |
|---|---|
| `SetupActivity.kt` | Nouvel ecran wizard (4 etapes) |
| `ApiClient.kt` | Ajouter les endpoints Telegram |
| `AndroidManifest.xml` | Ajouter le deep link `copytrading://` |
| `ApiModels.kt` | Ajouter les modeles Telegram |

### 6.3 — Fichiers serveur a modifier

| Fichier | Modification |
|---|---|
| `bot_api.py` | Ajouter les endpoints Telegram + middleware auth |

---

## Phase 7 — Tests

### 7.1 — Tests du .bat

| Test | Resultat attendu |
|---|---|
| VPS Windows vierge | Python installe, fichiers telecharges |
| Port 8000 occupe | Utilise 8001 automatiquement |
| Pas de connexion internet | Erreur claire |
| Python deja installe | Saute l'installation |

### 7.2 — Tests du wizard

| Test | Resultat attendu |
|---|---|
| QR code valide | Connexion automatique |
| QR code invalide | Message d'erreur |
| Mauvais token | "Token invalide" |
| Serveur injoignable | "Serveur injoignable" |
| Code SMS correct | Telegram connecte |
| Code SMS incorrect | "Code invalide" |

### 7.3 — Tests d'integration

| Test | Resultat attendu |
|---|---|
| Installation complete | Bot operationnel en 5 minutes |
| Redemarrage VPS | Bot redemarre automatiquement |
| Mise a jour via app | Fichiers remplaces, bot redemarre |

---

## Phase 8 — Deploiement

### 8.1 — Checklist avant deploiement

- [ ] Les 4 fichiers Python sont dans le repo
- [ ] Le GitHub Release est cree avec les fichiers
- [ ] Le .bat fonctionne sur un VPS vierge
- [ ] Le wizard Android fonctionne
- [ ] Les endpoints Telegram sont implementes
- [ ] Le middleware d'auth est en place
- [ ] Le QR code est genere correctement
- [ ] Le deep link Android fonctionne
- [ ] Les tests sont passes

### 8.2 — Documentation client

Creer un PDF avec :
1. Lien pour telecharger `install.bat`
2. Lien pour telecharger l'app Android (APK ou Play Store)
3. Instructions en images (screenshots)
4. FAQ (problemes courants)

---

## Resume des etapes par priorite

| Priorite | Etape | Duree estimee |
|---|---|---|
| P0 | Creer le GitHub Release avec les 4 fichiers | 30 min |
| P0 | Creer `install.bat` | 2h |
| P0 | Ajouter endpoints Telegram dans `bot_api.py` | 2h |
| P1 | Creer `SetupActivity.kt` (wizard Android) | 4h |
| P1 | Ajouter deep link `copytrading://` | 30 min |
| P1 | Ajouter middleware d'auth | 1h |
| P2 | Tests complets | 2h |
| P2 | Documentation client | 1h |
| **Total** | | **~13h** |

---

*Genere automatiquement — 2026-08-25*
