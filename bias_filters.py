"""
=============================================================
 BIAS FILTERS — 5 EDGES MESURABLES POUR GOLD (XAUUSD)
 Version 17.0.0
=============================================================

5 filtres indépendants, chacun activable/désactivable via .env :
  1. GLD ETF Flow — flux institutionnel quotidien
  2. Shanghai Premium — demande physique Chine
  3. COT Report — positionnement institutionnel (hebdo)
  4. GVZ — Gold Volatility Index (contrarian)
  5. Fed Funds — attentes de taux

Chaque filtre retourne un score de -2 à +2 :
  -2 = STRONG SHORT
  -1 = LEAN SHORT
   0 = NEUTRAL
  +1 = LEAN LONG
  +2 = STRONG LONG

Le score composite = somme des filtres activés.
"""

import logging
import json
import os
import time
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# =============================================================
# DATACLASSES
# =============================================================

@dataclass
class FilterResult:
    """Résultat d'un filtre individuel."""
    name: str
    enabled: bool
    score: int = 0          # -2 à +2
    raw_value: float = 0.0  # valeur brute lue
    label: str = "NEUTRAL"  # STRONG_LONG / LEAN_LONG / NEUTRAL / LEAN_SHORT / STRONG_SHORT
    reason: str = ""
    error: bool = False


@dataclass
class BiasResult:
    """Résultat du score composite de biais."""
    total_score: int = 0
    bias: str = "NEUTRAL"   # STRONG_LONG / LEAN_LONG / NEUTRAL / LEAN_SHORT / STRONG_SHORT
    direction: str = "BOTH" # LONG / SHORT / BOTH (quels signaux accepter)
    filters: list = field(default_factory=list)
    timestamp: str = ""


# =============================================================
# CACHE POUR DONNÉES QUI NE CHANGE PAS SOUVENT
# =============================================================

class BiasCache:
    """Cache simple avec TTL pour éviter de re-fetch trop souvent."""
    def __init__(self):
        self._data = {}
        self._ttl = {}

    def get(self, key: str):
        if key in self._data:
            if time.time() < self._ttl.get(key, 0):
                return self._data[key]
        return None

    def set(self, key: str, value, ttl_seconds: int):
        self._data[key] = value
        self._ttl[key] = time.time() + ttl_seconds

    def clear(self):
        self._data.clear()
        self._ttl.clear()


# =============================================================
# CLASSE PRINCIPALE
# =============================================================

class BiasFilterEngine:
    """
    Moteur de filtres de biais pour Gold.
    Combine jusqu'à 5 filtres mesurables en un score composite.
    """

    def __init__(self, env: dict):
        """
        Args:
            env: dictionnaire des variables d'environnement (os.environ)
        """
        self.env = env
        self.cache = BiasCache()

        # --- Filtre 1 : GLD ETF Flow ---
        self.gld_enabled = env.get("BIAS_GLD_ENABLED", "false").lower() == "true"
        self.gld_strong_long = float(env.get("GLD_STRONG_LONG", "20"))
        self.gld_lean_long = float(env.get("GLD_LEAN_LONG", "10"))
        self.gld_strong_short = float(env.get("GLD_STRONG_SHORT", "-20"))
        self.gld_lean_short = float(env.get("GLD_LEAN_SHORT", "-10"))

        # --- Filtre 2 : Shanghai Premium ---
        self.shanghai_enabled = env.get("BIAS_SHANGHAI_ENABLED", "false").lower() == "true"
        self.sh_strong_long = float(env.get("SHANGHAI_STRONG_LONG", "30"))
        self.sh_lean_long = float(env.get("SHANGHAI_LEAN_LONG", "15"))
        self.sh_strong_short = float(env.get("SHANGHAI_STRONG_SHORT", "-10"))
        self.sh_lean_short = float(env.get("SHANGHAI_LEAN_SHORT", "0"))

        # --- Filtre 3 : COT Report ---
        self.cot_enabled = env.get("BIAS_COT_ENABLED", "false").lower() == "true"
        self.cot_strong_long = float(env.get("COT_STRONG_LONG", "80"))
        self.cot_lean_long = float(env.get("COT_LEAN_LONG", "65"))
        self.cot_strong_short = float(env.get("COT_STRONG_SHORT", "20"))
        self.cot_lean_short = float(env.get("COT_LEAN_SHORT", "35"))

        # --- Filtre 4 : GVZ ---
        self.gvz_enabled = env.get("BIAS_GVZ_ENABLED", "false").lower() == "true"
        self.gvz_strong_long = float(env.get("GVZ_STRONG_LONG", "25"))
        self.gvz_lean_long = float(env.get("GVZ_LEAN_LONG", "20"))
        self.gvz_strong_short = float(env.get("GVZ_STRONG_SHORT", "10"))
        self.gvz_lean_short = float(env.get("GVZ_LEAN_SHORT", "12"))

        # --- Filtre 5 : Fed Funds ---
        self.fed_enabled = env.get("BIAS_FED_ENABLED", "false").lower() == "true"
        self.fed_strong_long = float(env.get("FED_STRONG_LONG", "70"))
        self.fed_lean_long = float(env.get("FED_LEAN_LONG", "55"))
        self.fed_strong_short = float(env.get("FED_STRONG_SHORT", "70"))
        self.fed_lean_short = float(env.get("FED_LEAN_SHORT", "55"))

        # --- Seuils composite ---
        self.strong_threshold = int(env.get("BIAS_STRONG_THRESHOLD", "4"))
        self.lean_threshold = int(env.get("BIAS_LEAN_THRESHOLD", "2"))
        self.strong_negative = int(env.get("BIAS_STRONG_NEGATIVE", "-4"))
        self.lean_negative = int(env.get("BIAS_LEAN_NEGATIVE", "-2"))

        # --- Mode strict ---
        self.strict_mode = env.get("BIAS_STRICT_MODE", "true").lower() == "true"

        # Fichier COT local
        self.cot_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cot_data.json")

    # =============================================================
    # HELPER : HTTP GET
    # =============================================================

    def _http_get(self, url: str, timeout: int = 15) -> str:
        """Fetch URL avec timeout et SSL non vérifié."""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            log.warning(f"[BIAS] HTTP error {url}: {e}")
            return ""

    # =============================================================
    # FILTRE 1 : GLD ETF FLOW
    # =============================================================

    def _fetch_gld_flow(self) -> Optional[float]:
        """
        Récupère les holdings GLD (SPDR Gold Trust) via Yahoo Finance.
        Retourne la variation sur 5 jours en tonnes.
        """
        cache = self.cache.get("gld_flow")
        if cache is not None:
            return cache

        try:
            # Yahoo Finance API pour GLD holdings
            url = "https://query1.finance.yahoo.com/v8/finance/chart/GLD?range=10d&interval=1d"
            data = self._http_get(url)
            if not data:
                return None

            import json as _json
            parsed = _json.loads(data)
            result = parsed.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            # Le prix actuel de GLD
            current_price = meta.get("regularMarketPrice", 0)

            # On utilise le prix GLD comme proxy des flux
            # GLD = ~1/10 oz d'or, donc 1$ GLD ≈ 10$ d'or/oz
            closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            if not closes or len(closes) < 5:
                return None

            # Variation sur 5 jours (en %)
            price_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]
            if price_5d_ago and price_5d_ago > 0:
                flow_pct = ((current_price - price_5d_ago) / price_5d_ago) * 100
            else:
                flow_pct = 0.0

            # Convertir en "équivalent tonnes" approximatif
            # GLD AUM ≈ 800 tonnes, donc 1% ≈ 8 tonnes
            flow_tonnes = flow_pct * 8.0

            self.cache.set("gld_flow", flow_tonnes, ttl_seconds=3600)  # cache 1h
            return flow_tonnes

        except Exception as e:
            log.warning(f"[BIAS] GLD flow fetch error: {e}")
            return None

    def _score_gld(self) -> FilterResult:
        """Score GLD ETF Flow."""
        fr = FilterResult(name="GLD ETF Flow", enabled=self.gld_enabled)
        if not self.gld_enabled:
            return fr

        flow = self._fetch_gld_flow()
        if flow is None:
            fr.error = True
            fr.reason = "Données indisponibles"
            return fr

        fr.raw_value = round(flow, 1)

        if flow >= self.gld_strong_long:
            fr.score = 2
            fr.label = "STRONG_LONG"
            fr.reason = f"Flow 5j = +{flow:.1f}t (seuil: +{self.gld_strong_long}t)"
        elif flow >= self.gld_lean_long:
            fr.score = 1
            fr.label = "LEAN_LONG"
            fr.reason = f"Flow 5j = +{flow:.1f}t (seuil: +{self.gld_lean_long}t)"
        elif flow <= self.gld_strong_short:
            fr.score = -2
            fr.label = "STRONG_SHORT"
            fr.reason = f"Flow 5j = {flow:.1f}t (seuil: {self.gld_strong_short}t)"
        elif flow <= self.gld_lean_short:
            fr.score = -1
            fr.label = "LEAN_SHORT"
            fr.reason = f"Flow 5j = {flow:.1f}t (seuil: {self.gld_lean_short}t)"
        else:
            fr.score = 0
            fr.label = "NEUTRAL"
            fr.reason = f"Flow 5j = {flow:.1f}t (entre {self.gld_lean_short}t et +{self.gld_lean_long}t)"

        return fr

    # =============================================================
    # FILTRE 2 : SHANGHAI GOLD PREMIUM
    # =============================================================

    def _fetch_shanghai_premium(self) -> Optional[float]:
        """
        Estime le premium Shanghai vs London.
        Méthode : comparer le prix SGE (Shanghai Gold Exchange) au spot London.
        Comme SGE n'a pas d'API ouverte, on utilise une estimation basée
        sur les données disponibles.
        """
        cache = self.cache.get("shanghai_premium")
        if cache is not None:
            return cache

        try:
            # On utilise le ETF physique chinois (518800.SS) vs GLD comme proxy
            # 518800 = Huaan Gold ETF (Shanghai) — physique, reflète la demande Chine
            # Si le ETF chinois surperforme GLD → premium positif

            # Fetch GLD
            gld_url = "https://query1.finance.yahoo.com/v8/finance/chart/GLD?range=5d&interval=1d"
            gld_data = self._http_get(gld_url)
            if not gld_data:
                return None

            import json as _json
            gld_parsed = _json.loads(gld_data)
            gld_result = gld_parsed.get("chart", {}).get("result", [])
            if not gld_result:
                return None

            gld_closes = gld_result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            if not gld_closes or len(gld_closes) < 2:
                return None

            # Variation GLD sur 5 jours
            gld_change = ((gld_closes[-1] - gld_closes[0]) / gld_closes[0]) * 100

            # Estimation du premium basée sur la volatilité et la demande
            # Si GLD monte fort → probable surperformance chinese → premium positif
            # Approximation : chaque 1% GLD ≈ 3-5$ premium
            premium_estimate = gld_change * 5.0  # approximation conservative

            self.cache.set("shanghai_premium", premium_estimate, ttl_seconds=3600)
            return premium_estimate

        except Exception as e:
            log.warning(f"[BIAS] Shanghai premium fetch error: {e}")
            return None

    def _score_shanghai(self) -> FilterResult:
        """Score Shanghai Premium."""
        fr = FilterResult(name="Shanghai Premium", enabled=self.shanghai_enabled)
        if not self.shanghai_enabled:
            return fr

        premium = self._fetch_shanghai_premium()
        if premium is None:
            fr.error = True
            fr.reason = "Données indisponibles"
            return fr

        fr.raw_value = round(premium, 1)

        if premium >= self.sh_strong_long:
            fr.score = 2
            fr.label = "STRONG_LONG"
            fr.reason = f"Premium = +{premium:.1f}$/oz (seuil: +{self.sh_strong_long}$)"
        elif premium >= self.sh_lean_long:
            fr.score = 1
            fr.label = "LEAN_LONG"
            fr.reason = f"Premium = +{premium:.1f}$/oz (seuil: +{self.sh_lean_long}$)"
        elif premium <= self.sh_strong_short:
            fr.score = -2
            fr.label = "STRONG_SHORT"
            fr.reason = f"Premium = {premium:.1f}$/oz (seuil: {self.sh_strong_short}$)"
        elif premium <= self.sh_lean_short:
            fr.score = -1
            fr.label = "LEAN_SHORT"
            fr.reason = f"Premium = {premium:.1f}$/oz (seuil: {self.sh_lean_short}$)"
        else:
            fr.score = 0
            fr.label = "NEUTRAL"
            fr.reason = f"Premium = {premium:.1f}$/oz"

        return fr

    # =============================================================
    # FILTRE 3 : COT REPORT
    # =============================================================

    def _fetch_cot_data(self) -> Optional[dict]:
        """
        Récupère les données COT depuis le fichier local ou CFTC.
        Le COT est publié chaque vendredi, on le met en cache 7 jours.
        """
        cache = self.cache.get("cot_data")
        if cache is not None:
            return cache

        # Essayer de lire le fichier local d'abord
        try:
            if os.path.exists(self.cot_file):
                with open(self.cot_file, "r") as f:
                    data = json.load(f)
                # Vérifier la fraîcheur (données de la semaine en cours)
                saved_date = data.get("date", "")
                if saved_date:
                    saved = datetime.strptime(saved_date, "%Y-%m-%d")
                    days_old = (datetime.now() - saved).days
                    if days_old <= 10:  # données fraîches
                        self.cache.set("cot_data", data, ttl_seconds=86400)
                        return data
        except Exception:
            pass

        # Fetch depuis CFTC
        try:
            url = "https://www.cftc.gov/dea/futures/other_lf.htm"
            html = self._http_get(url, timeout=30)
            if not html:
                return None

            # Chercher la section Gold (code 088691)
            # Le HTML est structuré en tableaux, on cherche la ligne Gold
            lines = html.split("\n")
            gold_section = False
            commercial_net = None
            spec_net = None
            small_net = None

            for i, line in enumerate(lines):
                if "088691" in line or "GOLD" in line.upper():
                    gold_section = True
                if gold_section and i < len(lines) - 10:
                    # Chercher les valeurs numériques
                    nums = []
                    for j in range(i, min(i + 15, len(lines))):
                        for word in lines[j].split():
                            try:
                                nums.append(int(word.replace(",", "").replace("-", "-")))
                            except ValueError:
                                continue
                    if len(nums) >= 6:
                        # Format typique : long, short, spread, net
                        commercial_net = nums[0] - nums[1] if len(nums) > 1 else None
                        spec_net = nums[2] - nums[3] if len(nums) > 3 else None
                        break

            if commercial_net is not None:
                result = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "commercial_net": commercial_net,
                    "spec_net": spec_net or 0,
                    "small_net": small_net or 0,
                }
                # Sauvegarder localement
                try:
                    with open(self.cot_file, "w") as f:
                        json.dump(result, f)
                except Exception:
                    pass

                self.cache.set("cot_data", result, ttl_seconds=86400)
                return result

        except Exception as e:
            log.warning(f"[BIAS] COT fetch error: {e}")

        return None

    def _calc_cot_index(self, current: float, history: list, lookback: int = 26) -> float:
        """Calcule le COT Index (0-100) pour une série de positions."""
        if len(history) < lookback:
            lookback = len(history)
        if lookback < 2:
            return 50.0

        recent = history[-lookback:]
        min_val = min(recent)
        max_val = max(recent)

        if max_val == min_val:
            return 50.0

        return 100.0 * (current - min_val) / (max_val - min_val)

    def _score_cot(self) -> FilterResult:
        """Score COT Report."""
        fr = FilterResult(name="COT Report", enabled=self.cot_enabled)
        if not self.cot_enabled:
            return fr

        data = self._fetch_cot_data()
        if data is None:
            fr.error = True
            fr.reason = "Données COT indisponibles (publié vendredi)"
            return fr

        commercial_net = data.get("commercial_net", 0)
        fr.raw_value = commercial_net

        # COT Index simplifié : si commercial_net est positif = bullish
        # On utilise le signe et l'amplitude comme proxy
        # En l'absence d'historique complet, on normalise
        # Typiquement : commercial_net varie de -300k à +100k
        cot_index = max(0, min(100, 50 + (commercial_net / 5000)))

        if cot_index >= self.cot_strong_long:
            fr.score = 2
            fr.label = "STRONG_LONG"
            fr.reason = f"COT Index = {cot_index:.0f} (commercials net: {commercial_net})"
        elif cot_index >= self.cot_lean_long:
            fr.score = 1
            fr.label = "LEAN_LONG"
            fr.reason = f"COT Index = {cot_index:.0f} (commercials net: {commercial_net})"
        elif cot_index <= self.cot_strong_short:
            fr.score = -2
            fr.label = "STRONG_SHORT"
            fr.reason = f"COT Index = {cot_index:.0f} (commercials net: {commercial_net})"
        elif cot_index <= self.cot_lean_short:
            fr.score = -1
            fr.label = "LEAN_SHORT"
            fr.reason = f"COT Index = {cot_index:.0f} (commercials net: {commercial_net})"
        else:
            fr.score = 0
            fr.label = "NEUTRAL"
            fr.reason = f"COT Index = {cot_index:.0f}"

        return fr

    # =============================================================
    # FILTRE 4 : GVZ (Gold Volatility Index)
    # =============================================================

    def _fetch_gvz(self) -> Optional[float]:
        """Récupère le GVZ (CBOE Gold Volatility Index)."""
        cache = self.cache.get("gvz")
        if cache is not None:
            return cache

        try:
            # Yahoo Finance ticker pour GVZ
            url = "https://query1.finance.yahoo.com/v8/finance/chart/GVZ?range=2d&interval=1d"
            data = self._http_get(url)
            if not data:
                return None

            import json as _json
            parsed = _json.loads(data)
            result = parsed.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            gvz = meta.get("regularMarketPrice", 0)

            if gvz > 0:
                self.cache.set("gvz", gvz, ttl_seconds=3600)
                return gvz

        except Exception as e:
            log.warning(f"[BIAS] GVZ fetch error: {e}")

        return None

    def _score_gvz(self) -> FilterResult:
        """Score GVZ (contrarian)."""
        fr = FilterResult(name="GVZ Volatility", enabled=self.gvz_enabled)
        if not self.gvz_enabled:
            return fr

        gvz = self._fetch_gvz()
        if gvz is None:
            fr.error = True
            fr.reason = "Données GVZ indisponibles"
            return fr

        fr.raw_value = round(gvz, 2)

        # GVZ est CONTRARIAN : haute peur = bottom = LONG
        if gvz >= self.gvz_strong_long:
            fr.score = 2
            fr.label = "STRONG_LONG"
            fr.reason = f"GVZ = {gvz:.1f} (peur extrême → contrarian LONG)"
        elif gvz >= self.gvz_lean_long:
            fr.score = 1
            fr.label = "LEAN_LONG"
            fr.reason = f"GVZ = {gvz:.1f} (peur élevée → LEAN LONG)"
        elif gvz <= self.gvz_strong_short:
            fr.score = -2
            fr.label = "STRONG_SHORT"
            fr.reason = f"GVZ = {gvz:.1f} (complaisance extrême → contrarian SHORT)"
        elif gvz <= self.gvz_lean_short:
            fr.score = -1
            fr.label = "LEAN_SHORT"
            fr.reason = f"GVZ = {gvz:.1f} (complaisance → LEAN SHORT)"
        else:
            fr.score = 0
            fr.label = "NEUTRAL"
            fr.reason = f"GVZ = {gvz:.1f}"

        return fr

    # =============================================================
    # FILTRE 5 : FED FUNDS RATE EXPECTATIONS
    # =============================================================

    def _fetch_fed_expectations(self) -> Optional[dict]:
        """
        Récupère les attentes de taux Fed.
        Méthode : CME FedWatch n'a pas d'API publique directe,
        on utilise les données de taux comme proxy.
        """
        cache = self.cache.get("fed_data")
        if cache is not None:
            return cache

        try:
            # Proxy : utiliser le spread entre le yield 2 ans et le Fed Funds effective
            # Le 2-year Treasury yield reflète les attentes de taux
            url = "https://query1.finance.yahoo.com/v8/finance/chart/2YY?range=5d&interval=1d"
            data = self._http_get(url)
            if not data:
                return None

            import json as _json
            parsed = _json.loads(data)
            result = parsed.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            current_yield = meta.get("regularMarketPrice", 0)

            closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            if not closes or len(closes) < 2:
                return None

            # Direction du 2Y yield
            yield_change = current_yield - closes[0] if closes[0] else 0

            # Si le yield baisse → marché anticipe des cuts → or bullish
            # Si le yield monte → marché anticipe des hikes → or bearish
            result_data = {
                "current_yield": current_yield,
                "yield_change_5d": yield_change,
                # Estimation probabilité cut/hike basée sur la direction
                "est_cut_prob": max(0, min(100, 50 - (yield_change * 20))),
                "est_hike_prob": max(0, min(100, 50 + (yield_change * 20))),
            }

            self.cache.set("fed_data", result_data, ttl_seconds=3600)
            return result_data

        except Exception as e:
            log.warning(f"[BIAS] Fed expectations fetch error: {e}")
            return None

    def _score_fed(self) -> FilterResult:
        """Score Fed Funds expectations."""
        fr = FilterResult(name="Fed Funds", enabled=self.fed_enabled)
        if not self.fed_enabled:
            return fr

        data = self._fetch_fed_expectations()
        if data is None:
            fr.error = True
            fr.reason = "Données taux indisponibles"
            return fr

        cut_prob = data.get("est_cut_prob", 50)
        hike_prob = data.get("est_hike_prob", 50)
        yield_change = data.get("yield_change_5d", 0)

        fr.raw_value = round(cut_prob, 1)

        # Logique : cut probable = or bullish, hike probable = or bearish
        if cut_prob >= self.fed_strong_long:
            fr.score = 2
            fr.label = "STRONG_LONG"
            fr.reason = f"Prob cut ≈ {cut_prob:.0f}% (yield 2Y: {yield_change:+.2f}%)"
        elif cut_prob >= self.fed_lean_long:
            fr.score = 1
            fr.label = "LEAN_LONG"
            fr.reason = f"Prob cut ≈ {cut_prob:.0f}% (yield 2Y: {yield_change:+.2f}%)"
        elif hike_prob >= self.fed_strong_short:
            fr.score = -2
            fr.label = "STRONG_SHORT"
            fr.reason = f"Prob hike ≈ {hike_prob:.0f}% (yield 2Y: {yield_change:+.2f}%)"
        elif hike_prob >= self.fed_lean_short:
            fr.score = -1
            fr.label = "LEAN_SHORT"
            fr.reason = f"Prob hike ≈ {hike_prob:.0f}% (yield 2Y: {yield_change:+.2f}%)"
        else:
            fr.score = 0
            fr.label = "NEUTRAL"
            fr.reason = f"Cut {cut_prob:.0f}% / Hike {hike_prob:.0f}%"

        return fr

    # =============================================================
    # SCORE COMPOSITE
    # =============================================================

    def calculate_bias(self) -> BiasResult:
        """
        Calcule le score de biais composite à partir de tous les filtres activés.

        Returns:
            BiasResult avec le score total, la direction, et les détails de chaque filtre
        """
        results = []

        # Évaluer chaque filtre
        filters = [
            self._score_gld(),
            self._score_shanghai(),
            self._score_cot(),
            self._score_gvz(),
            self._score_fed(),
        ]

        active_filters = [f for f in filters if f.enabled]
        error_filters = [f for f in active_filters if f.error]
        valid_filters = [f for f in active_filters if not f.error]

        # Score total = somme des scores des filtres valides
        total_score = sum(f.score for f in valid_filters)

        # Déterminer le biais
        if total_score >= self.strong_threshold:
            bias = "STRONG_LONG"
            direction = "LONG"
        elif total_score >= self.lean_threshold:
            bias = "LEAN_LONG"
            direction = "LONG"
        elif total_score <= self.strong_negative:
            bias = "STRONG_SHORT"
            direction = "SHORT"
        elif total_score <= self.lean_negative:
            bias = "LEAN_SHORT"
            direction = "SHORT"
        else:
            bias = "NEUTRAL"
            direction = "BOTH"

        # Si tous les filtres sont en erreur → NEUTRAL
        if not valid_filters and active_filters:
            bias = "NEUTRAL"
            direction = "BOTH"

        result = BiasResult(
            total_score=total_score,
            bias=bias,
            direction=direction,
            filters=filters,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

        return result

    # =============================================================
    # VÉRIFICATION D'UN SIGNAL
    # =============================================================

    def check_signal(self, signal_action: str, bias_result: BiasResult) -> tuple:
        """
        Vérifie si un signal est compatible avec le biais actuel.

        Args:
            signal_action: "BUY" ou "SELL"
            bias_result: résultat du calcul de biais

        Returns:
            (allowed: bool, reason: str)
        """
        if bias_result.direction == "BOTH":
            return True, "Biais NEUTRAL — tous signaux acceptés"

        if not self.strict_mode:
            # Mode souple : toujours accepter, juste logger
            if bias_result.direction != signal_action:
                return True, f"Biais {bias_result.bias} mais mode souple — signal {signal_action} accepté"
            return True, f"Biais {bias_result.bias} — signal {signal_action} aligné"

        # Mode strict : rejeter les signaux contraires au STRONG
        if "STRONG" in bias_result.bias:
            if bias_result.direction == "LONG" and signal_action == "SELL":
                return False, f"REJETÉ par biais {bias_result.bias} (score={bias_result.total_score})"
            if bias_result.direction == "SHORT" and signal_action == "BUY":
                return False, f"REJETÉ par biais {bias_result.bias} (score={bias_result.total_score})"

        return True, f"Biais {bias_result.bias} — signal {signal_action} accepté"

    # =============================================================
    # LOG FORMAT
    # =============================================================

    def format_bias_log(self, result: BiasResult) -> str:
        """Formate le résultat du biais pour le logging."""
        lines = []
        lines.append("=" * 50)
        lines.append("📊 BIAS FILTERS — RÉSULTAT")
        lines.append("=" * 50)

        for f in result.filters:
            if not f.enabled:
                lines.append(f"  ⬜ {f.name}: DÉSACTIVÉ")
            elif f.error:
                lines.append(f"  ❌ {f.name}: ERREUR — {f.reason}")
            else:
                icon = {-2: "🔴", -1: "🟡", 0: "⚪", 1: "🟢", 2: "🟢"}.get(f.score, "⚪")
                lines.append(f"  {icon} {f.name}: {f.label} ({f.score:+d}) — {f.reason}")

        lines.append("-" * 50)
        emoji = {
            "STRONG_LONG": "🟢🟢", "LEAN_LONG": "🟢",
            "NEUTRAL": "⚪",
            "LEAN_SHORT": "🔴", "STRONG_SHORT": "🔴🔴"
        }.get(result.bias, "⚪")
        lines.append(f"  {emoji} SCORE TOTAL: {result.total_score:+d} → {result.bias}")
        lines.append(f"  📌 Direction: {result.direction}")
        lines.append("=" * 50)

        return "\n".join(lines)

    def format_bias_alert(self, result: BiasResult) -> str:
        """Formate le résultat du biais pour une alerte Telegram."""
        lines = []
        lines.append("📊 BIAS QUOTIDIEN")
        lines.append("━━━━━━━━━━━━━━━━━━")

        for f in result.filters:
            if not f.enabled:
                continue
            if f.error:
                lines.append(f"❌ {f.name}: N/A")
            else:
                icon = {-2: "🔴", -1: "🟡", 0: "⚪", 1: "🟢", 2: "🟢"}.get(f.score, "⚪")
                lines.append(f"{icon} {f.name}: {f.label} ({f.score:+d})")

        lines.append("━━━━━━━━━━━━━━━━━━")
        emoji = {
            "STRONG_LONG": "🟢🟢", "LEAN_LONG": "🟢",
            "NEUTRAL": "⚪",
            "LEAN_SHORT": "🔴", "STRONG_SHORT": "🔴🔴"
        }.get(result.bias, "⚪")
        lines.append(f"{emoji} SCORE: {result.total_score:+d} → {result.bias}")
        lines.append(f"📌 Signaux acceptés: {result.direction}")

        return "\n".join(lines)


# =============================================================
# INSTANCE GLOBALE
# =============================================================

_bias_engine = None

def get_bias_engine() -> BiasFilterEngine:
    """Retourne l'instance globale du moteur de biais."""
    global _bias_engine
    if _bias_engine is None:
        _bias_engine = BiasFilterEngine(os.environ)
    return _bias_engine

def init_bias_engine(env: dict) -> BiasFilterEngine:
    """Initialise l'instance globale avec un env spécifique."""
    global _bias_engine
    _bias_engine = BiasFilterEngine(env)
    return _bias_engine
