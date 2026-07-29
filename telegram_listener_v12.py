"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 12.0.0 — Code cleanup & bug fixes
=============================================================
MODIFICATIONS v12.0.0 (depuis v11.1.1) :

■ BUGS CORRIGÉS :
- [CRITIQUE] NameError: variable `orders` non définie dans execute_signal()
- Crash TypeError: entry_price=None dans le filtre distance TP (Quick Alert)
- P&L=0 après fermeture dans _close_all_positions() (manque délai MT5)
- Doublons alertes Telegram: dédup basée sur hash() Python non-deterministe
- Doublons handler: clear() brutal du set _seen_msg_ids à 2000 entrées

■ CODE MORT SUPPRIMÉ (-452 lignes) :
- Méthodes: place_limit_order, modify_pending_order, update_pending_orders_sl,
  _cancel_pending_orders_for_entry, _get_tp_trigger, _check_pending_only_expiry,
  _resolve_order
- Boucles mortes: pending orders dans _check_all(), check_conflict(),
  NewsManager._close_all()
- Config morte: SL_PRIX_UNIQUE, expiry, orders dans entry dicts
- bot_messages.py: 17 fonctions mortes supprimées

■ DÉDUPLICATION REFAITE :
- _seen_msg_ids: dict avec TTL au lieu de set avec clear()
- _alert_dedup_cache: contenu normalisé (sans 'Canal:') au lieu de hash()
- TTL augmenté de 10s → 30s, eviction FIFO bornée

■ ARCHITECTURE (inchangée depuis v11) :
- Un seul MARKET par signal (SINGLE_POSITION_MODE)
- Signaux zone : ZN1/ZN2 → MARKET
- Prix Unique : PU1/PU2 → MARKET
- Quick Alert : AL-MP uniquement
- Fusion : SL/TP mis à jour sur QA existant
- BE : SL @ entry ± BE_USD + TP @ entry ± TP_FIXED_GAIN_USD
=============================================================
"""

import subprocess, sys
_deps = {"dotenv": "python-dotenv", "telethon": "telethon", "MetaTrader5": "MetaTrader5"}
for _mod, _pkg in _deps.items():
    try:
        __import__(_mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import asyncio
import re
import unicodedata
import logging
import time
import json
import urllib.request
import os
import threading
import ssl
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# =============================================================
# ★ FIX CRITIQUE : désactiver le "QuickEdit Mode" de la console Windows
# =============================================================
# Sur Windows, si quelqu'un clique/sélectionne du texte dans la fenêtre du
# terminal (même par erreur via RDP), Windows SUSPEND tout le processus en
# attendant Entrée/Échap — y compris la boucle asyncio (signaux, prix, BE,
# TP-FIXED...). C'est la cause typique d'un bot qui "se réveille" seulement
# après avoir tapé Entrée dans la console. On désactive ce mode au démarrage
# pour ne plus en dépendre (survit aussi aux redémarrages du serveur).
def _disable_quickedit_mode():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass  # pas de console attachée (ex: lancé en service/tâche planifiée) → sans effet, sans risque

_disable_quickedit_mode()

from telethon import TelegramClient, events
import MetaTrader5 as mt5

sys.stdout.reconfigure(line_buffering=True)

# Constantes
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
ORDER_FILLING_RETURN = 0
ORDER_FILLING_FOK = 1
ORDER_FILLING_IOC = 2

load_dotenv()

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
CHANNEL_NAME = os.getenv("TG_CHANNEL_1", os.getenv("TG_CHANNEL", ""))
CHANNEL_NAME_2 = os.getenv("TG_CHANNEL_2", "")
CHANNEL_NAME_3 = os.getenv("TG_CHANNEL_3", "")
CHANNEL_NAME_4 = os.getenv("TG_CHANNEL_4", "")
CHANNEL_NAME_5 = os.getenv("TG_CHANNEL_5", "")
CHANNEL_NAME_6 = os.getenv("TG_CHANNEL_6", "")
CHANNEL_NAME_7 = os.getenv("TG_CHANNEL_7", "")
CHANNEL_NAME_8 = os.getenv("TG_CHANNEL_8", "")
CHANNEL_NAME_9 = os.getenv("TG_CHANNEL_9", "")
CHANNEL_NAME_10 = os.getenv("TG_CHANNEL_10", "")
CHANNEL_NAME_11 = os.getenv("TG_CHANNEL_11", "")
CHANNEL_NAME_12 = os.getenv("TG_CHANNEL_12", "")
CHANNEL_NAME_13 = os.getenv("TG_CHANNEL_13", "")
CHANNEL_NAME_14 = os.getenv("TG_CHANNEL_14", "")
CHANNEL_NAME_15 = os.getenv("TG_CHANNEL_15", "")
CHANNEL_NAME_16 = os.getenv("TG_CHANNEL_16", "")
CHANNEL_NAME_17 = os.getenv("TG_CHANNEL_17", "")
CHANNEL_NAME_18 = os.getenv("TG_CHANNEL_18", "")
CHANNEL_NAME_19 = os.getenv("TG_CHANNEL_19", "")

# Mapping canal → numéro (TOUS les canaux 1-19)
CHANNEL_NUM_MAP = {}
_ALL_CHANNEL_NAMES = [
    CHANNEL_NAME, CHANNEL_NAME_2, CHANNEL_NAME_3, CHANNEL_NAME_4, CHANNEL_NAME_5,
    CHANNEL_NAME_6, CHANNEL_NAME_7, CHANNEL_NAME_8, CHANNEL_NAME_9, CHANNEL_NAME_10,
    CHANNEL_NAME_11, CHANNEL_NAME_12, CHANNEL_NAME_13, CHANNEL_NAME_14, CHANNEL_NAME_15,
    CHANNEL_NAME_16, CHANNEL_NAME_17, CHANNEL_NAME_18, CHANNEL_NAME_19,
]
for _i, _name in enumerate(_ALL_CHANNEL_NAMES, 1):
    if _name:
        CHANNEL_NUM_MAP[_name] = _i
        if _name.lstrip("-").isdigit():
            CHANNEL_NUM_MAP[_name.lstrip("-")] = _i

MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "")
MT5_PATH     = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20250226"))
SLIPPAGE = int(os.getenv("SLIPPAGE", "20"))
ORDER_EXPIRY_MIN = int(os.getenv("ORDER_EXPIRY_MINUTES", "240"))
LOT_SIZE = float(os.getenv("LOT_TOTAL", "0.01"))
LOT_UNIQUE_TRADE = float(os.getenv("LOT_UNIQUE_TRADE", "0.01"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_SPREAD_POINTS = float(os.getenv("MAX_SPREAD_POINTS", "50"))

# === GAIN FIXE ===
TP_FIXED_ENABLED = os.getenv("TP_FIXED_ENABLED", "true").lower() == "true"
TP_FIXED_GAIN_USD = float(os.getenv("TP_FIXED_GAIN_USD", "15.0"))
# ★ BE_USD : quand le BE se déclenche, le SL n'est plus posé exactement à l'entrée
# mais avec une petite marge de sécurité (BE_USD $) du côté DÉFAVORABLE — évite un
# stop sur un simple retour à l'entrée (bruit/spread). Ex: BUY @4000, BE_USD=3 → SL=3997.
BE_USD = float(os.getenv("BE_USD", "3"))
PNL_TRIGGER_USD = float(os.getenv("PNL_TRIGGER_USD", "8.0"))

# === FILTRES ===
TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "3"))
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "20"))
DAILY_PROFIT_LIMIT = float(os.getenv("DAILY_PROFIT_LIMIT", "30.0"))

# =============================================================
# CONFIG TIMESFM
# =============================================================
TIMESFM_ENABLED         = os.getenv("TIMESFM_ENABLED", "true").lower() == "true"
TIMESFM_TIMEFRAME       = os.getenv("TIMESFM_TIMEFRAME", "M5")   # M1, M5, M15, M30, H1
TIMESFM_CONTEXT_BARS    = int(os.getenv("TIMESFM_CONTEXT_BARS", "256"))
TIMESFM_HORIZON         = int(os.getenv("TIMESFM_HORIZON", "12"))
TIMESFM_MIN_MOVE_PIPS   = float(os.getenv("TIMESFM_MIN_MOVE_PIPS", "5.0"))
TIMESFM_MIN_CONFIDENCE  = float(os.getenv("TIMESFM_MIN_CONFIDENCE", "0.35"))
TIMESFM_SYMBOL          = os.getenv("TIMESFM_SYMBOL", "XAUUSDm")  # symbole MT5 exact

# === CACHE TTL ===


# === HEARTBEAT ===


# === PARAMÈTRES SL (définis dans .env) ===
FUSION_TOLERANCE = float(os.getenv("FUSION_TOLERANCE", "3"))
CONFLIT_FILTER_ENABLED = os.getenv("CONFLIT_FILTER_ENABLED", "true").lower() == "true"
# ★ MODE POSITION UNIQUE : convertit les signaux zone (2 positions) en MARKET seul,
# et désactive le merge QA+Fusion. Seul le Quick Alert est exécuté.

# === AUTRES ===
TG_ALERT_CHANNEL = os.getenv("TG_ALERT_CHANNEL", "")
ACTIVE_GRADE = os.getenv("ACTIVE_GRADE", "false").lower() == "true"
NUM_CHANEL_GRADE = int(os.getenv("NUM_CHANEL_GRADE", "0"))
REVERSE_PRICE = float(os.getenv("REVERSE_PRICE", "2.0"))
REALISED_GRADE = float(os.getenv("REALISED_GRADE", "3.0"))
MAX_GRADE_POSITIONS = int(os.getenv("MAX_GRADE_POSITIONS", "10"))
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
NEWS_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "false").lower() == "true"
# Fenêtres par défaut (tier "High" standard : Jobless Claims, Philly Fed, Consumer Confidence, etc.)
NEWS_BLOCK_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK", "15"))
NEWS_CLOSE_MIN = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE", "5"))
NEWS_AFTER_MIN = int(os.getenv("NEWS_WINDOW_AFTER", "15"))
# Tier NFP / CPI
NEWS_BLOCK_MIN_NFPCPI = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK_NFPCPI", "20"))
NEWS_CLOSE_MIN_NFPCPI = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE_NFPCPI", "10"))
NEWS_AFTER_MIN_NFPCPI = int(os.getenv("NEWS_WINDOW_AFTER_NFPCPI", "30"))
# Tier FOMC
NEWS_BLOCK_MIN_FOMC = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK_FOMC", "20"))
NEWS_CLOSE_MIN_FOMC = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE_FOMC", "15"))
NEWS_AFTER_MIN_FOMC = int(os.getenv("NEWS_WINDOW_AFTER_FOMC", "45"))
# Tier "spike" (PCE, PPI, GDP, ADP, ISM PMI, Retail Sales, Unemployment Rate...)
NEWS_BLOCK_MIN_SPIKE = int(os.getenv("NEWS_WINDOW_BEFORE_BLOCK_SPIKE", "20"))
NEWS_CLOSE_MIN_SPIKE = int(os.getenv("NEWS_WINDOW_BEFORE_CLOSE_SPIKE", "10"))
NEWS_AFTER_MIN_SPIKE = int(os.getenv("NEWS_WINDOW_AFTER_SPIKE", "20"))
# Niveau d'impact minimum pour filtrer les news ("high" ou "medium")
NEWS_MIN_IMPACT = os.getenv("NEWS_MIN_IMPACT", "high").lower()

# Mots-clés (titre en minuscules) pour classer chaque news dans le bon tier
_NEWS_KEYWORDS_NFPCPI = [
    "non-farm payrolls", "nonfarm payrolls", "non farm payrolls", "nfp",
    "cpi", "consumer price index",
]
_NEWS_KEYWORDS_FOMC = [
    "fomc", "federal funds rate", "fed interest rate", "interest rate decision",
    "powell", "federal reserve",
]
_NEWS_KEYWORDS_SPIKE = [
    "pce price index", "core pce", "personal consumption expenditures",
    "ppi", "producer price index",
    "gdp",
    "unemployment rate",
    "adp non-farm", "adp employment", "adp non farm",
    "ism manufacturing", "ism services",
    "retail sales",
]

def _get_news_window(title: str) -> tuple:
    """Retourne (block_min, close_min, after_min) selon le tier de la news."""
    t = (title or "").lower()
    if any(k in t for k in _NEWS_KEYWORDS_NFPCPI):
        return NEWS_BLOCK_MIN_NFPCPI, NEWS_CLOSE_MIN_NFPCPI, NEWS_AFTER_MIN_NFPCPI
    if any(k in t for k in _NEWS_KEYWORDS_FOMC):
        return NEWS_BLOCK_MIN_FOMC, NEWS_CLOSE_MIN_FOMC, NEWS_AFTER_MIN_FOMC
    if any(k in t for k in _NEWS_KEYWORDS_SPIKE):
        return NEWS_BLOCK_MIN_SPIKE, NEWS_CLOSE_MIN_SPIKE, NEWS_AFTER_MIN_SPIKE
    return NEWS_BLOCK_MIN, NEWS_CLOSE_MIN, NEWS_AFTER_MIN

POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "1"))
RUNTIME_MINUTES = int(os.getenv("RUNTIME_MINUTES", "0"))

START_TIME = datetime.now(timezone.utc)

# =============================================================
# LOGGING
# =============================================================
class OrderFilter(logging.Filter):
    HIDE = ["[SPAM]", "[CYCLE]"]
    def filter(self, record):
        msg = record.getMessage()
        for tag in self.HIDE:
            if tag in msg:
                return False
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot_trading.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

for handler in log.handlers[:]:
    if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
        log.removeHandler(handler)
flush_handler = FlushStreamHandler(sys.stdout)
flush_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
flush_handler.addFilter(OrderFilter())
log.addHandler(flush_handler)

# =============================================================
# HELPERS
# =============================================================
def get_trading_day_start() -> datetime:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=TRADING_START_HOUR, minute=0, second=0, microsecond=0)
    if now.hour < TRADING_START_HOUR:
        start = start - timedelta(days=1)
    return start

def in_blocked_window() -> tuple[bool, str]:
    if not TIME_FILTER_ENABLED:
        return False, ""
    now = datetime.now(timezone.utc)
    if TRADING_START_HOUR <= now.hour < TRADING_END_HOUR:
        return False, ""
    return True, f"Hors plage {TRADING_START_HOUR}h-{TRADING_END_HOUR}h UTC"

# =============================================================
# TELEGRAM ALERTS
# =============================================================
_alert_client = None
_main_loop = None

# ★ DÉDUPLICATION DES ALERTES : éviter d'envoyer 2x le même message
# Utilise un set borné (deque) au lieu d'un dict + clear() pour éviter les fuites
from collections import deque
_alert_dedup_cache: dict = {}  # content_key -> timestamp
_alert_dedup_order: deque = deque()  # ordre FIFO des clés pour eviction
_ALERT_DEDUP_TTL = 30.0  # secondes (augmenté de 10→30)
_ALERT_DEDUP_MAX = 500

def _make_alert_key(message: str) -> str:
    """Clé de dédup basée sur le contenu normalisé (pas hash() Python).
    Supprime le nom du canal pour capter les doublons canal+discussion group."""
    # Supprime espaces superflus
    normalized = ' '.join(message.split())
    # Supprime la ligne 'Canal: ...' pour que le même signal venant du canal
    # et du groupe de discussion ait la même clé
    normalized = re.sub(r'\nCanal:.*$', '', normalized)
    return normalized

def send_alert_sync(message: str, _retries: int = 2):
    """Envoie non-bloquant avec retry automatique.
    - 1er essai : timeout 8s
    - Si échec : 1 retry après 2s (connexion Telegram instable)
    - Ne bloque JAMAIS la boucle de trading plus de 10s total
    - Dédup: ignore les messages identiques envoyés dans les 30 dernières secondes"""
    if not TG_ALERT_CHANNEL or not _alert_client or not _main_loop:
        return
    # --- Déduplication envoi ---
    now_ts = time.time()
    alert_key = _make_alert_key(message)
    # Eviction des entrées expirées (FIFO borné, pas de clear() total)
    while _alert_dedup_order:
        oldest_key = _alert_dedup_order[0]
        if now_ts - _alert_dedup_cache.get(oldest_key, 0) > _ALERT_DEDUP_TTL:
            _alert_dedup_order.popleft()
            _alert_dedup_cache.pop(oldest_key, None)
        else:
            break
    # Eviction par taille max
    while len(_alert_dedup_order) > _ALERT_DEDUP_MAX:
        old_key = _alert_dedup_order.popleft()
        _alert_dedup_cache.pop(old_key, None)
    if alert_key in _alert_dedup_cache:
        log.debug(f"[ALERT-DEDUP] Message déjà envoyé récemment → ignoré")
        return
    _alert_dedup_cache[alert_key] = now_ts
    _alert_dedup_order.append(alert_key)
    # --- Fin dédup ---
    for attempt in range(_retries):
        try:
            coro = _alert_client.send_message(TG_ALERT_CHANNEL, message)
            future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
            future.result(timeout=8)
            return  # ✅ succès
        except TimeoutError:
            if attempt < _retries - 1:
                # ★ FIX doublon : le timeout est côté CLIENT — le message a pu être
                # livré côté serveur malgré tout. On vérifie l'historique récent avant
                # de retenter, sinon un retry aveugle envoie un vrai second message
                # identique (Telegram n'a aucune notion d'idempotence côté serveur).
                already_sent = False
                try:
                    check_coro = _alert_client.get_messages(TG_ALERT_CHANNEL, limit=5)
                    check_future = asyncio.run_coroutine_threadsafe(check_coro, _main_loop)
                    recent = check_future.result(timeout=5)
                    already_sent = any((m.text or "") == message for m in recent)
                except Exception:
                    pass  # vérification impossible → on retente quand même (par sécurité)
                if already_sent:
                    log.info(f"[ALERT] Message déjà livré malgré le timeout → pas de retry")
                    return
                log.info(f"[ALERT] Timeout tentative {attempt+1}, retry dans 2s...")
                time.sleep(2)
            else:
                log.warning(f"[ALERT] Timeout envoi alerte Telegram ({_retries} tentatives)")
        except Exception as e:
            log.warning(f"[ALERT] Erreur envoi alerte Telegram: {type(e).__name__}: {e}")
            return  # erreur non-récupérable, pas de retry

# =============================================================
# PERFORMANCE TRACKER
# =============================================================
class PerformanceTracker:
    def __init__(self):
        self._trades_cache = []
        self._report_sent = False

    def log_trade_open(self, entry):
        sig = entry["signal"]
        now = datetime.now(timezone.utc)
        row = {
            "canal": sig.get("source_channel", "Inconnu"),
            "symbol": sig["symbol"],
            "action": sig["action"],
            "result": "OPEN",
            "pnl": 0.0,
            "duree_min": 0,
            "_entry_time": now,
            "_entry": entry,
        }
        self._trades_cache.append(row)

    def log_trade_close(self, entry, total_pnl):
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        now = datetime.now(timezone.utc)
        result = "WIN" if total_pnl > 0 else ("BE" if total_pnl == 0 else "LOSS")
        for t in reversed(self._trades_cache):
            if (t["canal"] == canal and
                t["symbol"] == sig["symbol"] and
                t["action"] == sig["action"] and
                t["result"] == "OPEN"):
                entry_time = t.get("_entry_time", now)
                duree = (now - entry_time).total_seconds() / 60
                t["result"] = result
                t["pnl"] = round(total_pnl, 2)
                t["duree_min"] = round(duree, 1)
                break

    def format_session_summary(self) -> str:
        if not self._trades_cache:
            return "📊 Aucun trade cette session."
        wins = sum(1 for t in self._trades_cache if t["result"] == "WIN")
        losses = sum(1 for t in self._trades_cache if t["result"] == "LOSS")
        be = sum(1 for t in self._trades_cache if t["result"] == "BE")
        still_open = sum(1 for t in self._trades_cache if t["result"] == "OPEN")
        total_pnl = sum(t["pnl"] for t in self._trades_cache)
        lines = [
            "📊 RÉSUMÉ SESSION",
            "━━━━━━━━━━━━━━━━━━",
            f"✅ Wins : {wins}",
            f"❌ Losses : {losses}",
            f"⬜ Breakeven : {be}",
            f"🔵 Ouverts : {still_open}",
            f"💰 P&L session : {total_pnl:+.2f}$",
        ]
        return "\n".join(lines)

    def print_final_report(self):
        if self._report_sent:
            return
        self._report_sent = True
        log.info("<<<<< INFO >>>>> Rapport final :")
        summary = self.format_session_summary()
        for line in summary.split("\n"):
            log.info(f"<<<<< INFO >>>>> {line}")

# =============================================================
# TIMESFM VALIDATOR
# =============================================================
_TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
}

class TimesFMValidator:
    """
    Valide la direction d'un signal Telegram en comparant
    avec la prévision TimesFM sur l'historique MT5.

    Nécessite : pip install timesfm[torch]
    Modèle utilisé : TimesFM 2.5 (200M paramètres, google/timesfm-2.5-200m-pytorch)
    """

    def __init__(self):
        self._model   = None
        self._ready   = False
        self._loading = False
        self._lock    = threading.Lock()

        if TIMESFM_ENABLED:
            # Chargement en arrière-plan pour ne pas bloquer le démarrage
            t = threading.Thread(target=self._load_model, daemon=True)
            t.start()

    def _load_model(self):
        with self._lock:
            if self._ready or self._loading:
                return
            self._loading = True
        try:
            log.info("[TIMESFM] Chargement du modèle TimesFM 2.5 …")
            # Installation automatique si absent
            try:
                import timesfm  # noqa: F401
            except ImportError:
                log.info("[TIMESFM] Installation de timesfm[torch] …")
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "timesfm[torch]", "-q"]
                )

            import timesfm
            import torch

            torch.set_float32_matmul_precision("high")

            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                "google/timesfm-2.5-200m-pytorch"
            )
            model.compile(
                timesfm.ForecastConfig(
                    max_context=TIMESFM_CONTEXT_BARS,
                    max_horizon=TIMESFM_HORIZON,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                )
            )
            with self._lock:
                self._model  = model
                self._ready  = True
                self._loading = False
            log.info("[TIMESFM] ✅ Modèle chargé et prêt.")
        except Exception as e:
            with self._lock:
                self._loading = False
            log.warning(f"[TIMESFM] ⚠️  Impossible de charger le modèle : {e}")
            log.warning("[TIMESFM] Le bot fonctionnera sans validation TimesFM.")

    # ----------------------------------------------------------
    def _get_closes(self) -> list[float] | None:
        """Récupère les cours de clôture depuis MT5."""
        tf_key = TIMESFM_TIMEFRAME.upper()
        tf     = _TF_MAP.get(tf_key, mt5.TIMEFRAME_M5)
        symbol = TIMESFM_SYMBOL

        # Vérifier / activer le symbole
        info = mt5.symbol_info(symbol)
        if info is None:
            log.warning(f"[TIMESFM] Symbole {symbol} introuvable dans MT5")
            return None
        if not info.visible:
            mt5.symbol_select(symbol, True)
            time.sleep(0.3)

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, TIMESFM_CONTEXT_BARS)
        if rates is None or len(rates) == 0:
            log.warning(f"[TIMESFM] Aucune donnée pour {symbol} {tf_key}")
            return None

        import numpy as np
        closes = np.array([r[4] for r in rates], dtype=float)  # index 4 = close
        return closes.tolist()

    # ----------------------------------------------------------
    def validate(self, signal_direction: str) -> dict:
        """
        Valide un signal.

        Retourne :
            {
                "valid":               bool,
                "reason":              str,
                "predicted_direction": str,
                "confidence":          float,
                "predicted_move_pips": float,
            }
        """
        _pass = {
            "valid": True,
            "reason": "TimesFM désactivé ou non prêt",
            "predicted_direction": signal_direction,
            "confidence": 0.0,
            "predicted_move_pips": 0.0,
        }

        if not TIMESFM_ENABLED:
            return _pass

        with self._lock:
            ready = self._ready
            model = self._model

        if not ready or model is None:
            log.info("[TIMESFM] Modèle non prêt → signal accepté sans validation")
            return _pass

        try:
            import numpy as np

            closes = self._get_closes()
            if closes is None or len(closes) < 32:
                log.warning("[TIMESFM] Historique insuffisant → signal accepté")
                return _pass

            point_forecast, quantile_forecast = model.forecast(
                horizon=TIMESFM_HORIZON,
                inputs=[np.array(closes)],
            )

            predicted   = point_forecast[0]       # array (horizon,)
            last_price  = closes[-1]
            pred_end    = float(predicted[-1])

            # Direction prédite
            pred_dir = "BUY" if pred_end > last_price else "SELL"

            # Amplitude en pips (Gold : 1 pip = 0.1)
            move_pips = abs(pred_end - last_price) / 0.1

            # Confiance basée sur l'écart quantile 10%-90%
            q10 = float(quantile_forecast[0, -1, 0])
            q90 = float(quantile_forecast[0, -1, -1])
            spread = abs(q90 - q10)
            raw_move = abs(pred_end - last_price)
            confidence = max(0.0, 1.0 - (spread / (raw_move + 1e-6)))
            confidence = min(confidence, 1.0)

            direction_ok  = (pred_dir == signal_direction)
            move_ok       = (move_pips >= TIMESFM_MIN_MOVE_PIPS)
            confidence_ok = (confidence >= TIMESFM_MIN_CONFIDENCE)

            valid = direction_ok and move_ok and confidence_ok

            reasons = []
            if not direction_ok:
                reasons.append(f"direction prédite={pred_dir} ≠ signal={signal_direction}")
            if not move_ok:
                reasons.append(f"move={move_pips:.1f} pips < min={TIMESFM_MIN_MOVE_PIPS}")
            if not confidence_ok:
                reasons.append(f"confiance={confidence:.2f} < min={TIMESFM_MIN_CONFIDENCE}")

            reason = " | ".join(reasons) if reasons else "OK"

            log.info(
                f"[TIMESFM] Signal={signal_direction} Prédit={pred_dir} "
                f"Move={move_pips:.1f}pips Conf={confidence:.2f} → {'✅ VALID' if valid else '❌ REJETÉ'}"
            )
            if not valid:
                log.info(f"[TIMESFM] Raison rejet : {reason}")

            return {
                "valid":               valid,
                "reason":              reason,
                "predicted_direction": pred_dir,
                "confidence":          round(confidence, 2),
                "predicted_move_pips": round(move_pips, 1),
            }

        except Exception as e:
            log.error(f"[TIMESFM] Erreur lors de la validation : {e}")
            # En cas d'erreur, on laisse passer le signal
            return _pass


# Instance globale unique (chargement modèle en background)
timesfm_validator = TimesFMValidator()

# =============================================================
# SIGNAL PARSER
# =============================================================
from signal_parser import SignalParser, is_spam
import bot_messages as msg

# =============================================================
# NEWS MANAGER
# =============================================================
class NewsManager:
    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    def __init__(self, bridge):
        self.bridge = bridge
        self.manager = None
        self._news = []
        self._blocked = False
        self._stop = False
        self._task = None

    def set_manager(self, manager):
        self.manager = manager

    def is_blocked(self) -> bool:
        return self._blocked

    async def start(self):
        self._task = asyncio.create_task(self._loop_async())

    async def _loop_async(self):
        # ★ FIX : le fetch (téléchargement du calendrier) reste à 30 min — la liste
        # hebdomadaire ne change presque jamais en cours de journée. Mais la VÉRIFICATION
        # (décision de bloquer/fermer) tournait sur le même cycle de 30 min, ce qui est
        # bien trop grossier pour des fenêtres de protection de 5-15 minutes : le bot
        # pouvait complètement rater la fenêtre de fermeture avant une news. On sépare donc
        # les deux cadences — fetch toutes les 30 min, vérification toutes les 30 secondes
        # (réutilise les données déjà en cache, pas de nouvel appel réseau).
        FETCH_INTERVAL_SEC = 1800
        CHECK_INTERVAL_SEC = 30
        last_fetch = 0.0
        while not self._stop:
            try:
                now = time.time()
                if now - last_fetch >= FETCH_INTERVAL_SEC:
                    await asyncio.to_thread(self._fetch_news)
                    last_fetch = now
                await asyncio.to_thread(self._check_news)
            except Exception as e:
                log.error(f"NewsManager erreur: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    def _fetch_news(self):
        if not NEWS_ENABLED:
            return
        try:
            ssl_context = ssl._create_unverified_context()
            req = urllib.request.Request(
                self.FF_URL, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10, context=ssl_context) as r:
                data = json.loads(r.read().decode())
            # ★ FIX : le champ JSON de cet endpoint est "country" (pas "currency").
            # On vérifie les deux par sécurité, au cas où le schéma varie.
            # ★ FIX : NEWS_MIN_IMPACT était inversé — "high" incluait aussi "medium",
            # et "medium" excluait "high". Logique correcte : c'est un seuil MINIMUM,
            # donc tout ce qui est ≥ au niveau choisi doit être inclus.
            _IMPACT_ORDER = ["low", "medium", "high"]
            try:
                _min_idx = _IMPACT_ORDER.index(NEWS_MIN_IMPACT)
            except ValueError:
                _min_idx = _IMPACT_ORDER.index("high")  # valeur invalide → repli strict
            _impact_levels = _IMPACT_ORDER[_min_idx:]
            self._news = [
                n for n in data
                if n.get("impact", "").lower() in _impact_levels
                and (n.get("country", "") in ("USD", "XAU") or n.get("currency", "") in ("USD", "XAU"))
            ]
            # ★ FIX #8 : log les news 1h avant l'ouverture NY (12:30 UTC)
            now_utc = datetime.now(timezone.utc)
            is_pre_ny = (now_utc.hour == 12 and 25 <= now_utc.minute <= 35)
            if is_pre_ny or not hasattr(self, '_news_logged'):
                self._news_logged = True
                log.info(msg.log_news_loaded(len(self._news)))
                if len(self._news) == 0 and len(data) > 0:
                    sample_keys = list(data[0].keys())
                    log.warning(msg.log_news_zero_debug(len(data), sample_keys))
                elif len(self._news) > 0:
                    for n in self._news:
                        log.info(f"  NEWS: {n.get('title', '?')} @ {n.get('date', '?')} ({n.get('country', '?')})")
        except Exception as e:
            log.error(msg.log_news_fetch_error(str(e)))

    def _check_news(self):
        if not NEWS_ENABLED:
            return
        now = datetime.now(timezone.utc)
        # ★ FIX : réévaluer l'état à CHAQUE appel plutôt qu'un verrou à sens unique.
        # Avant : self._blocked ne se réinitialisait que si la MÊME news exacte était
        # revérifiée pile dans sa fenêtre de reprise — une fois cette fenêtre ratée
        # (ou une autre news scannée avant dans la liste), le blocage restait actif
        # indéfiniment, sans aucun mécanisme pour le lever.
        should_close = False
        should_block = False
        active_title = None
        active_diff = 0.0

        for news in self._news:
            try:
                news_time = datetime.fromisoformat(news["date"].replace("Z", "+00:00"))
            except Exception:
                continue
            title = news.get("title", "?")
            block_min, close_min, after_min = _get_news_window(title)
            diff_minutes = (news_time - now).total_seconds() / 60

            if -after_min <= diff_minutes <= close_min:
                should_close = True
                active_title = title
                active_diff = diff_minutes
                break
            elif close_min < diff_minutes <= block_min:
                if not should_block:
                    should_block = True
                    active_title = title
                    active_diff = diff_minutes

        if should_close:
            if not self._blocked:
                log.info(msg.log_news_closing_positions(active_title, active_diff))
                if self.manager:
                    self._close_all()
            self._blocked = True
        elif should_block:
            if not self._blocked:
                log.info(msg.log_news_blocking_signals(active_title, active_diff))
            self._blocked = True
        else:
            if self._blocked:
                log.info(msg.log_news_resumed(active_title or "Fenêtre news"))
            self._blocked = False

    def _close_all(self):
        if self.manager:
            # Capturer P&L avant fermeture
            positions_before = mt5.positions_get()
            tickets_before = {p.ticket: p for p in positions_before if p.magic == MAGIC_NUMBER} if positions_before else {}
            self.bridge.close_all()
            # Mettre à jour le P&L quotidien pour chaque position fermée
            time.sleep(0.3)
            for ticket, pos in tickets_before.items():
                deals = mt5.history_deals_get(position=ticket)
                if deals:
                    pnl = sum(d.profit for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT)
                    self.manager._update_daily_pnl(pnl)

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()

# =============================================================
# MT5 BRIDGE
# =============================================================
class MT5Bridge:
    _sym_cache: dict = {}

    def connect(self) -> bool:
        if mt5.initialize():
            info = mt5.account_info()
            if info and info.login > 0:
                return self._finish_connect()
        mt5.shutdown()
        if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER,
                              path=MT5_PATH if os.path.exists(MT5_PATH) else None):
            log.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        return self._finish_connect()

    def _finish_connect(self) -> bool:
        terminal = mt5.terminal_info()
        try:
            algo_ok = bool(getattr(terminal, "trade_expert", True))
        except Exception:
            algo_ok = True
        if algo_ok:
            log.info("MT5 connecté | Algo Trading actif ✅")
        else:
            log.info("MT5 connecté | Algo Trading DÉSACTIVÉ ⚠️ (activez le bouton vert dans MT5)")
        return True

    def disconnect(self):
        mt5.shutdown()

    def _sym(self, symbol: str):
        if symbol in self._sym_cache:
            return mt5.symbol_info(self._sym_cache[symbol])
        info = mt5.symbol_info(symbol)
        if info is None:
            for sfx in ["m", "m+", ".a", "pro", "+", ".", "z", "micro", "#", ""]:
                info = mt5.symbol_info(symbol + sfx)
                if info:
                    log.debug(f"Symbole résolu : {symbol} → {symbol + sfx}")
                    break
        if info is None and symbol.endswith("m"):
            info = mt5.symbol_info(symbol[:-1])
            if info:
                log.debug(f"Symbole résolu : {symbol} → {symbol[:-1]}")
        if info is None:
            all_syms = mt5.symbols_get()
            if all_syms:
                matches = [s for s in all_syms if s.name.upper().startswith(symbol.upper()[:6])]
                if matches:
                    info = matches[0]
                    log.debug(f"Symbole trouvé par recherche : {info.name}")
        if info is None:
            log.error(f"Symbole introuvable : {symbol}")
            return None
        self._sym_cache[symbol] = info.name
        if not info.visible:
            mt5.symbol_select(info.name, True)
            time.sleep(0.5)
        return mt5.symbol_info(info.name)

    def _get_filling(self, sym_info) -> int:
        filling = sym_info.filling_mode
        if filling & SYMBOL_FILLING_FOK:
            return ORDER_FILLING_FOK
        if filling & SYMBOL_FILLING_IOC:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_RETURN

    def current_price(self, symbol: str, action: str) -> float | None:
        sym_info = self._sym(symbol)
        if sym_info is None:
            return None
        tick = mt5.symbol_info_tick(sym_info.name)
        if not tick:
            return None
        return tick.ask if action == "BUY" else tick.bid

    def _validate_volume(self, sym_info, lot: float) -> float:
        vol_min = sym_info.volume_min
        vol_max = sym_info.volume_max
        vol_step = sym_info.volume_step
        if lot < vol_min:
            lot = vol_min
        elif lot > vol_max:
            lot = vol_max
        if vol_step > 0:
            lot = round(lot / vol_step) * vol_step
            lot = round(lot, 8)
        return lot

    def place_market_order(self, signal: dict, lot: float, tp: float, sl: float = 0.0, comment: str = "TG-market") -> int | None:
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        lot = self._validate_volume(sym, lot)
        action = signal["action"]
        tick = mt5.symbol_info_tick(sym.name)
        if not tick:
            return None
        price = tick.ask if action == "BUY" else tick.bid
        otype = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        filling_modes = []
        filling = sym.filling_mode
        if filling & SYMBOL_FILLING_FOK:
            filling_modes.append(ORDER_FILLING_FOK)
        if filling & SYMBOL_FILLING_IOC:
            filling_modes.append(ORDER_FILLING_IOC)
        filling_modes.append(ORDER_FILLING_RETURN)
        if sl == 0.0:
            sl = signal.get("sl", 0.0)
        for fill_mode in filling_modes:
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym.name,
                "volume": lot,
                "type": otype,
                "price": price,
                "sl": round(sl, sym.digits) if sl else 0,
                "tp": round(tp, sym.digits) if tp else 0,
                "deviation": SLIPPAGE,
                "magic": MAGIC_NUMBER,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill_mode,
            })
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log.debug(f"MARKET {action} {sym.name} lot={lot} @{price} ticket#{result.order}")
                return result.order
        return None

    def cancel_order(self, order_ticket: int) -> bool:
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket})
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        log.debug(f"{'OK' if ok else 'FAIL'} Annulation #{order_ticket}")
        return ok

    def close_position(self, ticket: int, comment: str = "close") -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        cprice = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        ctype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        filling = self._get_filling(mt5.symbol_info(pos.symbol))
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": ctype,
            "position": ticket,
            "price": cprice,
            "deviation": SLIPPAGE,
            "magic": MAGIC_NUMBER,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.debug(f"Fermeture #{ticket} ({comment}) P&L={pos.profit:.2f}")
        return ok

    def modify_sl_tp(self, ticket: int, new_sl: float, new_tp: float, label: str = "") -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        sym = mt5.symbol_info(pos.symbol)
        if sym is None:
            return False
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": round(new_sl, sym.digits),
            "tp": round(new_tp, sym.digits),
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.debug(f"SL/TP modifiés #{ticket} → SL={new_sl} TP={new_tp} {label}")
        return ok

    def update_sl_by_channel(self, new_sl: float, channel_num: int):
        positions = mt5.positions_get()
        if not positions:
            return
        updated = 0
        for pos in positions:
            if pos.magic != MAGIC_NUMBER:
                continue
            if not pos.comment.startswith(f"CH{channel_num}-"):
                continue
            sym = mt5.symbol_info(pos.symbol)
            if sym is None:
                continue
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": pos.ticket,
                "sl": round(new_sl, sym.digits),
                "tp": pos.tp,
            })
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                updated += 1
                log.debug(f"SL modifié #{pos.ticket} (canal CH{channel_num}) → {new_sl}")
        log.info(f"<<<<< INFO >>>>> SL MOVE canal {channel_num} → {new_sl} sur {updated} positions")


    def close_all(self, symbol: str | None = None, channel_num: int | None = None):
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            return
        for pos in positions:
            if pos.magic != MAGIC_NUMBER:
                continue
            if channel_num is not None:
                if not pos.comment.startswith(f"CH{channel_num}-"):
                    continue
            self.close_position(pos.ticket, comment="close-all")

# =============================================================
# TRADE MANAGER (avec whitelist BE)
# =============================================================
class TradeManager:
    def __init__(self, bridge: MT5Bridge, tracker=None, quick_alerts_ref=None):
        self.bridge = bridge
        self.tracker = tracker
        self.active = []
        self._lock = threading.Lock()
        self._daily_lock = threading.Lock()
        self._stop = False
        self._task = None
        self._quick_alerts_ref = quick_alerts_ref if quick_alerts_ref is not None else {}

        # ★★★ WHITELIST des rôles autorisés à déclencher le BE ★★★
        self._pos_cache = None  # rafraîchi à chaque cycle par _refresh_pos_cache()
        # ★ Tous les signaux sont MARKET, jamais de pending
        self._be_allowed_roles = {
            "market_single",       # PU1, PU2, ZN1, ZN2
            "quick_market",        # QA (AL-MP)
        }

        self._daily_pnl = self._recover_daily_pnl()
        self._daily_pnl_day = get_trading_day_start().day



    # =============================================================
    # P&L QUOTIDIEN (avec verrouillage)
    # =============================================================
    def _recover_daily_pnl(self) -> float:
        start = get_trading_day_start()
        now = datetime.now(timezone.utc)
        # ★ FIX : le magic du deal de CLÔTURE peut être 0 si la position a été fermée
        # manuellement (mobile/terminal/web) — dans ce cas le magic n'est PAS celui du bot,
        # même si la position a bien été OUVERTE par le bot. On indexe donc le magic
        # à l'OUVERTURE (DEAL_ENTRY_IN) de chaque position, sur une fenêtre élargie
        # (la position a pu être ouverte avant le début de la journée de trading),
        # et on l'utilise comme référence fiable au lieu du magic du deal OUT.
        lookup_start = start - timedelta(days=3)
        all_deals = mt5.history_deals_get(lookup_start, now)
        if all_deals is None or len(all_deals) == 0:
            return 0.0

        open_magic = {}
        for deal in all_deals:
            if deal.entry == mt5.DEAL_ENTRY_IN:
                open_magic[deal.position_id] = deal.magic

        total = 0.0
        for deal in all_deals:
            if deal.entry != mt5.DEAL_ENTRY_OUT:
                continue
            if deal.time < start.timestamp():
                continue
            origin_magic = open_magic.get(deal.position_id, deal.magic)
            if origin_magic == MAGIC_NUMBER:
                total += deal.profit
        return total

    def _get_floating_pnl(self) -> float:
        positions = mt5.positions_get()
        if not positions:
            return 0.0
        total = 0.0
        for pos in positions:
            if pos.magic == MAGIC_NUMBER:
                total += pos.profit
        return total

    def _update_daily_pnl(self, pnl: float):
        with self._daily_lock:
            start = get_trading_day_start()
            if start.day != self._daily_pnl_day:
                self._daily_pnl = 0.0
                self._daily_pnl_day = start.day
                log.info(f"<<<<< INFO >>>>> Reset journalier à {TRADING_START_HOUR}h UTC")
            self._daily_pnl += pnl
            total = self._daily_pnl + self._get_floating_pnl()
        log.debug(msg.log_daily_pnl_periodic(self._daily_pnl, self._get_floating_pnl(), total))

    def _check_daily_pnl_limit(self) -> bool:
        with self._daily_lock:
            start = get_trading_day_start()
            if start.day != self._daily_pnl_day:
                self._daily_pnl = 0.0
                self._daily_pnl_day = start.day
                log.info(f"<<<<< INFO >>>>> Reset journalier à {TRADING_START_HOUR}h UTC")
            total_pnl = self._daily_pnl + self._get_floating_pnl()
            if DAILY_PROFIT_LIMIT > 0 and total_pnl >= DAILY_PROFIT_LIMIT:
                log.info(f"<<<<< INFO >>>>> Limite quotidienne atteinte : {total_pnl:.2f}$ / {DAILY_PROFIT_LIMIT}$")
                return False
        return True

    # =============================================================
    # ARRÊT QUOTIDIEN
    # =============================================================
    def _cancel_all_pending_orders(self) -> int:
        orders = mt5.orders_get()
        if not orders:
            return 0
        cancelled = 0
        for order in orders:
            if order.magic == MAGIC_NUMBER:
                if self.bridge.cancel_order(order.ticket):
                    cancelled += 1

        log.debug(f"Annulation de {cancelled} ordre(s) pending (tous signaux)")
        return cancelled

    def _close_all_positions(self) -> float:
        positions = mt5.positions_get()
        if not positions:
            return 0.0
        total_pnl = 0.0
        for pos in positions:
            if pos.magic == MAGIC_NUMBER:
                ticket = pos.ticket
                if self.bridge.close_position(ticket, comment="DAILY-LIMIT-CLOSE"):
                    # ★ FIX : attendre la propagation du deal dans MT5 history
                    time.sleep(0.3)
                    deals = mt5.history_deals_get(position=ticket)
                    if deals:
                        # ★ FIX : sommer tous les deals OUT (couvre les clôtures partielles manuelles antérieures)
                        pos_pnl = sum(
                            deal.profit for deal in deals
                            if deal.position_id == ticket and deal.entry == mt5.DEAL_ENTRY_OUT
                        )
                        total_pnl += pos_pnl
                        log.debug(f"Fermeture #{ticket} (P&L={pos_pnl:.2f})")
                    else:
                        total_pnl += pos.profit
                        log.debug(f"Fermeture #{ticket} (P&L={pos.profit:.2f})")
        log.debug(f"Fermeture de toutes les positions")
        return total_pnl

    def _clear_all_entries(self):
        with self._lock:
            for entry in self.active:
                for t in entry.get("tickets", []):
                    t["_daily_limit_closed"] = True
            self.active.clear()
        log.debug("Liste des entrées vidée")

    def _shutdown_for_daily_limit(self):
        log.info("<<<<< INFO >>>>> OBJECTIF QUOTIDIEN ATTEINT")
        log.info(f"<<<<< INFO >>>>> Limite: {DAILY_PROFIT_LIMIT}$")

        positions = mt5.positions_get()
        nb_positions = len([p for p in positions if p.magic == MAGIC_NUMBER]) if positions else 0

        cancelled = self._cancel_all_pending_orders()
        total_pnl = self._close_all_positions()
        
        with self._daily_lock:
            self._update_daily_pnl(total_pnl)
            total = self._daily_pnl + self._get_floating_pnl()
        
        self._clear_all_entries()

        log.info(msg.log_daily_limit_header())
        log.info(msg.log_daily_limit_detail(total, nb_positions, cancelled))

        send_alert_sync(msg.alert_daily_limit(total, DAILY_PROFIT_LIMIT, nb_positions, cancelled))

    # =============================================================
    # GESTION DU BE (avec whitelist)
    # =============================================================
    def _check_pnl_trigger(self, entry: dict) -> bool:
        # Le BE se déclenche quand la position atteint le seuil PNL_TRIGGER_USD.
        # v11 : toujours 1 seule position MARKET par signal.
        min_profit = float('inf')
        min_role = "?"
        has_active = False
        for t in entry.get("tickets", []):
            if t.get("be_active"):
                continue
            # ★★★ Vérification whitelist ★★★
            if t.get("role") not in self._be_allowed_roles:
                continue
            pos = self._get_pos(t["ticket"])
            if pos:
                has_active = True
                if pos.profit < min_profit:
                    min_profit = pos.profit
                    min_role = t.get("role", "?")
        if has_active and min_profit >= PNL_TRIGGER_USD:
            return True

        # ✅ LOG DEBUG : pourquoi le BE ne se déclenche pas
        if has_active and min_profit < float('inf'):
            log.debug(f"[BE] PnL insuffisant : {min_profit:.2f}$ < {PNL_TRIGGER_USD}$ (pire rôle={min_role})")
        return False

    def _apply_be_on_open_positions(self, entry: dict, action: str):
        signal = entry.get("signal", {})
        canal = signal.get("source_channel", "Inconnu")
        ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
        mt5_comment = entry.get("_mt5_comment", f"CH{ch_num}-UNK")

        # ★ Toujours 1 position MARKET, jamais de pending
        open_tickets = [t for t in entry.get("tickets", []) if self._get_pos(t["ticket"])]
        if not open_tickets:
            log.warning(f"Aucune position ouverte au moment du BE pour {entry.get('_signal_id', '?')}")
            return

        t = open_tickets[0]
        entry_price = t.get("entry_price", 0)
        if entry_price == 0:
            return

        pos = self._get_pos(t["ticket"])
        if not pos:
            return

        sym = mt5.symbol_info(pos.symbol)
        # ★ BE_USD : SL posé à BE_USD $ de l'entrée, côté défavorable (pas exactement
        # à l'entrée) — pour XAUUSD 0.01 lot, 1$ de mouvement = 1$ de P&L.
        if action == "BUY":
            be_price = round(entry_price - BE_USD, sym.digits if sym else 2)
        else:
            be_price = round(entry_price + BE_USD, sym.digits if sym else 2)
        target_gain = TP_FIXED_GAIN_USD

        # ★ BE : SL @ entry, TP @ entry ± TP_FIXED_GAIN_USD (en points de prix)
        # Pour XAUUSD 0.01 lot : 1$ = 1 point de prix
        tp_fixed_points = TP_FIXED_GAIN_USD  # 10$ = 10 pts pour XAUUSD 0.01 lot
        if action == "BUY":
            be_tp = round(entry_price + tp_fixed_points, sym.digits)
        else:
            be_tp = round(entry_price - tp_fixed_points, sym.digits)

        if self.bridge.modify_sl_tp(t["ticket"], be_price, be_tp, f"[BE @{be_price} TP @{be_tp}]"):
            t["be_active"] = True
            t["be_sl"] = be_price
            t["tp_final"] = be_tp
            entry["_be_price"] = be_price
            entry["_be_market_entry"] = entry_price
            entry["_target_gain"] = target_gain
            entry["_be_activated"] = True
            log.info(msg.log_be_combined(mt5_comment, 1, be_price))
            send_alert_sync(msg.alert_be_activated(action, signal['symbol'], 1, be_price, target_gain, mt5_comment, 0))

    # =============================================================
    # MÉTHODES UTILITAIRES
    # =============================================================
    def _get_pos(self, ticket: int):
        # ★ FIX perf : utilise le cache rafraîchi une fois par cycle (_refresh_pos_cache)
        # au lieu d'un appel MT5 frais à chaque invocation — un même ticket peut être
        # interrogé 5-10x dans un seul passage de _check_all(). Réduit fortement le
        # nombre d'appels MT5 par cycle, ce qui permet de baisser POLL_INTERVAL_SEC
        # en toute sécurité pour réagir plus vite aux mèches/spikes brefs.
        if self._pos_cache is not None:
            return self._pos_cache.get(ticket)
        r = mt5.positions_get(ticket=ticket)
        return r[0] if r else None

    def _refresh_pos_cache(self):
        positions = mt5.positions_get()
        self._pos_cache = {p.ticket: p for p in positions} if positions else {}

    def _get_last_pnl(self, ticket: int, symbol: str) -> float:
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return 0.0
        # ★ FIX : sommer TOUS les deals OUT de la position (pas seulement le dernier)
        # → capture correctement les clôtures partielles manuelles en plus de la clôture finale
        total = 0.0
        found = False
        for deal in deals:
            if deal.entry == mt5.DEAL_ENTRY_OUT:
                total += deal.profit
                found = True
        return total if found else 0.0

    def _get_close_reason(self, ticket: int, symbol: str) -> str:
        # ★ Optimisation : chercher directement par position_id au lieu de scanner tout l'historique
        deals = mt5.history_deals_get(position=ticket)
        if deals:
            for deal in reversed(deals):
                if deal.entry == mt5.DEAL_ENTRY_OUT:
                    if deal.reason == mt5.DEAL_REASON_TP:
                        return "TP"
                    elif deal.reason == mt5.DEAL_REASON_SL:
                        return "SL"
        return "OTHER"

    # =============================================================
    # BOUCLE PRINCIPALE
    # =============================================================
    async def start(self):
        self._task = asyncio.create_task(self._loop_async())

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()

    def register(self, entry: dict):
        with self._lock:
            self.active.append(entry)
        sig = entry["signal"]
        canal = sig.get("source_channel", "Inconnu")
        mode = "DEMO" if DEMO_MODE else "LIVE"
        log.debug(f"TradeManager [{mode}]: {sig['action']} {sig['symbol']} Canal: {canal}")

    async def _loop_async(self):
        while not self._stop:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            try:
                await asyncio.to_thread(self._check_all)
            except Exception as exc:
                log.error(f"TradeManager erreur: {exc}")

    def _check_all(self):
        now = datetime.now(timezone.utc)
        self._refresh_pos_cache()

        if not self._check_daily_pnl_limit():
            if self.active:
                log.debug("[DAILY P&L] Limite atteinte ! Fermeture de toutes les positions et annulation des ordres.")
                self._shutdown_for_daily_limit()
            if not self.active:
                return

        with self._lock:
            entries_snapshot = list(self.active)

        for entry in entries_snapshot:
            signal = entry.get("signal", {})
            symbol = signal.get("symbol", "")
            action = signal.get("action", "")
            canal = signal.get("source_channel", "Inconnu")
            mt5_comment = entry.get("_mt5_comment", f"CH{CHANNEL_NUM_MAP.get(canal, '?')}-UNK")

            for t in entry.get("tickets", []):
                pos = self._get_pos(t["ticket"])
                if pos is None and not t.get("_reported"):
                    t["_reported"] = True
                    pnl = self._get_last_pnl(t["ticket"], symbol)
                    t["_last_pnl"] = pnl
                    # ★ Mise à jour immédiate du P&L quotidien à CHAQUE ticket fermé
                    # (au lieu d'attendre la fermeture complète de l'entrée)
                    self._update_daily_pnl(pnl)
                    daily_pnl_now = self._daily_pnl
                    close_reason = self._get_close_reason(t["ticket"], symbol)
                    if close_reason == "TP":
                        label = "TP"
                    elif close_reason == "SL":
                        label = "SL"
                    else:
                        label = "CLOSE"

                    idx = entry["tickets"].index(t) + 1
                    total = len(entry["tickets"])
                    log.info(msg.log_close_combined(mt5_comment, label, idx, total, t['ticket'], pnl))
                    log.info(msg.log_daily_pnl_final(daily_pnl_now))

                    send_alert_sync(msg.alert_close(label, action, symbol, pnl, idx, total, t['ticket'], daily_pnl_now, mt5_comment))

            active_tickets = []
            for t in entry.get("tickets", []):
                if self._get_pos(t["ticket"]):
                    active_tickets.append(t)

            # ★ FIX : race condition — un ticket peut se fermer PILE entre la boucle de
            # détection ci-dessus (ligne 1557) et cette re-vérification. Dans ce cas, il
            # n'est jamais marqué "_reported" (jamais passé par _get_last_pnl/_update_daily_pnl),
            # mais active_tickets peut quand même être vide → l'entrée serait retirée de
            # self.active et ce ticket ne serait plus JAMAIS revérifié, perdant son P&L
            # définitivement. On exige donc que TOUS les tickets soient "_reported" avant
            # de considérer l'entrée comme terminée, pas seulement qu'aucun ne soit "actif".
            all_reported = all(t.get("_reported") for t in entry.get("tickets", []))

            if not active_tickets and all_reported:
                total_pnl = sum(t.get("_last_pnl", 0.0) for t in entry.get("tickets", []))
                log.debug(f"Trade terminé ({symbol}) | Canal: {canal} | P&L total: {total_pnl:+.2f}")
                if self.tracker:
                    self.tracker.log_trade_close(entry, total_pnl)
                # Note : le P&L quotidien est déjà mis à jour par ticket (voir boucle ci-dessus),
                # pas besoin de le ré-additionner ici (éviterait un double comptage).
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)
                continue

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 3 : GESTION BE ★★★
            # ══════════════════════════════════════════════════════════════
            # BE → SL @ entry ± BE_USD (marge de sécurité) + TP @ entry ± TP_FIXED_GAIN_USD
            # MT5 ferme automatiquement quand le prix atteint le TP.
            # Plus de fermeture manuelle par le bot.
            if TP_FIXED_ENABLED and not entry.get("_be_activated"):
                if self._check_pnl_trigger(entry):
                    self._apply_be_on_open_positions(entry, action)
                    continue

# =============================================================
# CONFLIT & EXÉCUTION (avec SL paramétrable)
# =============================================================
def check_conflict(signal: dict, bridge: MT5Bridge, manager) -> bool:
    # ★ Le contrôle de conflit est maintenant scopé au MÊME CANAL uniquement — deux
    # canaux différents peuvent avoir des avis opposés sur le même symbole sans se
    # bloquer mutuellement. Activable/désactivable via CONFLIT_FILTER_ENABLED (.env).
    if not CONFLIT_FILTER_ENABLED:
        return False
    symbol = signal["symbol"]
    new_action = signal["action"]
    opposite = "SELL" if new_action == "BUY" else "BUY"
    canal = signal.get("source_channel", "Inconnu")
    ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), None))
    conflict = False

    positions = mt5.positions_get()
    if positions:
        for pos in positions:
            if pos.magic != MAGIC_NUMBER or pos.symbol != symbol:
                continue
            pos_dir = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
            if pos_dir != opposite:
                continue
            if ch_num is not None and pos.comment.startswith(f"CH{ch_num}-"):
                conflict = True
                break
    if not conflict:
        with manager._lock:
            for entry in manager.active:
                if (entry["signal"]["symbol"] == symbol
                        and entry["signal"]["action"] == opposite
                        and entry["signal"].get("source_channel") == canal):
                    conflict = True
                    break
    if not conflict:
        return False
    log.warning(f"<<<<< WARNING >>>>> CONFLIT {symbol} (canal {canal}) : entrant={new_action} existant={opposite}")
    to_remove = []
    with manager._lock:
        for entry in manager.active:
            if entry["signal"]["symbol"] != symbol:
                continue
            if entry["signal"].get("source_channel") != canal:
                continue
            to_remove.append(entry)
    # Capturer P&L des positions avant fermeture
    positions_before = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    conflict_tickets = {}
    if positions_before:
        for p in positions_before:
            if p.magic == MAGIC_NUMBER:
                if ch_num is None or p.comment.startswith(f"CH{ch_num}-"):
                    conflict_tickets[p.ticket] = p.profit
    for e in to_remove:
        if e in manager.active:
            manager.active.remove(e)
    bridge.close_all(symbol=symbol, channel_num=ch_num)
    # Mettre à jour le P&L quotidien
    time.sleep(0.3)
    for ticket in conflict_tickets:
        deals = mt5.history_deals_get(position=ticket)
        if deals:
            pnl = sum(d.profit for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT)
            manager._update_daily_pnl(pnl)
    return True

def _close_previous_signal(canal: str, bridge: MT5Bridge, manager: TradeManager) -> bool:
    """Ferme le signal actif d'un canal s'il en existe déjà un."""
    with manager._lock:
        for entry in list(manager.active):
            sig = entry.get("signal", {})
            entry_canal = sig.get("source_channel", "Inconnu")
            if entry_canal == canal:
                # Trouver les positions ouvertes
                for t in entry.get("tickets", []):
                    pos = manager._get_pos(t["ticket"])
                    if pos:
                        ticket = t["ticket"]
                        symbol = sig.get("symbol", "")
                        bridge.close_position(ticket, "NEW-SIGNAL")
                        # Mettre à jour le P&L quotidien (attendre que le deal apparaisse)
                        time.sleep(0.3)
                        pnl = manager._get_last_pnl(ticket, symbol)
                        manager._update_daily_pnl(pnl)
                        log.info(f"[1-PER-CH] Position #{ticket} fermée pour canal {canal} P&L={pnl:+.2f}")
                # Retirer l'entrée
                manager.active.remove(entry)
                return True
    return False

def execute_signal(signal: dict, bridge: MT5Bridge, manager, tracker):
    action = signal["action"]
    symbol = signal["symbol"]
    zone_low = signal["zone_low"]
    zone_mid = signal["zone_mid"]
    zone_high = signal["zone_high"]

    canal = signal.get("source_channel", "Inconnu")
    mode = "DEMO" if DEMO_MODE else "LIVE"
    ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))

    # ★ 1 signal par canal : fermer l'ancien si un nouveau arrive
    _close_previous_signal(canal, bridge, manager)

    all_tps = signal["tps"]
    if not all_tps:
        # ★ Signal sans TP (TP = "open") → utiliser TP_FIXED_GAIN_USD
        entry_for_tp = (zone_low + zone_high) / 2
        tp_fixed_points = TP_FIXED_GAIN_USD  # 10$ = 10 pts pour XAUUSD 0.01 lot
        if action == "BUY":
            generated_tp = round(entry_for_tp + tp_fixed_points, 2)
        else:
            generated_tp = round(entry_for_tp - tp_fixed_points, 2)
        all_tps = [generated_tp]
        signal["tps"] = all_tps
        log.info(f"Signal sans TP → TP généré: {generated_tp} (entry={entry_for_tp} ± {TP_FIXED_GAIN_USD}$)")

    if action == "SELL":
        all_tps = sorted(all_tps, reverse=True)
    else:
        all_tps = sorted(all_tps)

    if len(all_tps) == 1:
        tp_trigger_idx = 0
    else:
        if 3 > len(all_tps):
            tp_trigger_idx = len(all_tps) - 1
        else:
            tp_trigger_idx = 2

    tp_final = all_tps[-1]
    tp3 = all_tps[tp_trigger_idx]
    sl = signal["sl"]

    if check_conflict(signal, bridge, manager):
        log.info(msg.log_refuse(ch_num, "", msg.MOTIF_CONFLIT))
        return

    sym_info = bridge._sym(symbol)
    if sym_info is None:
        log.info(msg.log_refuse(ch_num, "", msg.MOTIF_SYMBOLE_INTROUVABLE))
        log.error(f"Signal rejeté — symbole introuvable dans MT5: {symbol}")
        return

    current = bridge.current_price(sym_info.name, action)
    if current is None:
        log.info(msg.log_refuse(ch_num, "", msg.MOTIF_PRIX_INDISPONIBLE))
        log.error(f"Signal rejeté — prix indisponible pour {sym_info.name} (action={action})")
        return

    avg_entry = (zone_low + zone_high) / 2
    if not SignalParser._validate_sl(action, avg_entry, sl):
        log.info(msg.log_refuse(ch_num, "", msg.MOTIF_SL_INVALIDE))
        log.error(f"Signal rejeté — SL {sl} invalide pour {action} (entry={avg_entry})")
        return

    tick = mt5.symbol_info_tick(sym_info.name)
    if tick and not DEMO_MODE:
        spread_points = abs(tick.ask - tick.bid)
        spread_pips = spread_points / sym_info.point
        if spread_pips > MAX_SPREAD_POINTS:
            log.info(msg.log_refuse(ch_num, "", f"{msg.MOTIF_SPREAD_LARGE} ({spread_pips:.0f} pts)"))
            log.warning(f"Signal ignoré — spread trop large: {spread_pips:.0f} pts (max={MAX_SPREAD_POINTS}) | {sym_info.name}")
            return

    total_signals = len(manager.active)
    if total_signals >= MAX_POSITIONS:
        log.info(msg.log_refuse(ch_num, "", f"{msg.MOTIF_MAX_SIGNAUX} ({total_signals}/{MAX_POSITIONS})"))
        log.warning(f"Signal ignoré — max signaux atteint ({total_signals}/{MAX_POSITIONS}) | {symbol} {action}")
        return

    tickets = []
    is_single_price = signal.get("is_single_price", False)

    # ★ SIGNAUX ZONE : garder la zone intacte (pas de conversion en midian)
    # Exécution MARKET si le prix est dans la zone OU entre la zone et SL.
    # Sinon annulé.
    is_zone_signal = False
    if not is_single_price and zone_low != zone_high:
        is_zone_signal = True

    # ── Signaux Zone ──
    # Prix dans la zone [zone_low, zone_high] → MARKET ZN1
    # Prix entre la zone et SL → MARKET ZN2
    # Sinon annulé
    if is_zone_signal and len(all_tps) >= 1:
        unique_lot = LOT_UNIQUE_TRADE

        # ZN1 : prix dans la zone
        in_zone = zone_low <= current <= zone_high
        # ZN2 : prix entre la zone et SL (meilleur prix)
        if action == "BUY":
            # SL < zone_low, prix entre SL et zone_low
            between_zone_sl = sl < current < zone_low
        else:
            # SL > zone_high, prix entre zone_high et SL
            between_zone_sl = zone_high < current < sl

        if in_zone:
            mt5_comment_zn = f"CH{ch_num}-ZN1"
        elif between_zone_sl:
            mt5_comment_zn = f"CH{ch_num}-ZN2"
        else:
            log.info(msg.log_refuse(ch_num, "-ZN", msg.MOTIF_PRIX_HORS_ZONE))
            log.warning(f"ZN annulé — prix={current} hors zone | "
                        f"zone={zone_low}-{zone_high} SL={sl}")
            return

        log.debug(f"ZN — {mt5_comment_zn} | zone={zone_low}-{zone_high} SL={sl} prix={current}")

        log.debug(f"  → MARKET {action} @{current} lot={unique_lot} TP={tp_final} SL={sl}")
        try:
            t = bridge.place_market_order(signal, unique_lot, tp=tp_final, sl=sl, comment=mt5_comment_zn)
        except Exception as e:
            log.error(f"  MARKET EXCEPTION: {e}")
            t = None
        if t:
            tickets.append({
                "ticket": t, "lot": unique_lot, "role": "market_single",
                "entry_price": current, "tp_index": tp_trigger_idx, "tp_target": tp3,
                "tp3": tp3, "tp_final": tp_final, "sl_step": 0, "trail_active": False,
                "be_active": False, "be_sl": 0,
            })
            log.debug(f"  ✓ MARKET #{t} @{current} TP={tp_final}")
            send_alert_sync(
                f"🟢 {symbol} | {action} | {mt5_comment_zn}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"MARKET: {current:.2f} | Lot: {unique_lot}\n"
                f"TICKET: {t}\n"
                f"TP: {tp_final} | SL: {sl}\n"
                f"Canal: {canal}"
            )
        else:
            log.error("  ✗ MARKET échoué")
            return

        entry = {
            "signal": signal,
            "tickets": tickets,
            "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "_signal_id": f"{symbol}_{action}_{int(time.time())}",
            "_expected_positions": 1,
            "_mt5_comment": mt5_comment_zn,
        }
        manager.register(entry)
        tracker.log_trade_open(entry)
        log.info(msg.log_order_placed(mt5_comment_zn, "MKT", t, current, sl))
        return

    # ── Prix unique ──
    if is_single_price and len(all_tps) >= 1:
        entry_price = zone_mid
        sl_price = sl
        unique_lot = LOT_UNIQUE_TRADE

        # ★ TOLÉRANCE : PU pour prix unique
        PRICE_TOLERANCE = float(os.getenv("PU_PRICE_TOLERANCE", "3.0"))
        prefix = "PU"

        # Type 1 : prix entre entry et SL
        if action == "BUY":
            is_type1 = sl_price < current < entry_price
            is_type2 = entry_price < current < entry_price + PRICE_TOLERANCE
        else:
            is_type1 = entry_price < current < sl_price
            is_type2 = entry_price - PRICE_TOLERANCE < current < entry_price

        if is_type1:
            mt5_comment_pu = f"CH{ch_num}-{prefix}1"
        elif is_type2:
            mt5_comment_pu = f"CH{ch_num}-{prefix}2"
        else:
            log.info(msg.log_refuse(ch_num, f"-{prefix}", msg.MOTIF_PRIX_HORS_ZONE))
            log.warning(f"{prefix} annulé — prix={current} hors zones | "
                        f"entry={entry_price} SL={sl_price} tolérance={PRICE_TOLERANCE}")
            return

        log.debug(f"{prefix} — {mt5_comment_pu} | entry={entry_price} SL={sl_price} prix={current}")

        # ★ SL et TPf = ceux du signal (pas de modification)
        log.debug(f"  → MARKET {action} @{current} lot={unique_lot} TP={tp_final} SL={sl}")
        try:
            t = bridge.place_market_order(signal, unique_lot, tp=tp_final, sl=sl, comment=mt5_comment_pu)
        except Exception as e:
            log.error(f"  MARKET EXCEPTION: {e}")
            t = None
        if t:
            tickets.append({
                "ticket": t, "lot": unique_lot, "role": "market_single",
                "entry_price": current, "tp_index": tp_trigger_idx, "tp_target": tp3,
                "tp3": tp3, "tp_final": tp_final, "sl_step": 0, "trail_active": False,
                "be_active": False, "be_sl": 0,
            })
            log.debug(f"  ✓ MARKET #{t} @{current} TP={tp_final}")
            send_alert_sync(
                f"🟢 {symbol} | {action} | {mt5_comment_pu}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"MARKET: {current:.2f} | Lot: {unique_lot}\n"
                f"TICKET: {t}\n"
                f"TP: {tp_final} | SL: {sl}\n"
                f"Canal: {canal}"
            )
        else:
            log.error("  ✗ MARKET échoué")

        if not tickets:
            log.info(msg.log_refuse(ch_num, f"-{prefix}", msg.MOTIF_ECHEC_PLACEMENT))
            log.error(f"Aucun ordre placé ({prefix}).")
            return

        entry = {
            "signal": signal,
            "tickets": tickets,
            "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "_signal_id": f"{symbol}_{action}_{int(time.time())}",
            "_expected_positions": 1,
            "_mt5_comment": mt5_comment_pu,
        }
        manager.register(entry)
        tracker.log_trade_open(entry)
        log.info(msg.log_order_placed(mt5_comment_pu, "MKT", t, current, sl))
        return

    # ── Les signaux zone sont convertis en Prix Unique plus haut ──
    # Ce code ne devrait jamais être atteint pour les signaux zone.
    # Si on arrive ici, c'est un signal sans zone et sans prix unique → erreur.
    log.error(f"Signal inattendu: pas de zone, pas de prix unique | {symbol} {action}")
    return

# =============================================================
# QUICK ALERT (CORRIGÉE)
# =============================================================
def _qa_key(symbol: str, action: str, channel_name: str = "") -> str:
    clean_channel = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', channel_name)
    ch_num = CHANNEL_NUM_MAP.get(clean_channel, CHANNEL_NUM_MAP.get(clean_channel.lstrip("-"), "?"))
    return f"CH{ch_num}_{symbol}_{action}"

def execute_quick_alert(signal: dict, bridge: MT5Bridge, manager: TradeManager,
                        tracker: PerformanceTracker, quick_alerts: dict):
    action = signal["action"]
    symbol = signal["symbol"]
    sl = signal.get("sl")
    entry_price = signal["zone_mid"]
    is_market_price = signal.get("is_market_price", False)

    total_signals = len(manager.active)
    if total_signals >= MAX_POSITIONS:
        log.warning(f"Quick Alert ignorée — max signaux atteint ({total_signals}/{MAX_POSITIONS}) | {symbol} {action}")
        return

    sym_info = bridge._sym(symbol)
    if not sym_info:
        log.error(f"Quick alert rejeté — symbole introuvable: {symbol}")
        return
    current = bridge.current_price(sym_info.name, action)
    if current is None:
        log.error(f"Quick alert rejeté — prix indisponible: {symbol}")
        return

    # --- MARKET PRICE : résoudre les offsets relatifs en prix absolus ---
    if is_market_price and entry_price is None:
        entry_price = current
        sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
        RR_RATIO = float(os.getenv("RR_RATIO_DEFAULT", "1.5"))
        if action == "BUY":
            sl = entry_price - sl_offset
            tp = entry_price + sl_offset * RR_RATIO
        else:
            sl = entry_price + sl_offset
            tp = entry_price - sl_offset * RR_RATIO
        signal["sl"] = round(sl, 2)
        signal["tps"] = [round(tp, 2)]
        signal["zone_mid"] = entry_price
        signal["zone_low"] = entry_price
        signal["zone_high"] = entry_price
        log.info(f"[MARKET PRICE] Résolu: entry={entry_price}, SL={sl}, TP={tp}")

    canal = signal.get("source_channel", "Inconnu")
    clean_canal = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', canal)
    ch_num = CHANNEL_NUM_MAP.get(clean_canal, CHANNEL_NUM_MAP.get(clean_canal.lstrip("-"), "?"))

    # ★ 1 signal par canal : fermer l'ancien si un nouveau arrive
    _close_previous_signal(canal, bridge, manager)

    # Utiliser le TP fourni par le parser s'il existe
    if signal.get("tps") and len(signal["tps"]) > 0:
        default_tp = signal["tps"][0]
        log.debug(f"Quick Alert : TP fourni par le parser = {default_tp}")
    else:
        sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
        if action == "BUY":
            default_tp = entry_price + sl_offset
        else:
            default_tp = entry_price - sl_offset
        default_tp = round(default_tp, 2)
        log.debug(f"Quick Alert : TP calculé (fallback) = {default_tp}")

    if sl is None:
        sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
        if action == "BUY":
            sl = entry_price - sl_offset
        else:
            sl = entry_price + sl_offset
        log.debug(f"Quick Alert : SL manquant, calculé = {sl}")

    # Ajuster SL/TP pour les rendre valides
    if action == "BUY":
        if sl >= entry_price:
            sl = entry_price - 10.0
            log.warning(f"Quick Alert : SL ajusté à {sl} (doit être < entry)")
        if default_tp <= entry_price:
            default_tp = entry_price + 10.0
            log.warning(f"Quick Alert : TP ajusté à {default_tp} (doit être > entry)")
    else:  # SELL
        if sl <= entry_price:
            sl = entry_price + 10.0
            log.warning(f"Quick Alert : SL ajusté à {sl} (doit être > entry)")
        if default_tp >= entry_price:
            default_tp = entry_price - 10.0
            log.warning(f"Quick Alert : TP ajusté à {default_tp} (doit être < entry)")

    # ★ VÉRIFICATION TOLÉRANCE DE PRIX (QA avec prix)
    # Si le prix a bougé dans le sens du TP (favorable) → MARKET (meilleur prix, pas de limite).
    # Si le prix a bougé CONTRE le signal de > QA_PRICE_TOLERANCE → annulé.
    # Ex: BUY 4000, prix actuel 3990 → favorable (meilleur prix) → MARKET
    # Ex: BUY 4000, prix actuel 4004 → défavorable (> 3$ contre) → annulé
    QA_PRICE_TOLERANCE = float(os.getenv("QA_PRICE_TOLERANCE", "3.0"))
    if not is_market_price and entry_price is not None:
        if action == "BUY":
            # Favorable: prix <= entry (on achète moins cher)
            # Défavorable: prix > entry + tolerance
            is_unfavorable = current > entry_price + QA_PRICE_TOLERANCE
        else:  # SELL
            # Favorable: prix >= entry (on vend plus cher)
            # Défavorable: prix < entry - tolerance
            is_unfavorable = current < entry_price - QA_PRICE_TOLERANCE
        if is_unfavorable:
            log.info(f"Quick Alert annulée — prix défavorable | "
                     f"prix={current} entry={entry_price} écart>défavorable de {QA_PRICE_TOLERANCE}")
            send_alert_sync(msg.alert_qa_cancelled(action, symbol, ch_num, current, entry_price, QA_PRICE_TOLERANCE))
            return

    # ★ GARDE : entry_price requis pour les filtres suivants
    if entry_price is None:
        log.warning(f"Quick Alert ignorée — entry_price manquant | {symbol} {action}")
        return

    # ★ FILTRE DISTANCE TP pour Quick Alert
    TP_DISTANCE_MIN_RATIO = float(os.getenv("TP_DISTANCE_MIN_RATIO", "0.3"))
    if action == "BUY":
        dist_remaining = abs(default_tp - current)
        dist_total = abs(default_tp - entry_price)
    else:
        dist_remaining = abs(current - default_tp)
        dist_total = abs(entry_price - default_tp)
    if dist_total > 0 and (dist_remaining / dist_total) < TP_DISTANCE_MIN_RATIO:
        log.warning(f"Quick Alert ignorée — prix trop proche du TP ({dist_remaining/dist_total:.0%} restant) | "
                    f"prix={current} entry={entry_price} TP={default_tp}")
        return

    key = _qa_key(symbol, action, canal)
    if key in quick_alerts and quick_alerts[key]:
        existing = quick_alerts[key][0]
        existing_ticket = existing.get("ticket")
        log.debug(f"Quick Alert déjà existante pour {key} → mise à jour")
        pos = mt5.positions_get(ticket=existing_ticket)
        if pos:
            bridge.modify_sl_tp(existing_ticket, sl, default_tp, "[QA-UPDATE-SL-TP]")
            log.debug(f"✓ SL/TP de la position #{existing_ticket} mis à jour")
            existing["signal"]["sl"] = sl
            return
        else:
            log.debug("Quick Alert existante introuvable → nouvelle alerte")

    # ★ EXÉCUTION MARKET (Type 1 et Type 2 dans tolérance)
    mt5_comment_qa = f"CH{ch_num}-AL-MP"
    log.info(msg.log_signal_detected(mt5_comment_qa, action, entry_price))
    log.debug(f"Quick Alert MARKET {action} {symbol} @{current} SL={sl}, TP={default_tp}")

    tickets = []

    try:
        t = bridge.place_market_order(signal, LOT_UNIQUE_TRADE, tp=default_tp, sl=sl, comment=mt5_comment_qa)
    except Exception as e:
        log.error(f"Quick alert MARKET exception: {e}")
        t = None

    if t:
        tickets.append({
            "ticket": t,
            "lot": LOT_UNIQUE_TRADE,
            "role": "quick_market",
            "entry_price": current,
            "tp_index": 0,
            "tp_target": default_tp,
            "tp3": default_tp,
            "tp_final": default_tp,
            "sl_step": 0,
            "trail_active": False,
            "be_active": False,
            "be_sl": 0,
        })
        order_ticket = t
        log.info(msg.log_order_placed(mt5_comment_qa, "MKT", t, current, sl))
        log.debug(f"✓ QUICK MARKET #{t}")
        send_alert_sync(
            f"⚡ {symbol} | {action} | {mt5_comment_qa}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"MARKET: {current:.2f} | Lot: {LOT_UNIQUE_TRADE}\n"
            f"TICKET: {t}\n"
            f"TP: {default_tp} | SL: {sl}\n"
            f"Canal: {canal}"
        )
    else:
        log.error("✗ QUICK MARKET échoué")
        return

    entry = {
        "signal": signal,
        "tickets": tickets,
        "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "_is_quick_alert": True,
        "_signal_id": f"{symbol}_{action}_{int(time.time())}_QA",
        "_expected_positions": 1,
        "_mt5_comment": mt5_comment_qa,
    }
    manager.register(entry)

    if key not in quick_alerts:
        quick_alerts[key] = []
    quick_alerts[key].append({
        "entry": entry,
        "signal": signal,
        "ticket": order_ticket,
        "entry_price": entry_price,
        "is_market_price": signal.get("is_market_price", False),
        "time": datetime.now(timezone.utc),
    })
    log.debug(f"Quick Alert enregistré: {key}")

def merge_quick_alert(qa: dict, key: str, full_signal: dict,
                      bridge: MT5Bridge, manager: TradeManager,
                      tracker: PerformanceTracker, quick_alerts: dict):
    qa_ticket = qa["ticket"]
    entry     = qa["entry"]
    real_sl   = full_signal["sl"]
    tp_final  = full_signal["tps"][-1] if full_signal["tps"] else 0
    canal     = full_signal.get("source_channel", "Inconnu")

    # Fusion : mettre à jour SL/TP du QA existant, jamais de 2e position.
    pos = mt5.positions_get(ticket=qa_ticket)
    if not pos:
        # QA déjà fermé (SL/TP touché) → ignorer le signal complet
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
        deal_pnl = 0.0
        close_reason = "unknown"
        if deals:
            for deal in reversed(deals):
                if deal.symbol == full_signal["symbol"] and (
                    deal.position_id == qa_ticket or deal.order == qa_ticket
                ):
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        deal_pnl = deal.profit + deal.commission + deal.swap
                        if deal.reason == mt5.DEAL_REASON_SL:
                            close_reason = "SL"
                        elif deal.reason == mt5.DEAL_REASON_TP:
                            close_reason = "TP"
                        break
        ch_num_fusion = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
        send_alert_sync(msg.alert_qa_already_closed(full_signal['action'], full_signal['symbol'], ch_num_fusion, qa_ticket, deal_pnl, close_reason))
    else:
        # QA actif -> mettre a jour SL et TP avec ceux du signal complet
        log.info(f"Fusion: mise a jour SL/TP du QA #{qa_ticket}")
        bridge.modify_sl_tp(qa_ticket, real_sl, tp_final, "[FUSION-SL-TP]")
        for t in entry["tickets"]:
            if t["ticket"] == qa_ticket:
                t["tp_final"]  = tp_final
                t["tp_target"] = tp_final
                t["tp3"]       = tp_final
                break
        entry["signal"]          = full_signal
        entry["_is_quick_alert"] = False
        ch_num_fusion = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
        send_alert_sync(msg.alert_fusion(full_signal['action'], ch_num_fusion, qa_ticket, real_sl, tp_final))

    # Nettoyer quick_alerts
    if key in quick_alerts and qa in quick_alerts[key]:
        quick_alerts[key].remove(qa)
        if not quick_alerts[key]:
            del quick_alerts[key]
    log.info(msg.log_merge_done(full_signal['action'], full_signal['symbol']))


# =============================================================
# MAIN
# =============================================================
def _is_signal_message(text: str) -> bool:
    if re.search(r'\d+\.\d+', text):
        return True
    if "CLOSE" in text.upper():
        return True
    if "BUY" in text.upper() or "SELL" in text.upper():
        return True
    return False

async def main():
    global _main_loop, _alert_client
    _main_loop = asyncio.get_running_loop()
    
    parser = SignalParser()
    bridge = MT5Bridge()
    tracker = PerformanceTracker()
    manager = None

    try:
        if not bridge.connect():
            log.critical("Bot arrêté — corrigez MT5 puis relancez.")
            return

        _quick_alerts = {}
        manager = TradeManager(bridge, tracker, quick_alerts_ref=_quick_alerts)
        acc_info = mt5.account_info()
        if acc_info:
            log.info(msg.log_balance_startup(acc_info.balance, manager._daily_pnl))
        await manager.start()

        news_mgr = NewsManager(bridge)
        news_mgr.set_manager(manager)
        await news_mgr.start()

        client = TelegramClient("session_trading", API_ID, API_HASH)
        await client.start()
        _alert_client = client
        log.info("Telegram connecté.")

        chats = []
        channel_names = [
            ("TG_CHANNEL_1", CHANNEL_NAME),
            ("TG_CHANNEL_2", CHANNEL_NAME_2),
            ("TG_CHANNEL_3", CHANNEL_NAME_3),
            ("TG_CHANNEL_4", CHANNEL_NAME_4),
            ("TG_CHANNEL_5", CHANNEL_NAME_5),
            ("TG_CHANNEL_6", CHANNEL_NAME_6),
            ("TG_CHANNEL_7", CHANNEL_NAME_7),
            ("TG_CHANNEL_8", CHANNEL_NAME_8),
            ("TG_CHANNEL_9", CHANNEL_NAME_9),
            ("TG_CHANNEL_10", CHANNEL_NAME_10),
            ("TG_CHANNEL_11", CHANNEL_NAME_11),
            ("TG_CHANNEL_12", CHANNEL_NAME_12),
            ("TG_CHANNEL_13", CHANNEL_NAME_13),
            ("TG_CHANNEL_14", CHANNEL_NAME_14),
            ("TG_CHANNEL_15", CHANNEL_NAME_15),
            ("TG_CHANNEL_16", CHANNEL_NAME_16),
            ("TG_CHANNEL_17", CHANNEL_NAME_17),
            ("TG_CHANNEL_18", CHANNEL_NAME_18),
            ("TG_CHANNEL_19", CHANNEL_NAME_19),
        ]
        entity_to_name = {}
        active_channels = [(e, v) for e, v in channel_names if v]
        log.info(f"Canaux surveillés : {len(active_channels)}")

        for env_name, ch_value in channel_names:
            if not ch_value:
                continue
            try:
                ch_resolved = int(ch_value) if ch_value.lstrip("-").isdigit() else ch_value
                entity = await client.get_entity(ch_resolved)
                title = getattr(entity, "title", ch_value)
                title_clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', title)
                # ★ Normalisation Unicode : convertit les caractères stylisés (gras/italique
                # mathématiques, ex: 𝗘𝗔𝗚𝗟𝗘) vers leur équivalent ASCII standard,
                # pour un affichage correct dans la console Windows.
                title_clean = unicodedata.normalize('NFKC', title_clean)
                if title_clean.strip() == "":
                    title_clean = ch_value
                chats.append(entity)
                entity_to_name[entity.id] = title_clean
                ch_num = int(env_name.replace("TG_CHANNEL_", ""))
                CHANNEL_NUM_MAP[title_clean] = ch_num
                log.info(f"Canal_{ch_num} : {title_clean}")
            except Exception as e:
                log.warning(f"Canal introuvable ({env_name}={ch_value}) : {e}")

        # ★ DÉDUPLICATION : éviter le double traitement du même message
        # (ex: canal + groupe de discussion lié → Telegram livre 2x le même msg)
        # Clé 1 = message_id (fiable côté Telegram, unique par message)
        # Clé 2 = hash(contenu + canal) pour couvrir les messages identiques
        # livrés via des chats différents (canal vs discussion group).
        _seen_messages: dict = {}  # dedup_key -> timestamp
        _seen_msg_ids: dict = {}   # message_id -> timestamp (dict au lieu de set pour TTL)
        _SEEN_TTL = 120.0
        _SEEN_MAX_IDS = 5000

        @client.on(events.NewMessage(chats=chats))
        async def handler(event):
            text = event.message.text or ""
            chat = await event.get_chat()
            canal_name = entity_to_name.get(chat.id, getattr(chat, "title", "inconnu"))

            # --- Déduplication ---
            now_ts = time.time()
            msg_id = event.message.id
            # Clé 1 : message_id (le plus fiable) — eviction TTL au lieu de clear()
            if msg_id in _seen_msg_ids:
                log.debug(f"[DEDUP] message_id {msg_id} déjà traité → ignoré")
                return
            # Clé 2 : contenu + canal (même texte du même canal → même clé)
            text_hash = hash(text.strip())
            dedup_key = (canal_name, text_hash)
            # Eviction TTL bornée (pas de clear() total)
            if len(_seen_messages) > 500:
                cutoff = now_ts - _SEEN_TTL
                stale = [k for k, v in _seen_messages.items() if v < cutoff]
                for k in stale:
                    del _seen_messages[k]
            if dedup_key in _seen_messages:
                log.debug(f"[DEDUP] message déjà traité pour {canal_name} → ignoré")
                return
            _seen_msg_ids[msg_id] = now_ts
            # Eviction bornée par taille (garde les plus récents, pas de clear())
            if len(_seen_msg_ids) > _SEEN_MAX_IDS:
                # Supprime les 1000 plus anciens
                sorted_ids = sorted(_seen_msg_ids.items(), key=lambda x: x[1])
                for old_id, _ in sorted_ids[:1000]:
                    del _seen_msg_ids[old_id]
            _seen_messages[dedup_key] = now_ts
            # --- Fin déduplication ---

            if is_spam(text):
                return

            if not _is_signal_message(text):
                return

            # Log brut en DEBUG seulement
            clean_text = text.replace('*', '').replace('\n', ' | ')[:150]
            log.debug(f"[{canal_name}] {clean_text}")

            signal_data = parser.parse(text)
            if signal_data is None:
                return

            signal_data.source_channel = canal_name

            # Log du signal reçu (sans scénario — le vrai scénario est loggé par execute_signal)
            if signal_data.signal_type == "TRADE":
                action = signal_data.direction or "?"
                symbol = signal_data.pair or "?"
                sl = signal_data.sl or 0
                tp_final = signal_data.tp_final or 0
                ch_num = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), "?"))

                if signal_data.is_market_price:
                    mode = "MP"
                elif signal_data.is_quick_alert:
                    mode = "AL"
                elif signal_data.is_single_price:
                    mode = "PU"
                else:
                    mode = "C"

                mt5_comment = f"CH{ch_num}-{mode}"
                log.info(msg.log_signal_detected(mt5_comment, action, signal_data.zone_mid))

            if signal_data.signal_type == "CLOSE":
                canal = canal_name
                ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), None))
                # Capturer P&L avant fermeture
                close_symbol = signal_data.close_symbol
                positions_before = mt5.positions_get(symbol=close_symbol) if close_symbol else mt5.positions_get()
                close_tickets = {}
                if positions_before:
                    for p in positions_before:
                        if p.magic == MAGIC_NUMBER:
                            if ch_num is None or p.comment.startswith(f"CH{ch_num}-"):
                                close_tickets[p.ticket] = p
                bridge.close_all(symbol=close_symbol, channel_num=ch_num)
                # Mettre à jour le P&L quotidien
                time.sleep(0.3)
                for ticket in close_tickets:
                    deals = mt5.history_deals_get(position=ticket)
                    if deals:
                        pnl = sum(d.profit for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT)
                        manager._update_daily_pnl(pnl)
                return

            elif signal_data.signal_type == "SL_MOVE":
                log.debug(f"SL MOVE reçu → nouveau SL={signal_data.new_sl}")
                ch_num = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), None))
                if ch_num is not None:
                    bridge.update_sl_by_channel(signal_data.new_sl, ch_num)
                else:
                    log.warning(f"SL MOVE ignoré : canal inconnu ({canal_name})")
                return

            elif signal_data.signal_type == "TRADE":
                if NEWS_ENABLED and news_mgr.is_blocked():
                    ch_num_news = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), "?"))
                    log.info(msg.log_refuse(ch_num_news, "", msg.MOTIF_PROTECTION_NEWS))
                    return

                blocked, reason = in_blocked_window()
                if blocked:
                    log.debug(f"[{canal_name}] Signal ignoré - Filtre horaire : {reason}")
                    return

                if not manager._check_daily_pnl_limit():
                    log.debug(f"[{canal_name}] Signal ignoré - Limite de P&L quotidien atteinte ({DAILY_PROFIT_LIMIT}$)")
                    return

                sig_dict = signal_data.to_dict()

                # ============================================================
                # ★★★ VALIDATION TIMESFM ★★★
                # Appliquée uniquement aux signaux TRADE (pas aux quick alerts)
                # ============================================================
                if TIMESFM_ENABLED and not signal_data.is_quick_alert:
                    tfm_result = timesfm_validator.validate(sig_dict["action"])
                    if not tfm_result["valid"]:
                        log.info(
                            f"[TIMESFM] 🚫 Signal {sig_dict['action']} {sig_dict['symbol']} REJETÉ "
                            f"— Prédit={tfm_result['predicted_direction']} "
                            f"Move={tfm_result['predicted_move_pips']}pips "
                            f"Conf={tfm_result['confidence']} "
                            f"({tfm_result['reason']})"
                        )
                        send_alert_sync(msg.alert_timesfm_rejected(
                            sig_dict['action'], sig_dict['symbol'],
                            tfm_result['predicted_direction'],
                            tfm_result['predicted_move_pips'],
                            tfm_result['confidence'],
                            tfm_result['reason'],
                            canal_name
                        ))
                        return
                    else:
                        log.info(
                            f"[TIMESFM] ✅ Signal {sig_dict['action']} validé — "
                            f"Move prédit={tfm_result['predicted_move_pips']}pips "
                            f"Conf={tfm_result['confidence']}"
                        )
                # ============================================================

                if signal_data.is_quick_alert:
                    execute_quick_alert(sig_dict, bridge, manager, tracker, _quick_alerts)
                    return

                key = _qa_key(sig_dict["symbol"], sig_dict["action"], canal_name)
                qa_list = _quick_alerts.get(key, [])
                found_qa = None
                found_idx = -1
                zone_low = sig_dict["zone_low"]
                zone_high = sig_dict["zone_high"]

                for idx, qa in enumerate(qa_list):
                    qa_price = qa["entry_price"]
                    if zone_low - FUSION_TOLERANCE <= qa_price <= zone_high + FUSION_TOLERANCE:
                        found_qa = qa
                        found_idx = idx
                        break

                if found_qa is not None:
                    merge_quick_alert(found_qa, key, sig_dict, bridge, manager, tracker, _quick_alerts)
                else:
                    # --- Hors tolérance de fusion : mettre à jour SL/TP du QA existant
                    # (pas de fermeture, pas de 2ème position) ---
                    updated_qa = False
                    real_sl = sig_dict["sl"]
                    tp_final_fusion = sig_dict["tps"][-1] if sig_dict["tps"] else 0
                    for idx, qa in enumerate(qa_list):
                        ticket = qa.get("ticket")
                        if ticket:
                            pos = mt5.positions_get(ticket=ticket)
                            if pos:
                                # QA actif → mettre à jour SL/TP avec ceux du signal complet
                                bridge.modify_sl_tp(ticket, real_sl, tp_final_fusion, "[FUSION-SL-TP-OOT]")
                                entry_qa = qa.get("entry")
                                if entry_qa:
                                    for t in entry_qa.get("tickets", []):
                                        if t["ticket"] == ticket:
                                            t["tp_final"] = tp_final_fusion
                                            t["tp_target"] = tp_final_fusion
                                            t["tp3"] = tp_final_fusion
                                            break
                                    entry_qa["signal"] = sig_dict
                                    entry_qa["_is_quick_alert"] = False
                                updated_qa = True
                                log.info(f"[FUSION OOT] QA #{ticket} SL/TP mis à jour (hors ±{FUSION_TOLERANCE}) SL={real_sl} TP={tp_final_fusion}")
                                send_alert_sync(msg.alert_fusion_oot(action, ch_num, ticket, real_sl, tp_final_fusion))
                                break
                            else:
                                # QA déjà fermé → ignorer le signal complet
                                log.info(f"[FUSION OOT] QA #{ticket} déjà fermé → signal complet ignoré")
                                qa_list.pop(idx)
                                if not qa_list:
                                    _quick_alerts.pop(key, None)
                                updated_qa = True
                                break

                    if not updated_qa:
                        # Aucun QA trouvé → exécuter le signal complet normalement
                        execute_signal(sig_dict, bridge, manager, tracker)

        # Banner
        mode = "🔲 DEMO" if DEMO_MODE else "💰 LIVE"
        log.info("=" * 55)
        log.info(f" Mode: {mode}")
        log.info(f" Lot :  total : {LOT_SIZE} | unique : {LOT_UNIQUE_TRADE}")
        log.info(f" Gain fixe par position : {TP_FIXED_GAIN_USD}$")
        log.info(f" BE déclenché à : {PNL_TRIGGER_USD}$")
        log.info(f" Objectif quotidien : {DAILY_PROFIT_LIMIT}$")
        log.info(f" Filtre horaire : {'ON' if TIME_FILTER_ENABLED else 'OFF'} ({TRADING_START_HOUR}h-{TRADING_END_HOUR}h UTC)")
        log.info(f" Filtre news : {'ON' if NEWS_ENABLED else 'OFF'}")
        if NEWS_ENABLED:
            log.info(f"   Impact min : {NEWS_MIN_IMPACT.upper()}")
            log.info(f"   Défaut    : {NEWS_BLOCK_MIN}/{NEWS_CLOSE_MIN}/{NEWS_AFTER_MIN} min (avant-bloc/avant-close/après)")
            log.info(f"   NFP/CPI   : {NEWS_BLOCK_MIN_NFPCPI}/{NEWS_CLOSE_MIN_NFPCPI}/{NEWS_AFTER_MIN_NFPCPI} min")
            log.info(f"   FOMC      : {NEWS_BLOCK_MIN_FOMC}/{NEWS_CLOSE_MIN_FOMC}/{NEWS_AFTER_MIN_FOMC} min")
            log.info(f"   Spike     : {NEWS_BLOCK_MIN_SPIKE}/{NEWS_CLOSE_MIN_SPIKE}/{NEWS_AFTER_MIN_SPIKE} min")
        log.info(f" Max signaux actifs : {MAX_POSITIONS}")
        log.info(f" TimesFM : {'ACTIVÉ' if TIMESFM_ENABLED else 'DÉSACTIVÉ'}")
        if TIMESFM_ENABLED:
            log.info(f"   Timeframe : {TIMESFM_TIMEFRAME} | Contexte : {TIMESFM_CONTEXT_BARS} bars")
            log.info(f"   Horizon : {TIMESFM_HORIZON} bougies")
            log.info(f"   Seuil move : {TIMESFM_MIN_MOVE_PIPS} pips | Confiance min : {TIMESFM_MIN_CONFIDENCE}")
            log.info(f"   Symbole MT5 : {TIMESFM_SYMBOL}")
        log.info("=" * 55)

        await client.run_until_disconnected()

    except Exception as e:
        send_alert_sync(
            f"💥 BOT CRASH !\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Erreur: {type(e).__name__}: {e}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        log.error(f"CRASH: {e}", exc_info=True)
        raise
    finally:
        if manager:
            manager.stop()
        if news_mgr:
            news_mgr.stop()
        bridge.disconnect()
        tracker.print_final_report()
        log.info("[SHUTDOWN] Bot arrêté proprement.")

if __name__ == "__main__":
    asyncio.run(main())
