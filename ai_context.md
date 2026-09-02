# AI Context — Bot CopyTrading (tgm) v17.4h

> Contexte unique pour un agent AI reprenant ce projet. Fusion de CONTEXT.md + AI_AGENT_PROMPT.md + API_DOCUMENTATION.md, **mis à jour au 01/09/2026 (v17.4h)**.
> Sources de vérité : ce fichier, `bot_documentation_v17.html` (doc complète), `~/.hermes/trading-bot-context.md` (notes opérationnelles), le code lui-même.

---

## 0. Instructions pour l'agent repreneur (ajout 02/09)

- **Lis ce fichier EN ENTIER avant toute action** — c'est la source de vérité (état 02/09, v17.4h).
- **Règle d'or** : ne modifie AUCUN code sans proposer d'abord — l'utilisateur valide chaque changement (il répond « oui appliquer » ou « non »). Commit + push **seulement après accord explicite**.
- **GitHub (push)** : repos `slh04ninja-cmyk` (`tgm` + `CopyTrading`). ⚠️ GitHub bloque (push protection) tout fichier contenant un token `ghp_` en clair → **token à demander à l'utilisateur** (il le fournit en début de session, comme le token API). Toujours demander avant de pusher.
- **Token API serveur** : **à demander à l'utilisateur** (volontairement pas stocké ici). ⚠️ **Ne JAMAIS le modifier / régénérer** — d'autres consommateurs l'utilisent (app Android + watchdog Hermes). S'il est changé, l'app et le watchdog cassent. Pour rappel : 32 caractères hex, généré le 02/09, présent dans `.env` (`API_TOKEN=...`, lu APRÈS `load_dotenv` dans bot_api.py).
- **Authentification API** : toute requête porte `Authorization: Bearer <token>` — sans token → **401**. Si l'API refuse tout même avec le bon token → vérifier que `API_TOKEN` est lu **après** `load_dotenv` dans `bot_api.py` (piège connu : avant = toujours vide = 401 partout).
- **Pièges critiques** :
  - Upload via `/api/file` transforme **LF → CRLF** : vérifier le MD5 sur la version **CRLF** locale avant de conclure.
  - `PUT /api/config` (et `/config/raw`) **redémarre le bot automatiquement** ; champ vidé = variable supprimée du `.env` (sauf `TG_FOLDER`/`TG_ALERT_CHANNEL` préservés vides) ; valeur `***` = ignorée (secrets jamais écrasés).
  - Le `.env` n'est lu **qu'au démarrage** → toute modif nécessite le restart (automatique via l'API).
  - `~/tgm/.env` est versionné = **reflet exact du serveur** → le garder synchronisé.
  - Le serveur est la vérité : vérifier par `GET /api/file` + MD5 (version CRLF) avant de conclure.
- **Méthode de travail** : après chaque modif de code → `python -m py_compile`, `test_tous_cas.py` (**76 PASS**) + `test_zn.py` (**45 PASS**), puis upload + restart, puis commit + push (après validation).
- **Logs** : timestamps **UTC**. Marqueurs utiles : `DOUBLON IGNORE`, `NOUVEAU SIGNAL`, `HORS-ZONE EXPIRE`, `[NEWS] Fermeture`, `TP dynamique`, `[RECOVERY]`, `REFUSE | SYMBOLE INTROUVABLE`.
- ⚠️ **Leçon 02/09** : un bot peut tourner **aveugle** (connexion MT5 tombée sans que le processus meure — fermetures échouées loggées `P&L: +0.00`, puis `symbole introuvable` en boucle). Le watchdog ne détecte que le processus mort. Toujours vérifier que les logs récents montrent une activité MT5 saine (`MT5 connecté`, ordres exécutés), pas seulement que le process vit.
- **Ne pas ressusciter** (supprimés par décision) : génération XLSX, fusion par prix (`merge_quick_alert`, `_market_in_zone`), `MAX_TEMPS`, `SIGNAL_FORWARD_DIR`, `QA_PRICE_TOLERANCE`, `TP_PAR_DEFAUT`, `RR_RATIO_DEFAULT`, `QUICK_ALERT_SL_OFFSET`, `FUSION_TOLERANCE`, `TP_DISTANCE_MIN_RATIO`.

### Bot 2 — expérience A/B (ABANDONNÉE, ne pas ressusciter)

- **Méthode** : le bot 1 (actuel) forwardait les signaux détectés vers un **2e bot** (dossier `C:\TradingBot2`, via la variable `SIGNAL_FORWARD_DIR` → `C:\TradingBot2\inbox`) qui tradait **les mêmes signaux en ajoutant des filtres** supplémentaires.
- **But** : **comparer les résultats** (P&L, winrate) entre les deux approches (sans filtres vs avec filtres) pour mesurer l'apport réel des filtres sur les trades.
- **Statut : ABANDONNÉ** — l'approche a été laissée tombée **à cause de problèmes rencontrés** (gestion de deux bots/instances MT5 simultanées sur le même VPS, suivi compliqué). Ne pas ressusciter : bot 2 oublié, `SIGNAL_FORWARD_DIR` supprimée du .env et du code (0 usage), aucun forward actif.

### Build de l'app Android (repo `CopyTrading`)

- **Déclenchement** : le workflow `.github/workflows/build.yml` (« Build Debug APK ») se lance **automatiquement à chaque `git push` sur `main`** (pas de build local) ; `workflow_dispatch` possible aussi.
- **Étapes du build** : ubuntu-latest → JDK 17 → Gradle 8.5 → `gradle assembleDebug`.
- **Artifact** : nom `copytrading-debug` (`app/build/outputs/apk/debug/*.apk`).
- **Récupération de l'APK** (après push) :
  ```bash
  gh run watch            # attendre la fin du build
  gh run download <run_id> -n copytrading-debug -D ~/apk_dl
  cp ~/apk_dl/app/build/outputs/apk/debug/*.apk ~/CopyTrading.apk
  ```
  Puis envoyer `~/CopyTrading.apk` à l'utilisateur (MEDIA: dans le chat Telegram). Vérifier le MD5 si besoin.
- **Cycle de test** : l'utilisateur installe l'APK sur son téléphone (Xiaomi, Android 13) et teste → rapporte les bugs dans le chat → fix → nouveau push/build/APK.
- ⚠️ **Règle** : proposer les modifications de l'app AVANT de les faire (validation utilisateur), comme pour le bot. Une modif du bot (listener) nécessite un restart ; une modif de l'app nécessite un nouvel APK.

---

## 1. Vue d'ensemble

Bot Telegram de **copy trading** qui lit les signaux de **91 canaux Telegram** et exécute des ordres sur **MetaTrader 5 (Exness)**. Le bot tourne sur un **serveur Windows VPS** (`38.247.138.124`, hostname `vps-mt5`, user `Administrator`).

- **Compte MT5** : 262342460 (Exness-MT5Trial16), symbole **XAUUSDm** (Gold), magic 20250226, lot 0.01
- **Fenêtre de trading** : **5h–21h UTC** (`TRADING_START_HOUR=5`, `TRADING_END_HOUR=21`)
- **Balance** : ~1454$ (01/09), P&L quotidien limité à **400$** (`DAILY_PROFIT_LIMIT`)
- Une **app Android Kotlin** (repo `CopyTrading`) se connecte à l'API REST pour dashboard/positions/config/logs

### Serveur

- **Pas de SSH** — accès uniquement via l'API REST (port 8000) ou RDP
- Dossier bot : `C:\TradingBot\` — fichiers : `telegram_listener_v17_1.py`, `signal_parser_v15.py`, `bot_messages_v15.py`, `bot_api.py`, `.env`, `start_bot1.py`
- Logs : `C:\TradingBot\bot_trading.log` (API) / `stdout.log` (bot lancé par start_bot1.py)
- ⚠️ Après reboot du VPS, `bot_api.py` n'est **pas relancé automatiquement** (relance manuelle RDP nécessaire)
- ⚠️ **Authentification API (02/09)** : TOUTES les routes exigent `Authorization: Bearer <API_TOKEN>` (middleware, 401 sinon) — token 32 hex généré 02/09, dans `.env` (`API_TOKEN=...`), à enregistrer dans l'app. Reste exposé volontairement : `/api/exec` (RCE) et `/api/config/raw` (secrets) **ne sont accessibles qu'avec le token** ; CORS `*`

---

## 2. Architecture du bot (4 fichiers)

| Fichier | Rôle |
|---|---|
| `telegram_listener_v17_1.py` (~4800 lignes) | Cœur du bot : boucle async Telethon, parsing → décision, exécution MT5, gestion des trades, rapport quotidien **PDF** |
| `signal_parser_v15.py` | Parser de signaux (classe `SignalParser`, log DEBUG) |
| `bot_messages_v15.py` | Messages/alertes Telegram (format unifié **sans accents**) |
| `bot_api.py` | API REST FastAPI (uvicorn, port 8000) — statut, positions, config, fichiers, exec |

**Chaîne de traitement** :
```
Telegram (91 canaux) → Parser → Filtres (horaire/news/spread/TV) → Anti-doublon → Exécution MT5
        → gestion des trades (phases 3b/4b/5) → rapport quotidien PDF
```

---

## 3. Types de signaux

| Type | Nom | Zone |
|---|---|---|
| **ZN** | Zone | zone [low, high] du signal + TOLERANCE_ZN |
| **PU** | Prix Unique | `[entry ± TOLERANCE_PU]` (3), bornes **strictes** côté entrée |
| **MP** | Market Price (« BUY NOW » sans prix) | `[current ± TOLERANCE_MP]` (2), symétrique — jamais refusé |
| **QA** | Quick Alert avec prix (« BUY 4439 ») | `[entry ± TOLERANCE_PU]` (3), **inclusif** |
| **AL** | Alert | non tradé |

- Hybride MP avec entry défini → zone QA inclusive (label MP)
- **Commentaires MT5** : `CH{canal}-{signal}-{ordre}` — ex. `CH5-ZN-MK`, `CH3-MP-L1`, `CH900-ZN-L3`

### Formules de zone (v17.4h, testées 76 cas)

- **ZN** : SELL accepté si `low−TOLERANCE_ZN ≤ prix ≤ high` (bord = low) ; BUY si `low ≤ prix ≤ high+TOLERANCE_ZN` (bord = high). **Hors zone** : dist au bord étendu ≤ `MAX_DISTANCE` (3) → ordres **L3/L4** (pas de market) ; **REFUSE** si dist > 3, prix de l'autre côté de la zone, ou `TRADE_HORS_ZONE=false`
- **PU** : BUY `entry−3 < prix ≤ entry+3` (4437 exact = REFUSE) ; SELL `entry−3 ≤ prix < entry+3` (4443 exact = REFUSE)
- **QA** : `entry−3 ≤ prix ≤ entry+3` (inclusif)
- **MP** : `[current ± 2]` → jamais refusé

### Anti-doublon QA/MP + PU/ZN (v17.4h — remplace l'ancienne fusion)

- **Cas 1** : PU/ZN arrive **≤ `TEMPS_DE_FUSION` min** (serveur : **3**) après un QA/MP du **même canal/symbole/action** = **MÊME signal** (même entry) → `DOUBLON IGNORE`, le QA/MP reste intact
- **Cas 2** : PU/ZN arrive **> `TEMPS_DE_FUSION` min** après = **NOUVEAU signal** → `execute_signal` (ferme l'ancien du canal via `_close_previous_signal`, puis ouvre le nouveau)
- Logs : `DOUBLON IGNORE (QA/MP <= X min)` / `NOUVEAU SIGNAL (QA/MP > X min)`
- `merge_quick_alert` et `_market_in_zone` **supprimés** (fusion par prix disparue)

---

## 4. Gestion des trades

### Ordres

- **Cas 1 (dans zone)** : 1 **MARKET** (exécution immédiate) + 2 **LIMITs** (offsets `LIMIT_OFFSET_1`=3, `LIMIT_OFFSET_2`=6, expiration native MT5 `LIMIT_EXPIRY_MIN`=30 min)
- **Cas 2 (hors zone ZN)** : 2 **LIMITs L3/L4** (pas de market) — L3 = bord de zone côté prix, L4 = milieu. TP/SL ±7/±12 sur le prix de l'ordre
- Filling fallback : FOK → IOC → RETURN (erreur 10013 gérée)
- `LOT_MARKET`/`LOT_LIMIT1`/`LOT_LIMIT2` = 0.01, `MAX_POSITIONS`=60

### TP (unifié v17.4h)

- **TP initial = `current ± TP_FIXED_GAIN_USD` (7$)** pour **TOUS** les types (ZN/PU/MP/QA), depuis le **prix d'exécution réel du MK**, même TP pour MK/L1/L2, **TP du signal ignoré**
- **TP dynamique** : recalculé au remplissage de chaque LIMIT — `pnl_cible = TP_FIXED_GAIN_USD × multiplicateur` (1.0 solo / 1.5 avec L1 / 2.0 avec L2), TP = average entry pondéré ± (pnl_cible / nb positions)
- Variables supprimées : `TP_PAR_DEFAUT`, `TP_DISTANCE_MIN_RATIO`, `RR_RATIO_DEFAULT` (le TP auto du parser = ±TP_FIXED_GAIN_USD)

### SL

- **SL capé `MAX_SL_USD` (12$)** sur prix d'exécution **réel** (min/max garde le SL le plus proche), **UNIQUE MK+L1+L2**, SL-FIX post-ouverture (3 essais, tolérance 3 points)
- `MAX_SL_USD` sert **aussi** de SL par défaut des quick alerts QA/MP (`QUICK_ALERT_SL_OFFSET` supprimé)

### Phases de gestion (cycle ~1s)

- **PHASE 3b (HORS-ZONE EXPIRE)** : prix s'éloigne > `MAX_DISTANCE` (3$) de L3 → annulation des LIMITs — **uniquement si L3 ET L4 sont encore toutes les deux pendantes** (règle 01/09 : une LIMIT remplie → MAX_DISTANCE désactivé, seule l'expiration native MT5 `LIMIT_EXPIRY_MIN`=30 min gère les ordres restants) — `MAX_TEMPS` **supprimé**
- **PHASE 4b (P&L CIBLE — clôture PAR TRADE)** : P&L flottant `pos.profit + pos.swap` ≥ pnl_cible (7/10.5/14$) → ferme tout + annule les LIMITs + alerte (garde `_pnl_close_done`)
- **PHASE 5** : MK fermé → `cancel_pending_limits(entry)` + log `MARKET #ticket fermé -> N LIMIT annulés`
- **Recovery au redémarrage** : scan MT5, restaure les entrées actives, matching par numéro CHxx
- LIMIT pendante vivante = ticket valide (fix v17.4h : plus d'annulation des L3/L4 vivants)
- CLOSE signal → `close_all()` + annulation des LIMITs du canal

### Rapports quotidiens

- **PDF uniquement** (généré + envoyé à minuit UTC) — **export XLSX SUPPRIMÉ** (bug `d.deal` ; décision : PDF seul)

---

## 5. Filtres

| Filtre | Variable | Valeur serveur |
|---|---|---|
| Horaire | `TRADING_START_HOUR` / `TRADING_END_HOUR` | 5 / 21 (UTC) |
| News | `NEWS_FILTER_ENABLED`, `NEWS_MIN_IMPACT`=high, fenêtres 15 min avant / 5 min close / 15 min après | ON |
| Spread | `MAX_SPREAD_POINTS` | 50 |
| TradingView | `TV_FILTER_ENABLED` | OFF |
| Conflit | `CONFLIT_FILTER_ENABLED` | — |

- `DAILY_PROFIT_LIMIT` (400$) : P&L quotidien **réalisé + flottant, swap ignoré** (tout est fermé chaque journée) → `_check_daily_pnl_limit`
- P&L quotidien reset à 5h UTC (début de journée)

---

## 6. Variables .env (état serveur 01/09 — 62 variables, 9 sections)

Groupées en sections commentées : TELEGRAM / MT5 (compte) / LOTS ET ORDRES / GESTION DES TRADES / ZONES ET ANTI-DOUBLON / FILTRES / HORAIRE-NEWS-TV / ALERTES ET LOGS / DIVERS.

**Valeurs clés** : `TEMPS_DE_FUSION=3` (min), `TOLERANCE_ZN=1.0`, `TOLERANCE_PU=3.0`, `TOLERANCE_MP=2.0`, `MAX_DISTANCE=3`, `TRADE_HORS_ZONE=true`, `TP_FIXED_GAIN_USD=7`, `TP_MULTIPE1=1.5`, `TP_MULTIPE2=2`, `MAX_SL_USD=12`, `DAILY_PROFIT_LIMIT=400`, `LOG_TRADE_MANAGEMENT=true`, `LIMIT_EXPIRY_MIN=30`.

**Supprimées** (mortes) : `MAX_TEMPS`, `SIGNAL_FORWARD_DIR` (bot2 oublié), `QA_PRICE_TOLERANCE`, `FUSION_TOLERANCE`, `TP_PAR_DEFAUT`, `TP_DISTANCE_MIN_RATIO`, `QUICK_ALERT_SL_OFFSET`, `RR_RATIO_DEFAULT`.

⚠️ **Le .env est relu UNIQUEMENT au démarrage du bot** — mais depuis v17.4h, **toute sauvegarde config via l'API redémarre le bot automatiquement** (voir §7).

---

## 7. API REST (port 8000)

- **URL** : `http://38.247.138.124:8000` — format JSON (`Content-Type: application/json`)
- **Authentification** : header `Authorization: Bearer <API_TOKEN>` — **OBLIGATOIRE depuis 02/09** (401 sinon). Valeur dans `.env` (`API_TOKEN=...`), masquée `***` par `GET /api/config` et ignorée par `PUT /api/config` si `***`. ⚠️ `API_TOKEN` est lu **APRÈS** `load_dotenv` dans bot_api.py (piège : le placer avant = token toujours vide = 401 partout).

### Endpoints

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/api/status` | GET | État du bot + compte MT5 |
| `/api/dashboard` | GET | P&L quotidien, balance, equity, winrate |
| `/api/positions` | GET | Positions ouvertes |
| `/api/trades?days=7` | GET | Historique deals |
| `/api/config` | GET/PUT | Variables .env (PUT body `{"values": {...}}`) |
| `/api/config/raw` | GET/PUT | Contenu brut du .env (PUT body `{"content": "..."}`) |
| `/api/logs?lines=100` | GET | Dernières lignes du log |
| `/ws/logs` | WS | Logs en temps réel |
| `/api/positions/{ticket}/close` | POST | Fermer une position |
| `/api/positions/close-all` | POST | Fermer toutes les positions |
| `/api/exec` | POST | Commande shell Windows (timeout 30s) |
| `/api/file?path=...` | GET | Lire un fichier (limitée à `C:\TradingBot\`) |
| `/api/file` | POST | Écrire/créer un fichier (body `{"path","content"}`) |
| `/api/files?path=` | GET | Lister les fichiers |
| `/api/bot/start` / `/api/bot/stop` | POST | Démarrer/arrêter le bot |
| `/api/restart` | POST | Redémarrer uvicorn (recharge bot_api.py) |

### ⭐ Redémarrage auto après sauvegarde config (v17.4h)

`PUT /api/config` et `PUT /api/config/raw` **redémarrent automatiquement le bot** s'il tourne (pour appliquer les nouvelles valeurs). La réponse inclut `"restart": {"restarted": true, "pid": ...}`. Si le bot est arrêté → pas de démarrage forcé.

### Workflow upload code + redémarrage

```bash
TOKEN=<API_TOKEN du .env>
# 1. Upload
curl -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"path": "telegram_listener_v17_1.py", "content": "<contenu>"}' \
  http://38.247.138.124:8000/api/file
# 2. Redémarrer le bot (si listener) ou l'API (si bot_api.py)
curl -X POST -H "Authorization: Bearer $TOKEN" http://38.247.138.124:8000/api/bot/stop && sleep 2 && curl -X POST -H "Authorization: Bearer $TOKEN" http://38.247.138.124:8000/api/bot/start
# (ou /api/restart pour recharger bot_api.py)
# 3. Vérifier
curl -s -H "Authorization: Bearer $TOKEN" http://38.247.138.124:8000/api/status
```

⚠️ **Piège encodage** : l'upload transforme LF → CRLF. Le MD5 serveur se vérifie sur la version **CRLF** locale : `md5(fichier_lu_binaire.replace(b"\n", b"\r\n"))` — sinon faux négatif.

---

## 8. Repos GitHub

- **tgm** (bot) : `slh04ninja-cmyk/tgm` — branche `main`, dossier local `~/tgm/` (Termux)
- **CopyTrading** (app Android) : `slh04ninja-cmyk/CopyTrading` — build GitHub Actions → APK (`copytrading-debug`), dossier local `~/CopyTrading/`
- **CT** : `slh04ninja-cmyk/CT` — copie isolée (install.bat + wizard), app renommée CopyTrading2, dossier serveur `C:\TradingBot2\` — **OUBLIÉ, ne pas toucher**
- Token push : config git locale (`slh04ninja-cmyk`)

### Workflow modifications

1. **Toujours demander avant de modifier les codes** (proposer d'abord, jamais modifier sans validation)
2. Commit hashes + simulations/tests avant production ; push **après validation**
3. Code : modifier localement → `python3 -m py_compile` → tests (`test_tous_cas.py`, `test_zn.py`) → upload serveur → vérif MD5 CRLF → redémarrage → vérif `/api/status`
4. App Android : modifier Kotlin → push → `gh run watch` → download APK → `MEDIA:/data/.../CopyTrading.apk`

---

## 9. Tests

- `~/tgm/test_tous_cas.py` : **76 PASS / 0 FAIL** — couverture ZN/PU/MP/QA × BUY/SELL × dans/hors zone (61) + anti-doublon temps (5) + TP unifié (4) + parser SL/TP (6)
- `~/tgm/test_zn.py` : **45 PASS / 0 FAIL** (32 ZN + 13 PU/MP/QA)
- Vérifs systématiques : `py_compile`, `pyflakes` (0 undefined name), MD5 CRLF serveur = local

---

## 10. App Android (aperçu)

- **Stack** : Kotlin, XML Material3, OkHttp + Gson, dark theme `#0F0F1A`/`#1A1A2E`, accent `#6C63FF`, **pas d'emojis UI**
- **Panels** : Dashboard (P&L/floating/balance/winrate) · Performance (par canal/signal/session, expandable) · Positions (badges CH/signal/ordre + bouton TOUT FERMER) · Config (éditeur .env) · Logs
- **Build** : GitHub Actions → artifact `copytrading-debug` → `rm -f` l'ancien APK avant download (conflit zip)

---

## 11. Historique récent (commits v17.4h, branche main)

`4ed627e` doc QA_PRICE_TOLERANCE → `75b0f4b` code PU/MP/QA (zones strictes) → `8fcd39f` tests 61 cas → `9120b14` doc 4.4 → `376699c` fusion par TEMPS_DE_FUSION → `d3e0553` doc TP unifié → `9c48c39` TP unifié → `6834d83` doc P&L cible → `003bd4f` variables SL/TP (MAX_SL_USD/TP_FIXED) → `d8bae20` anti-doublon + suppression XLSX/merge_quick_alert → `845184f` doc sans section API → `b6c1ae0` MAX_TEMPS supprimé → `c06c120` restart auto config + utf-8 stdout.

---

## 12. Contraintes & conventions

- **Français** (réponses courtes), pas d'emojis dans l'UI des apps
- Logs bot : **sans accents** (REFUSE pas REFUSÉ), format unifié
- Demander avant toute modification ; commit hashes + tests d'abord ; push après validation
- Pas de SSH, pas de adb — tout passe par l'API REST ou le RDP
- Watchdog : cron Hermes « Watchdog bot 1 » (statut toutes les 2 min, alerte + relance auto)
- Contexte opérationnel détaillé : `~/.hermes/trading-bot-context.md` (à relire en début de session)
- Doc complète : `bot_documentation_v17.html` (11 sections, référence des règles)
