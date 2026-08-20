"""
dashboard.py — Génère la page HTML statique du §4.6, à la demande
(commande /dashboard du bot de contrôle, src/control_bot.py).

Écart assumé au §4.6 littéral (validé par Ismaël, 20/08/2026), documenté
dans docs/DECISIONS.md : pas de serveur web, même temporaire — le HTML
généré est envoyé directement en pièce jointe Telegram
(audit_notifier.send_document), qu'Ismaël ouvre localement. Zéro port
exposé, zéro process supplémentaire — dépasse l'objectif du CDC ("pas de
surface d'attaque exposée en continu"), pas juste "temporaire".

Lecture seule, déterministe, aucune décision (§4.4 : dashboard ✅) —
construit exclusivement à partir de metrics.py et de requêtes DB
directes, JAMAIS via CapitalClient : la génération du dashboard ne doit
dépendre d'aucune session broker ni du réseau Capital.com.

Contenu dans l'ordre exact du §4.6. Deux blocs dépendent de modules non
construits (allocator, hypothesis_engine officiel du §3.9) — affichés
vides et clairement étiquetés "non construit" plutôt que simulés avec
des données factices (demande explicite d'Ismaël, 20/08/2026) :
Hypothèses (générateur officiel), Décisions. Le bloc Classement, lui, est
construit depuis le 20/08/2026 (confidence_scorer.py, §2.4) — en mode
observation uniquement, voir sa docstring pour le détail des écarts
assumés et le rappel comparaisons-multiples affiché sur ce bloc.
"""

import html
from datetime import datetime, timezone
from typing import Optional

from src.circuit_breaker_store import get_active_global_block
from src.confidence_scorer import MULTIPLE_COMPARISONS_CAVEAT, compute_all_confidence_scores
from src.db import connection_scope
from src.metrics import (
    HYPOTHESIS_SOURCE,
    get_aggregate_period_pnl,
    get_all_asset_keys,
    get_all_asset_metrics,
    get_asset_period_pnl,
    get_reserve_total,
    get_total_demo_capital,
    get_trade_counts_by_period,
)

_STYLE = """
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 2rem; color: #1a1a1a; background: #fff; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.1rem; margin-top: 2.5rem; border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }
table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f2f2f2; }
.empty { color: #888; font-style: italic; }
.not-built { color: #888; font-style: italic; padding: 0.5rem; border: 1px dashed #ccc; }
.pos { color: #0a7a0a; }
.neg { color: #b00020; }
input#filter { padding: 0.4rem; width: 100%; max-width: 24rem; margin-top: 0.5rem; }
footer { margin-top: 3rem; color: #888; font-size: 0.8rem; }
"""

_FILTER_SCRIPT = """
function filterHistorique() {
  var q = document.getElementById('filter').value.toLowerCase();
  var rows = document.querySelectorAll('#historique tbody tr');
  rows.forEach(function(row) {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
"""


def _fmt(value, digits=2) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    return f"{value:.{digits}f}"


def _class_recap(value: float) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return ""


def _get_process_statuses(db_path: str) -> dict:
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM system_state WHERE key LIKE 'watchdog:status:%'"
        ).fetchall()
    return {row["key"].split(":", 2)[2]: row["value"] for row in rows}


def _get_active_asset_breakers(db_path: str) -> list:
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT actif, source, breaker_type FROM circuit_breaker_events WHERE scope = 'asset' AND ("
            "  (breaker_type IN ('week_r', 'drawdown_r', 'manual_pause') AND cleared_at IS NULL)"
            "  OR (breaker_type = 'day_r' AND substr(triggered_at, 1, 10) = date('now'))"
            ") ORDER BY actif"
        ).fetchall()
    return [dict(row) for row in rows]


def _get_hypothesis_progress(db_path: str) -> int:
    """Nombre de trades clôturés du Flux B (source normalisée
    'hypothesis') — progression vers le seuil de 10 trades avant toute
    conclusion statistique (invariant #10), pas le générateur officiel
    du §3.9 (absent, voir audit du 19/08/2026)."""
    with connection_scope(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM trades WHERE statut = 'ferme' AND source = ?", (HYPOTHESIS_SOURCE,)
        ).fetchone()
    return row["n"]


def _get_trade_history(db_path: str) -> list:
    with connection_scope(db_path) as conn:
        rows = conn.execute(
            "SELECT actif, source, direction, ouvert_at, ferme_at, r_multiple_total, pnl_net "
            "FROM trades WHERE statut = 'ferme' ORDER BY ferme_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def _render_overview(db_path: str, now: datetime) -> str:
    total_capital = get_total_demo_capital(db_path)
    reserve = get_reserve_total(db_path)
    nb_actifs = len({actif for actif, _ in get_all_asset_keys(db_path)})
    processes = _get_process_statuses(db_path)
    breakers = _get_active_asset_breakers(db_path)
    global_block = get_active_global_block(db_path)

    process_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{'vivant' if status == 'up' else html.escape(status)}</td></tr>"
        for name, status in sorted(processes.items())
    ) or "<tr><td colspan='2' class='empty'>Aucune donnée de surveillance</td></tr>"

    breaker_summary = f"{len(breakers)} coupe-circuit(s) actif(s) par actif" + (
        f", pause globale active ({html.escape(global_block)})" if global_block else ""
    )

    return f"""
    <h2>Vue d'ensemble</h2>
    <table>
      <tr><th>Capital démo total</th><td>{_fmt(total_capital)} €</td></tr>
      <tr><th>Capital réel</th><td class="empty">Aucun (démo uniquement, §4.8)</td></tr>
      <tr><th>Réserve globale</th><td>{_fmt(reserve)} €</td></tr>
      <tr><th>Actifs actifs</th><td>{nb_actifs}</td></tr>
      <tr><th>Statut système</th><td>{breaker_summary}</td></tr>
    </table>
    <table>
      <tr><th>Process</th><th>État</th></tr>
      {process_rows}
    </table>
    """


def _render_par_actif(db_path: str) -> str:
    metrics = get_all_asset_metrics(db_path)
    if not metrics:
        return "<h2>Par actif</h2><p class='empty'>Aucune enveloppe créée</p>"

    with connection_scope(db_path) as conn:
        envelopes = {
            (row["actif"], row["source"]): dict(row)
            for row in conn.execute("SELECT actif, source, mode, capital_courant FROM envelopes").fetchall()
        }

    rows = []
    for m in metrics:
        env = envelopes.get((m.actif, m.source), {})
        rows.append(
            f"<tr><td>{html.escape(m.actif)}</td><td>{html.escape(m.source)}</td>"
            f"<td>{html.escape(env.get('mode', '—'))}</td>"
            f"<td>{_fmt(env.get('capital_courant'))} €</td>"
            f"<td>{_fmt(m.esperance_r)}R</td><td>{_fmt(m.profit_factor)}</td>"
            f"<td>{m.nb_trades}</td><td>{_fmt(m.drawdown_max_r)}R</td>"
            f"<td class='empty'>N/A (démo)</td></tr>"
        )

    return f"""
    <h2>Par actif</h2>
    <table>
      <tr><th>Actif</th><th>Source</th><th>Mode</th><th>Engagé</th><th>Espérance</th>
          <th>Profit factor</th><th>Nb trades</th><th>Drawdown max</th><th>Go/No-Go</th></tr>
      {''.join(rows)}
    </table>
    """


def _render_periodes(db_path: str, now: datetime) -> str:
    metrics = get_all_asset_metrics(db_path)
    rows = []
    for m in metrics:
        week, month, all_time = get_asset_period_pnl(db_path, m.actif, m.source, now)
        rows.append(
            f"<tr><td>{html.escape(m.actif)}</td><td>{html.escape(m.source)}</td>"
            f"<td class='{_class_recap(week)}'>{_fmt(week)} €</td>"
            f"<td class='{_class_recap(month)}'>{_fmt(month)} €</td>"
            f"<td class='{_class_recap(all_time)}'>{_fmt(all_time)} €</td></tr>"
        )

    agg_week, agg_month, agg_all = get_aggregate_period_pnl(db_path, now)
    rows.append(
        f"<tr><th>Total agrégé</th><td>—</td>"
        f"<td class='{_class_recap(agg_week)}'><b>{_fmt(agg_week)} €</b></td>"
        f"<td class='{_class_recap(agg_month)}'><b>{_fmt(agg_month)} €</b></td>"
        f"<td class='{_class_recap(agg_all)}'><b>{_fmt(agg_all)} €</b></td></tr>"
    )

    return f"""
    <h2>Périodes</h2>
    <table>
      <tr><th>Actif</th><th>Source</th><th>Semaine</th><th>Mois</th><th>Depuis le début</th></tr>
      {''.join(rows)}
    </table>
    <p><small>Gains crédités à l'enveloppe après la règle des 50% (§2.3) — pas le PnL brut du trade.</small></p>
    """


def _render_trades(db_path: str, now: datetime) -> str:
    counts = get_trade_counts_by_period(db_path, now)
    if not counts:
        return "<h2>Trades</h2><p class='empty'>Aucun trade clôturé</p>"
    rows = "".join(
        f"<tr><td>{html.escape(actif)}</td><td>{html.escape(source)}</td>"
        f"<td>{c['semaine']}</td><td>{c['mois']}</td><td>{c['total']}</td></tr>"
        for (actif, source), c in sorted(counts.items())
    )
    return f"""
    <h2>Trades</h2>
    <table>
      <tr><th>Actif</th><th>Source</th><th>Semaine</th><th>Mois</th><th>Total</th></tr>
      {rows}
    </table>
    """


def _render_hypotheses(db_path: str) -> str:
    nb_flux_b = _get_hypothesis_progress(db_path)
    return f"""
    <h2>Hypothèses</h2>
    <p class="not-built">Générateur d'hypothèses officiel (§3.9, module hypothesis_engine, cycle trimestriel Claude) : non construit.</p>
    <table>
      <tr><th>Flux B — Hypothèse #1 (docs/HYPOTHESES.md)</th><td>{nb_flux_b} / 10 trades clôturés
      avant toute conclusion statistique (invariant #10)</td></tr>
    </table>
    """


def _render_classement(db_path: str) -> str:
    scores = compute_all_confidence_scores(db_path)
    if not scores:
        return "<h2>Classement</h2><p class='empty'>Aucune enveloppe créée</p>"

    rows = []
    for s in scores:
        raisons = "; ".join(html.escape(c.detail) for c in s.checks if not c.satisfied) or "—"
        rows.append(
            f"<tr><td>{html.escape(s.actif)}</td><td>{html.escape(s.source)}</td>"
            f"<td>{s.nb_trades}</td><td>{html.escape(s.phase)}</td>"
            f"<td>{'éligible' if s.eligible else 'non éligible'}</td>"
            f"<td>{_fmt(s.score, 4) if s.score is not None else '—'}</td>"
            f"<td>{raisons}</td></tr>"
        )

    return f"""
    <h2>Classement</h2>
    <p><small>{html.escape(MULTIPLE_COMPARISONS_CAVEAT)}</small></p>
    <table>
      <tr><th>Actif</th><th>Source</th><th>Nb trades</th><th>Phase</th><th>Statut</th>
          <th>Score</th><th>Condition(s) non satisfaite(s)</th></tr>
      {''.join(rows)}
    </table>
    """


def _render_decisions() -> str:
    return "<h2>Décisions</h2><p class='not-built'>Allocation automatique du capital réel (§2.5, allocator) : non construit.</p>"


def _render_historique(db_path: str) -> str:
    trades = _get_trade_history(db_path)
    if not trades:
        return "<h2>Historique complet</h2><p class='empty'>Aucun trade clôturé</p>"

    rows = "".join(
        f"<tr><td>{html.escape(t['actif'])}</td><td>{html.escape(t['source'] or '')}</td>"
        f"<td>{html.escape(t['direction'])}</td><td>{html.escape(t['ouvert_at'] or '')}</td>"
        f"<td>{html.escape(t['ferme_at'] or '')}</td>"
        f"<td class='{_class_recap(t['r_multiple_total'] or 0)}'>{_fmt(t['r_multiple_total'])}R</td>"
        f"<td class='{_class_recap(t['pnl_net'] or 0)}'>{_fmt(t['pnl_net'])} €</td></tr>"
        for t in trades
    )
    return f"""
    <h2>Historique complet</h2>
    <input id="filter" type="text" placeholder="Filtrer (actif, source, direction...)" oninput="filterHistorique()">
    <table id="historique">
      <thead><tr><th>Actif</th><th>Source</th><th>Direction</th><th>Ouvert</th><th>Fermé</th><th>R</th><th>PnL net</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def generate_dashboard_html(db_path: str, now: Optional[datetime] = None) -> str:
    """Génère la page HTML complète (§4.6), autonome (CSS/JS inline,
    aucune ressource externe — doit s'ouvrir hors-ligne). Ne lève jamais
    d'exception par bloc : un bloc en échec est remplacé par un message
    d'erreur visible plutôt que de faire échouer tout le dashboard."""
    now = now or datetime.now(timezone.utc)

    def _safe(render_fn, *args) -> str:
        try:
            return render_fn(*args)
        except Exception as exc:
            return f"<h2>Erreur</h2><p class='not-built'>Bloc indisponible : {html.escape(str(exc))}</p>"

    # Ordre exact du §4.6 : vue d'ensemble, par actif, périodes, trades,
    # hypothèses, classement, décisions, historique complet.
    sections = [
        _safe(_render_overview, db_path, now),
        _safe(_render_par_actif, db_path),
        _safe(_render_periodes, db_path, now),
        _safe(_render_trades, db_path, now),
        _safe(_render_hypotheses, db_path),
        _safe(_render_classement, db_path),
        _render_decisions(),
        _safe(_render_historique, db_path),
    ]

    generated_at = now.isoformat()
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Assistant Trading — Dashboard</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Assistant Trading — Dashboard</h1>
<p><small>Généré le {html.escape(generated_at)}</small></p>
{''.join(sections)}
<footer>Généré à la demande (/dashboard) — aucune donnée envoyée ailleurs que ce fichier.</footer>
<script>{_FILTER_SCRIPT}</script>
</body>
</html>"""
