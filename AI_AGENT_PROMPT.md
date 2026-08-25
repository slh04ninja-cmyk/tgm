# Prompt pour Agent AI — Projet CopyTrading Installation Automatisee

## Contexte

Tu es un developpeur charge de creer un systeme d'installation automatisee pour un bot de trading Telegram → MetaTrader 5. Le bot existe deja et fonctionne. Tu dois creer le script d'installation Windows + le wizard Android pour que les clients puissent installer et configurer le bot sans connaissance technique.

## Connexion au serveur

- **IP :** `38.247.138.124`
- **Port API :** `8000`
- **Token API :** `2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d`
- **Header :** `Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d`
- **OS :** Windows Server (VPS)
- **Dossier bot :** `C:\TradingBot\`

**Commandes utiles :**
```bash
# Status du bot
curl -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" http://38.247.138.124:8000/api/status

# Executer une commande sur le serveur
curl -X POST -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" -H "Content-Type: application/json" \
  -d '{"command": "dir C:\\TradingBot"}' http://38.247.138.124:8000/api/exec

# Lire un fichier
curl -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" "http://38.247.138.124:8000/api/file?path=.env"

# Ecrire un fichier
curl -X POST -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" -H "Content-Type: application/json" \
  -d '{"path": "test.py", "content": "print(1)"}' http://38.247.138.124:8000/api/file

# Modifier la config
curl -X PUT -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" -H "Content-Type: application/json" \
  -d '{"values": {"MAX_SL_USD": "15.0"}}' http://38.247.138.124:8000/api/config
```

## Depots de travail

### Bot (Python) — Depot `tgm`
- **Chemin local :** `/data/data/com.termux/files/home/tgm/`
- **GitHub :** `slh04ninja-cmyk/tgm` (prive)
- **Branche :** `main`
- **Fichiers principaux :**
  - `telegram_listener_v17_1.py` — bot principal (~4100 lignes)
  - `signal_parser_v15.py` — parsing des signaux
  - `bot_messages_v15.py` — messages/alertes Telegram

### App Android — Depot `CopyTrading`
- **Chemin local :** `/data/data/com.termux/files/home/CopyTrading/`
- **GitHub :** `slh04ninja-cmyk/CopyTrading` (prive)
- **Branche :** `main`
- **Token GitHub actif :** `REDACTED` (compte `slh04ninja-cmyk`)
- **Fichiers principaux :**
  - `bot_api.py` — serveur API FastAPI
  - `app/src/main/java/com/copytrading/MainActivity.kt` — ecran principal
  - `app/src/main/java/com/copytrading/api/ApiClient.kt` — client HTTP
  - `app/src/main/java/com/copytrading/model/ApiModels.kt` — data classes
  - `app/src/main/res/layout/activity_main.xml` — layout principal

### Build APK
- **GitHub Actions :** `Build Debug APK` (triggered on push)
- **Commande :** `gh run list --limit 1` puis `gh run watch <ID> --exit-status`
- **Telechargement :** `gh run download <ID> -D /data/data/com.termux/files/home/apk_tmp`
- **APK :** `copytrading-debug/app-debug.apk`
- **Installation :** copier dans `/storage/emulated/0/Download/CopyTrading.apk`

## Architecture actuelle

```
Serveur Windows (VPS)
├── C:\TradingBot\
│   ├── telegram_listener_v17_1.py  (bot principal)
│   ├── signal_parser_v15.py        (parsing signaux)
│   ├── bot_messages_v15.py         (messages)
│   ├── bot_api.py                  (serveur API)
│   ├── .env                        (configuration)
│   ├── channels.txt                (canaux Telegram)
│   └── bot_trading.log             (logs)
│
├── MetaTrader 5                    (terminal de trading)
│   └── Compte: 262342460 / Exness-MT5Trial16
│
└── Python 3.x                      (runtime)
```

## API Endpoints existants

| Endpoint | Methode | Description |
|---|---|---|
| `/` | GET | Status du serveur (public) |
| `/api/status` | GET | Status bot + MT5 |
| `/api/dashboard` | GET | Resume trading du jour |
| `/api/positions` | GET | Positions ouvertes |
| `/api/trades?days=N` | GET | Historique deals |
| `/api/config` | GET | Lire la config (.env) |
| `/api/config` | PUT | Modifier des variables |
| `/api/config/raw` | GET | Lire le .env complet |
| `/api/config/raw` | PUT | Ecraser le .env |
| `/api/logs?lines=N` | GET | Logs du bot |
| `/api/bot/start` | POST | Demarrer le bot |
| `/api/bot/stop` | POST | Arreter le bot |
| `/api/positions/{ticket}/close` | POST | Fermer une position |
| `/api/positions/close-all` | POST | Fermer toutes les positions |
| `/api/exec` | POST | Executer une commande shell |
| `/api/file?path=` | GET | Lire un fichier |
| `/api/file` | POST | Ecrire un fichier |
| `/api/files?path=` | GET | Lister les fichiers |
| `ws://host:8000/ws/logs` | WS | Logs en temps reel |

## Travail a realiser

### Tache 1 — Creer `install.bat`

Creer un script d'installation Windows qui :
1. Verifie si Python est installe, sinon l'installe
2. Cree le dossier `C:\TradingBot`
3. Telecharge les 4 fichiers Python depuis un GitHub Release
4. Installe les dependances Python (`pip install fastapi uvicorn[standard] telethon MetaTrader5 fpdf2 openpyxl qrcode`)
5. Genere un token aleatoire (`secrets.token_urlsafe(32)`)
6. Trouve un port libre (essaie 8000 a 8009)
7. Cree le fichier `.env` avec le token, le port, et les valeurs par defaut
8. Lance `bot_api.py` en arriere-plan
9. Detecte l'IP publique du serveur (via https://api.ipify.org)
10. Genere et affiche un QR code contenant `copytrading://connect?ip=IP&port=PORT&token=TOKEN`
11. Affiche le resume (IP, port, token) pour saisie manuelle

**Fichier a creer :** `install.bat` (dans le repo CopyTrading)

### Tache 2 — Ajouter endpoints Telegram dans `bot_api.py`

Ajouter 3 nouveaux endpoints pour la connexion Telegram :

```python
# Nouveaux modeles
class TelegramConnectRequest(BaseModel):
    phone: str

class TelegramVerifyRequest(BaseModel):
    phone: str
    code: str

# Nouveaux endpoints
@app.post("/api/telegram/connect")
def telegram_connect(req: TelegramConnectRequest):
    """Envoie le code de verification Telegram au numero."""
    # Utilise l'instance Telethon du bot pour envoyer le code
    # Stocke le phone dans une variable globale
    # Retourne {"status": "code_sent", "phone": req.phone}

@app.post("/api/telegram/verify")
def telegram_verify(req: TelegramVerifyRequest):
    """Verifie le code SMS et cree la session Telegram."""
    # Utilise l'instance Telethon pour sign_in(phone, code)
    # Sauvegarde la session
    # Retourne {"status": "connected", "username": "..."}

@app.get("/api/telegram/status")
def telegram_status():
    """Verifie si Telegram est connecte."""
    # Retourne {"connected": true/false, "username": "..."}
```

**Fichier a modifier :** `bot_api.py`

**Attention :** L'instance Telethon est dans `telegram_listener_v17_1.py`, pas dans `bot_api.py`. Il faut soit :
- Partager l'instance entre les deux processus (difficile)
- Ou creer une instance Telethon separee dans `bot_api.py` pour la connexion initiale
- Ou utiliser un fichier de session partage

**Solution recommandee :** `bot_api.py` cree sa propre instance Telethon pour la connexion initiale. Apres la verification, la session est sauvegardee dans un fichier. Le bot principal (`telegram_listener_v17_1.py`) utilise ce fichier de session au demarrage.

### Tache 3 — Ajouter middleware d'auth dans `bot_api.py`

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public_paths = ["/", "/docs", "/openapi.json"]
    if request.url.path in public_paths:
        return await call_next(request)
    if not API_TOKEN:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        return JSONResponse(status_code=401, content={"detail": "Token invalide ou manquant"})
    return await call_next(request)
```

**Fichier a modifier :** `bot_api.py`

### Tache 4 — Creer `SetupActivity.kt` (wizard Android)

Creer un nouvel ecran avec 4 etapes :

**Etape 1 — Connexion :**
- Champs : IP, Port, Token
- Bouton "Scanner QR Code" (ouvre la camera)
- Bouton "Tester" → appelle `GET /api/status`
- Deep link : `copytrading://connect?ip=...&port=...&token=...`

**Etape 2 — Configuration MT5 :**
- Champs : Login, Mot de passe, Serveur
- Bouton "Suivant" → appelle `PUT /api/config`

**Etape 3 — Configuration Telegram :**
- Champs : API ID, API Hash, Numero de telephone
- Bouton "Obtenir API ID/Hash" → ouvre https://my.telegram.org
- Bouton "Suivant" → appelle `PUT /api/config` puis `POST /api/telegram/connect`

**Etape 4 — Verification + Lancement :**
- Champ : Code de verification SMS
- Bouton "Verifier" → appelle `POST /api/telegram/verify`
- Bouton "Demarrer le bot" → appelle `POST /api/bot/start`
- Ecran de confirmation

**Fichiers a creer :**
- `app/src/main/java/com/copytrading/SetupActivity.kt`
- `app/src/main/res/layout/activity_setup.xml`

**Fichiers a modifier :**
- `AndroidManifest.xml` — ajouter deep link + SetupActivity
- `ApiClient.kt` — ajouter les methodes Telegram
- `ApiModels.kt` — ajouter les modeles Telegram

### Tache 5 — Ajouter deep link Android

Dans `AndroidManifest.xml`, ajouter l'intent filter pour `SetupActivity` :

```xml
<intent-filter>
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="copytrading" android:host="connect" />
</intent-filter>
```

Dans `SetupActivity.kt`, parser l'URI au lancement :
```kotlin
val uri = intent.data
if (uri != null) {
    val ip = uri.getQueryParameter("ip")
    val port = uri.getQueryParameter("port")
    val token = uri.getQueryParameter("token")
    // Pre-remplir les champs et tester la connexion
}
```

### Tache 6 — Ajouter endpoints de mise a jour dans `bot_api.py`

```python
@app.post("/api/bot/update")
def update_bot_files():
    """Telecharge les derniers fichiers depuis GitHub et redemarre le bot."""
    # 1. Telecharger les fichiers depuis le GitHub Release
    # 2. Remplacer les fichiers existants
    # 3. Redemarrer le bot
    # Retourne {"status": "updated", "files": [...]}
```

## Contraintes

- **Langue :** tous les commentaires et messages en francais
- **Pas d'emojis dans l'UI Android**
- **Style UI :** Material3, gradients, dark theme (cf. prototype HTML dans le cache)
- **Build APK :** via GitHub Actions (push → build automatique)
- **Token GitHub :** `REDACTED`
- **Pas de sous-agents** (DaemonThreadPoolExecutor bug) — faire les modifications manuellement
- **Pas de SSH** sur le serveur Windows — utiliser l'API REST
- **Tester chaque modification** avant de commit

## Livrables

1. `install.bat` — script d'installation Windows
2. `bot_api.py` modifie — endpoints Telegram + middleware auth
3. `SetupActivity.kt` — wizard Android (4 etapes)
4. `activity_setup.xml` — layout du wizard
5. `ApiClient.kt` modifie — methodes Telegram
6. `ApiModels.kt` modifie — modeles Telegram
7. `AndroidManifest.xml` modifie — deep link
8. GitHub Release avec les 4 fichiers Python

## Verification finale

Apres chaque modification :
1. Commit + push sur le repo
2. Verifier que le build APK reussit
3. Tester l'endpoint sur le serveur
4. Tester l'APK sur le telephone

**Commande pour tester un endpoint :**
```bash
curl -s -H "Authorization: Bearer 2f7e1c4d8a9b3e5f0c6d2a8e4b1f7c9d" "http://38.247.138.124:8000/api/status" | python3 -m json.tool
```

---

*Prompt genere le 2026-08-25*
