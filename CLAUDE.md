# Assistant Trading — Contexte pour Claude Code

Agent de trading automatisé, usage strictement personnel, non commercialisé.
Ismaël est le porteur du projet, non technique. Modèle de travail : produire
du code complet prêt à coller/exécuter et des instructions pas-à-pas,
jamais de pseudo-code.

Le cahier des charges v4 (CDC v4, détenu par Ismaël, pas dans ce dépôt) fait
autorité. En cas de contradiction entre une demande ponctuelle et le CDC,
signaler la contradiction avant d'agir plutôt que d'appliquer silencieusement.

## Invariants non négociables (jamais contournés, même sur demande explicite)

1. Aucun LLM n'a accès au broker, au capital, ni au moteur de risque. Les LLM
   traduisent et expliquent ; le code déterministe décide.
2. Tout calcul financier est déterministe et testé unitairement.
3. Aucun signal ne devient un ordre sans validation déterministe complète.
4. Le passage en mode réel est verrouillé par code (`go_nogo.py`), jamais par
   discipline personnelle.
5. Un stop peut être resserré, jamais élargi. Aucune moyenne à la baisse.
   Aucune augmentation de position perdante.
6. Les plafonds de risque ne sont pas modifiables à chaud, uniquement par
   redéploiement.
7. Fail-safe : toute erreur non gérée arrête les entrées, ne les poursuit pas.
8. Aucun secret dans Git, jamais. `.env` est ignoré, jamais commité.
9. Le score de confiance est calculé statistiquement, jamais jugé par un LLM.
10. Anti-surapprentissage : 5 variables maximum, 10 trades par variable
    minimum, découpage train/test temporel, correction pour comparaisons
    multiples. Toute nouvelle variable exige une justification théorique
    écrite AVANT de regarder les données.

## État actuel (palier P0, en cours)

### Fait

- Structure du projet, `.gitignore`, `.env.example`, `requirements.txt`, `pyproject.toml`
- `src/config.py` : chargement/validation config `.env` (migré vers Capital.com,
  voir pivot broker ci-dessous — `CAPITAL_API_KEY`/`CAPITAL_IDENTIFIER`/
  `CAPITAL_API_PASSWORD`/`CAPITAL_ENVIRONMENT`)
- `src/db.py` : schéma SQLite (signals, trades, risk_decisions, envelope_events, go_nogo_events)
- `src/risk_engine.py` — **module critique**, 100% couverture (sizing déterministe,
  refus moyenne à la baisse, stop resserré uniquement, fail-safe sur exception)
- `src/capital_manager.py` — **module critique**, 100% couverture (suivi enveloppe journalisé)
- `src/go_nogo.py` — **module critique**, 100% couverture (verrou passage réel,
  messages mis à jour pour `CAPITAL_ENVIRONMENT`)
- `src/asset_whitelist.py` : `AssetSpec` finalisés pour les 8 actifs du CDC v4 §1.2
  (GOLD, US100, US30, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD) à partir des specs
  réelles Capital.com. `pip_value_per_unit` calibré empiriquement pour BTCUSD/ETHUSD
  le 16/08/2026 (écarts de 3.05% et 6.64% vs. valeur codée, sous le seuil de 20% —
  voir `calibrate_pip_value.py` et `data/pip_value_calibration.json`). GOLD/US100/US30
  restent à calibrer de la même façon un jour ouvré (marché fermé le dimanche
  16/08/2026 lors de l'extraction) — relancer simplement `calibrate_pip_value.py`.
  **TODO BLOQUANT AVANT TOUTE EXÉCUTION RÉELLE** (noté le 16/08/2026) :
  `_USD_TO_EUR`/`_JPY_TO_EUR` dans `asset_whitelist.py` sont des taux de
  change figés au 16/08/2026 — acceptables pour la décision de liste
  blanche, pas pour le dimensionnement réel des positions (dérive avec le
  temps). À remplacer par un taux rafraîchi dynamiquement via
  `market_data.py` (module prévu dans la structure CDC, pas encore écrit)
  avant tout passage en mode réel.
  16/08/2026 lors de l'extraction) — relancer simplement `calibrate_pip_value.py`.
- `discover_instruments.py` : extraction reproductible des specs Capital.com
  (epic, minDealSize, dealingRules, etc.) → `data/instrument_specs.json`
- `calibrate_pip_value.py` : calibration empirique de `pip_value_per_unit` par
  mesure réelle sur le compte démo (ouverture position taille calibrée sur budget
  de marge, mesure P&L après 60s, fermeture immédiate, comparaison au chiffre codé,
  signalement si écart > 20% sans correction automatique)
- `scripts/backup_db.py` : sauvegarde SQLite horodatée + purge après 30 jours
  (testé de bout en bout le 16/08/2026)
- 46 tests unitaires, tous verts. Commande de vérification :
  ```
  pytest --cov=src.risk_engine --cov=src.capital_manager --cov=src.go_nogo --cov-report=term-missing --cov-fail-under=100 tests/
  ```
- Dépôt Git initialisé le 16/08/2026 (`git init` + commits). Dépôt **distant**
  privé créé sur GitHub (`https://github.com/isma-78/assistant-trading.git`)
  le 16/08/2026, remote `origin` configuré, `main` synchronisée (poussé
  jusqu'au commit `2427da9`). Historique vérifié avant push : `.env` jamais
  commité, aucun secret en clair dans les diffs (seuls des noms de variables
  vides dans `.env.example` et des appels `_require(...)` dans `src/config.py`).
  Continuer à vérifier `.env` et secrets absents du staging avant tout commit
  futur.
- **VPS de production déployé** le 16/08/2026 : `assistant@163.172.189.239`
  (Ubuntu 26.04 LTS, Scaleway, hostname `scw-musing-kalam`), accès SSH par
  clé (pas de mot de passe). Dépôt cloné dans
  `/home/assistant/assistant-trading`, venv Python créé (`venv/`,
  Python 3.14.4), `requirements.txt` installé dedans. `.env` déposé
  manuellement par Ismaël en SSH direct (jamais transmis à/par un LLM),
  permissions `600`. **Durcissement complet du VPS selon le guide P0 §4.2
  (pare-feu, fail2ban, etc.) non vérifié à ce stade** — seul l'accès SSH par
  clé était déjà en place ; à auditer avant d'y faire tourner quoi que ce
  soit de sensible en continu.
- **Sauvegarde SQLite automatique avec synchronisation hors VPS** opérationnelle
  depuis le 16/08/2026 : `scripts/backup_and_sync.sh` (committé) enchaîne
  `scripts/backup_db.py` puis `rclone sync` vers Scaleway Object Storage
  (remote rclone `scaleway`, bucket `assistant-trading-backups`, région
  `fr-par`, configuré manuellement par Ismaël en SSH — jamais par un LLM,
  identifiants jamais vus). Cron VPS : `0 3 * * *`, logs dans
  `logs/backup_cron.log`. Bug corrigé pendant la mise en place : sans base
  SQLite existante, `data/backups/` n'était jamais créé et `rclone sync`
  échouait (`directory not found`) — le script crée désormais
  systématiquement le dossier avant la synchronisation. Testé de bout en
  bout avec une base factice (fichier confirmé apparu puis supprimé du
  bucket) avant d'activer le cron sans supervision.

### Pivot important : broker

Le CDC v4 prévoyait OANDA (API REST v20) comme broker. **Ce choix a été
invalidé** : depuis le Brexit, tous les clients UE d'OANDA (dont la France)
sont routés vers l'entité **OANDA TMS Brokers S.A.** (Pologne), qui est
explicitement exclue de l'API v20 par la documentation officielle OANDA
(disponible sur toutes les divisions "sauf OANDA Global Markets et OANDA TMS
Brokers S.A."). Un compte démo OANDA existe (via MT5, entité TMS) mais n'est
**pas utilisable pour l'automatisation** — pas d'accès API REST.

**Nouveau broker retenu : Capital.com**
- Accessible depuis la France (passeport européen, régulé CySEC)
- API REST complète confirmée fonctionnelle : ouverture de session
  (`POST /session`), lecture de solde (`GET /accounts`), recherche de
  marchés et prix (`GET /markets`), ouverture/fermeture de position
  (`POST /api/v1/positions`, `DELETE /api/v1/positions/{dealId}`)
- URL démo : `https://demo-api-capital.backend-capital.com/api/v1`
- Auth : header `X-CAP-API-KEY` + `POST /session` avec `identifier` (email
  de connexion) et `password` (mot de passe API personnalisé, généré avec
  la clé — PAS le mot de passe de connexion au compte)
- Compte démo ouvert, 2FA activé, clé API générée, connexion testée avec
  succès le 16/08/2026 (lecture solde 1000€ démo + prix EUR/USD en direct,
  puis calibration réelle avec ouverture/fermeture de positions BTCUSD/ETHUSD)
- Variables `.env` : `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_PASSWORD`,
  `CAPITAL_ENVIRONMENT` (`demo` | `live`, défaut `demo`)
- Point de vigilance repéré : Capital.com propose des marchés "week-end"
  synthétiques (ex: epic `EURUSD_W`, tradeable quand `EURUSD` classique est
  `CLOSED`). Le CDC verrouille une architecture 24/5, pas 24/7 — ne jamais
  utiliser ces marchés `_W` pour le forex/indices sans validation explicite
  d'Ismaël au préalable. `discover_instruments.py` les exclut déjà
  automatiquement du candidat principal.
- Particularité API découverte le 16/08/2026 : le `dealId` renvoyé au premier
  niveau de `GET /confirms/{dealReference}` est l'ID de l'**ordre**, pas de la
  **position** — pour fermer une position, utiliser
  `confirmation["affectedDeals"][0]["dealId"]`, pas `confirmation["dealId"]`.
- Ce compte démo exige un stop garanti (`guaranteedStop: true` +
  `stopDistance`) pour ouvrir une position sur les cryptos (BTCUSD/ETHUSD) —
  sans lui, l'API renvoie `error.vallidation.guaranteed-stop-loss.required`.
  À vérifier si ça s'applique aussi aux autres classes d'actifs.

**Compte réel séparé** : Ismaël a déposé de l'argent sur un compte OANDA
**réel** (pas démo), dans l'intention de s'en servir plus tard. Ce compte
n'est touché par aucun code, ne sera jamais utilisé par l'automatisation
(problème d'API identique au démo), et devra être transféré vers Capital.com
(ou un autre broker retenu) le jour où le système sera prêt pour le réel.
Ne jamais écrire de code qui s'y connecte.

### Pivot important : Telegram

Le CDC v4 prévoyait un compte Telegram **dédié** (distinct du compte
personnel) pour s'abonner à Station X et faire tourner Telethon. **Ce choix
a été invalidé le 16/08/2026** : impossible de retrouver ou renouveler le
lien d'invitation Station X pour l'abonner depuis un nouveau compte dédié.

**Décision retenue** : le compte Telegram **personnel** d'Ismaël sera utilisé
pour Telethon. Vérification en deux étapes (2FA) activée sur ce compte en
compensation.

**Conséquence de sécurité importante** : le fichier `.session` Telethon qui
en résultera donne accès au compte Telegram **personnel complet**
d'Ismaël (contacts, messages privés, tous les groupes/canaux) — pas
seulement à Station X, contrairement à ce qu'un compte dédié aurait limité.
Traiter ce fichier avec une vigilance renforcée :
- Permissions fichier restrictives sur le VPS (lecture/écriture propriétaire
  uniquement, ex. `chmod 600`)
- Jamais loggé (ni son contenu, ni son chemin dans des logs verbeux exposés)
- Jamais exposé (pas dans un dépôt Git même privé — déjà couvert par
  `.gitignore` : `*.session`, `*.session-journal` — pas dans une sauvegarde
  non chiffrée transmise ailleurs que le VPS lui-même)
- En cas de compromission du VPS, ce fichier compromet le compte Telegram
  personnel d'Ismaël dans son intégralité, pas juste le flux de signaux —
  à garder en tête pour le durcissement VPS (guide P0 §4.2)

### Pas encore fait (checklist P0)

- [x] Tableau §1.2 finalisé avec les vraies specs Capital.com (`src/asset_whitelist.py`)
- [ ] Compte Telegram : **pas de compte dédié** (voir pivot ci-dessus) — le
      compte personnel d'Ismaël sera utilisé, 2FA déjà activé. Reste à faire :
      abonnement Station X depuis ce compte + `api_id`/`api_hash` Telethon
- [ ] Bot de contrôle Telegram (`chat_id`)
- [ ] Clés API modèles (extraction + Anthropic), plafonds de dépense fixés
- [ ] VPS Linux **provisionné** (Ubuntu 26.04, Scaleway, `163.172.189.239`,
      code déployé, `.env` en place) — reste à faire : durcissement complet
      selon guide P0 §4.2 (pare-feu, fail2ban, etc., non audité), et
      permissions du futur fichier `.session` Telethon quand il existera
      (voir pivot Telegram ci-dessus)
- [x] Dépôt Git local **et distant** (GitHub privé, `origin` configuré et
      synchronisé le 16/08/2026, `.gitignore` vérifié, aucun secret commité)
- [x] Sauvegardes automatiques de la base SQLite **avec synchronisation hors
      VPS** (`scripts/backup_and_sync.sh`, cron quotidien 03:00, Scaleway
      Object Storage — voir détail ci-dessus)

## Ce qu'il ne faut jamais faire

- Passer `CAPITAL_ENVIRONMENT` en `live` manuellement — seul le verrou
  Go/No-Go (code) décide
- Committer `.env`, `*.session`, ou toute clé/mot de passe
- Écrire du code qui se connecte au compte réel OANDA d'Ismaël
- Utiliser les marchés `_W` (week-end synthétiques) sans validation explicite
- Lancer une exécution, même démo, avant que les tests `risk_engine`,
  `capital_manager`, `go_nogo` soient à 100% (ils le sont actuellement —
  vérifier qu'ils le restent après toute modification)
- Modifier les plafonds de risque à chaud
- Traiter le futur fichier `.session` Telethon comme un simple detail
  technique — il donne accès au compte Telegram personnel complet
  d'Ismaël (voir pivot Telegram ci-dessus)
