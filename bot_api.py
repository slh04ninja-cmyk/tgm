"""
=============================================================
 CopyTrading Bot — API REST (FastAPI)
 Version 1.0.0
=============================================================
Expose une API pour l'application Android :
  - Status du bot (running/stopped/error)
  - Dashboard (P&L, positions, winrate, trades)
  - Start/Stop du bot
  - Lecture/écriture de la config (.env)
  - Logs en temps réel (WebSocket)
  - Historique des trades

Lancez avec : python bot_api.py
=============================================================
"""

import subprocess, sys
import importlib

# Auto-install des dépendances
_deps = {"fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "MetaTrader5": "MetaTrader5"}
for _mod, _pkg in _deps.items():
    try:
        __import__(_mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import os
import re
import json
import time
import signal
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import MetaTrader5 as mt5

# =============================================================
# CONFIG
# =============================================================
BOT_SCRIPT = os.getenv("BOT_SCRIPT", "telegram_listener_v17_1.py")
BOT_WORKDIR = os.getenv("BOT_WORKDIR", os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BOT_WORKDIR, ".env")
LOG_FILE = os.path.join(BOT_WORKDIR, "bot_trading.log")
PID_FILE = os.path.join(BOT_WORKDIR, "bot.pid")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_TOKEN = os.getenv("API_TOKEN", "CPG7e5e97dDkHTyMJg8AwIPIcnqeV0gIrPn")  # Token d'authentification (optionnel)

# MT5 connection (reprend les mêmes vars que le bot)
from dotenv import load_dotenv
load_dotenv(ENV_FILE)

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
MAGIC_NUMBER = int(os.getenv("MAGIC_NUMBER", "20250226"))
DAILY_PROFIT_LIMIT = float(os.getenv("DAILY_PROFIT_LIMIT", "200.0"))
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "3"))
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "20"))

def _read_env_dynamic():
    """Relit les variables dynamiques depuis le .env a chaque appel."""
    from dotenv import dotenv_values
    vals = dotenv_values(ENV_FILE)
    return {
        "daily_limit": float(vals.get("DAILY_PROFIT_LIMIT", DAILY_PROFIT_LIMIT)),
        "start_hour": int(vals.get("TRADING_START_HOUR", TRADING_START_HOUR)),
        "end_hour": int(vals.get("TRADING_END_HOUR", TRADING_END_HOUR)),
    }

# =============================================================
# APP
# =============================================================
app = FastAPI(title="CopyTrading Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================
# BOT PROCESS MANAGER
# =============================================================
class BotProcess:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.start_time: Optional[datetime] = None
        self.status: str = "stopped"  # stopped, running, error
        self.last_error: str = ""
        self._lock = threading.Lock()

    def _find_running_bot(self) -> Optional[int]:
        """Cherche un processus bot deja lancé (PID file puis tasklist)."""
        # 1. Vérifier le fichier PID (rapide)
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE, "r") as f:
                    pid = int(f.read().strip())
                # Vérifier si le processus existe toujours
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and "python" in result.stdout.lower():
                    return pid
                # PID file obsolète, le supprimer
                os.remove(PID_FILE)
        except Exception:
            pass

        # 2. Fallback: chercher par nom de script (plus lent)
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.replace('"', '').split(",")
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1].strip())
                        wmic = subprocess.run(
                            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/FORMAT:LIST"],
                            capture_output=True, text=True, timeout=5
                        )
                        if BOT_SCRIPT in wmic.stdout:
                            # Sauvegarder le PID pour les prochaines vérifications
                            with open(PID_FILE, "w") as f:
                                f.write(str(pid))
                            return pid
                    except (ValueError, subprocess.TimeoutExpired):
                        continue
        except Exception:
            pass
        return None

    def start(self) -> dict:
        with self._lock:
            # Verifier si deja en cours (via API ou processus existant)
            if self.process and self.process.poll() is None:
                return {"status": "already_running", "pid": self.pid}
            found = self._find_running_bot()
            if found:
                self.pid = found
                self.status = "running"
                return {"status": "already_running", "pid": found}

            try:
                python_exe = sys.executable
                bot_path = os.path.join(BOT_WORKDIR, BOT_SCRIPT)

                if not os.path.exists(bot_path):
                    return {"status": "error", "message": f"Script introuvable: {bot_path}"}

                log_handle = open(os.path.join(BOT_WORKDIR, "bot_trading.log"), "a", encoding="utf-8")
                self.process = subprocess.Popen(
                    [python_exe, bot_path],
                    cwd=BOT_WORKDIR,
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                self._log_handle = log_handle
                self.pid = self.process.pid
                self.start_time = datetime.now(timezone.utc)
                self.status = "running"
                self.last_error = ""
                # Écrire le fichier PID
                try:
                    with open(PID_FILE, "w") as f:
                        f.write(str(self.pid))
                except Exception:
                    pass
                self._log_action(f"Bot demarré via API — PID {self.pid}")
                return {"status": "started", "pid": self.pid}
            except Exception as e:
                self.status = "error"
                self.last_error = str(e)
                return {"status": "error", "message": str(e)}

    def _log_action(self, message: str):
        """Ecrire une action API dans le log du bot"""
        try:
            from datetime import datetime as dt
            timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{timestamp} [API]  {message}\n"
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def stop(self) -> dict:
        with self._lock:
            # Cas 1: bot lance via l'API
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                except Exception:
                    pass
                self.process = None
                if hasattr(self, '_log_handle') and self._log_handle:
                    self._log_handle.close()
                    self._log_handle = None

            # Cas 2: bot trouve en cours (pas lance via l'API) — tuer par PID
            elif self.pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(self.pid)],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    pass

            # Cas 3: aucun bot en cours
            else:
                found = self._find_running_bot()
                if not found:
                    self.status = "stopped"
                    return {"status": "already_stopped"}
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(found)],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    pass

            self.status = "stopped"
            self.pid = None
            self.process = None
            if hasattr(self, '_log_handle') and self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            try:
                if os.path.exists(PID_FILE):
                    os.remove(PID_FILE)
            except Exception:
                pass
            self._log_action("Bot arrêté via API")
            return {"status": "stopped"}

    def get_status(self) -> dict:
        # 1. Vérifier si on a lancé le bot via l'API
        is_running = self.process is not None and self.process.poll() is None
        
        # 2. Si pas lancé via l'API, chercher un processus existant
        if not is_running:
            found_pid = self._find_running_bot()
            if found_pid:
                is_running = True
                self.pid = found_pid
                self.status = "running"

        if is_running:
            self.status = "running"
        elif self.status == "running":
            self.status = "stopped"

        uptime = None
        if self.start_time and is_running:
            delta = datetime.now(timezone.utc) - self.start_time
            uptime = int(delta.total_seconds())

        return {
            "status": self.status,
            "pid": self.pid if is_running else None,
            "uptime_seconds": uptime,
            "last_error": self.last_error,
        }

bot = BotProcess()

# =============================================================
# MT5 HELPER
# =============================================================
_mt5_connected = False

def _ensure_mt5():
    global _mt5_connected
    if _mt5_connected:
        # Vérifier que la connexion est toujours active
        try:
            info = mt5.account_info()
            if info and info.login > 0:
                return True
            # Connexion perdue, reconnecter
            _mt5_connected = False
        except Exception:
            _mt5_connected = False

    try:
        # Essayer d'initialiser sans login (terminal deja ouvert)
        if not mt5.initialize():
            # Si ça échoue, essayer avec les credentials
            if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
                mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD,
                              server=MT5_SERVER,
                              path=MT5_PATH if os.path.exists(MT5_PATH) else None)
            else:
                return False
        info = mt5.account_info()
        if info and info.login > 0:
            _mt5_connected = True
            return True
        return False
    except Exception:
        return False

def _get_trading_day_start() -> datetime:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=TRADING_START_HOUR, minute=0, second=0, microsecond=0)
    if now.hour < TRADING_START_HOUR:
        start = start - timedelta(days=1)
    return start

# =============================================================
# MODELS
# =============================================================
class EnvUpdate(BaseModel):
    key: str
    value: str

class EnvBulkUpdate(BaseModel):
    values: dict[str, str]

# =============================================================
# ENDPOINTS
# =============================================================

@app.get("/")
def root():
    return {"name": "CopyTrading Bot API", "version": "1.0.0", "status": "online"}

# --- STATUS ---
@app.get("/api/status")
def get_status():
    bot_status = bot.get_status()
    mt5_ok = _ensure_mt5()

    account = None
    if mt5_ok:
        info = mt5.account_info()
        if info:
            account = {
                "login": info.login,
                "server": info.server,
                "balance": round(info.balance, 2),
                "equity": round(info.equity, 2),
                "margin": round(info.margin, 2),
                "free_margin": round(info.margin_free, 2),
                "profit": round(info.profit, 2),
                "currency": info.currency,
                "leverage": info.leverage,
            }

    return {
        "bot": bot_status,
        "mt5": {"connected": mt5_ok, "account": account},
        "server_time": datetime.now(timezone.utc).isoformat(),
    }

# --- START / STOP ---
@app.post("/api/bot/start")
def start_bot():
    return bot.start()

@app.post("/api/bot/stop")
def stop_bot():
    return bot.stop()

# --- DASHBOARD ---
@app.get("/api/dashboard")
def get_dashboard():
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 non connecté")

    # Lire les variables dynamiquement depuis le .env
    env = _read_env_dynamic()
    daily_limit = env["daily_limit"]
    start_hour = env["start_hour"]
    end_hour = env["end_hour"]

    now = datetime.now(timezone.utc)
    start = _get_trading_day_start()

    # Hors plage trading → valeurs à zéro
    in_trading = start_hour <= now.hour < end_hour

    # P&L quotidien (deals)
    daily_pnl = 0.0
    trades_count = 0
    wins = 0
    losses = 0

    if in_trading:
        deals = mt5.history_deals_get(start, now)
        if deals:
            for d in deals:
                if d.entry != mt5.DEAL_ENTRY_OUT:
                    continue
                daily_pnl += d.profit
                trades_count += 1
                if d.profit > 0:
                    wins += 1
                elif d.profit < 0:
                    losses += 1

    # Positions ouvertes
    positions = mt5.positions_get()
    open_positions = []
    floating_pnl = 0.0
    if positions:
        for pos in positions:
            open_positions.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "open_price": round(pos.price_open, 2),
                "current_price": round(pos.price_current, 2),
                "sl": round(pos.sl, 2),
                "tp": round(pos.tp, 2),
                "profit": round(pos.profit, 2),
                "swap": round(pos.swap, 2),
                "comment": pos.comment,
                "magic": pos.magic,
                "bot_opened": pos.magic == MAGIC_NUMBER,
                "time": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
            })
            floating_pnl += pos.profit

    # Winrate
    winrate = (wins / trades_count * 100) if trades_count > 0 else 0.0

    # Account info
    info = mt5.account_info()
    balance = round(info.balance, 2) if info else 0.0

    return {
        "daily_pnl": round(daily_pnl, 2),
        "floating_pnl": round(floating_pnl, 2),
        "total_pnl": round(daily_pnl + floating_pnl, 2),
        "balance": balance,
        "equity": round(info.equity, 2) if info else 0.0,
        "trades": trades_count,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 1),
        "open_positions": open_positions,
        "open_count": len(open_positions),
        "daily_limit": daily_limit,
        "limit_pct": round((daily_pnl + floating_pnl) / daily_limit * 100, 1) if daily_limit > 0 else 0,
        "trading_hours": f"{start_hour}h-{end_hour}h UTC",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# --- POSITIONS ---
@app.get("/api/positions")
def get_positions():
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 non connecté")

    positions = mt5.positions_get()
    result = []
    if positions:
        for pos in positions:
            result.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "open_price": round(pos.price_open, 2),
                "current_price": round(pos.price_current, 2),
                "sl": round(pos.sl, 2),
                "tp": round(pos.tp, 2),
                "profit": round(pos.profit, 2),
                "swap": round(pos.swap, 2),
                "comment": pos.comment,
                "magic": pos.magic,
                "bot_opened": pos.magic == MAGIC_NUMBER,
                "time": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
            })
    return {"positions": result, "count": len(result)}

# --- TRADES HISTORY ---
@app.get("/api/trades")
def get_trades(days: int = 7, from_date: str = None, to_date: str = None):
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 non connecté")

    now = datetime.now(timezone.utc)
    if from_date and to_date:
        # Filtrage par plage de dates
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    else:
        start = now - timedelta(days=days)
        end = now
    deals = mt5.history_deals_get(start, end)

    trades = []
    if deals:
        open_deals = {}
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_IN:
                open_deals[d.position_id] = d

        # Indexer le commentaire du deal IN (ouverture) — c'est la source fiable
        # pour le format CH{num}-{signal}-{method} (ex: CH5-ZN-MK)
        open_comments = {}
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_IN:
                open_deals[d.position_id] = d
                if getattr(d, 'comment', ''):
                    open_comments[d.position_id] = d.comment

        # Fallback: lire le commentaire depuis les ordres d'ouverture
        missing_ids = [pid for pid in open_deals if pid not in open_comments]
        if missing_ids:
            orders = mt5.history_orders_get(start, now)
            if orders:
                order_comment = {o.ticket: o.comment for o in orders if getattr(o, 'comment', '')}
                for pid in missing_ids:
                    c = order_comment.get(pid)
                    if c:
                        open_comments[pid] = c

        for d in deals:
            if d.entry != mt5.DEAL_ENTRY_OUT:
                continue
            open_d = open_deals.get(d.position_id)
            origin_magic = open_d.magic if open_d else d.magic
            if origin_magic != MAGIC_NUMBER:
                continue
            # Utiliser le commentaire du deal IN (CH5-ZN-MK) au lieu du deal OUT ([tp], [sl], etc.)
            comment = open_comments.get(d.position_id, d.comment)
            trades.append({
                "ticket": d.position_id,
                "symbol": d.symbol,
                "type": "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume": d.volume,
                "open_price": round(open_d.price, 2) if open_d else 0,
                "close_price": round(d.price, 2),
                "profit": round(d.profit, 2),
                "commission": round(d.commission, 2),
                "swap": round(d.swap, 2),
                "comment": comment,
                "open_time": datetime.fromtimestamp(open_d.time, tz=timezone.utc).isoformat() if open_d else "",
                "close_time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
            })

    trades.sort(key=lambda x: x["close_time"], reverse=True)
    return {"trades": trades, "count": len(trades), "days": days}

# --- CONFIG (.env) ---
@app.get("/api/config")
def get_config():
    if not os.path.exists(ENV_FILE):
        return {"config": {}, "file": ENV_FILE}

    config = {}
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Masquer les mots de passe
                if any(s in key.upper() for s in ["PASSWORD", "SECRET", "TOKEN", "API_HASH"]):
                    config[key] = "***"
                else:
                    config[key] = value

    return {"config": config, "file": ENV_FILE}

@app.put("/api/config")
def update_config(updates: EnvBulkUpdate):
    if not os.path.exists(ENV_FILE):
        raise HTTPException(status_code=404, detail="Fichier .env introuvable")

    # Lire le fichier actuel
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Construire un dict des valeurs existantes
    existing = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            existing[key.strip()] = value.strip()

    # Appliquer les mises à jour
    for key, value in updates.values.items():
        # Ne pas écraser les passwords avec ***
        if value == "***":
            continue
        # Supprimer les clés vides
        if value == "" and key in existing:
            del existing[key]
            continue
        existing[key] = value

    # Réécrire le fichier
    new_lines = []
    written_keys = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in existing:
                new_lines.append(f"{key}={existing[key]}\n")
                written_keys.add(key)
            # else: clé supprimée → on saute la ligne
        else:
            new_lines.append(line)

    # Ajouter les nouvelles clés
    # Séparer les TG_CHANNEL_X des autres nouvelles clés
    new_channel_keys = []
    new_other_keys = []
    for key, value in existing.items():
        if key not in written_keys:
            if key.startswith("TG_CHANNEL_") and key[11:].isdigit():
                new_channel_keys.append((key, value))
            else:
                new_other_keys.append((key, value))

    # Insérer les nouveaux canaux après le dernier TG_CHANNEL_X existant
    if new_channel_keys:
        last_ch_idx = -1
        for i, line in enumerate(new_lines):
            stripped = line.strip()
            if stripped.startswith("TG_CHANNEL_") and not stripped.startswith("#"):
                last_ch_idx = i
        if last_ch_idx >= 0:
            for j, (key, value) in enumerate(sorted(new_channel_keys)):
                new_lines.insert(last_ch_idx + 1 + j, f"{key}={value}\n")
        else:
            for key, value in sorted(new_channel_keys):
                new_lines.append(f"{key}={value}\n")

    # Ajouter les autres nouvelles clés à la fin
    for key, value in new_other_keys:
        new_lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return {"status": "ok", "updated": list(updates.values.keys())}

class RawConfigUpdate(BaseModel):
    content: str

@app.get("/api/config/raw")
def get_config_raw():
    """Retourne le contenu brut du .env (pour l'éditeur)"""
    if not os.path.exists(ENV_FILE):
        return {"content": ""}
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        return {"content": f.read()}

@app.put("/api/config/raw")
def update_config_raw(req: RawConfigUpdate):
    """Écrase le contenu du .env (éditeur avancé)"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Le contenu ne peut pas être vide")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(req.content)
    return {"status": "ok"}

# --- LOGS ---
@app.get("/api/logs")
def get_logs(lines: int = 100):
    if not os.path.exists(LOG_FILE):
        return {"logs": [], "total_lines": 0}

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

    return {
        "logs": [l.rstrip() for l in tail],
        "total_lines": total,
        "returned": len(tail),
    }

# --- WEBSOCKET (logs live) ---
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    if not os.path.exists(LOG_FILE):
        await websocket.send_text("Log file not found")
        await websocket.close()
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            # Aller à la fin du fichier
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    await websocket.send_text(line.rstrip())
                else:
                    await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

# --- CLOSE POSITION ---
@app.post("/api/positions/{ticket}/close")
def close_position(ticket: int):
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 non connecté")

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise HTTPException(status_code=404, detail="Position introuvable")

    pos = positions[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        raise HTTPException(status_code=503, detail="Prix indisponible")

    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "API-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    })

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {"status": "closed", "ticket": ticket, "profit": round(pos.profit, 2)}
    else:
        retcode = result.retcode if result else "unknown"
        raise HTTPException(status_code=500, detail=f"Échec fermeture: retcode={retcode}")

# --- CLOSE ALL ---
@app.post("/api/positions/close-all")
def close_all_positions():
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 non connecté")

    positions = mt5.positions_get()
    closed = []
    failed = []

    if positions:
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                failed.append(pos.ticket)
                continue
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": MAGIC_NUMBER,
                "comment": "API-close-all",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            })
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                closed.append(pos.ticket)
            else:
                failed.append(pos.ticket)

    return {"closed": closed, "failed": failed, "total": len(closed) + len(failed)}

# =============================================================
# REMOTE MANAGEMENT (file + command execution)
# =============================================================
from fastapi import Body

class CommandRequest(BaseModel):
    command: str
    cwd: Optional[str] = None

class FileWriteRequest(BaseModel):
    path: str
    content: str

@app.post("/api/exec")
def exec_command(req: CommandRequest):
    """Execute a shell command on the server."""
    try:
        result = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=req.cwd or BOT_WORKDIR,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out (30s)", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}

@app.get("/api/file")
def read_file(path: str):
    """Read a file from the server."""
    try:
        # Security: only allow files in BOT_WORKDIR
        abs_path = os.path.abspath(path)
        if not abs_path.startswith(os.path.abspath(BOT_WORKDIR)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found")
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": abs_path, "content": content, "size": len(content)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/file")
def write_file(req: FileWriteRequest):
    """Write a file to the server."""
    try:
        abs_path = os.path.abspath(req.path)
        if not abs_path.startswith(os.path.abspath(BOT_WORKDIR)):
            raise HTTPException(status_code=403, detail="Access denied")
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "ok", "path": abs_path, "size": len(req.content)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files")
def list_files(path: str = ""):
    """List files in a directory."""
    try:
        target = os.path.join(BOT_WORKDIR, path) if path else BOT_WORKDIR
        abs_target = os.path.abspath(target)
        if not abs_target.startswith(os.path.abspath(BOT_WORKDIR)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not os.path.isdir(abs_target):
            raise HTTPException(status_code=404, detail="Directory not found")
        entries = []
        for name in os.listdir(abs_target):
            full = os.path.join(abs_target, name)
            entries.append({
                "name": name,
                "is_dir": os.path.isdir(full),
                "size": os.path.getsize(full) if os.path.isfile(full) else 0,
            })
        return {"path": abs_target, "entries": entries}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================
# MAIN
# =============================================================
@app.post("/api/restart")
def restart_server():
    """Redémarre uvicorn pour recharger bot_api.py."""
    import threading, sys
    def _restart():
        import time; time.sleep(2)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_restart, daemon=True).start()
    return {"status": "restarting"}

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 CopyTrading API démarrée sur {API_HOST}:{API_PORT}")
    print(f"📁 Bot script: {BOT_SCRIPT}")
    print(f"📁 Workdir: {BOT_WORKDIR}")
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="warning")
