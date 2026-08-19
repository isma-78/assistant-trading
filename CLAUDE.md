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
