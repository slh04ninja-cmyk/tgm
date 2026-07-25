"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 11.0.0 — SINGLE_POSITION_MODE
 MODIFICATIONS v11.0.0 :
 - SINGLE_POSITION_MODE : un seul MARKET par signal (pas de LIMIT, pas de pending)
 - Signaux zone convertis en Prix Unique (midian) → ZN1/ZN2
 - Prix Unique : PU1 (entry→SL) ou PU2 (tolérance) → MARKET
 - Quick Alert : AL-MP uniquement, tolérance prix QA_PRICE_TOLERANCE
 - Fusion : SL/TP mis à jour sur QA existant (pas de 2ème position)
 - BE : SL @ entry + TP @ entry ± TP_FIXED_GAIN_USD (MT5 ferme auto)
 - Plus de fermeture manuelle par le bot
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
from typing import List, Optional, Tuple  # ← IMPORT ESSENTIEL
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

# Mapping canal → numéro
CHANNEL_NUM_MAP = {}
for _i, _name in enumerate([CHANNEL_NAME, CHANNEL_NAME_2, CHANNEL_NAME_3,
                             CHANNEL_NAME_4, CHANNEL_NAME_5, CHANNEL_NAME_6,
                             CHANNEL_NAME_7, CHANNEL_NAME_8, CHANNEL_NAME_9], 1):
    if _name:
        CHANNEL_NUM_MAP[_name] = _i
        if _name.lstrip("-").isdigit():
            CHANNEL_NUM_MAP[_name.lstrip("-")] = _i
            if _name not in CHANNEL_NUM_MAP:
                CHANNEL_NUM_MAP[_name] = _i

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
HEARTBEAT_INTERVAL_MIN = int(os.getenv("HEARTBEAT_INTERVAL_MIN", "10"))  # minutes

# === PARAMÈTRES SL (définis dans .env) ===
SL_PRIX_UNIQUE = float(os.getenv("SL_PRIX_UNIQUE", "15.0"))
# ★ SL_TOTAL : pour les signaux à 2 positions (CAS1, CAS2, QA+Fusion), le SL des deux
# jambes est calculé pour que la perte réalisée TOTALE (si le SL est touché sur les
# deux) égale exactement SL_TOTAL $ (en supposant 0.01 lot par jambe sur XAUUSDm,
# où 1$ de mouvement = 1$ de P&L pour 0.01 lot).
SL_TOTAL = float(os.getenv("SL_TOTAL", "25"))
FUSION_TOLERANCE = float(os.getenv("FUSION_TOLERANCE", "3"))
CONFLIT_FILTER_ENABLED = os.getenv("CONFLIT_FILTER_ENABLED", "true").lower() == "true"
# ★ MODE POSITION UNIQUE : convertit les signaux zone (2 positions) en MARKET seul,
# et désactive le merge QA+Fusion. Seul le Quick Alert est exécuté.
SINGLE_POSITION_MODE = os.getenv("SINGLE_POSITION_MODE", "true").lower() == "true"

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

def send_alert_sync(message: str, _retries: int = 2):
    """Envoie non-bloquant avec retry automatique.
    - 1er essai : timeout 8s
    - Si échec : 1 retry après 2s (connexion Telegram instable)
    - Ne bloque JAMAIS la boucle de trading plus de 10s total"""
    if not TG_ALERT_CHANNEL or not _alert_client or not _main_loop:
        return
    for attempt in range(_retries):
        try:
            coro = _alert_client.send_message(TG_ALERT_CHANNEL, message)
            future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
            future.result(timeout=8)
            return  # ✅ succès
        except TimeoutError:
            if attempt < _retries - 1:
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
from signal_parser import SignalParser, is_spam, TradeSignal
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
            # ★ FIX #8 : filtre d'impact configurable (high ou medium)
            _impact_levels = [NEWS_MIN_IMPACT]
            if NEWS_MIN_IMPACT == "high":
                _impact_levels.append("medium")  # inclure medium quand on filtre sur high
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
            for entry in list(self.manager.active):
                for o in entry.get("orders", []):
                    self.bridge.cancel_order(o["order"])
                entry["orders"] = []
            self.bridge.close_all()

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

    def place_limit_order(self, signal: dict, lot: float, price: float, tp: float, expiry: datetime, comment: str = "TG-limit") -> int | None:
        sym = self._sym(signal["symbol"])
        if not sym:
            return None
        lot = self._validate_volume(sym, lot)
        action = signal["action"]
        if tp:
            if action == "BUY" and tp <= price:
                return None
            if action == "SELL" and tp >= price:
                return None
        otype = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        filling = self._get_filling(sym)
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym.name,
            "volume": lot,
            "type": otype,
            "price": round(price, sym.digits),
            "sl": round(signal.get("sl", 0), sym.digits) if signal.get("sl", 0) else 0,
            "tp": round(tp, sym.digits) if tp else 0,
            "deviation": SLIPPAGE,
            "magic": MAGIC_NUMBER,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": int(expiry.timestamp()),
            "type_filling": filling,
        })
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.debug(f"LIMIT {action} {sym.name} lot={lot} @{price} TP={tp} order#{result.order}")
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

    def modify_sl(self, ticket: int, new_sl: float, label: str = "") -> bool:
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
            "tp": pos.tp,
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.debug(f"SL modifié #{ticket} → {new_sl} {label}")
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

    def modify_pending_order(self, order_ticket: int, new_sl: float, new_tp: float, label: str = "") -> bool:
        orders = mt5.orders_get(ticket=order_ticket)
        if not orders:
            log.warning(f"Ordre pending #{order_ticket} introuvable")
            return False
        order = orders[0]
        sym = mt5.symbol_info(order.symbol)
        if sym is None:
            log.warning(f"Symbole introuvable pour l'ordre #{order_ticket}")
            return False
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": order_ticket,
            "price": order.price_open,
            "sl": round(new_sl, sym.digits),
            "tp": round(new_tp, sym.digits),
            "type_time": order.type_time,
            "expiration": order.time_expiration,
        })
        ok = result and result.retcode == mt5.TRADE_RETCODE_DONE
        if ok:
            log.debug(f"Ordre pending modifié #{order_ticket} → SL={new_sl} TP={new_tp} {label}")
        else:
            log.error(f"Échec modification ordre pending #{order_ticket}")
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

    def update_sl_all(self, new_sl: float):
        updated = 0
        positions = mt5.positions_get()
        if positions:
            for pos in positions:
                if pos.magic != MAGIC_NUMBER:
                    continue
                sym = mt5.symbol_info(pos.symbol)
                if not sym:
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
        log.info(f"<<<<< INFO >>>>> SL MOVE global → {new_sl} sur {updated} positions")

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
# SL_TOTAL : SL calculé pour une perte totale fixe sur 2 positions
# =============================================================
def compute_sl_total(p1: float, p2: float, action: str) -> float:
    """Calcule le SL commun pour 2 positions (0.01 lot chacune sur XAUUSDm) tel que
    la perte réalisée totale, si le SL est touché sur les deux, égale SL_TOTAL $.
    BUY  : SL = (P1 + P2 - SL_TOTAL) / 2
    SELL : SL = (P1 + P2 + SL_TOTAL) / 2"""
    if action == "BUY":
        return (p1 + p2 - SL_TOTAL) / 2
    else:
        return (p1 + p2 + SL_TOTAL) / 2


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
        # ★ SINGLE_POSITION_MODE : tous les signaux sont MARKET, pas de pending
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
        lookup_start = start - timedelta(days=7)
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
    # SL MOVE — Mettre à jour le SL des pending orders
    # =============================================================
    def update_pending_orders_sl(self, channel_num: int, new_sl: float):
        updated = 0
        with self._lock:
            for entry in self.active:
                signal = entry.get("signal", {})
                canal = signal.get("source_channel", "Inconnu")
                ch = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), None))
                if ch != channel_num:
                    continue
                # Mettre à jour le SL dans le signal dict
                signal["sl"] = new_sl
                # Modifier les ordres pending dans MT5
                for o in entry.get("orders", []):
                    order_ticket = o.get("order", 0)
                    tp = o.get("tp_final", 0)
                    if order_ticket and tp:
                        if self.bridge.modify_pending_order(order_ticket, new_sl, tp, f"[SL-MOVE @{new_sl}]"):
                            updated += 1
        if updated:
            log.info(f"<<<<< INFO >>>>> SL MOVE pending orders canal {channel_num} → {new_sl} sur {updated} ordres")

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
                entry["orders"] = []
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
    def _cancel_pending_orders_for_entry(self, entry: dict):
        # ★★★ FIX : vérifier si les ordres sont déjà remplis avant d'annuler ★★★
        # Le délai MT5 history peut faire qu'un ordre rempli n'est pas encore détecté.
        # On vérifie via mt5.orders_get() si l'ordre existe encore.
        orders_to_cancel = []
        already_filled = []
        for o in entry.get("orders", []):
            order_ticket = o.get("order", 0)
            if not order_ticket:
                continue
            # Vérifier si l'ordre existe encore dans MT5
            mt5_order = mt5.orders_get(ticket=order_ticket)
            if mt5_order:
                # L'ordre est toujours pending → on peut l'annuler
                orders_to_cancel.append(order_ticket)
            else:
                # L'ordre n'existe plus → il a été rempli !
                # Chercher la position correspondante
                symbol = entry.get("signal", {}).get("symbol", "")
                pos = self._resolve_order(order_ticket, symbol)
                if pos:
                    tk = {
                        "ticket": pos.ticket, "lot": o["lot"], "role": o["role"],
                        "entry_price": pos.price_open,
                        "tp_index": o.get("tp_index", 0), "tp_target": o.get("tp_target", 0),
                        "tp3": o.get("tp3", 0), "tp_final": o.get("tp_final", 0),
                        "sl_step": 0, "trail_active": False, "be_active": False, "be_sl": 0,
                    }
                    entry["tickets"].append(tk)
                    already_filled.append(order_ticket)
                    log.info(f"[BE] Ordre #{order_ticket} déjà rempli → ticket #{pos.ticket} ajouté")

        if already_filled:
            log.info(f"[BE] {len(already_filled)} ordre(s) déjà rempli(s) → ajoutés aux tickets")

        if not orders_to_cancel:
            # ★ FIX : même si aucun ordre n'a eu besoin d'être annulé (tous déjà remplis),
            # il faut quand même vider entry["orders"], sinon _check_pending_only_expiry
            # re-déclenche le TP_TRIGGER à l'infini à chaque cycle de poll.
            entry["orders"] = []
            return

        log.debug(f"Annulation de {len(orders_to_cancel)} ordre(s) pending")
        symbol = entry.get("signal", {}).get("symbol", "")
        for ticket in orders_to_cancel:
            ok = self.bridge.cancel_order(ticket)
            if not ok:
                # ★★★ FIX : cancel échoué → l'ordre s'est rempli entre le check et le cancel ★★★
                pos = self._resolve_order(ticket, symbol)
                if pos:
                    # Trouver l'order dict correspondant pour récupérer les métadonnées
                    o_data = next((o for o in entry["orders"] if o.get("order") == ticket), None)
                    if o_data:
                        tk = {
                            "ticket": pos.ticket, "lot": o_data["lot"], "role": o_data["role"],
                            "entry_price": pos.price_open,
                            "tp_index": o_data.get("tp_index", 0), "tp_target": o_data.get("tp_target", 0),
                            "tp3": o_data.get("tp3", 0), "tp_final": o_data.get("tp_final", 0),
                            "sl_step": 0, "trail_active": False, "be_active": False, "be_sl": 0,
                        }
                        entry["tickets"].append(tk)
                        log.info(f"[BE] Race condition détectée : #{ticket} rempli pendant annulation → #{pos.ticket} ajouté")
            else:
                log.debug(f"Annulation ordre pending #{ticket}")
        entry["orders"] = []

    def _get_gain_per_position(self, entry: dict) -> float:
        signal = entry.get("signal", {})
        action = signal.get("action", "")
        zone_low = signal.get("zone_low", 0)
        zone_high = signal.get("zone_high", 0)
        entry_price = (zone_low + zone_high) / 2
        tps = signal.get("tps", [])
        if not tps:
            return TP_FIXED_GAIN_USD
        tp_final = tps[-1]
        if action == "BUY":
            potential_gain = tp_final - entry_price
        else:
            potential_gain = entry_price - tp_final
        return min(TP_FIXED_GAIN_USD, potential_gain)

    def _get_tp_trigger(self, entry: dict) -> float:
        signal = entry.get("signal", {})
        tps = signal.get("tps", [])
        if len(tps) >= 3:
            return tps[2]
        elif len(tps) >= 2:
            return tps[1]
        elif len(tps) >= 1:
            return tps[0]
        return 0.0

    def _close_entry_tp_fixed(self, entry: dict, action: str, symbol: str, mt5_comment: str,
                               canal: str, active_tickets: list, total_pnl: float):
        """Ferme toutes les positions actives d'une entrée via TP-FIXED, met à jour le
        P&L quotidien avec le P&L RÉEL (pas l'estimation flottante), et retire l'entrée
        de self.active. Factorisé pour être appelable depuis le chemin normal ET depuis
        le raccourci de détection précoce (voir _check_pnl_trigger / phase BE)."""
        log.info(msg.log_tp_fixed_header(mt5_comment))
        ticket_list = ", ".join([f"#{t['ticket']}" for t in active_tickets])
        log.info(msg.log_tp_fixed_estimate(action, symbol, total_pnl, len(active_tickets)))
        log.info(msg.log_tp_fixed_tickets(ticket_list))
        actual_total_pnl = 0.0
        for t in active_tickets:
            if not t.get("_tp_fixed_closed"):
                closed_ok = self.bridge.close_position(t["ticket"], "TP-FIXED")
                t["_tp_fixed_closed"] = True
                if closed_ok:
                    real_pnl = self._get_last_pnl(t["ticket"], symbol)
                    t["_last_pnl"] = real_pnl
                    t["_reported"] = True
                    actual_total_pnl += real_pnl
                    self._update_daily_pnl(real_pnl)
        if actual_total_pnl != total_pnl:
            log.info(msg.log_tp_fixed_real_vs_estime(action, symbol, actual_total_pnl, total_pnl))
        log.info(msg.log_daily_pnl_final(self._daily_pnl))
        send_alert_sync(msg.alert_tp_fixed(action, symbol, actual_total_pnl, len(active_tickets), ticket_list, self._daily_pnl, canal))
        with self._lock:
            if entry in self.active:
                self.active.remove(entry)

    def _check_pnl_trigger(self, entry: dict) -> bool:
        # ★★★ FIX : utiliser min_profit (pire position) au lieu de best_profit ★★★
        # Le BE se déclenche quand la PIRE position atteint le seuil.
        # Pour BUY: market @ 2350 (pire entrée) doit atteindre 8$
        # Pour BUY: limit @ 2340 (meilleure entrée) aura forcément plus de profit.
        # → La market est protégée en premier.
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

        # ★ SINGLE_POSITION_MODE : toujours 1 position MARKET, pas de pending
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
        be_price = round(entry_price, sym.digits if sym else 2)
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
            send_alert_sync(msg.alert_be_activated(action, signal['symbol'], 1, be_price, target_gain, canal, 0))

    # ★★★ FIX : Appliquer BE aux nouveaux tickets (limit remplies après BE initial) ★★★

    # =============================================================
    # TP_TRIGGER PENDING UNIQUEMENT
    # =============================================================
    def _check_pending_only_expiry(self, entry: dict, symbol: str, action: str):
        has_open_position = False
        for t in entry.get("tickets", []):
            if self._get_pos(t["ticket"]):
                has_open_position = True
                break
        # ✅ MODIFIÉ : ne pas skip si position ouverte — le TP_TRIGGER doit quand même
        # annuler les ordres pending restants (ex: CAS2-b, limit_1 rempli, limit_2 pending)
        if not entry.get("orders"):
            return
        tp_trigger = self._get_tp_trigger(entry)
        if tp_trigger == 0:
            return
        sym_info = self.bridge._sym(symbol)
        if sym_info is None:
            return
        tick = mt5.symbol_info_tick(sym_info.name)
        if tick is None:
            return
        current = tick.bid if action == "BUY" else tick.ask
        triggered = False
        if action == "BUY" and current >= tp_trigger:
            triggered = True
        elif action == "SELL" and current <= tp_trigger:
            triggered = True
        if triggered:
            # ✅ Capturer les infos AVANT annulation
            pending_count = len(entry.get("orders", []))
            prices = [f"@{o['price']}" for o in entry.get("orders", []) if "price" in o]
            prices_str = ", ".join(prices) if prices else "inconnu"

            if has_open_position:
                log.debug(f"TP_TRIGGER ({tp_trigger:.2f}) atteint avec position ouverte → annulation de {pending_count} ordre(s) pending")
            else:
                log.debug(f"TP_TRIGGER ({tp_trigger:.2f}) atteint sans position ouverte → annulation des ordres pending")

            self._cancel_pending_orders_for_entry(entry)

            signal = entry.get("signal", {})
            canal = signal.get("source_channel", "Inconnu")
            ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
            mt5_comment = entry.get("_mt5_comment", f"CH{ch_num}-UNK")

            log.info(msg.log_tp_trigger(mt5_comment, prices_str, pending_count))

            send_alert_sync(
                f"⚠️ {action} {symbol} | TP_TRIGGER\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Ordres annulés : {pending_count}\n"
                f"Prix : {prices_str}\n"
                f"Position ouverte : {'Oui' if has_open_position else 'Non'}\n"
                f"Canal: {canal}"
            )

            # ★★★ FIX : Nettoyer l'entry si aucune position ouverte restante ★★★
            if not has_open_position:
                remaining_tickets = [t for t in entry.get("tickets", []) if self._get_pos(t["ticket"])]
                if not remaining_tickets and not entry.get("orders"):
                    with self._lock:
                        if entry in self.active:
                            self.active.remove(entry)
                    log.debug(f"[TP_TRIGGER] Entry supprimée de self.active (aucune position/order restant)")

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
        since = datetime.now(timezone.utc) - timedelta(days=7)
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
        if deals:
            for deal in reversed(deals):
                if deal.symbol == symbol and deal.position_id == ticket:
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
        log.debug(f"TradeManager [{mode}]: {sig['action']} {sig['symbol']} Canal: {canal} | {len(entry['orders'])} ordres")

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

            still_pending = []
            expired_orders = []
            for o in entry.get("orders", []):
                pos = self._resolve_order(o["order"], symbol)
                if pos:
                    # ★★★ FIX BE : un Quick Alert LIMIT rempli doit devenir "quick_limit_filled" ★★★
                    # (comportement documenté §5.2 : "Alert 1 pos (limit)" → quick_limit_filled)
                    # Sans ce renommage, le rôle reste "quick_limit" (exclu de la whitelist BE)
                    # et le BE ne se déclenche jamais pour une Quick Alert LIMIT non fusionnée.
                    resolved_role = o["role"]
                    if resolved_role == "quick_limit":
                        resolved_role = "quick_limit_filled"
                    tk = {
                        "ticket": pos.ticket,
                        "lot": o["lot"],
                        "role": resolved_role,
                        "entry_price": pos.price_open,
                        "tp_index": o.get("tp_index", 0),
                        "tp_target": o.get("tp_target", 0),
                        "tp3": o.get("tp3", 0),
                        "tp_final": o.get("tp_final", 0),
                        "sl_step": 0,
                        "trail_active": False,
                        "be_active": False,
                        "be_sl": 0,
                    }
                    entry["tickets"].append(tk)
                    log.debug(f"Ordre #{o['order']} rempli → ticket={pos.ticket} @{pos.price_open}")

                    # Log LIMIT remplie
                    log.info(msg.log_order_filled(mt5_comment, "LMT", pos.ticket))

                    sl_price = signal.get("sl", 0)
                    send_alert_sync(
                        f"🔵 {action} {symbol} | LIMIT REMPLIE\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"{resolved_role}: @{pos.price_open} | Lot: {o['lot']}\n"
                        f"TICKET: #{pos.ticket}\n"
                        f"TP: {o.get('tp_final', 0)} | SL: {sl_price}\n"
                        f"Canal: {canal}"
                    )

                elif now > entry.get("expiry", now):
                    self.bridge.cancel_order(o["order"])
                    expired_orders.append(o)
                else:
                    # --- Vérifier SL/TP provisoire sur les QA-LMT ---
                    if entry.get("_is_quick_alert") and o.get("role") == "quick_limit":
                        current_price = self.bridge.current_price(symbol, action)
                        if current_price is not None:
                            provisional_sl = signal.get("sl", 0)
                            provisional_tp = signal.get("tps", [0])[0] if signal.get("tps") else 0
                            sl_hit = False
                            tp_hit = False
                            if action == "BUY":
                                if provisional_sl and current_price <= provisional_sl:
                                    sl_hit = True
                                elif provisional_tp and current_price >= provisional_tp:
                                    tp_hit = True
                            else:  # SELL
                                if provisional_sl and current_price >= provisional_sl:
                                    sl_hit = True
                                elif provisional_tp and current_price <= provisional_tp:
                                    tp_hit = True

                            if sl_hit or tp_hit:
                                self.bridge.cancel_order(o["order"])
                                reason = "SL" if sl_hit else "TP"
                                emoji = "❌" if sl_hit else "✅"
                                log.info(f"[QA-LMT] #{o['order']} annulé — {reason} provisoire touché @{current_price}")
                                send_alert_sync(
                                    f"{emoji} QA-LMT {reason} TOUCHÉ | {action}\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"QA-LMT : #{o['order']} annulé\n"
                                    f"{reason} provisoire : @{provisional_sl if sl_hit else provisional_tp}\n"
                                    f"Prix actuel : @{current_price}\n"
                                    f"Canal: {canal}"
                                )
                                # Retirer le QA de _quick_alerts
                                qa_key = _qa_key(symbol, action, canal)
                                if qa_key in self._quick_alerts_ref:
                                    self._quick_alerts_ref[qa_key] = [
                                        qa for qa in self._quick_alerts_ref[qa_key]
                                        if qa.get("ticket") != o["order"]
                                    ]
                                    if not self._quick_alerts_ref[qa_key]:
                                        del self._quick_alerts_ref[qa_key]
                                continue

                    still_pending.append(o)

            if expired_orders:
                prices = [f"@{o['price']}" for o in expired_orders if "price" in o]
                prices_str = ", ".join(prices) if prices else "inconnu"
                log.info(msg.log_expiration(mt5_comment, prices_str, len(expired_orders)))
                send_alert_sync(
                    f"🕒 {action} {symbol} | EXPIRATION\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Ordres annulés : {len(expired_orders)}\n"
                    f"Prix : {prices_str}\n"
                    f"Canal: {canal}"
                )

            entry["orders"] = still_pending

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

                    send_alert_sync(msg.alert_close(label, action, symbol, pnl, idx, total, t['ticket'], daily_pnl_now, canal))

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

            if not entry.get("orders") and not active_tickets and all_reported:
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

            if not entry.get("_be_activated") and not active_tickets and all_reported:
                self._check_pending_only_expiry(entry, symbol, action)
                if not entry.get("orders"):
                    with self._lock:
                        if entry in self.active:
                            self.active.remove(entry)
                    continue

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 3 : GESTION BE ★★★
            # ══════════════════════════════════════════════════════════════
            # 1 position → pending annulés, SL @ entry
            # 2 positions → SL au médian (total = 0$)
            # Quand BE triggers → pending annulés → jamais de limit après BE

            # ★ SINGLE_POSITION_MODE : BE → SL @ entry + TP @ entry ± TP_FIXED_GAIN_USD
            # MT5 ferme automatiquement quand le prix atteint le TP.
            # Plus de fermeture manuelle par le bot.
            if TP_FIXED_ENABLED and not entry.get("_be_activated"):
                if self._check_pnl_trigger(entry):
                    self._apply_be_on_open_positions(entry, action)
                    continue

    # ★★★ FIX : Pas de cache pour les ordres pending ★★★
    # Le cache de 1s + délai MT5 = la limit peut être invisible quand le BE se déclenche.
    # On query MT5 directement à chaque cycle.
    def _resolve_order(self, order_ticket: int, symbol: str):
        since = datetime.now(timezone.utc) - timedelta(days=1)
        now = datetime.now(timezone.utc)
        deals = mt5.history_deals_get(since, now)
        if deals is None or len(deals) == 0:
            return None

        for deal in reversed(deals):
            if deal.order == order_ticket and deal.entry == mt5.DEAL_ENTRY_IN:
                positions = mt5.positions_get(ticket=deal.position_id)
                if positions:
                    return positions[0]

        return None

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
    for entry in manager.active:
        if entry["signal"]["symbol"] != symbol:
            continue
        if entry["signal"].get("source_channel") != canal:
            continue
        for o in entry.get("orders", []):
            bridge.cancel_order(o["order"])
        to_remove.append(entry)
    for e in to_remove:
        if e in manager.active:
            manager.active.remove(e)
    bridge.close_all(symbol=symbol, channel_num=ch_num)
    return True

def execute_signal(signal: dict, bridge: MT5Bridge, manager, tracker):
    action = signal["action"]
    symbol = signal["symbol"]
    zone_low = signal["zone_low"]
    zone_mid = signal["zone_mid"]
    zone_high = signal["zone_high"]

    canal = signal.get("source_channel", "Inconnu")
    mode = "DEMO" if DEMO_MODE else "LIVE"
    ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))

    all_tps = signal["tps"]
    if not all_tps:
        log.info(msg.log_refuse(ch_num, "", msg.MOTIF_AUCUN_TP))
        log.warning(f"Signal ignoré — aucun TP trouvé ({symbol} {action})")
        return

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
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ORDER_EXPIRY_MIN)

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

    orders, tickets = [], []
    is_single_price = signal.get("is_single_price", False)

    # ★ CONVERTIR LES SIGNAUX ZONE EN PRIX UNIQUE (midian de la zone)
    # Un signal zone (BUY 4000 4010) devient un Prix Unique avec entry=4005
    is_zone_signal = False
    if not is_single_price and zone_low != zone_high:
        midian = round((zone_low + zone_high) / 2, 2)
        log.debug(f"Signal zone converti en Prix Unique: {zone_low}-{zone_high} → entry={midian}")
        zone_low = midian
        zone_high = midian
        zone_mid = midian
        signal["zone_low"] = midian
        signal["zone_high"] = midian
        signal["zone_mid"] = midian
        is_single_price = True
        is_zone_signal = True

    # ── Prix unique ──
    if is_single_price and len(all_tps) >= 1:
        entry_price = zone_mid
        sl_price = sl
        unique_lot = LOT_UNIQUE_TRADE

        # ★ TOLÉRANCE selon type : ZN pour zone, PU pour prix unique
        if is_zone_signal:
            PRICE_TOLERANCE = float(os.getenv("ZN_PRICE_TOLERANCE", "3.0"))
            prefix = "ZN"
        else:
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
                f"🟢 {action} {symbol} | {mt5_comment_pu}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"MARKET: @{current} | Lot: {unique_lot}\n"
                f"TICKET: {t}\n"
                f"TP: {tp_final} | SL: {sl}\n"
                f"Canal: {canal}"
            )
        else:
            log.error("  ✗ MARKET échoué")

        if not orders and not tickets:
            log.info(msg.log_refuse(ch_num, f"-{prefix}", msg.MOTIF_ECHEC_PLACEMENT))
            log.error(f"Aucun ordre placé ({prefix}).")
            return

        entry = {
            "signal": signal,
            "orders": orders,
            "tickets": tickets,
            "expiry": expiry,
            "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "_signal_id": f"{symbol}_{action}_{int(time.time())}",
            "_expected_positions": 1,
            "_mt5_comment": mt5_comment_pu,
        }
        manager.register(entry)
        tracker.log_trade_open(entry)

        parts_data = []
        for t in tickets:
            parts_data.append(("MKT", t['ticket'], t['entry_price']))
        for o in orders:
            parts_data.append(("LIMIT", o['order'], o['price']))
        if len(parts_data) == 1:
            log.info(msg.log_order_placed(mt5_comment_pu, parts_data[0][0], parts_data[0][1], parts_data[0][2], sl))
        elif len(parts_data) >= 2:
            log.info(msg.log_order_placed_dual(mt5_comment_pu, parts_data[0][0], parts_data[0][1], parts_data[0][2],
                                                parts_data[1][0], parts_data[1][1], parts_data[1][2], sl))
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
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ORDER_EXPIRY_MIN)

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

    # ★ VÉRIFICATION TOLÉRANCE DE PRIX (QA Type 2 avec prix)
    # Si le signal n'est pas un "market price" (pas de prix), on exécute directement.
    # Si le signal a un prix, on vérifie que le prix actuel est dans [entry ± TOLERANCE].
    QA_PRICE_TOLERANCE = float(os.getenv("QA_PRICE_TOLERANCE", "3.0"))
    if not is_market_price and entry_price is not None:
        if abs(current - entry_price) > QA_PRICE_TOLERANCE:
            log.info(f"Quick Alert annulée — prix hors tolérance | "
                     f"prix={current} entry={entry_price} écart={abs(current-entry_price):.1f} > tolérance={QA_PRICE_TOLERANCE}")
            send_alert_sync(
                f"❌ QA ANNULÉE | {action} {symbol}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Prix: {current} | Entry signal: {entry_price}\n"
                f"Écart: {abs(current-entry_price):.1f} > {QA_PRICE_TOLERANCE}\n"
                f"Canal: {canal}"
            )
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

    orders = []
    tickets = []
    order_ticket = None

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
            f"⚡ {action} {symbol} | AL-MP\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"MARKET : @{current} | Lot: {LOT_UNIQUE_TRADE}\n"
            f"Ticket : #{t}\n"
            f"TP: {default_tp} | SL: {sl}\n"
            f"Canal: {canal}"
        )
    else:
        log.error("✗ QUICK MARKET échoué")
        return

    entry = {
        "signal": signal,
        "orders": orders,
        "tickets": tickets,
        "expiry": expiry,
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
        "is_limit": False,
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

    # ★ SINGLE_POSITION_MODE : mettre à jour SL/TP du QA existant (pas de 2ème position)
    if SINGLE_POSITION_MODE:
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
            emoji = "❌" if close_reason == "SL" else "✅"
            log.info(f"[SINGLE_POS] QA #{qa_ticket} déjà fermé ({close_reason}) → signal complet ignoré")
            send_alert_sync(
                f"{emoji} QA {close_reason} | {full_signal['action']} {full_signal['symbol']}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"QA : #{qa_ticket}\n"
                f"P&L : {deal_pnl:+.2f} $\n"
                f"Signal complet ignoré\n"
                f"Canal: {canal}"
            )
        else:
            # QA actif → mettre à jour SL et TP avec ceux du signal complet
            log.info(f"[SINGLE_POS] Fusion: mise à jour SL/TP du QA #{qa_ticket}")
            bridge.modify_sl_tp(qa_ticket, real_sl, tp_final, "[FUSION-SL-TP]")
            for t in entry["tickets"]:
                if t["ticket"] == qa_ticket:
                    t["tp_final"]  = tp_final
                    t["tp_target"] = tp_final
                    t["tp3"]       = tp_final
                    break
            entry["signal"]          = full_signal
            entry["_is_quick_alert"] = False
            send_alert_sync(
                f"✅ FUSION SL/TP mis à jour\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"QA : #{qa_ticket}\n"
                f"Nouveau SL: {real_sl} | Nouveau TP: {tp_final}\n"
                f"Canal: {canal}"
            )

        # Nettoyer quick_alerts
        if key in quick_alerts and qa in quick_alerts[key]:
            quick_alerts[key].remove(qa)
            if not quick_alerts[key]:
                del quick_alerts[key]
        return

    # ── Mode multi-positions (ancien comportement) ──
    qa_is_limit = qa["is_limit"]

    if qa_is_limit:
        pos   = manager._resolve_order(qa_ticket, full_signal["symbol"])
        order = mt5.orders_get(ticket=qa_ticket)
        if not pos and not order:
            since = datetime.now(timezone.utc) - timedelta(minutes=30)
            deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
            sl_hit = False
            if deals:
                for deal in reversed(deals):
                    if deal.position_id == qa_ticket and deal.entry == mt5.DEAL_ENTRY_OUT:
                        if deal.reason == mt5.DEAL_REASON_SL:
                            sl_hit = True
                        break
            if sl_hit:
                log.info(msg.log_merge(qa_ticket, "SL touché -> signal complet ignore"))
                deal_pnl = 0.0
                for deal in reversed(deals):
                    if deal.position_id == qa_ticket and deal.entry == mt5.DEAL_ENTRY_OUT:
                        deal_pnl = deal.profit + deal.commission + deal.swap
                        break
                send_alert_sync(
                    f"❌ QA SL TOUCHÉ | {full_signal['action']}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"QA-LMT : #{qa_ticket}\n"
                    f"P&L : {deal_pnl:+.2f} $\n"
                    f"Signal complet ignoré\n"
                    f"Canal: {canal}"
                )
            else:
                log.info(msg.log_merge(qa_ticket, "expire/annule -> executer signal complet"))
                execute_signal(full_signal, bridge, manager, tracker)
            if key in quick_alerts and qa in quick_alerts[key]:
                quick_alerts[key].remove(qa)
                if not quick_alerts[key]:
                    del quick_alerts[key]
            return
    else:
        pos = mt5.positions_get(ticket=qa_ticket)
        if not pos:
            since = datetime.now(timezone.utc) - timedelta(minutes=30)
            deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
            sl_hit = tp_hit = False
            if deals:
                for deal in reversed(deals):
                    if deal.symbol == full_signal["symbol"] and (
                        deal.position_id == qa_ticket or deal.order == qa_ticket
                    ):
                        if deal.entry == mt5.DEAL_ENTRY_OUT:
                            if deal.reason == mt5.DEAL_REASON_SL:
                                sl_hit = True
                            elif deal.reason == mt5.DEAL_REASON_TP:
                                tp_hit = True
                            break
            deal_pnl = 0.0
            for deal in reversed(deals):
                if deal.symbol == full_signal["symbol"] and (
                    deal.position_id == qa_ticket or deal.order == qa_ticket
                ):
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        deal_pnl = deal.profit + deal.commission + deal.swap
                        break
            if sl_hit:
                log.info(msg.log_merge(qa_ticket, "SL touché -> signal complet ignore"))
                send_alert_sync(
                    f"❌ QA SL TOUCHÉ | {full_signal['action']}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"QA : #{qa_ticket}\n"
                    f"P&L : {deal_pnl:+.2f} $\n"
                    f"Signal complet ignoré\n"
                    f"Canal: {canal}"
                )
            elif tp_hit:
                log.info(msg.log_merge(qa_ticket, "TP touche -> signal complet ignore"))
                send_alert_sync(
                    f"✅ QA TP TOUCHÉ | {full_signal['action']}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"QA : #{qa_ticket}\n"
                    f"P&L : {deal_pnl:+.2f} $\n"
                    f"Signal complet ignoré\n"
                    f"Canal: {canal}"
                )
            else:
                log.info(msg.log_merge(qa_ticket, "ferme (autre raison) -> executer signal complet"))
                execute_signal(full_signal, bridge, manager, tracker)
            if key in quick_alerts and qa in quick_alerts[key]:
                quick_alerts[key].remove(qa)
                if not quick_alerts[key]:
                    del quick_alerts[key]
            return

    if not qa_is_limit:
        log.info(msg.log_merge(qa_ticket, "position ouverte -> SL/TP + LIMIT"))
        merge_ticket, merge_sl = _place_merge_limit(full_signal, bridge, entry, real_sl, tp_final)
        bridge.modify_sl_tp(qa_ticket, merge_sl, tp_final, "[MERGE-SL-TP]")
        for t in entry["tickets"]:
            if t["ticket"] == qa_ticket:
                if len(full_signal["tps"]) == 1:
                    tp_trigger_idx = 0
                else:
                    tp_trigger_idx = 2 if 3 <= len(full_signal["tps"]) else len(full_signal["tps"]) - 1
                t["tp_final"]  = tp_final
                t["tp_target"] = full_signal["tps"][tp_trigger_idx] if len(full_signal["tps"]) > tp_trigger_idx else tp_final
                t["tp3"]       = t["tp_target"]
                t["tp_index"]  = tp_trigger_idx
                break
        entry["signal"]           = full_signal
        entry["_is_quick_alert"]  = False
        merge_p = full_signal.get("merge_price")
        send_alert_sync(
            f"✅ FUSION RÉUSSIE | PO-OV\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"QA : #{qa_ticket}\n"
            f"MERGE LMT: @{merge_p}\n"
            f"Ticket : #{merge_ticket}\n"
            f"SL: {merge_sl} | TPf: {tp_final}\n"
            f"Canal: {canal}"
        )
    else:
        resolved_pos = manager._resolve_order(qa_ticket, full_signal["symbol"])
        if resolved_pos:
            log.info(msg.log_merge(qa_ticket, "limit rempli -> SL/TP + LIMIT"))
            if len(full_signal["tps"]) == 1:
                tp_trigger_idx = 0
            else:
                tp_trigger_idx = 2 if 3 <= len(full_signal["tps"]) else len(full_signal["tps"]) - 1
            tk = {
                "ticket":       resolved_pos.ticket,
                "lot":          qa["entry"]["orders"][0]["lot"] if qa["entry"]["orders"] else LOT_UNIQUE_TRADE,
                "role":         "quick_limit_filled",
                "entry_price":  resolved_pos.price_open,
                "tp_index":     tp_trigger_idx,
                "tp_target":    full_signal["tps"][tp_trigger_idx] if len(full_signal["tps"]) > tp_trigger_idx else tp_final,
                "tp3":          full_signal["tps"][tp_trigger_idx] if len(full_signal["tps"]) > tp_trigger_idx else tp_final,
                "tp_final":     tp_final,
                "sl_step":      0,
                "trail_active": False,
                "be_active":    False,
                "be_sl":        0,
            }
            entry["tickets"].append(tk)
            entry["orders"] = [o for o in entry["orders"] if o["order"] != qa_ticket]
            merge_ticket, merge_sl = _place_merge_limit(full_signal, bridge, entry, real_sl, tp_final)
            bridge.modify_sl_tp(resolved_pos.ticket, merge_sl, tp_final, "[MERGE-SL-TP]")
            entry["signal"]          = full_signal
            entry["_is_quick_alert"] = False
            merge_p = full_signal.get("merge_price")
            send_alert_sync(
                f"✅ FUSION RÉUSSIE | LMT-RP\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"QA : #{qa_ticket}\n"
                f"MERGE REMPLI : @{merge_p}\n"
                f"Ticket : #{merge_ticket}\n"
                f"SL: {merge_sl} | TPf: {tp_final}\n"
                f"Canal: {canal}"
            )
        else:
            log.info(msg.log_merge(qa_ticket, "limit pending -> modif SL/TP + LIMIT"))
            if len(full_signal["tps"]) == 1:
                tp_trigger_idx = 0
            else:
                tp_trigger_idx = 2 if 3 <= len(full_signal["tps"]) else len(full_signal["tps"]) - 1
            for o in entry["orders"]:
                if o["order"] == qa_ticket:
                    o["tp_final"]  = tp_final
                    o["tp_target"] = full_signal["tps"][tp_trigger_idx] if len(full_signal["tps"]) > tp_trigger_idx else tp_final
                    o["tp3"]       = o["tp_target"]
                    o["tp_index"]  = tp_trigger_idx
                    break
            merge_ticket, merge_sl = _place_merge_limit(full_signal, bridge, entry, real_sl, tp_final)
            bridge.modify_pending_order(qa_ticket, merge_sl, tp_final, "[MERGE-ORD-SL-TP]")
            entry["signal"]          = full_signal
            entry["_is_quick_alert"] = False
            merge_p = full_signal.get("merge_price")
            send_alert_sync(
                f"✅ FUSION RÉUSSIE | LMT-PDN\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"QA-LMT : #{qa_ticket}\n"
                f"MERGE LMT: @{merge_p}\n"
                f"Ticket : #{merge_ticket}\n"
                f"SL: {merge_sl} | TPf: {tp_final}\n"
                f"Canal: {canal}"
            )

    if abs(full_signal["zone_high"] - full_signal["zone_low"]) >= 1:
        market_entry_price = None
        for t in entry["tickets"]:
            if t.get("role") == "quick_market":
                market_entry_price = t.get("entry_price")
                break
        if not market_entry_price:
            market_entry_price = entry.get("signal", {}).get("entry", 0)
        if market_entry_price:
            entry["_grade_market_price"] = market_entry_price
            if full_signal["action"] == "BUY":
                entry["_grade_limit_price"] = full_signal["zone_low"]
            else:
                entry["_grade_limit_price"] = full_signal["zone_high"]

    if key in quick_alerts and qa in quick_alerts[key]:
        quick_alerts[key].remove(qa)
        if not quick_alerts[key]:
            del quick_alerts[key]
    log.info(msg.log_merge_done(full_signal['action'], full_signal['symbol']))


def _place_merge_limit(
    full_signal: dict, bridge: MT5Bridge, entry: dict, real_sl: float, tp_final: float
):
    zone_low  = full_signal["zone_low"]
    zone_high = full_signal["zone_high"]
    action    = full_signal["action"]
    merge_price = full_signal.get("merge_price")
    # Si merge_price existe, l'utiliser comme prix LIMIT (edge)
    if merge_price is not None:
        limit_price = merge_price
    elif abs(zone_high - zone_low) >= 1:
        limit_price = zone_high if action == "SELL" else zone_low
    else:
        return None, real_sl
    sym_info = bridge._sym(full_signal["symbol"])
    if not sym_info:
        return None, real_sl

    # ★ SL_TOTAL : perte totale fixe (QA + LIMIT de fusion) si le prix d'entrée
    # de la QA est trouvable ; sinon on garde le SL brut du signal complet (cas improbable).
    qa_entry_price = None
    if entry.get("tickets"):
        qa_entry_price = entry["tickets"][0].get("entry_price")
    elif entry.get("orders"):
        qa_entry_price = entry["orders"][0].get("price")

    if qa_entry_price is not None:
        merge_sl = round(compute_sl_total(qa_entry_price, limit_price, action), sym_info.digits)
    else:
        merge_sl = round(real_sl, sym_info.digits)
    # Mettre à jour le signal pour que place_limit_order utilise le bon SL
    full_signal["sl"] = merge_sl

    expiry      = datetime.now(timezone.utc) + timedelta(minutes=ORDER_EXPIRY_MIN)
    canal       = full_signal.get("source_channel", "Inconnu")
    ch_num      = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
    # (log emis apres placement, voir plus bas)
    o = bridge.place_limit_order(
        full_signal, LOT_UNIQUE_TRADE, limit_price, tp_final, expiry, comment=f"CH{ch_num}-MG"
    )
    if o:
        if len(full_signal["tps"]) == 1:
            tp_trigger_idx = 0
        else:
            tp_trigger_idx = 2 if 3 <= len(full_signal["tps"]) else len(full_signal["tps"]) - 1
        entry["orders"].append({
            "order":      o,
            "lot":        LOT_UNIQUE_TRADE,
            "price":      limit_price,
            "role":       "merge_limit",
            "tp_index":   len(full_signal["tps"]) - 1,
            "tp_target":  tp_final,
            "tp3":        full_signal["tps"][tp_trigger_idx] if len(full_signal["tps"]) > tp_trigger_idx else tp_final,
            "tp_final":   tp_final,
            "sl_step":    0,
            "trail_active": False,
        })
        log.info(msg.log_merge_limit_placed(ch_num, action, full_signal['symbol'], o, limit_price, merge_sl))
        return o, merge_sl
    else:
        log.error(msg.log_merge_limit_failed(ch_num, limit_price))
        return None, real_sl

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

async def heartbeat_loop(manager, tracker):
    """Heartbeat désactivé — pas de messages BOT ACTIF"""
    return

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

        asyncio.create_task(heartbeat_loop(manager, tracker))

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

        @client.on(events.NewMessage(chats=chats))
        async def handler(event):
            text = event.message.text or ""
            chat = await event.get_chat()
            canal_name = entity_to_name.get(chat.id, getattr(chat, "title", "inconnu"))

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
                bridge.close_all(symbol=signal_data.close_symbol, channel_num=ch_num)
                return

            elif signal_data.signal_type == "SL_MOVE":
                log.debug(f"SL MOVE reçu → nouveau SL={signal_data.new_sl}")
                ch_num = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), None))
                if ch_num is not None:
                    bridge.update_sl_by_channel(signal_data.new_sl, ch_num)
                    manager.update_pending_orders_sl(ch_num, signal_data.new_sl)
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
                        send_alert_sync(
                            f"🚫 SIGNAL REJETÉ PAR TIMESFM\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"{sig_dict['action']} {sig_dict['symbol']}\n"
                            f"Prédit: {tfm_result['predicted_direction']} "
                            f"({tfm_result['predicted_move_pips']} pips)\n"
                            f"Confiance: {tfm_result['confidence']}\n"
                            f"Raison: {tfm_result['reason']}\n"
                            f"Canal: {canal_name}"
                        )
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
                    # --- Échec fusion : annuler le QA, qu'il soit une position ouverte
                    # (Market Price) OU un ordre LIMIT encore en attente ---
                    cancelled_qa = False
                    qa_pnl = 0.0
                    qa_type_label = ""
                    for idx, qa in enumerate(qa_list):
                        ticket = qa.get("ticket")
                        if qa.get("is_market_price", False):
                            # QA déjà ouverte en position réelle → fermer
                            if ticket:
                                pos = mt5.positions_get(ticket=ticket)
                                if pos:
                                    bridge.close_position(ticket)
                                    # ★ FIX : P&L réel post-clôture (pas le snapshot flottant pré-clôture)
                                    qa_pnl = manager._get_last_pnl(ticket, sig_dict["symbol"])
                                    log.info(f"[FUSION FAIL] QA Market Price #{ticket} annulé (hors tolérance ±{FUSION_TOLERANCE})")
                                    cancelled_qa = True
                                    qa_type_label = "Market Price"
                                else:
                                    log.debug(f"[FUSION FAIL] QA #{ticket} déjà fermé")
                        else:
                            # ★ FIX : QA-LMT encore pending → annuler l'ORDRE (pas une position),
                            # sinon il reste orphelin dans MT5, jamais suivi par le bot s'il se
                            # remplit plus tard (aucune entrée ne le référence plus).
                            if ticket:
                                order = mt5.orders_get(ticket=ticket)
                                if order:
                                    bridge.cancel_order(ticket)
                                    qa_entry = qa.get("entry")
                                    if qa_entry is not None:
                                        qa_entry["orders"] = [o for o in qa_entry.get("orders", []) if o["order"] != ticket]
                                    log.info(f"[FUSION FAIL] QA-LMT #{ticket} annulé (hors tolérance ±{FUSION_TOLERANCE})")
                                    cancelled_qa = True
                                    qa_type_label = "LMT"
                                else:
                                    log.debug(f"[FUSION FAIL] QA-LMT #{ticket} déjà résolu (rempli ou expiré)")
                        # Retirer le QA de la liste
                        qa_list.pop(idx)
                        if not qa_list:
                            _quick_alerts.pop(key, None)
                        break

                    if cancelled_qa:
                        log.info(f"[FUSION FAIL] Exécution signal complet après annulation QA")
                        pnl_line = f"P&L : {qa_pnl:+.2f} $\n" if qa_type_label == "Market Price" else ""
                        send_alert_sync(
                            f"⚠️ FUSION ÉCHOUÉE | {sig_dict['action']}\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"QA {qa_type_label} annulé (hors ±{FUSION_TOLERANCE})\n"
                            f"{pnl_line}"
                            f"Signal complet exécuté\n"
                            f"Canal: {canal_name}"
                        )
                    execute_signal(sig_dict, bridge, manager, tracker)

        # Banner
        mode = "🔲 DEMO" if DEMO_MODE else "💰 LIVE"
        log.info("=" * 55)
        log.info(f" Mode: {mode}")
        log.info(f" Lot :  total : {LOT_SIZE} | unique : {LOT_UNIQUE_TRADE}")
        log.info(f" Gain fixe par position : {TP_FIXED_GAIN_USD}$")
        log.info(f" BE déclenché à : {PNL_TRIGGER_USD}$")
        log.info(f" Objectif quotidien : {DAILY_PROFIT_LIMIT}$")

        log.info(f" Heartbeat : {HEARTBEAT_INTERVAL_MIN} min")
        log.info(f" SL prix unique: {SL_PRIX_UNIQUE}$")
        log.info(f" SL total (2 positions) : {SL_TOTAL}$")
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
