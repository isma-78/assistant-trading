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
   statistique de l'alignement entre le biais déclaré et un biais du corps
   inféré par heuristique, utile pour la variable #1 du §3.8 et pour
   repérer les contradictions internes du canal (§3.4), plus les niveaux
   techniques cités par actif (prix, zone FVG, Fibonacci). La détection du
   biais du corps est une heuristique de premier niveau (motif
   "reste donc <mot>") : en cas de doute, elle renvoie "indetermine"
   plutôt que de trancher au hasard. Le biais déclaré lui-même est
   capturé sous deux libellés possibles selon le format observé du canal —
   "Sentiment X" (§3.4 littéral) ou "Biais X." (format réel observé depuis
   le 20/08/2026, voir docs/DECISIONS.md) — cohérent avec le principe du
   projet de journaliser les contradictions sans jamais les arbitrer
   soi-même.

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
    sentiment_tag: Optional[str]            # "haussier" | "baissier" | "neutre" | None — voir docs/DECISIONS.md (20/08/2026) : capture "Sentiment X" (legacy) OU "Biais X." (format réel observé depuis), quel que soit le libellé employé par le canal
    contradiction_detectee: bool
    # Champs numériques ajoutés le 20/08/2026, calibrés sur un exemple réel
    # (voir docs/DECISIONS.md) — tous Optional : None si la phrase attendue
    # n'apparaît pas dans ce bloc (jamais devinés).
    prix_courant: Optional[float] = None
    zone_depart_min: Optional[float] = None
    zone_depart_max: Optional[float] = None
    niveau_majeur: Optional[float] = None
    fvg_haut: Optional[float] = None
    fvg_bas: Optional[float] = None
    fib_50: Optional[float] = None
    fib_618: Optional[float] = None
    fib_786: Optional[float] = None


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

# Nombre avec séparateur de milliers = espace/espace insécable ("91 950"),
# formatage réel du canal — un \d+ simple ne capturait que les chiffres
# avant le premier espace (91 950 -> "91"), corrompant silencieusement le
# prix. Gère aussi le cas sans séparateur (4367) et la partie décimale
# (comma ou point). Voir docs/DECISIONS.md.
_NUMBER = r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?"

_STRUCTURED_SIGNAL = re.compile(
    r"\b(?P<action>je\s+vends?|vends?|vente|ach[eè]te?|achat)\b\s+"
    # Les tickers d'indices contiennent des chiffres (NAS100, US30, US100) —
    # la classe de caractères doit les inclure, sinon le regex échoue en
    # silence sur ces actifs (voir docs/DECISIONS.md).
    r"(?P<asset>[A-Za-zÀ-ÿ0-9/]{2,12})\s+"
    rf"à\s+(?P<price>{_NUMBER})",
    re.IGNORECASE,
)
_ACTION_ONLY = re.compile(r"\b(je\s+vends?|vends?|vente|ach[eè]te?|achat)\b", re.IGNORECASE)
_TP_LINE = re.compile(rf"\bTP\s*([123])\s*:\s*(ouvert|open|{_NUMBER})", re.IGNORECASE)
_SL_LINE = re.compile(rf"\bSL\s*:\s*({_NUMBER})", re.IGNORECASE)


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
# "Biais haussier." : tag de fin de paragraphe observé sur le format réel du
# canal depuis (au moins) le 20/08/2026 — voir docs/DECISIONS.md. Le champ
# "Sentiment X" du §3.4 du CDC n'apparaît plus dans cet exemple ; ce tag
# semble en être le libellé actuel, alimente donc la même donnée
# (sentiment_tag) plutôt qu'un champ séparé, en repli si "Sentiment X" est
# absent.
_BIAIS_TAG = re.compile(r"\bbiais\s+(haussier|baissier|neutre)\b", re.IGNORECASE)

# Champs numériques par actif, calibrés le 20/08/2026 sur un exemple réel
# (Bitcoin + Gold, docs/DECISIONS.md) — un seul exemple disponible à ce
# jour, à ajuster si un futur message révèle une formulation différente.
_MATINALE_PRIX_COURANT = re.compile(rf"[ée]volue\s+actuellement\s+autour\s+des\s+({_NUMBER})\s*\$", re.IGNORECASE)
_MATINALE_ZONE_DEPART = re.compile(
    rf"parti\s+de\s+la\s+zone\s+des\s+({_NUMBER})\s*\$\s*/\s*({_NUMBER})\s*\$", re.IGNORECASE
)
# "...situé à 70 048 $, qui représente le niveau majeur..." — la distance
# entre le prix et la mention "niveau majeur" est bornée ([^.]{0,80}, pas de
# point entre les deux) pour ne pas capturer un prix sans rapport plus loin
# dans le paragraphe.
_MATINALE_NIVEAU_MAJEUR = re.compile(
    rf"situ[ée]e?\s+[àa]\s+({_NUMBER})\s*\$[^.]{{0,80}}niveau\s+majeur", re.IGNORECASE | re.DOTALL
)
# Deux formulations réelles observées pour la zone FVG : bornes haute et
# basse données ensemble ("FVG... approximativement entre X $ et Y $",
# exemple Gold) OU seulement la borne haute ("partie haute se situe autour
# des X $", exemple Bitcoin) — la borne basse de ce second cas n'est
# extraite que si le texte la relie explicitement au bas de la zone
# ("X $ correspond[ent]... au bas de la zone"), jamais déduite par défaut
# du niveau de Fibonacci le plus profond (pas de lien textuel explicite,
# donc pas d'extraction plutôt qu'une supposition).
_MATINALE_FVG_ENTRE = re.compile(
    rf"FVG[+-]?.{{0,40}}?approximativement\s+entre\s+({_NUMBER})\s*\$\s*et\s+({_NUMBER})\s*\$",
    re.IGNORECASE | re.DOTALL,
)
_MATINALE_FVG_HAUT_SEUL = re.compile(rf"partie\s+haute\s+se\s+situe\s+autour\s+des\s+({_NUMBER})\s*\$", re.IGNORECASE)
_MATINALE_FVG_BAS_CORRESPOND = re.compile(
    rf"({_NUMBER})\s*\$\s*correspond\w*.{{0,30}}?bas\s+de\s+la\s+zone", re.IGNORECASE | re.DOTALL
)
# Niveaux de Fibonacci : "<pct> % à <prix> $", classés par la valeur du
# pourcentage (tolérance 0.5 pour absorber l'arrondi de saisie du canal),
# jamais par leur ordre d'apparition dans le texte.
_MATINALE_FIB = re.compile(rf"({_NUMBER})\s*%\s*[àa]\s+({_NUMBER})\s*\$", re.IGNORECASE)
_FIB_RATIOS = {50.0: "fib_50", 61.8: "fib_618", 78.6: "fib_786"}


def _classify_biais_phrase(phrase: str) -> str:
    phrase_lower = phrase.lower()
    if any(p in phrase_lower for p in _BEARISH_PHRASES):
        return "baissier"
    if any(p in phrase_lower for p in _BULLISH_PHRASES):
        return "haussier"
    if any(p in phrase_lower for p in _NEUTRAL_PHRASES):
        return "neutre"
    return "indetermine"


def _split_asset_paragraphs(text: str) -> List[str]:
    """Segmente le texte en un bloc par actif détecté ("Du côté du <actif>
    en <horizon>"). Chaque bloc s'étend jusqu'au début du bloc actif
    suivant ; le dernier bloc s'arrête juste après son propre tag de biais
    ("Biais X." ou "Sentiment X") pour ne jamais déborder sur un paragraphe
    de clôture ou d'annonces macro qui le suivrait, sans tag trouvé (message
    malformé), il s'étend jusqu'à la fin du texte — comportement défensif
    identique à l'ancien découpage par "✅".

    Remplace l'ancien découpage par séparateur visuel "✅" (docs/DECISIONS.md,
    20/08/2026) : l'exemple réel calibré ce jour-là n'utilise pas cet
    émoji — ancrer sur la position des en-têtes de bloc eux-mêmes est
    indépendant de toute convention de mise en forme du canal."""
    headers = list(_ASSET_BLOCK_HEADER.finditer(text))
    paragraphs = []
    for i, header in enumerate(headers):
        start = header.start()
        if i + 1 < len(headers):
            end = headers[i + 1].start()
        else:
            remainder = text[start:]
            tag_match = _BIAIS_TAG.search(remainder) or _SENTIMENT_TAG_MATINALE.search(remainder)
            end = start + tag_match.end() if tag_match else len(text)
        paragraphs.append(text[start:end])
    return paragraphs


def _extract_fib_levels(paragraph: str) -> dict:
    levels = {}
    for match in _MATINALE_FIB.finditer(paragraph):
        pct = _parse_number(match.group(1))
        price = _parse_number(match.group(2))
        for ratio, field in _FIB_RATIOS.items():
            if abs(pct - ratio) < 0.5:
                levels[field] = price
                break
    return levels


def _extract_fvg_zone(paragraph: str) -> tuple:
    entre_match = _MATINALE_FVG_ENTRE.search(paragraph)
    if entre_match:
        return _parse_number(entre_match.group(1)), _parse_number(entre_match.group(2))

    haut_match = _MATINALE_FVG_HAUT_SEUL.search(paragraph)
    fvg_haut = _parse_number(haut_match.group(1)) if haut_match else None

    bas_match = _MATINALE_FVG_BAS_CORRESPOND.search(paragraph)
    fvg_bas = _parse_number(bas_match.group(1)) if bas_match else None

    return fvg_bas, fvg_haut


def extract_matinale(text: str) -> MatinaleExtraction:
    """Extrait, pour chaque actif mentionné dans une Matinale : biais du
    corps (heuristique "reste donc X", historique — voir _split_asset_
    paragraphs pour le découpage), tag de biais déclaré ("Sentiment X" ou
    "Biais X.", quel que soit le libellé du canal), contradiction entre les
    deux, et les niveaux techniques cités (prix courant, zone de départ du
    mouvement, niveau majeur, zone FVG, niveaux de Fibonacci) — calibré le
    20/08/2026 sur un exemple réel (Bitcoin + Gold, docs/DECISIONS.md).

    Ne lève jamais d'exception : un actif non résolu ou un champ absent du
    texte reste simplement None plutôt que de faire échouer tout le
    message (fail-safe, invariant #7 — même patron que le reste du
    module)."""
    summaries = []

    for paragraph in _split_asset_paragraphs(text):
        header = _ASSET_BLOCK_HEADER.search(paragraph)
        if not header:
            continue  # défensif, ne devrait pas arriver (paragraphe déjà ancré sur un header)

        raw_asset = header.group("asset").strip()
        asset = _resolve_asset(raw_asset)

        biais_match = _BIAIS_PHRASE.search(paragraph)
        biais_corps = _classify_biais_phrase(biais_match.group(1)) if biais_match else "indetermine"

        tag_match = _SENTIMENT_TAG_MATINALE.search(paragraph) or _BIAIS_TAG.search(paragraph)
        sentiment_tag = tag_match.group(1).lower() if tag_match else None

        contradiction = bool(
            sentiment_tag
            and biais_corps in ("haussier", "baissier", "neutre")
            and biais_corps != sentiment_tag
        )

        prix_match = _MATINALE_PRIX_COURANT.search(paragraph)
        prix_courant = _parse_number(prix_match.group(1)) if prix_match else None

        zone_match = _MATINALE_ZONE_DEPART.search(paragraph)
        zone_depart_min = _parse_number(zone_match.group(1)) if zone_match else None
        zone_depart_max = _parse_number(zone_match.group(2)) if zone_match else None

        niveau_match = _MATINALE_NIVEAU_MAJEUR.search(paragraph)
        niveau_majeur = _parse_number(niveau_match.group(1)) if niveau_match else None

        fvg_bas, fvg_haut = _extract_fvg_zone(paragraph)
        fib_levels = _extract_fib_levels(paragraph)

        summaries.append(
            MatinaleAssetSummary(
                raw_asset_mention=raw_asset,
                asset=asset,
                biais_corps=biais_corps,
                sentiment_tag=sentiment_tag,
                contradiction_detectee=contradiction,
                prix_courant=prix_courant,
                zone_depart_min=zone_depart_min,
                zone_depart_max=zone_depart_max,
                niveau_majeur=niveau_majeur,
                fvg_haut=fvg_haut,
                fvg_bas=fvg_bas,
                fib_50=fib_levels.get("fib_50"),
                fib_618=fib_levels.get("fib_618"),
                fib_786=fib_levels.get("fib_786"),
            )
        )

    return MatinaleExtraction(assets=summaries)
