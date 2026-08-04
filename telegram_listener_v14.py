"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 14.0.0 — Multi-Position A/B Testing & Capped SL
=============================================================
MODIFICATIONS v13.0.0 (depuis v12.0.0) :

■ NOUVEAU : Lecture des canaux depuis un dossier Telegram
- client.get_dialogs(folder=N) pour charger les canaux d'un dossier
- Configuration via TG_FOLDER dans .env
- Fallback sur TG_CHANNEL_* si TG_FOLDER non défini

■ HÉRITÉ DE V12 :
- Chargement dynamique des canaux (scan TG_CHANNEL_*)
- News filtrées par jour de trading
- QA sans prix (MP) / avec prix (AL1/AL2)
- Tolérance SELL corrigée
- Déduplication robuste
- Code mort supprimé (-452 lignes)
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

# =============================================================
# CHARGEMENT DYNAMIQUE DES CANAUX TELEGRAM
# =============================================================
# Scan l'env pour TOUS les TG_CHANNEL_* (sans limite de nombre).
# TG_CHANNEL ou TG_CHANNEL_1 = canal 1, TG_CHANNEL_2 = canal 2, etc.
# Pour ajouter un canal : ajouter TG_CHANNEL_N=<id> dans .env, c'est tout.
# =============================================================
def _load_channels_from_env():
    channels = []
    tg_base = os.getenv("TG_CHANNEL", "")
    if tg_base:
        channels.append(("TG_CHANNEL_1", tg_base))
    for key, val in sorted(os.environ.items()):
        if key == "TG_CHANNEL":
            continue
        if key.startswith("TG_CHANNEL_") and key[11:].isdigit():
            num = int(key[11:])
            if val and (num != 1 or not tg_base):
                channels.append((key, val))
    channels.sort(key=lambda x: int(x[0].replace("TG_CHANNEL_", "")))
    return channels

_CHANNELS_LIST = _load_channels_from_env()
CHANNEL_NAME = _CHANNELS_LIST[0][1] if _CHANNELS_LIST else ""

CHANNEL_NUM_MAP = {}
for _env_name, _val in _CHANNELS_LIST:
    _num = int(_env_name.replace("TG_CHANNEL_", ""))
    CHANNEL_NUM_MAP[_val] = _num
    if _val.lstrip("-").isdigit():
        CHANNEL_NUM_MAP[_val.lstrip("-")] = _num

# ★ DOSSIER TELEGRAM : si TG_FOLDER est défini, le bot charge les canaux
# depuis ce dossier Telegram au lieu de TG_CHANNEL_*
# Ex: TG_FOLDER=2 → lit le dossier #2 de Telegram
TG_FOLDER = os.getenv("TG_FOLDER", "")

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
TP_FIXED_GAIN_USD = float(os.getenv("TP_FIXED_GAIN_USD", "8.0"))
# ★ BE_USD : quand le BE se déclenche, le SL n'est plus posé exactement à l'entrée
# mais avec une petite marge de sécurité (BE_USD $) du côté DÉFAVORABLE — évite un
# stop sur un simple retour à l'entrée (bruit/spread). Ex: BUY @4000, BE_USD=3 → SL=3997.
BE_USD = float(os.getenv("BE_USD", "3"))  # pas de changement, déjà 3
PNL_TRIGGER_USD = float(os.getenv("PNL_TRIGGER_USD", "5.0"))

# === SL PLAFONNÉ ===
# Distance SL maximale en $ (pour XAUUSD 0.01 lot, 1$ prix = 1$ P&L)
# Si le SL du signal dépasse cette distance, il est capé.
MAX_SL_USD = float(os.getenv("MAX_SL_USD", "10.0"))

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

def send_alert_sync(message: str, _retries: int = 1):
    """Envoie non-bloquant, PAS de retry.
    - Timeout 15s (généreux pour éviter les faux timeouts)
    - Pas de retry : un doublon est pire qu'une alerte perdue
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
    try:
        coro = _alert_client.send_message(TG_ALERT_CHANNEL, message)
        future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
        future.result(timeout=15)
    except TimeoutError:
        log.warning(f"[ALERT] Timeout envoi alerte Telegram (15s) — message possiblement livré")
    except Exception as e:
        log.warning(f"[ALERT] Erreur envoi alerte Telegram: {type(e).__name__}: {e}")

# =============================================================
# PERFORMANCE TRACKER
# =============================================================
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
from signal_parser_v14 import SignalParser, is_spam
import bot_messages_v14 as msg

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
        self._force_log = False

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
        last_day = None
        while not self._stop:
            try:
                now = time.time()
                current_day = get_trading_day_start().day
                # ★ Re-fetch forcé quand le jour de trading change
                if last_day is not None and current_day != last_day:
                    _log_mgmt("[NEWS] Nouveau jour de trading → re-fetch des news")
                    self._force_log = True
                    await asyncio.to_thread(self._fetch_news)
                    last_fetch = now
                last_day = current_day
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
            # Filtrer par impact + devise
            filtered = [
                n for n in data
                if n.get("impact", "").lower() in _impact_levels
                and (n.get("country", "") in ("USD", "XAU") or n.get("currency", "") in ("USD", "XAU"))
            ]
            # ★ FIX : ne garder que les news du JOUR DE TRADING en cours
            # (ff_calendar_thisweek.json retourne toute la semaine)
            trading_day = get_trading_day_start()
            trading_day_date = trading_day.date()
            self._news = []
            for n in filtered:
                try:
                    news_dt = datetime.fromisoformat(n["date"].replace("Z", "+00:00"))
                    if news_dt.date() == trading_day_date:
                        self._news.append(n)
                except Exception:
                    # Si la date est invalide, on garde la news par sécurité
                    self._news.append(n)
            # ★ FIX #8 : log les news 1h avant l'ouverture NY (12:30 UTC)
            now_utc = datetime.now(timezone.utc)
            is_pre_ny = (now_utc.hour == 12 and 25 <= now_utc.minute <= 35)
            if is_pre_ny or not hasattr(self, '_news_logged') or self._force_log:
                self._news_logged = True
                self._force_log = False
                log.info(f"{len(self._news)} NEWS HIGH IMPACT ({trading_day_date})")
                if len(self._news) == 0 and len(data) > 0:
                    sample_keys = list(data[0].keys())
                    log.warning(msg.log_news_zero_debug(len(data), sample_keys))
                elif len(self._news) > 0:
                    for n in self._news:
                        raw_date = n.get('date', '?')
                        # Séparer date et heure : 2026-07-29T14:00:00-04:00 → 2026-07-29 | 14:00:00-04:00
                        if 'T' in raw_date:
                            date_part, time_part = raw_date.split('T', 1)
                            formatted = f"{date_part} | {time_part}"
                        else:
                            formatted = raw_date
                        country = n.get('country', '?')
                        _log_mgmt(f"NEWS: {n.get('title', '?')} @ {formatted} ({country})")
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
                _log_mgmt(msg.log_news_closing_positions(active_title, active_diff))
                if self.manager:
                    self._close_all()
            self._blocked = True
        elif should_block:
            if not self._blocked:
                _log_mgmt(msg.log_news_blocking_signals(active_title, active_diff))
            self._blocked = True
        else:
            if self._blocked:
                _log_mgmt(msg.log_news_resumed(active_title or "Fenêtre news"))
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
        _log_mgmt(f"SL MOVE canal {channel_num} → {new_sl} sur {updated} positions")


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
    def __init__(self, bridge: MT5Bridge, quick_alerts_ref=None):
        self.bridge = bridge
        self.active = []
        self._lock = threading.Lock()
        self._daily_lock = threading.RLock()  # FIX: RLock pour éviter deadlock quand _shutdown_for_daily_limit appelle _update_daily_pnl
        self._stop = False
        self._task = None
        self._quick_alerts_ref = quick_alerts_ref if quick_alerts_ref is not None else {}
        self._daily_limit_reached = False  # FIX: flag pour bloquer le trading sans perdre les données du rapport

        # ★★★ WHITELIST des rôles autorisés à déclencher le BE ★★★
        self._pos_cache = None  # rafraîchi à chaque cycle par _refresh_pos_cache()
        # ★ Tous les signaux sont MARKET, jamais de pending
        self._be_allowed_roles = {
            "market_single",       # PU1, PU2, ZN1, ZN2
            "quick_market",        # QA (AL-MP)
        }

        self._daily_pnl = self._recover_daily_pnl()
        self._daily_pnl_day = get_trading_day_start().day
        self._end_of_day_done = False  # flag pour éviter les fermetures répétées



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
                self._end_of_day_done = False  # reset pour le nouveau jour
                self._daily_limit_reached = False  # FIX: reset du flag quotidien
                log.info(f"RESET JOURNALIER A {TRADING_START_HOUR}H UTC")
            self._daily_pnl += pnl
            total = self._daily_pnl + self._get_floating_pnl()
        log.debug(msg.log_daily_pnl_periodic(self._daily_pnl, self._get_floating_pnl(), total))

    def _check_daily_pnl_limit(self) -> bool:
        with self._daily_lock:
            start = get_trading_day_start()
            if start.day != self._daily_pnl_day:
                self._daily_pnl = 0.0
                self._daily_pnl_day = start.day
                self._end_of_day_done = False  # reset pour le nouveau jour
                self._daily_limit_reached = False  # FIX: reset du flag quotidien
                log.info(f"RESET JOURNALIER A {TRADING_START_HOUR}H UTC")
            total_pnl = self._daily_pnl + self._get_floating_pnl()
            if DAILY_PROFIT_LIMIT > 0 and total_pnl >= DAILY_PROFIT_LIMIT:
                log.info(f"Limite quotidienne atteinte : {total_pnl:.2f}$ / {DAILY_PROFIT_LIMIT}$")
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
        log.info("OBJECTIF QUOTIDIEN ATTEINT")
        log.info(f"Limite: {DAILY_PROFIT_LIMIT}$")

        positions = mt5.positions_get()
        nb_positions = len([p for p in positions if p.magic == MAGIC_NUMBER]) if positions else 0

        cancelled = self._cancel_all_pending_orders()
        total_pnl = self._close_all_positions()

        with self._daily_lock:
            self._update_daily_pnl(total_pnl)
            total = self._daily_pnl + self._get_floating_pnl()

        # ★ FIX : ne PAS vider self.active ici — les données sont nécessaires
        # pour le rapport de fin de journée. Le flag _daily_limit_reached
        # bloque le traitement des nouveaux signaux.
        self._daily_limit_reached = True

        log.info(msg.log_daily_limit_header())
        log.info(msg.log_daily_limit_detail(total, nb_positions, cancelled))

        if ALERT_DAILY_PERFORMANCE:
            send_alert_sync(msg.alert_daily_limit(total, DAILY_PROFIT_LIMIT, nb_positions, cancelled))

    def _shutdown_end_of_day(self):
        """Ferme toutes les positions à TRADING_END_HOUR et génère le rapport quotidien."""
        self._end_of_day_done = True
        log.info(f" FIN DE JOURNÉE {TRADING_END_HOUR}H UTC — Fermeture de toutes les positions")

        positions = mt5.positions_get()
        nb_positions = len([p for p in positions if p.magic == MAGIC_NUMBER]) if positions else 0

        # Fermer les positions et collecter les données AVANT clear
        cancelled = 0
        if nb_positions > 0:
            cancelled = self._cancel_all_pending_orders()
            total_pnl = self._close_all_positions()
            time.sleep(0.5)
        else:
            total_pnl = 0.0

        # ★ Collecter les données du rapport AVANT de vider les entrées
        report_data = self._collect_daily_report_data()

        with self._daily_lock:
            self._update_daily_pnl(total_pnl)
            total = self._daily_pnl + self._get_floating_pnl()

        self._clear_all_entries()

        # Générer et envoyer le rapport
        if ALERT_DAILY_PERFORMANCE:
            from datetime import date as date_cls
            today = date_cls.today().isoformat()
            report = msg.report_daily_full(
                date=today,
                pnl_realise=self._daily_pnl,
                trades=report_data['trades'],
                wins=report_data['wins'],
                losses=report_data['losses'],
                winrate=report_data['winrate'],
                methods=report_data['methods'],
                channels=report_data['channels'],
                tp_count=report_data['tp_count'],
                tp_pnl=report_data['tp_pnl'],
                sl_count=report_data['sl_count'],
                sl_pnl=report_data['sl_pnl'],
                total_signals=report_data['total_signals'],
                max_drawdown=report_data['max_drawdown'],
            )
            send_alert_sync(report)
            log.info(report)

            # ★ Générer et envoyer le PDF
            pdf_path = _generate_daily_report_pdf(report_data, self._daily_pnl, today)
            if pdf_path:
                _send_telegram_document(pdf_path, f"📊 Rapport {today}")

        else:
            log.info(msg.log_daily_limit_header())
            log.info(msg.log_daily_limit_detail(total, nb_positions, cancelled))

    def _collect_daily_report_data(self) -> dict:
        """Collecte les données pour le rapport quotidien."""
        # Structure: {method: {trades, wins, losses, pnl}}, {ch_num: {trades, wins, losses, pnl}}
        methods = {}
        channels = {}
        tp_count = tp_pnl = sl_count = sl_pnl = 0
        total_trades = total_wins = total_losses = 0
        total_signals = 0
        # Max drawdown tracking
        running_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0

        # Map ch_num → canal name
        ch_name_map = {}
        for _env_name, _val in _CHANNELS_LIST:
            _num = int(_env_name.replace('TG_CHANNEL_', ''))
            ch_name_map[_num] = _val

        for entry in list(self.active):
            total_signals += 1
            mt5_comment = entry.get('_mt5_comment', '')
            signal = entry.get('signal', {})
            source_channel = signal.get('source_channel', '')

            # Extraire le numéro de canal (CH1, CH2...)
            ch_num = 0
            for part in mt5_comment.split('-'):
                if part.startswith('CH') and part[2:].isdigit():
                    ch_num = int(part[2:])
                    break

            # Nom du canal (sans le @)
            ch_name = ch_name_map.get(ch_num, source_channel)
            if ch_name.startswith('@'):
                ch_name = ch_name[1:]

            for t in entry.get('tickets', []):
                pnl = t.get('_last_pnl', 0.0)
                reported = t.get('_reported', False)
                role = t.get('role', 'unknown')

                if not reported:
                    # Position encore ouverte — récupérer le P&L actuel
                    pos = self._get_pos(t['ticket'])
                    if pos:
                        pnl = pos.profit
                        close_reason = 'OPEN'
                    else:
                        continue
                else:
                    close_reason = self._get_close_reason(t['ticket'], entry.get('signal', {}).get('symbol', ''))

                # Max drawdown
                running_pnl += pnl
                if running_pnl > peak_pnl:
                    peak_pnl = running_pnl
                dd = peak_pnl - running_pnl
                if dd > max_drawdown:
                    max_drawdown = dd

                # Stats par méthode
                if role not in methods:
                    methods[role] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
                methods[role]['trades'] += 1
                methods[role]['pnl'] += pnl
                if pnl > 0:
                    methods[role]['wins'] += 1
                else:
                    methods[role]['losses'] += 1

                # Stats par canal
                if ch_num not in channels:
                    channels[ch_num] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'name': ch_name}
                channels[ch_num]['trades'] += 1
                channels[ch_num]['pnl'] += pnl
                if pnl > 0:
                    channels[ch_num]['wins'] += 1
                else:
                    channels[ch_num]['losses'] += 1

                # Stats TP/SL
                if close_reason == 'TP':
                    tp_count += 1
                    tp_pnl += pnl
                elif close_reason == 'SL':
                    sl_count += 1
                    sl_pnl += pnl

                # Total
                total_trades += 1
                if pnl > 0:
                    total_wins += 1
                else:
                    total_losses += 1

        winrate = total_wins / total_trades * 100 if total_trades > 0 else 0

        # Formater les méthodes pour bot_messages
        # ★ P4a + P4b combinés en une seule ligne P4 (50/50 split)
        method_names = {
            'tp_fixe': 'P1 TP Fixe',
            'be_scale': 'P2 BE Scale',
            'trailing': 'P3 Trailing',
            'partial': 'P4 Partial (50/50)',
        }
        # Fusionner partial_quick + partial_trail → partial
        if 'partial_quick' in methods or 'partial_trail' in methods:
            pq = methods.get('partial_quick', {'trades': 0, 'wins': 0, 'pnl': 0.0})
            pt = methods.get('partial_trail', {'trades': 0, 'wins': 0, 'pnl': 0.0})
            methods['partial'] = {
                'trades': pq['trades'] + pt['trades'],
                'wins': pq['wins'] + pt['wins'],
                'pnl': pq['pnl'] + pt['pnl'],
            }
        methods_list = []
        for role in ['tp_fixe', 'be_scale', 'trailing', 'partial']:
            if role in methods:
                m = methods[role]
                m['name'] = method_names.get(role, role)
                methods_list.append(m)

        # Formater les canaux
        channels_list = [{'ch_num': ch, **stats} for ch, stats in channels.items()]

        return {
            'total_signals': total_signals,
            'trades': total_trades,
            'wins': total_wins,
            'losses': total_losses,
            'winrate': winrate,
            'max_drawdown': max_drawdown,
            'methods': methods_list,
            'channels': channels_list,
            'tp_count': tp_count,
            'tp_pnl': tp_pnl,
            'sl_count': sl_count,
            'sl_pnl': sl_pnl,
        }

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
            _log_mgmt(msg.log_be_combined(mt5_comment, 1, be_price))
            _alert_mgmt(msg.alert_be_activated(action, signal['symbol'], 1, be_price, target_gain, mt5_comment, 0))

    # =============================================================
    # GESTION PAR RÔLE (5 méthodes A/B testing)
    # =============================================================

    def _manage_tp_fixe(self, t: dict, pos, entry: dict, action: str):
        """P1: TP Fixe — BE classique (méthode actuelle)
        BE à PNL_TRIGGER_USD → SL à entry ± BE_USD, TP à entry ± TP_FIXED_GAIN_USD"""
        if pos.profit >= PNL_TRIGGER_USD:
            entry_price = t["entry_price"]
            sym = mt5.symbol_info(pos.symbol)
            if action == "BUY":
                be_price = round(entry_price - BE_USD, sym.digits if sym else 2)
                be_tp = round(entry_price + TP_FIXED_GAIN_USD, sym.digits if sym else 2)
            else:
                be_price = round(entry_price + BE_USD, sym.digits if sym else 2)
                be_tp = round(entry_price - TP_FIXED_GAIN_USD, sym.digits if sym else 2)
            if self.bridge.modify_sl_tp(t["ticket"], be_price, be_tp, f"[P1-BE]"):
                t["be_active"] = True
                t["be_sl"] = be_price
                t["tp_final"] = be_tp
                if LOG_TRADE_MANAGEMENT:
                    log.info(msg.log_p1_be(t["ticket"], be_price, be_tp))
                if ALERT_TRADE_MANAGEMENT:
                    send_alert_sync(msg.alert_p1_be(t["ticket"], be_price, be_tp))

    def _manage_be_scale(self, t: dict, pos, entry: dict, action: str):
        """P2: BE Escaladé — SL progressif selon le profit
        +5$ → SL à entry, +10$ → SL entry+3$, +15$ → SL entry+7$"""
        entry_price = t["entry_price"]
        current_level = t.get("be_scale_level", 0)
        sym = mt5.symbol_info(pos.symbol)
        digits = sym.digits if sym else 2

        for i, level in enumerate(BE_SCALE_LEVELS):
            if i <= current_level:
                continue
            if pos.profit >= level["trigger"]:
                if action == "BUY":
                    new_sl = round(entry_price + level["sl_offset"], digits)
                else:
                    new_sl = round(entry_price - level["sl_offset"], digits)
                if self.bridge.modify_sl_tp(t["ticket"], new_sl, pos.tp, f"[P2-SCALE L{i}]"):
                    t["be_scale_level"] = i
                    t["be_sl"] = new_sl
                    if LOG_TRADE_MANAGEMENT:
                        log.info(msg.log_p2_be_scale(t["ticket"], i, new_sl, pos.profit))
                    if ALERT_TRADE_MANAGEMENT:
                        send_alert_sync(msg.alert_p2_be_scale(t["ticket"], i, new_sl))

    def _manage_trailing(self, t: dict, pos, entry: dict, action: str):
        """P3: Trailing Stop — pas de TP fixe, trailing TRAILING_STOP_USD$"""
        entry_price = t["entry_price"]
        sym = mt5.symbol_info(pos.symbol)
        digits = sym.digits if sym else 2

        if action == "BUY":
            new_sl = round(pos.price_current - TRAILING_STOP_USD, digits)
            current_sl = pos.sl
            if new_sl > current_sl and new_sl > entry_price:
                if self.bridge.modify_sl_tp(t["ticket"], new_sl, pos.tp, f"[P3-TRAIL]"):
                    t["trail_active"] = True
                    t["be_sl"] = new_sl
                    if LOG_TRADE_MANAGEMENT:
                        log.info(msg.log_p3_trail(t["ticket"], new_sl))
                    if ALERT_TRADE_MANAGEMENT:
                        send_alert_sync(msg.alert_p3_trail(t["ticket"], new_sl))
        else:
            new_sl = round(pos.price_current + TRAILING_STOP_USD, digits)
            current_sl = pos.sl
            if new_sl < current_sl and new_sl < entry_price:
                if self.bridge.modify_sl_tp(t["ticket"], new_sl, pos.tp, f"[P3-TRAIL]"):
                    t["trail_active"] = True
                    t["be_sl"] = new_sl
                    if LOG_TRADE_MANAGEMENT:
                        log.info(msg.log_p3_trail(t["ticket"], new_sl))
                    if ALERT_TRADE_MANAGEMENT:
                        send_alert_sync(msg.alert_p3_trail(t["ticket"], new_sl))

    def _manage_partial_quick(self, t: dict, pos, entry: dict, action: str):
        """P4a: Partial Quick — fermeture rapide à +5$"""
        if pos.profit >= P4A_QUICK_TARGET:
            if self.bridge.close_position(t["ticket"], "P4a-QUICK"):
                t["be_active"] = True  # marquer comme géré
                if LOG_TRADE_MANAGEMENT:
                    log.info(msg.log_p4a_close(t["ticket"], pos.profit))
                if ALERT_TRADE_MANAGEMENT:
                    send_alert_sync(msg.alert_p4a_close(t["ticket"], pos.profit))

    def _manage_partial_trail(self, t: dict, pos, entry: dict, action: str):
        """P4b: Partial Trail — trailing PARTIAL_TRAIL_USD$"""
        entry_price = t["entry_price"]
        sym = mt5.symbol_info(pos.symbol)
        digits = sym.digits if sym else 2

        if action == "BUY":
            new_sl = round(pos.price_current - PARTIAL_TRAIL_USD, digits)
            current_sl = pos.sl
            if new_sl > current_sl and new_sl > entry_price:
                if self.bridge.modify_sl_tp(t["ticket"], new_sl, pos.tp, f"[P4b-TRAIL]"):
                    t["trail_active"] = True
                    t["be_sl"] = new_sl
                    if LOG_TRADE_MANAGEMENT:
                        log.info(msg.log_p4b_trail(t["ticket"], new_sl))
                    if ALERT_TRADE_MANAGEMENT:
                        send_alert_sync(msg.alert_p4b_trail(t["ticket"], new_sl))
        else:
            new_sl = round(pos.price_current + PARTIAL_TRAIL_USD, digits)
            current_sl = pos.sl
            if new_sl < current_sl and new_sl < entry_price:
                if self.bridge.modify_sl_tp(t["ticket"], new_sl, pos.tp, f"[P4b-TRAIL]"):
                    t["trail_active"] = True
                    t["be_sl"] = new_sl
                    if LOG_TRADE_MANAGEMENT:
                        log.info(msg.log_p4b_trail(t["ticket"], new_sl))
                    if ALERT_TRADE_MANAGEMENT:
                        send_alert_sync(msg.alert_p4b_trail(t["ticket"], new_sl))

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

        # ★ FIN DE JOURNÉE : fermer toutes les positions à TRADING_END_HOUR
        if TIME_FILTER_ENABLED and not self._end_of_day_done:
            if now.hour >= TRADING_END_HOUR:
                self._shutdown_end_of_day()
                return

        if not self._check_daily_pnl_limit() or self._daily_limit_reached:
            if self.active and not self._daily_limit_reached:
                log.debug("[DAILY P&L] Limite atteinte ! Fermeture de toutes les positions et annulation des ordres.")
                self._shutdown_for_daily_limit()
            # Bloquer le traitement des positions (signaux déjà ignorés par le handler)
            # mais laisser passer la vérification fin de journée ci-dessus
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
                    if LOG_TRADE_MANAGEMENT:
                        _log_mgmt(msg.log_close_combined(mt5_comment, label, idx, total, t['ticket'], pnl))
                        _log_mgmt(msg.log_daily_pnl_final(daily_pnl_now))

                    if ALERT_TRADE_MANAGEMENT:
                        _alert_mgmt(msg.alert_close(label, action, symbol, pnl, idx, total, t['ticket'], daily_pnl_now, mt5_comment))

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
                # Note : le P&L quotidien est déjà mis à jour par ticket (voir boucle ci-dessus),
                # pas besoin de le ré-additionner ici (éviterait un double comptage).
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)
                continue

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 3 : GESTION PAR RÔLE ★★★
            # ══════════════════════════════════════════════════════════════
            for t in entry.get("tickets", []):
                if t.get("be_active") or not self._get_pos(t["ticket"]):
                    continue
                role = t.get("role", "tp_fixe")
                pos = self._get_pos(t["ticket"])
                if not pos:
                    continue
                entry_price = t.get("entry_price", 0)
                if entry_price == 0:
                    continue

                if role == "tp_fixe":
                    self._manage_tp_fixe(t, pos, entry, action)
                elif role == "be_scale":
                    self._manage_be_scale(t, pos, entry, action)
                elif role == "trailing":
                    self._manage_trailing(t, pos, entry, action)
                elif role == "partial_quick":
                    self._manage_partial_quick(t, pos, entry, action)
                elif role == "partial_trail":
                    self._manage_partial_trail(t, pos, entry, action)

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
    log.warning(f"<< WARNING >> CONFLIT {symbol} (canal {canal}) : entrant={new_action} existant={opposite}")
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

def _cap_sl(action: str, entry_price: float, signal_sl: float, max_sl_usd: float) -> float:
    """Plafonne le SL à max_sl_usd$ de l'entrée.
    Pour XAUUSD 0.01 lot : 1$ de prix = 1$ de P&L."""
    distance = abs(entry_price - signal_sl)
    if distance <= max_sl_usd:
        return signal_sl
    if action == "BUY":
        capped = round(entry_price - max_sl_usd, 2)
    else:
        capped = round(entry_price + max_sl_usd, 2)
    _log_mgmt(msg.log_sl_cap(signal_sl, capped, distance, max_sl_usd))
    return capped


# =============================================================
# POSITIONS MULTI-MÉTHODES (A/B Testing)
# =============================================================
# Pour chaque signal, on ouvre 5 positions simultanées (même prix, même lot)
# et on les gère avec des méthodes différentes pour comparer.
#
# P1: TP Fixe — BE classique (actuel)
# P2: BE Escaladé — SL progressif selon le profit
# P3: Trailing Stop — pas de TP fixe, trailing 5$
# P4a: Partial Quick — TP rapide à +5$
# P4b: Partial Trail — pas de TP fixe, trailing 3$
# =============================================================

TRAILING_STOP_USD = float(os.getenv("TRAILING_STOP_USD", "7.0"))
PARTIAL_TRAIL_USD = float(os.getenv("PARTIAL_TRAIL_USD", "5.0"))

# === P2 BE ESCALADÉ — paliers configurables ===
P2_TP_OFFSET = float(os.getenv("P2_TP_OFFSET", "15.0"))
P2_TRIGGER_1 = float(os.getenv("P2_TRIGGER_1", "4.0"))
P2_SL_OFFSET_1 = float(os.getenv("P2_SL_OFFSET_1", "0.0"))
P2_TRIGGER_2 = float(os.getenv("P2_TRIGGER_2", "8.0"))
P2_SL_OFFSET_2 = float(os.getenv("P2_SL_OFFSET_2", "2.0"))
P2_TRIGGER_3 = float(os.getenv("P2_TRIGGER_3", "12.0"))
P2_SL_OFFSET_3 = float(os.getenv("P2_SL_OFFSET_3", "5.0"))

# === P4a PARTIAL QUICK — target configurable ===
P4A_TP_OFFSET = float(os.getenv("P4A_TP_OFFSET", "5.0"))  # pas de changement
P4A_QUICK_TARGET = float(os.getenv("P4A_QUICK_TARGET", "3.0"))

# Méthodes de gestion
METHODS = [
    {"suffix": "P1",  "role": "tp_fixe",      "tp_offset": None,          "desc": "TP Fixe"},
    {"suffix": "P2",  "role": "be_scale",      "tp_offset": P2_TP_OFFSET, "desc": "BE Escaladé"},
    {"suffix": "P3",  "role": "trailing",      "tp_offset": 0,             "desc": "Trailing"},
    {"suffix": "P4a", "role": "partial_quick", "tp_offset": P4A_TP_OFFSET, "desc": "Partial Quick"},
    {"suffix": "P4b", "role": "partial_trail", "tp_offset": 0,             "desc": "Partial Trail"},
]

# === ALERTES & LOGS ===
LOG_TRADE_MANAGEMENT = os.getenv("LOG_TRADE_MANAGEMENT", "true").lower() == "true"
ALERT_TRADE_MANAGEMENT = os.getenv("ALERT_TRADE_MANAGEMENT", "true").lower() == "true"
ALERT_DAILY_PERFORMANCE = os.getenv("ALERT_DAILY_PERFORMANCE", "true").lower() == "true"

def _log_mgmt(msg_text: str):
    """Log uniquement si LOG_TRADE_MANAGEMENT=true"""
    if LOG_TRADE_MANAGEMENT:
        log.info(msg_text)

def _alert_mgmt(msg_text: str):
    """Alerte Telegram uniquement si ALERT_TRADE_MANAGEMENT=true"""
    if ALERT_TRADE_MANAGEMENT:
        send_alert_sync(msg_text)


def _generate_daily_report_pdf(report_data: dict, daily_pnl: float, date_str: str) -> str:
    """Génère un PDF du rapport quotidien et retourne le chemin du fichier."""
    try:
        from fpdf import FPDF
    except ImportError:
        log.warning("fpdf2 non installé — PDF non généré")
        return ""

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Titre
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, f"Performance du {date_str}", ln=True, align="C")
    pdf.ln(5)

    # Résumé
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Resume Global", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    trades = report_data['trades']
    wins = report_data['wins']
    losses = report_data['losses']
    winrate = report_data['winrate']
    total_signals = report_data.get('total_signals', 0)
    max_dd = report_data.get('max_drawdown', 0.0)
    pdf.cell(0, 6, f"P&L realise : {daily_pnl:+.2f}$", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Total signaux : {total_signals} | Total trades : {trades}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Wins : {wins} | Losses : {losses} | Winrate : {winrate:.1f}%", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Max Drawdown : {max_dd:.2f}$", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Par methode
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Performance par methode", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(35, 6, "Methode", border=1)
    pdf.cell(25, 6, "P&L", border=1, align="C")
    pdf.cell(18, 6, "Trades", border=1, align="C")
    pdf.cell(15, 6, "Win", border=1, align="C")
    pdf.cell(15, 6, "Loss", border=1, align="C")
    pdf.cell(18, 6, "Winrate", border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for m in report_data['methods']:
        wr = m['wins'] / m['trades'] * 100 if m['trades'] > 0 else 0
        m_losses = m.get('losses', m['trades'] - m['wins'])
        pdf.cell(35, 6, m['name'], border=1)
        pdf.cell(25, 6, f"{m['pnl']:+.2f}$", border=1, align="C")
        pdf.cell(18, 6, str(m['trades']), border=1, align="C")
        pdf.cell(15, 6, str(m['wins']), border=1, align="C")
        pdf.cell(15, 6, str(m_losses), border=1, align="C")
        pdf.cell(18, 6, f"{wr:.1f}%", border=1, align="C")
        pdf.ln()
    pdf.ln(5)

    # Par canal
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Performance par canal", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(18, 6, "Canal", border=1)
    pdf.cell(48, 6, "Nom", border=1)
    pdf.cell(25, 6, "P&L", border=1, align="C")
    pdf.cell(18, 6, "Trades", border=1, align="C")
    pdf.cell(15, 6, "Win", border=1, align="C")
    pdf.cell(15, 6, "Loss", border=1, align="C")
    pdf.cell(18, 6, "Winrate", border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for c in sorted(report_data['channels'], key=lambda x: x['pnl'], reverse=True):
        wr = c['wins'] / c['trades'] * 100 if c['trades'] > 0 else 0
        c_losses = c.get('losses', c['trades'] - c['wins'])
        name = c.get('name', '')[:22]
        pdf.cell(18, 6, f"CH{c['ch_num']}", border=1)
        pdf.cell(48, 6, name, border=1)
        pdf.cell(25, 6, f"{c['pnl']:+.2f}$", border=1, align="C")
        pdf.cell(18, 6, str(c['trades']), border=1, align="C")
        pdf.cell(15, 6, str(c['wins']), border=1, align="C")
        pdf.cell(15, 6, str(c_losses), border=1, align="C")
        pdf.cell(18, 6, f"{wr:.1f}%", border=1, align="C")
        pdf.ln()
    pdf.ln(5)

    # Clotures
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Clotures", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"TP : {report_data['tp_count']} trades | {report_data['tp_pnl']:+.2f}$", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"SL : {report_data['sl_count']} trades | {report_data['sl_pnl']:+.2f}$", new_x="LMARGIN", new_y="NEXT")

    filepath = f"daily_report_{date_str}.pdf"
    pdf.output(filepath)
    log.info(f"PDF rapport généré: {filepath}")
    return filepath


def _send_telegram_document(filepath: str, caption: str):
    """Envoie un fichier document à Telegram via l'alert client."""
    if not TG_ALERT_CHANNEL or not _alert_client or not _main_loop:
        return
    try:
        from telethon import types
        coro = _alert_client.send_file(
            TG_ALERT_CHANNEL,
            file=filepath,
            caption=caption,
        )
        future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
        future.result(timeout=30)
        log.info(f"PDF envoyé à Telegram: {filepath}")
    except Exception as e:
        log.warning(f"Erreur envoi PDF Telegram: {type(e).__name__}: {e}")

BE_SCALE_LEVELS = [
    {"trigger": P2_TRIGGER_1,  "sl_offset": P2_SL_OFFSET_1},
    {"trigger": P2_TRIGGER_2,  "sl_offset": P2_SL_OFFSET_2},
    {"trigger": P2_TRIGGER_3,  "sl_offset": P2_SL_OFFSET_3},
]


def _open_multi_positions(signal: dict, bridge: MT5Bridge, manager,
                          action: str, symbol: str, current: float,
                          sl: float, tp_final: float,
                          unique_lot: float, ch_num, canal: str,
                          prefix: str = "ZN1") -> bool:
    """Ouvre 5 positions simultanément pour A/B testing.
    Retourne True si au moins 1 position est ouverte."""
    tickets = []
    methods_desc = []

    for method in METHODS:
        suffix = method["suffix"]
        role = method["role"]
        tp_offset = method["tp_offset"]
        mt5_comment = f"CH{ch_num}-{prefix}-{suffix}"

        # Calculer le TP pour cette méthode
        if tp_offset is None:
            # P1: utiliser le TP du signal
            tp_method = tp_final
        elif tp_offset == 0:
            # P3/P4b: pas de TP fixe (trailing), mettre un TP très lointain
            if action == "BUY":
                tp_method = round(current + 500, 2)
            else:
                tp_method = round(current - 500, 2)
        else:
            # P2/P4a: TP à entry ± offset
            if action == "BUY":
                tp_method = round(current + tp_offset, 2)
            else:
                tp_method = round(current - tp_offset, 2)

        log.debug(f"  → {suffix} {action} @{current} lot={unique_lot} TP={tp_method} SL={sl}")
        try:
            t = bridge.place_market_order(signal, unique_lot, tp=tp_method, sl=sl, comment=mt5_comment)
        except Exception as e:
            log.error(f"  MARKET EXCEPTION {suffix}: {e}")
            t = None

        if t:
            tickets.append({
                "ticket": t, "lot": unique_lot, "role": role,
                "entry_price": current, "tp_final": tp_method,
                "sl_step": 0, "trail_active": False,
                "be_active": False, "be_sl": 0,
                "be_scale_level": 0,
            })
            methods_desc.append(f"{suffix}=#{t}")
            log.debug(f"  ✓ {suffix} #{t} @{current} TP={tp_method}")
        else:
            log.error(f"  ✗ {suffix} échoué")

    if not tickets:
        log.error("  ✗ Aucune position ouverte")
        return False

    # Enregistrer toutes les positions dans une seule entrée
    entry = {
        "signal": signal,
        "tickets": tickets,
        "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "_signal_id": f"{symbol}_{action}_{int(time.time())}",
        "_expected_positions": len(tickets),
        "_mt5_comment": f"CH{ch_num}-{prefix}",
    }
    manager.register(entry)

    # Alerte Telegram
    methods_str = " | ".join(methods_desc)
    mt5_comment = f"CH{ch_num}-{prefix}"
    _alert_mgmt(msg.alert_multi_pos_open(symbol, action, mt5_comment, current,
                                              unique_lot, len(tickets), methods_str,
                                              sl, tp_final, canal))
    if LOG_TRADE_MANAGEMENT:
        _log_mgmt(msg.log_multi_pos_open(action, symbol, current, len(tickets), methods_str))
    return True


def execute_signal(signal: dict, bridge: MT5Bridge, manager):
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
        _log_mgmt(f"Signal sans TP → TP généré: {generated_tp} (entry={entry_for_tp} ± {TP_FIXED_GAIN_USD}$)")

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

        # ★ SL plafonné
        avg_entry = (zone_low + zone_high) / 2
        sl = _cap_sl(action, avg_entry, sl, MAX_SL_USD)

        log.debug(f"ZN — {mt5_comment_zn} | zone={zone_low}-{zone_high} SL={sl} prix={current}")

        # ★ Multi-positions (A/B testing)
        zn_prefix = "ZN1" if in_zone else "ZN2"
        _open_multi_positions(signal, bridge, manager,
                              action, symbol, current, sl, tp_final,
                              unique_lot, ch_num, canal, prefix=zn_prefix)
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

        # ★ SL plafonné
        sl = _cap_sl(action, entry_price, sl, MAX_SL_USD)

        log.debug(f"{prefix} — {mt5_comment_pu} | entry={entry_price} SL={sl} prix={current}")

        # ★ Multi-positions (A/B testing)
        pu_prefix = "PU1" if is_type1 else "PU2"
        _open_multi_positions(signal, bridge, manager,
                              action, symbol, current, sl, tp_final,
                              unique_lot, ch_num, canal, prefix=pu_prefix)
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
                        quick_alerts: dict):
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
        _log_mgmt(f"[MARKET PRICE] Résolu: entry={entry_price}, SL={sl}, TP={tp}")

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
            # Favorable: prix <= entry (on vend plus cher que le marché actuel)
            # Défavorable: prix > entry + tolerance (le prix monte contre le SELL)
            is_unfavorable = current > entry_price + QA_PRICE_TOLERANCE
        if is_unfavorable:
            log.info(f"Quick Alert annulée — prix défavorable | "
                     f"prix={current} entry={entry_price} écart>défavorable de {QA_PRICE_TOLERANCE}")
            _alert_mgmt(msg.alert_qa_cancelled(action, symbol, ch_num, current, entry_price, QA_PRICE_TOLERANCE))
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

    # ★ DÉTERMINER LE TYPE DE COMMENTAIRE MT5
    # MP  = Market Price (pas de prix dans le signal, ex: BUY NOW)
    # AL1 = Alert avec prix — prix entre entry et SL ou prix exact
    # AL2 = Alert avec prix — prix dans tolérance (entry ± tolerance)
    if is_market_price:
        mt5_comment_qa = f"CH{ch_num}-MP"
    else:
        # Vérifier si le prix est dans la zone tolérance ou entre entry et SL
        in_tolerance = False
        if action == "BUY":
            # Tolérance: entry <= current <= entry + tolerance
            in_tolerance = entry_price <= current <= entry_price + QA_PRICE_TOLERANCE
        else:  # SELL
            # Tolérance: entry - tolerance <= current <= entry
            in_tolerance = entry_price - QA_PRICE_TOLERANCE <= current <= entry_price
        
        if in_tolerance:
            mt5_comment_qa = f"CH{ch_num}-AL2"
        else:
            # Prix entre entry et SL (favorable) — pas dans tolérance
            mt5_comment_qa = f"CH{ch_num}-AL1"
    _log_mgmt(msg.log_signal_detected(mt5_comment_qa, action, entry_price))
    log.debug(f"Quick Alert MARKET {action} {symbol} @{current} SL={sl}, TP={default_tp}")

    # ★ SL plafonné
    entry_for_sl = entry_price if entry_price else current
    sl = _cap_sl(action, entry_for_sl, sl, MAX_SL_USD)

    # ★ Multi-positions (A/B testing)
    qa_prefix = mt5_comment_qa.split("-")[1] if "-" in mt5_comment_qa else "MP"
    ok = _open_multi_positions(signal, bridge, manager,
                               action, symbol, current, sl, default_tp,
                               LOT_UNIQUE_TRADE, ch_num, canal, prefix=qa_prefix)
    if not ok:
        log.error("✗ QUICK MARKET échoué")
        return

    # Récupérer l'entrée créée par _open_multi_positions
    # (elle est déjà enregistrée dans manager.active)
    with manager._lock:
        entry = manager.active[-1] if manager.active else None
    if not entry:
        log.error("✗ Quick Alert — entrée non trouvée")
        return
    first_ticket = entry["tickets"][0]["ticket"] if entry.get("tickets") else 0

    if key not in quick_alerts:
        quick_alerts[key] = []
    quick_alerts[key].append({
        "entry": entry,
        "signal": signal,
        "ticket": first_ticket,
        "entry_price": entry_price,
        "is_market_price": signal.get("is_market_price", False),
        "time": datetime.now(timezone.utc),
    })
    log.debug(f"Quick Alert enregistré: {key}")

def merge_quick_alert(qa: dict, key: str, full_signal: dict,
                      bridge: MT5Bridge, manager: TradeManager,
                      quick_alerts: dict):
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
        _alert_mgmt(msg.alert_qa_already_closed(full_signal['action'], full_signal['symbol'], ch_num_fusion, qa_ticket, deal_pnl, close_reason))
    else:
        # QA actif -> mettre a jour SL et TP avec ceux du signal complet
        # ★ SL plafonné aussi lors de la fusion
        qa_entry_price = qa.get("entry_price", 0)
        real_sl = _cap_sl(full_signal["action"], qa_entry_price, real_sl, MAX_SL_USD)
        log.info(f"Fusion: mise a jour SL/TP du QA #{qa_ticket} SL={real_sl} TP={tp_final}")
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
        _alert_mgmt(msg.alert_fusion(full_signal['action'], ch_num_fusion, qa_ticket, real_sl, tp_final))

    # Nettoyer quick_alerts
    if key in quick_alerts and qa in quick_alerts[key]:
        quick_alerts[key].remove(qa)
        if not quick_alerts[key]:
            del quick_alerts[key]
    _log_mgmt(msg.log_merge_done(full_signal['action'], full_signal['symbol']))


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
    manager = None

    try:
        if not bridge.connect():
            log.critical("Bot arrêté — corrigez MT5 puis relancez.")
            return

        _quick_alerts = {}
        manager = TradeManager(bridge, quick_alerts_ref=_quick_alerts)
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
        entity_to_name = {}

        if TG_FOLDER:
            # ★ CHARGEMENT DEPUIS DOSSIERS TELEGRAM (multiples séparés par virgules)
            folder_names = [f.strip() for f in TG_FOLDER.split(',') if f.strip()]
            log.info(f"Recherche de {len(folder_names)} dossier(s): {', '.join(folder_names)}...")
            try:
                from telethon import functions
                result = None
                for fn_name in ['GetDialogFilters', 'GetDialogFiltersRequest', 'getDialogFilters']:
                    try:
                        fn = getattr(functions.messages, fn_name)
                        result = await client(fn())
                        break
                    except AttributeError:
                        continue
                if result is None:
                    log.error("Impossible de trouver GetDialogFilters dans Telethon.")
                    return

                filters = []
                if hasattr(result, 'filters'):
                    filters = result.filters
                elif hasattr(result, 'to_dict'):
                    d = result.to_dict()
                    for key in ['filters', 'dialogs', 'items']:
                        if key in d:
                            filters = d[key]
                            break
                if not filters:
                    log.error(f"Aucun filtre trouvé. Type: {type(result).__name__}")
                    return

                # Map des dossiers disponibles
                folder_map = {}
                for f in filters:
                    if isinstance(f, dict):
                        fid = f.get('id')
                        ftitle = f.get('title', '')
                        if isinstance(ftitle, dict):
                            ftitle = ftitle.get('text', str(ftitle))
                    else:
                        fid = getattr(f, 'id', None)
                        ftitle_obj = getattr(f, 'title', '')
                        ftitle = ftitle_obj.text if hasattr(ftitle_obj, 'text') else str(ftitle_obj)
                    folder_map[ftitle.lower()] = (fid, ftitle, f)
                    folder_map[str(fid)] = (fid, ftitle, f)

                # Chercher chaque dossier
                all_peers = []
                found_folders = []
                for name in folder_names:
                    match = folder_map.get(name.lower()) or folder_map.get(name)
                    if match:
                        fid, ftitle, filt = match
                        peers = filt.get('include_peers', []) if isinstance(filt, dict) else getattr(filt, 'include_peers', [])
                        all_peers.extend(peers)
                        found_folders.append(ftitle)
                        log.debug(f"  Dossier '{ftitle}' (id={fid})")
                    else:
                        log.warning(f"  Dossier '{name}' introuvable")

                if not found_folders:
                    log.error("Aucun dossier trouvé.")
                    log.info("Dossiers disponibles:")
                    for k, (fid, ftitle, _) in sorted(folder_map.items(), key=lambda x: x[1][0] or 0):
                        if k == ftitle.lower():
                            log.info(f"  - {ftitle} (id={fid})")
                    return

                # Dédupliquer les peers
                seen_ids = set()
                unique_peers = []
                for peer in all_peers:
                    pid = getattr(peer, 'user_id', None) or getattr(peer, 'channel_id', None) or getattr(peer, 'chat_id', None) or str(peer)
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        unique_peers.append(peer)

                ch_num = 0
                for peer in unique_peers:
                    try:
                        entity = await client.get_entity(peer)
                        if not hasattr(entity, 'title'):
                            continue
                        ch_num += 1
                        title = entity.title
                        title_clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', title)
                        title_clean = unicodedata.normalize('NFKC', title_clean)
                        if title_clean.strip() == "":
                            title_clean = str(entity.id)
                        chats.append(entity)
                        entity_to_name[entity.id] = title_clean
                        CHANNEL_NUM_MAP[title_clean] = ch_num
                        CHANNEL_NUM_MAP[str(entity.id)] = ch_num
                        log.debug(f"Canal_{ch_num} : {title_clean}")
                    except Exception as e:
                        log.debug(f"Impossible de résoudre un peer: {e}")

                folders_str = " , ".join(found_folders)
                log.info(f"Dossier Trouvé : '{folders_str}'")
                log.info(f"Channels Telechargés : {ch_num}")
                if TG_ALERT_CHANNEL:
                    log.info(f"Canal de Rapport : {TG_ALERT_CHANNEL}")

                # Sauvegarder la liste des canaux dans Channel.txt
                try:
                    with open("Channel.txt", "w", encoding="utf-8") as f:
                        f.write(f"# Canaux chargés depuis les dossiers Telegram: {', '.join(found_folders)}\n")
                        f.write(f"# {ch_num} canaux — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
                        f.write("\n")
                        for i, (ent, name) in enumerate([(e, entity_to_name.get(e.id, '?')) for e in chats], 1):
                            f.write(f"Canal_{i} : {name}\n")
                    log.info(f"Liste sauvegardée dans Channel.txt")
                except Exception as e:
                    log.warning(f"Impossible de sauvegarder Channel.txt: {e}")
            except Exception as e:
                log.error(f"Erreur lecture dossier Telegram '{TG_FOLDER}': {e}")
                return
        else:
            # ★ CHARGEMENT DEPUIS .ENV (TG_CHANNEL_*)
            active_channels = [(e, v) for e, v in _CHANNELS_LIST if v]
            log.info(f"Canaux depuis .env : {len(active_channels)}")

            for env_name, ch_value in _CHANNELS_LIST:
                if not ch_value:
                    continue
                try:
                    ch_resolved = int(ch_value) if ch_value.lstrip("-").isdigit() else ch_value
                    entity = await client.get_entity(ch_resolved)
                    title = getattr(entity, "title", ch_value)
                    title_clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', title)
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

                if not manager._check_daily_pnl_limit() or manager._daily_limit_reached:
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
                        _alert_mgmt(msg.alert_timesfm_rejected(
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
                    execute_quick_alert(sig_dict, bridge, manager, _quick_alerts)
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
                    merge_quick_alert(found_qa, key, sig_dict, bridge, manager, _quick_alerts)
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
                                _alert_mgmt(msg.alert_fusion_oot(action, ch_num, ticket, real_sl, tp_final_fusion))
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
                        execute_signal(sig_dict, bridge, manager)

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
        log.info("[SHUTDOWN] Bot arrêté proprement.")

if __name__ == "__main__":
    asyncio.run(main())
