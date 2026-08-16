# Assistant Trading

Agent de trading automatisé, usage strictement personnel, capital propre,
non commercialisé. Gouverné par `docs/CDC_v4.md` (cahier des charges v4).
Voir les invariants non négociables du projet (§4.2) avant toute
modification du code de risque ou d'exécution.

Autonomie déléguée à Claude Code sur l'architecture, le choix
déterministe vs LLM et la structure de données depuis le 16/08/2026 —
tout écart notable au CDC littéral est journalisé dans
`docs/DECISIONS.md` (raisonnement, alternative écartée). Seuls les 10
invariants du §4.2 et la couverture 100% des modules financiers
critiques restent non négociables.

## État actuel

**Palier P2 en cours** (exécution démo autonome). P0 (infrastructure,
VPS, Capital.com) et P1 (ingestion Telegram, classification, extraction,
audit manuel) sont fonctionnels et vérifiés sur données réelles — détail
complet dans `CLAUDE.md`.

### Modules critiques — 100% de couverture de tests obligatoire

- `src/risk_engine.py` — sizing, coupe-circuits de stop, R-multiple
- `src/capital_manager.py` — enveloppes, règle de réinvestissement 50% (§2.3)
- `src/go_nogo.py` — verrou de passage en réel
- `src/validator.py` — revalidation d'un signal juste avant exécution (§2.8)
- `src/executor.py` — **partie décision/calcul uniquement** (le reste,
  orchestration I/O, est testé mais pas soumis à la même exigence)

### Autres modules

| Module | Rôle |
|---|---|
| `src/config.py` | Chargement/validation `.env` |
| `src/db.py` | Schéma SQLite (aligné §4.5 du CDC + ajouts documentés) |
| `src/asset_whitelist.py` | Liste blanche §1.2, `build_asset_whitelist()` pour un dimensionnement avec taux EUR en direct |
| `src/message_classifier.py` | Classification déterministe (matinale/signal/suivi/autre) |
| `src/parser.py` | Extraction déterministe des signaux (écart documenté au §4.4 littéral) |
| `src/telegram_listener.py` | Capture Telegram (Telethon), threading, routage |
| `src/audit_notifier.py` | Notifications vers le bot de contrôle (§3.6, §7.2) |
| `src/capital_client.py` | Client HTTP Capital.com (session, ordres, prix) |
| `src/market_data.py` | Prix, bougies, ATR(14), moyenne mobile, conversion EUR en direct |
| `src/envelope_store.py` | Persistance DB des enveloppes et de la réserve globale |
| `src/trade_analyzer.py` | Analyse post-trade : features déterministes + résumé narratif LLM garde-fouté (§3.10) |

## Installation (Windows, VS Code)

1. Ouvre le dossier dans VS Code, terminal PowerShell (`` Ctrl+` ``)
2. Environnement virtuel :
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   Si PowerShell bloque le script : `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
3. Dépendances :
   ```powershell
   pip install -r requirements.txt
   ```
4. Tests (doivent tous passer, 100% de couverture sur les modules critiques) :
   ```powershell
   pytest --cov=src.risk_engine --cov=src.capital_manager --cov=src.go_nogo --cov=src.validator --cov-report=term-missing --cov-fail-under=100 tests/
   ```
   Tu dois voir `211 passed` et `Total coverage: 100.00%` (pour les
   modules critiques listés — le reste du projet a sa propre couverture,
   généralement élevée mais pas soumise à la même exigence).
5. `.env` : jamais commité (`.gitignore`), contient les clés Capital.com,
   Telegram, Anthropic, Scaleway (backups). Voir `.env.example` pour la
   liste des variables.

## Documents de référence

- `docs/CDC_v4.md` — cahier des charges complet, fait autorité
- `docs/DECISIONS.md` — journal des écarts au CDC littéral, avec
  raisonnement et alternative écartée pour chacun
- `CLAUDE.md` — état détaillé du projet, historique des paliers, pivots
  (broker, Telegram), ce qu'il ne faut jamais faire

## Rappel des invariants non négociables (§4.2 du CDC)

1. Aucun LLM n'a accès au broker, au capital, ni au moteur de risque.
2. Tout calcul financier est déterministe et testé unitairement.
3. Aucun signal ne devient un ordre sans validation déterministe complète.
4. Le passage en mode réel est verrouillé par code (`go_nogo.py`), jamais
   par discipline personnelle.
5. Tout ordre est journalisé avant envoi et après confirmation.
6. Les plafonds de risque ne sont modifiables que par redéploiement.
7. Un stop peut être resserré, jamais élargi. Aucune moyenne à la baisse.
8. Aucune donnée sensible en clair dans Git.
9. Fail-safe : toute erreur non gérée arrête les entrées, ne les
   poursuit pas. Le score de confiance et toute évaluation de
   performance restent calculés, jamais jugés par un LLM.
10. Anti-surapprentissage : 5 variables max, 10 trades/variable minimum,
    découpage train/test temporel, justification théorique écrite avant
    d'observer les données.

Compte **démo uniquement** à ce palier. Aucune bascule vers le réel
n'est envisageable avant Porte A/B (§4.8), verrouillée par `go_nogo.py`.
