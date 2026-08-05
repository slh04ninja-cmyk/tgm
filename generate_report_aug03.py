#!/usr/bin/env python3
"""Génère le rapport du 03 Août 2026 — standalone (sans MetaTrader5)."""

import os, sys

# ============================================================
# DONNÉES DU 03 AOÛT 2026
# ============================================================
date_str = "2026-08-03"
pnl_realise = 171.57
total_trades = 15
wins = 10
losses = 5
winrate = 66.7
max_drawdown = -46.32
total_signals = 15

methods = [
    {'name': 'P1 TP Fixe',        'trades': 15, 'wins': 9,  'losses': 6,  'pnl': 86.74},
    {'name': 'P2 BE Scale',        'trades': 15, 'wins': 9,  'losses': 6,  'pnl': 86.74},
    {'name': 'P3 Trailing',        'trades': 15, 'wins': 5,  'losses': 10, 'pnl': 37.75},
    {'name': 'P4 Partial (50/50)', 'trades': 30, 'wins': 11, 'losses': 19, 'pnl': 21.08},
]

signal_types = [
    {'type': 'ZN1', 'channels': 7, 'trades': 9,  'wins': 6, 'losses': 3, 'pnl': 62.61,  'avg_win': 17.40, 'avg_loss': -11.44},
    {'type': 'ZN2', 'channels': 3, 'trades': 3,  'wins': 2, 'losses': 1, 'pnl': 28.47,  'avg_win': 19.72, 'avg_loss': -10.97},
    {'type': 'PU1', 'channels': 3, 'trades': 3,  'wins': 2, 'losses': 1, 'pnl': -4.51,   'avg_win': 4.23,  'avg_loss': -12.97},
    {'type': 'PU2', 'channels': 1, 'trades': 1,  'wins': 0, 'losses': 1, 'pnl': -1.14,   'avg_win': 0.00,  'avg_loss': -1.14},
    {'type': 'MP',  'channels': 4, 'trades': 4,  'wins': 3, 'losses': 1, 'pnl': 62.45,  'avg_win': 24.51, 'avg_loss': -11.08},
    {'type': 'AL',  'channels': 2, 'trades': 2,  'wins': 2, 'losses': 0, 'pnl': 13.57,  'avg_win': 6.79,  'avg_loss': 0.00},
]

channels = [
    {'ch_num': 32, 'name': 'Goldstorm🔺',              'trades': 3, 'wins': 2, 'losses': 1, 'pnl': 55.53},
    {'ch_num': 38, 'name': 'ForexLens Inc.',            'trades': 2, 'wins': 2, 'losses': 0, 'pnl': 37.55},
    {'ch_num': 44, 'name': 'Gold Sniper VIP',           'trades': 1, 'wins': 1, 'losses': 0, 'pnl': 25.73},
    {'ch_num': 36, 'name': 'GOLD TRADE OFFICIAL',       'trades': 2, 'wins': 1, 'losses': 1, 'pnl': 22.90},
    {'ch_num': 42, 'name': 'Gold HUNTER',               'trades': 1, 'wins': 1, 'losses': 0, 'pnl': 18.59},
    {'ch_num': 52, 'name': 'Momentum Hunters',          'trades': 1, 'wins': 1, 'losses': 0, 'pnl': 17.75},
    {'ch_num': 40, 'name': 'S♕calp Gold',               'trades': 1, 'wins': 1, 'losses': 0, 'pnl': 8.73},
    {'ch_num': 46, 'name': 'HARI TRADER',               'trades': 1, 'wins': 1, 'losses': 0, 'pnl': 6.35},
    {'ch_num': 2,  'name': '𝐔𝐋𝐓𝐈𝐌𝐀𝐓𝐄 𝐆𝐎𝐋𝐃 𝐅𝐗¹',       'trades': 1, 'wins': 0, 'losses': 1, 'pnl': -1.14},
    {'ch_num': 35, 'name': 'VIP SIGNALS',               'trades': 1, 'wins': 0, 'losses': 1, 'pnl': -7.17},
    {'ch_num': 37, 'name': 'PipsPro Gold',              'trades': 1, 'wins': 0, 'losses': 1, 'pnl': -9.60},
    {'ch_num': 39, 'name': 'ONLY GOLD',                 'trades': 1, 'wins': 0, 'losses': 1, 'pnl': -11.08},
    {'ch_num': 48, 'name': 'GOLD VISION',               'trades': 1, 'wins': 0, 'losses': 1, 'pnl': -12.97},
]

tp_count = 10;  tp_pnl = 243.14
sl_count = 5;   sl_pnl = -71.57

# ============================================================
# 1. RAPPORT TEXTE
# ============================================================
def report_daily_summary(date, pnl, trades, w, l, wr, total_sig=0, dd=0.0):
    return (
        f"📅 PERFORMANCE DU {date}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"P&L réalisé : {pnl:+.2f}$\n"
        f"Total signaux : {total_sig} | Total trades : {trades}\n"
        f"Wins : {w} | Losses : {l} | Winrate : {wr:.1f}%\n"
        f"Max Drawdown : {dd:.2f}$"
    )

def report_daily_by_method(methods):
    lines = ["📊 PAR MÉTHODE", "━━━━━━━━━━━━━━━━━━"]
    lines.append(f"{'Méthode':<18} | {'P&L':>8} | {'Trades':>6} | {'Win':>4} | {'Loss':>5} | {'Winrate':>7}")
    lines.append("-" * 60)
    for m in methods:
        wr = m['wins'] / m['trades'] * 100 if m['trades'] > 0 else 0
        lines.append(f"{m['name']:<18} | {m['pnl']:>+7.2f}$ | {m['trades']:>6} | {m['wins']:>4} | {m['losses']:>5} | {wr:>6.1f}%")
    return '\n'.join(lines)

def report_daily_by_signal_type(signal_types):
    lines = ["📊 PAR TYPE DE SIGNAL", "━━━━━━━━━━━━━━━━━━"]
    lines.append(f"{'Signal':<8} | {'Canaux':>6} | {'P&L':>8} | {'Trades':>6} | {'Win':>4} | {'Loss':>5} | {'WR':>4} | {'Gain':>7} | {'Perte':>7}")
    lines.append("-" * 70)
    for st in signal_types:
        wr = st['wins'] / st['trades'] * 100 if st['trades'] > 0 else 0
        lines.append(f"{st['type']:<8} | {st['channels']:>6} | {st['pnl']:>+7.2f}$ | {st['trades']:>6} | {st['wins']:>4} | {st['losses']:>5} | {wr:>3.0f}% | {st['avg_win']:>+6.1f}$ | {st['avg_loss']:>+6.1f}$")
    return '\n'.join(lines)

def report_daily_by_channel(channels):
    lines = ["📊 PAR CANAL", "━━━━━━━━━━━━━━━━━━"]
    lines.append(f"{'Canal':<28} | {'P&L':>8} | {'Trades':>6} | {'Win':>4} | {'Loss':>5} | {'Winrate':>7}")
    lines.append("-" * 70)
    for c in sorted(channels, key=lambda x: x['pnl'], reverse=True):
        wr = c['wins'] / c['trades'] * 100 if c['trades'] > 0 else 0
        label = f"CH{c['ch_num']} {c['name']}"[:28]
        lines.append(f"{label:<28} | {c['pnl']:>+7.2f}$ | {c['trades']:>6} | {c['wins']:>4} | {c['losses']:>5} | {wr:>6.1f}%")
    return '\n'.join(lines)

def report_daily_closes(tp_c, tp_p, sl_c, sl_p):
    return (
        f"📋 CLÔTURES\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"TP : {tp_c} trades | {tp_p:+.2f}$\n"
        f"SL : {sl_c} trades | {sl_p:+.2f}$"
    )

report = "\n\n".join([
    report_daily_summary(date_str, pnl_realise, total_trades, wins, losses, winrate, total_signals, max_drawdown),
    report_daily_by_method(methods),
    report_daily_by_signal_type(signal_types),
    report_daily_by_channel(channels),
    report_daily_closes(tp_count, tp_pnl, sl_count, sl_pnl),
])

print("=" * 60)
print("RAPPORT TELEGRAM")
print("=" * 60)
print(report)

# ============================================================
# 2. PDF
# ============================================================
from fpdf import FPDF

class DailyReportPDF(FPDF):
    _current_table_headers = None
    def header(self):
        if self.page_no() > 1 and self._current_table_headers:
            headers, font_info = self._current_table_headers
            self.set_font(*font_info)
            for txt, w, align in headers:
                self.cell(w, 6, txt, border=1, align=align)
            self.ln()
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

pdf = DailyReportPDF()
pdf.alias_nb_pages()
pdf.add_page()
pdf.set_auto_page_break(auto=True, margin=20)

# Titre
pdf.set_font("Helvetica", "B", 16)
pdf.cell(0, 12, f"Performance du {date_str}", new_x="LMARGIN", new_y="NEXT", align="C")
pdf.ln(5)

# Résumé
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Resume Global", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "", 10)
pdf.cell(0, 6, f"P&L realise : {pnl_realise:+.2f}$", new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, f"Total signaux : {total_signals} | Total trades : {total_trades}", new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, f"Wins : {wins} | Losses : {losses} | Winrate : {winrate:.1f}%", new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, f"Max Drawdown : {max_drawdown:.2f}$", new_x="LMARGIN", new_y="NEXT")
pdf.ln(5)

# Par méthode
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Performance par methode", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "B", 9)
for txt, w, align in [("Methode", 40, "L"), ("P&L", 25, "C"), ("Trades", 18, "C"),
                      ("Win", 15, "C"), ("Loss", 15, "C"), ("Winrate", 18, "C")]:
    pdf.cell(w, 6, txt, border=1, align=align)
pdf.ln()
pdf.set_font("Helvetica", "", 9)
for m in methods:
    wr = m['wins'] / m['trades'] * 100 if m['trades'] > 0 else 0
    pdf.cell(40, 6, m['name'], border=1)
    pdf.cell(25, 6, f"{m['pnl']:+.2f}$", border=1, align="C")
    pdf.cell(18, 6, str(m['trades']), border=1, align="C")
    pdf.cell(15, 6, str(m['wins']), border=1, align="C")
    pdf.cell(15, 6, str(m['losses']), border=1, align="C")
    pdf.cell(18, 6, f"{wr:.1f}%", border=1, align="C")
    pdf.ln()
pdf.ln(5)

# Par type de signal
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Performance par type de signal", new_x="LMARGIN", new_y="NEXT")
sig_headers = [("Signal", 18, "L"), ("Canaux", 16, "C"), ("P&L", 22, "C"),
               ("Trades", 14, "C"), ("Win", 12, "C"), ("Loss", 12, "C"),
               ("WR", 14, "C"), ("Gain", 18, "C"), ("Perte", 18, "C")]
pdf._current_table_headers = (sig_headers, ("Helvetica", "B", 8))
pdf.set_font("Helvetica", "B", 8)
for txt, w, align in sig_headers:
    pdf.cell(w, 6, txt, border=1, align=align)
pdf.ln()
pdf.set_font("Helvetica", "", 8)
for st in signal_types:
    wr = st['wins'] / st['trades'] * 100 if st['trades'] > 0 else 0
    pdf.cell(18, 6, st['type'], border=1)
    pdf.cell(16, 6, str(st['channels']), border=1, align="C")
    pdf.cell(22, 6, f"{st['pnl']:+.2f}$", border=1, align="C")
    pdf.cell(14, 6, str(st['trades']), border=1, align="C")
    pdf.cell(12, 6, str(st['wins']), border=1, align="C")
    pdf.cell(12, 6, str(st['losses']), border=1, align="C")
    pdf.cell(14, 6, f"{wr:.0f}%", border=1, align="C")
    pdf.cell(18, 6, f"{st['avg_win']:+.1f}$", border=1, align="C")
    pdf.cell(18, 6, f"{st['avg_loss']:+.1f}$", border=1, align="C")
    pdf.ln()
pdf._current_table_headers = None
pdf.ln(5)

# Par canal
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Performance par canal", new_x="LMARGIN", new_y="NEXT")
ch_headers = [("Canal", 18, "L"), ("Nom", 48, "L"), ("P&L", 25, "C"),
              ("Trades", 18, "C"), ("Win", 15, "C"), ("Loss", 15, "C"), ("Winrate", 18, "C")]
pdf._current_table_headers = (ch_headers, ("Helvetica", "B", 9))
pdf.set_font("Helvetica", "B", 9)
for txt, w, align in ch_headers:
    pdf.cell(w, 6, txt, border=1, align=align)
pdf.ln()
pdf.set_font("Helvetica", "", 9)
for c in sorted(channels, key=lambda x: x['pnl'], reverse=True):
    wr = c['wins'] / c['trades'] * 100 if c['trades'] > 0 else 0
    import re as _re
    name = _re.sub(r'[^\x00-\x7F]+', '', c['name']).strip()[:22]
    if not name:
        name = f"CH{c['ch_num']}"
    pdf.cell(18, 6, f"CH{c['ch_num']}", border=1)
    pdf.cell(48, 6, name, border=1)
    pdf.cell(25, 6, f"{c['pnl']:+.2f}$", border=1, align="C")
    pdf.cell(18, 6, str(c['trades']), border=1, align="C")
    pdf.cell(15, 6, str(c['wins']), border=1, align="C")
    pdf.cell(15, 6, str(c['losses']), border=1, align="C")
    pdf.cell(18, 6, f"{wr:.1f}%", border=1, align="C")
    pdf.ln()
pdf._current_table_headers = None
pdf.ln(5)

# Clôtures
pdf.set_font("Helvetica", "B", 12)
pdf.cell(0, 8, "Clotures", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "", 10)
pdf.cell(0, 6, f"TP : {tp_count} trades | {tp_pnl:+.2f}$", new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, f"SL : {sl_count} trades | {sl_pnl:+.2f}$", new_x="LMARGIN", new_y="NEXT")

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"daily_report_{date_str}.pdf")
pdf.output(filepath)
print(f"\nPDF généré: {filepath}")
