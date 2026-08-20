"""
Tests de message_classifier — classification déterministe des messages du
canal Station X, sur des exemples réels fournis par Ismaël le 16/08/2026.
"""

from src.message_classifier import MessageCategory, classify

MATINALE = (
    "Bonjour à tous ! C'est reparti pour un point marché sur la Matinale.\n\n"
    "✅ Du côté du Bitcoin en Daily, le prix évolue actuellement autour des 62 997 $. "
    "Le Bitcoin reste donc sous pression en Daily et les acheteurs doivent rapidement "
    "défendre cette zone basse pour éviter une nouvelle extension de la correction. "
    "Sentiment baissier.\n\n"
    "✅ Du côté du Gold en Daily, le prix évolue actuellement autour des 4 335 $. "
    "Malgré ce repli, la structure de fond reste constructive. "
    "Le Gold reste donc solide en Daily, mais le rejet des 4 447 $ appelle désormais "
    "à davantage de prudence. Sentiment baissier.\n\n"
    "✅ Concernant les annonces économiques, deux publications américaines importantes "
    "sont attendues aujourd'hui à 14h30.\n\n"
    "✅ Bonne journée de trading à tous !"
)

# Format réel du 20/08/2026 (voir docs/DECISIONS.md) : ni "Matinale" ni
# "point marché" en intro, ni tag "Sentiment X" — seul le tag "Biais X."
# de fin de paragraphe permet la détection par repli structurel.
MATINALE_FORMAT_REEL_SANS_MOT_CLE = (
    "Du côté du Bitcoin en H4, le prix évolue actuellement autour des 69 710 $ "
    "après une accélération haussière particulièrement importante. Biais haussier."
)

SIGNAL_ALERT = "VENTE XAUUSD NOW !"
SIGNAL_STRUCTURED = "🔴 JE VENDS XAUUSD à 4367\n🎯 TP1 : 4364\n🎯 TP2 : 4357\n🎯 TP3 : Ouvert\n🔒 SL : 4370"

SUIVI_SL = "SL -30 pips"
SUIVI_TP1 = "TP1 TOUCHÉ 🔥 +30 PIPS 🟢"
SUIVI_TP2 = "TP2 TOUCHÉ 🔥 +100 PIPS 🟢"

AUTRE_BILAN_SIMPLE = "Bilan trading du jour : +2,3R ✅"
AUTRE_BILAN_TRADES = "14/08 VENTE OR -30PIPS ❌ / 14/08 VENTE OR +100PIPS ✅ BILAN TRADES : 1/2"
AUTRE_WEEKEND = "Le week-end, pas de marché, mais tu peux quand même faire avancer tes compétences..."


def test_classifies_matinale():
    assert classify(MATINALE) == MessageCategory.MATINALE


def test_classifies_matinale_format_reel_via_biais_tag_fallback():
    # Régression : avant le repli "Biais X.", ce message échouait à être
    # classé "matinale" faute du mot "Matinale"/"point marché" et faute du
    # tag "Sentiment X" (bug réel trouvé le 20/08/2026, voir docs/DECISIONS.md).
    assert classify(MATINALE_FORMAT_REEL_SANS_MOT_CLE) == MessageCategory.MATINALE


def test_classifies_structured_signal():
    assert classify(SIGNAL_STRUCTURED) == MessageCategory.SIGNAL


def test_classifies_short_alert_as_signal():
    assert classify(SIGNAL_ALERT) == MessageCategory.SIGNAL


def test_classifies_sl_update_as_suivi():
    assert classify(SUIVI_SL) == MessageCategory.SUIVI


def test_classifies_tp_touched_as_suivi():
    assert classify(SUIVI_TP1) == MessageCategory.SUIVI
    assert classify(SUIVI_TP2) == MessageCategory.SUIVI


def test_classifies_simple_bilan_as_autre():
    assert classify(AUTRE_BILAN_SIMPLE) == MessageCategory.AUTRE


def test_bilan_wins_over_signal_and_suivi_looking_text():
    # Ce message contient "VENTE" et des résultats en pips (marqueurs de
    # signal/suivi) mais reste un bilan auto-déclaré du canal : "autre"
    # obligatoire, jamais nos métriques (§3.10).
    assert classify(AUTRE_BILAN_TRADES) == MessageCategory.AUTRE


def test_classifies_weekend_message_as_autre():
    assert classify(AUTRE_WEEKEND) == MessageCategory.AUTRE


def test_empty_text_is_autre():
    assert classify("") == MessageCategory.AUTRE
    assert classify("   ") == MessageCategory.AUTRE


def test_structured_signal_never_classified_as_suivi():
    # Le message structuré contient TP1/TP2/TP3/SL comme un suivi pourrait,
    # mais la présence d'un prix d'entrée explicite ("à 4367") doit
    # toujours l'emporter : c'est un signal, pas une mise à jour.
    assert classify(SIGNAL_STRUCTURED) != MessageCategory.SUIVI
