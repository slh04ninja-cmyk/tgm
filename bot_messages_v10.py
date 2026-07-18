"""
=============================================================
 BOT_MESSAGES.PY — Textes des logs et alertes Telegram
Version v10.0.0
=============================================================
Centralise tous les textes affichés (console + Telegram) pour pouvoir
les modifier facilement SANS toucher à la logique de trading dans
telegram_listener_v9.py.

Chaque message est une fonction qui prend les variables nécessaires et
retourne le texte final. Pour changer un texte : modifier UNIQUEMENT
le f-string à l'intérieur de la fonction correspondante, ne rien
changer aux noms de fonction ni aux paramètres (sinon il faut aussi
mettre à jour l'appel dans telegram_listener_v9.py).

Organisation :
  1. Logs et alertes de CLÔTURE (TP / SL / CLOSE générique)
  2. Logs et alertes de BE (Break-Even)
  3. Logs et alertes TP-FIXED (gain fixe par position)
  4. Logs de REJET de signal (REFUSÉ | motif)
  5. Logs du P&L quotidien
  6. Logs et alertes NEWS (filtre high-impact)
  7. Alerte de limite quotidienne atteinte
=============================================================
"""


# =============================================================
# 1. CLÔTURE DE POSITION (TP / SL / CLOSE générique)
# =============================================================
def log_close_header(mt5_comment: str, label: str) -> str:
    """Ligne d'en-tête au moment de la clôture d'un ticket.
    label = 'TP', 'SL', ou 'CLOSE' (générique / fermeture manuelle)."""
    return f"===== | {mt5_comment} | {label} | ====="


def log_close_detail(action: str, symbol: str, pnl: float, idx: int, total: int, ticket: int) -> str:
    """Ligne de détail : direction, P&L du ticket, position dans le groupe (idx/total)."""
    return f"{action} {symbol} | P&L: {pnl:+.2f}$ | {idx}/{total} #{ticket}"


def log_daily_pnl_after_close(daily_pnl_now: float) -> str:
    """Ligne affichée juste après chaque clôture, indiquant le P&L quotidien à jour."""
    return f">>>>> P&L QUOTIDIEN : {daily_pnl_now:.2f} $"


def alert_close(label: str, action: str, symbol: str, pnl: float, idx: int, total: int,
                 ticket: int, daily_pnl_now: float, canal: str) -> str:
    """Alerte Telegram envoyée à la clôture d'un ticket (TP, SL, ou CLOSE générique)."""
    emoji = "🎯" if label == "TP" else "🛑" if label == "SL" else "⚪"
    return (
        f"{emoji} {action} {symbol} | {label}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L: {pnl:+.2f}$\n"
        f"Ticket {idx}/{total}: #{ticket}\n"
        f"P&L QT : {daily_pnl_now:.2f}$\n"
        f"Canal: {canal}"
    )


# =============================================================
# 2. BREAK-EVEN (BE)
# =============================================================
def log_be_header(mt5_comment: str) -> str:
    return f"===== | {mt5_comment} | BE | ====="


def log_be_detail_median(action: str, symbol: str, sl_price: float, nb_pos: int) -> str:
    """BE appliqué avec SL au prix médian (2 positions ou plus)."""
    return f"{action} {symbol} | SL @{sl_price} | {nb_pos} POS"


def log_be_activation_detail(action: str, symbol: str, sl_price: float, nb_pos: int,
                              pending_annules: int = 0) -> str:
    """Ligne de log au moment de l'activation du BE (chemin principal)."""
    pos_info = f"{nb_pos} POS"
    if pending_annules > 0:
        pos_info += f" | {pending_annules} PENDING annulés"
    return f"{action} {symbol} | SL @{sl_price:.2f} | {pos_info}"


def alert_be_activated(action: str, symbol: str, nb_pos: int, sl_price: float, target_gain: float,
                        canal: str, pending_annules: int = 0) -> str:
    """Alerte Telegram principale à l'activation du BE."""
    lines = [
        f"🔒 {action} {symbol} | BE ACTIVE",
        "━━━━━━━━━━━━━━━━━━",
        f"NB POS : {nb_pos} positions",
        f"SL : {sl_price:.2f}",
        f"Gain cible (close manuel) : {target_gain:.2f}$",
    ]
    if pending_annules > 0:
        ordre_txt = "ordre" if pending_annules == 1 else "ordres"
        lines.append(f"PENDING annulés : {pending_annules} {ordre_txt}")
    lines.append(f"Canal: {canal}")
    return "\n".join(lines)


def log_be_detail_late(action: str, symbol: str, sl_price: float) -> str:
    """BE appliqué en retard à une position remplie après coup (limit tardif, cas multi-position)."""
    return f"{action} {symbol} | SL @{sl_price} | LATE LIMIT PROTÉGÉ"


def log_be_detail_late_single(action: str, symbol: str, sl_price: float) -> str:
    """BE appliqué en retard, cas rare où c'est le seul ticket non protégé."""
    return f"{action} {symbol} | SL @{sl_price} | LATE PROTÉGÉ"


def alert_be_late(action: str, symbol: str, sl_price: float, nb_pos: int, target_gain_now: float,
                   target_gain_before: float, canal: str) -> str:
    """Alerte Telegram : une position limite s'est remplie APRÈS que le BE initial
    ait déjà été activé sur une autre jambe — le SL est étendu à la nouvelle position."""
    return (
        f"🔒 {action} {symbol} | BE LATE\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Limit rempli après BE → SL @{sl_price}\n"
        f"Positions: {nb_pos}\n"
        f"Objectif: {target_gain_now:.2f}$ (était {target_gain_before:.2f}$)\n"
        f"Canal: {canal}"
    )


# =============================================================
# 3. TP-FIXED (gain fixe par position, TP_FIXED_GAIN_USD)
# =============================================================
def log_tp_fixed_header(mt5_comment: str) -> str:
    return f"===== | {mt5_comment} | TP-FIXED | ====="


def log_tp_fixed_estimate(action: str, symbol: str, total_pnl_estime: float, nb_pos: int) -> str:
    """P&L flottant (snapshot avant clôture) au moment du déclenchement."""
    return f"{action} {symbol} | P&L estimé: {total_pnl_estime:+.2f}$ | {nb_pos} POS"


def log_tp_fixed_tickets(ticket_list: str) -> str:
    return ticket_list


def log_tp_fixed_real_vs_estime(action: str, symbol: str, total_reel: float, total_estime: float) -> str:
    """N'apparaît que si le P&L réel diffère de l'estimation (slippage/spike pendant la clôture)."""
    return f"{action} {symbol} | P&L réel: {total_reel:+.2f}$ (estimé {total_estime:+.2f}$)"


def alert_tp_fixed(action: str, symbol: str, total_reel: float, nb_pos: int, ticket_list: str,
                    daily_pnl_now: float, canal: str) -> str:
    return (
        f"🎯 {action} {symbol} | TP FIXED\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L total: +{total_reel:.2f}$\n"
        f"Positions: {nb_pos}\n"
        f"Ticket(s): {ticket_list}\n"
        f"P&L QT : {daily_pnl_now:.2f}$\n"
        f"Canal: {canal}"
    )


# =============================================================
# 4. REJET DE SIGNAL (format uniforme : CHx[-C/-PU] | REFUSÉ | MOTIF)
# =============================================================
def log_refuse(ch_num, suffix: str, motif: str) -> str:
    """suffix = '' (rejet précoce, avant de savoir si CAS ou PU), '-C', ou '-PU'.
    motif = texte du motif, ex: 'SL INVALIDE', 'PRIX HORS ZONE', 'PROTECTION NEWS'..."""
    return f"CH{ch_num}{suffix} | REFUSÉ | {motif}"


# Motifs standards (pour garder une orthographe cohérente partout où ils sont utilisés)
MOTIF_AUCUN_TP = "AUCUN TP TROUVÉ"
MOTIF_CONFLIT = "CONFLIT DE POSITION"
MOTIF_SYMBOLE_INTROUVABLE = "SYMBOLE INTROUVABLE"
MOTIF_PRIX_INDISPONIBLE = "PRIX INDISPONIBLE"
MOTIF_SL_INVALIDE = "SL INVALIDE"
MOTIF_SPREAD_LARGE = "SPREAD TROP LARGE"          # complété avec ({spread_pips:.0f} pts)
MOTIF_MAX_SIGNAUX = "MAX SIGNAUX ATTEINT"          # complété avec ({total}/{max})
MOTIF_PRIX_HORS_ZONE = "PRIX HORS ZONE"
MOTIF_PRIX_ATTEINT_TP3 = "PRIX ATTEINT TP3"
MOTIF_ECHEC_PLACEMENT = "ÉCHEC PLACEMENT ORDRE"
MOTIF_PROTECTION_NEWS = "PROTECTION NEWS"


# =============================================================
# 5. P&L QUOTIDIEN (log périodique réalisé/flottant/total)
# =============================================================
def log_daily_pnl_periodic(realise: float, flottant: float, total: float) -> str:
    return f"<<<<< INFO >>>>> P&L quotidien : réalisé {realise:.2f}$ | flottant {flottant:.2f}$ | total {total:.2f}$"


def log_daily_pnl_recovered(daily_pnl: float) -> str:
    """Affiché au démarrage, juste après récupération de l'historique MT5 du jour."""
    return f"P&L quotidien récupéré : {daily_pnl:.2f}$"


def log_balance_startup(balance: float, daily_pnl: float) -> str:
    return f"Balance : {balance:.2f}$ | P&L quotidien : {daily_pnl:.2f}$"


# =============================================================
# 6. NEWS (filtre high-impact)
# =============================================================
def log_news_loaded(count: int) -> str:
    return f"<<<<< INFO >>>>> {count} news HIGH impact chargées"


def log_news_fetch_error(error: str) -> str:
    return f"[NEWS] Erreur fetch: {error}"


def log_news_zero_debug(nb_events_recus: int, sample_keys: list) -> str:
    """Avertissement si le fetch réussit mais 0 news filtrées — aide à détecter un
    changement de schéma JSON de l'API (ex: champ 'country' renommé)."""
    return f"[NEWS] 0 news filtrées sur {nb_events_recus} events reçus — clés JSON disponibles : {sample_keys}"


def log_news_blocking_signals(title: str, minutes_restantes: float) -> str:
    return f"<<<<< INFO >>>>> {title} dans {minutes_restantes:.0f} min → signaux bloqués"


def log_news_closing_positions(title: str, minutes_restantes: float) -> str:
    return f"<<<<< INFO >>>>> {title} dans {minutes_restantes:.0f} min → fermeture positions"


def log_news_resumed(title: str) -> str:
    return f"<<<<< INFO >>>>> {title} terminé → reprise"


# =============================================================
# 7. DÉTECTION / EXÉCUTION DE SIGNAL (format condensé)
# =============================================================
def log_signal_detected(mt5_comment: str, action: str, entry_price) -> str:
    """Ligne unique à la réception d'un signal, avant résolution du scénario (S1/S2/CAS1/CAS2)."""
    return f"=== || {mt5_comment} | {action} | {entry_price} || ==="


def log_order_placed(mt5_comment: str, order_type: str, ticket: int, price, sl) -> str:
    """order_type = 'LIMIT', 'MKT', etc. Une seule ligne pour la confirmation de placement."""
    return f">>> | {mt5_comment} | {order_type} #{ticket} @{price} | SL: {sl}"


def log_order_placed_dual(mt5_comment: str, type1: str, ticket1: int, price1,
                           type2: str, ticket2: int, price2, sl) -> str:
    """Variante pour les signaux à deux ordres (CAS1/CAS2 : MKT + LIMIT)."""
    return f">>> | {mt5_comment} | {type1} #{ticket1} @{price1} | {type2} #{ticket2} @{price2} | SL: {sl}"


def log_order_filled(mt5_comment: str, order_type: str, ticket: int) -> str:
    """order_type = 'LMT', 'MKT'... Confirmation de remplissage, une seule ligne."""
    return f">>> | {mt5_comment} | {order_type} REMPLI | #{ticket}"


def log_be_combined(mt5_comment: str, nb_pos: int, sl_price: float) -> str:
    return f">>> | {mt5_comment} | BE {nb_pos} POS | SL @{sl_price:.2f}"


def log_close_combined(mt5_comment: str, label: str, idx: int, total: int, ticket: int, pnl: float) -> str:
    """label = 'TP', 'SL', 'CLOSE', 'TP-FIXED'..."""
    return f">>> | {mt5_comment} | {label} | {idx}/{total} #{ticket} | P&L: {pnl:+.2f}"


def log_daily_pnl_final(daily_pnl_now: float) -> str:
    return f"<<< P&L QUOTIDIEN : {daily_pnl_now:.2f} $ >>>"


def log_tp_trigger(mt5_comment: str, prices_str: str, pending_count: int) -> str:
    return f">>> | {mt5_comment} | TP_TRIGGER | {prices_str} | {pending_count} annulés"


def log_expiration(mt5_comment: str, price, pending_count: int) -> str:
    return f">>> | {mt5_comment} | EXPIRATION | @{price} | {pending_count} annulés"


def log_annule(mt5_comment: str, reason: str) -> str:
    """ANNULÉ (ex: Prix Unique scénario 3, prix hors zone)."""
    return f">>> | {mt5_comment} | ANNULÉ | {reason}"


def log_merge(qa_ticket: int, motif: str) -> str:
    """Log lors de la fusion Quick Alert → signal complet (ou annulation de la fusion)."""
    return f">>> | MERGE #{qa_ticket} | {motif}"


def log_merge_limit_placed(ch_num, action: str, symbol: str, ticket: int, price, sl) -> str:
    return f">>> | CH{ch_num}-MG | LIMIT #{ticket} @{price} | SL: {sl}"


def log_merge_limit_failed(ch_num, price) -> str:
    return f">>> | CH{ch_num}-MG | LIMIT ÉCHOUÉ @{price}"


def log_merge_done(action: str, symbol: str) -> str:
    return f">>> | MERGE terminé | {action} {symbol}"
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
        f"Ordres annulés : {nb_annules}\n"
        f"⏸️ Trading arrêté pour aujourd'hui"
    )
