# -*- coding: utf-8 -*-
"""Lance le bot 1 (TradingBot) en détaché — chemin complet du script pour PID distinct."""
import subprocess, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BOT_DIR = r"C:\TradingBot"
SCRIPT = os.path.join(BOT_DIR, "telegram_listener_v17_1.py")
flags = 0x00000200 | 0x00000008
try:
    try:
        os.remove(os.path.join(BOT_DIR, "bot.pid"))
    except Exception:
        pass
    p = subprocess.Popen(
        [sys.executable, SCRIPT],
        cwd=BOT_DIR, creationflags=flags, close_fds=True,
        stdout=open(os.path.join(BOT_DIR, "stdout.log"), "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL)
    print(f"Bot1 lance PID={p.pid} script={SCRIPT}")
except Exception as e:
    print(f"ERREUR: {e}")
