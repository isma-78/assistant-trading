"""
parser.py — Extraction déterministe des champs structurés depuis les
messages du canal Station X, une fois catégorisés par message_classifier.

Deux régimes de fiabilité, volontairement séparés :

1. extract_signal() et extract_suivi() produisent des chiffres qui peuvent,
   plus loin dans le pipeline, alimenter risk_engine.evaluate_new_entry
   (prix d'entrée, stop, take-profits). Ce sont donc des calculs
   déterministes purs, sans LLM (invariants #1, #2 du projet), testés sur
   des exemples réels du canal. Aucun LLM n'a accès à ces chiffres avant
   que risk_engine ne les valide.

2. extract_matinale() ne produit rien qui déclenche un ordre directement
   (aucun signal n'en découle jamais — voir §3.8) : c'est une observation
   statistique de l'alignement entre le biais déclaré dans le corps du
   texte et le tag "Sentiment" affiché, utile pour la variable #1 du §3.8
   et pour repérer les contradictions internes du canal (§3.4). La
   détection du biais du corps est une heuristique de premier niveau
   (motif "reste donc <mot>") : en cas de doute, elle renvoie
   "indetermine" plutôt que de trancher au hasard — cohérent avec le
   principe du projet de journaliser les contradictions sans jamais les
   arbitrer soi-même.

extraction_status sur SignalExtraction vaut "ok" seulement si asset,
direction, entry_price et stop_price sont tous résolus ; sinon
"incomplete". Un stop absent est de toute façon refusé plus loin par
risk_engine (RiskRejectionReason.STOP_MISSING) : extraction_status est une
indication précoce, pas une garde de sécurité supplémentaire.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from src.asset_whitelist import ASSET_WHITELIST

# Alias en langage naturel / tickers courants du canal -> symbole de la
# liste blanche (src/asset_whitelist.py). Toutes les valeurs sont vérifiées
# par test contre ASSET_WHITELIST pour éviter une dérive silencieuse.
ASSET_ALIASES = {
    "btcusd": "BTCUSD", "btc": "BTCUSD", "bitcoin": "BTCUSD",
    "ethusd": "ETHUSD", "eth": "ETHUSD", "ethereum": "ETHUSD",
    "xauusd": "GOLD", "gold": "GOLD", "or": "GOLD",
    "us100": "US100", "nasdaq": "US100", "nas100": "US100",
    "us30": "US30", "dowjones": "US30", "dow": "US30",
    "eurusd": "EURUSD",
    "gbpusd": "GBPUSD",
    "usdjpy": "USDJPY",
}

_BEARISH_PHRASES = ("sous pression", "baissier", "fragile", "vulnerable", "vulnérable", "negatif", "négatif", "faible")
_BULLISH_PHRASES = ("solide", "haussier", "robuste", "resilient", "résilient", "constructif", "positif", "vigoureux")
_NEUTRAL_PHRASES = ("stable", "neutre", "indecis", "indécis")


@dataclass(frozen=True)
class SignalExtraction:
    raw_asset_mention: Optional[str]
    asset: Optional[str]                    # symbole liste blanche, None si non résolu
    direction: Optional[str]                # "long" | "short" | None
    entry_price: Optional[float]
    stop_price: Optional[float]
    take_profits: List[Optional[float]]     # [tp1, tp2, tp3] ; None = absent ou "Ouvert"
    reply_to_msg_id: Optional[int]
    extraction_status: str                  # "ok" | "incomplete"


@dataclass(frozen=True)
class SuiviExtraction:
    event: str                              # "sl_hit" | "tp1_hit" | "tp2_hit" | "tp3_hit" | "update"
    pips: Optional[float]
    reply_to_msg_id: Optional[int]
    raw_text: str


@dataclass(frozen=True)
class MatinaleAssetSummary:
    raw_asset_mention: str
    asset: Optional[str]                    # symbole liste blanche, None si non résolu
    biais_corps: str                        # "haussier" | "baissier" | "neutre" | "indetermine"
    sentiment_tag: Optional[str]            # "haussier" | "baissier" | "neutre" | None
    contradiction_detectee: bool


@dataclass(frozen=True)
class MatinaleExtraction:
    assets: List[MatinaleAssetSummary]


def _parse_number(raw: str) -> float:
    cleaned = re.sub(r"[\s  ]", "", raw.strip())
    cleaned = cleaned.rstrip("$€£")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def _resolve_asset(raw_mention: str) -> Optional[str]:
    key = re.sub(r"[^a-z0-9]", "", raw_mention.strip().lower())
    return ASSET_ALIASES.get(key)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

_STRUCTURED_SIGNAL = re.compile(
    r"\b(?P<action>je\s+vends?|vends?|vente|ach[eè]te?|achat)\b\s+"
    r"(?P<asset>[A-Za-zÀ-ÿ/]{2,12})\s+"
    r"à\s+(?P<price>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_ACTION_ONLY = re.compile(r"\b(je\s+vends?|vends?|vente|ach[eè]te?|achat)\b", re.IGNORECASE)
_TP_LINE = re.compile(r"\bTP\s*([123])\s*:\s*(ouvert|open|\d+(?:[.,]\d+)?)", re.IGNORECASE)
_SL_LINE = re.compile(r"\bSL\s*:\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def _direction_from_action(action: str) -> Optional[str]:
    # action peut être préfixé ("je vends") : recherche de sous-chaîne, pas
    # startswith, sur un texte déjà restreint à l'alternation _ACTION_ONLY.
    action_lower = action.strip().lower()
    if "vend" in action_lower or "vente" in action_lower:
        return "short"
    if "ach" in action_lower:
        return "long"
    return None


def _find_asset_anywhere(text: str) -> Optional[tuple]:
    """Recherche un alias d'actif n'importe où dans le texte, en bordure de
    mot (évite les faux positifs de type "or" trouvé dans "correction")."""
    for alias in sorted(ASSET_ALIASES, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE)
        if match:
            return match.group(0), ASSET_ALIASES[alias]
    return None


def extract_signal(text: str, reply_to_msg_id: Optional[int] = None) -> SignalExtraction:
    """Extrait un signal depuis un message déjà classifié "signal". Gère
    aussi bien le message structuré complet ("JE VENDS XAUUSD à 4367" +
    TP/SL) que l'alerte courte préalable ("VENTE XAUUSD NOW !"), auquel cas
    l'essentiel des champs reste None et extraction_status="incomplete"."""
    match = _STRUCTURED_SIGNAL.search(text)
    if match:
        raw_asset = match.group("asset")
        asset = _resolve_asset(raw_asset)
        direction = _direction_from_action(match.group("action"))
        entry_price = _parse_number(match.group("price"))
        stop_match = _SL_LINE.search(text)
        stop_price = _parse_number(stop_match.group(1)) if stop_match else None
        take_profits = _extract_take_profits(text)
    else:
        found = _find_asset_anywhere(text)
        raw_asset, asset = found if found else (None, None)
        action_match = _ACTION_ONLY.search(text)
        direction = _direction_from_action(action_match.group(1)) if action_match else None
        entry_price = None
        stop_price = None
        take_profits = [None, None, None]

    status = "ok" if asset and direction and entry_price is not None and stop_price is not None else "incomplete"

    return SignalExtraction(
        raw_asset_mention=raw_asset,
        asset=asset,
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profits=take_profits,
        reply_to_msg_id=reply_to_msg_id,
        extraction_status=status,
    )


def _extract_take_profits(text: str) -> List[Optional[float]]:
    tps: List[Optional[float]] = [None, None, None]
    for match in _TP_LINE.finditer(text):
        idx = int(match.group(1)) - 1
        value = match.group(2).strip().lower()
        tps[idx] = None if value in ("ouvert", "open") else _parse_number(value)
    return tps


# ---------------------------------------------------------------------------
# Suivi
# ---------------------------------------------------------------------------

_PIPS_PATTERN = re.compile(r"([+-])\s?(\d+(?:[.,]\d+)?)\s*pips?\b", re.IGNORECASE)
_TP_TOUCHED = re.compile(r"\bTP\s*([123])\b.*?\btouch[ée]", re.IGNORECASE | re.DOTALL)
_TOUCHED_TP = re.compile(r"touch[ée].{0,20}?\bTP\s*([123])\b", re.IGNORECASE | re.DOTALL)
_SL_WORD = re.compile(r"\bSL\b", re.IGNORECASE)


def _extract_pips(text: str) -> Optional[float]:
    match = _PIPS_PATTERN.search(text)
    if not match:
        return None
    sign, value = match.groups()
    signed = _parse_number(value)
    return -signed if sign == "-" else signed


def extract_suivi(text: str, reply_to_msg_id: Optional[int] = None) -> SuiviExtraction:
    """Extrait un événement de suivi (SL touché, TPn touché, mise à jour
    générique) depuis un message déjà classifié "suivi"."""
    pips = _extract_pips(text)

    tp_match = _TP_TOUCHED.search(text) or _TOUCHED_TP.search(text)
    if tp_match:
        event = f"tp{tp_match.group(1)}_hit"
    elif _SL_WORD.search(text):
        event = "sl_hit"
    else:
        event = "update"

    return SuiviExtraction(event=event, pips=pips, reply_to_msg_id=reply_to_msg_id, raw_text=text)


# ---------------------------------------------------------------------------
# Matinale
# ---------------------------------------------------------------------------

_ASSET_BLOCK_HEADER = re.compile(
    r"du c[ôo]t[ée] du\s+(?P<asset>.+?)\s+en\s+(?:daily|h4|h1|weekly)", re.IGNORECASE
)
_BIAIS_PHRASE = re.compile(r"reste\s+donc\s+([^.,;]+)", re.IGNORECASE)
_SENTIMENT_TAG_MATINALE = re.compile(r"\bsentiment\s+(haussier|baissier|neutre)\b", re.IGNORECASE)


def _classify_biais_phrase(phrase: str) -> str:
    phrase_lower = phrase.lower()
    if any(p in phrase_lower for p in _BEARISH_PHRASES):
        return "baissier"
    if any(p in phrase_lower for p in _BULLISH_PHRASES):
        return "haussier"
    if any(p in phrase_lower for p in _NEUTRAL_PHRASES):
        return "neutre"
    return "indetermine"


def extract_matinale(text: str) -> MatinaleExtraction:
    """Extrait, pour chaque actif mentionné dans une Matinale, un résumé
    léger (biais du corps, tag Sentiment déclaré, contradiction entre les
    deux). Segmente le message en paragraphes délimités par "✅" : chaque
    paragraphe qui commence par "Du côté du <actif> en <horizon>" est traité
    comme un bloc actif, les autres (annonces économiques, clôture...) sont
    ignorés."""
    paragraphs = re.split(r"(?:^|\n)\s*✅\s*", text)
    summaries = []

    for paragraph in paragraphs:
        header = _ASSET_BLOCK_HEADER.search(paragraph)
        if not header:
            continue

        raw_asset = header.group("asset").strip()
        asset = _resolve_asset(raw_asset)

        biais_match = _BIAIS_PHRASE.search(paragraph)
        biais_corps = _classify_biais_phrase(biais_match.group(1)) if biais_match else "indetermine"

        tag_match = _SENTIMENT_TAG_MATINALE.search(paragraph)
        sentiment_tag = tag_match.group(1).lower() if tag_match else None

        contradiction = bool(
            sentiment_tag
            and biais_corps in ("haussier", "baissier", "neutre")
            and biais_corps != sentiment_tag
        )

        summaries.append(
            MatinaleAssetSummary(
                raw_asset_mention=raw_asset,
                asset=asset,
                biais_corps=biais_corps,
                sentiment_tag=sentiment_tag,
                contradiction_detectee=contradiction,
            )
        )

    return MatinaleExtraction(assets=summaries)
