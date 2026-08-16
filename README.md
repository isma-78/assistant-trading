# Assistant Trading

Agent de trading automatisé, usage strictement personnel. Gouverné par le
cahier des charges v4 (CDC v4). Voir les invariants non négociables du
projet avant toute modification du code de risque ou d'exécution.

## État actuel

Palier **P0 en cours**. Ce dépôt contient le socle du projet :

- Structure du projet
- `config.py` : chargement/validation de la configuration (`.env`)
- `db.py` : schéma SQLite (signaux, trades, décisions de risque, enveloppe, Go/No-Go)
- `risk_engine.py` — **module critique**, 100% de couverture de tests
- `capital_manager.py` — **module critique**, 100% de couverture de tests
- `go_nogo.py` — **module critique**, 100% de couverture de tests

Ce qui **n'est pas encore fait** (bloquant avant le palier P1) :

- [ ] Tableau des tailles minimales OANDA rempli (§1.2 du CDC) — conditionne la liste blanche réelle utilisée par `risk_engine`
- [ ] Compte Telegram dédié + Station X
- [ ] VPS sécurisé
- [ ] Dépôt Git privé
- [ ] Clés API (extraction, Anthropic)

Tant que ces éléments ne sont pas réunis, aucun code d'ingestion Telegram
ni d'exécution OANDA n'a de sens à écrire — ils dépendent de ces
identifiants. Le socle ci-dessus, lui, est indépendant et testable dès
maintenant.

## Installation (Windows, VS Code)

1. Dézippe ce dossier où tu veux (ex: `C:\Users\ismael\Projects\assistant-trading`)
2. Ouvre le dossier dans VS Code (`Fichier > Ouvrir un dossier`)
3. Ouvre un terminal PowerShell dans VS Code (`` Ctrl+` ``)
4. Crée l'environnement virtuel :
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   Si PowerShell bloque le script, lance d'abord :
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
5. Installe les dépendances :
   ```powershell
   pip install -r requirements.txt
   ```
6. Lance les tests (doivent tous passer, 100% de couverture sur les 3 modules critiques) :
   ```powershell
   pytest --cov=src.risk_engine --cov=src.capital_manager --cov=src.go_nogo --cov-report=term-missing --cov-fail-under=100 tests/
   ```

Tu dois voir `43 passed` et `Total coverage: 100.00%`.

7. Copie `.env.example` vers `.env` :
   ```powershell
   Copy-Item .env.example .env
   ```
   Ne le remplis pas encore complètement — les identifiants Telegram/OANDA/API
   ne sont pas tous disponibles. `.env` est ignoré par Git (`.gitignore`),
   il ne doit jamais être commité.

## Prochaine étape

1. Remplir le tableau des tailles minimales OANDA (§1.2 du CDC) et me le
   transmettre — je finalise la liste blanche réelle (`AssetSpec` par actif)
   et ajuste le dimensionnement dans `risk_engine`.
2. En parallèle, avancer sur les autres points de la checklist P0 (Telegram
   dédié, VPS, dépôt Git privé, clés API).
3. Une fois P0 complet : développement du palier P1 (ingestion Telegram,
   classification, extraction LLM).

## Rappel des invariants non négociables

1. Aucun LLM n'a accès au broker, au capital, ni au moteur de risque.
2. Tout calcul financier est déterministe et testé unitairement.
3. Aucun signal ne devient un ordre sans validation déterministe complète.
4. Le passage en mode réel est verrouillé par code (`go_nogo.py`), jamais
   par discipline personnelle.
5. Un stop peut être resserré, jamais élargi. Aucune moyenne à la baisse.
6. Les plafonds de risque ne sont modifiables que par redéploiement.
7. Fail-safe : toute erreur non gérée arrête les entrées.
8. Aucun secret dans Git, jamais.
9. Le score de confiance est calculé statistiquement, jamais jugé par un LLM.
10. Anti-surapprentissage : 5 variables max, 10 trades/variable minimum,
    découpage train/test temporel, justification théorique écrite avant
    d'observer les données.
