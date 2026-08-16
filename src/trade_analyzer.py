"""
trade_analyzer.py — Analyse structurée systématique de chaque trade clos
(palier P2, demande explicite d'Ismaël). Correspond au module
`post_trade_review` du CDC (§4.4 : Déterministe="Mixte", §3.10) —
renommé ici pour rester fidèle au vocabulaire de la demande, mêmes
garde-fous.

Deux parties strictement séparées dans le code ET dans le schéma DB
(table `trade_analysis`), pour qu'aucune confusion ne soit possible
entre elles (invariant #9) :

1. compute_trade_features() : déterministe, calculée, jamais par un LLM.
   Lit r_multiple_total déjà calculé par executor.py (compute_r_multiple
   dans risk_engine.py) — ne le recalcule JAMAIS, voir docs/DECISIONS.md.
   Matière première pour un futur module de raffinement statistique —
   NE PAS CONSTRUIT ICI (n'aura de sens qu'au seuil de l'invariant #10 :
   10 trades minimum par variable, 5 variables maximum, hypothèse
   théorique écrite avant observation des données, §3.8). Ce module se
   contente de garantir que le logging est assez riche et structuré pour
   le permettre plus tard.

2. generate_narrative_summary() : LLM (Anthropic, modèle rapide et
   économique — §3.1 : "Revue post-trade... tâche cadrée, volume
   significatif"), texte descriptif SEULEMENT. Interdiction stricte
   appliquée à DEUX niveaux : le prompt système l'instruit explicitement,
   ET un filtre de sortie déterministe (_check_narrative_guardrail)
   rejette tout texte contenant un motif de jugement/recommandation
   avant qu'il ne soit stocké ou notifié — jamais une confiance aveugle
   dans le seul prompt (§3.10 : "systématiquement étiquetée hypothèse
   non confirmée... ne déclenche aucun ajustement"). Le LLM traduit et
   explique, il ne juge jamais (invariant #9). Reçoit les données déjà
   calculées par la partie 1, ne les produit jamais lui-même.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.audit_notifier import send_notification
from src.db import connection_scope

logger = logging.getLogger(__name__)

# Modèle rapide et économique (§3.1 du CDC : la revue post-trade est une
# tâche cadrée à volume significatif, pas un raisonnement complexe —
# Claude "pleine puissance" est réservé à l'analyse causale au
# coupe-circuit et à la génération d'hypothèses trimestrielle, hors
# périmètre P2).
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "Tu résumes factuellement un trade déjà clos, pour une relecture humaine "
    "par le porteur du projet. Tu reçois des données déjà calculées de façon "
    "déterministe (prix, R-multiple, paliers touchés, durée) : ne recalcule "
    "rien, ne modifie aucun chiffre, n'invente aucun fait absent des données "
    "fournies.\n\n"
    "INTERDICTION ABSOLUE, sans exception : ne jamais juger la stratégie, "
    "ne jamais recommander une action future, ne jamais évaluer la "
    "performance ou qualifier le trade (\"bon\", \"mauvais\", \"aurait dû\", "
    "\"il faudrait\", etc.). Décris uniquement, dans l'ordre chronologique, "
    "ce qui s'est passé et avec quels chiffres. Ce texte est une "
    "hypothèse non confirmée, jamais une conclusion."
)

# Filtre de sortie déterministe : dernière ligne de défense si le LLM
# ignore malgré tout l'instruction système (invariant #9 — le jugement
# ne doit jamais atteindre la base de données ni une notification, même
# une fois). Liste non exhaustive par construction : à enrichir si un
# nouveau motif de jugement est observé en production, jamais retirée.
_FORBIDDEN_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bdevrait\b", r"\baurait[\s-]d[uû]\b", r"\bil (faudrait|faut)\b",
        r"\brecommand\w*", r"\bconseill\w*", r"\bsugg[eè]r\w*",
        r"\b(bonne?|mauvaise?)\s+(d[ée]cision|entr[ée]e|sortie|strat[ée]gie|trade)\b",
        # fenêtre large ([^.]{0,40}) : couvre "la stratégie s'est révélée
        # gagnante" autant que "stratégie gagnante" — le jugement porte
        # sur la validité systémique de la stratégie, pas sur le simple
        # constat factuel qu'un trade a été gagnant/perdant (vocabulaire
        # neutre du CDC lui-même, §2.3).
        r"\bstrat[ée]gie\b[^.]{0,40}\b(valide|invalide|gagnante?|perdante?|efficace|inefficace|r[ée]v[ée]l[ée]e)\b",
        r"\berreur\s+(de|d')\s*(strat[ée]gie|jugement)\b",
        r"\b[àa]\s+l['\s]avenir\b", r"\bla prochaine fois\b",
    ]
]


class NarrativeGuardrailError(RuntimeError):
    """Levée quand le texte généré contient un motif de jugement interdit
    (invariant #9). Le texte n'est alors ni stocké ni notifié."""


def _check_narrative_guardrail(text: str) -> None:
    for pattern in _FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            raise NarrativeGuardrailError(
                f"Résumé rejeté : motif de jugement détecté ({match.group(0)!r}) — "
                "jamais stocké ni notifié (invariant #9)."
            )


# ---------------------------------------------------------------------------
# Partie 1 — déterministe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeFeatures:
    trade_id: int
    signal_id: Optional[int]
    r_multiple_realise: float
    denouement: str              # "sl_hit" | "tp1_hit" | "tp2_hit" | "tp3_hit" | "cloture_manuelle"
    duree_secondes: int
    ecart_signal_execution: Optional[float]
    actif: str
    confiance_signal: Optional[float]
    heure_ouverture: int         # 0-23, UTC
    jour_semaine: int            # 0=lundi ... 6=dimanche (datetime.weekday())
    boosted: bool


def compute_trade_features(trade_row, last_partial_row, signal_row=None) -> TradeFeatures:
    """`trade_row` : ligne de `trades`, déjà fermée (statut='ferme').
    `last_partial_row` : la ligne de `trade_partials` qui a clos le
    dernier fragment restant — détermine le dénouement final (un trade
    peut se terminer sur SL, TP1, TP2 ou TP3/trailing selon où le prix
    s'est arrêté). `signal_row` : ligne de `signals` d'origine,
    optionnelle (None si le trade n'est pas issu d'un signal Station X).

    Ne recalcule jamais r_multiple_total (déjà calculé par executor.py
    via risk_engine.compute_weighted_r_multiple) — le lit tel quel."""
    ouvert_at = datetime.fromisoformat(trade_row["ouvert_at"])
    ferme_at = datetime.fromisoformat(trade_row["ferme_at"])
    duree = int((ferme_at - ouvert_at).total_seconds())

    ecart = None
    if trade_row["prix_entree_reel"] is not None and trade_row["prix_entree_prevu"] is not None:
        ecart = round(trade_row["prix_entree_reel"] - trade_row["prix_entree_prevu"], 8)

    palier = last_partial_row["palier"]
    denouement = "sl_hit" if palier == "sl" else f"{palier}_hit"

    # "boosted" (§2.3) : le pourcentage de risque appliqué est 4% plutôt
    # que 2% par défaut — reconstruit depuis le pourcentage réellement
    # appliqué au trade (pas une nouvelle donnée à faire remonter
    # d'ailleurs), avec une tolérance pour l'arrondi.
    boosted = trade_row["pourcentage_risque_applique"] is not None and trade_row["pourcentage_risque_applique"] > 3.0

    return TradeFeatures(
        trade_id=trade_row["id"],
        signal_id=trade_row["signal_id"],
        r_multiple_realise=trade_row["r_multiple_total"],
        denouement=denouement,
        duree_secondes=duree,
        ecart_signal_execution=ecart,
        actif=trade_row["actif"],
        confiance_signal=signal_row["confiance"] if signal_row is not None else None,
        heure_ouverture=ouvert_at.hour,
        jour_semaine=ouvert_at.weekday(),
        boosted=boosted,
    )


# ---------------------------------------------------------------------------
# Partie 2 — narratif LLM (jamais un jugement)
# ---------------------------------------------------------------------------

def _build_user_prompt(features: TradeFeatures) -> str:
    return (
        f"Actif : {features.actif}\n"
        f"R-multiple réalisé : {features.r_multiple_realise:+.2f}R\n"
        f"Dénouement : {features.denouement}\n"
        f"Durée : {features.duree_secondes} secondes\n"
        f"Écart signal/exécution : {features.ecart_signal_execution}\n"
        f"Confiance du signal au moment de l'entrée : {features.confiance_signal}\n"
        f"Heure d'ouverture (UTC) : {features.heure_ouverture}h\n"
        f"Jour de la semaine (0=lundi) : {features.jour_semaine}\n"
        f"Risque boosté (4% au lieu de 2%) : {'oui' if features.boosted else 'non'}\n\n"
        "Rédige un résumé factuel de 2-3 phrases maximum, en français."
    )


def generate_narrative_summary(features: TradeFeatures, anthropic_client, model: str = DEFAULT_MODEL) -> str:
    """Génère le résumé narratif via le client Anthropic injecté (jamais
    instancié ici — voir analyze_closed_trade). Lève
    NarrativeGuardrailError si le texte contient un motif de jugement
    interdit ; l'appelant ne doit alors ni le stocker ni le notifier."""
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=300,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(features)}],
    )
    text = response.content[0].text.strip()
    _check_narrative_guardrail(text)
    return text


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def analyze_closed_trade(
    db_path: str, trade_id: int, anthropic_client=None,
    bot_token: Optional[str] = None, chat_id: Optional[str] = None,
    model: str = DEFAULT_MODEL,
) -> TradeFeatures:
    """Point d'entrée : à appeler juste après qu'executor.py a fermé
    entièrement un trade (statut='ferme'). Calcule toujours la partie
    déterministe et la persiste. Le narratif LLM est best-effort :
    `anthropic_client=None` (ou tout échec, y compris le garde-fou)
    persiste la partie déterministe seule plutôt que de bloquer
    l'analyse sur un texte optionnel — fail-safe, mais jamais l'inverse
    (jamais de narratif sans les chiffres qui le fondent)."""
    with connection_scope(db_path) as conn:
        trade_row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if trade_row is None or trade_row["statut"] != "ferme":
            raise ValueError(f"Trade {trade_id} introuvable ou pas encore fermé")

        last_partial_row = conn.execute(
            "SELECT * FROM trade_partials WHERE trade_id = ? ORDER BY id DESC LIMIT 1", (trade_id,)
        ).fetchone()
        if last_partial_row is None:
            raise ValueError(f"Trade {trade_id} fermé sans aucune clôture partielle enregistrée")

        signal_row = None
        if trade_row["signal_id"] is not None:
            signal_row = conn.execute("SELECT * FROM signals WHERE id = ?", (trade_row["signal_id"],)).fetchone()

    features = compute_trade_features(trade_row, last_partial_row, signal_row)

    narrative = None
    if anthropic_client is not None:
        try:
            narrative = generate_narrative_summary(features, anthropic_client, model)
        except NarrativeGuardrailError:
            logger.warning("Résumé narratif rejeté par le garde-fou pour le trade %s — partie déterministe conservée seule", trade_id)
        except Exception:
            logger.exception("Échec de génération du résumé narratif pour le trade %s — partie déterministe conservée seule", trade_id)

    now = datetime.now().astimezone().isoformat()
    with connection_scope(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_analysis "
            "(trade_id, signal_id, r_multiple_realise, denouement, duree_secondes, "
            " ecart_signal_execution, actif, confiance_signal, heure_ouverture, jour_semaine, boosted, "
            " resume_narratif, resume_genere_at, resume_modele, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                features.trade_id, features.signal_id, features.r_multiple_realise, features.denouement,
                features.duree_secondes, features.ecart_signal_execution, features.actif,
                features.confiance_signal, features.heure_ouverture, features.jour_semaine, int(features.boosted),
                narrative, now if narrative else None, model if narrative else None, now,
            ),
        )

    if narrative and bot_token and chat_id:
        message = (
            f"📊 Analyse de trade clos — {features.actif} ({features.denouement}, "
            f"{features.r_multiple_realise:+.2f}R)\n\n{narrative}"
        )
        send_notification(bot_token, chat_id, message)

    return features
