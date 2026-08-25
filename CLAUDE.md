# Assistant Trading — Contexte pour Claude Code

Agent de trading automatisé, usage strictement personnel, non commercialisé.
Ismaël est le porteur du projet, non technique. Modèle de travail : produire
du code complet prêt à coller/exécuter et des instructions pas-à-pas,
jamais de pseudo-code.

Le cahier des charges v4 fait autorité : `docs/CDC_v4.md` (ajouté au dépôt
le 16/08/2026). Autonomie accordée par Ismaël le 16/08/2026 sur
l'architecture, le découpage des modules, le choix déterministe vs LLM et
la structure de données — seuls les 10 invariants de son §4.2 et la
couverture 100% des modules financiers critiques (`risk_engine`,
`capital_manager`, `go_nogo`, futur `executor`) sont non négociables.
Tout écart notable au CDC littéral doit être journalisé dans
`docs/DECISIONS.md` (raisonnement, alternative écartée) — pas de
validation préalable requise, traçabilité obligatoire après coup. Pour
toute autre contradiction (hors autonomie déléguée), la signaler avant
d'agir plutôt que d'appliquer silencieusement.

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

## Palier P1 — terminé et vérifié sur données réelles (16/08/2026)

Voir `docs/DECISIONS.md` pour le raisonnement détaillé de chaque écart au
CDC listé ci-dessous.

### Fait

- `src/message_classifier.py` : classification déterministe en 4
  catégories (matinale/signal/suivi/autre — "autre" ajoutée pour les
  bilans auto-déclarés du canal, jamais nos métriques, §3.10). 10 tests.
- `src/parser.py` : `extract_signal`, `extract_suivi`, `extract_matinale`
  — 100% déterministe (écart documenté au §4.4 littéral qui prévoyait un
  LLM ici, voir `docs/DECISIONS.md`). 14 tests, validés sur exemples réels
  du canal.
- **Étape 0 — réconciliation du schéma DB** : `src/db.py` réécrit pour
  suivre `docs/CDC_v4.md` §4.5 littéralement, plus deux tables hors CDC
  documentées (`matinale_summaries`, `suivi_events`) et deux tables P0
  conservées (`risk_decisions`, `go_nogo_events`). Rien ne dépendait de
  l'ancien schéma (vérifié avant réécriture) — aucune régression. 7 tests.
- `src/audit_notifier.py` : notification Telegram vers le bot de contrôle
  (§3.6, §7.2), portée volontairement réduite par rapport au
  `control_bot` complet du CDC (les commandes du §7.1 dépendent de
  modules qui n'existent pas encore — YAGNI, voir `docs/DECISIONS.md`).
  8 tests.
- **Étape 2 — `src/telegram_listener.py`** : `process_message()`
  (logique métier pure, testable sans Telegram réel — routage
  classifier → parser → DB → notification, idempotent) + `run_listener()`
  (câblage Telethon, import différé). 8 tests sur `process_message`.
  `run_listener` lui-même non testable unitairement (nécessite une
  session Telethon réelle).
- **Étape 4 — audit manuel** : `process_message` notifie chaque
  signal/Matinale extrait pendant la fenêtre `audit_all=True` (§3.6, "3
  premières semaines" — implémenté comme un paramètre simple, pas un
  mécanisme de calendrier, à désactiver à la main plus tard). Les
  contradictions Matinale (§3.4) sont notifiées en permanence, même après
  désactivation de `audit_all`.
- 93 tests passent, aucune régression. Couverture 100% toujours vérifiée
  sur `risk_engine`/`capital_manager`/`go_nogo`.

### Fait — validation en conditions réelles (16/08/2026)

- Authentification Telethon faite par Ismaël sur le VPS (session `tmux
  telegram_listener`), canal Station X confirmé être un **canal privé
  sans `@username` public** (id numérique `-1002481537588` — l'ancien
  `@station_x` dans `.env` était un placeholder jamais corrigé, cause
  du premier échec réel, voir `docs/DECISIONS.md`).
- **Backfill de 50 messages réels** (`--backfill=N`, ajouté à
  `telegram_listener.py`, utilise la session déjà authentifiée, aucun
  risque de conflit) : 21 autre / 17 signal / 10 suivi / 2 matinale.
  8 signaux extraits complets, 9 rejetés (tous des alertes `"... NOW !"`
  légitimes sans prix). Threading (`reply_to_msg_id`) confirmé
  fonctionnel sur un cas réel (TP1 touché lié au bon signal d'origine).
- **Deux bugs réels trouvés et corrigés** sur `parser.py` en inspectant
  ces données : séparateur de milliers "espace" non géré (prix
  silencieusement faux) et tickers d'indices avec chiffres (NAS100/US100)
  non capturés (échec silencieux). Détail et tests de régression dans
  `docs/DECISIONS.md`. Base réinitialisée et recapturée après correctif.
- Compat Telethon 1.36.0 / Python 3.14 : `RuntimeError: no running event
  loop` au premier lancement (asyncio ne crée plus de boucle implicite) —
  corrigé par une boucle explicite passée au constructeur. Détail dans
  `docs/DECISIONS.md`.
- **`audit_notifier` confirmé de bout en bout** : appel réel à l'API
  Telegram Bot (pas un mock) depuis le VPS, réponse `ok=True`. Les 8
  signaux du backfill n'ont volontairement déclenché aucune notification
  (`audit_all=False` sur l'historique, choix documenté) — les captures
  **en direct**, à partir de maintenant, utilisent `audit_all=True` par
  défaut (§3.6, audit manuel intégral 3 semaines).
- Le listener tourne en direct sur le VPS (`tmux telegram_listener`),
  en observation, prêt à capturer le prochain vrai message.

### À surveiller (hérité de P1, toujours ouvert)

- La prochaine vraie Matinale, pour calibrer `extract_matinale()` sur le
  format actuel (celui du backfill, fév-mars 2025, était un simple titre
  court, pas la version narrative détaillée — probablement un format qui
  a évolué depuis).
- Calibration empirique GOLD/US100/US30 (`calibrate_pip_value.py`) un
  jour de semaine, marché fermé le week-end (déjà noté au palier P0).
- Exemples du canal "éducatif" à fournir par Ismaël quand disponibles.

## Palier P2 — en cours (exécution démo autonome, analyse de trade)

Écart de phasage assumé, pas choisi seul : le §4.8 du CDC prévoit pour
P2 une "exécution démo avec validation manuelle (rodage)" — l'autonomie
complète sans validation par trade est une demande explicite d'Ismaël
pour ce palier (16/08/2026), pas une décision d'architecture prise en
autonomie. Voir `docs/DECISIONS.md` pour cette entrée et toutes les
autres de ce palier (client Capital.com, ordres limite, péremption,
schéma DB, bug de verrou SQLite trouvé et corrigé, etc.).

### Fait

- `src/capital_client.py` (nouveau) : client HTTP Capital.com,
  factorise la logique déjà dupliquée dans 3 scripts P0. `requests`
  passe de dépendance de fait à dépendance déclarée. 23 tests.
- `src/market_data.py` : prix courant, bougies, ATR(14) de Wilder,
  moyenne mobile, conversion EUR **en direct** (lève le TODO bloquant de
  `asset_whitelist.py` — `build_asset_whitelist()` accepte désormais des
  taux rafraîchis via `market_data.get_eur_conversion_rate()`). 13 tests
  + 2 sur `asset_whitelist`.
- `src/validator.py` — **module critique, 100% de couverture** :
  revalidation d'un signal juste avant exécution (liste blanche, marché
  ouvert, péremption §2.8 — tolérance = 50% de la distance de stop,
  valeur non fixée par le CDC, choix documenté). 11 tests.
- `src/capital_manager.py` étendu : `apply_trade_result()` — règle de
  réinvestissement des 50% (§2.3, gain → moitié enveloppe / moitié
  réserve globale sanctuarisée). Toujours 100% couvert.
- `src/envelope_store.py` (nouveau) : persistance DB des enveloppes et
  de la réserve globale (`envelopes`, `envelope_ledger`,
  `reserve_ledger`), sans modifier `capital_manager.py` lui-même. 6 tests.
- `src/risk_engine.py` étendu : `compute_r_multiple`/
  `compute_weighted_r_multiple` (§2.1, §2.10) — un seul endroit pour
  tout calcul de R, jamais dupliqué dans `executor.py`. Toujours 100%
  couvert.
- **Étape 0 — migration DB** : `trades.deal_id` ajouté (identifiant
  broker, absent du §4.5 — oubli du schéma d'origine) ; nouvelle table
  `trade_analysis` (partie déterministe + narratif LLM, colonnes
  séparées pour qu'aucune confusion ne soit possible entre les deux,
  invariant #9).
- `src/executor.py` (nouveau, le plus gros module) : ordres **limite
  uniquement** (§2.8, jamais au marché — vérifié en direct sur le
  compte démo avant implémentation), gestion TP1(50%)/TP2(30%)/TP3(20%
  sous trailing 2×ATR, §2.10), SL au breakeven dès TP1, détection de
  remplissage d'ordre, annulation des ordres périmés. Partie
  décision/calcul (`decide_entry`, `compute_tp_allocations`,
  `evaluate_position_management`, `compute_trailing_stop_level`) à
  **100% de couverture** (demande explicite d'Ismaël) ; orchestration
  I/O à 92% (cohérent avec le traitement déjà appliqué à
  `telegram_listener.run_listener`). 29 tests. Bug réel trouvé et
  corrigé par ces tests : verrou SQLite (`database is locked`) causé par
  une `connection_scope` imbriquée dans une autre.
- `src/trade_analyzer.py` (nouveau) : correspond au module
  `post_trade_review` du CDC (§4.4, §3.10) sous un autre nom.
  `compute_trade_features()` déterministe (R-multiple lu, jamais
  recalculé, denouement, durée, écart signal/exécution, contexte) +
  `generate_narrative_summary()` (Anthropic, modèle rapide/économique
  `claude-haiku-4-5-20251001` — §3.1) avec garde-fou de sortie
  déterministe (`_check_narrative_guardrail`, motifs de jugement
  interdits, testé y compris pour ne jamais bloquer le vocabulaire
  neutre du CDC comme "trade gagnant"). 19 tests.
- `anthropic` et `requests` ajoutés à `requirements.txt` (déclarés,
  installés localement et sur le VPS).
- 211 tests passent, aucune régression. Couverture 100% vérifiée sur
  `risk_engine`/`capital_manager`/`go_nogo`/`validator`.

### Pas encore fait / vérifié

- **Déploiement VPS et démarrage de la boucle autonome** : le code est
  prêt et testé localement, reste à déployer sur le VPS et à décider
  avec Ismaël du moment de démarrage effectif de `run_executor_loop`
  (pas encore écrite comme point d'entrée `if __name__ == "__main__"` —
  seules les fonctions qu'elle appellerait sont construites et testées).

### P2 — validation réelle et passage en autonome (16-20/08/2026)

Test réel encadré effectué (feu vert d'Ismaël) : boucle démarrée sous
supervision, un trade BTCUSD réel ouvert → géré → fermé de bout en bout.
**4 bugs réels trouvés et corrigés** avant le passage en autonome (détail
complet dans `docs/DECISIONS.md`) :
1. Migration de schéma manquante (`trades.deal_id` absent sur la base
   existante — corrigé par un mécanisme de migration de colonnes dans `init_db()`)
2. Stop garanti jamais envoyé à l'API (aurait fait échouer toute ouverture)
3. **Bug critique** : le `dealId` de la position résultante est différent
   de celui de l'ordre limite d'origine (le lien est `position.workingOrderId`)
   — `check_pending_fills` ne détectait jamais un remplissage réel avant
   correction ; confirmé et corrigé en observant un vrai remplissage BTCUSD
4. `trade_analyzer.py` était construit et testé mais jamais appelé depuis
   `executor.py` — câblé dans `_apply_management_action`

**`executor_loop` tourne en autonome sur le VPS depuis le 16/08/2026**
(tmux, aux côtés de `telegram_listener`). Audit du 19/08/2026 : aucun
crash, 26 signaux GOLD captés en direct, 0 trade ouvert (tous rejetés —
9 par péremption de prix, 4 par le garde-fou du stop garanti, GOLD
exigeant ~1% de distance minimum vs ~3 points typiques du canal). Aucune
tentative d'élargissement de stop ni de moyenne à la baisse observée.
Calibration GOLD/US100/US30 toujours bloquée le week-end (hérité de P0/P1).

## Palier P2.5 — Flux B (Hypothèse #1, 20/08/2026)

Implémente `docs/HYPOTHESES.md` (validée par Ismaël) : filtre de
tendance MA(200) + rupture de canal Donchian(20) sur les 5 actifs sans
signal Station X (US30, EURUSD, GBPUSD, USDJPY, ETHUSD). Détail complet
de l'architecture (process séparé, filtrage multi-source, migration
`envelopes.source`) dans `docs/DECISIONS.md`.

- `src/trend_strategy.py` — module `trend_strategy` du CDC (§2.11,
  §4.4), 100% couvert. Logique pure : régime MA(200) + canal de
  Donchian(20), aucun LLM.
- `src/trend_executor.py` — boucle autonome séparée (tmux `trend_executor`
  sur le VPS), réutilise le même `open_signal`/`manage_open_trades`/
  `validator`/`risk_engine` qu'`executor.py`, filtré par
  `source="hypothesis"`.
- Enveloppes démo strictement séparées (`envelopes.source`, migration de
  schéma appliquée), trades tagués `source` dans `trade_analysis`.
- 250 tests passent, 100% sur `risk_engine`/`capital_manager`/`go_nogo`/
  `validator`/`trend_strategy`.
- **Aucune conclusion statistique avant 10 trades** (invariant #10, un
  seul paramètre réglable pour cette hypothèse) — les trades du Flux B
  sont journalisés, jamais interprétés avant ce seuil.

## Palier P2.6 — Coupe-circuits (§2.7) + bot de contrôle minimal (§7.1, 20/08/2026)

Suite à un audit de conformité au CDC (19/08/2026) qui a confirmé
l'absence totale de coupe-circuits R et de commandes de contrôle à
distance — les deux manques les plus consequents côté protection.
Détail complet des choix (scoping par source, sémantique "reprise
manuelle", interprétations de la surcouche anomalie système,
dérogation `/stop_urgence`) dans `docs/DECISIONS.md`.

- `src/circuit_breaker.py` — module critique, 100% couvert. Logique pure
  des seuils R (§2.7 : -2R jour/actif, -5R semaine/actif, -12R depuis le
  plus haut/actif) + plafond d'exposition simultanée (§2.3, 10% de
  l'enveloppe) + surcouche anomalie système (3 erreurs API, ≥5 actifs
  simultanés, inactivité canal 7j).
- `src/circuit_breaker_store.py` — persistance (`circuit_breaker_events`,
  `system_state`, nouvelles tables hors §4.5), notifications, commandes
  /pause /reprendre /stop_urgence. Coupe-circuits scopés (actif, source)
  — écart assumé pour rester cohérent avec la séparation Station
  X/Flux B des enveloppes.
- `src/control_bot.py` — bot de contrôle Telegram, 4e process autonome
  (tmux `control_bot` sur le VPS). Premier lot du §7.1 : `/etat`,
  `/pause [actif]`, `/reprendre [actif]`, `/stop_urgence`. Authentifie
  l'expéditeur par `chat_id` (pas de nouvelle variable d'environnement).
  N'ouvre jamais de session broker : écrit un événement de contrôle,
  lu et appliqué par `executor.py`/`trend_executor.py` à leur cycle
  suivant.
- `executor.py`/`trend_executor.py` étendus : `open_signal` vérifie le
  blocage (coupe-circuit + plafond d'exposition) avant toute ouverture ;
  `force_close_all_open_trades()` (nouveau) implémente `/stop_urgence`
  — seule dérogation assumée à "ordres limite uniquement" (§2.8), une
  action de sécurité manuelle n'est pas l'exécution d'un signal.
- Bug réel trouvé par les tests avant déploiement : `record_trigger`
  horodatait avec l'horloge réelle plutôt que le `now` évalué par
  l'appelant, cassant la déduplication "déjà déclenché aujourd'hui" —
  corrigé, `now` est désormais toujours explicite, jamais recalculé en
  interne.
- 318 tests passent, 100% sur `risk_engine`/`capital_manager`/`go_nogo`/
  `validator`/`trend_strategy`/`circuit_breaker`.
- **Watchdog processus** (`scripts/process_watchdog.py`, cron VPS toutes
  les 5 min) : vérifie que `telegram_listener`/`executor_loop`/
  `trend_executor`/`control_bot` tournent (`pgrep -f`, pas une présence
  de session tmux — les deux se désynchronisent en production). Alerte
  Telegram une fois par transition vivant->mort, jamais de redémarrage
  automatique. Motivé par la mort silencieuse de `trend_executor` le
  19/08/2026 — cause non identifiée avec certitude (OOM/cron/fail2ban/
  reboot écartés, accès root manquant pour aller plus loin ; point clos
  par Ismaël tant que ce n'est pas récurrent, voir `docs/DECISIONS.md`).

## Palier P2.7 — `metrics` + `dashboard` (§4.4, §4.5, §4.6, 20/08/2026)

Plan validé par Ismaël avant codage. Détail des écarts dans
`docs/DECISIONS.md`.

- `src/metrics.py` — 100% couvert (demande explicite d'Ismaël : future
  base de la bascule 2%/4% et du score de confiance). Calcul à la
  demande (pas de snapshot périodique dans `metrics_snapshot`) :
  R-multiple, espérance, profit factor, taux de réussite indicatif,
  drawdown courant/max par (actif, source), gains/pertes en euros par
  période (semaine/mois/depuis le début) sourcés sur `envelope_ledger`.
- `src/dashboard.py` + commande `/dashboard` sur `control_bot.py` :
  page HTML autonome (§4.6, dans l'ordre exact), envoyée en pièce
  jointe Telegram — **pas de serveur web, même temporaire** (écart
  assumé au §4.6 littéral, validé par Ismaël). Blocs Hypothèses
  (générateur officiel)/Classement/Décisions affichés vides et
  étiquetés "non construit" (modules absents), jamais simulés.
- `audit_notifier.send_document()` (nouveau) : seule fonction du module
  à utiliser `requests` plutôt que `urllib` (upload multipart).
- 368 tests passent, 100% sur `risk_engine`/`capital_manager`/`go_nogo`/
  `validator`/`trend_strategy`/`circuit_breaker`/`metrics`.
- **`/aide` + menu natif Telegram** (`control_bot.COMMANDS`, source
  unique) : `register_bot_commands()` (`setMyCommands`) est appelée à
  chaque démarrage de `run_control_bot_loop` — le menu Telegram se
  rafraîchit automatiquement à chaque redémarrage de `control_bot`,
  aucune étape séparée à lancer. **Pour toute future commande** : ajouter
  `(nom, description)` à `COMMANDS` + la branche dans `handle_command`,
  puis redémarrer `control_bot` sur le VPS — voir `docs/DECISIONS.md`.
  376 tests passent au total.

## Palier P2.8 — `confidence_scorer` (§2.4), mode observation uniquement (20/08/2026)

Demande explicite d'Ismaël, en mode observation uniquement : aucune
décision réelle n'en dépend encore (`allocator.py` §2.5 et le verrou
§4.9 restent volontairement non construits). Détail complet des deux
écarts assumés (unité de `drawdown_max`, choix de ne pas persister
`confidence_scores` à chaque calcul) et du gap de données identifié
dans `docs/DECISIONS.md`.

- `src/confidence_scorer.py` — **100% couvert** (demande explicite
  d'Ismaël, même régime que `risk_engine.py`, y compris l'orchestration
  I/O). Score exact du §2.4 (conditions éliminatoires + `espérance ×
  facteur_échantillon × facteur_stabilité`), calculé par (actif, source)
  séparément, à la demande (pas de snapshot périodique dans
  `confidence_scores`, même choix que `metrics.py`/`metrics_snapshot`).
  Constante `MULTIPLE_COMPARISONS_CAVEAT` exposée pour rappeler que ce
  score est indicatif tant qu'`hypothesis_engine` (§3.9, correction
  multiple-comparaisons) n'existe pas.
- **Gap de données réel trouvé en construisant ce module** : la
  condition éliminatoire "spread médian < 15% du stop typique" ne peut
  être satisfaite par AUCUN actif à ce jour — `market_snapshots.spread`
  existe dans le schéma mais n'est écrit par aucun code du projet
  (`executor.py`/`trend_executor.py` ne l'alimentent jamais). Traité en
  fail-safe (donnée manquante = condition non satisfaite, jamais
  court-circuitée à vrai), pas corrigé ici (hors périmètre de la
  demande, sans impact avant le seuil de 20 trades). Câblage candidat :
  `market_data.get_price_snapshot()` à l'ouverture d'un trade.
- `src/dashboard.py` : bloc "Classement" (§4.6) câblé, affiche
  désormais le classement réel (éligible/non éligible, score, taille
  d'échantillon, raison(s) d'inéligibilité) au lieu de "non construit".
- `tests/test_confidence_scorer.py` (42 tests, 100%),
  `tests/test_dashboard.py` étendu. 496 tests passent au total, 100%
  toujours vérifié sur `risk_engine`/`capital_manager`/`go_nogo`/
  `validator`/`trend_strategy`/`circuit_breaker`.

## Palier P2.9 — Hypothèses #3 et #2 (21/08/2026)

Feu vert explicite d'Ismaël après reconsidération et correction du
modèle de budget de variables (§2.11 vs §3.8, voir `docs/HYPOTHESES.md`
et `docs/DECISIONS.md` du 21/08/2026 — chaque hypothèse a son propre
budget de 2-3 paramètres, jamais partagé).

- **Bug bloquant corrigé avant tout code d'hypothèse** :
  `_normalize_source`/`_envelope_source_key` (dupliquée dans 4 modules)
  ne reconnaissait QUE `"hypothesis"` (H1) — toute autre source serait
  retombée silencieusement sur `"stationx"`. Généralisée à un ensemble
  de sources connues + garde-fou de cohérence entre les 4 copies
  (`tests/test_source_normalization_consistency.py`). Second bug trouvé
  et corrigé dans la foulée : le trailing (`executor.manage_open_
  trades`) récupérait toujours des bougies horaires quelle que soit la
  résolution de l'hypothèse — `executor._TREND_CANDLE_RESOLUTION` ajouté.
- `src/technical_strategy_executor.py` (nouveau) : moteur générique de
  boucle extrait de `trend_executor.py` — H1, H3 et H2 partagent
  désormais la même orchestration (ordres, coupe-circuits,
  /stop_urgence, enveloppes), ne diffèrent que par leurs paramètres.
  Comportement de l'Hypothèse #1 vérifié strictement inchangé par
  régression.
- **Hypothèse #3** (`src/hypothesis3_executor.py`) — déployée : identique
  à H1 (`trend_strategy.py` réutilisé tel quel), résolution M15,
  8 actifs, compte Capital.com dédié (`CAPITAL_ACCOUNT_ID_HYPOTHESIS3`
  retrouvé via `GET /accounts`, ajouté à `.env`).
- **Hypothèse #2** (`src/ict_strategy.py`, module critique 100% couvert,
  34 tests + `src/hypothesis2_executor.py`) — Option B (swings fractals
  K=2, confluence Fibonacci, FVG) : code construit et testé, **PAS
  déployée** — identifiants Capital.com dédiés au compte "hypothèse 2"
  manquants (voir `docs/DECISIONS.md` pour les options).
- 549 tests passent au total, 100% toujours vérifié sur
  `risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`/
  `circuit_breaker`/`ict_strategy`.
- `scripts/process_watchdog.py` étendu à `hypothesis3_executor` (pas
  `hypothesis2_executor`, pas encore démarré — l'ajouter aurait
  déclenché une fausse alerte).

## Palier P2.9 (suite) — Hypothèses #4 et #5 (21 et 23/08/2026)

Note : H4 avait été construite et validée en démo le 21/08/2026 (voir
`docs/HYPOTHESES.md`/`docs/DECISIONS.md`) mais jamais reportée ici —
gap comblé rétroactivement en même temps que l'ajout de H5.

- **Hypothèse #4** (`src/mean_reversion_strategy.py`, module critique
  100% couvert + `src/hypothesis4_executor.py`) — retour à la moyenne
  (MA200 + Bandes de Bollinger 20/2σ) : 3e mécanisme de sortie construit
  dans `executor._evaluate_position_management`
  (`ManagementActionType.CLOSE_FULL_TP`, TP/stop fixes, aucun trailing).
  **Correctif de documentation, 23/08/2026 après-midi** : contrairement à
  ce que CLAUDE.md/DECISIONS.md affirmaient depuis le 21/08/2026 (« PAS
  déployée, identifiants manquants »), constaté en vérifiant l'état réel
  du VPS pendant cette session : `hypothesis4_executor` tourne en
  production (tmux, actif depuis le 21/08/2026 20:50, identifiants
  présents dans le `.env` du VPS) et a déjà produit 2 trades réels
  (23/08/2026, 04:51-05:10 UTC). Démarré manuellement par Ismaël en SSH
  après la session qui l'a construit, jamais resynchronisé dans la
  documentation — gap comblé ici, aucune autre investigation menée (hors
  périmètre de cette session).
- **Hypothèse #5, version d'origine** (23/08/2026, matin) — même entrée
  ICT que H2, seule la sortie changeait (TP1 1R/TP2 2R/reliquat 20%
  trailing). **Jamais déployée, jamais un seul trade** — remplacée le
  jour même par la redéfinition ci-dessous avant toute exécution
  réelle.

## Palier P2.9 (suite) — Bascule du régime H2 + redéfinition et déploiement réel de H5 (23/08/2026, après-midi)

- **Hypothèse #2 — bascule du régime** : `ict_strategy.py` n'utilise
  plus `trend_strategy.compute_regime` (MA200) pour son régime de fond —
  nouvelle fonction `compute_structural_regime` (réutilise
  `classify_structure_break`, codée depuis l'origine mais jamais
  branchée). Déclencheur (K=2, Fibonacci, FVG) et sortie (trailing
  Donchian(20)) inchangés. Nouvelle colonne `trades.regime_type`
  (`"ma200"` | `"structural_bos_choch"`) — les 2 trades H2 antérieurs à
  la bascule sont rétro-remplis `"ma200"`, jamais mélangés aux futurs
  trades structurels. **Déployé et actif sur le VPS**
  (`hypothesis2_executor` redémarré).
- **Hypothèse #5 — REDÉFINIE** (`src/hypothesis5_strategy.py`,
  réécriture complète, module critique 100% couvert) : régime
  structurel (hérité du H2 post-bascule) + confluence ICT de H2 **ET**
  RSI(14) franchissant 50 dans le même sens, même bougie — les deux
  conditions réunies pour entrer (limite assumée : impossible d'isoler
  la contribution de chacune, voir `docs/HYPOTHESES.md`). Sortie §2.10
  inchangée (TP1 1R/TP2 2R/reliquat 20% trailing ATR), toujours branchée
  sans modification d'`executor._evaluate_position_management`.
  Indépendance vis-à-vis de Station X vérifiée (aucune lecture DB dans
  le module, toutes les valeurs viennent des bougies H5 elles-mêmes).
  **Déployée et active sur le VPS depuis le 23/08/2026 après-midi**
  (4 identifiants complétés — compte "hypothèse 5" identifié via
  `GET /accounts` en lecture seule, accountId `328096601896998046` —
  voir `docs/DECISIONS.md` pour le détail complet, dont deux expositions
  de secrets supplémentaires signalées en cours de route, aucune
  réutilisée).
- Correction pour comparaisons multiples (§3.9) : calibrée sur **5
  hypothèses simultanées (H1-H5), jamais 4** — toute future
  évaluation/validation devra l'appliquer (voir `docs/HYPOTHESES.md`).
- 659 tests passent au total, 100% toujours vérifié sur
  `risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`/
  `circuit_breaker`/`ict_strategy`/`mean_reversion_strategy`/
  `confidence_scorer`/`hypothesis2_strategy`/`hypothesis3_strategy`/
  `hypothesis5_strategy`.
- `scripts/process_watchdog.py` surveille désormais les 4 hypothèses
  déployées (`hypothesis2_executor`/`hypothesis3_executor`/
  `hypothesis4_executor`/`hypothesis5_executor`, ce dernier ajouté au
  démarrage de H5 le 23/08/2026 après-midi) — les 4 tournent
  réellement, plus aucune hypothèse construite en attente d'identifiants
  à ce jour.

## Palier P2.9 (suite) — Sortie TP1/TP2/trailing pour H2 et H3 (23/08/2026, décision explicite d'Ismaël)

Va à l'encontre de ma recommandation de préserver H3 comme copie exacte
de H1 (isolation "timeframe seule", entrée du 20/08/2026 de
`docs/HYPOTHESES.md`) — décision assumée pleinement par Ismaël, pas la
mienne. Détail complet dans `docs/DECISIONS.md`/`docs/HYPOTHESES.md`
(23/08/2026).

- H2 et H3 : trailing Donchian(20) pur remplacé par le mécanisme §2.10
  (TP1 50% à 1R/TP2 30% à 2R/TP3 20% trailing 2×ATR) déjà câblé pour
  Station X et H5 — aucune nouvelle logique de sortie, `executor.
  _evaluate_position_management` non modifié. **H1 reste inchangée**,
  seul témoin encore en trailing pur.
- `src/hypothesis2_strategy.py`/`src/hypothesis3_strategy.py` (nouveaux,
  modules critiques 100% couverts) : enveloppent `ict_strategy.
  evaluate_entry`/`trend_strategy.evaluate_entry` (INCHANGÉS) et
  ajoutent TP1(1R)/TP2(2R) via `trend_strategy.compute_tp_levels`
  (nouvelle, partagée). `trend_strategy.TrendSignal` étendu avec
  `tp1`/`tp2` (défaut `None`, zéro impact sur H1, testé explicitement).
- Prospectif uniquement : les trades H2/H3 déjà ouverts/clos gardent
  leur trailing pur d'origine jusqu'à clôture normale — vérifié
  explicitement (`_load_open_trade_state` lit `tp1`/`tp2` depuis le
  signal d'origine, jamais recalculés).
- Nouvelle colonne `trades.exit_type` (`"trailing_pur"` |
  `"tp_partiel"` | `"tp_fixe"`) — dimension INDÉPENDANTE de
  `regime_type` (l'une porte sur l'entrée, l'autre sur la sortie),
  migration + rétro-remplissage par source (`db._backfill_exit_type`),
  même patron que `regime_type` du 23/08/2026 matin.
- 659 tests passent au total, 100% toujours vérifié sur
  `risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`/
  `circuit_breaker`/`ict_strategy`/`mean_reversion_strategy`/
  `confidence_scorer`/`hypothesis2_strategy`/`hypothesis3_strategy`/
  `hypothesis5_strategy`.

## Palier P2.9 (suite) — Rate-limiting Capital.com : échelonnement + retry ciblé ; Hypothèse #5 V3 (24/08/2026)

Suite à une investigation demandée par Ismaël sur des trades manqués
dans la journée et l'inactivité totale de H5 — diagnostic : 6 process
concurrents (executor_loop/trend_executor/H2-H5, tous déployés
ensemble le 23/08/2026) pollent l'API Capital.com depuis la même IP,
provoquant des milliers de 429/jour. Détail complet (chiffres du
diagnostic, rationale des deux correctifs, tableau des décalages,
estimation de fréquence H5) dans `docs/DECISIONS.md`/`docs/HYPOTHESES.md`
du 24/08/2026.

- **Échelonnement fixe des 6 process** : nouveau paramètre
  `startup_offset_seconds` sur `run_executor_loop`/`run_technical_
  strategy_loop` (pause unique avant le premier appel réseau) —
  executor_loop=0s, trend_executor=10s, H2=20s, H3=30s, H4=40s, H5=50s.
- **`src/retry.py`** (nouveau) : `retry_with_backoff`, 3 tentatives/
  backoff court, appliqué à exactement deux points (jamais aux ordres,
  jamais en décorateur générique — voir docs/DECISIONS.md) : la sonde
  de connectivité générale en début de cycle, et le rafraîchissement du
  contexte de régime croisé H3/H4 (`regime_confirmation.
  compute_index_regimes`) — ce dernier identifié comme le point de
  défaillance le plus coûteux (jusqu'à ~8h de rejets fail-safe "régime :
  None" par échec transitoire, faute de retry).
- **Hypothèse #5 — V3** (`src/hypothesis5_strategy.py`) : retrait de la
  confluence ICT (Fibonacci/FVG), motivé par 0 signal produit en ~26h
  avec l'ancienne définition (pas un ajustement sur trades — H5 n'en a
  produit aucun). Devient régime structurel + RSI(14)/50 seuls.
  `src/ict_strategy.py` refactoré (extraction de `_find_regime_and_leg`,
  nouvelle fonction publique `compute_structural_entry`) pour exposer
  régime+jambe sans la confluence, réutilisée par H5 — comportement de
  `evaluate_entry` (H2) vérifié strictement inchangé par régression.
- 712 tests passent au total (659 + 53 nouveaux/modifiés sur ce lot,
  dont 7 pour `retry.py`), 100% toujours vérifié sur `risk_engine`/`capital_manager`/`go_nogo`/
  `validator`/`trend_strategy`/`circuit_breaker`/`ict_strategy`/
  `mean_reversion_strategy`/`confidence_scorer`/`hypothesis2_strategy`/
  `hypothesis3_strategy`/`hypothesis5_strategy`/`regime_confirmation`.
- **Déploiement VPS effectué le 24/08/2026** : `git pull` puis
  redémarrage des 6 process (tmux) pour que les nouveaux
  `startup_offset_seconds` prennent effet. Vérification en direct sur
  plusieurs heures en cours (taux de 429 avant/après, fréquence des
  rejets "régime : None", premier signal H5 V3 le cas échéant) — voir
  `docs/DECISIONS.md` pour le suivi.

## Palier P3 — Backtest rétrospectif (§2.11, 24/08/2026, soir)

Pré-enregistré dans `docs/HYPOTHESES.md` avant tout code/donnée, deux
vérifications empiriques préalables (profondeur d'historique démo ~2 ans,
plafond `max=1000`/requête, rate-limit partagé entre clés API — pas
isolable par clé dédiée). Détail complet (méthodologie, modèle de coûts,
bug réel trouvé pendant les tests) dans `docs/HYPOTHESES.md`/
`docs/DECISIONS.md` du 24/08/2026 (soir).

- `src/backtest_engine.py` (nouveau, **module critique, 100% couvert**) :
  rejoue les 5 hypothèses sur historique local, fenêtre glissante stricte
  (jamais de bougie future), réutilise `executor.decide_entry`/
  `evaluate_position_management`/`risk_engine`/`capital_manager` SANS
  AUCUNE modification — modèle de coûts (spread bid/ask réel + slippage
  forfaitaire 100% du spread + financement 1bp/jour, constantes a priori)
  appliqué autour de ces appels, jamais dedans.
- `scripts/download_historical_data.py`/`scripts/run_retrospective_
  backtest.py` (nouveaux) : téléchargement en masse ponctuel (throttlé,
  jamais depuis les boucles live) puis rejeu 100% local (aucun appel
  réseau), persistance sous des sources dédiées `*_backtest` (jamais les
  sources live) via les tables existantes.
- `src/confidence_scorer.py` : seuils d'éligibilité backtest distincts et
  plus élevés (`PHASE_A_MIN_TRADES_BACKTEST=60`/`PHASE_B_MIN_TRADES_
  BACKTEST=150` vs 20/50 en live), passés en paramètres optionnels —
  défaut live inchangé par construction.
- `executor.open_signal` : nouveau garde-fou Option B
  (`_check_backtest_confidence_gate`) — rejette un signal live AVANT
  sizing si son backtest est éligible ET d'espérance nette ≤ 0. Jamais
  Station X. Ne module jamais le risque à la hausse, `risk_engine.py`
  non modifié. **Sans effet tant qu'aucun backtest n'a tourné pour un
  couple donné** (comportement live inchangé par construction).
- **Bug réel trouvé pendant les tests** : le garde-fou utilisait d'abord
  `ConfidenceScore.eligible`, qui exige déjà une espérance > 0 (§2.4) —
  rendait le garde-fou structurellement inatteignable pour son propre cas
  d'usage. Corrigé (suffisance d'échantillon vérifiée séparément de
  l'espérance) — voir `docs/DECISIONS.md`.
- 760 tests passent au total (712 avant ce lot), 100% toujours vérifié
  sur `risk_engine`/`capital_manager`/`go_nogo`/`validator`/
  `trend_strategy`/`circuit_breaker`/`ict_strategy`/
  `mean_reversion_strategy`/`confidence_scorer`/`hypothesis2_strategy`/
  `hypothesis3_strategy`/`hypothesis5_strategy`/`regime_confirmation`/
  `backtest_engine`. `risk_engine.py` non modifié (vérifié).
- Déploiement/exécution en direct : voir `docs/DECISIONS.md` pour l'état
  à jour (téléchargement + rejeu à exécuter/vérifier sur le VPS).

## Palier P3 (suite) — Moteur d'analyse causale (§3.11) + capture réelle du spread (§2.6, 24/08/2026 soir)

Deux prérequis identifiés lors de la proposition du cycle autonome
(§3.9, palier séparé, non construit dans cette session), construits
dans cet ordre : spread d'abord (le moteur causal en dépend pour son
contexte), moteur causal ensuite. Détail complet dans
`docs/DECISIONS.md`.

- `executor.open_signal` capture désormais le spread bid/ask réellement
  observé (`market_snapshots`) pour CHAQUE signal évalué, approuvé ou
  non — ferme le gap qui bloquait la condition d'éligibilité spread de
  `confidence_scorer.py` pour toute source live, vérifié bout en bout.
- `src/causal_analyzer.py` (nouveau, **module critique, 100% couvert**) :
  §3.11 relu en entier avant construction. Déclenché automatiquement à
  chaque coupe-circuit R (jamais les déclencheurs administratifs),
  câblé dans `circuit_breaker_store.is_asset_blocked` sans toucher à sa
  décision de blocage (double filet de sécurité, testé). Classification
  100% déterministe en 3 catégories (anomalie_technique/
  evenement_marche/hypothese_pattern, texte du CDC), `analyse_texte`
  en gabarit déterministe (jamais un LLM, écart assumé vs
  `trade_analyzer.py`). Ne propose et n'applique jamais rien — écrit
  uniquement `causal_analysis_log`, la promotion en proposition reste
  la charge du cycle autonome séparé.
- Gap identifié mais non comblé (hors périmètre) : `macro_events`
  toujours vide, §2.9 calendrier macro non construit — la branche
  "événement macro" du classificateur est correcte mais sans donnée.
- 811 tests passent au total (760 avant ce lot), 100% toujours vérifié
  sur `risk_engine`/`capital_manager`/`go_nogo`/`validator`/
  `trend_strategy`/`circuit_breaker`/`ict_strategy`/
  `mean_reversion_strategy`/`confidence_scorer`/`hypothesis2_strategy`/
  `hypothesis3_strategy`/`hypothesis5_strategy`/`regime_confirmation`/
  `backtest_engine`/`causal_analyzer`. Aucune régression sur
  `confidence_scorer.py`/`circuit_breaker_store.py` (logique de
  décision intacte, vérifié par leurs suites de tests existantes).
- Déploiement/vérification en direct : voir `docs/DECISIONS.md`.

## Palier P3 (suite) — Premier rejeu réel du backtest, garde-fou Option B actif, notification + comparaison de coûts (24/08/2026 soir)

Le backtest (~2 ans d'historique, 8 actifs × 2 résolutions) a tourné
pour de vrai sur la base de production. Détail chiffré complet
(espérance par couple, comparaison 100%/50% de slippage) dans
`docs/DECISIONS.md`.

- **Le garde-fou Option B bloque désormais 24/40 couples (actif,
  hypothèse) en direct** — H5 sur 8/8 actifs, H4 sur 6/8, H1 sur 6/8,
  H3 sur 4/8, H2 sur 0/8 (encore trop peu de trades). C'était un no-op
  jusqu'au premier rejeu ; ça ne l'est plus.
- **Notification Telegram ajoutée** sur chaque rejet `backtest_
  confidence_gate` (`executor.open_signal`) — vérifiée en direct sur le
  VPS avec un signal réel sur un couple bloqué (US100/hypothesis),
  aucun appel broker, notification confirmée reçue.
- **Fenêtre sans notification documentée honnêtement** (avant cet
  ajout) : vérifié factuellement en base, 0 rejet réel n'a eu lieu
  pendant cette fenêtre — risque théorique, pas un incident.
- **Comparaison 100% vs 50% de slippage forfaitaire** (rejeu sur une
  base séparée, jamais celle qui pilote le live) : la dégradation
  sévère de H4 (négative sur tous les couples avec assez de données) et
  H5 (négative sur 8/8) **résiste** au changement d'hypothèse de coût —
  aucun changement de signe, seulement ~30-40% d'amélioration en
  magnitude. Un seul changement de signe sur l'ensemble des 32 couples
  testés (`hypothesis`/BTCUSD). Conclusion : la négativité de H4/H5
  n'est pas un artefact du choix de slippage précis.
- 821 tests passent, 100% de couverture maintenue.
- **Construction du cycle autonome (§3.9) volontairement mise en
  pause** — décision explicite d'Ismaël, en attente d'avoir vu l'effet
  de cette comparaison avant de bâtir dessus.

## Palier P3 (suite) — Évolution entraînement/validation H2-H5 : résultat nul, bug d'alignement corrigé (25/08/2026)

Détail chiffré complet (11 candidats, tableau espérance par candidat,
comparaison avant/après correctif) dans `docs/DECISIONS.md`.

- **Bug réel trouvé** : `backtest_engine.replay_hypothesis` alignait les
  bougies de l'actif et les séries de confirmation de régime (US30/US100,
  utilisées par H3/H4) par position dans la liste, pas par horodatage —
  ces séries n'ont pas le même nombre de bougies en pratique. Corrigé
  (pointeur monotone par horodatage), données H3/H4 en production purgées
  et régénérées avec le correctif.
- **Évolution H2/H3/H4/H5 (11 candidats testés au total sur
  l'entraînement seul) : AUCUN n'a une espérance positive** — la période
  validation n'a donc jamais été consultée pour aucune hypothèse (règle
  anti-fuite pré-enregistrée respectée). **Aucun fichier de stratégie
  modifié.**
- H4/H5 restent en pause pour ce chantier, sans nouvelle tentative
  prévue (limite fixée par avance). H2/H3 restent explorables si de
  nouvelles hypothèses théoriques émergent.
- Effet du correctif d'alignement sur les données H3/H4 déjà en
  production : conclusion qualitative inchangée pour H4 (toujours
  négative partout, amplitude quasi identique) ; un seul changement de
  signe (H3/GOLD, désormais légèrement positif et libéré du garde-fou
  Option B).
- 828 tests passent, 100% de couverture maintenue.

## Palier P3 (suite) — Cycle 2 de l'évolution H3/H4/H5 : axe timeframe, résultat nul, infrastructure d'application automatique (§3.9 débloqué, 25/08/2026)

Détail chiffré complet (8 candidats, tableau par hypothèse) dans
`docs/DECISIONS.md`.

- À la demande explicite d'Ismaël, le chantier d'évolution est
  "débloqué" : timeframe devient un axe explorable (entrée + confirmation
  croisée US30/US100 indépendamment), avec application automatique dès
  validation — **écart assumé et journalisé au §3.9 du CDC** ("jamais
  appliquée automatiquement"), couvert par l'autonomie déléguée du
  16/08/2026.
- **8 candidats testés (H3/H4/H5, H2 reportée), tous négatifs sur
  l'entraînement** — correction Bonferroni intra-hypothèse appliquée (le
  §3.9 l'exige explicitement pour comparaisons multiples), aucun n'a
  qualifié, validation jamais consultée. Aucun paramètre live modifié.
- **`src/hypothesis_params.py`** (nouveau, 100% couvert) : lecture
  fail-safe de la table `rule_changes` déjà au schéma, application
  UNIQUEMENT au démarrage de chaque `hypothesisN_executor.py` — jamais en
  cours de run. H3/H4/H5 câblés (résolution + TP/RSI/Bollinger/stop selon
  l'hypothèse). **Mécanisme construit et opérationnel, non exercé ce
  cycle** (rien n'a validé).
- `technical_strategy_executor.run_technical_strategy_loop` gagne
  `confirming_resolution` (optionnel, défaut = `resolution`,
  comportement inchangé sans argument explicite) — permet un timeframe
  d'entrée différent de celui de la confirmation croisée pour H3/H4,
  rendu possible par le correctif d'alignement du même jour.
- **Pas de crontab** — décision explicite après réflexion : l'étape
  GÉNÉRATION du §3.9 exige un raisonnement neuf par cycle, pas une grille
  figée rejouée automatiquement.
- **Cadence corrigée à 10 jours le 25/08/2026** (instruction explicite
  d'Ismaël, remplace le trimestriel) — prochaine échéance **2026-09-04**.
  Troisième écart CDC assumé (§3.9 écarte littéralement le mensuel ;
  défendable ici car le mécanisme est rétrospectif, pas prospectif — voir
  `docs/DECISIONS.md`/`docs/HYPOTHESES.md`). Règle explicite : un cycle
  sans justification théorique neuve conclut "rien à tester ce
  cycle-ci", jamais une justification inventée pour le calendrier.
  Aucun déclenchement automatique fiable (`CronCreate` expire avant 10
  jours) — échéance à vérifier manuellement.
- 845 tests passent, 100% de couverture maintenue. Déployé, suite verte
  sur le VPS, aucun redémarrage nécessaire (rien à appliquer).

## Palier P3 (suite) — Investigation trades réels vs backtest : bug réel trouvé et corrigé (positions simultanées, 25/08/2026)

Demande explicite d'Ismaël : expliquer l'écart entre trades réels
(certains gagnants) et l'espérance négative du backtest, avec
vérification explicite de l'absence de divergence de logique
live/backtest. Détail chiffré complet dans `docs/DECISIONS.md`.

- **Bug réel trouvé** : le 21/08/2026, 4 positions H3/ETHUSD ont été
  ouvertes SIMULTANÉMENT (garde-fou anti-doublon `_has_active_signal_
  or_trade` avait une fenêtre de course — `signals.statut` passait à
  'approuve' avant l'insertion de la ligne `trades`). Ces 4 positions
  expliquent +88.36€ sur les +87.65€ net d'ETHUSD/H3 — sans cet
  incident isolé (vérifié : seul cas sur toute la base), les résultats
  live H2-H4 sont majoritairement négatifs ou n=1, **cohérents avec le
  backtest, pas contradictoires**.
- **Config live confirmée identique au candidat A (référence)** déjà
  testé et rejeté dans les cycles 1/2 d'évolution — pas une config
  distincte non testée.
- **Corrigé le même jour** (`src/executor.py::open_signal`) : la ligne
  `trades` est désormais insérée AVANT l'appel réseau de placement
  d'ordre (deal_id NULL), visible au garde-fou immédiatement ; mise à
  jour du deal_id après succès, `statut='annule'` explicite en cas
  d'échec réseau. Correctif effectif pour les 6 process live (module
  partagé). 2 nouveaux tests, 847 passent au total.
- Déployé, 6 process redémarrés proprement (sessions tmux préservées),
  tous confirmés vivants après redémarrage.

## Palier P3 (suite) — Vérifications post-incident ETHUSD/H3 : plafond §2.3 confirmé respecté, trades exclus des stats §2.4 (25/08/2026)

- **Plafond d'exposition simultanée (§2.3, 10%) : NON dépassé** par les 4
  positions H3/ETHUSD de l'incident — pic réel 40,27€ = 8,05% de
  l'enveloppe (500€), sous les 50€ (10%). Point de vigilance journalisé
  (pas corrigé, hors périmètre) : `get_open_risk_eur` ne compte que
  `statut='ouvert'`, pas `'en_attente'` — marge favorable plutôt que
  garde-fou actif dans ce cas précis.
- **Nouvelle colonne `trades.anomalie_technique`** (TEXT, NULL par
  défaut) : `metrics.get_closed_trades_r_for_stats` exclut désormais tout
  trade marqué (même patron que l'exclusion `stop_urgence` déjà en
  place) — les 4 trades de l'incident sont exclus des stats §2.4/du
  dashboard, jamais du P&L réel (`circuit_breaker_store`, non affectée).
  ETHUSD/hypothesis3 passe de n=9/+87,65€ à n=4/-0,71€ une fois exclu —
  cohérent avec le reste de H3.
- 849 tests passent, 100% de couverture maintenue sur `db.py`/`metrics.py`.

## Palier P3 (suite) — Cycle 3 de l'évolution H4/H5 : espace de recherche élargi, résultat nul (25/08/2026)

Détail complet dans `docs/DECISIONS.md`.

- Budget de variables vérifié AVANT tout calcul (demande explicite
  d'Ismaël) : H4 3/5→4/5, **H5 4/5→5/5 (plafond atteint)** — 1 seule
  nouvelle variable ajoutée par hypothèse ce cycle, pas de dérogation
  improvisée.
- H4-B (Bollinger+RSI confluence) : améliore nettement l'espérance
  (-0,29R→-0,14R) mais reste négatif, ne qualifie pas.
- H5-B (confluence ICT complète Fibo+FVG réintroduite) : seulement 4
  signaux sur 1,5 an/8 actifs — confirme empiriquement la décision V3 du
  24/08/2026 de retirer cette confluence (jusque-là motivée par 0 signal
  en 26h de live seulement, jamais testée statistiquement).
- **Aucun candidat qualifié, validation jamais consultée, aucun fichier
  de stratégie modifié.** Écart CDC §2.11 (2-3 paramètres) formalisé
  comme dépassé depuis plusieurs paliers — à intégrer dans une future
  révision du CDC v4.

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
- **Passer un ordre manuel sur le compte démo Capital.com configuré
  dans `.env`** (celui-là même que `executor.py`/`trend_executor.py`
  utilisent) — réservé strictement au système (exécution automatique +
  scripts de calibration `calibrate_pip_value.py`). Un trade manuel
  signalé le 19/08/2026 a nécessité une vérification complète (aucun
  écart trouvé au final, voir `docs/DECISIONS.md`) : tout test manuel de
  la plateforme à l'avenir doit se faire sur un **compte démo Capital.com
  séparé**, jamais celui-ci — pas besoin de le re-vérifier à chaque fois.
