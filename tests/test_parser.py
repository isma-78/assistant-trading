"""
Tests de parser — extraction déterministe des champs structurés, sur des
exemples réels du canal Station X fournis par Ismaël le 16/08/2026.
"""

from src.asset_whitelist import ASSET_WHITELIST
from src.parser import (
    ASSET_ALIASES,
    extract_matinale,
    extract_signal,
    extract_suivi,
)

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

SIGNAL_ALERT = "VENTE XAUUSD NOW !"
SIGNAL_STRUCTURED = "🔴 JE VENDS XAUUSD à 4367\n🎯 TP1 : 4364\n🎯 TP2 : 4357\n🎯 TP3 : Ouvert\n🔒 SL : 4370"


def test_asset_aliases_all_resolve_to_whitelisted_symbols():
    # Garde-fou anti-dérive : un alias qui pointerait vers un symbole retiré
    # de la liste blanche romprait silencieusement l'extraction.
    for symbol in ASSET_ALIASES.values():
        assert symbol in ASSET_WHITELIST


def test_extract_signal_structured_message_is_complete():
    result = extract_signal(SIGNAL_STRUCTURED, reply_to_msg_id=42)
    assert result.asset == "GOLD"
    assert result.raw_asset_mention == "XAUUSD"
    assert result.direction == "short"
    assert result.entry_price == 4367.0
    assert result.stop_price == 4370.0
    assert result.take_profits == [4364.0, 4357.0, None]
    assert result.reply_to_msg_id == 42
    assert result.extraction_status == "ok"


def test_extract_signal_tp3_ouvert_is_none_not_error():
    result = extract_signal(SIGNAL_STRUCTURED)
    assert result.take_profits[2] is None


def test_extract_signal_short_alert_is_incomplete():
    # Message 1 de l'exemple : alerte préalable sans prix ni niveaux.
    # L'actif et la direction sont tout de même récupérés pour archivage /
    # liaison éventuelle, mais extraction_status doit rester "incomplete" —
    # ce texte ne doit jamais devenir un ordre.
    result = extract_signal(SIGNAL_ALERT)
    assert result.asset == "GOLD"
    assert result.direction == "short"
    assert result.entry_price is None
    assert result.stop_price is None
    assert result.take_profits == [None, None, None]
    assert result.extraction_status == "incomplete"


def test_extract_signal_buy_direction():
    result = extract_signal("🟢 J'ACHÈTE EURUSD à 1.0850\n🔒 SL : 1.0800")
    assert result.asset == "EURUSD"
    assert result.direction == "long"
    assert result.entry_price == 1.085
    assert result.stop_price == 1.08


def test_extract_signal_thousands_separator_space():
    # Format réel du canal (capturé en production le 16/08/2026, backfill
    # historique) : "91 950" avec espace comme séparateur de milliers.
    # Bug réel trouvé ici : un \d+ simple ne capturait que "91" avant
    # l'espace, corrompant silencieusement le prix. Voir docs/DECISIONS.md.
    text = (
        "🛑 JE VENDS BTCUSD à 91 950\n\n"
        "🎯 TP1 : 91 650\n"
        "🎯 TP2 : 91 050\n"
        "🎯 TP3 : Ouvert\n\n"
        "🔒 SL : 92 170"
    )
    result = extract_signal(text)
    assert result.asset == "BTCUSD"
    assert result.direction == "short"
    assert result.entry_price == 91950.0
    assert result.stop_price == 92170.0
    assert result.take_profits == [91650.0, 91050.0, None]
    assert result.extraction_status == "ok"


def test_extract_signal_ticker_with_digits():
    # Bug réel trouvé sur données de production : la classe de caractères
    # de l'actif excluait les chiffres, donc "NAS100" ne pouvait jamais
    # matcher le regex structuré (échec silencieux -> incomplete alors que
    # le message était complet). Voir docs/DECISIONS.md.
    text = (
        "🟢 J'ACHÈTE NAS100 à 20 270\n\n"
        "🎯 TP1 : 20 320\n"
        "🎯 TP2 : 20 420\n"
        "🎯 TP3 : Ouvert\n\n"
        "SL : 20 230 🔒"
    )
    result = extract_signal(text)
    assert result.asset == "US100"
    assert result.raw_asset_mention == "NAS100"
    assert result.direction == "long"
    assert result.entry_price == 20270.0
    assert result.stop_price == 20230.0
    assert result.extraction_status == "ok"


def test_extract_signal_unresolved_asset_does_not_crash():
    result = extract_signal("🔴 JE VENDS SILVERUSD à 24.50\n🔒 SL : 25.00")
    assert result.asset is None
    assert result.raw_asset_mention == "SILVERUSD"
    assert result.extraction_status == "incomplete"


def test_extract_suivi_sl_hit_negative_pips():
    result = extract_suivi("SL -30 pips", reply_to_msg_id=99)
    assert result.event == "sl_hit"
    assert result.pips == -30.0
    assert result.reply_to_msg_id == 99


def test_extract_suivi_tp1_touched():
    result = extract_suivi("TP1 TOUCHÉ 🔥 +30 PIPS 🟢", reply_to_msg_id=99)
    assert result.event == "tp1_hit"
    assert result.pips == 30.0


def test_extract_suivi_tp2_touched():
    result = extract_suivi("TP2 TOUCHÉ 🔥 +100 PIPS 🟢", reply_to_msg_id=99)
    assert result.event == "tp2_hit"
    assert result.pips == 100.0


def test_extract_matinale_returns_one_summary_per_asset_block():
    result = extract_matinale(MATINALE)
    assets = [a.raw_asset_mention for a in result.assets]
    assert assets == ["Bitcoin", "Gold"]


def test_extract_matinale_skips_non_asset_paragraphs():
    result = extract_matinale(MATINALE)
    # Le paragraphe "annonces économiques" et la clôture ne doivent générer
    # aucun MatinaleAssetSummary.
    assert len(result.assets) == 2


def test_extract_matinale_bitcoin_body_matches_declared_sentiment():
    result = extract_matinale(MATINALE)
    bitcoin = next(a for a in result.assets if a.raw_asset_mention == "Bitcoin")
    assert bitcoin.asset == "BTCUSD"
    assert bitcoin.biais_corps == "baissier"
    assert bitcoin.sentiment_tag == "baissier"
    assert bitcoin.contradiction_detectee is False


def test_extract_matinale_gold_contradiction_detected():
    # Cas réel de contradiction §3.4 : corps constructif ("reste donc
    # solide") mais tag final "Sentiment baissier." — à journaliser, jamais
    # à trancher automatiquement en faveur de l'un ou l'autre.
    result = extract_matinale(MATINALE)
    gold = next(a for a in result.assets if a.raw_asset_mention == "Gold")
    assert gold.asset == "GOLD"
    assert gold.biais_corps == "haussier"
    assert gold.sentiment_tag == "baissier"
    assert gold.contradiction_detectee is True


def test_extract_matinale_no_asset_blocks_returns_empty_list():
    result = extract_matinale("Bonne journée à tous, pas de point marché aujourd'hui.")
    assert result.assets == []
