"""
message_classifier.py — Classification déterministe des messages Telegram
du canal Station X, en 4 catégories exclusives (CDC v4 §3.x).

Catégories :
- "matinale" : point marché quotidien, un paragraphe par actif se terminant
  par un tag de biais déclaré — "Sentiment {haussier|baissier|neutre}"
  (§3.4 littéral) ou "Biais {haussier|baissier|neutre}." (libellé réel
  observé depuis le 20/08/2026, voir docs/DECISIONS.md)
- "signal"   : appel à l'ordre — alerte courte ("VENTE XAUUSD NOW !") ou
  message structuré ("JE VENDS XAUUSD à 4367" + niveaux TP/SL).
- "suivi"    : mise à jour d'un signal déjà envoyé (stop touché, TP touché,
  résultat en pips), typiquement en réponse au message de signal d'origine.
- "autre"    : tout le reste, y compris les bilans auto-déclarés par le canal
  ("Bilan trading du jour...", "BILAN TRADES : 1/2") — ces bilans ne doivent
  jamais alimenter nos métriques (§3.10, on calcule les nôtres,
  indépendamment et déterministe) : ils sont classés "autre" et archivés
  bruts, sans traitement supplémentaire.

Aucun LLM ici : classification 100% déterministe par motifs de texte,
testée unitairement (invariants #1 et #2 du projet). Un LLM peut être
utilisé en aval pour traduire/expliquer, jamais pour décider de la
catégorie d'un message susceptible de mener à un ordre.
"""

import re
from enum import Enum


class MessageCategory(str, Enum):
    MATINALE = "matinale"
    SIGNAL = "signal"
    SUIVI = "suivi"
    AUTRE = "autre"


# --- Bilans auto-déclarés du canal : toujours "autre", priorité sur tout le
# reste (un bilan peut mentionner "VENTE" et "+100PIPS" sans être un signal
# ou un suivi exploitable — §3.10).
_BILAN_MARKERS = (
    re.compile(r"\bbilan\b", re.IGNORECASE),
)

# --- Matinale ---
_MATINALE_MARKERS = (
    re.compile(r"\bmatinale\b", re.IGNORECASE),
    re.compile(r"\bpoint march[ée]\b", re.IGNORECASE),
)
_MATINALE_ASSET_BLOCK = re.compile(
    r"du c[ôo]t[ée] du .+ en (?:daily|h4|h1|weekly)", re.IGNORECASE
)
_SENTIMENT_TAG = re.compile(r"\bsentiment\s+(haussier|baissier|neutre)\b", re.IGNORECASE)
# "Biais haussier." : libellé du tag de fin de paragraphe observé sur le
# format réel du canal depuis (au moins) le 20/08/2026 (voir
# docs/DECISIONS.md) — le mot "Sentiment" n'apparaît plus dans cet exemple.
# Sans ce repli, un message de ce format sans le mot "Matinale"/"point
# marché" explicite ailleurs échouerait silencieusement à être classé
# "matinale" (bug réel trouvé en calibrant parser.extract_matinale() sur
# cet exemple).
_BIAIS_TAG = re.compile(r"\bbiais\s+(haussier|baissier|neutre)\b", re.IGNORECASE)

# --- Signal ---
# Message structuré (avec prix d'entrée explicite après "à") : la marque la
# plus fiable qu'il s'agit d'un signal complet, extractible.
_SIGNAL_ACTION = re.compile(r"\b(?:je\s+vends?|vends?|vente|ach[eè]te?|achat)\b", re.IGNORECASE)
_SIGNAL_ENTRY_PRICE = re.compile(r"\bà\s+\d", re.IGNORECASE)
_SIGNAL_NOW = re.compile(r"\bnow\b", re.IGNORECASE)

# --- Suivi ---
_SUIVI_TOUCHED = re.compile(r"\btouch[ée]", re.IGNORECASE)
_SUIVI_PIPS = re.compile(r"[+-]\s?\d+\s*pips?\b", re.IGNORECASE)
_SUIVI_SL_RESULT = re.compile(r"\bsl\b\s*[+-]\s?\d+\s*pips?\b", re.IGNORECASE)


def classify(text: str) -> MessageCategory:
    """Classe un message brut du canal en une des 4 catégories. Ne lève
    jamais d'exception : un texte vide ou inattendu tombe dans "autre"."""
    if not text or not text.strip():
        return MessageCategory.AUTRE

    if _matches_any(text, _BILAN_MARKERS):
        return MessageCategory.AUTRE

    if _looks_like_matinale(text):
        return MessageCategory.MATINALE

    if _looks_like_suivi(text):
        return MessageCategory.SUIVI

    if _looks_like_signal(text):
        return MessageCategory.SIGNAL

    return MessageCategory.AUTRE


def _matches_any(text: str, patterns) -> bool:
    return any(p.search(text) for p in patterns)


def _looks_like_matinale(text: str) -> bool:
    if _matches_any(text, _MATINALE_MARKERS):
        return True
    # Sans le mot "Matinale" explicite : au moins un bloc actif structuré
    # ("Du côté du X en Daily/H4") accompagné d'un tag de biais déclaré
    # suffit — "Sentiment X" (§3.4 littéral) ou "Biais X." (format réel
    # observé depuis le 20/08/2026), quel que soit le libellé du canal.
    return bool(
        _MATINALE_ASSET_BLOCK.search(text)
        and (_SENTIMENT_TAG.search(text) or _BIAIS_TAG.search(text))
    )


def _is_structured_signal(text: str) -> bool:
    """Signal complet : verbe d'action + prix d'entrée explicite après "à"."""
    return bool(_SIGNAL_ACTION.search(text) and _SIGNAL_ENTRY_PRICE.search(text))


def _looks_like_suivi(text: str) -> bool:
    # Un message structuré avec prix d'entrée est toujours un signal, jamais
    # un suivi, même s'il contient par ailleurs des niveaux TP/SL.
    if _is_structured_signal(text):
        return False
    return bool(
        _SUIVI_TOUCHED.search(text) or _SUIVI_PIPS.search(text) or _SUIVI_SL_RESULT.search(text)
    )


def _looks_like_signal(text: str) -> bool:
    if _is_structured_signal(text):
        return True
    # Alerte courte sans prix ("VENTE XAUUSD NOW !") : action + "NOW".
    return bool(_SIGNAL_ACTION.search(text) and _SIGNAL_NOW.search(text))
