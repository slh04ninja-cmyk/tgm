# AI Context — Bot CopyTrading (tgm) v17.4h

> Contexte unique pour un agent AI reprenant ce projet. Fusion de CONTEXT.md + AI_AGENT_PROMPT.md + API_DOCUMENTATION.md, **mis à jour au 04/09/2026 (v17.4h + filtres MOVE/KER)**.
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

### Bot 2 — expérience A/B (RÉACTIVÉE le 03/09 — test live des filtres MOVE+KER)

- **Méthode actuelle** : le bot 1 forwarde les signaux détectés vers le **bot 2** (`C:\TradingBot2\`, **forward HTTP port 8001** — serveur intégré au listener, envoi-seul @TrdReport2, compte Trial **474496186 / Exness-MT5Trial15** (EXNESS 2), **55 canaux** via `C:\TradingBot\Channels.txt` (retraits du 04/09 : 85 → 71 → 59 → 55 — l'utilisateur supprime les canaux déficitaires dans les dossiers Telegram), mapping Channels.txt prioritaire). Le bot 2 suit automatiquement les canaux du bot 1 (forward) : pas de redémarrage nécessaire après un retrait côté bot 1.
- **But** : comparer (A/B) bot 1 (sans filtres) vs bot 2 (**avec filtres MOVE+KER** depuis le 04/09) sur le même flux de signaux.
- **Ancienne méthode (abandonnée)** : dossier `inbox` + variable `SIGNAL_FORWARD_DIR` (supprimée du .env et du code). Le forward passe maintenant par le port 8001.
- ⚠️ **Ne PAS utiliser `/api/bot/stop` ni `/api/bot/start` (bug constaté 04/09)** : l'API tracke un mauvais PID — `stop` a tué le **bot 2** au lieu du bot 1, `start` répond « already_running » sans rien lancer. Redémarrage fiable : `taskkill /F /PID <pid>` puis `Start-Process python C:\TradingBot2\telegram_listener_v17_1.py` (WorkingDirectory `C:\TradingBot2`). **Attendre ~6 s entre kill et relance** sinon crash au démarrage : `CRASH: database is locked` (session Telethon pas encore libérée). Log : `C:\TradingBot2\bot_trading.log`. Trouver le PID : `wmic process where "name='python.exe'" get ProcessId,CommandLine`.
- ⚠️ **Redémarrage après 17h UTC = renvoi des rapports en doublon** : la routine EOD (rapport quotidien + hebdo le vendredi) s'exécute à chaque démarrage après 17h → doublons sur TrdReport/TrdReport2 (normal, à ignorer).
- **Fix PDF hebdo bot 1 (04/09)** : un caractère hors latin-1 (emoji dans un nom de canal, ex. « 👑Jennifer GOLD👑 ») faisait échouer `_generate_weekly_report_pdf` (fpdf2/Helvetica) → rapport hebdo jamais envoyé. Correctif : monkey-patch `FPDF.cell`/`multi_cell` en début de fonction (assainissement latin-1 : accents conservés, le reste → `?`). **Rattrapage manuel** : créer `C:\TradingBot\SEND_WEEKLY.flag` puis redémarrer le bot 1 → le code (après « Telegram connecté ») envoie le hebdo et supprime le flag.

### FILTRAGE DES SIGNAUX — contrainte & campagne de backtest (03-04/09) — À LIRE AVANT TOUTE MODIF

**Contrainte utilisateur (04/09)** : le système (mean-reversion) est **rentable en range mais perdant en tendance forte** (03/09 : −300$ ; 04/09 : encore rouge). L'utilisateur **refuse la gestion du risque** (stop-loss journalier proposé, non retenu) → la solution = **FILTRER LES SIGNAUX D'ENTRÉE**. Toujours revalider une config positive sur une autre période (leçon KER : +209 sur 24-27/08 mais −119 sur 01-02/09 = artefact de régime).

**Remarque clé (analyse des deals réels 24/08-04/09)** :
- **La plupart des signaux PERDANTS sont ceux pris CONTRE la tendance en mouvement** (le bot achète les baisses / vend les hausses). Quand le mouvement est violent ou établi, le prix continue → **SL en cascade** : 82 cascades de ≥3 SL en ≤10 min, **489 SL = −4538$ cumulés** ; les grosses cascades (≥8 SL) sont précédées d'un move de 15-25$ en 15 min contre les positions.
- ⚠️ **MAIS en range / faible mouvement, les contre-tendances GAGNENT** → ne PAS bloquer toutes les contre-tendances (KER/ROC « classiques » = négatifs car ils refusaient des gagnants). Ne bloquer que si le mouvement est **violent** (≥ 8$/10 min) ou la tendance **établie** (KER long M10 élevé).

**Filtres testés** (méthode : EA évaluateur 0 trade → verdicts CSV → P&L via **deals MT5 réels**, 613 signaux 24/08-02/09, baseline réelle **+579.92$**) :

| Filtre | Résultat |
|---|---|
| KER seul (toutes variantes/timeframes) | négatif ou artefact de régime ❌ |
| ROC classique « bloquer contre-tendance » (0.2→0.6%) | négatif partout (−31 à −203$) ❌ |
| Volume ticks (somme 3 bougies ≥ 2× moyenne 50) | inopérant : 0 refus (CFD Exness sans volume réel ; tick volume non discriminant dans le testeur) ❌ |
| ROC 0.4% logique inversée (refuser le sens du move) | +46.48$ mais sur 01-02/09 seulement (jamais confirmé période complète) |
| **MOVE seul** (refuser si le prix a bougé ≥ X$ en Y min CONTRE le signal) | pic **10 min / 8$ = +145.60$** (38 refus tous perdants) ; 6$=+83, 10$=+105, 8min/8$=+109, 15min/12$=+115, 20min/12$=**−36** ❌ |
| **MOVE + KER (combo, OR)** | **+236.42$ (+41%)** — la meilleure config de toute la campagne |

**Balayage KER dans le combo (Move 10min/8$ fixé)** : Strong 0.3→+156 · 0.4→+222 · **0.45→+236 pic** · 0.5→+201 · 0.6→+168 ; Accel 0.3→+170 (0.35 meilleur) ; Short 4→−69 · **9→pic** · 10→+140 · 12→+110 ; Long 20→−121 · **40→pic** · 50→−61.

**✅ BONNES VALEURS (implémentées bot 2 le 04/09)** :
```
MOVE_FILTER_ENABLED=true  MOVE_FILTER_MIN=10  MOVE_FILTER_USD=8
KER_FILTER_ENABLED=true   KER_SHORT=9  KER_LONG=40  KER_STRONG=0.45  KER_ACCEL=0.35
```
Logique (OR, appliquée aux flux direct + FWD) : REFUSE si move ≥ 8$ en 10 min contre le signal, OU tendance M10 établie (KER long > 0.45 ou accélération court−long > 0.35) opposée au signal. Le **filtre TradingView a été supprimé** du bot 2 (blocs retirés + `TV_FILTER_ENABLED=false`). Variables dans `C:\TradingBot2\.env` (ajustables sans toucher au code) ; backup code : `telegram_listener_v17_1.py.bak_moveker`.

**EAs évaluateurs créés (hors repo — serveur `C:\TradingBot\` + copie locale `~/`)**
- `ea_backtest_signaux.mq5` : évaluateur KER/ROC/volume (0 trade, RAW 613 signaux inline 24/08-02/09)
- `ea_backtest_move.mq5` : évaluateur MOVE + KER (0 trade, même RAW) — utilisé pour le balayage final
- Verdicts CSV dans le dossier Files du testeur (`InpOutFile`) : `timestamp,action,ch,type,entry_mk,l1,l2,sl,Etat,motif` ; le P&L est calculé APRÈS via les **deals MT5 réels** (méthode deals-only : MK + L1/L2 rattachés au dernier MK du même canal/type ; sanity check : P&L recomposé = P&L réel exact). Script d'analyse : `C:\TradingBot\_ana_combo.py` (P&L par motif MOVE/KER).
- ⚠️ Pièges testeur MT5 : le **jour de fin de période est EXCLU** (fin au 03/09 pour couvrir le 02/09) ; un test avec une période plus courte que le RAW verdict les signaux hors période en « ACCEPTE » artefact (ne pas les analyser).

**Calcul EXACT des résultats par canal — méthode deals validée (04/09)** :
- Baseline réel vérifié directement dans les deals (EXNESS 1 262342460, 24/08 00:00 → 03/09 00:00) : **+579.92$** = 1422 positions CH (706 MK +117.90 ; 716 limites +462.02). P&L par jour : 24/08 +400.59 · 25/08 −114.86 · 26/08 +106.10 · 27/08 +92.05 · 28/08 +66.06 · 31/08 −95.06 · 01/09 +19.91 · 02/09 +105.26.
- ⚠️ **Piège double comptage** : grouper par signal du CSV (find_mk ±5 s) SURRÉVALUE le baseline (+667.35 au lieu de 579.92, +87.43) — 2 signaux proches du même canal peuvent matcher le même MK ou partager des limites. **Méthode correcte** (script `C:\TradingBot\_tab_exact.py`) : itérer sur **chaque position CH 1 seule fois** (sanity check : total = +579.92 exact) ; statut « REFUSE » = le MK (ou le dernier MK même canal/type avant une L) a un verdict REFUSE ; les L3/L4 hors-zone **sans MK** et les MK orphelins (sans verdict CSV) ne sont **jamais évalués** → ils restent dans le baseline des 2 scénarios et s'annulent dans le gain.
- **Gain des filtres (robuste — identique dans les 2 méthodes) : MOVE +145.60 + KER +90.82 = +236.42** → P&L global avec filtres = 579.92 + 236.42 = **+816.34** (+41%).

**Tableau par canal — filtres MOVE+KER (10min/8$ + 9/40/0.45/0.35), 24/08-02/09, baseline exact** (58 canaux avec ≥1 refus ; convention : baseline = P&L réel du canal ; **move/ker = EFFET du filtre** (+ = améliore en refusant des perdants, − = dégrade en refusant des gagnants) ; **total = move + ker** ; **écart = baseline + total** = P&L du canal avec filtres) :

```
canal   baseline     move      ker    total     ecart
CH103     -93.10    -0.00   +79.32   +79.32    -13.78
CH8       -56.03   +33.34   +25.21   +58.55     +2.52
CH30      -56.11   +23.40   +32.71   +56.11     +0.00
CH94      -50.90   +35.02   +19.91   +54.93     +4.03
CH99      +36.54   +26.31   +27.99   +54.30    +90.84
CH15      -47.28   +50.26   -10.50   +39.76     -7.52
CH26      -14.54    -0.00   +38.93   +38.93    +24.39
CH81      -32.65   +26.79    +8.45   +35.24     +2.59
CH2       -23.92   +29.43    -0.00   +29.43     +5.51
CH3       -25.99   +29.31    -0.00   +29.31     +3.32
CH93      -48.74    -0.00   +27.21   +27.21    -21.53
CH73       +0.93   +27.00    -0.00   +27.00    +27.93
CH58      +22.06    -0.00   +27.00   +27.00    +49.06
CH79      -82.85    -0.00   +26.71   +26.71    -56.14
CH61      -54.70    -0.00   +23.79   +23.79    -30.91
CH64      -22.82    -0.00   +22.82   +22.82     +0.00
CH5       -83.81   -21.00   +39.02   +18.02    -65.79
CH106     +16.95   +26.45   -10.45   +16.00    +32.95
CH29      -20.54    -0.00   +13.59   +13.59     -6.95
CH65      -21.27   +26.93   -14.01   +12.92     -8.35
CH109      +7.74   +12.79    -0.00   +12.79    +20.53
CH4        -5.22    -0.00    +7.54    +7.54     +2.32
CH102    +120.78    -0.00    +7.22    +7.22   +128.00
CH1       +14.09   -14.08   +21.00    +6.92    +21.01
CH21       +7.52    +0.20    -0.00    +0.20     +7.72
CH87     +108.51    -6.69    -0.00    -6.69   +101.82
CH39      +62.99    -0.00    -6.89    -6.89    +56.10
CH24      +50.57    -0.00    -6.92    -6.92    +43.65
CH38      +17.49    -0.00    -6.99    -6.99    +10.50
CH66      -25.62    -0.00    -7.00    -7.00    -32.62
CH55      +38.21    -0.00    -7.00    -7.00    +31.21
CH70      +38.97    -7.00    -0.00    -7.00    +31.97
CH17      +38.98    -0.00    -7.00    -7.00    +31.98
CH74       +7.09    -7.09    -0.00    -7.09     +0.00
CH22      +19.40    -0.00    -8.90    -8.90    +10.50
CH27      +24.59    -0.00   -10.48   -10.48    +14.11
CH67      +19.42    -0.00   -10.49   -10.49     +8.93
CH75      +30.94   -10.49    -0.00   -10.49    +20.45
CH11      +20.51    -0.00   -10.50   -10.50    +10.01
CH62      +31.92   -10.50    -0.00   -10.50    +21.42
CH13      -17.06    -0.00   -10.63   -10.63    -27.69
CH85      +38.78   -10.64    -0.00   -10.64    +28.14
CH60      +11.88   -10.71    -0.00   -10.71     +1.17
CH45      +28.39    -0.00   -13.99   -13.99    +14.40
CH53       +1.22    -0.00   -14.01   -14.01    -12.79
CH31      +38.61    -0.00   -14.11   -14.11    +24.50
CH91      +60.32   -14.32    -0.00   -14.32    +46.00
CH90      -75.17   -14.39    -0.00   -14.39    -89.56
CH107     +70.45    -0.00   -14.47   -14.47    +55.98
CH18       -4.49   -14.51    -0.00   -14.51    -19.00
CH42      +15.83    -0.00   -17.50   -17.50     -1.67
CH72      +39.27    -0.00   -21.44   -21.44    +17.83
CH92      +66.85   -18.53    -5.22   -23.75    +43.10
CH82      +38.42    -0.00   -24.55   -24.55    +13.87
CH6       +26.70    -0.00   -24.76   -24.76     +1.94
CH88     +124.10   -13.68   -20.43   -34.11    +89.99
CH110     +49.41   -28.00   -10.42   -38.42    +10.99
CH105    +175.76    -0.00   -48.94   -48.94   +126.82
TOTAL     +579.92  +145.60   +90.82  +236.42   +816.34
```

Lecture : les filtres aident surtout les canaux **perdants** (CH103 −93→−14, CH8 −56→+2.5, CH30, CH94, CH15) ; ils dégradent quelques gros gagnants (CH105 +176→+127, CH110, CH88, CH92, CH82) — net +236.42. 224 positions marquées REFUSE au total (38 MOVE + 65 KER × MK + leurs L).

### MÉTHODE BACKTEST & CALCUL DES FILTRES — protocole complet (à lire avant TOUTE analyse de résultats)

**1. L'EA évaluateur (0 trade)**
- L'EA ne passe **aucun ordre** : il reçoit chaque signal du RAW inline, applique les filtres (inputs `InpUseMove`, `InpMoveWin`, `InpMoveUSD`, `InpUseKER`, `InpKERShort/Long/Strong/Accel`, `InpOutFile`…), et écrit **1 ligne CSV par signal** : `timestamp,action,ch,type,entry_mk,l1,l2,sl,Etat,motif` (`Etat` = ACCEPTE/REFUSE, `motif` = MOVE CONTRE / KER CONTRE).
- Le P&L n'est **jamais** calculé par l'EA : il est calculé APRÈS sur les **deals réels** (le testeur n'a ni les mêmes ticks ni le même volume que le réel).
- Fichiers : `ea_backtest_signaux.mq5` (campagne KER/ROC/volume) et `ea_backtest_move.mq5` (MOVE+KER, RAW 613 signaux inline, config du pic) — hors repo, synchronisés `~/` ↔ `C:\TradingBot\` ↔ `MQL5\Experts`.

**2. Ajout des signaux dans l'EA (RAW inline)**
- Le **RAW** = les vrais signaux de la période (timestamp, canal CHx, type PU/ZN/MP/QA/AL, prix), copiés **en dur** dans le .mq5 sous forme de tableau. Un script Python d'assemblage génère le fichier (attention : ne pas déclarer 2× le tableau RAW → erreur de compile).
- RAW actuel : **613 signaux du 24/08-02/09**. Pour évaluer le 03-04/09 → régénérer un RAW étendu (signaux jusqu'au 04/09) et recompiler.

**3. Compilation (`~/compile_ea.py`)**
- Copie le `.mq5` (C:\TradingBot\) → `MQL5\Experts` du terminal **53785E099C927DB68A545C249CDBCE06** (EXNESS 1) → lance `metaeditor64.exe /compile:<fichier> /log:...` → vérifier **0 erreur / 0 warning** dans le log (`C:\TradingBot\ea_compile.log`). Le `.ex5` (taille ~45-57 Ko) apparaît à côté.

**4. Run dans le Strategy Tester (exécuté par l'UTILISATEUR — jamais par l'agent)**
- L'utilisateur configure : EA, symbol XAUUSDm, mode ticks réels, **période** (ex. 24/08 → 03/09 pour couvrir le 02/09), inputs (config exacte), `InpOutFile=verdicts_move.csv` → lance le run → dit « c'est fait ».
- Sortie : `C:\Users\Administrator\AppData\Roaming\MetaQuotes\Tester\53785E099C927DB68A545C249CDBCE06\Agent-127.0.0.1-3000\MQL5\Files\verdicts_move.csv`.
- ⚠️ **Chaque run ÉCRASE le CSV** → si le CSV du pic compte (614 lignes), ne pas relancer sans l'avoir préservé. Vérifier la config réellement chargée dans le log du testeur (`…\logs\20260904.log`, encodage UTF-16-le).
- ⚠️ Pièges testeur : le **jour de fin de période est EXCLU** (fin au 03/09 → couvre le 02/09) ; période plus courte que le RAW → signaux hors période classés ACCEPTE d'office (**artefact, ne pas analyser** — c'est ce qui a faussé les runs nocturnes du 01-02/09) ; tick volume du testeur ≠ réel (3× plus faible, filtre volume inexploitable sur CFD Exness).

**5. Calcul du P&L (méthode deals-only EXACTE)**
- Connexion : toujours **expliciter** `C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe` + login 262342460 (le terminal par défaut peut être le Trial du bot 2 → résultats faux silencieusement).
- `history_deals_get(FROM, TO)` avec **les 2 bornes** (1 seule borne → 0 résultat).
- Baseline = P&L de **toutes les positions CH** (commentaire `CHx-…-MK`/`L1..L4`) fermées dans la fenêtre : P&L d'une position = somme de ses DEAL OUT. Référence vérifiée : 24/08-02/09 = **1422 positions = +579.92$** (sanity : la somme = P&L réel du compte, exact).
- Scripts serveur : `_baseline_check.py` (P&L réel + par jour), `_tab_exact.py` (tableau par canal exact), `_stats_v3/v4.py` (rapports par canal).
- **Chaque position compte EXACTEMENT 1× → sanity check obligatoire** : le total doit retomber sur +579.92 (24/08-02/09). Sinon le rattachement est faux.

**6. Matching positions ↔ verdicts (le piège principal)**
- Les signaux `REFUSE` du CSV ont **vraiment été exécutés** par le bot réel (il n'avait pas les filtres) → leur MK **existe** dans les deals. Le CSV dit simplement ce que les filtres *auraient* refusé (scénario contrefactuel).
- Rattachement **CORRECT** (position par position, script `_tab_exact.py`) :
  - **MK** : verdict du CSV à ±5 s (même canal CHx + même type) ;
  - **L1-L4** : verdict du **dernier MK du même (canal, type) ouvert AVANT la L** (les limites appartiennent au groupe de leur MK) ;
  - **L3/L4 sans MK** (ZN hors-zone) et **MK orphelins** (sans verdict) : jamais évalués → présents dans le baseline des 2 scénarios → s'annulent dans le gain.
- Effet d'un filtre = −(P&L réel des positions des signaux REFUSE). Convention des tableaux : `move`/`ker` = **effet** (+ = le filtre améliore en refusant des perdants, − = dégrade en refusant des gagnants) ; `total = move + ker` ; `écart = baseline + total` = P&L avec filtres. Run pic (MOVE 10min/8$ + KER 9/40/0.45/0.35) : **MOVE +145.60 (38 refus) + KER +90.82 (65 refus) = +236.42** → P&L filtré = 579.92 + 236.42 = **+816.34**.
- ⚠️ **Erreur documentée (05/09, autre agent)** : matcher « par signal/canal/jour » ou grouper les positions par signal (±5 s) fait **rater des refus** (68/103 trouvés → gain faux +66.31 au lieu de +236.42) et **double-compter le baseline** (+667.35 au lieu de +579.92, +87.43). Toujours repartir des **positions réelles**, chacune 1×.

**7. Comparaison**
- **Backtest** : baseline (sans filtres) vs baseline − refusés (avec filtres) = gain du filtre sur la période.
- **A/B live** : bot 1 (sans filtres) et bot 2 (filtres MOVE+KER) reçoivent le même flux (forward port 8001) → comparer le P&L réel sur la même fenêtre (ex. 04/09 12:34→15:00 : bot 2 **+43.34** vs bot 1 **−102.89**).
- **Revalidation obligatoire** : toute config positive doit être re-testée sur une autre période (leçon KER : +209 sur 24-27/08 était un artefact de régime 01-02/09).

**8. Limites connues**
- Le scénario contrefactuel suppose que refuser un signal n'affecte pas les autres (pas d'effet de portefeuille/corrélation entre canaux).
- Le CSV actuel s'arrête au 02/09 → l'effet des filtres sur le **03-04/09** (tendance forte, −665.78$ en réel) **reste à mesurer** : il faut un run avec RAW étendu jusqu'au 04/09 (période 24/08 → 05/09).
- La semaine 24-28/08 était range/gagnante : les filtres y dégradent (−97.44$) ; leur gain vient des journées de tendance et des canaux retirés.

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
