"""
=============================================================
 BOT_MESSAGES.PY — Textes des logs et alertes Telegram
 Version v12.0.0 — SINGLE_POSITION_MODE (MARKET only)
=============================================================
Centralise tous les textes affichés (console + Telegram) pour pouvoir
les modifier facilement SANS toucher à la logique de trading.

v12.0.0 :
- Suppression de log_order_placed_dual (jamais appelée en v11+)
- Nettoyage complet des fonctions mortes

Organisation :
  1. Logs et alertes de CLÔTURE (TP / SL / CLOSE)
  2. Logs et alertes de BE (Break-Even) — SL @ entry ± BE_USD + TP @ entry ± TP_FIXED_GAIN_USD
  3. Logs et alertes de signal (ZN1/ZN2, PU1/PU2, AL-MP)
  4. Logs de REJET de signal
  5. Logs du P&L quotidien
  6. Logs et alertes NEWS
  7. Alerte de limite quotidienne
  8. Fusion (SL/TP mis à jour sur QA existant)
=============================================================
"""


# =============================================================
# 1. CLÔTURE DE POSITION (TP / SL / CLOSE)
# =============================================================
def log_close_combined(mt5_comment: str, label: str, idx: int, total: int, ticket: int, pnl: float) -> str:
    return f">>> | {mt5_comment} | {label} | {idx}/{total} #{ticket} | P&L: {pnl:+.2f}"


def log_daily_pnl_final(daily_pnl_now: float) -> str:
    return f"<<< P&L QUOTIDIEN : {daily_pnl_now:.2f} $ >>>"


def alert_close(label: str, action: str, symbol: str, pnl: float, idx: int, total: int,
                 ticket: int, daily_pnl_now: float, canal: str) -> str:
    """Alerte Telegram à la clôture d'un ticket (TP, SL, CLOSE)."""
    if label == "TP":
        emoji = "🎯"
    elif label == "SL":
        emoji = "🛑"
    else:
        emoji = "⚪"
    return (
        f"{emoji} {action} {symbol} | {label}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L: {pnl:+.2f}$\n"
        f"Ticket: #{ticket}\n"
        f"P&L QT: {daily_pnl_now:.2f}$\n"
        f"Canal: {canal}"
    )


# =============================================================
# 2. BREAK-EVEN (BE) — SL @ entry ± BE_USD + TP @ entry ± TP_FIXED_GAIN_USD
# =============================================================
def log_be_combined(mt5_comment: str, nb_pos: int, sl_price: float) -> str:
    return f">>> | {mt5_comment} | BE | SL @{sl_price:.2f}"


def alert_be_activated(action: str, symbol: str, nb_pos: int, sl_price: float, target_gain: float,
                        canal: str, pending_annules: int = 0) -> str:
    """Alerte Telegram à l'activation du BE.
    SL @ entry ± BE_USD + TP @ entry ± TP_FIXED_GAIN_USD. MT5 ferme automatiquement."""
    return (
        f"🔒 {action} {symbol} | BE ACTIVÉ\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"SL → @{sl_price:.2f} (entry)\n"
        f"TP → MT5 ferme auto à +{target_gain:.2f}$\n"
        f"Canal: {canal}"
    )


# =============================================================
# 3. SIGNAUX (ZN1/ZN2, PU1/PU2, AL-MP)
# =============================================================
def log_signal_detected(mt5_comment: str, action: str, entry_price) -> str:
    return f"=== || {mt5_comment} | {action} | {entry_price} || ==="


def log_order_placed(mt5_comment: str, order_type: str, ticket: int, price, sl) -> str:
    return f">>> | {mt5_comment} | {order_type} #{ticket} @{price} | SL: {sl}"


# =============================================================
# 4. REJET DE SIGNAL
# =============================================================
def log_refuse(ch_num, suffix: str, motif: str) -> str:
    return f"CH{ch_num}{suffix} | REFUSÉ | {motif}"


# Motifs standards
MOTIF_AUCUN_TP = "AUCUN TP"
MOTIF_CONFLIT = "CONFLIT DE POSITION"
MOTIF_SYMBOLE_INTROUVABLE = "SYMBOLE INTROUVABLE"
MOTIF_PRIX_INDISPONIBLE = "PRIX INDISPONIBLE"
MOTIF_SL_INVALIDE = "SL INVALIDE"
MOTIF_SPREAD_LARGE = "SPREAD TROP LARGE"
MOTIF_MAX_SIGNAUX = "MAX SIGNAUX ATTEINT"
MOTIF_PRIX_HORS_ZONE = "PRIX HORS ZONE"
MOTIF_PRIX_ATTEINT_TP3 = "PRIX ATTEINT TP3"
MOTIF_ECHEC_PLACEMENT = "ÉCHEC PLACEMENT ORDRE"
MOTIF_PROTECTION_NEWS = "PROTECTION NEWS"


# =============================================================
# 5. P&L QUOTIDIEN
# =============================================================
def log_daily_pnl_periodic(realise: float, flottant: float, total: float) -> str:
    return f"<<<<< INFO >>>>> P&L quotidien : réalisé {realise:.2f}$ | flottant {flottant:.2f}$ | total {total:.2f}$"


def log_balance_startup(balance: float, daily_pnl: float) -> str:
    return f"Balance : {balance:.2f}$ | P&L quotidien : {daily_pnl:.2f}$"


# =============================================================
# 6. NEWS
# =============================================================
def log_news_loaded(count: int) -> str:
    return f"<<<<< INFO >>>>> {count} news HIGH impact chargées"


def log_news_fetch_error(error: str) -> str:
    return f"[NEWS] Erreur fetch: {error}"


def log_news_zero_debug(nb_events_recus: int, sample_keys: list) -> str:
    return f"[NEWS] 0 news filtrées sur {nb_events_recus} events reçus — clés JSON : {sample_keys}"


def log_news_blocking_signals(title: str, minutes_restantes: float) -> str:
    return f"<<<<< INFO >>>>> {title} dans {minutes_restantes:.0f} min → signaux bloqués"


def log_news_closing_positions(title: str, minutes_restantes: float) -> str:
    return f"<<<<< INFO >>>>> {title} dans {minutes_restantes:.0f} min → fermeture positions"


def log_news_resumed(title: str) -> str:
    return f"<<<<< INFO >>>>> {title} terminé → reprise"


# =============================================================
# 7. LIMITE QUOTIDIENNE
# =============================================================
def log_daily_limit_header() -> str:
    return "===== | DAILY-LIMIT | ====="


def log_daily_limit_detail(total: float, nb_positions: int, nb_annules: int) -> str:
    return f"P&L: {total:.2f}$ | {nb_positions} positions fermées | {nb_annules} ordres annulés"


def alert_daily_limit(total: float, limite: float, nb_positions: int, nb_annules: int) -> str:
    return (
        f"🚨 OBJECTIF QUOTIDIEN ATTEINT\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L total: {total:.2f}$ / Limite: {limite}$\n"
        f"Positions fermées : {nb_positions}\n"
        f"⏸️ Trading arrêté pour aujourd'hui"
    )


# =============================================================
# 8. FUSION (SL/TP mis à jour sur QA existant)
# =============================================================
def log_merge_done(action: str, symbol: str) -> str:
    return f">>> | MERGE terminé | {action} {symbol}"
