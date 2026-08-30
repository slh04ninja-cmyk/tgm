"""
Signal Parser V14 — Améliorations de parsing

Modifications v14.0.0 (depuis v13) :

■ FIX : Fallback XAUUSD par défaut
- Si aucun symbole (GOLD, XAUUSD...) n'est détecté, le signal est parsé avec XAUUSD
- Permet de parser les signaux des canaux Gold qui n'écrivent pas le symbole

■ FIX : Détection des signaux à double zones
- Si le texte contient plusieurs paires de nombres sur la même ligne
  (hors TPs et SL), le signal est annulé comme ambigu
- Ex: "Gold 4088 to 4063 ... SELL 4087 4088" → annulé

■ FIX : Zone détectée après BUY/SELL (règle 10 & 11)
- BUY NOW 4079 - 4080 → zone [4079, 4080] (pas prix unique)
- BUY 4030 4025 → zone [4025, 4030] (2 nombres consécutifs)
- NOW ignoré entre BUY/SELL et les prix

■ FIX : @ dans les TPs ignoré
- TP1 @ 4076 n'est plus matché comme entrée

■ FIX : Décimaux préservés
- 4074.00 reste 4074.00 (pas 407400)
- Points parasites (4025...) supprimés sans casser les décimaux

■ FIX : Séparateurs de zone dans la détection double zone
- "to", "and", "between" nettoyés avant détection par ligne
"""

import re
import unicodedata
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime

log = logging.getLogger(__name__)

# =============================================================
# DATACLASSES
# =============================================================
@dataclass
class TradeSignal:
    signal_type: str
    direction: Optional[str] = None
    entry: Optional[float] = None
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    tps: List[float] = field(default_factory=list)
    sl: Optional[float] = None
    pair: str = "XAUUSD"
    raw_text: str = ""
    timestamp: Optional[datetime] = None
    confidence: float = 0.0
    close_all: bool = False
    close_symbol: Optional[str] = None
    new_sl: Optional[float] = None
    is_single_price: bool = False
    is_quick_alert: bool = False
    is_market_price: bool = False
    merge_price: Optional[float] = None
    format_profile: Optional['FormatProfile'] = None
    source_channel: Optional[str] = None   # ✅ BUG #1 corrigé

    def to_dict(self) -> dict:
        return {
            "type": self.signal_type,
            "action": self.direction,
            "symbol": self.pair,
            "zone_low": self.zone_low,
            "zone_mid": self.zone_mid,
            "zone_high": self.zone_high,
            "tps": self.tps,
            "tp1": self.tp1,
            "tp_final": self.tp_final,
            "sl": self.sl,
            "source_channel": self.source_channel,
            "new_sl": self.new_sl,
            "close_all": self.close_all,
            "close_symbol": self.close_symbol,
            "is_single_price": self.is_single_price,
            "is_quick_alert": self.is_quick_alert,
            "is_market_price": self.is_market_price,
            "merge_price": self.merge_price,
        }

    @property
    def tp1(self) -> Optional[float]:
        return self.tps[0] if len(self.tps) >= 1 else None

    @property
    def tp_final(self) -> Optional[float]:
        return self.tps[-1] if self.tps else None

    @property
    def zone_mid(self) -> Optional[float]:
        if self.zone_low is not None and self.zone_high is not None:
            return round((self.zone_low + self.zone_high) / 2, 2)
        return self.entry


@dataclass
class FormatProfile:
    channel_id: Optional[int] = None
    channel_name: str = ""
    direction_style: str = "text"
    direction_keywords: List[str] = field(default_factory=lambda: ["BUY", "SELL"])
    entry_style: str = "labeled"
    entry_keywords: List[str] = field(default_factory=lambda: ["ENTRY", "OPEN", "@"])
    tp_style: str = "numbered"
    tp_labels: List[str] = field(default_factory=lambda: ["TP"])
    has_superscripts: bool = False
    avg_tp_count: float = 1.0
    sl_style: str = "standard"
    sl_labels: List[str] = field(default_factory=lambda: ["SL"])
    pair: str = "XAUUSD"
    pair_keywords: List[str] = field(default_factory=lambda: ["XAUUSD", "GOLD", "XAU"])
    signal_density: float = 0.0
    confidence: float = 0.0
    sample_size: int = 0
    noise_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "direction_style": self.direction_style,
            "entry_style": self.entry_style,
            "tp_style": self.tp_style,
            "sl_style": self.sl_style,
            "pair": self.pair,
            "signal_density": round(self.signal_density, 2),
            "confidence": round(self.confidence, 2),
            "sample_size": self.sample_size,
            "avg_tp_count": round(self.avg_tp_count, 1),
            "has_superscripts": self.has_superscripts,
        }

    def get_parsing_hints(self) -> dict:
        return {}


# =============================================================
# CONSTANTES
# =============================================================
SYMBOL_MAP = {
    "GOLD": "XAUUSD", "XAU/USD": "XAUUSD", "XAUUSD": "XAUUSD",
    "XXAU/USD": "XAUUSD", "XXAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAG/USD": "XAGUSD", "XAGUSD": "XAGUSD",
    "OIL": "USOIL", "USOIL": "USOIL",
    "BTC": "BTCUSD", "BTC/USD": "BTCUSD", "BITCOIN": "BTCUSD", "BTCUSD": "BTCUSD",
    "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
    "GBPUSD": "GBPUSD", "GBP/USD": "GBPUSD",
}

RE_SYMBOL = re.compile(
    r"(XAU/?USD|GOLD|XAG/?USD|SILVER|USOIL|OIL|BTC/?USD|BITCOIN|BTCUSD|EUR/?USD|GBP/?USD)",
    re.IGNORECASE,
)

RE_NUM = r"(\d{4,6}(?:\.\d+)?)"
QUICK_ALERT_SL_OFFSET = 10.0

# =============================================================
# SPAM FILTER
# =============================================================
EXCLUDE_KEYWORDS = [
    "hit", "pips", "tp hit", "tp1 hit", "tp2 hit", "tp3 hit", "all tp hit",
    "mission acomplished", "boom boom boom", "my signal are on fire",
    "closed at", "exit at", "sl hit", "stopped", "secured", "hit target",
    "be safe", "good luck", "market update", "analysis", "are you in big loss",
    "contact", "good morning", "good night", "hello", "welcome", "thank",
    "recap", "result", "motivation", "join", "vip", "subscribe",
    "will", "analysis only", "not signal",
]
SPAM_STANDALONE = ["target", "running"]

# ★ FIX (v15.2) : mots FORTS — marqueurs de RÉSULTATS/recaps. Ils restent du spam
# MÊME si le message contient BUY/SELL (ex: "GOLD BUY : +170pips" = résultat).
# Tous les autres mots d'EXCLUDE_KEYWORDS (analysis, join, vip, motivation...) 
# gardent l'exception BUY/SELL : un vrai signal peut contenir un disclaimer
# ("Educational market analysis only") ou une promo sans être du spam.
STRONG_EXCLUDE_KEYWORDS = [
    "hit", "pips", "tp hit", "tp1 hit", "tp2 hit", "tp3 hit", "all tp hit",
    "mission acomplished", "boom boom boom", "my signal are on fire",
    "closed at", "exit at", "sl hit", "stopped", "secured", "hit target",
    "recap", "result",
]

def is_spam(text: str) -> bool:
    low = text.lower()
    lines = low.split("\n")
    # ★ FIX (v16) : filtrer les messages “Active” même s’ils contiennent BUY/SELL
    # Ex: "GOLD SELL 4337 Active" → statut de signal déjà suivi, pas un nouveau signal
    for line in lines:
        stripped = line.strip().strip("📍🎯📊📈📉❌✅🔴🟢⚪")
        if stripped.endswith(" active") or stripped == "active":
            return True
    has_direction = bool(re.search(r'\b(buy|sell|long|short)\b', low))
    for kw in EXCLUDE_KEYWORDS:
        if kw in low:
            # ★ FIX (v15.1) : low est en minuscules — l'exception BUY/SELL ne matchait jamais
            # ★ FIX (v15.2) : les mots forts (result/recap/hit/pips...) = toujours spam ;
            # les autres mots (analysis, join, vip...) laissent passer un vrai signal.
            if kw not in STRONG_EXCLUDE_KEYWORDS and has_direction:
                continue
            return True
    for kw in SPAM_STANDALONE:
        for line in lines:
            stripped = line.strip().strip("📍🎯📊📈📉❌✅🔴🟢⚪")
            if stripped == kw or stripped == kw + ":":
                return True
    return False






# =============================================================
# NORMALISATION AVEC SUPPRESSION LARGE DES CARACTÈRES INVISIBLES
# =============================================================
def normalize_text(text: str) -> str:
    """Nettoie le texte : supprime emojis, convertit superscripts, remplace séparateurs et parenthèses."""
    # ★ FIX : normaliser les caractères Unicode stylisés (gras/italique mathématiques,
    # ex: 𝗚𝗢𝗟𝗗) vers leur équivalent ASCII standard AVANT tout le reste. Sans ça,
    # .upper() ne les reconnaît pas comme A-Z, et l'étape de suppression des préfixes
    # parasites (^[^A-Z]*) les efface entièrement — le symbole disparaît du texte.
    text = unicodedata.normalize('NFKC', text)

    # 0. Supprimer les caractères invisibles (zéro-width, espaces insécables, etc.)
    text = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', ' ', text)

    # 1. Convertir les superscripts
    sup_map = {'¹':'1', '²':'2', '³':'3', '⁴':'4', '⁵':'5',
               '⁶':'6', '⁷':'7', '⁸':'8', '⁹':'9', '⁰':'0'}
    for sup, digit in sup_map.items():
        text = text.replace(sup, digit)

    # 2. Supprimer les astérisques (utilisés pour le gras)
    text = text.replace('*', '')

    # 3. Remplacer les abréviations
    text = text.replace('S/L', 'SL')
    text = re.sub(r'\(SL\)', 'SL', text)

    # 4. Supprimer les parenthèses, crochets, accolades
    text = re.sub(r'[()\[\]{}]', ' ', text)

    # 5. Remplacer les séparateurs de zone et flèches par des espaces (incluant les deux‑points)
    text = re.sub(r'[–—_/|>→,;~+*=<>:]', ' ', text)

    # 6. Remplacer les flèches unicode
    text = text.replace('▶️', ' ').replace('➡️', ' ').replace('→', ' ')

    # 7. Supprimer les guillemets
    text = re.sub(r'["\']', ' ', text)

    # 8. Supprimer les emojis
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"
        u"\U0001F300-\U0001F5FF"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F1E0-\U0001F1FF"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    text = emoji_pattern.sub(r' ', text)

    # 9. Remplacer les mots-clés de zone
    text = re.sub(r'\b(?:À|A|AND|TO|OU|OR|BETWEEN|ENTRE)\b', ' ', text, flags=re.IGNORECASE)

    # 10. Remplacer les doubles tirets, doubles slashes
    text = re.sub(r'[-]{2,}', ' ', text)
    text = re.sub(r'[/]{2,}', ' ', text)

    # 10b. Nettoyer les points devant les nombres (.4040 → 4040)
    text = re.sub(r'\.(\d{4,6})', r' \1', text)

    # 10c. Nettoyer les points parasites après les nombres (4025... → 4025)
    # Ne supprime que les points NON suivis de chiffres (pas les décimaux comme 4074.00)
    text = re.sub(r'(\d{4,6})\.{1,}(?!\d)', r'\1', text)

    # 11. Normalisation spécifique : remplacer XAU/USD par XAUUSD
    text = text.replace('XAU/USD', 'XAUUSD')
    text = text.replace('X AUUSD', 'XAUUSD')
    text = text.replace('XAU USD', 'XAUUSD')
    text = text.replace('BTC USD', 'BTCUSD')
    text = text.replace('BTC/USD', 'BTCUSD')
    text = text.replace('EUR USD', 'EURUSD')
    text = text.replace('EUR/USD', 'EURUSD')
    text = text.replace('GBP USD', 'GBPUSD')
    text = text.replace('GBP/USD', 'GBPUSD')
    text = text.replace('XAG USD', 'XAGUSD')
    text = text.replace('XAG/USD', 'XAGUSD')
    text = text.replace('US OIL', 'USOIL')

    # BUG #2 : mettre en majuscules avant de supprimer les préfixes
    text = text.upper()

    # Supprimer les préfixes parasites (ex: 0XXAU/USD)
    text = re.sub(r'^[^A-Z]*', '', text)

    # Normaliser les espaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# =============================================================
# EXTRACTEURS
# =============================================================
def _extract_symbol(text: str) -> Optional[str]:
    clean = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\u00a0]', '', text)
    clean = clean.replace('X AUUSD', 'XAUUSD')
    clean = clean.replace('XAU USD', 'XAUUSD')
    clean = clean.replace('BTC USD', 'BTCUSD')
    clean = clean.replace('BTC/USD', 'BTCUSD')
    clean = clean.replace('XXAU/USD', 'XAUUSD')
    clean = clean.replace('XXAUUSD', 'XAUUSD')
    clean = re.sub(r'^[^A-Z]*', '', clean)
    m = RE_SYMBOL.search(clean)
    if m:
        raw = m.group(1).upper().replace(" ", "")
        return SYMBOL_MAP.get(raw, raw)
    return None

def _extract_action(normalized_text: str) -> Optional[str]:
    m = re.search(r'\b(BUY|SELL|LONG|SHORT|PURCHASE|BUYING|SELLING|SEL)\b', normalized_text)
    if m:
        raw = m.group(1).upper()
        if raw in ("BUY", "LONG", "PURCHASE", "BUYING"):
            return "BUY"
        elif raw in ("SELL", "SHORT", "SELLING", "SEL"):
            return "SELL"
    if '▲' in normalized_text or '⬆' in normalized_text:
        return "BUY"
    if '▼' in normalized_text or '⬇' in normalized_text:
        return "SELL"
    return None

def _extract_entry_and_zone(normalized_text: str) -> Tuple[Optional[float], Optional[float], bool]:
    # 1. Prix collés
    m = re.search(r'(\d{4})(\d{4})', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 2. Séparateurs à un caractère
    for sep in [',', ';', '~', '+', '=', '*']:
        m = re.search(r'(\d{4,6}(?:\.\d+)?)\s*' + re.escape(sep) + r'\s*(\d{4,6}(?:\.\d+)?)', normalized_text)
        if m:
            low = float(m.group(1)); high = float(m.group(2))
            if 1000 <= low <= 99999 and 1000 <= high <= 99999:
                return min(low, high), max(low, high), False

    # 3. Séparateurs à deux caractères
    for sep in ['//', '--', '::']:
        m = re.search(r'(\d{4,6}(?:\.\d+)?)\s*' + re.escape(sep) + r'\s*(\d{4,6}(?:\.\d+)?)', normalized_text)
        if m:
            low = float(m.group(1)); high = float(m.group(2))
            if 1000 <= low <= 99999 and 1000 <= high <= 99999:
                return min(low, high), max(low, high), False

    # ★★★ FIX : détecter les zones AVANT les mots-clés ★★★
    # Ex: @4067-4060 → zone 4060-4067 (tiret ou espace comme séparateur)
    m = re.search(r'(?:@|ENTRY|OPEN|ZONE)\s*:?\s*(\d{4,6}(?:\.\d+)?)\s*[-\s]\s*(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 4. Prix unique avec mots-clés (ENTRY, ZONE) — un seul prix
    entry_keywords = ['ENTRY', 'ZONE']
    for kw in entry_keywords:
        m = re.search(rf'{kw}\s*:?\s*' + RE_NUM, normalized_text)
        if m:
            val = float(m.group(1))
            if 1000 <= val <= 99999:
                return val, val, True

    # 4b. @ comme mot-clé d'entrée (mais pas après TP/TARGET)
    m = re.search(r'(?<![A-Z])@\s*:?\s*' + RE_NUM, normalized_text)
    if m:
        # Vérifier que le @ n'est pas précédé de TP ou TARGET
        before = normalized_text[:m.start()]
        if not re.search(r'(?:TP|TARGET|TGT)\s*\d*\s*$', before):
            val = float(m.group(1))
            if 1000 <= val <= 99999:
                return val, val, True

    # 4b. OPEN comme mot-clé d'entrée (mais pas "TP OPEN")
    m = re.search(r'(?<!TP\s)OPEN\s*:?\s*' + RE_NUM, normalized_text)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 99999:
            return val, val, True

    # 5. Mot-clé LIMIT/LMT
    m = re.search(r'(\d{4,6}(?:\.\d+)?)\s+(?:LIMIT|LMT)\s+(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 6. Point entre deux prix (ex: 4199.4203)
    m = re.search(r'(\d{4,6}(?:\.\d+)?)\.(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 7. Deux-points (ex: 4445:4440)
    m = re.search(r'(\d{4,6}(?:\.\d+)?)\s*:\s*(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 8. Parenthèses (ex: (4445)(4440))
    m = re.search(r'\((\d{4,6}(?:\.\d+)?)\)\s*\((\d{4,6}(?:\.\d+)?)\)', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 9. Espace (deux nombres consécutifs) → en dernier pour éviter confusion
    m = re.search(r'\b(\d{4,6}(?:\.\d+)?)\s+(\d{4,6}(?:\.\d+)?)\b', normalized_text)
    if m:
        low = float(m.group(1)); high = float(m.group(2))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False

    # 9b. Raccourci zone : 4033/37 ou 4028-24 → zone (2 chiffres, diff <= 10)
    # Supporte espace, tiret, slash comme séparateur
    m = re.search(r'\b(\d{4})\s*[/\-\s]\s*(\d{2})\b', normalized_text)
    if m:
        base = int(m.group(1))   # 4028
        short = int(m.group(2))  # 24
        last2 = base % 100       # 28
        diff_normal = abs(short - last2)
        diff_rollover = abs((short + 100) - last2)  # ex: 4098/03 → abs(103-98)=5
        if diff_normal <= 10:
            high = (base // 100) * 100 + short
            return float(min(base, high)), float(max(base, high)), False
        elif diff_rollover <= 10:
            # Rollover dizaine : 4098/03 → zone 4098-4103
            high = (base // 100 + 1) * 100 + short
            return float(min(base, high)), float(max(base, high)), False

    # 10. Prix après la direction — zone si 2 nombres (BUY NOW? 4030 4025 ou BUY 4030-4025), unique sinon
    m = re.search(r'\b(BUY|SELL|LONG|SHORT|SEL)\s+(?:NOW\s+)?(\d{4,6}(?:\.\d+)?)\s*[-]?\s*(\d{4,6}(?:\.\d+)?)\b', normalized_text)
    if m and m.group(3):
        low = float(m.group(2)); high = float(m.group(3))
        if 1000 <= low <= 99999 and 1000 <= high <= 99999:
            return min(low, high), max(low, high), False
    # 10a. Raccourci zone après BUY/SELL : BUY 4028-24 ou SELL 4028-24
    m = re.search(r'\b(BUY|SELL|LONG|SHORT|SEL)\s+(?:NOW\s+)?(\d{4})\s*[-]\s*(\d{2})\b', normalized_text)
    if m:
        base = int(m.group(2))   # 4028
        short = int(m.group(3))  # 24
        last2 = base % 100       # 28
        diff_normal = abs(short - last2)
        diff_rollover = abs((short + 100) - last2)
        if diff_normal <= 10:
            high = (base // 100) * 100 + short
            return float(min(base, high)), float(max(base, high)), False
        elif diff_rollover <= 10:
            high = (base // 100 + 1) * 100 + short
            return float(min(base, high)), float(max(base, high)), False
    # 10b. Prix unique après la direction (ex: BUY 4512 ou BUY NOW 4512)
    m = re.search(r'\b(BUY|SELL|LONG|SHORT|SEL)\s+(?:NOW\s+)?([\d.]+)', normalized_text)
    if m:
        val = float(m.group(2))
        if 1000 <= val <= 99999:
            return val, val, True

    # 11. Capture de nombres proches de la direction — zone si 2 nombres sans mot-clé entre eux
    dir_match = re.search(r'\b(BUY|SELL|LONG|SHORT|SEL)\b', normalized_text)
    if dir_match:
        start = dir_match.end()
        snippet = normalized_text[start:start+50]
        # Ignorer NOW au début du snippet
        snippet = re.sub(r'^\s*NOW\s+', '', snippet)
        # Chercher 2 nombres consécutifs (séparés uniquement par des espaces/ponctuation)
        # sans mot-clé TP/SL/STOP entre les deux
        m = re.match(r'[^\d]*?(\d{4,6}(?:\.\d+)?)\s*[-]?\s*(\d{4,6}(?:\.\d+)?)\b', snippet)
        if m:
            # Vérifier qu'aucun mot-clé TP/SL n'apparaît entre les deux nombres
            between = snippet[m.start(1):m.end(2)]
            if not re.search(r'\b(TP|SL|STOP|TARGET|TGT)\b', between):
                a, b = float(m.group(1)), float(m.group(2))
                if 1000 <= a <= 99999 and 1000 <= b <= 99999:
                    return min(a, b), max(a, b), False
        nums = re.findall(r'\b(\d{4,6}(?:\.\d+)?)\b', snippet)
        if nums:
            val = float(nums[0])
            if 1000 <= val <= 99999:
                return val, val, True

    # 12. Dernier recours
    all_nums = re.findall(r'\b\d{4,6}(?:\.\d+)?\b', normalized_text)
    if all_nums:
        val = float(all_nums[0])
        return val, val, True

    log.warning(f"[PARSING] Aucune entrée trouvée dans : {normalized_text[:100]}")
    return None, None, False

def _extract_all_tps(normalized_text: str) -> List[float]:
    # ★ FIX : "TP OPEN" = pas de TP spécifique (sera généré par le bot)
    if re.search(r'TP\s+OPEN', normalized_text):
        return []

    # ★ FIX : extraire le SL pour l'exclure des TP
    sl_val = _extract_sl(normalized_text)

    tps = {}
    sep = r'[:\-=_/|▶️➡️→@]?'

    for m in re.finditer(r'TP\s*(\d+)?\s*' + sep + r'\s*' + RE_NUM, normalized_text):
        num = int(m.group(1)) if m.group(1) else len(tps) + 1
        val = float(m.group(2))
        if 1000 <= val <= 99999 and val != sl_val:
            tps[num] = val

    if not tps:
        for m in re.finditer(r'(?:TAKE\s*PROFIT|TARGET|TGT)\s*(\d+)?\s*' + sep + r'\s*' + RE_NUM, normalized_text):
            num = int(m.group(1)) if m.group(1) else len(tps) + 1
            val = float(m.group(2))
            if 1000 <= val <= 99999 and val != sl_val:
                tps[num] = val

    if not tps:
        for m in re.finditer(r'T\s*(\d+)?\s*' + sep + r'\s*' + RE_NUM, normalized_text):
            num = int(m.group(1)) if m.group(1) else len(tps) + 1
            val = float(m.group(2))
            if 1000 <= val <= 99999 and val != sl_val:
                tps[num] = val

    return [tps[k] for k in sorted(tps.keys())]


def _extract_orphan_tps(normalized_text: str, zone_low: float, zone_high: float, sl: float, action: str) -> List[float]:
    """Détecte les TP sans mot-clé (TP, TARGET, etc.).
    Cherche les nombres entre la zone et le SL qui sont dans la direction du trade.
    Ex: BUY ENTRY 4028-4022 4030 4040 SL 4002 → TPs = [4030, 4040]"""
    sl_match = re.search(r'SL\s*\d{4,6}', normalized_text)
    search_text = normalized_text[:sl_match.start()] if sl_match else normalized_text

    zone_match = re.search(r'(?:ENTRY|@|ZONE)\s*:?\s*\d{4,6}[^\d]*\d{4,6}', search_text)
    if zone_match:
        search_text = search_text[zone_match.end():]

    nums = re.findall(r'\b(\d{4,6}(?:\.\d+)?)\b', search_text)
    zone_mid = (zone_low + zone_high) / 2
    tps = []
    for n in nums:
        val = float(n)
        # Exclure zone boundaries, SL, et toute valeur dans [zone_low, zone_high]
        if val == sl:
            continue
        if zone_low <= val <= zone_high:
            continue
        if not (1000 <= val <= 99999):
            continue
        if action == "BUY" and val > zone_mid:
            tps.append(val)
        elif action == "SELL" and val < zone_mid:
            tps.append(val)
    return sorted(tps) if action == "BUY" else sorted(tps, reverse=True)


def _extract_sl(normalized_text: str) -> Optional[float]:
    m = re.search(r'STOP\s+LOSS.*?(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 99999:
            return val

    m = re.search(r'SL.*?(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 99999:
            return val

    m = re.search(r'STOP.*?(\d{4,6}(?:\.\d+)?)', normalized_text)
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 99999:
            return val

    return None


def _extract_merge_price(normalized_text: str) -> Optional[float]:
    """Extrait le prix après BUY MORE / SELL MORE / ADD MORE (et l'ordre inversé
    MORE BUY / MORE SELL / MORE ADD, format aussi utilisé par certains canaux).
    Ex: 'XAUUSD BUY 3885 BUY MORE 3880' → 3880.0
    Ex: 'XAUUSD SELL 4006 MORE SELL 4010' → 4010.0"""
    m = re.search(
        r'(?:(?:BUY|SELL|ADD)\s+MORE|MORE\s+(?:BUY|SELL|ADD))\s+(\d{4,6}(?:\.\d+)?)',
        normalized_text
    )
    if m:
        val = float(m.group(1))
        if 1000 <= val <= 99999:
            return val
    return None


# =============================================================
# PARSER PRINCIPAL
# =============================================================
class SignalParser:
    def __init__(self, format_profile: Optional[FormatProfile] = None):
        self.format_profile = format_profile

    def set_format_profile(self, profile: FormatProfile):
        self.format_profile = profile

    def parse(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        if not text or not text.strip():
            return None
        norm = normalize_text(text)
        if is_spam(text):
            log.debug(f"[SPAM] {text[:60]}")
            return None

        # CLOSE
        result = self._parse_close(text, timestamp)
        if result:
            return result

        # SL_MOVE
        result = self._parse_sl_move(norm, timestamp)
        if result:
            return result

        # TRADE
        result = self._parse_trade(norm, timestamp, raw_text=text)
        if result:
            return result

        log.debug(f"[PARSING] Aucun signal reconnu dans : {norm[:100]}")
        return None

    def _parse_close(self, text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        if re.search(r'^\s*CLOSE\s*$', text, re.IGNORECASE):
            return TradeSignal(
                signal_type="CLOSE",
                close_all=True,
                close_symbol=None,
                raw_text=text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        if re.search(r'CLOSE\s+NOW', text, re.IGNORECASE):
            return TradeSignal(
                signal_type="CLOSE",
                close_all=True,
                close_symbol=None,
                raw_text=text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        m = re.search(r'CLOSE\s+([A-Z]{3,10})(?:\s+NOW)?', text, re.IGNORECASE)
        if m:
            symbol = _extract_symbol(m.group(1))
            return TradeSignal(
                signal_type="CLOSE",
                close_all=False,
                close_symbol=symbol,
                raw_text=text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        if re.search(r'CLOSE\s+ALL', text, re.IGNORECASE):
            return TradeSignal(
                signal_type="CLOSE",
                close_all=True,
                close_symbol=None,
                raw_text=text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        return None

    def _parse_sl_move(self, normalized_text: str, timestamp: Optional[datetime] = None) -> Optional[TradeSignal]:
        m = re.search(
            r'(?:SL\s*MOVE|MOVE\s*SL|NEW\s*SL|SL\s*→|SL\s*MOVED?\s*TO)\s*:?\s*' + RE_NUM,
            normalized_text
        )
        if m:
            return TradeSignal(
                signal_type="SL_MOVE",
                new_sl=float(m.group(1)),
                raw_text=normalized_text[:200],
                timestamp=timestamp,
                confidence=1.0,
            )
        return None

    def _parse_trade(self, normalized_text: str, timestamp: Optional[datetime] = None, raw_text: str = "") -> Optional[TradeSignal]:
        symbol = _extract_symbol(normalized_text)
        if not symbol:
            # ★ FIX : fallback XAUUSD par défaut si aucun symbole détecté
            # (certains canaux Gold n'écrivent pas GOLD/XAUUSD dans leurs signaux)
            symbol = "XAUUSD"
            log.debug(f"[PARSING] Symbole non trouvé → fallback XAUUSD : {normalized_text[:80]}")

        action = _extract_action(normalized_text)

        # ★ FIX (v15.1) : canaux qui masquent BUY/SELL (image jointe au lieu du mot)
        # Déduire la direction depuis la structure Entry/TP/SL :
        #   - TPs au-dessus de l'entrée → BUY ; TPs en dessous → SELL
        #   - SL en dessous de l'entrée → BUY ; SL au-dessus → SELL
        if not action:
            sl_tmp = _extract_sl(normalized_text)
            zone_tmp, zone_tmp_h, is_single_tmp = _extract_entry_and_zone(normalized_text)
            tps_tmp = _extract_all_tps(normalized_text)
            if zone_tmp is not None:
                entry_mid = (zone_tmp + zone_tmp_h) / 2
                if tps_tmp:
                    above = sum(1 for tp in tps_tmp if tp > entry_mid)
                    below = sum(1 for tp in tps_tmp if tp < entry_mid)
                    if above > below:
                        action = "BUY"
                    elif below > above:
                        action = "SELL"
                if not action and sl_tmp is not None:
                    if sl_tmp < entry_mid:
                        action = "BUY"
                    elif sl_tmp > entry_mid:
                        action = "SELL"
                if action:
                    log.info(f"[PARSING] Direction déduite Entry/TP/SL → {action} : {normalized_text[:80]}")
        if not action:
            log.debug(f"[PARSING] Action non trouvée dans : {normalized_text[:100]}")
            return None

        # ★ FIX : extraire SL AVANT l'entrée pour éviter que le SL soit capturé comme entrée
        sl = _extract_sl(normalized_text)

        zone_low, zone_high, is_single = _extract_entry_and_zone(normalized_text)

        if zone_low is None:
            # --- MARKET PRICE : signal sans prix (ex: GOLD BUY NOW, BUY XAUUSD NOW) ---
            if self._is_market_now(normalized_text):
                log.debug(f"[PARSING] Market price signal détecté: {action} {symbol}")
                sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
                RR_RATIO = float(os.getenv("RR_RATIO_DEFAULT", "1.5"))
                if action == "BUY":
                    default_sl = -sl_offset  # offset relatif, le prix réel sera résolu à l'exécution
                    default_tp = sl_offset * RR_RATIO
                else:
                    default_sl = sl_offset
                    default_tp = -(sl_offset * RR_RATIO)
                return TradeSignal(
                    signal_type="TRADE",
                    direction=action,
                    entry=None,  # prix marché → résolu par le bridge MT5
                    zone_low=None,
                    zone_high=None,
                    tps=[round(default_tp, 2)],
                    sl=round(default_sl, 2),
                    pair=symbol,
                    raw_text=normalized_text[:200],
                    timestamp=timestamp,
                    confidence=0.15,
                    is_single_price=True,
                    is_quick_alert=True,
                    is_market_price=True,
                    format_profile=self.format_profile,
                )
            log.debug(f"[PARSING] Entrée non trouvée dans : {normalized_text[:100]}")
            return None

        tps = _extract_all_tps(normalized_text)
        # sl déjà extrait plus haut
        merge_price = _extract_merge_price(normalized_text)

        # ★ FIX : MORE SELL/BUY → convertir en zone si prix unique
        # Ex: SELL 4045 MORE SELL 4050 → zone [4045, 4050]
        if is_single and merge_price is not None:
            zone_low = min(zone_low, merge_price)
            zone_high = max(zone_high, merge_price)
            is_single = False
            log.debug(f"MORE détecté → zone [{zone_low}, {zone_high}]")

        # ★ FIX : TP sans mot-clé — chercher les nombres entre zone et SL
        if not tps and sl is not None:
            orphan_tps = _extract_orphan_tps(normalized_text, zone_low, zone_high, sl, action)
            if orphan_tps:
                tps = orphan_tps
                log.debug(f"TP détectés sans mot-clé: {tps}")

        # ★ FIX : détecter les signaux à double zones (résultat + signal)
        # Compte les paires de nombres (hors TPs et SL) qui ressemblent à des zones.
        # Si > 1, le signal est ambigu → on le rejette.
        zone_count = self._count_zone_candidates(raw_text or normalized_text, zone_low, zone_high, tps, sl)
        if zone_count > 1:
            log.warning(f"[PARSING] Signal à double zones ANNULÉ : {normalized_text[:120]}")
            return None

        # Génération auto SL si TPs présents mais pas de SL
        if tps and sl is None:
            avg_tp = sum(tps) / len(tps)
            entry_mid = (zone_low + zone_high) / 2
            if action == "BUY":
                distance = avg_tp - entry_mid
                sl = entry_mid - max(distance * 1.5, 2.0)
            else:
                distance = entry_mid - avg_tp
                sl = entry_mid + max(distance * 1.5, 2.0)
            sl = round(sl, 2)
            log.debug(f"SL généré automatiquement : {sl} (basé sur les TPs)")

        # Génération auto TP si SL présent mais pas de TPs
        if sl is not None and not tps:
            RR_RATIO = float(os.getenv("RR_RATIO_DEFAULT", "1.5"))
            entry_mid = (zone_low + zone_high) / 2
            if action == "BUY":
                tp = entry_mid + (entry_mid - sl) * RR_RATIO
            else:
                tp = entry_mid - (sl - entry_mid) * RR_RATIO
            tps = [round(tp, 2)]
            log.debug(f"TP généré automatiquement : {tps[0]} (RR={RR_RATIO})")

        # Quick alert si ni TP ni SL
        if not tps and sl is None:
            log.debug(f"Quick alert détecté: {action} {symbol} @{zone_low}")
            sl_offset = float(os.getenv("QUICK_ALERT_SL_OFFSET", "10.0"))
            RR_RATIO = float(os.getenv("RR_RATIO_DEFAULT", "1.5"))
            entry_mid = (zone_low + zone_high) / 2
            if action == "BUY":
                provisional_sl = entry_mid - sl_offset
                default_tp = entry_mid + sl_offset * RR_RATIO
            else:
                provisional_sl = entry_mid + sl_offset
                default_tp = entry_mid - sl_offset * RR_RATIO
            return TradeSignal(
                signal_type="TRADE",
                direction=action,
                entry=zone_low,
                zone_low=zone_low,
                zone_high=zone_high,
                tps=[round(default_tp, 2)],
                sl=round(provisional_sl, 2),
                pair=symbol,
                raw_text=normalized_text[:200],
                timestamp=timestamp,
                confidence=0.2,
                is_single_price=is_single,
                is_quick_alert=True,
                merge_price=merge_price,
                format_profile=self.format_profile,
            )

        entry_mid = (zone_low + zone_high) / 2
        if not self._validate_sl(action, entry_mid, sl):
            log.warning(f"SL invalide: {action} entry={entry_mid} SL={sl}")
            return None

        confidence = 0.3
        if tps:
            confidence += 0.3
        if sl:
            confidence += 0.2
        if len(tps) >= 2:
            confidence += 0.1
        if len(tps) >= 3:
            confidence += 0.1

        return TradeSignal(
            signal_type="TRADE",
            direction=action,
            entry=zone_low,
            zone_low=zone_low,
            zone_high=zone_high,
            tps=tps,
            sl=sl,
            pair=symbol,
            raw_text=normalized_text[:200],
            timestamp=timestamp,
            confidence=confidence,
            is_single_price=is_single,
            is_quick_alert=False,
            merge_price=merge_price,
            format_profile=self.format_profile,
        )

    @staticmethod
    def _count_zone_candidates(raw_text: str, zone_low: float, zone_high: float, tps: list = None, sl: float = None) -> int:
        """Détecte les signaux ambigus contenant plus d'une paire de nombres
        sur la MÊME LIGNE qui ressemble à une zone (entrée ou résultat).
        Ex: "Gold 4088 to 4063 ... GOLD SELL 4087 4088" → 2 zones → annulé.
        Les TP orphelins (un nombre par ligne) ne sont jamais comptés."""
        sl_val = sl
        tp_vals = set(tps or [])

        zone_pairs = set()
        # Analyser ligne par ligne — une zone = deux prix sur la même ligne
        for line in raw_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Supprimer les séparateurs de zone (to, à, and, between...) pour
            # que "4088 to 4063" devienne "4088 4063"
            line_clean = re.sub(r'\b(?:À|A|AND|TO|OU|OR|BETWEEN|ENTRE)\b', ' ', line, flags=re.IGNORECASE)
            line_clean = re.sub(r'\s+', ' ', line_clean).strip()
            # Paires séparées par espace (ex: 4088 4063)
            for m in re.finditer(r'\b(\d{4,6})\s+(\d{4,6})\b', line_clean):
                a, b = int(m.group(1)), int(m.group(2))
                if not (1000 <= a <= 99999 and 1000 <= b <= 99999):
                    continue
                if abs(a - b) > 50:
                    continue
                if a == sl_val or b == sl_val:
                    continue
                if a in tp_vals or b in tp_vals:
                    continue
                zone_pairs.add((min(a, b), max(a, b)))
            # Paires format point (ex: 4087.4088)
            for m in re.finditer(r'\b(\d{4,6})\.(\d{4,6})\b', line_clean):
                a, b = int(m.group(1)), int(m.group(2))
                if 1000 <= a <= 99999 and 1000 <= b <= 99999 and abs(a - b) <= 50:
                    if a == sl_val or b == sl_val:
                        continue
                    if a in tp_vals or b in tp_vals:
                        continue
                    zone_pairs.add((min(a, b), max(a, b)))

        return len(zone_pairs)

    @staticmethod
    def _is_market_now(normalized_text: str) -> bool:
        """Détecte les signaux sans prix : BUY NOW, SELL NOW, GOLD BUY NOW, etc."""
        # Mots-clés indiquant un ordre marché immédiat
        market_keywords = ['NOW', 'MARKET', 'MKT', 'IMMEDIATELY', 'IMMEDIATE', 'INSTANT', 'OPEN']
        # Vérifier qu'il n'y a AUCUN prix dans le texte (pas de nombre 4-5 chiffres)
        has_price = re.search(r'\b\d{4,6}(?:\.\d+)?\b', normalized_text)
        if has_price:
            return False
        # Vérifier présence d'un mot-clé marché
        for kw in market_keywords:
            if re.search(rf'\b{kw}\b', normalized_text):
                return True
        return False

    @staticmethod
    def _validate_sl(action: str, entry_price: float, sl: float) -> bool:
        if action == "BUY" and sl >= entry_price:
            return False
        if action == "SELL" and sl <= entry_price:
            return False
        return True


# =============================================================
# BATCH PARSING
# =============================================================
def parse_messages(messages: List[Tuple[str, datetime]],
                   format_profile: Optional[FormatProfile] = None) -> List[TradeSignal]:
    parser = SignalParser(format_profile)
    signals = []
    for text, ts in messages:
        signal = parser.parse(text, ts)
        if signal:
            signals.append(signal)
    return signals
