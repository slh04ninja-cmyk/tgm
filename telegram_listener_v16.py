"""
=============================================================
 TELEGRAM → MT5 | Bot Trading
 Version 15.0.0 — Canaux depuis Channels.txt
=============================================================
MODIFICATIONS v14.0.0 (depuis v13.0.0) :

■ NOUVEAU : Lecture des canaux depuis Channels.txt
- Numérotation persistante (même après redémarrage)
- Plus de dépendance au dossier Telegram pour la numérotation
- Fallback sur TG_FOLDER si Channels.txt absent
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
_deps = {"dotenv": "python-dotenv", "telethon": "telethon", "MetaTrader5": "MetaTrader5", "fpdf": "fpdf2"}
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
# ★ V15 : si Channels.txt existe, les canaux sont lus depuis ce fichier
# pour garantir une numérotation persistante après redémarrage.
TG_FOLDER = os.getenv("TG_FOLDER", "")
CHANNELS_TXT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Channels.txt")

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

# === SL PLAFONNÉ ===
# Distance SL maximale en $ (pour XAUUSD 0.01 lot, 1$ prix = 1$ P&L)
# Si le SL du signal dépasse cette distance, il est capé.
MAX_SL_USD = float(os.getenv("MAX_SL_USD", "10.0"))

# === FILTRES ===
TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "3"))
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "20"))
DAILY_PROFIT_LIMIT = float(os.getenv("DAILY_PROFIT_LIMIT", "200.0"))

# === FILTRE TRADINGVIEW (consensus 26 indicateurs) ===
# Bloque les signaux en opposition avec le consensus TradingView.
# 26 indicateurs votent BUY/SELL/NEUTRAL → consensus = STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
TV_FILTER_ENABLED = os.getenv("TV_FILTER_ENABLED", "false").lower() == "true"
TV_FILTER_SYMBOL = os.getenv("TV_FILTER_SYMBOL", "GOLD")
TV_FILTER_SCREENER = os.getenv("TV_FILTER_SCREENER", "cfd")
TV_FILTER_EXCHANGE = os.getenv("TV_FILTER_EXCHANGE", "TVC")
TV_FILTER_TIMEFRAME = os.getenv("TV_FILTER_TIMEFRAME", "5m")  # 1m, 5m, 15m, 30m, 1h, 4h, 1d
TV_FILTER_CACHE_TTL = int(os.getenv("TV_FILTER_CACHE_TTL", "30"))  # secondes entre chaque refresh
# Seuils de consensus (nombre de votes BUY/SELL sur 26 indicateurs)
TV_STRONG_BUY = int(os.getenv("TV_STRONG_BUY", "15"))   # ≥ 15 BUY → STRONG_BUY
TV_BUY = int(os.getenv("TV_BUY", "10"))                  # ≥ 10 BUY → BUY
TV_STRONG_SELL = int(os.getenv("TV_STRONG_SELL", "15"))   # ≥ 15 SELL → STRONG_SELL
TV_SELL = int(os.getenv("TV_SELL", "10"))                 # ≥ 10 SELL → SELL

# === CACHE TTL ===


# === HEARTBEAT ===


# === PARAMÈTRES SL (définis dans .env) ===
FUSION_TOLERANCE = float(os.getenv("FUSION_TOLERANCE", "3"))
CONFLIT_FILTER_ENABLED = os.getenv("CONFLIT_FILTER_ENABLED", "true").lower() == "true"
# ★ MODE POSITION UNIQUE : convertit les signaux zone (2 positions) en MARKET seul,
# et désactive le merge QA+Fusion. Seul le Quick Alert est exécuté.

# === TOLÉRANCES ===
TOLERANCE_ZN = float(os.getenv("TOLERANCE_ZN", "1.0"))
TOLERANCE_PU = float(os.getenv("TOLERANCE_PU", "3.0"))
TOLERANCE_MP = float(os.getenv("TOLERANCE_MP", "5.0"))
TP_PAR_DEFAUT = float(os.getenv("TP_PAR_DEFAUT", "15.0"))

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
# SIGNAL PARSER
# =============================================================
from signal_parser_v15 import SignalParser, is_spam
import bot_messages_v15 as msg

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
            # ★ FIX : annuler aussi les ordres LIMIT en attente
            with self.manager._lock:
                for entry in self.manager.active:
                    if not entry.get("_limit_cancelled"):
                        self.bridge.cancel_pending_limits(entry)
                        entry["_limit_cancelled"] = True
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
# TRADINGVIEW FILTER (consensus 26 indicateurs)
# =============================================================
class TradingViewFilter:
    """Filtre basé sur le consensus des 26 indicateurs TradingView.

    Logique :
    - 26 indicateurs (11 oscillateurs + 15 moyennes mobiles) votent BUY/SELL/NEUTRAL
    - Consensus = STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    - Signal BUY reçu + consensus SELL/STRONG_SELL → BLOQUÉ
    - Signal SELL reçu + consensus BUY/STRONG_BUY → BLOQUÉ
    """

    _INTERVAL_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "2h": "2h", "4h": "4h",
        "1d": "1d", "1W": "1W", "1M": "1M",
    }

    def __init__(self):
        self.enabled = TV_FILTER_ENABLED
        self.symbol = TV_FILTER_SYMBOL
        self.screener = TV_FILTER_SCREENER
        self.exchange = TV_FILTER_EXCHANGE
        self.timeframe = TV_FILTER_TIMEFRAME
        self.cache_ttl = TV_FILTER_CACHE_TTL
        self.strong_buy_threshold = TV_STRONG_BUY
        self.buy_threshold = TV_BUY
        self.strong_sell_threshold = TV_STRONG_SELL
        self.sell_threshold = TV_SELL
        # Cache
        self._last_consensus = None    # "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"
        self._last_buy_count = 0
        self._last_sell_count = 0
        self._last_neutral_count = 0
        self._last_recommendation = ""
        self._last_update = 0.0

    def is_allowed(self, action: str) -> tuple[bool, str]:
        """Vérifie si la direction du signal est alignée avec le consensus TradingView.

        Returns:
            (True, "") si le signal est autorisé
            (False, motif) si le signal doit être bloqué
        """
        if not self.enabled:
            return True, ""

        now = time.time()
        if now - self._last_update > self.cache_ttl:
            self._refresh()

        if self._last_consensus is None:
            # Données indisponibles → laisser passer (ne pas bloquer par erreur)
            return True, ""

        consensus = self._last_consensus

        # Signal BUY → consensus doit être BUY ou STRONG_BUY (NEUTRAL/SELL/STRONG_SELL = bloqué)
        if action == "BUY" and consensus not in ("BUY", "STRONG_BUY"):
            motif = (
                f"TRADINGVIEW OPPOSÉ : signal BUY mais consensus {consensus} "
                f"({self._last_buy_count} BUY / {self._last_sell_count} SELL / {self._last_neutral_count} NEUTRAL)"
            )
            return False, motif

        # Signal SELL → consensus doit être SELL ou STRONG_SELL (NEUTRAL/BUY/STRONG_BUY = bloqué)
        if action == "SELL" and consensus not in ("SELL", "STRONG_SELL"):
            motif = (
                f"TRADINGVIEW OPPOSÉ : signal SELL mais consensus {consensus} "
                f"({self._last_buy_count} BUY / {self._last_sell_count} SELL / {self._last_neutral_count} NEUTRAL)"
            )
            return False, motif

        return True, ""

    def _refresh(self):
        """Interroge TradingView pour obtenir le consensus des 26 indicateurs."""
        try:
            from tradingview_ta import TA_Handler, Interval

            # Mapper le timeframe
            interval = self._INTERVAL_MAP.get(self.timeframe, Interval.INTERVAL_5_MINUTES)

            handler = TA_Handler(
                symbol=self.symbol,
                screener=self.screener,
                exchange=self.exchange,
                interval=interval,
            )

            analysis = handler.get_analysis()
            summary = analysis.summary

            recommendation = summary.get("RECOMMENDATION", "")
            buy_count = summary.get("BUY", 0)
            sell_count = summary.get("SELL", 0)
            neutral_count = summary.get("NEUTRAL", 0)

            # Déterminer le consensus avec les seuils personnalisés
            if buy_count >= self.strong_buy_threshold:
                consensus = "STRONG_BUY"
            elif buy_count >= self.buy_threshold:
                consensus = "BUY"
            elif sell_count >= self.strong_sell_threshold:
                consensus = "STRONG_SELL"
            elif sell_count >= self.sell_threshold:
                consensus = "SELL"
            else:
                consensus = "NEUTRAL"

            self._last_consensus = consensus
            self._last_buy_count = buy_count
            self._last_sell_count = sell_count
            self._last_neutral_count = neutral_count
            self._last_recommendation = recommendation
            self._last_update = time.time()

            log.debug(
                f"[TV] {self.symbol} {self.timeframe} → {consensus} "
                f"({buy_count} BUY / {sell_count} SELL / {neutral_count} NEUTRAL) "
                f"[TV raw: {recommendation}]"
            )

        except ImportError:
            log.error("[TV] tradingview_ta non installé → pip install tradingview_ta")
            self._last_consensus = None
        except Exception as e:
            log.warning(f"[TV] Erreur TradingView: {e}")
            self._last_consensus = None

    def get_status(self) -> str:
        """Retourne une ligne de status pour les logs."""
        if not self.enabled:
            return "Filtre TradingView: OFF"
        if self._last_consensus is None:
            return (
                f"Filtre TradingView: ON ({self.symbol} {self.timeframe}) "
                f"— données indisponibles"
            )
        return (
            f"Filtre TradingView: ON ({self.symbol} {self.timeframe}) "
            f"— {self._last_consensus} "
            f"({self._last_buy_count}B/{self._last_sell_count}S/{self._last_neutral_count}N)"
        )


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

    def cancel_pending_limits(self, entry: dict) -> int:
        """Annule tous les ordres LIMIT non remplis d'une entrée."""
        cancelled = 0
        orders = mt5.orders_get()
        if not orders:
            return 0
        for t in entry.get("tickets", []):
            if t.get("role") != "limit":
                continue
            ticket = t["ticket"]
            for order in orders:
                if order.ticket == ticket and order.magic == MAGIC_NUMBER:
                    if self.cancel_order(ticket):
                        cancelled += 1
                        log.debug(f"  ✗ LIMIT #{ticket} annulé")
        return cancelled

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
        # ★ FIX (v16) : self._daily_pnl doit être calculé AVANT _load_daily_limit_state(),
        # qui a maintenant besoin du P&L réel pour revalider le flag persisté (voir plus bas).
        self._daily_pnl = self._recover_daily_pnl()
        self._daily_pnl_day = get_trading_day_start().day
        self._daily_limit_reached = self._load_daily_limit_state()  # FIX: persisté sur disque, revalidé contre le P&L réel
        # ★ FIX (v16) : garde anti-répétition du log "Limite quotidienne atteinte" (voir
        # _check_daily_pnl_limit). Si le bot redémarre alors que la limite est déjà
        # atteinte, on considère qu'elle a déjà été "annoncée" dans une vie précédente du
        # process — pas la peine de la re-logguer au premier cycle qui suit.
        self._limit_log_emitted = self._daily_limit_reached
        # ★ Max Drawdown tracking en temps réel
        self._running_pnl = 0.0    # P&L cumulé du jour
        self._min_pnl = 0.0        # creux le plus bas vs 0$

        self._pos_cache = None  # rafraîchi à chaque cycle par _refresh_pos_cache()
        self._end_of_day_done = False  # flag pour éviter les fermetures répétées
        self._completed_entries = []  # entrées terminées (pour rapport fin de journée)



    # =============================================================
    # P&L QUOTIDIEN (avec verrouillage)
    # ★ FIX : persistance du flag _daily_limit_reached sur disque
    _LIMIT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_daily_limit_state.json")

    def _load_daily_limit_state(self) -> bool:
        """Charge le flag _daily_limit_reached depuis le disque.

        ★ FIX (v16) : l'ancienne version relisait `limit_reached` tel quel depuis le
        fichier JSON, sans jamais le recroiser avec le P&L réel ni la limite actuelle.
        Conséquence concrète observée : limite quotidienne atteinte → bot arrêté →
        redémarrage (même en ayant doublé DAILY_PROFIT_LIMIT dans le .env) → le flag
        restait bloqué à True pour le reste de la journée → plus aucun signal exécuté,
        car `_check_daily_pnl_limit()` (qui, lui, aurait correctement recalculé avec la
        nouvelle limite) n'était jamais consulté : `self._daily_limit_reached` seul
        suffisait à bloquer (cf. `if not self._check_daily_pnl_limit() or self._daily_limit_reached`).
        On revalide donc désormais le flag au chargement : s'il était vrai mais que le
        P&L réel du jour (self._daily_pnl, déjà recalculé depuis MT5 par
        _recover_daily_pnl() avant cet appel — voir ordre dans __init__) est repassé sous
        la limite actuelle (nouvelle valeur .env ou pertes ayant réduit le P&L cumulé), on
        le lève.
        """
        try:
            if os.path.exists(self._LIMIT_STATE_FILE):
                with open(self._LIMIT_STATE_FILE, 'r') as f:
                    data = json.load(f)
                # Vérifier que c'est le même jour de trading
                start = get_trading_day_start()
                if data.get('day') == start.day:
                    was_reached = data.get('limit_reached', False)
                    if not was_reached:
                        return False
                    # Revalider contre le P&L réel (déjà rechargé depuis MT5) et la limite
                    # actuelle du .env — pas celle en vigueur au moment où le flag a été écrit.
                    real_pnl = getattr(self, '_daily_pnl', None)
                    if real_pnl is None:
                        # Ordre d'init inattendu : rester prudent, ne pas débloquer à tort.
                        return was_reached
                    if DAILY_PROFIT_LIMIT > 0 and real_pnl >= DAILY_PROFIT_LIMIT:
                        return True
                    log.info(
                        f"Flag limite quotidienne levé au démarrage : P&L réel "
                        f"{real_pnl:.2f}$ < limite actuelle {DAILY_PROFIT_LIMIT}$ "
                        f"(le flag persisté datait d'une limite différente ou le P&L a changé)"
                    )
                    return False
        except Exception:
            pass
        return False

    def _save_daily_limit_state(self):
        """Sauvegarde le flag _daily_limit_reached sur disque."""
        try:
            start = get_trading_day_start()
            with open(self._LIMIT_STATE_FILE, 'w') as f:
                json.dump({'day': start.day, 'limit_reached': self._daily_limit_reached}, f)
        except Exception:
            pass

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
                self._limit_log_emitted = False  # ★ FIX (v16) : réarmer le log pour le nouveau jour
                self._save_daily_limit_state()
                self._running_pnl = 0.0
                self._min_pnl = 0.0
                self._completed_entries = []  # reset pour le nouveau jour
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
                self._limit_log_emitted = False  # ★ FIX (v16) : réarmer le log pour le nouveau jour
                self._save_daily_limit_state()
                self._running_pnl = 0.0
                self._min_pnl = 0.0
                self._completed_entries = []  # reset pour le nouveau jour
                log.info(f"RESET JOURNALIER A {TRADING_START_HOUR}H UTC")
            total_pnl = self._daily_pnl + self._get_floating_pnl()
            if DAILY_PROFIT_LIMIT > 0 and total_pnl >= DAILY_PROFIT_LIMIT:
                # ★ FIX (v16) : cette fonction est appelée à chaque cycle de _check_all()
                # (~1x/seconde). Sans garde, le log ci-dessous s'écrivait en boucle infinie
                # toute la journée dès que la limite était atteinte. On ne log désormais
                # qu'au moment de la transition (première détection), pas à chaque appel.
                if not getattr(self, '_limit_log_emitted', False):
                    log.info(f"Limite quotidienne atteinte : {total_pnl:.2f}$ / {DAILY_PROFIT_LIMIT}$")
                    self._limit_log_emitted = True
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
        time.sleep(0.5)  # ★ FIX: attendre la propagation MT5 avant de lire le P&L

        with self._daily_lock:
            self._update_daily_pnl(total_pnl)
            # ★ FIX: après fermeture de toutes les positions, le floating = 0
            # Ne pas appeler _get_floating_pnl() qui peut encore voir les positions fermées
            total = self._daily_pnl

        # ★ FIX : ne PAS vider self.active ici — les données sont nécessaires
        # pour le rapport de fin de journée. Le flag _daily_limit_reached
        # bloque le traitement des nouveaux signaux.
        self._daily_limit_reached = True
        self._save_daily_limit_state()

        # ★ FIX : marquer tous les tickets comme _reported pour éviter
        # le double comptage P&L quand _check_all détecte les positions fermées
        for entry in self.active:
            for t in entry.get("tickets", []):
                if not t.get("_reported"):
                    t["_reported"] = True
                    sym = entry.get("signal", {}).get("symbol", "")
                    t["_last_pnl"] = self._get_last_pnl(t["ticket"], sym)

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
        # ★ FIX (v16) : le vrai P&L du jour est la somme des canaux recalculée depuis MT5
        # dans report_data (déjà validée = somme trades réels), pas self._daily_pnl qui est
        # un accumulateur incrémental pouvant dériver (double-comptage partiel observé :
        # écart constaté de -78.75$ sur un rapport réel). On l'utilise pour l'affichage du
        # rapport (résumé + PDF) sans toucher self._daily_pnl lui-même, qui reste utilisé
        # tel quel pour la logique de limite quotidienne ailleurs dans le code.
        report_pnl_realise = sum(ch['pnl'] for ch in report_data['channels'])

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
                pnl_realise=report_pnl_realise,
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
                signal_types=report_data.get('signal_types'),
            )
            send_alert_sync(report)
            log.info(report)

            # ★ Générer et envoyer le PDF
            pdf_path = _generate_daily_report_pdf(report_data, report_pnl_realise, today)
            if pdf_path:
                _send_telegram_document(pdf_path, f"📊 Rapport {today}")

        else:
            log.info(msg.log_daily_limit_header())
            log.info(msg.log_daily_limit_detail(total, nb_positions, cancelled))

    def _collect_daily_report_data(self) -> dict:
        """Collecte les données pour le rapport quotidien.

        ★ FIX (v16) : source de vérité = historique MT5 (mt5.history_deals_get), pas la
        mémoire du process (self.active / self._completed_entries). L'ancienne version ne
        voyait que les signaux traités depuis le dernier démarrage du bot : après un
        redémarrage en cours de journée (crash, redéploiement...), le rapport ne montrait
        qu'une fraction des trades réels (ex: 20 trades au lieu de 444), alors que
        `_daily_pnl` lui-même est déjà recalculé correctement depuis MT5 via
        `_recover_daily_pnl()`. On applique ici le même principe : reconstruire trades,
        méthodes, canaux, types de signal et TP/SL depuis les deals MT5 de la journée,
        en utilisant le commentaire d'ordre `CH{num}-{signal}-{méthode}` (écrit à
        l'ouverture, cf. `_open_market_limit`) comme clé de regroupement — exactement
        la même donnée, à la même source, que celle utilisée pour les rapports manuels.
        """
        # Structure: {method: {trades, wins, losses, pnl}}, {ch_num: {trades, wins, losses, pnl}}
        methods = {}
        channels = {}
        signal_types = {}  # stats par type de signal (PU1, PU2, ZN1, ZN2, MP, AL1, AL2...)
        tp_count = tp_pnl = sl_count = sl_pnl = 0
        total_trades = total_wins = total_losses = 0

        # Map ch_num → canal name — priorité Channels.txt (nom en commentaire), fallback .env
        # ★ FIX (v16) : depuis le passage de Channels.txt au format par ID Telegram
        # (Canal_N : -100XXXXXXXXXX # NomDuCanal), le nom lisible est maintenant dans le
        # commentaire après le '#', et non plus dans la valeur principale (qui est un ID).
        # L'ancienne extraction prenait la valeur principale telle quelle, affichant l'ID
        # brut ("−1003864453549") dans le rapport au lieu du nom du canal.
        ch_name_map = {}
        _channels_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Channels.txt')
        try:
            with open(_channels_file, 'r', encoding='utf-8') as f:
                for line in f:
                    m = re.match(r'Canal_(\d+)\s*:\s*(.+)', line.strip())
                    if m:
                        ch_num = int(m.group(1))
                        raw = m.group(2)
                        if '#' in raw:
                            main_part, comment_part = raw.split('#', 1)
                            main_part = main_part.strip()
                            comment_part = comment_part.strip()
                        else:
                            main_part, comment_part = raw.strip(), ''
                        # Si la valeur principale est un ID Telegram (entier négatif), le
                        # nom lisible est dans le commentaire ; sinon (ancien format), la
                        # valeur principale EST le nom.
                        try:
                            int(main_part)
                            is_id = True
                        except ValueError:
                            is_id = False
                        if is_id:
                            ch_name_map[ch_num] = comment_part if comment_part else f"CH{ch_num}"
                        else:
                            ch_name_map[ch_num] = main_part
        except Exception:
            pass
        # Repli supplémentaire : si le nom résolu ci-dessus est vide/absent, utiliser le
        # titre réel résolu par Telethon au démarrage (CHANNEL_NUM_MAP, ch_num → id/nom →
        # ch_num ; on en tire l'inverse ici), plus fiable qu'un commentaire pouvant dater
        # d'un renommage du canal depuis la dernière mise à jour de Channels.txt.
        _chnum_to_live_name = {}
        for _key, _cn in CHANNEL_NUM_MAP.items():
            if not _key.lstrip('-').isdigit():  # ignore les clés qui sont des IDs numériques
                _chnum_to_live_name[_cn] = _key
        for _cn, _live_name in _chnum_to_live_name.items():
            if not ch_name_map.get(_cn) or ch_name_map[_cn].startswith('CH'):
                ch_name_map[_cn] = _live_name
        for _env_name, _val in _CHANNELS_LIST:
            _num = int(_env_name.replace('TG_CHANNEL_', ''))
            if _num not in ch_name_map:
                ch_name_map[_num] = _val

        # Rôle interne (utilisé pour l'affichage "Performance par methode") d'après le
        # suffixe MK/L1/L2 du commentaire — reflète _open_market_limit.
        suffix_to_role = {
            'MK': 'market', 'L1': 'limit', 'L2': 'limit',
        }

        # ── Récupérer tous les deals MT5 de la journée de trading en cours ──
        start = get_trading_day_start()
        now = datetime.now(timezone.utc)
        lookup_start = start - timedelta(days=3)  # même marge que _recover_daily_pnl
        all_deals = mt5.history_deals_get(lookup_start, now)
        if all_deals is None:
            all_deals = []

        # Indexer : magic + comment à l'OUVERTURE de chaque position (le deal de clôture
        # peut avoir un magic=0 si fermeture manuelle, et un commentaire vide côté deal —
        # le commentaire fiable est celui de l'ORDRE d'ouverture, cf. bug connu v8SL2).
        open_magic = {}
        open_comment = {}
        for deal in all_deals:
            if deal.entry == mt5.DEAL_ENTRY_IN:
                open_magic[deal.position_id] = deal.magic
                # Le commentaire du deal IN reprend celui de l'ordre d'ouverture dans la
                # quasi-totalité des cas ; fallback ci-dessous si vide.
                if getattr(deal, 'comment', ''):
                    open_comment[deal.position_id] = deal.comment

        # Fallback : pour les positions dont le deal IN n'a pas de commentaire, relire le
        # commentaire directement sur l'ordre d'ouverture (mt5.history_orders_get).
        missing_ids = [pid for pid in open_magic if pid not in open_comment]
        if missing_ids:
            orders = mt5.history_orders_get(lookup_start, now)
            if orders:
                order_comment = {o.ticket: o.comment for o in orders if getattr(o, 'comment', '')}
                for pid in missing_ids:
                    # position_id d'une position ouverte au marché == ticket de l'ordre d'ouverture
                    c = order_comment.get(pid)
                    if c:
                        open_comment[pid] = c

        # ── Regrouper les deals OUT (clôtures) de la journée par position ──
        closed_positions = {}  # position_id -> {'pnl': float}
        for deal in all_deals:
            if deal.entry != mt5.DEAL_ENTRY_OUT:
                continue
            if deal.time < start.timestamp():
                continue
            origin_magic = open_magic.get(deal.position_id, deal.magic)
            if origin_magic != MAGIC_NUMBER:
                continue
            cp = closed_positions.setdefault(deal.position_id, {'pnl': 0.0})
            cp['pnl'] += deal.profit
            # ★ FIX (v16) : ne plus classer TP/SL sur deal.reason (DEAL_REASON_TP/SL).
            # Cette raison reflète UNIQUEMENT le type d'ordre MT5 qui a déclenché la
            # clôture (un stop-order, que ce soit le SL initial ou un trailing/BE déplacé
            # en profit, est TOUJOURS reporté par MT5 comme DEAL_REASON_SL). Résultat
            # observé : des trades gagnants fermés par trailing (P3) ou BE scale (P2)
            # étaient comptés comme "SL" dans la section Clôtures du rapport, alors même
            # que Win/Loss (basé sur le signe du P&L) les comptait correctement comme
            # gagnants — d'où un écart de plusieurs dizaines de trades entre les deux
            # sections d'un même rapport (ex: 27 TP / 81 SL affichés pour 62 wins / 53
            # losses réels). Le classement TP/SL ci-dessous se fait maintenant sur le
            # signe du P&L cumulé de la position, cohérent avec le comptage Win/Loss.

        # ── Regrouper par signal (canal + type) pour compter "Total signaux" correctement ──
        signal_groups = set()

        # Max drawdown tracking — recalculé à partir des clôtures triées chronologiquement,
        # cohérent avec self._min_pnl (qui, lui, reste mis à jour en temps réel par ailleurs).
        running_pnl = 0.0
        min_pnl = 0.0
        # trier par heure de clôture : on doit re-scanner all_deals pour l'ordre temporel
        ordered_pids = sorted(
            closed_positions.keys(),
            key=lambda pid: min(d.time for d in all_deals if d.position_id == pid and d.entry == mt5.DEAL_ENTRY_OUT)
        )

        for pid in ordered_pids:
            cp = closed_positions[pid]
            pnl = cp['pnl']
            comment = open_comment.get(pid, '')

            parts = comment.split('-')
            ch_num = 0
            for part in parts:
                if part.startswith('CH') and part[2:].isdigit():
                    ch_num = int(part[2:])
                    break
            sig_type = parts[1] if len(parts) >= 2 else None
            suffix = parts[2] if len(parts) >= 3 else None
            role = suffix_to_role.get(suffix, 'unknown')

            source_channel = ''
            ch_name = ch_name_map.get(ch_num, source_channel)
            if ch_name.startswith('@'):
                ch_name = ch_name[1:]
            if not ch_name:
                ch_name = f"CH{ch_num}" if ch_num else "Inconnu"

            if sig_type:
                signal_groups.add((ch_num, sig_type))

            # Max drawdown
            running_pnl += pnl
            if running_pnl < min_pnl:
                min_pnl = running_pnl

            # Stats par méthode
            if role not in methods:
                methods[role] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
            methods[role]['trades'] += 1
            methods[role]['pnl'] += pnl
            if pnl > 0:
                methods[role]['wins'] += 1
            elif pnl < 0:
                methods[role]['losses'] += 1

            # Stats par canal
            if ch_num not in channels:
                channels[ch_num] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'name': ch_name}
            channels[ch_num]['trades'] += 1
            channels[ch_num]['pnl'] += pnl
            if pnl > 0:
                channels[ch_num]['wins'] += 1
            elif pnl < 0:
                channels[ch_num]['losses'] += 1

            # Stats par type de signal
            if sig_type:
                if sig_type not in signal_types:
                    signal_types[sig_type] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'win_pnl': 0.0, 'loss_pnl': 0.0, 'channels': set()}
                signal_types[sig_type]['trades'] += 1
                signal_types[sig_type]['pnl'] += pnl
                if ch_num:
                    signal_types[sig_type]['channels'].add(ch_num)
                if pnl > 0:
                    signal_types[sig_type]['wins'] += 1
                    signal_types[sig_type]['win_pnl'] += pnl
                elif pnl < 0:
                    signal_types[sig_type]['losses'] += 1
                    signal_types[sig_type]['loss_pnl'] += pnl

            # Stats TP/SL — ★ FIX (v16) : classement par signe du P&L (cohérent avec
            # Win/Loss ci-dessus), et non plus par deal.reason MT5 (voir commentaire plus
            # haut sur closed_positions). "TP" = trade gagnant, "SL" = trade perdant.
            if pnl > 0:
                tp_count += 1
                tp_pnl += pnl
            elif pnl < 0:
                sl_count += 1
                sl_pnl += pnl

            # Total
            total_trades += 1
            if pnl > 0:
                total_wins += 1
            elif pnl < 0:
                total_losses += 1

        total_signals = len(signal_groups)
        winrate = total_wins / total_trades * 100 if total_trades > 0 else 0

        # Formater les méthodes pour bot_messages
        # ★ P4a + P4b combinés en une seule ligne P4 (50/50 split)
        method_names = {
            'market': 'MARKET',
            'limit': 'LIMIT',
        }
        methods_list = []
        for role in ['market', 'limit']:
            if role in methods:
                m = methods[role]
                m['name'] = method_names.get(role, role)
                methods_list.append(m)

        # Formater les canaux — ★ FIX (v16) : trié par P&L décroissant (cohérent avec les
        # rapports manuels générés jusqu'ici) au lieu de l'ordre d'insertion arbitraire.
        channels_list = sorted(
            [{'ch_num': ch, **stats} for ch, stats in channels.items()],
            key=lambda c: -c['pnl']
        )

        # Formater les types de signal
        # ★ FIX (v16) : l'ancienne liste figée ['ZN1','ZN2','PU1','PU2','MP','AL'] excluait
        # silencieusement tout type de signal non prévu à l'avance (ex: AL1/AL2, apparus en
        # Août 2026 — cf. rapport du 05/08 où ils représentent une part significative du volume).
        # On affiche désormais tous les types réellement rencontrés dans les deals du jour,
        # triés par P&L décroissant pour rester lisible.
        signal_types_list = []
        for st, d in sorted(signal_types.items(), key=lambda kv: -kv[1]['pnl']):
            avg_win = d['win_pnl'] / d['wins'] if d['wins'] > 0 else 0
            avg_loss = d['loss_pnl'] / d['losses'] if d['losses'] > 0 else 0
            signal_types_list.append({
                'type': st,
                'channels': len(d['channels']),
                'trades': d['trades'],
                'wins': d['wins'],
                'losses': d['losses'],
                'pnl': d['pnl'],
                'avg_win': avg_win,
                'avg_loss': avg_loss,
            })

        return {
            'total_signals': total_signals,
            'trades': total_trades,
            'wins': total_wins,
            'losses': total_losses,
            'winrate': winrate,
            'max_drawdown': min_pnl,  # ★ FIX (v16) : recalculé depuis l'historique MT5 de la
            # journée (variable locale ci-dessus), et non plus self._min_pnl qui repart de 0
            # à chaque redémarrage du process et sous-estimait le vrai drawdown journalier.
            'methods': methods_list,
            'channels': channels_list,
            'signal_types': signal_types_list,
            'tp_count': tp_count,
            'tp_pnl': tp_pnl,
            'sl_count': sl_count,
            'sl_pnl': sl_pnl,
        }

    # =============================================================
    # GESTION DU BE (avec whitelist)
    # =============================================================
    def _recalculate_tp(self, entry: dict):
        """Recalcule le TP dynamique basé sur TP_FIXED_GAIN_USD.

        Cas 1: MARKET seul         → pnl_cible = TP_FIXED_GAIN_USD * 1
        Cas 2: MARKET + LIMIT1     → pnl_cible = TP_FIXED_GAIN_USD * TP_MULTIPE1
        Cas 3: MARKET + L1 + L2    → pnl_cible = TP_FIXED_GAIN_USD * TP_MULTIPE2

        Formule: TP = average_entry ± (pnl_cible / nb_positions)
        XAUUSD: 0.01 lot = 1 oz, 1$ mouvement = 1$ P&L par position.
        Le SL reste fixe (jamais déplacé).
        """
        signal = entry.get("signal", {})
        action = signal.get("action", "BUY")

        # Collecter les positions actives
        active = []
        for t in entry.get("tickets", []):
            pos = self._get_pos(t["ticket"])
            if pos:
                active.append(t)

        if not active:
            return

        nb = len(active)
        if entry.get("_tp_calculated_for", 0) >= nb:
            return  # Déjà calculé pour ce nombre de positions

        # Average entry pondéré
        total_lot = sum(t["lot"] for t in active)
        weighted_entry = sum(t["entry_price"] * t["lot"] for t in active) / total_lot

        # Multiplicateur selon le nombre de positions
        if nb >= 3:
            multiplier = TP_MULTIPE2
        elif nb >= 2:
            multiplier = TP_MULTIPE1
        else:
            multiplier = 1.0

        # P&L cible et mouvement requis
        pnl_cible = TP_FIXED_GAIN_USD * multiplier
        price_movement = pnl_cible / nb

        # TP prix
        if action == "BUY":
            tp_price = round(weighted_entry + price_movement, 2)
        else:
            tp_price = round(weighted_entry - price_movement, 2)

        # Mettre à jour le TP sur toutes les positions
        updated = 0
        for t in active:
            pos = self._get_pos(t["ticket"])
            if pos:
                if self.bridge.modify_sl_tp(t["ticket"], pos.sl, tp_price, f"[DYN-TP x{multiplier}]"):
                    t["tp_final"] = tp_price
                    updated += 1

        # ★ FIX : ne marquer comme calculé QUE si au moins un TP a été mis à jour.
        # Sinon, le retry ne se fera jamais si modify_sl_tp échoue.
        if updated > 0:
            entry["_tp_calculated_for"] = nb
        entry["_pnl_cible"] = pnl_cible
        _log_mgmt(f"TP dynamique: {nb} pos | avg={weighted_entry:.2f} | lot={total_lot} | "
                  f"cible={pnl_cible}$ (x{multiplier}) | TP={tp_price} | {updated}/{len(active)} mis à jour")

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

        # ★ Max Drawdown tracking — mis à jour à chaque polling cycle
        with self._daily_lock:
            total_pnl_now = self._daily_pnl + self._get_floating_pnl()
            if total_pnl_now < self._min_pnl:
                self._min_pnl = total_pnl_now

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
                # ★ FIX (v16) : annuler les LIMITs restants AVANT de supprimer l'entrée.
                # Sans ça, quand toutes les positions (MK+L1 ou MK seul) sont fermées
                # au même cycle (TP/SL/CLOSE), l'entrée est retirée et PHASE 5 ne
                # s'exécute jamais → les LIMITs non remplis restent orphelins dans MT5
                # pendant LIMIT_EXPIRY_MIN. Si le prix revient, ils se remplissent en
                # créant des positions non gérées par le bot.
                if not entry.get("_limit_cancelled"):
                    cancelled = self.bridge.cancel_pending_limits(entry)
                    if cancelled > 0:
                        _log_mgmt(f"Trade terminé ({symbol}) → {cancelled} LIMIT orphelins annulés")
                    entry["_limit_cancelled"] = True

                total_pnl = sum(t.get("_last_pnl", 0.0) for t in entry.get("tickets", []))
                log.debug(f"Trade terminé ({symbol}) | Canal: {canal} | P&L total: {total_pnl:+.2f}")
                # ★ Sauvegarder l'entrée terminée pour le rapport de fin de journée
                # (self.active sera vidé mais le rapport a besoin de ces données)
                if entry not in self._completed_entries:
                    self._completed_entries.append(entry)
                # Note : le P&L quotidien est déjà mis à jour par ticket (voir boucle ci-dessus),
                # pas besoin de le ré-additionner ici (éviterait un double comptage).
                with self._lock:
                    if entry in self.active:
                        self.active.remove(entry)
                continue

            # ★★★ DÉTECTION LIMITS EXPIRÉS ★★★
            # Un LIMIT expire s'il n'est plus dans mt5.orders_get() et n'a jamais été rempli
            if not entry.get("_limit_expired_logged"):
                # Récupérer les ordres en attente
                pending_orders = mt5.orders_get()
                pending_tickets = {o.ticket for o in pending_orders} if pending_orders else set()

                expired_limits = []
                for t in entry.get("tickets", []):
                    if t.get("role") != "limit":
                        continue
                    if t.get("_reported") or t.get("_expired_logged"):
                        continue
                    # Si le LIMIT n'est plus en attente et n'a pas de position → expiré
                    if t["ticket"] not in pending_tickets and not self._get_pos(t["ticket"]):
                        expired_limits.append(t)
                        t["_expired_logged"] = True

                if expired_limits:
                    canal = entry.get("signal", {}).get("source_channel", "?")
                    ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
                    prefix = entry.get("_mt5_comment", "").split("-")[-1] if "-" in entry.get("_mt5_comment", "") else "?"
                    # Identifier quels LIMITs ont expiré
                    limit_names = []
                    for t in expired_limits:
                        comment = entry.get("_mt5_comment", "")
                        for tk in entry.get("tickets", []):
                            if tk is t:
                                idx = entry["tickets"].index(t)
                                # Trouver le numéro du LIMIT (L1, L2...)
                                limit_count = 0
                                for tk2 in entry["tickets"][:idx+1]:
                                    if tk2.get("role") == "limit":
                                        limit_count += 1
                                limit_names.append(f"L{limit_count}")
                                break
                    if limit_names:
                        names_str = " et ".join(limit_names)
                        _log_mgmt(f"CH{ch_num}-{prefix} | {names_str} EXPIRE{'S' if len(limit_names) > 1 else ''}")
                    entry["_limit_expired_logged"] = len([t for t in entry["tickets"] if t.get("role") == "limit"]) == len([t for t in entry["tickets"] if t.get("role") == "limit" and t.get("_expired_logged")])

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 4 : TP DYNAMIQUE (recalcul si LIMIT rempli) ★★★
            # ══════════════════════════════════════════════════════════════
            self._recalculate_tp(entry)

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 4b : CLÔTURE P&L CIBLE ★★★
            # Si P&L flottant >= pnl_cible → fermer tout + annuler LIMITs
            # ══════════════════════════════════════════════════════════════
            pnl_cible = entry.get("_pnl_cible", 0)
            if pnl_cible > 0 and not entry.get("_pnl_close_done"):
                floating = 0.0
                for t in entry.get("tickets", []):
                    pos = self._get_pos(t["ticket"])
                    if pos:
                        floating += pos.profit + pos.swap
                if floating >= pnl_cible:
                    entry["_pnl_close_done"] = True
                    symbol = entry.get("signal", {}).get("symbol", "?")
                    action = entry.get("signal", {}).get("action", "?")
                    closed = 0
                    for t in entry.get("tickets", []):
                        pos = self._get_pos(t["ticket"])
                        if pos:
                            if self.bridge.close_position(t["ticket"], comment="PNL-TARGET"):
                                closed += 1
                    cancelled = self.bridge.cancel_pending_limits(entry)
                    entry["_limit_cancelled"] = True
                    _log_mgmt(f"P&L CIBLE atteint: {floating:.2f}$ >= {pnl_cible}$ | "
                              f"{action} {symbol} | {closed} pos fermées | {cancelled} LIMIT annulés")
                    if ALERT_TRADE_MANAGEMENT:
                        _alert_mgmt(f"🎯 P&L CIBLE {pnl_cible}$ atteint ({floating:.2f}$) | "
                                    f"{action} {symbol} | {closed} fermées + {cancelled} LIMIT annulés")

            # ══════════════════════════════════════════════════════════════
            # ★★★ PHASE 5 : ANNULER LIMIT SI MARKET FERMÉ ★★★
            # ══════════════════════════════════════════════════════════════
            market_ticket = entry.get("_market_ticket")
            if market_ticket:
                market_pos = self._get_pos(market_ticket)
                if market_pos is None and not entry.get("_limit_cancelled"):
                    # MARKET fermé -> annuler tous les LIMIT non remplis
                    entry["_limit_cancelled"] = True
                    cancelled = self.bridge.cancel_pending_limits(entry)
                    if cancelled > 0:
                        _log_mgmt(f"MARKET #{market_ticket} fermé -> {cancelled} LIMIT annulés")

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
        # ★ FIX : ne supprimer l'entrée qu'APRÈS la fermeture réussie
        pass
    bridge.close_all(symbol=symbol, channel_num=ch_num)
    # Mettre à jour le P&L quotidien
    time.sleep(0.3)
    for ticket in conflict_tickets:
        deals = mt5.history_deals_get(position=ticket)
        if deals:
            pnl = sum(d.profit for d in deals if d.position_id == ticket and d.entry == mt5.DEAL_ENTRY_OUT)
            manager._update_daily_pnl(pnl)
    # ★ FIX : supprimer les entrées APRÈS la fermeture
    for e in to_remove:
        if e in manager.active:
            manager.active.remove(e)
    return True

def _close_previous_signal(canal: str, bridge: MT5Bridge, manager: TradeManager) -> bool:
    """Ferme le signal actif d'un canal s'il en existe déjà un."""
    with manager._lock:
        for entry in list(manager.active):
            sig = entry.get("signal", {})
            entry_canal = sig.get("source_channel", "Inconnu")
            if entry_canal == canal:
                # Trouver les positions ouvertes
                closed_any = False
                for t in entry.get("tickets", []):
                    pos = manager._get_pos(t["ticket"])
                    if pos:
                        ticket = t["ticket"]
                        sig_type = sig.get("type", "PU")
                        ch_num = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))
                        # ★ FIX : annuler les LIMITs AVANT de fermer
                        if not entry.get("_limit_cancelled"):
                            bridge.cancel_pending_limits(entry)
                            entry["_limit_cancelled"] = True
                        if bridge.close_position(ticket, "NEW-SIGNAL"):
                            closed_any = True
                            log.info(f"CH{ch_num}-{sig_type} | ANNULE PAR DUPLICATION")
                            # Mettre à jour le P&L quotidien (attendre que le deal apparaisse)
                            time.sleep(0.3)
                            pnl = manager._get_last_pnl(ticket, sig.get("symbol", ""))
                            manager._update_daily_pnl(pnl)
                # Retirer l'entrée APRÈS la fermeture
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

# === MARKET + LIMIT ORDERS ===
# Au lieu de 5 positions A/B testing (P1-P4), on ouvre :
#   1 MARKET immédiat (lot principal)
#   N LIMIT orders à meilleur prix pour catcher le pullback
LIMIT_ENABLED = os.getenv("LIMIT_ENABLED", "true").lower() == "true"
LIMIT_COUNT = int(os.getenv("LIMIT_COUNT", "2"))           # Nombre de LIMIT orders (0, 1 ou 2)
LIMIT_OFFSET_1 = float(os.getenv("LIMIT_OFFSET_1", "3.0"))  # 1er LIMIT à current ± ce montant
LIMIT_OFFSET_2 = float(os.getenv("LIMIT_OFFSET_2", "6.0"))  # 2ème LIMIT à current ± ce montant
LOT_MARKET = float(os.getenv("LOT_MARKET", "0.01"))         # Lot pour le MARKET
LOT_LIMIT1 = float(os.getenv("LOT_LIMIT1", "0.01"))         # Lot pour le LIMIT 1
LOT_LIMIT2 = float(os.getenv("LOT_LIMIT2", "0.01"))         # Lot pour le LIMIT 2
LIMIT_EXPIRY_MIN = int(os.getenv("LIMIT_EXPIRY_MIN", "30"))  # Expiration des LIMIT orders (minutes)
TP_MULTIPE1 = float(os.getenv("TP_MULTIPE1", "2.0"))         # Multiplicateur TP quand MARKET + L1 rempli
TP_MULTIPE2 = float(os.getenv("TP_MULTIPE2", "3.0"))         # Multiplicateur TP quand MARKET + L1 + L2 remplis

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
        log.warning("fpdf2 non installé — tentative d'installation...")
        try:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
            from fpdf import FPDF
            log.info("fpdf2 installé avec succès")
        except Exception as install_err:
            log.error(f"Impossible d'installer fpdf2: {install_err}")
            return ""

    # ── Helpers ──
    def _sanitize(text: str, max_len: int = 0) -> str:
        """Supprime les caractères non-ASCII et tronque."""
        clean = re.sub(r'[^\x00-\x7F]+', '', str(text)).strip()
        if max_len > 0:
            clean = clean[:max_len]
        return clean or "?"

    def _table_header(pdf, headers, font_name="Helvetica", font_size=9):
        """Écrit les headers d'un tableau avec style gras + fond gris."""
        pdf.set_font(font_name, "B", font_size)
        pdf.set_fill_color(230, 230, 230)
        for txt, w, align in headers:
            pdf.cell(w, 7, txt, border=1, align=align, fill=True)
        pdf.ln()
        pdf.set_font(font_name, "", font_size)

    class DailyReportPDF(FPDF):
        """PDF avec répétition automatique des headers de table sur page break."""
        _current_table_headers = None

        def header(self):
            if self.page_no() > 1 and self._current_table_headers:
                headers, font_info = self._current_table_headers
                self.set_font(*font_info)
                self.set_fill_color(230, 230, 230)
                for txt, w, align in headers:
                    self.cell(w, 7, txt, border=1, align=align, fill=True)
                self.ln()

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    pdf = DailyReportPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Données ──
    trades = report_data.get('trades', 0)
    wins = report_data.get('wins', 0)
    losses = report_data.get('losses', 0)
    winrate = report_data.get('winrate', 0.0)
    total_signals = report_data.get('total_signals', 0)
    max_dd = report_data.get('max_drawdown', 0.0)
    signal_types = report_data.get('signal_types', []) or []
    channels = report_data.get('channels', []) or []

    # ══════════════════════════════════════════════════════════════
    # TITRE
    # ══════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, f"Rapport Quotidien - {date_str}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # ══════════════════════════════════════════════════════════════
    # RESUME GLOBAL
    # ══════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_fill_color(45, 45, 45)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, "  Resume Global", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"P&L realise : {daily_pnl:+.2f}$", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Total signaux : {total_signals}  |  Total trades : {trades}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Wins : {wins}  |  Losses : {losses}  |  Winrate : {winrate:.1f}%", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Max Drawdown : {max_dd:.2f}$", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ══════════════════════════════════════════════════════════════
    # TABLEAU 1 : PERFORMANCE PAR TYPE DE SIGNAL
    # ══════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_fill_color(45, 45, 45)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, "  Performance par type de signal", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    sig_headers = [
        ("Signal", 22, "L"),
        ("P&L", 28, "C"),
        ("Nb signaux", 22, "C"),
        ("Win", 16, "C"),
        ("Loss", 16, "C"),
        ("Winrate", 20, "C"),
    ]
    pdf._current_table_headers = (sig_headers, ("Helvetica", "B", 9))
    _table_header(pdf, sig_headers, font_size=9)

    if not signal_types:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 7, "Aucun trade enregistre", new_x="LMARGIN", new_y="NEXT")
    else:
        for st in signal_types:
            st_trades = st.get('trades', 0)
            st_wins = st.get('wins', 0)
            st_losses = st.get('losses', st_trades - st_wins)
            st_pnl = st.get('pnl', 0.0)
            st_wr = st_wins / st_trades * 100 if st_trades > 0 else 0
            pdf.cell(22, 7, _sanitize(st.get('type', '?'), 10), border=1)
            pdf.cell(28, 7, f"{st_pnl:+.2f}$", border=1, align="C")
            pdf.cell(22, 7, str(st_trades), border=1, align="C")
            pdf.cell(16, 7, str(st_wins), border=1, align="C")
            pdf.cell(16, 7, str(st_losses), border=1, align="C")
            pdf.cell(20, 7, f"{st_wr:.1f}%", border=1, align="C")
            pdf.ln()
    pdf._current_table_headers = None
    pdf.ln(6)

    # ══════════════════════════════════════════════════════════════
    # TABLEAU 2 : PERFORMANCE PAR CANAL
    # ══════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_fill_color(45, 45, 45)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, "  Performance par canal", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    ch_headers = [
        ("Canal", 18, "L"),
        ("Nom", 42, "L"),
        ("P&L", 25, "C"),
        ("Nb signaux", 22, "C"),
        ("Win", 16, "C"),
        ("Loss", 16, "C"),
        ("Winrate", 20, "C"),
    ]
    pdf._current_table_headers = (ch_headers, ("Helvetica", "B", 9))
    _table_header(pdf, ch_headers, font_size=9)

    channels_sorted = sorted(channels, key=lambda x: x.get('pnl', 0), reverse=True)
    if not channels_sorted:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 7, "Aucun trade sur les canaux", new_x="LMARGIN", new_y="NEXT")
    else:
        for c in channels_sorted:
            c_trades = c.get('trades', 0)
            c_wins = c.get('wins', 0)
            c_losses = c.get('losses', c_trades - c_wins)
            c_pnl = c.get('pnl', 0.0)
            c_wr = c_wins / c_trades * 100 if c_trades > 0 else 0
            c_name = _sanitize(c.get('name', ''), 20)
            if not c_name or c_name == '?':
                c_name = f"CH{c.get('ch_num', '?')}"
            pdf.cell(18, 7, f"CH{c.get('ch_num', '?')}", border=1)
            pdf.cell(42, 7, c_name, border=1)
            pdf.cell(25, 7, f"{c_pnl:+.2f}$", border=1, align="C")
            pdf.cell(22, 7, str(c_trades), border=1, align="C")
            pdf.cell(16, 7, str(c_wins), border=1, align="C")
            pdf.cell(16, 7, str(c_losses), border=1, align="C")
            pdf.cell(20, 7, f"{c_wr:.1f}%", border=1, align="C")
            pdf.ln()
    pdf._current_table_headers = None
    pdf.ln(5)

    # ── Sauvegarde ──
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"daily_report_{date_str}.pdf")
    try:
        pdf.output(filepath)
        file_size = os.path.getsize(filepath)
        log.info(f"PDF rapport généré: {filepath} ({file_size} octets)")
        return filepath
    except Exception as e:
        log.error(f"Erreur génération PDF: {e}")
        return ""


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

def _open_market_limit(signal: dict, bridge: MT5Bridge, manager,
                       action: str, symbol: str, current: float,
                       sl: float, tp_final: float,
                       lot_market: float,
                       ch_num, canal: str, prefix: str = "ZN") -> bool:
    """Ouvre 1 MARKET + N LIMIT orders pour catcher le pullback.
    Retourne True si au moins le MARKET est ouvert."""
    tickets = []
    orders_desc = []

    # === MARKET ORDER ===
    mt5_comment_mk = f"CH{ch_num}-{prefix}-MK"
    try:
        t = bridge.place_market_order(signal, lot_market, tp=tp_final, sl=sl, comment=mt5_comment_mk)
    except Exception as e:
        log.error(f"  MARKET EXCEPTION: {e}")
        t = None

    if t:
        tickets.append({
            "ticket": t, "lot": lot_market, "role": "market",
            "entry_price": current, "tp_final": tp_final,
            "be_active": False, "be_sl": 0,
        })
        orders_desc.append(f"MK=#{t} @{current}")
        log.debug(f"  ✓ MARKET #{t} @{current} TP={tp_final} SL={sl}")
    else:
        log.error("  ✗ MARKET échoué")
        return False

    # === LIMIT ORDERS ===
    if LIMIT_ENABLED and LIMIT_COUNT > 0:
        # Récupérer sym_info via bridge._sym() (résolution suffixes comme pour MARKET)
        _sym_limit = bridge._sym(symbol)
        _digits = _sym_limit.digits if _sym_limit else 2
        _stops_level = getattr(_sym_limit, 'stops_level', 0) if _sym_limit else 0
        _trade_mode = getattr(_sym_limit, 'trade_mode', '?') if _sym_limit else '?'
        _resolved_symbol = _sym_limit.name if _sym_limit else symbol
        _filling_modes = []
        if _sym_limit:
            _fm = _sym_limit.filling_mode
            if _fm & SYMBOL_FILLING_FOK:
                _filling_modes.append(ORDER_FILLING_FOK)
            if _fm & SYMBOL_FILLING_IOC:
                _filling_modes.append(ORDER_FILLING_IOC)
        _filling_modes.append(ORDER_FILLING_RETURN)

        log.debug(f"  LIMIT sym={symbol}→{_resolved_symbol} digits={_digits} stops_level={_stops_level} trade_mode={_trade_mode} filling_modes={_filling_modes}")

        for i in range(LIMIT_COUNT):
            offset = LIMIT_OFFSET_1 if i == 0 else LIMIT_OFFSET_2
            limit_lot = LOT_LIMIT1 if i == 0 else LOT_LIMIT2
            if action == "BUY":
                limit_price = round(current - offset, _digits)
            else:
                limit_price = round(current + offset, _digits)

            mt5_comment_l = f"CH{ch_num}-{prefix}-L{i+1}"
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
            expiry = datetime.now(timezone.utc) + timedelta(minutes=LIMIT_EXPIRY_MIN)

            # Essayer chaque filling mode en fallback (comme pour MARKET)
            log.debug(f"  LIMIT {i+1}: price={limit_price} current={current} offset={offset} stops_level={_stops_level}")
            for fill_mode in _filling_modes:
                try:
                    result = mt5.order_send({
                        "action": mt5.TRADE_ACTION_PENDING,
                        "symbol": _resolved_symbol,
                        "volume": limit_lot,
                        "type": order_type,
                        "price": limit_price,
                        "sl": round(sl, _digits) if sl else 0,
                        "tp": round(tp_final, _digits) if tp_final else 0,
                        "deviation": SLIPPAGE,
                        "magic": MAGIC_NUMBER,
                        "comment": mt5_comment_l,
                        "type_time": mt5.ORDER_TIME_SPECIFIED,
                        "type_filling": fill_mode,
                        "expiration": int(expiry.timestamp()),
                    })
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        tickets.append({
                            "ticket": result.order, "lot": limit_lot, "role": "limit",
                            "entry_price": limit_price, "tp_final": tp_final,
                            "be_active": False, "be_sl": 0,
                        })
                        orders_desc.append(f"L{i+1}={result.order} @{limit_price}")
                        log.debug(f"  ✓ LIMIT {i+1} #{result.order} @{limit_price} fill={fill_mode}")
                        break  # Succès, pas besoin d'essayer le filling suivant
                    else:
                        _ret = getattr(result, 'retcode', '?')
                        _comment = getattr(result, 'comment', '')
                        log.debug(f"  LIMIT {i+1} fill_mode={fill_mode} retcode={_ret} comment='{_comment}' price={limit_price} symbol={_resolved_symbol}")
                except Exception as e:
                    log.error(f"  LIMIT {i+1} EXCEPTION: {e}")
                    break
            else:
                # Tous les filling modes ont échoué
                log.warning(f"  ✗ LIMIT {i+1} échoué: tous filling modes épuisés | price={limit_price} symbol={symbol} sl={sl} tp={tp_final}")

    if not tickets:
        return False

    # Enregistrer l'entrée
    entry = {
        "signal": signal,
        "tickets": tickets,
        "_open_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "_signal_id": f"{symbol}_{action}_{int(time.time())}",
        "_mt5_comment": f"CH{ch_num}-{prefix}",
        "_market_ticket": tickets[0]["ticket"],  # premier = MARKET
        "_limit_cancelled": False,
    }
    manager.register(entry)

    # Alerte
    orders_str = " | ".join(orders_desc)
    nb_limits = len(tickets) - 1
    _alert_mgmt(f"{'🟢' if action=='BUY' else '🔴'} {action} | CH{ch_num}-{prefix} | {symbol}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"MARKET: @{current} lot={lot_market}\n"
                f"LIMITS: {nb_limits} | {orders_str}\n"
                f"TP: {tp_final} | SL: {sl}")
    if LOG_TRADE_MANAGEMENT:
        _log_mgmt(f"OPEN {action} CH{ch_num}-{prefix} | {orders_str}")
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

    # ★ OVERRIDE : TP basé sur TP_FIXED_GAIN_USD (ignorer le TP du signal)
    # Sera recalculé dynamiquement si des LIMIT se remplissent
    if action == "BUY":
        tp_final = round(avg_entry + TP_FIXED_GAIN_USD, 2)
    else:
        tp_final = round(avg_entry - TP_FIXED_GAIN_USD, 2)
    _log_mgmt(f"TP initial: {tp_final} (entry={avg_entry:.2f} ± {TP_FIXED_GAIN_USD}$)")

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
    # Prix dans la zone [zone_low, zone_high] → MARKET + LIMITS
    # Prix légèrement au-dessus (TOLERANCE_ZN) → LIMITS seules
    # Sinon annulé
    if is_zone_signal and len(all_tps) >= 1:
        if action == "BUY":
            in_zone = zone_low <= current <= zone_high
            above_zone = zone_high < current <= zone_high + TOLERANCE_ZN
        else:
            in_zone = zone_low <= current <= zone_high
            below_zone = zone_low - TOLERANCE_ZN <= current < zone_low

        if not in_zone and not (action == "BUY" and above_zone) and not (action == "SELL" and below_zone):
            log.info(f"CH{ch_num}-ZN | REFUSÉ HORS ZONE")
            return

        prefix = "ZN"
        avg_entry = (zone_low + zone_high) / 2
        sl = _cap_sl(action, avg_entry, sl, MAX_SL_USD)
        # Calculer les prix LIMIT pour le log
        if action == "BUY":
            l1_price = round(current - LIMIT_OFFSET_1, 2)
            l2_price = round(current - LIMIT_OFFSET_2, 2)
        else:
            l1_price = round(current + LIMIT_OFFSET_1, 2)
            l2_price = round(current + LIMIT_OFFSET_2, 2)

        if in_zone:
            log.info(f"CH{ch_num}-{prefix} | ACCEPTE | MK={current} | L1={l1_price} | L2={l2_price}")
            _open_market_limit(signal, bridge, manager, action, symbol, current,
                              sl, tp_final, LOT_MARKET, ch_num, canal, prefix)
        else:
            log.info(f"CH{ch_num}-{prefix} | ACCEPTE | L1={l1_price} | L2={l2_price}")
            signal_limits_only = dict(signal)
            _open_market_limit(signal_limits_only, bridge, manager, action, symbol, current,
                              sl, tp_final, 0, ch_num, canal, prefix)
        return

    # ── Prix unique → converti en zone [entry ± TOLERANCE_PU] ──
    if is_single_price and len(all_tps) >= 1:
        entry_price = zone_mid
        prefix = "PU"

        # Créer zone autour du prix d'entrée
        if action == "BUY":
            zone_low_pu = round(entry_price - TOLERANCE_PU, 2)
            zone_high_pu = round(entry_price + TOLERANCE_PU, 2)
            in_zone = zone_low_pu <= current <= zone_high_pu
            above_zone = zone_high_pu < current <= zone_high_pu + TOLERANCE_ZN
        else:
            zone_low_pu = round(entry_price - TOLERANCE_PU, 2)
            zone_high_pu = round(entry_price + TOLERANCE_PU, 2)
            in_zone = zone_low_pu <= current <= zone_high_pu
            below_zone = zone_low_pu - TOLERANCE_ZN <= current < zone_low_pu

        if not in_zone and not (action == "BUY" and above_zone) and not (action == "SELL" and below_zone):
            log.info(f"CH{ch_num}-PU | REFUSÉ HORS ZONE | prix={current} | entry={entry_price}")
            return

        sl = _cap_sl(action, entry_price, sl, MAX_SL_USD)

        # Calculer les prix LIMIT pour le log
        if action == "BUY":
            l1_price = round(current - LIMIT_OFFSET_1, 2)
            l2_price = round(current - LIMIT_OFFSET_2, 2)
        else:
            l1_price = round(current + LIMIT_OFFSET_1, 2)
            l2_price = round(current + LIMIT_OFFSET_2, 2)

        if in_zone:
            log.info(f"CH{ch_num}-PU | ACCEPTE | MK={current} | L1={l1_price} | L2={l2_price}")
            _open_market_limit(signal, bridge, manager, action, symbol, current,
                              sl, tp_final, LOT_MARKET, ch_num, canal, prefix)
        else:
            log.info(f"CH{ch_num}-PU | ACCEPTE | L1={l1_price} | L2={l2_price}")
            _open_market_limit(signal, bridge, manager, action, symbol, current,
                              sl, tp_final, 0, ch_num, canal, prefix)
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

    # --- MARKET PRICE : zone = [current - TOLERANCE_MP, current] (BUY) / [current, current + TOLERANCE_MP] (SELL)
    if is_market_price and entry_price is None:
        entry_price = current
        sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
        if action == "BUY":
            sl = entry_price - sl_offset
            zone_low_mp = round(entry_price - TOLERANCE_MP, 2)
            zone_high_mp = entry_price
        else:
            sl = entry_price + sl_offset
            zone_low_mp = entry_price
            zone_high_mp = round(entry_price + TOLERANCE_MP, 2)
        signal["sl"] = round(sl, 2)
        signal["tps"] = []  # TP = TP_PAR_DEFAUT
        signal["zone_mid"] = entry_price
        signal["zone_low"] = zone_low_mp
        signal["zone_high"] = zone_high_mp
        _log_mgmt(f"[MARKET PRICE] Résolu: entry={entry_price}, SL={sl}, TP=TP_PAR_DEFAUT({TP_PAR_DEFAUT}$)")

    canal = signal.get("source_channel", "Inconnu")
    clean_canal = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', canal)
    ch_num = CHANNEL_NUM_MAP.get(clean_canal, CHANNEL_NUM_MAP.get(clean_canal.lstrip("-"), "?"))

    # ★ 1 signal par canal : fermer l'ancien si un nouveau arrive
    _close_previous_signal(canal, bridge, manager)

    # TP : signal TP ou TP_FIXED_GAIN_USD
    if signal.get("tps") and len(signal["tps"]) > 0:
        # ★ FIX : ignorer le TP du parser, utiliser TP_FIXED_GAIN_USD comme execute_signal
        # Le TP sera recalculé dynamiquement par _recalculate_tp si LIMIT se remplit
        if action == "BUY":
            default_tp = round(entry_price + TP_FIXED_GAIN_USD, 2)
        else:
            default_tp = round(entry_price - TP_FIXED_GAIN_USD, 2)
        log.debug(f"Quick Alert : TP override = {default_tp} (TP_FIXED_GAIN_USD={TP_FIXED_GAIN_USD})")
    else:
        if action == "BUY":
            default_tp = round(entry_price + TP_PAR_DEFAUT, 2)
        else:
            default_tp = round(entry_price - TP_PAR_DEFAUT, 2)
        log.debug(f"Quick Alert : TP par défaut = {default_tp} ({TP_PAR_DEFAUT}$)")

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
            # Défavorable: prix < entry - tolerance (le prix descend trop contre le SELL)
            is_unfavorable = current < entry_price - QA_PRICE_TOLERANCE
        if is_unfavorable:
            log.info(f"CH{ch_num}-AL | Quick Alert ANNULE | prix={current} | entry={entry_price}")
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

    # ★ DÉTERMINER LE TYPE ET LOGIQUE
    if is_market_price:
        qa_label = "MP"
        # Zone MP : [current - TOLERANCE_MP, current] pour BUY
        if action == "BUY":
            in_zone = zone_low_mp <= current <= zone_high_mp
            above_zone = zone_high_mp < current <= zone_high_mp + TOLERANCE_ZN
        else:
            in_zone = zone_low_mp <= current <= zone_high_mp
            below_zone = zone_low_mp - TOLERANCE_ZN <= current < zone_low_mp
    else:
        qa_label = "QA"
        # Zone QA : [entry - TOLERANCE_PU, entry + TOLERANCE_PU]
        zone_low_qa = round(entry_price - TOLERANCE_PU, 2)
        zone_high_qa = round(entry_price + TOLERANCE_PU, 2)
        if action == "BUY":
            in_zone = zone_low_qa <= current <= zone_high_qa
            above_zone = zone_high_qa < current <= zone_high_qa + TOLERANCE_ZN
        else:
            in_zone = zone_low_qa <= current <= zone_high_qa
            below_zone = zone_low_qa - TOLERANCE_ZN <= current < zone_low_qa

    # Vérifier si le prix est acceptable
    if not in_zone and not (action == "BUY" and above_zone) and not (action == "SELL" and below_zone):
        log.info(f"CH{ch_num}-{qa_label} | REFUSÉ HORS ZONE | prix={current}")
        return

    # Calculer les prix LIMIT pour le log
    if action == "BUY":
        l1_price = round(current - LIMIT_OFFSET_1, 2)
        l2_price = round(current - LIMIT_OFFSET_2, 2)
    else:
        l1_price = round(current + LIMIT_OFFSET_1, 2)
        l2_price = round(current + LIMIT_OFFSET_2, 2)

    if in_zone:
        log.info(f"CH{ch_num}-{qa_label} | ACCEPTE | MK={current} | L1={l1_price} | L2={l2_price}")
    else:
        log.info(f"CH{ch_num}-{qa_label} | ACCEPTE | L1={l1_price} | L2={l2_price}")

    # SL plafonné
    entry_for_sl = entry_price if entry_price else current
    sl = _cap_sl(action, entry_for_sl, sl, MAX_SL_USD)

    # MARKET + LIMITS (ou LIMITS seules si légèrement au-dessus)
    if in_zone:
        ok = _open_market_limit(signal, bridge, manager, action, symbol, current,
                                sl, default_tp, LOT_MARKET, ch_num, canal, qa_label)
    else:
        ok = _open_market_limit(signal, bridge, manager, action, symbol, current,
                                sl, default_tp, 0, ch_num, canal, qa_label)
    if not ok:
        log.error("✗ QUICK MARKET échoué")
        return

    # Récupérer l'entrée créée par _open_market_limit
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
    # ★ FIX (v16) : ch_num_fusion défini AVANT le if/else pour être disponible dans les deux branches
    ch_num_fusion = CHANNEL_NUM_MAP.get(canal, CHANNEL_NUM_MAP.get(canal.lstrip("-"), "?"))

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
        log.info(f"CH{ch_num_fusion}-MP | FUSION REFUSÉ | QA #{qa_ticket} déjà fermé ({close_reason})")
        _alert_mgmt(msg.alert_qa_already_closed(full_signal['action'], full_signal['symbol'], ch_num_fusion, qa_ticket, deal_pnl, close_reason))
    else:
        # QA actif -> mettre a jour SL et TP avec ceux du signal complet
        # ★ SL plafonné aussi lors de la fusion
        qa_entry_price = qa.get("entry_price", 0)
        real_sl = _cap_sl(full_signal["action"], qa_entry_price, real_sl, MAX_SL_USD)
        log.info(f"CH{ch_num_fusion}-MP | FUSION ACCEPTE | SL={real_sl} | TP={tp_final}")
        bridge.modify_sl_tp(qa_ticket, real_sl, tp_final, "[FUSION-SL-TP]")
        for t in entry["tickets"]:
            if t["ticket"] == qa_ticket:
                t["tp_final"]  = tp_final
                t["tp_target"] = tp_final
                t["tp3"]       = tp_final
                break
        entry["signal"]          = full_signal
        entry["_is_quick_alert"] = False
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
    tv_filter = TradingViewFilter()
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

        # =============================================================
        # CHARGEMENT DES CANAUX — ORDRE DE PRIORITÉ :
        #   1. TG_FOLDER (dossiers Telegram) → source principale
        #   2. Channels.txt → persistance de la numérotation
        #   3. TG_CHANNEL_* (.env) → fallback final
        # =============================================================

        # ★ Charger le mapping existant depuis Channels.txt (pour persistance numérotation)
        _existing_ch_mapping = {}  # id_telegram -> (ch_num, nom)
        _max_ch_num = 0
        if os.path.exists(CHANNELS_TXT_FILE):
            try:
                with open(CHANNELS_TXT_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        m = re.match(r'Canal_(\d+)\s*:\s*(.+)', line.strip())
                        if m:
                            ch_num = int(m.group(1))
                            raw = m.group(2)
                            if '#' in raw:
                                main_part, comment = raw.split('#', 1)
                                main_part = main_part.strip()
                                comment = comment.strip()
                            else:
                                main_part, comment = raw.strip(), ''
                            _existing_ch_mapping[main_part] = (ch_num, comment)
                            if ch_num > _max_ch_num:
                                _max_ch_num = ch_num
                log.info(f"Channels.txt chargé : {len(_existing_ch_mapping)} canaux existants (max CH{_max_ch_num})")
            except Exception as e:
                log.warning(f"Erreur lecture Channels.txt pour persistance: {e}")

        if TG_FOLDER:
            # ★ PRIORITÉ 1 : CHARGEMENT DEPUIS LES DOSSIERS TELEGRAM
            folder_names = [f.strip() for f in TG_FOLDER.split(',') if f.strip()]
            log.info(f"Chargement depuis dossiers Telegram: {', '.join(folder_names)}...")
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

                # ★ Résoudre les entités et conserver la numérotation existante
                # Construire un index inverse : id_telegram -> ch_num (depuis Channels.txt)
                _id_to_num = {}  # id_string -> ch_num
                for _key, (_cn, _) in _existing_ch_mapping.items():
                    _id_to_num[_key] = _cn

                _next_num = _max_ch_num + 1
                _new_channels = []  # [(entity, ch_num, title_clean)] pour sauvegarde

                for peer in unique_peers:
                    try:
                        entity = await client.get_entity(peer)
                        if not hasattr(entity, 'title'):
                            continue
                        title_raw = entity.title
                        title_clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', title_raw)
                        title_clean = unicodedata.normalize('NFKC', title_clean)
                        if title_clean.strip() == "":
                            title_clean = str(entity.id)

                        # Construire l'ID Telegram au format -100XXXXXXXXXX
                        tg_id = f"-100{entity.id}"

                        # Chercher un numéro existant (par ID ou par nom)
                        canal_num = None
                        if tg_id in _id_to_num:
                            canal_num = _id_to_num[tg_id]
                        elif str(entity.id) in _id_to_num:
                            canal_num = _id_to_num[str(entity.id)]
                        elif title_clean in _id_to_num:
                            canal_num = _id_to_num[title_clean]

                        if canal_num is None:
                            canal_num = _next_num
                            _next_num += 1

                        chats.append(entity)
                        entity_to_name[entity.id] = title_clean
                        CHANNEL_NUM_MAP[title_clean] = canal_num
                        CHANNEL_NUM_MAP[str(entity.id)] = canal_num
                        CHANNEL_NUM_MAP[tg_id] = canal_num
                        _new_channels.append((tg_id, canal_num, title_clean))
                        log.debug(f"Canal_{canal_num} : {title_clean}")
                    except Exception as e:
                        log.debug(f"Impossible de résoudre un peer: {e}")

                # ★ Sauvegarder dans Channels.txt (persistance)
                try:
                    with open(CHANNELS_TXT_FILE, 'w', encoding='utf-8') as f:
                        f.write("# Canaux Telegram — Numérotation persistante\n")
                        f.write("# Format: Canal_N : -100XXXXXXXXXX # NomDuCanal\n")
                        f.write(f"# Mis à jour automatiquement — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
                        f.write(f"# Sources: {', '.join(found_folders)}\n")
                        f.write("\n")
                        # Trier par numéro de canal
                        _new_channels.sort(key=lambda x: x[1])
                        for tg_id, ch_num, title in _new_channels:
                            f.write(f"Canal_{ch_num} : {tg_id} # {title}\n")
                    log.info(f"Channels.txt sauvegardé : {len(_new_channels)} canaux")
                except Exception as e:
                    log.warning(f"Impossible de sauvegarder Channels.txt: {e}")

                folders_str = " , ".join(found_folders)
                log.info(f"Dossier Trouvé : '{folders_str}'")
                log.info(f"Channels Téléchargés : {len(chats)}")
                if TG_ALERT_CHANNEL:
                    log.info(f"Canal de Rapport : {TG_ALERT_CHANNEL}")

            except Exception as e:
                log.error(f"Erreur lecture dossier Telegram '{TG_FOLDER}': {e}")
                return

        elif os.path.exists(CHANNELS_TXT_FILE):
            # ★ PRIORITÉ 2 : CHARGEMENT DEPUIS Channels.txt (si TG_FOLDER absent)
            log.info(f"Chargement des canaux depuis {CHANNELS_TXT_FILE}")
            try:
                _channels_from_file = []
                with open(CHANNELS_TXT_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        m = re.match(r'Canal_(\d+)\s*:\s*(.+)', line.strip())
                        if m:
                            ch_num = int(m.group(1))
                            value = m.group(2).split('#')[0].strip()
                            _channels_from_file.append((ch_num, value))

                if not _channels_from_file:
                    log.error(f"Aucun canal trouvé dans {CHANNELS_TXT_FILE}")
                    return

                log.info(f"{len(_channels_from_file)} canaux trouvés dans Channels.txt")
                from telethon.tl.types import PeerChannel

                def _is_telegram_id(value: str) -> bool:
                    try:
                        return int(value) < 0
                    except ValueError:
                        return False

                for ch_num, value in _channels_from_file:
                    try:
                        if _is_telegram_id(value):
                            raw_id = int(value)
                            channel_id = int(str(raw_id)[4:]) if str(raw_id).startswith('-100') else abs(raw_id)
                            entity = await client.get_entity(PeerChannel(channel_id))
                        else:
                            entity = await client.get_entity(value)
                        title_raw = getattr(entity, 'title', value)
                        title_clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', title_raw)
                        title_clean = unicodedata.normalize('NFKC', title_clean)
                        chats.append(entity)
                        entity_to_name[entity.id] = title_clean
                        CHANNEL_NUM_MAP[title_clean] = ch_num
                        CHANNEL_NUM_MAP[str(entity.id)] = ch_num
                        log.debug(f"Canal_{ch_num} : {title_clean}")
                    except Exception as e:
                        log.warning(f"Canal_{ch_num} : {value} — introuvable: {e}")

                log.info(f"Canaux chargés : {len(chats)}")
                if TG_ALERT_CHANNEL:
                    log.info(f"Canal de Rapport : {TG_ALERT_CHANNEL}")
            except Exception as e:
                log.error(f"Erreur lecture Channels.txt: {e}")
                return

        else:
            # ★ PRIORITÉ 3 : CHARGEMENT DEPUIS .ENV (TG_CHANNEL_*)
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

            # Sauvegarder dans Channels.txt pour persistance
            if chats:
                try:
                    with open(CHANNELS_TXT_FILE, 'w', encoding='utf-8') as f:
                        f.write("# Canaux Telegram — Numérotation persistante\n")
                        f.write("# Format: Canal_N : -100XXXXXXXXXX # NomDuCanal\n")
                        f.write(f"# Mis à jour automatiquement — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
                        f.write("\n")
                        for ent in chats:
                            name = entity_to_name.get(ent.id, '?')
                            ch = CHANNEL_NUM_MAP.get(name, CHANNEL_NUM_MAP.get(str(ent.id), '?'))
                            tg_id = f"-100{ent.id}"
                            f.write(f"Canal_{ch} : {tg_id} # {name}\n")
                    log.info(f"Channels.txt sauvegardé : {len(chats)} canaux")
                except Exception as e:
                    log.warning(f"Impossible de sauvegarder Channels.txt: {e}")

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

            # ★ Déterminer le mode du signal (utilisé plus tard pour le log)
            _sig_mode = None
            _sig_ch_num = None
            _sig_mt5_comment = None
            _sig_zone_low = None
            _sig_zone_high = None
            if signal_data.signal_type == "TRADE":
                _sig_ch_num = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), "?"))
                if signal_data.is_market_price:
                    _sig_mode = "MP"
                elif signal_data.is_quick_alert:
                    _sig_mode = "AL"
                elif signal_data.is_single_price:
                    _sig_mode = "PU"
                elif signal_data.zone_low is not None and signal_data.zone_high is not None and signal_data.zone_low != signal_data.zone_high:
                    _sig_mode = "ZN"
                    _sig_zone_low = signal_data.zone_low
                    _sig_zone_high = signal_data.zone_high
                else:
                    _sig_mode = "C"
                _sig_mt5_comment = f"CH{_sig_ch_num}-{_sig_mode}"
                # ★ FIX (v16) : ne pas logger ici — le log est déplacé après le check fusion
                # pour éviter d'afficher "PU | SELL | 4348" quand c'est en fait une FUSION

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

                # ★ Annuler les ordres LIMIT en attente pour ce canal
                cancelled_limits = 0
                with manager._lock:
                    for entry in manager.active:
                        entry_ch = entry.get("signal", {}).get("source_channel", "")
                        entry_ch_num = CHANNEL_NUM_MAP.get(entry_ch, CHANNEL_NUM_MAP.get(entry_ch.lstrip("-"), None))
                        if ch_num is not None and entry_ch_num != ch_num:
                            continue
                        if close_symbol and entry.get("signal", {}).get("symbol") != close_symbol:
                            continue
                        if not entry.get("_limit_cancelled"):
                            c = bridge.cancel_pending_limits(entry)
                            if c > 0:
                                cancelled_limits += c
                            entry["_limit_cancelled"] = True
                if cancelled_limits > 0:
                    log.info(f"CLOSE: {cancelled_limits} ordres LIMIT annulés pour CH{ch_num}")

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
                # ★ FIX (v16) : log de réception AVANT les filtres (pour toujours l'afficher)
                if _sig_mt5_comment:
                    if _sig_mode == "ZN" and _sig_zone_low is not None and _sig_zone_high is not None:
                        log.info(msg.log_signal_detected_zone(_sig_mt5_comment, signal_data.direction or "?", _sig_zone_low, _sig_zone_high))
                    else:
                        log.info(msg.log_signal_detected(_sig_mt5_comment, signal_data.direction or "?", signal_data.zone_mid))

                if NEWS_ENABLED and news_mgr.is_blocked():
                    ch_num_news = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), "?"))
                    log.info(msg.log_refuse(ch_num_news, "", msg.MOTIF_PROTECTION_NEWS))
                    return

                blocked, reason = in_blocked_window()
                if blocked:
                    log.info(f"CH{_sig_ch_num}-{_sig_mode} | REFUSÉ HORAIRE")
                    return

                if not manager._check_daily_pnl_limit() or manager._daily_limit_reached:
                    log.info(f"CH{_sig_ch_num}-{_sig_mode} | REFUSÉ LIMITE P&L")
                    return

                # ★ FILTRE TRADINGVIEW — bloquer les signaux opposés au consensus 26 indicateurs
                if tv_filter.enabled and signal_data.direction:
                    allowed, motif = tv_filter.is_allowed(signal_data.direction)
                    if not allowed:
                        ch_num_tf = CHANNEL_NUM_MAP.get(canal_name, CHANNEL_NUM_MAP.get(canal_name.lstrip("-"), "?"))
                        log.info(msg.log_refuse(ch_num_tf, "", msg.MOTIF_TREND_OPPOSE))
                        log.info(f"CH{ch_num_tf}-{_sig_mode} | {motif}")
                        _alert_mgmt(msg.alert_trend_blocked(
                            signal_data.direction, signal_data.pair, ch_num_tf,
                            signal_data.direction, tv_filter._last_consensus,
                            tv_filter._last_buy_count, tv_filter._last_sell_count
                        ))
                        return

                sig_dict = signal_data.to_dict()

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
                                _alert_mgmt(msg.alert_fusion_oot(sig_dict["action"], ch_num, ticket, real_sl, tp_final_fusion))
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
        log.info(f" {tv_filter.get_status()}")
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
