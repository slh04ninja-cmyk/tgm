"""
=============================================================
 BOT_MESSAGES.PY — Textes des logs et alertes Telegram
 Version v15.0.0 — Formats d'alertes mis à jour
=============================================================
Centralise tous les textes affichés (console + Telegram) pour pouvoir
les modifier facilement SANS toucher à la logique de trading.

- Formats d'alertes harmonisés: SYMBOL | ACTION | COMMENT
- Prix sans @
- Clôtures: ACTION | COMMENT | LABEL
- BE: ACTION | COMMENT | BE
- Fusion: ACTION | CANAL_NUM | FUSION

Organisation :
  1. Logs et alertes de CLÔTURE (TP / SL / CLOSE)
  2. Logs et alertes de BE (Break-Even)
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
                 ticket: int, daily_pnl_now: float, mt5_comment: str) -> str:
    """Alerte Telegram à la clôture d'un ticket (TP, SL, CLOSE).
    Format: {emoji} {action} | {mt5_comment} | {label}"""
    if label == "TP":
        emoji = "🎯"
    elif label == "SL":
        emoji = "🛑"
    else:
        emoji = "⚪"
    return (
        f"{emoji} {action} | {mt5_comment} | {label}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L: {pnl:+.2f}$\n"
        f"Ticket: {ticket}\n"
        f"P&L QT: {daily_pnl_now:.2f}$\n"
        f"Canal: {mt5_comment.split('-')[0] if '-' in mt5_comment else ''}"
    )


# =============================================================
# 2. BREAK-EVEN (BE)
# =============================================================
# =============================================================
# 3. SIGNAUX (ZN1/ZN2, PU1/PU2, AL-MP)
# =============================================================
def log_signal_detected(mt5_comment: str, action: str, entry_price) -> str:
    return f"==|| {mt5_comment} | {action} | P={entry_price} ||=="


def log_signal_detected_zone(mt5_comment: str, action: str, zone_low: float, zone_high: float) -> str:
    """Log de détection pour les signaux zone."""
    return f"==|| {mt5_comment} | {action} | Z={zone_low}-{zone_high} ||=="


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
MOTIF_TREND_OPPOSE = "TREND OPPOSÉ"


def alert_qa_cancelled(action: str, symbol: str, ch_num, current: float,
                        entry_price: float, tolerance: float) -> str:
    """Alerte Telegram quand une QA est annulée (prix défavorable).
    Format: {emoji} {action} | {ch_num} | QA ANNULÉE"""
    return (
        f"❌ {action} | CH{ch_num} | QA ANNULÉE\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Prix: {current:.2f} | Entry: {entry_price:.2f}\n"
        f"Prix défavorable (>{tolerance}$)\n"
        f"Canal: CH{ch_num}"
    )


def alert_qa_already_closed(action: str, symbol: str, ch_num, qa_ticket: int,
                              deal_pnl: float, close_reason: str) -> str:
    """Alerte Telegram quand un QA est déjà fermé.
    Format: {emoji} {action} | {ch_num} | QA déjà {reason}"""
    emoji = "❌" if close_reason == "SL" else "✅"
    return (
        f"{emoji} {action} | CH{ch_num} | QA déjà {close_reason}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"QA : #{qa_ticket}\n"
        f"P&L : {deal_pnl:+.2f} $\n"
        f"FUSION IGNORÉ\n"
        f"Canal: CH{ch_num}"
    )


def alert_fusion(action: str, ch_num, qa_ticket: int, new_sl: float, new_tp: float) -> str:
    """Alerte Telegram quand un QA est fusionné avec un signal complet.
    Format: {emoji} {action} | {ch_num} | FUSION"""
    return (
        f"✅ {action} | CH{ch_num} | FUSION\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"QA : #{qa_ticket}\n"
        f"SL : {new_sl}\n"
        f"TP : {new_tp}\n"
        f"Canal: CH{ch_num}"
    )


def alert_trend_blocked(action: str, symbol: str, ch_num, signal_dir: str, consensus: str, buy_count: int, sell_count: int) -> str:
    """Alerte Telegram quand un signal est bloqué par le filtre TradingView."""
    emoji_map = {"STRONG_BUY": "🟢", "BUY": "🟢", "NEUTRAL": "⚪", "SELL": "🔴", "STRONG_SELL": "🔴"}
    emoji = emoji_map.get(consensus, "⚪")
    return (
        f"🚫 {action} | CH{ch_num} | TRADINGVIEW OPPOSÉ\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Signal: {signal_dir} | Consensus: {emoji} {consensus}\n"
        f"Votes: {buy_count} BUY / {sell_count} SELL\n"
        f"Canal: CH{ch_num}"
    )


def alert_fusion_oot(action: str, ch_num, qa_ticket: int, new_sl: float, new_tp: float) -> str:
    """Alerte Telegram quand un QA est fusionné (hors tolérance).
    Format: {emoji} {action} | {ch_num} | FUSION (hors tolérance)"""
    return (
        f"✏️ {action} | CH{ch_num} | FUSION (hors tolérance)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"QA : #{qa_ticket}\n"
        f"SL : {new_sl}\n"
        f"TP : {new_tp}\n"
        f"Canal: CH{ch_num}"
    )


# =============================================================
# 5. P&L QUOTIDIEN
# =============================================================
def log_daily_pnl_periodic(realise: float, flottant: float, total: float) -> str:
    return f" P&L quotidien : réalisé {realise:.2f}$ | flottant {flottant:.2f}$ | total {total:.2f}$"


def log_balance_startup(balance: float, daily_pnl: float) -> str:
    return f"Balance : {balance:.2f}$ | P&L quotidien : {daily_pnl:.2f}$"


# =============================================================
# 6. NEWS
# =============================================================
def log_news_fetch_error(error: str) -> str:
    return f"[NEWS] Erreur fetch: {error}"


def log_news_zero_debug(nb_events_recus: int, sample_keys: list) -> str:
    return f"[NEWS] 0 news filtrées sur {nb_events_recus} events reçus "


def log_news_blocking_signals(title: str, minutes_restantes: float) -> str:
    return f" {title} dans {minutes_restantes:.0f} min → signaux bloqués"


def log_news_closing_positions(title: str, minutes_restantes: float) -> str:
    return f" {title} dans {minutes_restantes:.0f} min → fermeture positions"


def log_news_resumed(title: str) -> str:
    return f" {title} terminé → reprise de trading"


# =============================================================
# 7. LIMITE QUOTIDIENNE
# =============================================================
def log_daily_limit_header() -> str:
    return "=== | DAILY LIMIT ATTEINT| ==="


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


# =============================================================
# 9. MULTI-POSITIONS (A/B Testing — P1 à P4b)
# =============================================================

def log_sl_cap(signal_sl: float, capped: float, distance: float, max_sl: float) -> str:
    return f"[SL CAP] SL plafonné: {signal_sl} → {capped} (distance {distance:.2f}$ > {max_sl}$)"


# =============================================================
# 10. RAPPORT JOURNALIER
# =============================================================
def report_daily_full(date: str, pnl_realise: float, trades: int, wins: int, losses: int,
                      winrate: float, methods: list, channels: list,
                      tp_count: int, tp_pnl: float, sl_count: int, sl_pnl: float,
                      total_signals: int = 0, max_drawdown: float = 0.0,
                      signal_types: list = None) -> str:
    parts = [
        report_daily_summary(date, pnl_realise, trades, wins, losses, winrate, total_signals, max_drawdown),
        "",
        report_daily_by_method(methods),
    ]
    if signal_types:
        parts.extend(["", report_daily_by_signal_type(signal_types)])
    parts.extend([
        "",
        report_daily_by_channel(channels),
        "",
        report_daily_closes(tp_count, tp_pnl, sl_count, sl_pnl),
    ])
    return '\n'.join(parts)