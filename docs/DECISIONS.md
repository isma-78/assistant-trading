# Journal de décisions — écarts au CDC v4 littéral

Autonomie accordée par Ismaël le 16/08/2026 : je tranche seul sur
l'architecture, le découpage des modules, le choix déterministe vs LLM et
la structure de données, à condition d'évaluer explicitement fiabilité /
coût / latence / auditabilité / maintenabilité plutôt que de suivre
`docs/CDC_v4.md` à la lettre quand une meilleure option existe. Seuls les
10 invariants de son §4.2 et la couverture 100% des modules financiers
critiques (`risk_engine`, `capital_manager`, `go_nogo`, futur `executor`)
sont non négociables. Ce fichier journalise chaque écart notable : entrée
la plus récente en tête.

---

## 2026-08-21 — Lancement de l'Hypothèse #2 — bug générique trouvé et corrigé au premier démarrage

Ismaël a fourni les identifiants Capital.com dédiés à l'Hypothèse #2
(`CAPITAL_API_KEY_HYPOTHESIS2`/`CAPITAL_IDENTIFIER_HYPOTHESIS2`/
`CAPITAL_API_PASSWORD_HYPOTHESIS2`, ajoutés directement dans `.env` local
par lui — jamais collés dans la conversation, transférés vers le VPS via
un fichier temporaire jamais affiché dans aucune sortie d'outil, même
principe que pour H3 le 20/08/2026). `CAPITAL_ACCOUNT_ID_HYPOTHESIS2`
(327950654613312670) était déjà en place depuis la découverte du compte
lors de la préparation de H3. Test de connexion en lecture seule réussi
en local avant tout déploiement : identifiants valides, compte
"hypothèse 2" confirmé, 0 position/ordre existant.

**`hypothesis2_executor` a planté au tout premier démarrage** :
`error.not-different.accountId`. Cause générique, pas spécifique à H2 —
le compte "hypothèse 2" se trouvait être le compte "préféré" partagé au
moment du login (voir l'incident du 20/08/2026 sur ce même flag). Notre
`CapitalClient.switch_account()` traitait ce cas comme une erreur fatale
alors que la post-condition voulue (session ciblant bien `account_id`)
était déjà remplie — le process plantait au lieu de simplement continuer.
**Ce bug était latent depuis le correctif du 20/08/2026 et aurait pu
frapper H1 ou H3 à n'importe quel redémarrage** où leur compte cible
serait devenu le compte "préféré" du moment — juste jamais arrivé avant
aujourd'hui par pur hasard de timing.

**Corrigé** : `switch_account()` vérifie désormais via `GET /accounts`
(lecture fraîche, jamais un texte d'erreur à interpréter) si
`account_id` est déjà le compte actif avant de tenter le `PUT /session`
— si oui, retourne directement sans appel superflu ni erreur.

**Tests** : `test_switch_account_skips_put_when_already_active`,
`test_switch_account_puts_when_target_not_yet_preferred`,
`test_switch_account_puts_when_accounts_list_empty` (défense en
profondeur : jamais présumer "déjà actif" sur une liste de comptes
vide/inexploitable). Les 4 tests `switch_account` existants adaptés
pour configurer explicitement `GET /accounts` plutôt que de dépendre
implicitement du comportement par défaut d'un `MagicMock` non configuré
(fragile, corrigé en même temps). 565 tests au total, 100% toujours
vérifié sur les modules critiques, `capital_client.py` à 100% également.

**`hypothesis2_executor` relancé après correctif** — voir résultat daté
ci-dessous.

`hypothesis2_executor` ajouté à `process_watchdog.py` (retiré de la
liste d'exclusion documentée le 21/08/2026 : "à ajouter le jour où il
tourne réellement" — c'est ce jour).

---

## 2026-08-21 — Deux correctifs de précision sur le stop garanti (marge de sécurité + plafond de trailing)

Trouvés en tentant de soumettre un ordre GOLD réel pour vérifier le
correctif de péremption (3 tentatives, 2 erreurs broker distinctes) et
en investiguant EURUSD-24 (67 échecs de trailing).

### Correctif 1 — marge de sécurité sur `_compute_guaranteed_stop_adjustment`

**Cause confirmée en conditions réelles** : `minGuaranteedStopDistance`
est réévalué par le broker EN DIRECT au moment où l'ordre est
effectivement traité — pas seulement lu une fois par notre appel à
`get_market_snapshot()`. Constaté le jour même : GOLD passé de 1% à 2%
entre deux lectures successives (vraisemblablement lié à la volatilité).
Un stop calculé exactement à la limite lue par nous est donc rejeté
(`error.invalid.stoploss.minvalue`), de quelques dixièmes de point sous
le seuil réel au moment de la soumission — **pas un artefact de
précision flottante binaire** (déjà traité ailleurs, voir
`market_data._mid_of`, `round(...,8)`, 20/08/2026) mais une dérive
réelle du seuil lui-même entre lecture et exécution.

**Corrigé** : `GUARANTEED_STOP_SAFETY_MARGIN = 1.01` (+1% de marge
relative) appliqué à la distance minimale calculée, `round(...,8)`
appliqué en plus pour le bruit binaire. La fonction est aussi
généralisée : `entry_price` renommé `reference_price` — le prix contre
lequel la distance minimale est mesurée n'est plus toujours le prix
d'entrée (voir correctif 2 ci-dessous, qui l'utilise avec le prix
courant). Le sizing reste correct par construction (invariant #2) :
`risk_engine.evaluate_new_entry()` est toujours rappelé sur le
`stop_price` réellement retourné par cette fonction, jamais sur une
estimation séparée.

### Correctif 2 — plafond de trailing au minimum garanti broker

**Cause confirmée en conditions réelles** (EURUSD-24, 67 occurrences
de `error.invalid.stoploss.minvalue`) : notre canal Donchian(20) ne
connaît jamais la contrainte `minGuaranteedStopDistance` du broker après
l'ouverture — seule `_compute_guaranteed_stop_adjustment`, appelée à
l'ouverture (`open_signal`), la consultait. Une fois le prix suffisamment
favorable, le canal propose un stop plus serré que ce minimum ; la mise
à jour échoue en boucle, indéfiniment, sans jamais dégrader
gracieusement — **vérifié que la position reste protégée** dans
l'intervalle (le broker refuse la mise à jour, pas la position : le
dernier stop accepté reste actif), mais le trailing n'avance plus.

**Corrigé** : `_apply_management_action` plafonne désormais tout
candidat de trailing (`UPDATE_TRAILING_STOP`, uniquement si
`state.guaranteed_stop`) via `_compute_guaranteed_stop_adjustment`,
avec le prix de marché COURANT comme `reference_price` (pas le prix
d'entrée d'origine — c'est contre le marché courant que le broker
applique cette contrainte à un trade déjà ouvert). `risk_engine` et
`current_price` (le `snapshot.mid` déjà récupéré par
`manage_open_trades`, aucun appel réseau supplémentaire) sont désormais
transmis à `_apply_management_action`. Si le plafond calculé
n'améliore plus le stop déjà en place (le candidat plafonné serait un
élargissement par rapport à l'existant), **aucune mise à jour n'est
tentée** — revalidé via `risk_engine.evaluate_stop_update`
(invariant #5), jamais le plafond appliqué aveuglément.

**Bug latent trouvé en modifiant la signature** : les deux appels
existants à `_apply_management_action` (dans `manage_open_trades` et
`force_close_all_open_trades`) passaient `anthropic_client`/
`bot_token`/`chat_id` en positionnel — l'ajout des deux nouveaux
paramètres avant eux les aurait silencieusement décalés sur les mauvais
arguments. Corrigé en passant tous les arguments par mot-clé aux deux
appels (jamais repéré en production : aurait cassé les notifications de
clôture sans lever d'exception, trouvé en écrivant ce correctif, pas en
production).

**Tests** : `test_guaranteed_stop_safety_margin_widens_at_exact_raw_
boundary`, `test_guaranteed_stop_safety_margin_applied_to_absolute_
unit_too` (correctif 1) ; `test_manage_open_trades_trailing_capped_at_
broker_minimum`, `test_manage_open_trades_trailing_skipped_when_
capped_value_not_tighter` (correctif 2, y compris le contre-test :
jamais un élargissement). Tests existants affectés par la marge de
sécurité mis à jour avec les nouvelles valeurs exactes (5,0 → 5,05 sur
GOLD, 250 → 252,5 sur BTCUSD). 562 tests au total, 100% toujours vérifié
sur les modules critiques.

**Vérification en conditions réelles** (21/08/2026, après déploiement) :

- **EURUSD-24 (correctif 2)** : dès le premier cycle après redémarrage,
  le trailing a réellement avancé — log observé : `Trailing plafonné au
  minimum garanti broker pour le trade 24 : 1.170985 -> 1.17090409
  (candidat brut 1.169965)`. Le stop garanti a bien été mis à jour côté
  broker (plus de blocage indéfini). **Un résidu attendu, pas un échec
  du correctif** : le cycle suivant a de nouveau été rejeté
  (`error.invalid.stoploss.minvalue: 1.17091`, un écart de ~0,000006 —
  la contrainte a continué de dériver entre-temps, exactement le
  phénomène diagnostiqué). Sans gravité : contrairement à l'ancien
  comportement, ce n'est plus un blocage permanent — un seul cycle sur
  plusieurs échoue désormais au lieu de 67/67, et le dernier stop
  accepté reste actif entre deux tentatives (position toujours
  protégée). La marge de 1% réduit la fréquence des rejets sans les
  éliminer totalement, cohérent avec une contrainte qui continue de
  bouger en direct.
- **GOLD, ordre réel (correctifs 1+2 combinés)** : un 4e signal de test
  a été soumis après déploiement — **accepté de bout en bout par le
  broker** (trade id=30, deal_id réel `00000000-5d7b-b311-...`, statut
  `en_attente`, stop garanti élargi à 4722,679234 avec la marge
  appliquée). Confirme la résolution complète des deux erreurs
  rencontrées lors des tentatives précédentes (`error.validation.
  limit.price`, `error.invalid.stoploss.minvalue`). **Annulé
  immédiatement après confirmation** (broker + base) pour ne pas
  laisser un ordre de test synthétique se transformer en position
  réelle dans les statistiques Station X — vérifié après coup : 0 ordre
  en attente, 1 seule position GOLD réelle (le trade H1 déjà suivi),
  aucun état résiduel.

---

## 2026-08-21 — Incident critique, réponse — Étape 5 : redémarrage propre, vérification finale, clôture de l'incident

`executor_loop` redémarré une seconde fois (pour charger le correctif
des trades fantômes, après le premier redémarrage pour l'étape 2) —
propre, aucune erreur au démarrage, zéro référence à un deal_id H3 dans
son log depuis (vérifié par grep sur tout le log post-redémarrage, pas
juste visuellement). `hypothesis3_executor` redémarré (nouvelle session
tmux, l'ancienne étant morte à l'arrêt du 20/08). **Vérification finale
demandée par Ismaël, faite programmatiquement, pas visuellement** :
`GET /positions` (compte "hypothèse 3") comparé aux trades
`statut='ouvert'` en base — **correspondance exacte (7/7), aucun
écart**, avant ET après le redémarrage complet des deux process.

**Nouveau point relevé en redémarrant `hypothesis3_executor`, PAS
investigué** (hors périmètre de cet incident, à traiter séparément) :
`update_position_stop` échoue à répétition sur le trade EURUSD-24
(`error.invalid.stoploss.minvalue`) — 67 occurrences déjà dans le log
avant même ce redémarrage, donc préexistant, pas causé par les
correctifs du jour. Le trailing semble bloqué sur ce trade précis
depuis un moment. À investiguer séparément, signalé mais pas creusé
dans l'urgence de cet incident.

**Correctif péremption (tâche interrompue par l'incident) : re-vérifié
après coup**, même méthode qu'avant l'incident (signal structuré comme
les rejets réels, prix GOLD en direct, à travers `open_signal`) —
`decide_entry` approuve toujours (stop élargi 3 → 46,3 points, 0,25
unité, 9,90€ de risque). **Toujours aucun ordre réel soumis** : la
réserve exprimée avant l'incident (éviter de mélanger un trade de
vérification synthétique aux statistiques réelles de Station X) reste
valable indépendamment de l'incident désormais résolu — décision
explicite d'Ismaël nécessaire pour aller plus loin, pas prise
unilatéralement ici. : réconciliation + vraie cause racine trouvée (deux causes distinctes, aucune des deux n'était l'hypothèse initiale)

**Hypothèse initiale (workingOrderId collision dans `check_pending_fills`) FORMELLEMENT ÉCARTÉE** : vérifiée puis infirmée avec les données réelles du broker (`GET /positions` détaillé) — les 4 positions ETHUSD orphelines ont chacune un `workingOrderId` DIFFÉRENT (`...c669`, `...c9e4`, `...ceab`, `...bdd0`). Le dict de `check_pending_fills` ne pouvait donc pas les avoir collisionnées. Ne pas réutiliser cette explication ailleurs.

### Cause réelle n°1 (les 5 trades fantômes) — CONFIRMÉE via `GET /history/activity` + `/history/transactions`

Les 5 trades marqués "ouvert" en base (BTCUSD-14, USDJPY-15, ETHUSD-16, GBPUSD-19, US100-23) ont TOUS un événement `"source": "SL"` dans l'historique du compte — un **stop garanti authentique, exécuté côté broker**, entre 11h34 et 13h46 UTC ce jour-là. Le mécanisme : un stop garanti Capital.com s'exécute INSTANTANÉMENT dès que le prix le touche, sans attendre notre prochain cycle de polling. Quand notre boucle (`manage_open_trades`) détecte ensuite le même stop touché de son côté et appelle `client.close_position()`, le broker répond 404 "position introuvable" (déjà fermée) — cette exception, non gérée spécifiquement, remontait telle quelle jusqu'au `except Exception` générique de `manage_open_trades`, qui journalise et passe au trade suivant **sans jamais mettre à jour `trades.statut`**. Le trade reste fantôme indéfiniment, retenté à chaque cycle (source du bruit 404 constaté dans les deux process). Rien à voir avec le bug d'exclusion de sources (étape 2) — un bug distinct, préexistant, simplement rendu plus visible/fréquent par le doublon de process.

**Corrigé** : `_apply_management_action` capture désormais `CapitalApiError` autour de `client.close_position()`. Sur échec, vérifie la réalité via un appel FRAIS à `get_open_positions()` (jamais une lecture du message d'erreur, dont le format pourrait changer) : si la position n'existe VRAIMENT plus, la clôture est quand même journalisée en base (au meilleur prix connu, `action.exit_price`) au lieu de laisser un fantôme — fail-safe réinterprété : ici, "ne rien faire" est le vrai danger, pas l'action. Si la position existe toujours, l'exception est relevée telle quelle (comportement inchangé, aucune régression sur les vraies erreurs).

### Cause probable n°2 (les 4 positions ETHUSD orphelines) — bien étayée, PAS certaine à 100%

Confirmé via `/history/activity` : 4 ordres RÉELLEMENT placés et exécutés côté broker entre 07:17 et 07:21 UTC (`"source": "USER", "type": "POSITION", "status": "ACCEPTED"`), correspondant chacun à un signal distinct en base (id 83, 84, 85, 86 — 4 signaux ETHUSD approuvés en l'espace de 4 minutes, stop identique 2336,485, entrées légèrement différentes). **Aucun des 4 n'a de ligne `trades`** alors que le placement a réussi côté broker — signifie que `client.place_limit_order()` a réussi (le broker confirme), mais l'`INSERT INTO trades` qui suit immédiatement dans `open_signal()` n'a jamais abouti. Hypothèse la plus plausible, non confirmée avec certitude (aucune trace disponible a posteriori pour trancher définitivement) : une exception silencieuse entre les deux (ex. contention SQLite si une exécution concurrente du process a eu lieu) a interrompu `open_signal()` après l'appel broker mais avant l'écriture — `_has_active_signal_or_trade` ne voyait alors plus aucune ligne bloquante et laissait le prochain cycle regénérer un signal sur la même rupture de canal. **Pas corrigé aujourd'hui** — nécessiterait d'instrumenter `open_signal()` pour capturer ce cas précis (ex. réconciliation automatique après un succès de `place_limit_order()` suivi d'un échec d'écriture), proposé mais pas implémenté, à valider avec Ismaël séparément.

### Réconciliation effectuée (compte "hypothèse 3")

Sauvegarde DB prise avant toute écriture
(`data/assistant_trading.db.backup-before-reconcile-21-08`).

**5 trades fantômes fermés** avec les vraies données du broker (stop
garanti, prix de sortie = `stop_loss_courant` déjà suivi par notre
système — les stops garantis s'exécutent exactement à leur niveau
configuré, sans slippage) :

| Trade | R | PnL estimé (système, R×risque_eur) | PnL réel broker (TRADE+frais GSL) | Écart |
|---|---|---|---|---|
| USDJPY-15 | −0,496 | −4,95€ | −5,32€ | +0,37€ |
| BTCUSD-14 | +0,476 | +4,75€ | +3,47€ | +1,28€ |
| US100-23 | −0,664 | −6,62€ | −7,01€ | +0,39€ |
| GBPUSD-19 | −0,955 | −9,45€ | −9,98€ | +0,53€ |
| ETHUSD-16 | −0,674 | −6,73€ | −7,64€ | +0,91€ |

Écart systématiquement positif et modeste (< 1,3€) : cohérent avec les
frais de stop garanti ("GSL fee") que notre modèle R×risque_eur
n'intègre pas — écart attendu, pas une anomalie de calibration à
creuser dans l'urgence (le modèle R×risque_eur est la même
méthodologie que pour TOUT autre trade du système, gardée pour rester
cohérente avec les statistiques existantes plutôt que d'introduire une
seconde méthode de calcul juste pour ces 5 trades).

**4 trades ETHUSD créés** pour les positions réelles orphelines,
rattachés aux signaux d'origine identifiés par corrélation des horodatages
(`WORKING_ORDER CREATED` de l'historique broker ↔ `signals.created_at`) :
trades id 26-29, ~10€ de risque chacun (cohérent avec 2% de
l'enveloppe). **Toutes les 4 avaient déjà un stop garanti broker actif**
(`guaranteedStop: true`, `stopLevel: 2336.485`) — contrairement à la
crainte initiale, ces positions n'étaient jamais totalement dépourvues
de protection ; seul le SUIVI (trailing, clôture pilotée par le
système) leur manquait. Aucun nouveau stop appliqué : celui déjà en
place chez le broker est repris tel quel comme `stop_loss_initial`/
`stop_loss_courant`.

**Vérification finale** : `GET /positions` (7 réelles) comparé aux
trades `statut='ouvert'` en base pour `source='hypothesis3'` (7) —
**correspondance exacte, aucun écart résiduel** (confirmé
programmatiquement, pas visuellement).

**Tests** : `test_manage_open_trades_reconciles_when_broker_already_
closed_position` (le cas confirmé), `test_manage_open_trades_reraises_
when_position_genuinely_still_open` (contre-test : une vraie erreur
reste une vraie erreur, jamais masquée). 558 tests au total, 100%
toujours vérifié sur les modules critiques.

---

## 2026-08-21 — Incident critique, réponse — Étape 1 : H3 mis en pause

Priorité absolue demandée par Ismaël. `/pause` écarté : il ne bloque que
les NOUVELLES entrées (`is_asset_blocked`, appelé uniquement dans
`open_signal`), pas la gestion des positions déjà ouvertes — n'aurait
donc pas arrêté la course entre `executor_loop` et
`hypothesis3_executor` sur les trades déjà ouverts.

`hypothesis3_executor` arrêté directement (tmux) : le premier `Ctrl-C`
a tué le process mais la session tmux est morte avec (comportement déjà
documenté le 20/08/2026), laissant un orphelin quelques secondes de
plus — confirmé stoppé (`pgrep` vide) avant de poursuivre.
`executor_loop` laissé tourner (Station X non concerné par ce bug une
fois l'étape 2 déployée) : arrêter ce trade continuerait de gérer
(incorrectement) les trades H3 restants pendant la préparation du
correctif — accepté comme fenêtre de risque résiduelle, minimisée en
enchaînant l'étape 2 immédiatement, sans faire autre chose entre les
deux.

## 2026-08-21 — Incident critique, réponse — Étape 2 : `exclude_sources` remplacé par une reconnaissance positive de Station X

**Rejeté d'emblée** : élargir la liste figée
(`exclude_sources=[HYPOTHESIS_SOURCE, HYPOTHESIS3_SOURCE, ...]`) —
règle exactement le même mode d'échec pour la prochaine hypothèse
oubliée.

**Première version tentée, rejetée en cours d'écriture** :
`_is_stationx_source(source) = (_envelope_source_key(source) ==
"stationx")`. Problème trouvé en écrivant le test de non-régression
lui-même (`hypothesis4` doit être exclue) : `_envelope_source_key`
retombe elle-même sur `"stationx"` pour toute source NON reconnue
(comportement voulu à l'origine pour le routage d'enveloppe, où
Station X est la source imprévisible — id de canal Telegram brut).
Une hypothèse future jamais ajoutée à `_KNOWN_HYPOTHESIS_SOURCES`
aurait donc été INCLUSE dans Station X par cette version — exactement
le bug qu'on corrige, sous une autre forme.

**Corrigé** : `_is_stationx_source(source, telegram_channel)` compare
`source` à la valeur EXACTE que `telegram_listener.run_listener` écrit
dans `signals.source`/`trades.source` pour Station X
(`config.telegram_channel`, vérifié dans le code : `process_message(...,
channel=config.telegram_channel, ...)`), plus la valeur conventionnelle
littérale `"stationx"` (utilisée par les tests, jamais écrite en
production). Reconnaissance positive et explicite — toute source qui
n'est ni l'une ni l'autre est exclue par défaut, y compris une
hypothèse jamais enregistrée nulle part (fail-safe, invariant #7).

`run_executor_loop` construit une fermeture `_stationx_filter(source)`
liée à `config.telegram_channel` une seule fois au démarrage, réutilisée
pour les 4 points où une fuite existait :
- `force_close_all_open_trades` (/stop_urgence) : `exclude_sources=
  [HYPOTHESIS_SOURCE]` → `source_filter=_stationx_filter`
- `manage_open_trades` : idem
- La requête des signaux en attente : filtrait `source != HYPOTHESIS_
  SOURCE` en SQL → filtre Python `_stationx_filter` après une requête
  non filtrée (le filtre positif ne peut pas s'exprimer proprement en
  SQL, dépend d'une valeur de config)
- `check_pending_fills` : **appelée sans AUCUN filtre** (`sources=None`)
  — bug distinct, trouvé en corrigeant celui-ci : `run_executor_loop`
  détectait aussi les remplissages d'ordres d'autres sources. Corrigé
  par le nouveau paramètre `source_filter` (Python, complémentaire au
  paramètre `sources` existant qui reste une liste SQL positive).

`manage_open_trades`/`force_close_all_open_trades`/`check_pending_
fills` gagnent toutes trois un paramètre `source_filter: Optional[
Callable[[str], bool]]`, appliqué après la requête SQL, combinable avec
`include_sources`/`exclude_sources` existants (non retirés — toujours
utilisés tels quels par `hypothesis3_executor`/`hypothesis2_executor`
via `include_sources=[source]`, une inclusion positive et précise, déjà
correcte).

**Tests** : `test_is_stationx_source_*` (4 tests, y compris le cas
`hypothesis4`/canal inconnu qui a fait échouer la première version),
`test_manage_open_trades_source_filter_only_stationx_ignores_
hypothesis3`, `test_force_close_all_open_trades_source_filter_only_
stationx`, `test_check_pending_fills_source_filter_only_stationx`. 556
tests au total, 100% toujours vérifié sur les modules critiques.

---

## 2026-08-21 — INCIDENT CRITIQUE trouvé en redémarrant executor_loop : gestion croisée H1/H3/H2, trades fantômes, positions orphelines non suivies — RIEN CORRIGÉ, rapport factuel uniquement

Trouvé par accident en redémarrant `executor_loop` pour déployer le
correctif de péremption ci-dessous — sans lien direct avec ce correctif,
mais découvert par sa mise en œuvre. Pas encore rapporté à Ismaël au
moment de l'écriture de cette entrée ; aucune correction appliquée.

### 1. `executor.run_executor_loop` ne s'exclut pas correctement des sources H3/H2 — CONFIRMÉ dans le code

`run_executor_loop` (Station X) filtre ses signaux/trades avec
`exclude_sources=[HYPOTHESIS_SOURCE]` (`manage_open_trades` ligne 1218,
`force_close_all_open_trades` ligne 1187) et
`"SELECT * FROM signals WHERE ... source != ?"` avec `HYPOTHESIS_SOURCE`
(ligne 1203) — mais `HYPOTHESIS_SOURCE` vaut littéralement `"hypothesis"`
(l'Hypothèse #1 seule). **Ni `"hypothesis3"` ni `"hypothesis2"` ne sont
exclus.** Conséquence : `executor_loop` gère AUSSI, en double avec
`hypothesis3_executor`, tous les trades et signaux de l'Hypothèse #3
(et gérerait ceux de l'Hypothèse #2 si son process tournait), avec
l'enveloppe et la liste blanche de STATION X plutôt que celles de
l'hypothèse concernée. Ce bug existe depuis le déploiement de H3 ce
matin — n'affectait rien avant (H1 seule existait, et H1 est
correctement exclue).

### 2. Preuve en direct : deux process se disputent la même position, en boucle infinie

Confirmé dans les logs (`logs/executor_loop.log` ET
`logs/hypothesis3_executor.log`) : le trade H3 id=15 (USDJPY, deal_id
`00000000-5d58-1b1b-...`) génère un `CapitalApiError 404
error.not-found.dealId` à **chaque cycle des deux process**, sans
interruption, depuis son stop (le broker ne connaît plus ce dealId —
position déjà fermée par ailleurs), parce que `trades.statut` est resté
`'ouvert'` en base. Chaque process retente indéfiniment sans jamais
corriger cet état — aucune alerte, aucun arrêt, juste un bruit d'erreur
continu et des appels API gaspillés par deux process au lieu d'un.

### 3. Quatre trades DB "ouvert" dont la position n'existe plus chez le broker (fantômes)

Vérifié en direct via `GET /positions` sur le compte "hypothèse 3"
(7 positions réelles ouvertes) comparé aux 8 trades marqués `'ouvert'`
en base pour `source='hypothesis3'` :

| Trade DB | Actif | Statut DB | Position broker réelle ? |
|---|---|---|---|
| 13 | GOLD | ouvert | ✅ correspond |
| 14 | BTCUSD | ouvert | ❌ absente (fantôme) |
| 15 | USDJPY | ouvert | ❌ absente (fantôme, voir point 2) |
| 16 | ETHUSD | ouvert | ❌ deal_id ne correspond à AUCUNE des 4 positions ETHUSD réelles (voir point 4) |
| 19 | GBPUSD | ouvert | ❌ absente (fantôme) |
| 23 | US100 | ouvert | ❌ absente (fantôme) |
| 24 | EURUSD | ouvert | ✅ correspond |
| 25 | US30 | ouvert | ✅ correspond |

**Cause probable, pas encore confirmée avec certitude** : le point 1
(gestion croisée) crée un scénario où `executor_loop` ET
`hypothesis3_executor` peuvent chacun tenter de fermer la même position
au même stop touché — l'un réussit, journalise la clôture ; l'autre
arrive après coup, échoue (404, position déjà fermée par le premier),
et son échec passe par le `except Exception` générique de
`manage_open_trades` sans jamais corriger `trades.statut`. Cohérent
avec le fait que ces 4 trades fantômes ont tous un `stop_loss_courant`
proche de niveaux de marché plausibles pour un stop touché — mais pas
vérifié trade par trade avec certitude.

### 4. PLUS GRAVE : quatre positions ETHUSD réelles ouvertes sur le compte, dont trois ne sont tracées par AUCUNE ligne de la base

`GET /positions` sur le compte "hypothèse 3" montre **4 positions
ETHUSD distinctes**, deal_ids `00000000-5d2e-c7dd...`, `...ca43...`,
`...d023...`, `...d1a3...`, tailles 0,226/0,228/0,229/0,256. La base ne
contient qu'**une seule** ligne `trades` pour ETHUSD/hypothesis3 de
toute son histoire (id=16, deal_id `00000000-5d58-1f3f...`, taille
0,19) — un deal_id et une taille qui ne correspondent à AUCUNE des 4
positions réelles. **Trois positions réelles, avec du capital démo
engagé dessus, existent sur le broker sans qu'aucune ligne de la base
n'en ait jamais connaissance** — donc jamais gérées, jamais de stop
suivi, jamais de clôture possible par le système.

**Hypothèse de cause, non confirmée** : `check_pending_fills` construit
`positions_by_working_order_id = {p["position"]["workingOrderId"]: p
for p in positions}` (executor.py ligne 703) — un dict Python, qui ne
garde qu'UNE position par `workingOrderId` si plusieurs positions
partagent la même clé. Si l'ordre limite placé par `open_signal` pour
ce signal ETHUSD a été rempli en plusieurs fois par le broker (fills
partiels créant plusieurs positions distinctes, toutes rattachées au
même `workingOrderId` d'origine), ce dict n'en retiendrait qu'une —
les autres fills resteraient invisibles à `check_pending_fills`, jamais
rattachés à aucune ligne `trades`. Les tailles proches mais différentes
des 4 positions réelles (0,226 à 0,256) sont cohérentes avec des fills
successifs d'un même ordre plutôt qu'avec 4 signaux indépendants
(auquel cas `_has_active_signal_or_trade` aurait dû bloquer les
suivants). **Non vérifié avec certitude — hypothèse la plus probable
identifiée, pas confirmée.**

### Rien corrigé

Aucune ligne de code touchée pour ces quatre points — uniquement
constaté et journalisé ici, en attendant qu'Ismaël soit informé et
tranche la suite (les deux process continuent de tourner en l'état, y
compris la boucle d'erreur 404 du point 2, jusqu'à nouvel ordre).

---

## 2026-08-21 — Péremption calculée sur le stop d'origine au lieu du stop élargi — corrigé

Trouvé en investiguant, à la demande d'Ismaël, pourquoi les 10 signaux
GOLD reçus depuis le déblocage du stop garanti (20/08/2026) avaient tous
été rejetés avant même d'atteindre cette logique.

**Cause confirmée dans le code** : `open_signal()` appelait `decide_entry()`
(donc `validator.validate_signal()`, donc le calcul de tolérance de
péremption `stop_distance * STALENESS_FRACTION_OF_STOP_DISTANCE`) avec
`signal_row["stop_loss"]` — le stop BRUT du signal Station X (2-3 points
sur GOLD) — alors que `_compute_guaranteed_stop_adjustment()`, qui
détermine le stop RÉELLEMENT utilisé pour la décision (~1% du prix sur
GOLD, ~45 points), n'était appelée qu'après, une fois `decide_entry`
déjà approuvé.

**Vérifié numériquement sur les 5 rejets réels** (les 5 autres avaient
échoué dès l'extraction, sans rapport avec ce point) : la tolérance
utilisée (50% de 2-3 points ≈ 1-1,5 point) était 15 à 20 fois plus
serrée que la tolérance qu'aurait donnée le stop réellement utilisé
(50% de ~45 points ≈ 22-23 points). Les 5 écarts observés (2,05 à 7,15
points) sont tous largement sous ce seuil élargi — les 5 auraient été
approuvés côté péremption avec le stop réel.

**Corrigé** : `_compute_guaranteed_stop_adjustment()` est désormais
appelée AVANT `decide_entry()` dans `open_signal()` (au lieu d'après),
et son résultat (`adjustment.stop_price`, le stop effectif) est transmis
à `decide_entry()` à la place du stop brut du signal — péremption ET
sizing sont donc calculés sur le stop réellement utilisé, dès le
premier passage. Conséquence directe : la logique de "second passage"
(reconstruction d'un `TradeSignal` élargi, second appel à
`risk_engine.evaluate_new_entry`, second `risk_decisions`) devient
inutile et est supprimée — le sizing est correct dès le premier appel à
`decide_entry`, un seul `risk_decisions` par signal désormais (au lieu
de deux dans le cas élargi).

**Comportement inchangé pour les signaux qui n'ont jamais besoin
d'élargissement** (majorité des cas, y compris tous les actifs sans
exigence de stop garanti du broker) : `_compute_guaranteed_stop_
adjustment` retourne alors `stop_price` identique au stop d'origine —
`decide_entry` reçoit exactement la même valeur qu'avant ce correctif.
Épinglé par un test dédié (`test_open_signal_no_widening_needed_sizing_
unchanged_from_original_stop`) qui vérifie taille, risque en euros, et
paramètres d'ordre exacts.

**Coût accepté** : `_compute_guaranteed_stop_adjustment` (un appel
`get_market_snapshot`) est désormais appelée pour TOUT signal atteignant
ce point, même ceux qui seront ensuite rejetés par `decide_entry` pour
d'autres raisons (liste blanche, confiance, etc.) — auparavant, cet
appel n'avait lieu qu'après approbation. Coût réseau marginal, du même
ordre que `get_price_snapshot` déjà appelé inconditionnellement.

**Tests** : `tests/test_executor.py` — le test couvrant l'ancien
"second passage" (`test_open_signal_rejected_when_resize_after_
widening_below_minimum_size`) adapté au nouveau comportement en un seul
passage (`test_open_signal_rejected_when_widened_stop_gives_size_
below_minimum`) ; nouveau test de non-régression explicite sur le cas
sans élargissement. 64 tests sur `test_executor.py`, 550 au total,
aucune régression, 100% toujours vérifié sur les modules critiques.

**Vérification en conditions réelles** (21/08/2026, après déploiement) :
rejeu d'un signal structuré exactement comme les 5 signaux réellement
rejetés (short, stop à 3 points), avec entrée = prix GOLD en direct
(drift de péremption nul par construction, isole la question du stop
élargi) : `_compute_guaranteed_stop_adjustment` élargit correctement
3 → 46,1 points, `decide_entry` **approuve** (validator + risk_engine,
0,25 unité, 9,87€ de risque ≈ 1,97% de l'enveloppe) — les paramètres
exacts de l'ordre limite qui aurait été soumis ont été calculés et
affichés (`place_limit_order(epic=GOLD, direction=SELL, size=0.25,
level=4610.85, guaranteed_stop=True, stop_distance=46.1)`). **Aucun
ordre réel soumis** délibérément — lecture seule au-delà de ce point,
pour ne pas mélanger un trade de vérification synthétique aux
statistiques réelles de Station X (voir aussi l'incident critique
découvert juste après, entrée séparée ci-dessous, qui rendait cette
prudence d'autant plus justifiée).

---

## 2026-08-21 — Construction des Hypothèses #3 et #2 (feu vert explicite d'Ismaël)

Feu vert reçu : « toutes les questions préalables étant résolues ». Ce
qui suit couvre les deux hypothèses, dans l'ordre construit (fondations
communes d'abord, puis H3, puis H2).

### Bug bloquant trouvé AVANT tout code d'hypothèse : normalisation de source binaire

En préparant H3, découverte d'un bug réel touchant potentiellement les
statistiques : `_normalize_source`/`_envelope_source_key`, dupliquée dans
`executor.py`, `metrics.py`, `circuit_breaker_store.py`,
`confidence_scorer.py`, ne reconnaissait QUE la source littérale
`"hypothesis"` (Hypothèse #1) — toute autre valeur (y compris une
nouvelle hypothèse légitime comme `"hypothesis3"`) retombait
silencieusement sur `"stationx"`. Sans correction, les trades de H3/H2
auraient été comptés dans les statistiques de Station X (enveloppes,
coupe-circuits R, métriques, score de confiance), une violation directe
du principe "métriques calculées séparément par source" (§2.11) —
jamais observé en production (aucune hypothèse au-delà de H1 n'existait
avant aujourd'hui), corrigé avant tout déploiement.

**Corrigé** : les 4 copies généralisées à un ENSEMBLE de sources
hypothèse connues (`{"hypothesis", "hypothesis3", "hypothesis2"}`) —
toute source hors de cet ensemble retombe sur `"stationx"` (comportement
inchangé pour Station X, dont `source` reste l'id de canal Telegram
brut). **Risque de divergence assumé** : ces 4 copies doivent désormais
rester synchronisées manuellement à chaque nouvelle hypothèse — garde-fou
ajouté (`tests/test_source_normalization_consistency.py`) qui compare
les 4 fonctions sur un jeu de sources fixe et échoue si l'une diverge
des trois autres, plutôt que de centraliser dans un module partagé
(aurait rompu la convention du projet "dupliqué plutôt qu'importé, nom
privé" pour un gain marginal — le test de cohérence couvre le risque
réel sans ce coût).

**Second bug trouvé dans la foulée** : `executor.manage_open_trades`
récupérait TOUJOURS des bougies horaires (`resolution="HOUR"` codé en
dur) pour recalculer le canal de Donchian du trailing (§2.11), quelle
que soit la source du trade. Sans correction, le trailing de
l'Hypothèse #3 (bougies M15) aurait utilisé un canal calculé sur des
bougies horaires — silencieusement incohérent avec sa propre logique
d'entrée. Corrigé par `executor._TREND_CANDLE_RESOLUTION`, un dict
{source: résolution} consulté au moment du calcul (Station X non
concernée : `state.tp1 is None` ne s'applique jamais à elle, donc le
repli par défaut "HOUR" reste inchangé pour elle).

### Refactor : `technical_strategy_executor.py`, moteur générique

`trend_executor.py` codait en dur la boucle complète de l'Hypothèse #1
(~150 lignes : gestion des ordres, coupe-circuits, /stop_urgence,
enveloppes). Construire H3 et H2 en copiant ce fichier deux fois de plus
aurait triplé la maintenance du moindre correctif de boucle (ex: le bug
ATR trouvé plus tôt, ou celui ci-dessus). Extrait en
`src/technical_strategy_executor.py` (`run_technical_strategy_loop`,
paramétrée par source/actifs/résolution/fonction de détection/
identifiants) ; `trend_executor.py` ne contient plus que les paramètres
propres à l'Hypothèse #1. **Comportement de l'Hypothèse #1 vérifié
strictement inchangé** : les 9 tests existants de
`tests/test_trend_executor.py` passent sans modification après le
refactor (même texte d'audit, même câblage, testé par régression avant
tout déploiement).

### Hypothèse #3 — déployée

- `src/hypothesis3_executor.py` : source `"hypothesis3"`, résolution
  `MINUTE_15`, `trend_strategy.evaluate_entry`/
  `compute_trailing_stop_channel` réutilisés tels quels (identiques à
  H1, seule la résolution change — conforme à la proposition validée du
  20/08/2026), 8 actifs.
- **`CAPITAL_ACCOUNT_ID_HYPOTHESIS3` retrouvé** : absent de `.env` malgré
  des identifiants API H3 déjà en place (`CAPITAL_API_KEY_HYPOTHESIS3`
  et consorts, préparés le 20/08/2026) — sans lui, `run_hypothesis3_loop`
  aurait échoué net dès le démarrage (`ConfigError`, comportement voulu,
  pas un bug). Retrouvé par `GET /accounts` avec la clé H3 (lecture
  seule) : `327950877951612062` — confirmé identique à la valeur déjà
  utilisée comme donnée de test réaliste dans `tests/test_config.py`
  depuis une session précédente (jamais reportée dans `.env` à l'époque).
  Ajouté à `.env` (local + VPS) avec `CAPITAL_ACCOUNT_ID_HYPOTHESIS2`
  (`327950654613312670`, découvert au passage, voir section H2
  ci-dessous — valeur non secrète, sans risque à stocker avant que les
  identifiants H2 eux-mêmes existent).
- **Observation en clair au passage** : le compte marqué "préféré" pour
  la clé H3 est actuellement "hypothèse 2", pas "premier test" — confirme
  que l'instabilité du flag "préféré" documentée le 20/08/2026 reste
  d'actualité (aucune régression : les deux boucles de production
  ciblent déjà explicitement leur compte par `accountId`, jamais le
  compte préféré).
- Tests : `tests/test_hypothesis3_executor.py` (paramètres transmis
  correctement), `tests/test_config.py` étendu. Déployé et vérifié en
  conditions réelles — voir entrée séparée ci-dessous pour le résultat
  du test encadré.

### Hypothèse #2 — Option B, code construit, PAS déployée (identifiants manquants)

`src/ict_strategy.py` (module critique, 100% couvert) : régime MA(200) +
trailing Donchian(20) réutilisés tels quels de `trend_strategy.py`,
détection ICT propre à cette hypothèse (swings fractals K=2, zone de
confluence Fibonacci 61,8%-78,6%, FVG chevauchant la zone) — Option B de
la proposition validée le 21/08/2026 (voir `docs/HYPOTHESES.md`).

**Trois choix de conception concrets, nécessaires pour écrire du code
exécutable, PAS entièrement détaillés dans la proposition d'origine du
20/08/2026** (qui ne détaillait la règle d'entrée complète — point 4 —
que pour l'Option A) — documentés en détail dans la docstring du module
et dans `docs/HYPOTHESES.md`, à vérifier par Ismaël avant toute
observation de résultat (aucune donnée regardée avant ces choix) :
1. Sélection de la jambe d'impulsion (dernier swing bas confirmé, puis
   premier swing haut confirmé plus récent que lui) — un choix parmi
   plusieurs défendables, pas une règle ICT canonique.
2. Fenêtre de recherche des swings/FVG : réutilise `DONCHIAN_PERIOD=20`
   (pas un nouveau paramètre), avec une marge de `2×FRACTAL_K` bougies
   pour la confirmation en bord de fenêtre.
3. `classify_structure_break` (BOS/CHoCH, point 3 de la proposition)
   implémentée et testée à 100%, mais **PAS câblée comme condition
   d'entrée** dans cette première version — l'ajouter aurait été une
   complexité non validée par Ismaël, pas une simple traduction de ce
   qui a été approuvé.

**Identifiants Capital.com dédiés à l'Hypothèse #2 : absents à ce jour.**
Contrairement à H3, aucune clé API/mot de passe distincts n'ont été
générés pour le compte "hypothèse 2" (accountId `327950654613312670`,
retrouvé et vérifié en lecture seule — voir section H3 ci-dessus).
**Fait vérifié empiriquement (lecture seule, aucun ordre)** : la clé API
H3, déjà en place, PEUT techniquement cibler et lire le compte
"hypothèse 2" via `switch_account` (les clés API Capital.com sont
scopées à l'identifiant de connexion, pas à un compte précis — les 3
comptes du même identifiant sont visibles et accessibles depuis
n'importe laquelle de ses clés). **Décision explicitement NON prise ici**
: réutiliser la clé H3 (ou la clé principale) pour faire tourner H2
fonctionnerait techniquement, mais casserait la séparation
d'identifiants déjà établie pour H3 (une clé, un usage) sans
autorisation explicite d'Ismaël — jamais décidé silencieusement pour un
identifiant/mot de passe, même quand la solution technique est triviale
(même principe que le refus systématique de coller des secrets ailleurs
que dans cette session, établi au palier P2). `hypothesis2_executor.py`
est câblé pour exiger `CAPITAL_API_KEY_HYPOTHESIS2`/
`CAPITAL_IDENTIFIER_HYPOTHESIS2`/`CAPITAL_API_PASSWORD_HYPOTHESIS2` —
absents, `run_hypothesis2_loop` échoue net (`ConfigError`) au démarrage,
jamais un repli silencieux. Code déployé sur le VPS, PAS démarré. Pas
ajouté à `process_watchdog.py` (aurait déclenché une fausse alerte
"process manquant" pour un process pas encore censé tourner).

Tests : `tests/test_ict_strategy.py` (34 tests, 100% de couverture),
`tests/test_hypothesis2_executor.py`, `tests/test_config.py` étendu.

### Vérification en conditions réelles (H3, contrôlée)

Déployé sur le VPS (`git pull`, 549 tests verts, 100% sur tous les
modules critiques y compris `ict_strategy`), `.env` complété
(`CAPITAL_ACCOUNT_ID_HYPOTHESIS3`/`CAPITAL_ACCOUNT_ID_HYPOTHESIS2`).

`trend_executor` (H1) redémarré en premier pour valider le refactor en
conditions réelles avant de construire dessus : reconnecté proprement,
log de démarrage conforme (`source=hypothesis, résolution=HOUR, 8
actifs`), enveloppes existantes relues avec leurs soldes réels
(EURUSD 500,35€, GBPUSD 499€, US30 507,14€ — pas remises à 500€ à plat,
confirme qu'aucun état n'a été perdu par le refactor).

`hypothesis3_executor` démarré pour la première fois (nouvelle session
tmux) : connexion réussie avec les identifiants H3, `switch_account`
vers le compte "hypothèse 3" confirmé, log de démarrage conforme
(`source=hypothesis3, résolution=MINUTE_15, 8 actifs`), 8 enveloppes
créées en base avec `source='hypothesis3'` (capital initial 500€
chacune, distinctes des enveloppes `hypothesis`/Station X du même
actif). Process resté vivant et sans erreur sur plusieurs cycles de 60s
consécutifs (aucun signal généré pendant la fenêtre d'observation — pas
anormal, une rupture M15 confirmée reste un événement rare). Laissé en
autonomie sur le VPS (tmux `hypothesis3_executor`), à ajouter au
`process_watchdog.py` (déjà fait ci-dessus).

---

## 2026-08-21 — 4e compte démo "synthèse" proposé par Ismaël = `allocator.py` (§2.5) — intention future, rien construit

Ismaël a évoqué un 4e compte démo destiné à tester une logique
combinant les flux existants (Station X, H1, et H3/H2 une fois
validées). Question posée explicitement : est-ce une idée nouvelle, ou
déjà prévue par l'architecture ?

**Correspondance confirmée, avec une précision technique importante**
(pas un simple "oui ça correspond") : `docs/CDC_v4.md` §2.5 ("Allocation
automatique du capital réel") alloue le capital réel vers les **actifs**
les mieux classés par score de confiance (§2.4), sous plafonds durs
(max 2 actifs en réel en phase B, max 60% du capital réel par actif),
avec retrait automatique si le score repasse sous le seuil éliminatoire.

Ce que ce mécanisme fait concrètement, une fois `confidence_scorer.py`
étendu par **(actif, source)** comme c'est déjà le cas depuis le palier
P2.8 : une **sélection de capital entre les flux les plus performants
sur un même actif** — ex. allouer le réel sur GOLD via l'Hypothèse #1
plutôt que via Station X si son score de confiance est meilleur.

**Ce que ce mécanisme NE fait PAS** : il ne fusionne jamais les
**signaux d'entrée** de plusieurs flux (pas de logique du type "n'ouvrir
un trade que si 2 flux sur 3 sont d'accord simultanément"). Si
l'intention d'Ismaël pour ce 4e compte est une fusion de signaux plutôt
qu'une sélection de capital entre stratégies déjà indépendantes, ce
serait un mécanisme distinct, non prévu littéralement par le CDC — à
clarifier explicitement le jour où cette idée est reprise, pas quelque
chose que je déciderais seul entre les deux lectures.

**Rien construit** : `allocator.py` n'a de sens qu'une fois
`confidence_scorer.py` produit des scores réels sur un volume de trades
significatif (constat d'Ismaël lui-même) — vérifié le 20/08/2026 :
**0 actif/source éligible aujourd'hui**, tous bloqués sur le seuil de
20 trades minimum et/ou l'absence de données de spread (voir entrée du
20/08/2026 ci-dessous). Toute logique de combinaison serait fabriquée
sur du vide. Aucun compte créé, aucun identifiant demandé, aucun code
écrit sur ce point.

Voir aussi `docs/HYPOTHESES.md` (21/08/2026) : ce 4e compte "synthèse"
n'est pas une 4e hypothèse **prédictive** au sens du §3.9 — il ne
consomme donc pas le plafond de 3 hypothèses par cycle (H1 + H3 + H2 y
sont déjà, une fois H3 validée).

---

## 2026-08-20 — `confidence_scorer.py` (§2.4), mode observation uniquement — deux écarts documentés, un gap de données identifié

Demande explicite d'Ismaël : construire le score de confiance du §2.4
maintenant que plusieurs flux tournent en parallèle (Station X, Flux B
H1), en **mode observation uniquement** — aucune décision réelle n'en
dépend (`allocator.py` §2.5 et le verrou §4.9 restent volontairement non
construits, rien à décider avec si peu de trades). Bloc "Classement" du
dashboard (§4.6) câblé, jusque-là affiché vide et étiqueté "non
construit".

**Formule implémentée telle quelle** : conditions éliminatoires
(nb_trades ≥ 20 phase A / ≥ 50 phase B, espérance nette > 0, taille
minimale broker compatible avec l'enveloppe, spread médian < 15% du
stop typique) puis `score = espérance_nette_R × facteur_échantillon
(√(nb_trades/50), plafonné à 1) × facteur_stabilité (1 −
drawdown_max/20%, plancher 0)`. Calculé par (actif, source) séparément,
jamais un agrégat mélangé.

**Écart 1 — unité de `drawdown_max`** : le CDC écrit "drawdown_max/20%"
sans préciser l'unité. Le seul drawdown calculé partout ailleurs dans le
projet (`metrics.AssetMetrics.drawdown_max_r`, `circuit_breaker.py`) est
en multiples de R, pas en % de capital — aucune mesure en % n'existe
(la règle de réinvestissement des 50%, §2.3, rend un "% de l'enveloppe"
glissant, pas trivial à définir sans une nouvelle table de suivi
dédiée). Approximation retenue : drawdown_% ≈ |drawdown_max_r| ×
risk_percent (2% par défaut, paramètre explicite du module, jamais lu
depuis `.env` en direct) — cohérent avec le modèle mental du §2.3 (1R ≈
risk_percent% du capital engagé), documenté comme approximation, pas
comme mesure directe, dans la docstring de `confidence_scorer.py`.

**Écart 2 / gap de données — spread médian** : `market_snapshots.spread`
existe dans le schéma (§4.5) mais n'est alimenté par **aucun code du
projet à ce jour** — vérifié par grep exhaustif, ni `executor.py` ni
`trend_executor.py` n'y écrivent jamais. Conséquence : la condition
"spread médian < 15%" est actuellement **toujours indéterminée**, pour
tous les actifs — `get_median_spread_ratio()` retourne `None` en
l'absence de donnée, ce qui fait échouer la condition (fail-safe,
invariant #7 : donnée manquante bloque l'éligibilité, ne la
court-circuite jamais à `True`). Concrètement : même un actif/source
dépassant 50 trades avec une belle espérance restera "non éligible"
tant que la capture de spread n'est pas câblée quelque part (candidat
naturel : `market_data.get_price_snapshot()`, déjà appelé à l'ouverture
d'un trade dans `executor.py`/`trend_executor.py` — pas fait ici, hors
périmètre de la demande d'aujourd'hui, pas de décision réelle qui en
dépendrait avant longtemps vu le seuil de 20 trades). Gap connu, pas un
oubli silencieux.

**Persistance** : `confidence_scores` (table du §4.5) existe dans le
schéma mais n'est PAS écrite automatiquement à chaque calcul — même
choix que `metrics.py` pour `metrics_snapshot` (aucun consommateur d'un
historique de scores n'existe encore, écrire une ligne à chaque
`/dashboard` serait de la complexité sans lecteur ; réversible dès
qu'`allocator`/`hypothesis_engine` existeront et en auront besoin).

**Taille minimale broker compatible** : ré-application en lecture seule
de la formule de dimensionnement de `risk_engine.evaluate_new_entry`
(`risk_amount_eur / (stop_distance × pip_value_per_unit) ≥ min_units`),
sur l'enveloppe courante et la distance de stop médiane historique de
l'(actif, source) — jamais utilisée pour placer un ordre réel (module de
reporting, l'invariant #3 concerne le flux d'exécution, pas une
statistique de dashboard). `pip_value_per_unit` vient de la liste
blanche statique (`asset_whitelist.ASSET_WHITELIST`, taux EUR figés au
16/08/2026) plutôt que d'un taux rafraîchi en direct — cohérent avec le
principe déjà établi dans `asset_whitelist.py` : acceptable pour une
décision d'inclusion/reporting, jamais pour un dimensionnement réel de
position.

**Tests** : `tests/test_confidence_scorer.py`, 42 tests, 100% de
couverture sur `src/confidence_scorer.py` (calcul pur ET orchestration
I/O — même régime que `risk_engine.py`, demande explicite d'Ismaël).
`tests/test_dashboard.py` étendu (le bloc Classement n'est plus vide).
496 tests passent au total, aucune régression, 100% toujours vérifié sur
`risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`/
`circuit_breaker`.

---

## 2026-08-20 — Incident : le compte "préféré" Capital.com a basculé silencieusement — ciblage explicite par accountId

Trouvé en préparant le test de connexion du compte démo H3 (entrée
suivante ci-dessous, chronologiquement avant celle-ci) : une connexion
fraîche avec la clé API **principale** (Station X/H1) ne voyait soudain
plus aucune position ouverte, alors que US100 et US30 étaient bien
réellement ouverts.

**Cause** : Ismaël avait créé deux nouveaux comptes démo sur la
plateforme Capital.com ("hypothèse 2", "hypothèse 3") en préparation de
futures hypothèses. Le compte marqué **"préféré"** — celui que `POST
/session` active par défaut pour toute nouvelle session, en l'absence de
ciblage explicite — a basculé de "premier test" (le compte historique,
utilisé par Station X et le Flux B H1 depuis le palier P0) vers
"hypothèse 2". Confirmé par test direct : **ce flag "préféré" est
partagé entre TOUTES les clés API d'un même identifiant Capital.com** —
la clé H3, toute nouvelle, voyait exactement le même compte préféré que
la clé principale, jamais utilisée avant aujourd'hui pour ce compte.
`capital_client.py` ne ciblait jusque-là jamais de compte explicitement :
`login()` active implicitement le compte préféré du moment, et `get_
account_balance()` (la seule fonction qui lisait `GET /accounts`) suppose
`preferred: true` sans jamais vérifier lequel.

**Risque encouru** : `executor_loop` et `trend_executor`, alors en
cours d'exécution depuis avant la création des nouveaux comptes, sont
restés correctement accrochés à "premier test" — leur session déjà
établie n'a pas été affectée. Mais **tout redémarrage** (crash, erreur
429 déjà observée plusieurs fois ce jour, reboot VPS, un déploiement
comme ceux de toute cette session) se serait reconnecté sur le nouveau
compte "préféré" — silencieusement, sans erreur, avec perte complète du
suivi des positions réelles (`get_open_positions()` aurait renvoyé une
liste vide, `place_limit_order()` aurait ouvert des positions sur un
compte vide sans rapport avec les enveloppes suivies en base).

**Correctif** : `CapitalClient.switch_account(account_id)` — `PUT
/session` avec un `accountId` explicite, appelé systématiquement juste
après `login()`, avant tout autre appel. Plus aucune dépendance au
flag "préféré" pour les deux boucles de production. `config.py` gagne
`capital_account_id` (compte principal, partagé Station X + Flux B H1 —
mêmes credentials, donc même compte) et `capital_account_id_hypothesis3`
(compte H3, en anticipation — jamais câblé dans une boucle d'exécution
à ce stade, voir entrée suivante). Les deux sont `Optional[str] = None`
dans `AppConfig` (aucun process ne les exige tous), mais `run_executor_
loop`/`run_trend_loop` lèvent une `ConfigError` explicite à LEUR
démarrage si `CAPITAL_ACCOUNT_ID` manque — échec net plutôt qu'un
démarrage qui semblerait réussir sur le mauvais compte (invariant #7).

**Pourquoi anticiper aussi pour H3** : même s'il n'existe qu'un seul
compte "hypothèse 3" aujourd'hui, câbler dès maintenant `capital_
account_id_hypothesis3` (ciblage explicite, jamais "préféré") évite de
reproduire exactement cet incident le jour où un quatrième compte démo
serait créé sur la même plateforme.

**Tests** : `tests/test_capital_client.py` (+5 : ciblage explicite,
capture des nouveaux tokens si renvoyés, conservation des tokens
existants sinon, erreur avant login, erreur HTTP propagée),
`tests/test_config.py` (+2, nouveaux champs). 453 tests passent, 100%
de couverture maintenue.

**Vérification en conditions réelles, confirmée** : `executor_loop` ET
`trend_executor` redémarrés délibérément après déploiement du correctif
(les deux étaient concernés, pas seulement `executor_loop`). Les deux
ont atteint leur log "Démarrage..." (n'apparaît qu'après `login()` +
`switch_account()` + construction de la liste blanche — donc après un
ciblage de compte réussi). Contrôle indépendant supplémentaire : une
connexion fraîche + `switch_account(cfg.capital_account_id)` exécutée
séparément a bien retrouvé les deux positions réelles (US100, US30,
mêmes `dealId` que ceux suivis en base) — la régression est
définitivement corrigée, plus de dépendance au compte "préféré" pour
aucun des deux process de production.

---

## 2026-08-20 — Identifiants du compte démo dédié à l'Hypothèse #3 : préparation, aucun câblage d'exécution

Compte démo Capital.com séparé créé par Ismaël pour l'Hypothèse #3
(M5/M15, proposition `docs/HYPOTHESES.md` non encore validée). Option A
retenue (identifiant/mot de passe API distincts par compte, pas de
bascule de compte partagée via `PUT /session`) — voir l'entrée
précédente (analyse comptes multiples) pour le raisonnement complet.

**Sécurité des identifiants** : jamais collés dans la conversation —
Ismaël les a ajoutés directement dans son `.env` local via son éditeur,
même discipline que le tout premier `.env` du palier P0 ("jamais
transmis à/par un LLM"). Vérifiés présents et non vides via `dotenv_
values()` + longueur uniquement, jamais la valeur elle-même affichée.

**`config.py`** : `capital_api_key_hypothesis3`/`capital_identifier_
hypothesis3`/`capital_api_password_hypothesis3`, tous `Optional[str] =
None` (contrainte dataclass : après tous les champs obligatoires).
`capital_identifier_hypothesis3` se replie sur `CAPITAL_IDENTIFIER`
(compte principal) si `CAPITAL_IDENTIFIER_HYPOTHESIS3` est absent — même
identifiant de connexion possible pour deux comptes démo distincts.
Aucun `_require()` : un `.env` sans ces trois variables continue de
charger normalement, `telegram_listener`/`executor_loop`/
`trend_executor`/`control_bot` ne les lisent jamais.

**Aucun câblage d'exécution à ce stade** — demande explicite d'Ismaël,
la question des identifiants est indépendante de la validation de
l'hypothèse elle-même : aucune boucle, aucun `open_signal`, aucune
enveloppe `hypothesis3` n'existe. Prochaine étape (après confirmation
locale) : transfert vers le VPS et test de connexion en lecture seule
(solde, session) pour confirmer l'isolation du compte — toujours aucun
ordre, aucune exécution.

**Tests** : `tests/test_config.py` (nouveau, +3 : absence par défaut,
présence explicite, repli d'identifiant). 446 tests passent, 100% de
couverture maintenue sur les modules critiques.

---

## 2026-08-20 — Bug trouvé en déployant l'extension à 8 actifs : bruit de précision binaire dans (bid+ask)/2 rejeté par l'API

Trouvé dans les toutes premières secondes après le redémarrage de
`trend_executor` avec les 8 actifs (entrée précédente) : un signal
US100 a été généré et rejeté par l'API Capital.com
(`error.validation.limit.price`).

**Cause** : `market_data._mid_of()`/`get_price_snapshot()` calculent
`(bid + ask) / 2` en float Python brut. Pour certaines paires bid/ask,
ça produit un résultat avec du bruit de précision binaire — reproduit
exactement : `(29148.1 + 29148.3) / 2 == 29148.199999999997`, pas
`29148.2`. US100 impose `minStepDistance = 0.1 point` (`dealingRules`,
vérifié en direct) — l'API rejette un prix qui n'est pas un multiple
exact de ce pas, et `29148.199999999997` n'en est pas un à cause du
bruit. Jamais manifesté avant : aucun actif couvert par le Flux B avant
aujourd'hui (US30/EURUSD/GBPUSD/USDJPY/ETHUSD) n'a une combinaison
magnitude de prix + pas minimum aussi sensible à ce bruit — un artefact
de la précision binaire, pas propre à US100 en soi, juste plus souvent
révélé par lui.

**Correctif** : `round(..., 8)` sur les deux calculs de `(bid+ask)/2`
(`get_price_snapshot` et `_mid_of`, `market_data.py`) — élimine le bruit
binaire (qui apparaît bien au-delà de la 8e décimale, quelle que soit la
magnitude de prix de la liste blanche, de 0,0001 BTC à 100 000 USD) sans
jamais tronquer une précision réellement significative pour un
instrument réel. Corrige à la source : ce prix alimente `Candle.close`
(donc `trend_strategy.evaluate_entry`, `entry_price`/`stop_price` de
tout signal Flux B) ET `PriceSnapshot.mid` (validator, staleness,
guaranteed-stop) — un seul correctif couvre les deux chemins plutôt que
de corriger uniquement le symptôme observé (le prix envoyé à
`place_limit_order`).

**Tests** : `tests/test_market_data.py` (+2, reproduisent exactement le
cas réel — `29148.1`/`29148.3` -> `29148.2`, vérifié par `repr()` pour
exclure tout bruit résiduel). 443 tests passent, 100% de couverture
maintenue.

---

## 2026-08-20 — Flux B étendu aux 8 actifs (coexistence assumée avec Station X) + marquage de session horaire

**Décision explicite d'Ismaël**, suite à l'audit du jour montrant que
GOLD/US100/BTCUSD étaient exclus par construction du Flux B alors que
Station X n'y apporte pas (ou peu) de volume : les deux flux tournent
désormais sur les 8 actifs de la liste blanche, sans exclusivité — quand
Station X est silencieux sur un actif, le Flux B continue quand même
dessus, chacun avec sa propre enveloppe et ses propres métriques.

### 1. Extension du périmètre

`trend_executor.HYPOTHESIS_ASSETS` passe de 5 à 8 :
`["US30", "EURUSD", "GBPUSD", "USDJPY", "ETHUSD", "GOLD", "US100", "BTCUSD"]`.
Aucune autre modification de code nécessaire — la boucle, la génération
de signal, l'exécution partagée (`executor.open_signal`/
`manage_open_trades`) sont déjà génériques par actif.

**Vérification demandée avant déploiement — non-mélange des deux flux
sur un même actif, confirmée par lecture du code, pas supposée** :
- `circuit_breaker_store.get_open_risk_eur()` (plafond d'exposition
  simultanée, §2.3, 10% de l'enveloppe) filtre déjà par `source`
  normalisée — l'exposition Station X et Flux B sur un même actif sont
  comptées et plafonnées séparément, jamais mélangées.
- `get_closed_trades_r()` / `_is_day_triggered_today()` / `_is_latched()`
  (coupe-circuits R, §2.7 : jour/semaine/drawdown) : tous scopés par
  `(actif, source normalisée)` — un coupe-circuit R déclenché côté
  Station X sur GOLD ne bloque jamais le Flux B sur GOLD, et
  réciproquement.
- Routage d'enveloppe (`envelope_managers`/`envelope_ids`, clé
  `(actif, "stationx"|"hypothesis")`) : déjà strictement séparé depuis
  P2.5, aucune collision possible.
- **Seule exception, pré-existante et volontairement non changée** :
  `_is_manual_pause_active()` (`/pause [actif]`, §7.1) n'est **pas**
  scopée par source — une pause manuelle sur un actif coupe les DEUX
  flux à la fois. Documenté comme tel dans le code depuis P2.6 ("§7.1 ne
  distingue pas"), confirmé toujours volontaire ici : si Ismaël pause un
  actif, l'hypothèse par défaut est qu'il veut les deux flux à l'arrêt
  dessus, pas seulement celui qui posait problème.

### 2. Marquage de session horaire (collecte uniquement)

`src/session_marker.py` (nouveau, pur, 100% couvert) :
`compute_market_session(hour_utc)` classe l'heure UTC en `"asie"` |
`"europe"` | `"us"` | `"hors_session"`.

**Convention retenue** (plages fournies par Ismaël, chevauchements à
résoudre) : Asie 00h-08h UTC, Europe 07h-16h UTC, US 13h-21h UTC.
Chevauchements résolus par priorité **US > Europe > Asie** — la session
qui démarre le plus tard dans un chevauchement est conventionnellement
celle où la liquidité bascule (l'arrivée de New York domine
l'après-midi européenne 13h-16h ; l'arrivée de Londres domine la fin de
nuit asiatique 07h-08h). Un choix de convention explicite, pas une
mesure — documenté ici pour ne jamais être présumé plus tard. Résidu
21h-24h UTC (creux de liquidité mondial, hors des trois plages
fournies) : `"hors_session"`.

**Câblé au même point qu'`align_matinale`** (`executor.open_signal()`,
juste après l'insertion du trade) : `trades.session_marche` (nouvelle
colonne), calculée sur `now` — le même horodatage que `ouvert_at`, pas
un second appel horloge qui pourrait diverger. "Ouverture" = placement
de l'ordre limite, pas remplissage — même convention qu'`align_matinale`
pour rester cohérent entre les deux collectes.

**Collecte uniquement, confirmé** : aucun code ne lit `session_marche`
pour une décision — ni `risk_engine`, ni `validator`, ni `executor`.
N'est **pas** une variable du §3.8 (invariant #10) : elle n'entre dans
aucune décision, contrairement aux 5 variables dont le budget est
explicitement suivi dans `docs/HYPOTHESES.md` — pas soumise à la règle
des 10 trades/variable, comme demandé.

**Tests** : `tests/test_session_marker.py` (+11, toutes les branches et
bornes, y compris les deux chevauchements et l'heure invalide),
`tests/test_executor.py` (+1, persistance bout en bout),
`tests/test_trend_executor.py` (garde-fou mis à jour pour les 8 actifs).
441 tests passent, 100% de couverture maintenue.

---

## 2026-08-20 — Le stop garanti trop serré est désormais ÉLARGI, plus rejeté — remplace l'entrée du 16/08/2026

**Remplace explicitement** l'entrée "2026-08-16 — Bug réel trouvé avant
démarrage : stop garanti manquant dans `open_signal`" (plus bas dans ce
fichier) sur le point précis du comportement en cas de stop trop serré.
Le reste de cette entrée-là (constat du bug initial, découverte de
l'exigence de stop garanti) reste vrai et n'est pas remis en cause — seul
le choix "rejeter plutôt qu'élargir" est renversé ici.

**Décision explicite d'Ismaël, en connaissance de cause**, après que
l'audit du 20/08/2026 a montré (par rejeu réel contre l'API Capital.com,
pas par hypothèse) que les 13 signaux GOLD rejetés pour péremption depuis
le 19/08 auraient de toute façon tous été bloqués par le stop garanti
(stops de 2-3 points du canal vs minimum de 1% du prix ≈ 45 points,
13/13 confirmés rejetés en rejouant la fonction réelle contre l'API) :
compte 100% démo, aucun risque financier réel, priorité à l'observation
de ce que donne le signal une fois réellement exécutable plutôt qu'à la
fidélité parfaite au stop d'origine. Le raisonnement du 16/08
("élargir violerait l'invariant #2") reste valable en soi — il est
explicitement mis de côté ici pour ce contexte précis (démo, décision du
porteur du projet), pas invalidé comme principe général.

**Mécanique retenue — `risk_engine` reste la seule source de vérité du
sizing, jamais court-circuité** : `_compute_guaranteed_stop_adjustment()`
(remplace `_compute_guaranteed_stop_distance()`) détermine désormais un
stop EFFECTIF (élargi ou non), sans jamais calculer elle-même un risque
en euros. Si élargi, `open_signal()` reconstruit un `TradeSignal` avec ce
nouveau stop et **rappelle `risk_engine.evaluate_new_entry()`** — la
même fonction critique, 100% couverte, jamais modifiée — pour
redimensionner la position sur la nouvelle distance et garder le risque
en euros plafonné à 2%/4% de l'enveloppe (§2.3), exactement comme demandé :
"logique déjà existante pour le sizing, juste appliquée à un stop
différent". Si ce redimensionnement est lui-même rejeté (ex : taille
retombée sous le minimum broker après élargissement — cas réel possible,
testé), l'entrée est rejetée à ce stade, **jamais** placée avec
l'ancienne taille calculée sur le stop d'origine (aurait dépassé le
risque budgété).

**Portée de l'élargissement, choix délibéré** : seul `risk_engine`
retravaille sur le nouveau stop. `validate_signal()` (péremption §2.8,
tolérance = 50% de la distance de stop) continue d'utiliser le stop
D'ORIGINE du signal, évalué AVANT toute décision d'élargissement — la
péremption mesure la fraîcheur du signal tel qu'émis par le canal, pas
une propriété qui devrait s'assouplir parce que le broker impose un
stop plus large. Élargir la tolérance de péremption en même temps que le
stop aurait été un changement non demandé, plus difficile à isoler
statistiquement plus tard.

**Traçabilité (exigence non négociable de la demande)** :
`trades.stop_elargi` (booléen) + `trades.stop_origine_signal` (le stop
tel qu'émis par le signal, NULL si non élargi) — colonnes additives,
`trades.stop_loss_initial` devient le stop EFFECTIF réellement utilisé
pour le trading et le calcul du R-multiple (cohérent : le R-multiple
doit refléter le risque réellement pris, pas un stop jamais utilisé).
`metrics.py` n'exclut PAS ces trades par défaut (demande explicite) —
aucune fonction de filtrage n'a été ajoutée à ce stade, la colonne
suffit à distinguer les deux populations par requête directe le jour où
c'est utile ; ajouter une fonction dédiée maintenant, sans consommateur,
aurait été de la complexité anticipée non sollicitée.

**Tests** : `tests/test_executor.py` (+8 : les branches pures de
`_compute_guaranteed_stop_adjustment` — non requis/suffisant/élargi
long/élargi short/direction inconnue — et l'orchestration complète :
élargissement + redimensionnement réussi avec les bonnes colonnes en
base, rejet propre si le redimensionnement retombe sous le minimum).
429 tests passent, 100% de couverture maintenue.

---

## 2026-08-20 — Fuite de trade fantôme : `cancel_stale_working_orders()` ne mettait jamais à jour `trades.statut`

Trouvé le même jour en cherchant un ordre en attente pour vérifier les
notifications §7.2 (entrée plus bas) : `trades.id=5` (ETHUSD, Flux B)
bloqué "en_attente" depuis le 19/08 20h21 — 21 heures — alors que l'ordre
n'existait plus du tout côté broker (annulé pour péremption, log
"Ordre limite périmé annulé... âge=912s"). Ismaël a confirmé et demandé
la correction.

**Cause** : `cancel_stale_working_orders()` appelait bien `client.
cancel_working_order()` (annulation réelle côté broker, confirmée par le
log) mais ne touchait jamais `trades.statut` en base — le trade restait
un fantôme "en_attente" indéfiniment.

**Conséquence concrète, pas seulement cosmétique** :
`_has_active_hypothesis_signal_or_trade()` (trend_executor.py) voit ce
trade fantôme et bloque silencieusement tout nouveau signal Flux B sur
l'actif concerné — ETHUSD était de facto exclu du Flux B depuis 21h sans
aucune erreur ni alerte visible.

**Correctif** : après confirmation de l'annulation côté broker
(`client.cancel_working_order()` n'a pas levé d'exception), la ligne
`trades` correspondante — rapprochée par `deal_id`, identifiant broker
unique, aucun filtre par source nécessaire (contrairement à `check_
pending_fills`, cohérent avec le choix déjà documenté de ne jamais
filtrer cette fonction par source) — passe à **`statut='annule'`**
(nouvelle valeur, jamais utilisée avant dans ce projet). Fail-safe
(invariant #7) : en cas d'échec de `cancel_working_order`, le trade
reste "en_attente" sans modification — jamais annulé en base sur une
incertitude côté broker.

**Pas de filtre supplémentaire requis ailleurs** : les requêtes déjà
existantes sur `statut = 'en_attente'` (`_has_active_hypothesis_signal_
or_trade`, `check_pending_fills`, `manage_open_trades`, etc.) excluent
déjà naturellement `'annule'` sans aucune modification — un statut
terminal de plus dans un `WHERE statut = 'en_attente'` littéral n'a
besoin d'aucun câblage particulier.

**Correction rétroactive du trade fantôme** (`id=5`, VPS) : marqué
`statut='annule'` directement en base (le broker avait déjà confirmé
l'annulation il y a 21h, fait déjà vérifié, pas une supposition) — Flux B
peut de nouveau générer des signaux sur ETHUSD dès le prochain cycle.

**Tests** : `tests/test_executor.py` (+2 : trade marqué `annule` après
annulation confirmée, trade laissé `en_attente` si l'annulation échoue
côté broker) + le test existant migré vers une vraie base temporaire
(utilisait jusque-là un chemin `"unused.db"` littéral, jamais initialisé
— suffisant tant que la fonction ne touchait pas la DB, plus maintenant).
426 tests passent, 100% de couverture maintenue.

---

## 2026-08-20 — `trades.cloture_reason` : distinguer une clôture forcée (/stop_urgence) d'une sortie organique

Ismaël s'apprêtait à déclencher `/stop_urgence` lui-même depuis Telegram
pour se familiariser avec la commande (déjà validée techniquement en
production, ceci est un test de prise en main, pas une vérification
supplémentaire) — sur les positions Flux B alors ouvertes (GBPUSD id=6,
US30 id=7). Demande explicite : que cette clôture soit identifiable comme
forcée, pas confondue avec un stop/trailing organique.

**Constat avant correctif** : rien ne distinguait les deux cas dans les
données structurées. `trade_partials.palier` vaut `"sl"` pour TOUTE
`CLOSE_FULL_STOP` (stop initial, breakeven, trailing, ET /stop_urgence
confondus — voir son commentaire dans `db.py`) ; `trade_analysis.
denouement` (dérivé du dernier palier via `trade_analyzer.py`) aurait
donc affiché `"sl_hit"` pour un arrêt d'urgence, indiscernable d'un vrai
stop touché. Seul le texte libre `trade_partials.motif`/`action.detail`
("Arrêt d'urgence (/stop_urgence)") portait l'information, jamais
structuré ni filtrable.

**`trades.cloture_reason`** (nouvelle colonne, migration additive comme
les autres du jour) : code parmi `"stop_initial"` | `"stop_breakeven"` |
`"trailing"` | `"stop_urgence"`. Réutilise la fonction `_infer_close_
reason()` déjà écrite aujourd'hui pour la notification de clôture (§7.2,
entrée précédente) — un seul endroit calcule la distinction, la
notification et la colonne DB lisent la même valeur (`_CLOSE_REASON_
LABELS` traduit le code en libellé français pour l'affichage, jamais
l'inverse).

**Décision sur `metrics.py` : exclu des statistiques en R, pas du P&L en
euros.** Un arrêt d'urgence sort au prix du marché à un instant
arbitraire (déclenché manuellement, ou par une anomalie système) — ça ne
mesure jamais si le placement du stop/TP/trailing de la stratégie est
bien calibré, contrairement à une sortie organique ; le mélanger aux
statistiques d'espérance/profit factor fausserait l'évaluation de la
stratégie elle-même. Nouvelle fonction `metrics.get_closed_trades_r_for_
stats()` (filtre `cloture_reason != 'stop_urgence'`), utilisée à la
place de `circuit_breaker_store.get_closed_trades_r()` — **jamais
l'inverse** : le coupe-circuit (§2.7) doit rester informé de TOUTE perte
réalisée y compris un arrêt d'urgence (invariant #7, fail-safe) ; filtrer
cette fonction aurait été une régression de sécurité, pas une
amélioration statistique. Le P&L en euros (`envelope_ledger`) n'est PAS
filtré non plus : l'argent a réellement bougé sur l'enveloppe, peu
importe pourquoi le trade s'est fermé. `get_trade_counts_by_period`
(nombre de trades, bloc "Trades" du dashboard) n'est pas filtré non plus,
volontairement — c'est un compte d'activité/audit, pas une mesure de
performance de la stratégie.

**Message `/stop_urgence` du bot de contrôle renforcé** : mentionne
désormais explicitement "Station X ET Flux B" (pas seulement le flux qui
avait des positions ouvertes au moment du test) et le rappel `/reprendre`
en évidence — la version précédente disait déjà "jusqu'à /reprendre"
mais sans jamais nommer les deux flux, risque de confusion pour un test
ciblé sur un seul des deux.

**Tests** : `tests/test_executor.py` (+2 : les 4 branches de `_infer_
close_reason`, `cloture_reason='stop_urgence'` + libellé notifié bout en
bout via `force_close_all_open_trades`), `tests/test_metrics.py` (+1,
exclusion confirmée), `tests/test_control_bot.py` (+3 assertions sur le
message renforcé). 424 tests passent, 100% de couverture maintenue.

---

## 2026-08-20 — §7.2 : notifications ouverture/clôture de position ajoutées + audit complet

Remonté par Ismaël : les deux trades Flux B (GBPUSD, US30) ont été
ouverts sans aucune notification — découvert en vérifiant lui-même sur
l'app Capital.com. Le §7.2 exige explicitement une notification à
l'ouverture, à chaque clôture partielle, et à la clôture finale (avec
R-multiple). Ni `executor.py` ni `trend_executor.py` n'en envoyaient
aucune — confirmé par grep (`send_notification`/`send_document`
n'apparaissaient dans aucun des deux avant ce jour).

**Ouverture — notifiée au REMPLISSAGE, pas au placement de l'ordre**
(`check_pending_fills`, nouveaux paramètres `bot_token`/`chat_id`) : un
ordre limite placé (`open_signal`) peut être annulé par péremption sans
jamais être exécuté (§2.8) — le notifier à ce stade aurait annoncé des
positions qui n'existent parfois jamais vraiment. `prix_entree_reel`
n'est de toute façon connu qu'au remplissage. Contenu : actif, source
(`stationx`/`hypothesis`, normalisée comme partout ailleurs), sens, prix
d'entrée réel, stop initial, taille.

**Clôture partielle — uniquement TP1/TP2 Station X.** Le Flux B n'a
structurellement aucune clôture partielle (`trend_strategy.evaluate_entry`
ne produit jamais de TP1/TP2, voir docs/HYPOTHESES.md du 20/08/2026) —
la phrase du prompt "palier de trailing pour Flux B" est couverte par la
clôture FINALE ci-dessous (raison="stop suiveur (trailing)"), pas par
une notification de palier intermédiaire qui n'existe pas pour ce flux.

**Clôture finale — raison reconstruite depuis l'état, pas stockée
littéralement.** `trade_partials.palier` vaut toujours `"sl"` pour toute
`CLOSE_FULL_STOP` (stop initial, breakeven, trailing confondus — voir
son commentaire dans `db.py`), et `action.detail` ne les distingue pas
non plus. `_infer_close_reason()` compare `state.stop_price` à
`state.initial_stop_price`/`entry_price` au moment de la clôture pour
retrouver lequel des trois a réellement été touché ("arrêt d'urgence"
détecté séparément via `action.detail`). La raison "fermeture macro
anticipée" mentionnée dans la demande n'a **aucun chemin de code
existant** (dépend du futur `macro_calendar`, §2.9, absent) — non
implémentée, pas de raison inventée pour un cas qui ne peut pas se
produire aujourd'hui.

**Bug réel trouvé en câblant cette notification** : `trades.r_multiple_total`
ne stockait que le R du DERNIER palier fermé (`action.r_multiple`),
jamais le total pondéré sur l'ensemble des paliers, malgré son nom.
`risk_engine.compute_weighted_r_multiple()` existait déjà (§2.10, testée
à 100%) mais n'était appelée nulle part dans `executor.py` — trouvé en
voulant afficher un "R-multiple total" correct dans la notification de
clôture. Corrigé (`_weighted_r_multiple_for_trade()`, lit tous les
`trade_partials` du trade et pondère). Impact rétroactif : les trades
déjà clos en base avant ce correctif gardent leur `r_multiple_total`
sous-estimé/faux pour tout trade ayant eu au moins un palier partiel
avant sa clôture finale — aucun trade réel de ce type n'existe encore à
ce jour (vérifié : les seuls trades fermés à ce jour sont des clôtures à
paliers uniques, non affectés), donc aucune correction rétroactive de
données nécessaire.

**Audit complet §7.2** (état vérifié dans le code, pas supposé) :

| Notification (§7.2) | État | Détail |
|---|---|---|
| Ouverture et clôture de position (R-multiple) | ✅ Présent (corrigé aujourd'hui) | `check_pending_fills`/`_apply_management_action` |
| Clôture partielle à chaque palier | ✅ Présent (corrigé aujourd'hui) | TP1/TP2 Station X uniquement, voir ci-dessus |
| Déclenchement de coupe-circuit | ✅ Présent (déjà en place, P2.6) | `circuit_breaker_store.record_trigger` |
| Bascule 2 %↔4 % | ⬜ Sans objet | Aucun chemin de code ne met `boosted=True` — dépend de `confidence_scorer.py`, non construit (confirmé, docs/HYPOTHESES.md) |
| Réallocation de capital (§2.5) | ⬜ Sans objet | Mécanisme d'allocation réel multi-actifs par score de confiance — aucun capital réel, `confidence_scorer.py` non construit |
| Contradiction Matinale (§3.4) | ✅ Présent (déjà en place, P1) | `telegram_listener._handle_matinale`, notifiée en permanence même hors `audit_all` |
| Signal hors liste blanche | ❌ Absent | Rejeté et journalisé dans `risk_decisions` (`ASSET_NOT_WHITELISTED`), jamais notifié spécifiquement — non corrigé aujourd'hui (hors périmètre demandé) |
| Échec d'extraction répété | ❌ Absent | Seul un flag ponctuel (`raison_rejet='extraction_incomplete'`) existe par signal ; aucun compteur de répétition (contrairement à `api_error_streak`, §2.7) — non corrigé aujourd'hui |
| Absence de message depuis 7 jours | ✅ Présent (déjà en place, P2.6) | `circuit_breaker_store.check_channel_inactivity` |
| Erreur API | ✅ Présent (déjà en place, P2.6 ; trou de détection réseau corrigé plus tôt le 20/08/2026) | `circuit_breaker_store.record_api_result`, seuil 3 échecs consécutifs |
| Franchissement d'un palier de métriques | ❌ Absent | Aucune notion de "palier"/seuil dans `metrics.py` — calcul strictement à la demande (`/metriques`, `/dashboard`), confirmé par grep, pas de mécanisme proactif du tout |
| Actif atteignant les critères de passage en réel | ⬜ Sans objet | Aucun code, aucune trace, aucun stub — confirmé par grep (`passer_reel`, `passage.*reel` : zéro résultat dans `control_bot.py`/`go_nogo.py`) |
| *(hors CDC, ajout P2.6)* Absence de processus | ✅ Présent | `scripts/process_watchdog.py`, alerte une fois par transition vivant→mort |

Trois items restent **absents** (signal hors liste blanche, échec
d'extraction répété, franchissement de palier de métriques) — non
corrigés aujourd'hui, hors périmètre de la demande ("vérifie", pas
"corrige" pour ces trois). Candidats pour un futur palier si souhaité.

**Tests** : `tests/test_audit_notifier.py` (+3, nouveaux formats),
`tests/test_executor.py` (+4 : notification d'ouverture, régression du
R pondéré en isolation, bout en bout multi-paliers avec les deux
notifications). 422 tests passent, 100% de couverture maintenue sur les
modules critiques.

---

## 2026-08-20 — §3.8 : collecte de la variable #1 (`align_matinale`) activée

Suite directe de la recalibration de `extract_matinale()` ci-dessous, qui
débloquait cette collecte. Demande explicite d'Ismaël : collecte
uniquement, aucune décision n'en dépend.

**État réel des 5 variables du §3.8, vérifié dans le code (pas supposé)** :
`trade_features` (schéma §4.5) existait depuis le palier P2 mais
**n'était écrite nulle part** — grep confirmé sur tout `src/` avant cette
entrée. Aucune des 5 colonnes n'était donc collectée. Après ce lot :
- `align_matinale` : **collectée**, voir ci-dessous.
- `align_tendance_fond`, `ratio_gain_risque_prevu`, `proximite_macro`,
  `volatilite_relative` : **toujours non collectées**. Rien dans ce lot
  ne les construit — hors périmètre de la demande, à traiter séparément
  quand elles seront demandées (pas de "tant qu'à faire" non sollicité).

**`src/trade_features_store.py`** (nouveau module) :
- `compute_align_matinale(direction, biais)` : pure, 100% couverte.
  Retourne `True` (aligné), `False` (opposé), ou `None` si `biais` est
  absent, "neutre" ou "indetermine" — un biais non directionnel n'est ni
  aligné ni opposé, jamais deviné.
- **Encodage retenu sur `trade_features.align_matinale INTEGER`** (colonne
  du §4.5, déjà figée, pas de migration) : `1`=aligné, `0`=opposé,
  `NULL`=non disponible. Tri-état sur une colonne binaire plutôt qu'une
  migration de schéma pour ajouter une distinction "neutre"/"absent" —
  le §3.8 ne demande que "aligné/opposé/non disponible", ce que NULL
  couvre déjà sans ambiguïté ; la distinction fine "neutre déclaré" vs
  "aucune Matinale du tout" n'a pas d'usage identifié aujourd'hui.
- `get_latest_matinale_biais(db_path, actif, before)` : lit
  `matinale_summaries.sentiment_tag` (pas `biais_corps` — c'est le biais
  **déclaré** par le canal que le §3.8 demande, pas l'heuristique du
  corps, qui reste réservée à la détection de contradiction §3.4).
  Filtre `published_at <= before` explicitement — jamais une Matinale
  future par rapport au trade, pour ne pas introduire de biais
  rétrospectif dans la future analyse statistique (invariant #10).

**Point de collecte : à l'OUVERTURE du trade, pas à la clôture.** Câblé
dans `executor.open_signal()`, juste après l'INSERT dans `trades` (donc
commun à Station X ET au Flux B — `trend_executor.py` appelle la même
fonction, aucun câblage séparé nécessaire). Choix distinct de
`trade_analyzer.compute_trade_features()` (qui tourne à la CLÔTURE, table
`trade_analysis`, un objet complètement différent malgré la ressemblance
de nom) : le §3.8 veut savoir si le trade était aligné avec le biais
connu AU MOMENT de l'entrée, pas reconstruit après coup.

**Best-effort, non bloquant** : encapsulé dans un `try/except` autour de
l'appel dans `open_signal()` — un échec de cette collecte ne remet
jamais en cause l'ouverture déjà actée (déjà journalisée en base avant
cet appel), même patron que l'analyse post-trade dans
`_apply_management_action`.

**Tests** : `tests/test_trade_features_store.py` (15, nouveau, 100% de
couverture du module) + `tests/test_executor.py` (+1, bout en bout via
`open_signal`). 416 tests passent au total, 100% maintenu sur les
modules critiques.

---

## 2026-08-20 — Recalibration d'`extract_matinale()` sur un exemple réel du format actuel

Premier vrai post Matinale détaillé partagé par Ismaël (canal Station X,
20/08/2026) depuis le backfill fév-mars 2025 qui avait servi de base
initiale (§P1 de `CLAUDE.md` : "format probablement obsolète, à recalibrer
sur la prochaine vraie Matinale").

**Constat** : le format a effectivement changé sur deux points.
1. Le motif "reste donc <mot>" (heuristique de `biais_corps`) n'apparaît
   pas dans ce texte — normal, il ne prétendait couvrir qu'UN motif
   possible parmi d'autres, jamais retiré (toujours actif sur l'ancien
   exemple, `tests/test_parser.py::MATINALE`).
2. Le tag "Sentiment X" (§3.4 littéral du CDC) est absent ; à la place,
   chaque paragraphe par actif se termine par "Biais {haussier|baissier|
   neutre}." — je traite ce tag comme le libellé actuel du même champ
   déclaré (repli : `_SENTIMENT_TAG_MATINALE` cherché en premier,
   `_BIAIS_TAG` en second), pas un troisième signal séparé — les deux
   alimentent `sentiment_tag`, jamais un nouveau champ.

**Conséquence directe sur la détection de contradiction (§3.4)** : sans
"reste donc X" dans ce format (texte technique de niveaux/FVG/Fibonacci,
sans adjectif directionnel dans le corps), `biais_corps` reste
"indetermine" pour les deux blocs de l'exemple — donc `contradiction_
detectee` reste `False`. C'est le comportement fail-safe voulu (jamais de
contradiction inventée), mais ça signifie concrètement que la détection
de contradiction §3.4 est **dormante sur ce nouveau format** tant qu'un
exemple réel ne montre pas comment (ou si) le canal exprime une
divergence corps/tag dans ce style d'écriture. Pas comblé par une
heuristique inventée sans preuve — à recalibrer si/quand un tel exemple
apparaît.

**Segmentation par actif changée** : l'ancien découpage sur le séparateur
visuel "✅" (`re.split`) ne fonctionne plus, cet émoji étant absent de
l'exemple. Remplacé par un ancrage sur la position des en-têtes de bloc
eux-mêmes ("Du côté du X en <horizon>") — `_split_asset_paragraphs()`,
indépendant de toute convention de mise en forme du canal. Le dernier
bloc s'arrête juste après son propre tag de biais pour ne jamais déborder
sur un paragraphe de clôture ou d'annonces macro (testé explicitement,
`test_extract_matinale_format_reel_macro_paragraph_not_extracted_as_asset`).
Les deux exemples (ancien ET nouveau format) passent avec ce même
découpage, aucune régression.

**Bug de classification trouvé pendant la recalibration** : sans le mot
"Matinale"/"point marché" explicite ailleurs dans le message,
`message_classifier._looks_like_matinale()` exigeait un tag "Sentiment X"
pour son repli structurel — un message de ce nouveau format échouerait
silencieusement à être classé "matinale" (jamais atteint `extract_
matinale()` du tout). Corrigé : le repli accepte aussi "Biais X.". Testé
(`test_classifies_matinale_format_reel_via_biais_tag_fallback`).

**Nouveaux champs numériques extraits par actif** (calibrés sur cet UNIQUE
exemple, `matinale_summaries` migrée en conséquence — colonnes
additives, nullables) : `prix_courant`, `zone_depart_min/max`,
`niveau_majeur`, `fvg_haut/bas`, `fib_50/618/786`. Deux formulations
réelles différentes observées pour la zone FVG dans le même message
(Bitcoin : borne haute explicite + borne basse déduite d'une phrase liant
explicitement le niveau de Fibonacci 78,6% au "bas de la zone" ; Gold :
les deux bornes données ensemble, "FVG... approximativement entre X $ et
Y $") — les deux formulations sont supportées, aucune bas de zone n'est
jamais déduite par convention silencieuse (ex: "toujours le Fibonacci le
plus profond") sans lien textuel explicite. **Calibré sur un seul
exemple réel** : à ajuster si un futur post révèle une formulation
différente (même prudence que `extract_signal`/`extract_suivi` en leur
temps, §CLAUDE.md P1).

**Sur l'extraction d'image (chart annoté)** : dans cet exemple, l'image
jointe (chart H4 avec zone FVG grisée) ne semble apporter aucune
information au-delà du texte — mêmes niveaux, même zone. Priorité donnée
au texte (riche et suffisant ici) : l'extraction d'image n'est **pas**
construite à ce palier, reste une piste à activer seulement si un futur
exemple montre une image porteuse d'une info absente du texte.

**Sur le bloc annonces macro** : utile en complément mais ne remplace pas
le futur module `macro_calendar` (§2.9, toujours absent) — le canal ne
mentionne pas nécessairement systématiquement toutes les annonces à fort
impact, s'y fier comme unique source donnerait une couverture
incomplète. Volontairement non extrait en données structurées (le §3.8
ne demande que la variable #4 "proximité annonce macro", pas une base de
calendrier macro complète — hors périmètre de ce lot).

**Tests** : `tests/test_parser.py` (+5, nouvel exemple réel complet,
ancien exemple toujours vert), `tests/test_message_classifier.py` (+1,
régression du bug de classification), `tests/test_audit_notifier.py`
(+1, repli d'affichage). 400 tests passent à ce stade (avant l'ajout de
`trade_features_store.py` ci-dessus), aucune régression.

---

## 2026-08-20 — Deuxième ordre manuel confirmé sur le compte démo système : `record_manual_test_movement()` écrit

Suite directe de l'entrée du 19/08/2026 ci-dessous ("Vérification d'un
trade manuel signalé"). En déployant le trailing Flux B (entrée
suivante), une erreur 404 ("position introuvable") sur le trade EURUSD
(id=4) a mené à l'historique d'activité du compte démo Capital.com : un
ordre SELL de 1000 unités EURUSD, `source: "USER"`, exécuté le
20/08/2026 à 06:50:44 UTC, sans aucune correspondance dans `trades`/
`signals` — a clôturé la position longue du Flux B par netting.

**Confirmé par Ismaël : trade manuel, comme le 19/08/2026.** Ce n'est
plus un cas isolé — la consigne "aucun ordre manuel sur ce compte démo,
utiliser un compte démo séparé pour tout test" (déjà dans `CLAUDE.md`
depuis le 19/08) reste la référence ; cette entrée documente
l'occurrence, pas un changement de règle.

**Réconciliation appliquée** (mécanisme envisagé mais pas encore écrit
au 19/08, écrit maintenant) : `envelope_store.record_manual_test_movement()`
— crédite le montant réel à l'enveloppe (`envelope_ledger.type_mouvement
= 'manual_test'`, jamais `'trade_pnl'`, donc invisible de
`metrics.get_trade_pnl_movements` sans code supplémentaire, comme prévu
le 19/08), **sans** passer par la règle de réinvestissement des 50%
(§2.3) — un trade hors système n'est pas un gain de trading à répartir
vers la réserve sanctuarisée.

Écart assumé par rapport à la proposition initiale du 19/08 ("trade_id
NULL, pas de ligne trades associée") : ici une ligne `trades` (id=4)
existe déjà et est simplement marquée `statut='ferme'` séparément (le
module ne la modifie jamais lui-même, invariant de conception déjà en
place pour `persist_trade_result`) — `trade_id=4` est donc bien
renseigné dans le mouvement `manual_test`, pour la traçabilité. La
fonction accepte aussi `trade_id=None` pour le cas d'origine (trade
entièrement hors `trades`).

**Chiffres réels** (calculés à partir de l'historique broker, pas
estimés) : entrée 1.16779, clôture 1.16836 (niveau du côté SELL de la
transaction de netting), swap -0.16$ (prélevé la veille), soit +0.41$
net → **+0,35€** au taux EUR/USD du moment de la clôture (1.16836).
`r_multiple_total` recalculé via `risk_engine.compute_r_multiple` (même
fonction que toute clôture système, jamais réinventée) : **+0,0529R**.
`trades.id=4` marqué `statut='ferme'`, `ferme_at` = l'horodatage exact
de l'événement broker (06:50:44.905 UTC), `pnl_net=0.35` — cohérent avec
le fait que ni `_apply_management_action` ni ce correctif ne peuplent
jamais `pnl_brut`/`couts` (colonnes du schéma §4.5 restées inutilisées
dans tout le code existant, pas une omission propre à ce correctif).

**Tests** : `tests/test_envelope_store.py` (+3, montant complet crédité
sans partage de réserve, exclusion confirmée de `get_trade_pnl_movements`,
`trade_id=None` accepté).

---

## 2026-08-20 — Flux B : trailing Donchian dès l'ouverture (sortie sur profit manquante)

Remonté par Ismaël en observant les deux premiers trades réels du Flux B
(GBPUSD, US30, ouverts ce jour). Détail du raisonnement et de la
mécanique dans `docs/HYPOTHESES.md` (entrée du même jour, dédiée à
l'Hypothèse #1) — résumé ici pour la traçabilité des écarts.

**Constat** : `trend_strategy.evaluate_entry()` ne produit jamais de
TP1/TP2/TP3, mais la gestion de position d'`executor.py` (partagée avec
Station X) ne déclenche le trailing ATR qu'après TP1 **et** TP2 —
condition qui ne peut jamais devenir vraie sans TP. Seul le stop initial
fixe pouvait donc clôturer un trade du Flux B : oubli d'implémentation
du palier P2.5, jamais une décision actée, corrigé aujourd'hui.

**Décision (tranchée par Ismaël entre deux options proposées)** :
trailing sur le canal de Donchian(20) — la même fenêtre déjà utilisée
pour le déclencheur et le stop initial, aucun paramètre supplémentaire —
plutôt qu'un trailing ATR (aurait introduit un second mécanisme de
sortie sans justification théorique propre à l'hypothèse) ou des TP fixes
façon Station X (aurait introduit des ratios gain/risque choisis
arbitrairement, invariant #10).

**Code** : `src/trend_strategy.compute_trailing_stop_channel()` (nouvelle
fonction pure, module critique, 100% couverte) ; câblée dans
`executor._evaluate_position_management` sur le critère `state.tp1 is
None` (identifie sans ambiguïté un trade Flux B) ; `manage_open_trades`
récupère désormais `DONCHIAN_PERIOD + 1` bougies (au lieu de 20) pour
disposer d'assez d'historique. Le stop candidat passe par
`risk_engine.evaluate_stop_update()` comme le trailing ATR de Station X —
resserrement seul, jamais élargi (invariant #5).

**Appliqué rétroactivement aux deux trades déjà ouverts** (GBPUSD id=6,
US30 id=7) : aucune migration de données nécessaire, le trailing se
recalcule à partir de `stop_loss_courant` déjà en base dès le
redéploiement.

**Tests** : `tests/test_trend_strategy.py` (+6 tests, fonction pure) et
`tests/test_executor.py` (+6 tests, branche Flux B de
`_evaluate_position_management`). 100% de couverture maintenue sur
`risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`/
`circuit_breaker`/`metrics`.

---

## 2026-08-20 — Bug de production trouvé en déployant le trailing Flux B : `update_position_stop` sans stop garanti

Trouvé immédiatement après le redéploiement du trailing Donchian
ci-dessus, en observant les tout premiers cycles réels sur le VPS : les
3 positions Flux B alors ouvertes (EURUSD id=4, GBPUSD id=6, US30 id=7)
échouaient systématiquement à chaque tentative de resserrement de stop —
`CapitalApiError: 400 ... error.vallidation.guaranteed-stop-loss.required`.

**Cause** : `capital_client.update_position_stop()` n'envoyait que
`{"stopLevel": ...}`. Or ce compte démo exige un stop garanti sur de
nombreux instruments (déjà documenté au palier P0 pour BTCUSD/ETHUSD, et
"confirmé aussi sur EURUSD" à l'ouverture — voir `CLAUDE.md`) : une
position ouverte avec `guaranteedStop: true` refuse toute mise à jour de
`stopLevel` qui ne réaffirme pas `guaranteedStop: true` dans le même
appel. Vérifié empiriquement en lecture puis en écriture sur le compte
démo (`GET /positions` confirme `guaranteedStop: true` sur les 3
positions concernées ; un `PUT` de test avec `guaranteedStop: true`
ajouté, `stopLevel` inchangé, réussit). Ce bug était **latent depuis le
palier P2** pour Station X aussi (même fonction, même chemin de code
pour le trailing ATR post-TP1/TP2) — jamais manifesté faute qu'un trade
Station X ait déjà atteint ce stade.

**Correctif** :
- `trades.guaranteed_stop` (nouvelle colonne, migration additive comme
  `deal_id` en son temps) : persisté à l'ouverture (`open_signal`, déjà
  calculé via `_compute_guaranteed_stop_distance`), lu par
  `_load_open_trade_state`, transmis par `_apply_management_action` à
  `client.update_position_stop(..., guaranteed_stop=state.guaranteed_stop)`.
- Écarté délibérément : deviner via un texte d'erreur (retry sur
  `error.vallidation.guaranteed-stop-loss.required`) plutôt que stocker
  le fait — plus fragile (dépend d'un message d'erreur non contractuel)
  et contraire au style déterministe déjà en place ici (le fait est déjà
  connu à l'ouverture, pas besoin de le redécouvrir par l'échec).
- **Correction ponctuelle des 3 trades déjà ouverts** au moment du bug
  (`UPDATE trades SET guaranteed_stop = 1 WHERE id IN (4, 6, 7)`) :
  synchronisation avec un fait déjà vérifié côté broker (`GET /positions`
  ci-dessus), pas une supposition — même logique que
  `check_pending_fills` qui réécrit déjà `deal_id` depuis la vérité
  broker.

**Tests** : `tests/test_capital_client.py` (+1, corps de la requête PUT
avec `guaranteedStop`) et `tests/test_executor.py` (+1, régression de
bout en bout : trade Flux B avec stop garanti, trailing déclenché,
vérifie l'appel exact à `update_position_stop`). 100% de couverture
maintenue sur les modules critiques.

---

## 2026-08-20 — Trou dans la détection d'anomalie API (§2.7) : erreurs réseau brutes non capturées

Trouvé en investiguant pourquoi le Flux B semblait ne plus rien tenter
après ses deux premiers trades du jour : le log de `trend_executor`
montrait 15 `RemoteDisconnected` (coupures de connexion côté API démo
Capital.com) depuis la veille, sans jamais avoir déclenché la surcouche
anomalie système (§2.7 : 3 échecs consécutifs de la sonde de
connectivité → pause). En pratique la boucle récupérait seule à chaque
fois (retry au cycle suivant), donc sans conséquence observée à ce jour —
mais le garde-fou lui-même était aveugle à ce mode de panne.

**Cause** : la sonde de connectivité (`run_executor_loop` et
`run_trend_loop`, code dupliqué intentionnellement — voir l'entrée P2.6
sur `circuit_breaker_store`) ne capturait que `CapitalApiError`. Or
`capital_client.py` ne traduit en `CapitalApiError` que les erreurs HTTP
(`requests.HTTPError`, via `raise_for_status()`) — une coupure réseau
brute (`requests.exceptions.ConnectionError`/`RemoteDisconnected`, pas
un code HTTP) n'est pas enveloppée et retombait directement dans le
`except Exception` générique du bas de boucle, qui n'incrémente jamais
`circuit_breaker_store.record_api_result`.

**Correctif** : `except (CapitalApiError, requests.exceptions.RequestException)`
dans les deux boucles — `RequestException` est la classe de base de
toutes les exceptions `requests` (HTTP, connexion, timeout), donc ce
correctif couvre aussi les futurs modes de panne réseau sans capturer
plus large que nécessaire (`except Exception` resterait un filet
générique volontairement distinct, en dernier recours). Si Capital.com
devient durablement inaccessible, la pause générale des entrées (§2.7)
se déclenchera désormais correctement.

---

## 2026-08-19 — Vérification d'un trade manuel signalé par Ismaël : aucun écart trouvé

Ismaël a signalé avoir passé un trade manuellement sur le compte démo
Capital.com (directement sur la plateforme, hors système) pour tester le
compte — date/actif non précisés. Vérification demandée avant toute
correction.

**Méthode** : plutôt qu'une reconstruction comptable du solde attendu
(peu fiable ici — le solde réel du compte agrège aussi l'activité des
scripts P0 hors `trades`, ex. `calibrate_pip_value.py`, qui ne
journalisent jamais dans la DB par conception), rapprochement direct par
`dealId` : liste de TOUTE l'activité du compte depuis sa création
(`GET /history/activity`, du 16/08/2026 à aujourd'hui) comparée
exhaustivement à `trades` et à l'historique documenté des scripts P0/P2.

**Constat** : compte unique sous ce login (`GET /accounts` : un seul
compte, "premier test", pas de sous-compte caché où le trade manuel
aurait pu atterrir). 37 événements d'activité au total, 14
`POSITION/ACCEPTED`, correspondant à 8 positions distinctes :
- 5 le 16/08 matin (11h10-11h18 UTC, BTCUSD/ETHUSD, tailles 0,005-type,
  cycles ouverture/fermeture de ~1-2 min) : profil de
  `calibrate_pip_value.py` (§P0), jamais censé écrire dans `trades`.
- 1 le 16/08 après-midi (18h25-18h26 UTC, BTCUSD SELL 0,005, stop
  garanti, fermée ~90s après) : même profil scripté que ci-dessus — pas
  dans `trades` non plus, cohérent avec un cycle de calibration/test
  supplémentaire.
- 1 le 16/08 après-midi (18h54-19h03 UTC, BTCUSD) : `dealId` identique à
  `trades.id=3` (`deal_id=00000000-5be9-cdc4-...`, `source=test_manuel_p2`)
  — le test réel encadré documenté du palier P2. Correspond exactement.
- 1 aujourd'hui (19h30 UTC, EURUSD BUY 1000 unités) : `dealId` identique
  à `trades.id=4` (`source=hypothesis`) — le Flux B (`trend_executor`)
  qui vient d'ouvrir légitimement une position réelle après un signal
  Donchian(20). **Ce n'est pas le trade manuel signalé** : taille et
  horodatage collent exactement à ce que le système a lui-même journalisé.

**Aucune activité restante, aucun dealId orphelin.** Solde réel actuel
~998,2-998,3€ (`deposit` interne Capital.com : 998,58€, contre un
financement de départ ~1000€) — l'écart de ~1,4€ est cohérent avec les
pertes déjà connues et documentées des tests P0/P2 (aucune activité
inconnue à imputer).

**Conclusion : aucun trade manuel non tracké identifié.** Deux
hypothèses à vérifier avec Ismaël plutôt qu'une correction sans
fondement : (a) le trade a été passé sur un AUTRE compte démo
Capital.com (un second identifiant, pas celui configuré dans `.env`) ;
(b) le trade décrit n'a en réalité pas été exécuté (navigation sur la
plateforme sans validation finale). Rien n'a été journalisé ni corrigé
dans la DB — pas d'écart confirmé, pas de correction à faire.

**Mécanisme proposé si un écart est confirmé un jour** (non implémenté
maintenant, faute de cas réel à traiter) : un mouvement
`envelope_ledger.type_mouvement = 'manual_test'` distinct de `'trade_pnl'`,
avec son propre `trade_id` NULL (pas de ligne `trades` associée) —
`metrics.py` ne lit que `type_mouvement = 'trade_pnl'` (voir
`get_trade_pnl_movements`), donc un mouvement `manual_test` serait déjà
naturellement exclu de toutes les métriques statistiques sans code
supplémentaire. Resterait à écrire : une petite fonction de
`envelope_store.py` pour l'enregistrer (ajustement de
`capital_courant` + ligne `manual_test`), jamais appelée
automatiquement — seulement à la demande, en cas de trade manuel confirmé
à l'avenir malgré la consigne ci-dessous.

**Recommandation, appliquée immédiatement (voir aussi CLAUDE.md)** :
plus aucun ordre manuel sur ce compte démo Capital.com — il est
strictement réservé au système (calibration + exécution automatique).
Tout test manuel de la plateforme à l'avenir doit utiliser un compte
démo Capital.com séparé, jamais celui configuré dans `.env`.

---

## 2026-08-20 — `/aide` + menu natif Telegram (`setMyCommands`)

Demande explicite d'Ismaël : les commandes du bot toujours visibles dans
Telegram (menu natif au "/"), pas un pense-bête qui deviendrait obsolète.

**Liste unique** : `control_bot.COMMANDS` (nom, description courte) est
la seule source — alimente à la fois `/aide` et `register_bot_commands()`
(`setMyCommands`). Toute commande future doit être ajoutée **uniquement**
à cette liste, jamais dans une chaîne de texte séparée.

**Ré-enregistrement automatique, pas une étape manuelle à se rappeler** :
`register_bot_commands()` est appelée à **chaque démarrage** de
`run_control_bot_loop()`, donc à chaque redéploiement qui redémarre
`control_bot` (déjà systématique pour toute modification de code de ce
module, cf. les redémarrages P2.6/P2.7). Conséquence pratique pour les
prochains ajouts (`/ajouter_actif`, `/passer_reel`, etc., §7.1) :
1. Ajouter `(nom, description)` à `COMMANDS`.
2. Ajouter la branche `if command == "nom": ...` dans `handle_command`.
3. Redémarrer `control_bot` sur le VPS (kill + relance tmux, procédure
   déjà utilisée) — le menu Telegram se met à jour tout seul à ce moment,
   aucune commande `setMyCommands` à lancer séparément.

Échec réseau de `setMyCommands` : jamais bloquant (log + le process
continue), un menu non rafraîchi n'empêche aucune commande de fonctionner
via texte brut.

---

## 2026-08-20 — `metrics.py` + `dashboard.py` (§4.4, §4.5, §4.6)

Plan validé par Ismaël avant codage (deux confirmations explicites).

### `metrics.py` — snapshot périodique non implémenté

Calcul à la demande, jamais écrit dans `metrics_snapshot` (§4.5) malgré
son existence dans le schéma : rien d'autre ne lit cette table
aujourd'hui, y ajouter un scheduler serait de la complexité sans
consommateur. Réversible — si un suivi de tendance dans le temps devient
utile (ex: espérance qui se dégrade progressivement), écrire dedans
plus tard sans changer l'API de calcul.

Couverture 100% (demande explicite d'Ismaël, 20/08/2026) : ce module
alimentera plus tard la bascule 2%/4% (§2.3) et le score de confiance
(§2.4), donc un bug de calcul ici a un impact financier réel même si
aujourd'hui il n'est que reporting.

### `dashboard.py` — envoi en pièce jointe Telegram, pas de serveur web

**Écart assumé au §4.6 littéral** ("génère une page HTML statique avec
lien temporaire"), validé explicitement par Ismaël le 20/08/2026 :
`/dashboard` envoie le fichier HTML généré directement en pièce jointe
Telegram (`audit_notifier.send_document`, nouvelle fonction utilisant
`requests` — déjà une dépendance déclarée du projet — plutôt que
`urllib`, un multipart/form-data à la main serait disproportionné),
qu'Ismaël ouvre localement dans son navigateur. Zéro port exposé, zéro
process supplémentaire à faire tourner et sécuriser : dépasse l'objectif
du §4.6 ("pas de surface d'attaque exposée en continu"), qui suppose
malgré tout un serveur web, même bref.

### Blocs volontairement vides (Hypothèses officiel, Classement, Décisions)

Trois blocs du §4.6 dépendent de modules confirmés absents par l'audit
du 19/08/2026 (`confidence_scorer`, `allocator`, `hypothesis_engine`
officiel du §3.9). Affichés vides et clairement étiquetés "non
construit" — jamais simulés avec des données factices (demande
explicite d'Ismaël). Le bloc "Hypothèses" affiche quand même, à part, la
progression réelle du Flux B (nb de trades clôturés vers le seuil de 10,
invariant #10) : distinct du générateur officiel du §3.9, mais une
donnée déjà disponible et honnête plutôt qu'une case vide inutile.

### Périmètre du contenu "Par actif"

La colonne "statut Go/No-Go" du §4.6 affiche toujours "N/A (démo)" :
`go_nogo.py` n'est pas évalué par actif aujourd'hui (confirmé par
l'audit du 19/08/2026 — voir son entrée pour le détail des 7 critères du
§4.9 non implémentés). Pas de simulation ici non plus.

368 tests passent, 100% sur `risk_engine`/`capital_manager`/`go_nogo`/
`validator`/`trend_strategy`/`circuit_breaker`/`metrics`.

---

## 2026-08-19 — Watchdog processus + investigation de la mort silencieuse de `trend_executor`

### Watchdog (`scripts/process_watchdog.py`)

Demande explicite d'Ismaël suite à la découverte que `trend_executor`
s'était arrêté sans laisser de trace (voir investigation ci-dessous).
Exigences : vérification périodique des 4 process critiques, alerte
immédiate (pas de redémarrage automatique — "mériterait sa propre
réflexion"), journalisation systématique.

**cron plutôt qu'un 5e process tmux dédié** : un watchdog qui tournerait
lui-même comme process long-lived aurait exactement le même point de
défaillance que ce qu'il surveille — un `trend_executor` bis. cron est
géré par le système d'init du VPS, indépendant de tout process
applicatif, invoqué toutes les 5 minutes (`*/5 * * * *`).

**Détection par `pgrep -f` sur la ligne de commande complète, pas par
présence d'une session tmux** : observé en production que les deux se
désynchronisent dans les deux sens —
- une session tmux créée sur un shell interactif (`executor_loop`,
  lancée à l'origine avec `tmux new-session` puis peuplée via
  `send-keys`) **survit** au crash du process Python qu'elle contenait
  (retour au prompt shell, session toujours listée par `tmux ls`) ;
- une session tmux "one-shot" (`trend_executor`, `control_bot`, lancées
  directement avec `tmux new-session -d -s NOM 'commande'` comme
  commande de pane) **disparaît entièrement** dès que cette commande se
  termine (`remain-on-exit` non activé), emportant avec elle tout
  scrollback qui aurait pu contenir une trace de l'erreur — c'est
  exactement ce qui s'est produit pour `trend_executor` (voir
  ci-dessous).

Seule `pgrep -f "python -m <module>"` (présence du process réel, pas de
son conteneur tmux) donne un signal fiable dans les deux cas.

**État persisté dans `system_state`** (même table que
`circuit_breaker_store.py`) : une alerte par transition vivant->mort
(pas de spam à chaque exécution cron tant que le process reste absent),
notification de reprise au retour vivant. 6 tests, ~91% de couverture
(cohérent avec le traitement des autres points d'entrée `main()`/boucles
non testés unitairement ailleurs dans le projet).

### Investigation — mort silencieuse de `trend_executor` (survenue entre 20/08/2026 18:02 et 18:53 UTC)

Constat : le process `python -m src.trend_executor` et sa session tmux
ont disparu entièrement, sans qu'aucune ligne d'erreur n'apparaisse dans
`logs/trend_executor.log` au-delà du message de démarrage — donc après
l'initialisation complète (session Capital.com, liste blanche, chargement
des enveloppes), puisque ce message n'est journalisé qu'après. Le
`while True` de `run_trend_loop` capture déjà toute exception via
`except Exception:` (jamais atteint ici, sinon "Erreur non gérée dans la
boucle Flux B" serait présent) — un arrêt de ce type ne peut provenir que
d'un signal reçu par le process (SIGTERM/SIGKILL, non interceptable sans
handler dédié, qu'aucun module de ce projet n'installe) ou d'un
`SystemExit`, jamais présent dans le code.

**Vérifié et écarté** :
- **OOM cgroup** : `/sys/fs/cgroup/user.slice/user-1001.slice/memory.events`
  montre `oom_kill 0` (compteur cumulatif jamais incrémenté) et
  `memory.peak` = 304 Mo, très loin du plafond effectif de la tranche
  utilisateur (`EffectiveMemoryMax` ≈ 1,9 Go, `free -h` confirmant un
  total système de 1,9 Gi) — la mémoire n'a jamais été sous pression
  significative.
- **cron** : `crontab -l` ne contient que la sauvegarde quotidienne
  (`0 3 * * *`), sans lien temporel avec l'incident (survenu en
  après-midi/soirée).
- **fail2ban** : actif mais scope limité à SSH, sans mécanisme pouvant
  toucher un process local applicatif.
- **Reboot du VPS** : `uptime` continue sans interruption sur les jours
  concernés — pas de redémarrage.

**Non vérifiable, accès insuffisant** : les journaux noyau
(`dmesg`, `journalctl -k`) nécessitent un accès root ; `sudo` exige une
authentification interactive par mot de passe, indisponible depuis une
commande SSH non-interactive de ce niveau d'accès. Sans ces journaux,
impossible de confirmer ou d'exclure un signal envoyé manuellement
(commande `kill` depuis une session SSH séparée d'Ismaël, ou tout autre
mécanisme root) — **question restée ouverte, à poser directement à
Ismaël** : a-t-il, via une session SSH distincte, exécuté une commande
qui aurait pu toucher ce process pendant cette fenêtre ?

**Conclusion honnête** : cause non identifiée avec certitude. Le
mécanisme le plus probable, sur la base de ce qui a été écarté, est un
signal externe (pas une exception applicative, pas une pression mémoire,
pas un redémarrage système) — sans pouvoir en identifier l'émetteur avec
les accès actuels. Le watchdog ci-dessus ne résout pas la cause mais
réduit fortement le risque qu'une récidive passe inaperçue au-delà de
5-10 minutes. Si l'incident se reproduit, vérifier en priorité
`memory.peak`/`oom_kill` (déjà écartés cette fois, à recontrôler) et
demander l'accès aux journaux système (root) avant de conclure à
nouveau.

**Réponse d'Ismaël (19/08/2026)** : aucune commande lancée directement
sur le VPS via SSH pendant la fenêtre concernée (18:02-18:53 UTC) —
uniquement des prompts envoyés à Claude Code. L'hypothèse d'une
intervention manuelle de sa part est donc écartée à son tour. Cause
définitive toujours non identifiée (signal externe le plus probable,
émetteur inconnu, accès root non disponible pour aller plus loin).
**Point clos pour l'instant** — pas d'investigation supplémentaire
(accès root/journalctl) tant que ce n'est pas récurrent : le watchdog
(`scripts/process_watchdog.py`, actif depuis ce jour) donne désormais une
alerte immédiate si ça se reproduit, plutôt qu'une découverte après
coup. Si récidive : rouvrir cette entrée plutôt qu'en créer une nouvelle.

---

## 2026-08-20 — Palier P2.6 : coupe-circuits (§2.7) + bot de contrôle minimal (§7.1)

Priorité fixée par Ismaël après l'audit de conformité du 19/08/2026 :
combler les deux manques les plus consequents identifiés (aucun frein
automatique sur un actif qui perd, aucun levier manuel à distance) avant
dashboard/metrics.

### Architecture

- **`src/circuit_breaker.py`** (module critique, 100% couvert) : logique
  pure des seuils R (§2.7 : -2R jour, -5R semaine, -12R depuis le plus
  haut) + plafond d'exposition simultanée (§2.3, 10%) + surcouche
  anomalie système (3 erreurs API, ≥5 actifs, inactivité canal). Aucun
  I/O, même séparation que `risk_engine.py`.
- **`src/circuit_breaker_store.py`** (I/O, ~99% couvert — même niveau
  d'exigence qu'`envelope_store.py`, pas les 100% de la logique pure) :
  persistance des déclenchements (`circuit_breaker_events`), lecture de
  l'historique de trades, notifications, commandes /pause /reprendre
  /stop_urgence.
- **`src/control_bot.py`** (I/O, ~74% couvert — boucle de long-polling
  Telegram non unitairement testée, même traitement que
  `run_executor_loop`/`run_listener`) : 4e process autonome sur le VPS.

### Coupe-circuits scopés (actif, source), pas juste (actif)

Le §2.7 littéral ne mentionne pas "source" (notion introduite après coup
par le Flux B, palier P2.5, postérieur à la rédaction du §2.7). Sans ce
scoping, une série de pertes du Flux B sur EURUSD bloquerait à tort
Station X sur le même actif (et inversement) — incohérent avec la
séparation déjà appliquée aux enveloppes (`envelopes.source`). Les
commandes `/pause`/`/reprendre` du bot, elles, restent scopées au seul
actif (les deux sources à la fois) : le §7.1 ne distingue pas la source,
et Ismaël n'a pas à connaître cette distinction interne pour piloter le
système en urgence.

### Sémantique de "reprise manuelle" — journal d'événements, jamais un flag

`circuit_breaker_events` est un journal, pas un simple booléen : un
déclenchement "reprise manuelle" (week_r, drawdown_r, manual_pause,
stop_urgence, api_errors, breadth) reste actif tant qu'aucune ligne
`cleared_at` n'existe pour lui, **quel que soit** ce qu'un recalcul en
direct des R redonnerait ensuite (testé explicitement :
`test_is_asset_blocked_week_latched_ignores_live_recovery`) — sinon
"reprise manuelle" du §2.7 n'aurait aucun sens, la semaine suivante
lèverait le blocage toute seule. Seul `day_r` s'éteint sans action
(changement de date UTC), cohérent avec "Reprise Auto le lendemain".

### Bug réel trouvé par les tests : horodatage d'un déclenchement divergent du `now` évalué

`record_trigger()` et `_maybe_trigger_breadth_pause()` utilisaient
`datetime.now()` (horloge réelle) pour horodater un événement, alors que
la décision qui vient de le produire (`evaluate_circuit_breakers`) avait
été prise avec un `now` explicite passé par l'appelant. En production les
deux coïncident presque toujours (le `now` réel), mais le test qui
vérifie la déduplication "déjà déclenché aujourd'hui, pas de second
appel API" l'a immédiatement révélé (le timestamp stocké ne tombait pas
sur le même jour que le `now` de test, provoquant un redéclenchement à
chaque appel). Corrigé : ces deux fonctions acceptent maintenant un
`now`/`triggered_at` explicite, propagé depuis `is_asset_blocked`, jamais
recalculé en interne. Symptomatique d'un principe à garder : toute
fonction qui doit répondre "est-ce aujourd'hui ?" reçoit son `now` en
paramètre, elle ne l'interroge jamais elle-même.

### Plafond d'exposition simultanée (§2.3, pas §2.7) implémenté ici quand même

Demande explicite d'Ismaël de le grouper avec les coupe-circuits — noté
pour la traçabilité : structurellement c'est un concept du §2.3 (Gestion
du capital), pas du §2.7 (Coupe-circuits), mais l'implémentation
(`evaluate_exposure_cap` dans `circuit_breaker.py`) suit exactement la
même logique de garde-fou avant ouverture, donc reste dans le même
module plutôt que dans `capital_manager.py` (qui reste volontairement
pur/en mémoire, voir l'entrée P2 sur `envelope_store.py`). Approximation
assumée : l'exposition ouverte utilise `risque_eur` budgété à
l'ouverture, jamais réduit après une clôture partielle (TP1/TP2) —
surestime légèrement plutôt que sous-estimer, cohérent avec le parti
pris fail-safe du reste du projet.

### Surcouche anomalie système — interprétations assumées

- **"3 erreurs API consécutives"** : plutôt que d'instrumenter chaque
  site d'appel réseau existant (nombreux, risque de régression sur du
  code déjà critique et testé), une sonde de connectivité légère
  (`client.get_account_balance()`) est appelée une fois par cycle de
  boucle ; le compteur est persisté (`system_state`, survit à un
  redémarrage) plutôt qu'en mémoire. Détecte une vraie coupure API/auth,
  pas chaque échec ponctuel d'un appel métier individuel — écart assumé
  pour limiter le risque d'implémentation, documenté ici plutôt que
  silencieux.
- **"Pause générale"** (api_errors, breadth) : interprétée comme un
  blocage des NOUVELLES entrées uniquement, jamais un arrêt de la gestion
  des positions déjà ouvertes — cohérent avec le principe fail-safe déjà
  appliqué ailleurs dans le projet ("abandonner la surveillance d'une
  position ouverte serait plus dangereux que de patienter", voir
  `executor.py`). Le CDC ne tranche pas explicitement ce point.
- **Inactivité du canal (7 jours)** : exclut explicitement les
  `raw_messages` synthétiques du Flux B (`channel='trend_strategy'`) —
  sinon l'activité de `trend_executor.py` masquerait une vraie coupure
  du canal Telegram Station X, qui est le seul concerné par cette alerte
  (testé : `test_check_channel_inactivity_ignores_trend_strategy_synthetic_messages`).

### `/stop_urgence` — seule dérogation à "ordres limite uniquement, jamais au marché" (§2.8)

`force_close_all_open_trades()` (dans `executor.py`, réutilisé par
`trend_executor.py`) ferme au prix courant, pas via un ordre limite —
`/stop_urgence` est une action de sécurité manuelle explicitement
déclenchée par Ismaël, pas l'exécution d'un signal ; le §2.8 encadre
l'exécution des signaux, pas les actions d'urgence humaines. Réutilise
`_apply_management_action` (même code que toute clôture SL/TP —
enveloppe, réserve, journalisation, analyse post-trade), seule la
construction de l'action diffère.

Latence assumée : le bot de contrôle n'ouvre jamais de session broker
(séparation contrôle/exécution, invariant #1 même hors LLM) — il écrit
uniquement un événement ; ce sont `executor.run_executor_loop` et
`trend_executor.run_trend_loop`, qui possèdent déjà leur propre
`CapitalClient` authentifié, qui ferment réellement les positions à leur
cycle suivant (jusqu'à ~60s, l'intervalle de `trend_executor`). Chaque
process ne ferme que ses propres trades (source), et ne traite un même
événement `stop_urgence` qu'une seule fois (`system_state`, clé
`stop_urgence_handled:<process>`) — sinon il retenterait de fermer/annuler
des positions/ordres déjà clos à chaque itération tant que
`/reprendre` n'est pas envoyé.

### `control_bot.py` — portée réduite, authentification par chat_id

Seules `/etat`, `/pause [actif]`, `/reprendre [actif]`, `/stop_urgence`
sont implémentées (demande explicite d'Ismaël pour ce premier lot) — les
10 autres commandes du §7.1 dépendent de modules encore absents
(`dashboard`, `confidence_scorer`, `allocator`, `hypothesis_engine`
officiel, `metrics`), confirmé par l'audit du 19/08/2026. Même bot
Telegram que `audit_notifier.py` (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`),
pas le compte d'écoute Station X (Telethon) — deux mécanismes distincts.
Authentification de l'expéditeur : pas de nouvelle variable
d'environnement — dans une conversation privée Telegram,
`message.chat.id == message.from.id`, donc `TELEGRAM_CHAT_ID` (déjà
utilisé pour recevoir les notifications) sert aussi de liste blanche
d'expéditeur autorisé ; tout autre `chat_id` est journalisé et ignoré,
jamais exécuté (testé :
`test_process_update_unauthorized_chat_ignored`).

---

## 2026-08-19 — Bug réel : migration `envelopes.source` échouait sur la vraie base VPS (FK)

**Constat** : en déployant P2.5 (Flux B) sur le VPS, `init_db()` a levé
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` sur
`DROP TABLE envelopes`, jamais reproduit localement ni sur les 250 tests
qui passaient pourtant tous, y compris un test dédié à cette migration
(`test_init_db_migrates_envelopes_source_preserving_data_and_ids`).

**Cause** : `envelope_ledger.envelope_id` porte une contrainte
`REFERENCES envelopes(id)` (§4.5) et `get_connection()` active
`PRAGMA foreign_keys = ON` sur toute connexion. SQLite refuse de `DROP`
une table encore référencée comme parent par une clé étrangère tant que
l'enforcement est actif — la vraie base VPS a `envelope_ledger` peuplée
(9 lignes, dont une pour l'enveloppe BTCUSD réellement tradée). Le test
dédié à cette migration recréait une table `envelope_ledger` factice
**sans** la clause `REFERENCES`, donc sans contrainte réelle à enfreindre
— écart silencieux entre le fixture de test et le schéma réel qui a
laissé passer le bug jusqu'au déploiement.

**Conséquence observée sur le VPS** : `CREATE TABLE envelopes_new` et
l'`INSERT` (DDL/DML avant l'échec) ont bien été appliqués, mais
`DROP TABLE envelopes` a levé une exception avant tout `commit()` —
la table `envelopes_new` restante était vide (l'`INSERT`, en transaction
implicite, a été annulé à la fermeture de connexion sans commit) tandis
que `envelopes` d'origine était intacte (vérifié : 8 enveloppes, soldes
corrects, BTCUSD à 499.66€). **Aucune perte de donnée** — sauvegarde
prise avant coup par précaution (`data/backups/assistant_trading_20260819T175913Z.db`)
puis confirmée inutile.

**Correctif** : `_migrate_envelopes_source()` désactive
`PRAGMA foreign_keys = OFF` avant la reconstruction de table, commit
explicitement, puis réactive le pragma — hors de toute transaction
implicite (le pragma est un no-op au milieu d'une transaction ouverte).
Table `envelopes_new` résiduelle nettoyée manuellement sur le VPS avant
de relancer la migration corrigée. Test de régression corrigé pour
inclure la vraie clause `REFERENCES` sur `envelope_ledger`, reproduisant
fidèlement l'échec avant le correctif.

**Leçon retenue** : pour toute migration de schéma testée avec une table
de fixture recréée à la main (plutôt qu'issue du vrai `SCHEMA`), vérifier
que les contraintes (FK, UNIQUE, NOT NULL) sont répliquées à l'identique
— sinon le test peut passer sur un schéma qui ne reflète pas les
contraintes réelles.

---

## 2026-08-16 — `parser.py` déterministe plutôt que LLM (§4.4, §3.3, §4.1)

**CDC littéral** : §4.4 classe `parser` comme "Texte → JSON via LLM
(zones)", seule zone non déterministe de tout le pipeline (§4.1 : "EXTRACTION
(LLM — seule zone non déterministe)"). Justification du CDC : le canal
raisonne en zones ICT (FVG, retracements Fibonacci 50/61,8/78,6%, §3.3),
donc "les niveaux sont des zones, pas des points. L'extraction produit des
intervalles."

**Constat empirique** : sur les exemples réels du canal fournis par
Ismaël (Matinale, Signal en deux messages, Suivi), le signal exécutable
final est **un prix unique** ("JE VENDS XAUUSD à 4367"), pas une zone. Les
zones ICT n'apparaissent que dans le raisonnement narratif de la Matinale,
jamais dans le prix d'entrée du signal structuré. Ismaël a lui-même
corrigé sa spécification initiale sur ce point avant de fournir les
exemples.

**Décision** : `extract_signal()` et `extract_suivi()` sont 100%
déterministes (regex), sans aucun appel LLM. `extract_matinale()` (biais
du corps / tag Sentiment / contradiction §3.4) l'est également, via un
motif textuel explicite (`"reste donc <mot>"`) plutôt qu'un LLM — voir
entrée dédiée ci-dessous.

**Alternative écartée** : LLM à température 0, schéma strict, avec les
mitigations du §3.5/§3.6 (score de confiance auto-déclaré, contrôle
croisé déterministe en aval, audit manuel intégral 3 semaines).

**Justification (fiabilité / coût / latence / auditabilité /
maintenabilité)** :
- **Fiabilité** : un prix de signal alimente potentiellement
  `risk_engine.TradeSignal.entry_price`/`stop_price`/`take_profits` — le
  cœur qui dimensionne les positions. Une extraction 100% déterministe
  élimine tout risque d'hallucination sur ces chiffres, ce qui satisfait
  l'invariant §4.2.1 ("Aucun LLM n'a accès... au moteur de risque") de
  façon plus stricte que le design CDC original, qui laissait quand même
  un LLM produire les chiffres (validés en aval, mais produits par le LLM).
- **Coût** : zéro appel API par message signal/suivi (le canal publie des
  dizaines de messages/jour selon §3.1).
- **Latence** : zéro round-trip réseau — pertinent vu la fenêtre de
  péremption du signal (§2.8, 10-60s de latence structurelle déjà serrée).
- **Auditabilité** : un motif regex est lisible, versionné, testable
  unitairement sur des cas réels et reproductible bit à bit — un
  comportement LLM ne l'est pas, même à température 0.
- **Maintenabilité** : pas de dépendance à un fournisseur/modèle pour la
  fonction la plus critique du pipeline d'ingestion.
- **Sécurité** : immunité structurelle à l'injection de prompt (§3.5) —
  il n'y a pas de prompt à injecter dans un moteur regex.

**Risque résiduel et mitigation** : un motif regex est plus rigide qu'un
LLM face à une formulation inhabituelle du canal. Mitigation : tout signal
qui ne correspond pas au motif attendu obtient `extraction_status =
"incomplete"` (jamais une valeur devinée), `statut = "rejete"` en base, et
génère quand même une notification d'audit (§3.6) — donc jamais un échec
silencieux. Si la variété réelle des formulations dépasse ce que 2
exemples permettent de couvrir, un LLM de repli *uniquement pour
signaler/résumer les cas non reconnus à l'audit manuel* (jamais pour
produire des chiffres exploités par `risk_engine`) reste une extension
possible sans rien remettre en cause de ce qui précède. Pas construit
maintenant : aucun cas réel rencontré à ce jour qui le justifie (YAGNI).

**Validé sur** : exemples réels Matinale, Signal (message d'alerte +
message structuré), Suivi fournis par Ismaël le 16/08/2026. 24 tests
(`tests/test_message_classifier.py`, `tests/test_parser.py`) + 8
(`tests/test_telegram_listener.py`).

---

## 2026-08-16 — Détection du biais Matinale par motif textuel, pas par LLM

Sous-décision de l'entrée précédente, isolée ici car le raisonnement est
spécifique. §3.4 exige de "conserver le biais du corps de l'analyse" et
"notifier l'incohérence" avec le tag Sentiment — le CDC ne précise pas
*comment* déterminer ce biais.

**Décision** : recherche du motif `"Le {actif} reste donc <mot>"` (la
phrase de conclusion de chaque paragraphe d'actif dans les deux exemples
réels), classification du mot capturé via un petit lexique
(haussier/baissier/neutre/indéterminé). Si le motif ne matche pas →
`biais_corps = "indetermine"`, jamais une supposition.

**Alternative écartée** : décompte de mots-clés positifs/négatifs sur tout
le paragraphe — testé mentalement sur l'exemple Gold réel, donne le
**mauvais** résultat (le paragraphe contient plus de mots à connotation
négative que positive alors que sa phrase de conclusion est "reste donc
solide", explicitement constructive). Le motif de conclusion est plus
fidèle à ce que le canal affirme réellement.

**Point ouvert, assumé** : validé sur seulement 2 exemples réels
(Bitcoin, Gold). Si d'autres Matinales n'utilisent pas la formule "reste
donc", le résultat sera `"indetermine"` (jamais une fausse confiance) —
à élargir au fil des messages captés en production, pas d'anticipation
nécessaire (règle anti-surapprentissage §3.8 : ne pas ajuster sur des cas
qu'on n'a pas encore vus).

---

## 2026-08-16 — Schéma DB : deux tables hors §4.5, deux tables P0 conservées

**Constat** : `docs/CDC_v4.md` §4.5 ne prévoit aucune table pour (a) le
résumé par actif d'une Matinale (biais du corps, tag Sentiment,
contradiction) alors que §3.8 variable #1 et §3.4 en dépendent
directement, et (b) les événements de suivi (§3.2) tant qu'aucun trade
réel n'existe pour les rattacher à `trade_partials` (P2+).

**Décision** : ajout de `matinale_summaries` et `suivi_events` (voir
`src/db.py`), non prévues par le §4.5 mais sans aucun conflit avec les
tables qu'il définit. Conservation de `risk_decisions` et `go_nogo_events`
(palier P0, absentes du §4.5) pour l'audit — invariant §4.2.5 : "Tout
ordre est journalisé avant envoi et après confirmation", et plus
généralement traçabilité des rejets de risque.

**Justification** : le reste du schéma §4.5 (trades, envelopes,
confidence_scores, hypotheses, etc.) est repris **littéralement**, sans
modification, y compris pour des tables qu'aucun module actuel
n'utilise encore (P2+) — le DDL est bon marché et déclaratif, le
reconstruire plus tard coûterait plus cher que le construire une fois,
conforme au CDC, dès l'étape 0 de réconciliation.

**`signals.entree_min = entree_max`** : le CDC prévoit une zone
(`entree_min`, `entree_max`) pour rester cohérent avec §3.3. Le signal
final observé étant un prix unique (voir entrée ci-dessus), les deux
colonnes reçoivent la même valeur plutôt que de modifier le schéma —
compatible avec le CDC littéral, fidèle à la donnée réelle, et
reste ouvert si un futur signal publie effectivement une zone.

**`signals.confiance` déterministe (1.0/0.0), pas un score LLM
auto-déclaré (§3.6)** : conséquence directe du choix parser déterministe.
`1.0` si asset + direction + prix + stop sont tous résolus, `0.0` sinon —
ne pas confondre avec la table `confidence_scores` (§2.4), qui est un
score statistique **par actif**, calculé plus tard sur l'historique de
trades (P2+), et qui reste totalement séparée et non affectée par ce
choix.

---

## 2026-08-16 — `audit_notifier.py` : portée réduite vs `control_bot` (§4.4)

**CDC littéral** : `control_bot` est un module unique gérant l'ensemble
des commandes du §7.1 (`/etat`, `/dashboard`, `/pause`, `/stop_urgence`,
`/classement`, etc.) et les notifications du §7.2.

**Décision** : à ce palier (P1 — "ingestion, classification, extraction,
audit manuel des extractions", §4.8), seul un sous-ensemble minimal est
construit : `send_notification()` (émission uniquement, aucune réception
de commande). Les commandes du §7.1 dépendent de modules qui n'existent
pas encore (`executor`, `metrics`, `dashboard`, `allocator`) — les
construire maintenant serait de l'anticipation sans besoin réel (YAGNI),
contraire au principe "ne pas concevoir pour des besoins hypothétiques".

**Justification** : §3.6 exige uniquement un canal de notification pour
l'audit manuel intégral des 3 premières semaines et §7.2 une liste de
notifications automatiques — aucune des deux n'exige de recevoir des
commandes. `control_bot` sera complété incrémentalement à mesure que les
modules qu'il pilote existent (P2+).

**Implémentation** : API HTTP `sendMessage` du bot Telegram via `urllib`
(bibliothèque standard), pas `requests` ni Telethon — un bot n'a pas
besoin d'une session utilisateur, et un simple POST JSON ne justifie pas
une dépendance supplémentaire. Ne lève jamais d'exception (échec de
notification ≠ échec d'ingestion).

**Fenêtre d'audit intégral (§3.6, 3 semaines)** : implémentée comme un
paramètre `audit_all: bool = True` sur `process_message()`, plutôt qu'un
mécanisme de comptage de dates. Simple à désactiver plus tard (un
paramètre), pas de logique de calendrier à maintenir pour un besoin
ponctuel. Les contradictions Matinale (§3.4) restent notifiées même si
`audit_all=False` — elles figurent séparément dans la liste permanente du
§7.2.

---

## 2026-08-16 — Gestion d'erreur de `telegram_listener` : par message, pas globale

**Contexte** : invariant §4.2.9 (fail-safe) — "toute erreur non gérée
arrête les entrées, ne les poursuit pas." Ce principe s'applique
explicitement aux **entrées** (ordres), pas à la capture brute.

**Décision** : dans `run_listener()`, une exception lors du traitement
d'un message est journalisée (`logger.exception`) et n'interrompt pas
l'écoute — le message suivant est traité normalement.

**Justification** : en P1, aucun executor n'est branché ; aucune décision
d'ordre ne dépend de ce module. Un crash du listener sur un message
malformé ferait perdre la capture de tous les messages suivants jusqu'au
redémarrage — pire que l'inverse. Ce raisonnement devra être réévalué
quand `executor` existera et pourra recevoir des signaux en aval de ce
pipeline : à ce moment-là, le fail-safe strict de l'invariant #9
s'appliquera au bon niveau (validation/risk_engine), pas à l'ingestion.

---

## 2026-08-16 — Boucle asyncio explicite dans `run_listener` (compat Python 3.14)

**Constat** : premier lancement réel sur le VPS →
`RuntimeError: There is no current event loop in thread 'MainThread'`.
Python 3.14 a supprimé la création implicite d'une boucle asyncio par
`asyncio.get_event_loop()` (dépréciée depuis 3.10, retirée en 3.14).
Telethon 1.36.0 (dernière version publiée testée) accède à `self.loop` de
façon synchrone **dès la construction de `TelegramClient`**, pas
seulement à `.start()` — comportement hérité d'avant cette suppression.

**Décision** : créer et définir explicitement une boucle asyncio
(`asyncio.new_event_loop()` + `asyncio.set_event_loop(loop)`) avant toute
utilisation de Telethon, et la passer explicitement au constructeur
(`TelegramClient(..., loop=loop)`) plutôt que de dépendre de l'état
global implicite. Reproduit et corrigé localement (Python 3.14.7) avant
redéploiement sur le VPS (Python 3.14.4), sans mock du réseau réel — seuls
`client.start()`/`client.run_until_disconnected()` sont mockés pour le
test de non-régression, l'authentification réelle restant un test manuel
d'Ismaël (déjà hors périmètre de l'automatisable, voir entrée listener
ci-dessus).

**Alternative écartée** : épingler une version de Telethon antérieure
compatible avec l'ancien comportement d'asyncio. Écartée car cela
signifierait dépendre indéfiniment d'un comportement Python explicitement
retiré (dette technique croissante), alors que la boucle explicite est la
correction recommandée par la documentation de migration asyncio
elle-même et fonctionne avec la version de Telethon déjà choisie.

---

## 2026-08-16 — Résolution du canal Station X par id numérique, pas par `@username`

**Constat** : premier test réel du backfill → `UsernameInvalidError` en
tentant de résoudre `@station_x`. Diagnostic (lecture des canaux de
diffusion du compte via la session déjà authentifiée, jamais les groupes
ni les conversations privées — voir garde-fou dans le code du
diagnostic) : Station X est un **canal privé rejoint par lien
d'invitation**, sans `@username` public résolvable — cohérent avec le
pivot Telegram déjà documenté (lien d'invitation irrécupérable pour un
nouveau compte). `TELEGRAM_CHANNEL=@station_x` dans `.env` était un
placeholder jamais remplacé par le véritable identifiant, pas une valeur
vérifiée.

**Décision** : `TELEGRAM_CHANNEL` passe à l'id numérique du canal
(`-1002481537588`, obtenu via `iter_dialogs()`), en local et sur le VPS.
`telegram_listener.py` accepte désormais les deux formats (id numérique
ou `@username`) — tente `int(...)`, retombe sur la chaîne sinon — pour
rester robuste si Station X gagnait un jour un username public, ou pour
tout autre canal source ajouté plus tard (§0.3 : sources interchangeables
par design).

**Risque déjà présent, révélé ici** : le listener "en direct" tournait
depuis le tour précédent avec la même valeur cassée — il n'a
vraisemblablement jamais reçu un seul événement du bon canal (silence
total, aucune exception visible, car `events.NewMessage(chats=...)`
échoue différemment de `iter_messages()` face à un identifiant
invalide). Le zéro-message constaté n'était donc pas dû au calme
dominical, mais à ce bug. Corrigé et redéployé avant tout nouveau test.

---

## 2026-08-16 — Deux bugs réels trouvés sur données de production (backfill)

Pas des écarts au CDC, mais journalisés ici car découverts pendant la
validation en conditions réelles et directement liés à la fiabilité de
l'extraction déterministe (voir entrée principale ci-dessus).

**Bug 1 — séparateur de milliers "espace" non géré.** Le canal formate
les prix avec un espace (`"91 950"`), jamais rencontré dans les 2
exemples fournis initialement (tous en dessous de 10 000, sans
séparateur). Les regex de `_STRUCTURED_SIGNAL`/`_TP_LINE`/`_SL_LINE`
capturaient seulement les chiffres avant le premier espace (`"91 950"` ->
`91.0`) — un prix silencieusement faux plutôt qu'un échec explicite,
le pire des deux cas. Détecté en inspectant un signal BTCUSD réel
extrait avec `statut="a_valider"` mais des valeurs manifestement absurdes
(stop à 92€ sur un prix d'entrée à 91€). Corrigé par un motif de nombre
partagé (`_NUMBER`) gérant les groupes de 3 chiffres séparés par espace
normal ou insécable.

**Bug 2 — tickers d'indices avec chiffres non capturés.** La classe de
caractères de l'actif dans `_STRUCTURED_SIGNAL` (`[A-Za-zÀ-ÿ/]`)
excluait les chiffres : `NAS100`/`US100`/`US30` ne pouvaient jamais
matcher entièrement, faisant échouer tout le regex structuré en silence
(retombée sur le chemin "alerte incomplète" même pour un message
parfaitement structuré). Détecté en retrouvant, parmi les signaux
`statut="rejete"` du backfill, deux messages NAS100 pourtant complets
(prix, TP1-3, SL). Corrigé en ajoutant `0-9` à la classe de caractères.

**Pourquoi ces deux bugs n'ont pas été vus avant** : les 2 exemples
fournis initialement ne couvraient ni un prix à 5 chiffres avec
séparateur, ni un ticker contenant un chiffre — exactement le
raisonnement anti-surapprentissage du CDC (§3.8) appliqué à du code de
parsing plutôt qu'à des variables statistiques : un motif validé sur un
petit échantillon peut échouer silencieusement sur un cas non couvert.
Le backfill de 50 messages réels (demandé par Ismaël pour valider avant
de faire confiance au flux automatique) a rempli exactement ce rôle.
Base de données réinitialisée après correction — aucune donnée capturée
avant ce correctif n'a de valeur (chiffres faux ou rejets erronés).

**Point noté, non corrigé (hors périmètre demandé)** : un message réel
`"Mettez votre SL BE ✅"` (instruction de resserrer le stop au
breakeven, §2.10) est classé `"autre"` faute de motif dédié dans
`message_classifier` — il ne rapporte ni un TP/SL touché ni un résultat
en pips, les seuls motifs de `"suivi"` actuellement couverts. Sans
impact en P1 (aucune action déclenchée sur `"suivi"` de toute façon,
pur archivage), mais à couvrir si `executor` (P2+) doit un jour agir sur
ce type d'instruction.

---

## 2026-08-16 — Palier P2 : architecture executor/validator/market_data

Autonomie déléguée exercée sur l'ensemble de cette section — aucune de
ces décisions n'a été validée au préalable, conformément au mandat
d'Ismaël. Regroupées ici pour éviter une dizaine d'entrées séparées sur
une seule session de travail.

**Écart de phasage assumé, pas choisi** : le §4.8 du CDC prévoit pour P2
une "exécution démo avec validation manuelle (rodage)" — l'autonomie
complète sans validation par trade est explicitement demandée par
Ismaël pour ce palier, pas une décision prise seul. Notée ici pour la
traçabilité, conformément à la règle du mandat ("documenter chaque
écart notable au CDC littéral").

**`src/capital_client.py`** (nouveau) : factorise la logique Capital.com
déjà dupliquée trois fois (session, GET/POST/DELETE/PUT, le piège
`affectedDeals[0].dealId`) dans les scripts P0. `requests` passe de
dépendance de fait (non déclarée) à dépendance déclarée dans
`requirements.txt` — utilisée par 3 scripts existants sans jamais avoir
été ajoutée officiellement.

**Ordres limite, jamais au marché (§2.8)** : `place_limit_order()` via
`POST /workingorders`, vérifié en direct sur le compte démo (ordre placé,
visible dans `/workingorders`, annulé proprement) avant toute
implémentation dans `executor.py` — `open_position()` (marché) reste
disponible mais réservée aux scripts de calibration ponctuels, jamais
utilisée par l'exécuteur.

**Fenêtre de péremption d'ordre limite = 15 minutes**
(`LIMIT_ORDER_EXPIRY_SECONDS`) : le CDC ne fixe pas de chiffre pour la
durée de vie d'un ordre limite non exécuté. Choix documenté : au-delà
de la latence structurelle (10-60s, §2.8), un ordre qui traîne des
heures n'a plus de rapport avec le signal qui l'a généré — 15 minutes
laisse une marge réaliste pour qu'un prix atteigne une zone d'entrée
proche sans laisser un engagement fantôme indéfiniment. Ajustable sans
migration (constante).

**Tolérance de péremption de signal = 50% de la distance de stop**
(`validator.STALENESS_FRACTION_OF_STOP_DISTANCE`) : le CDC fixe le
principe (§2.8) sans valeur chiffrée. Choix documenté dans le docstring
de `validator.py` : si le marché a déjà parcouru la moitié du risque
prévu avant l'entrée, le rapport gain/risque planifié n'est plus celui
du signal d'origine.

**R-multiple dans `risk_engine.py`, pas `executor.py` ni
`trade_analyzer.py`** : `compute_r_multiple`/`compute_weighted_r_multiple`
ajoutées au module déjà critique et 100% couvert plutôt qu'à un nouveau
module — un seul endroit pour tout calcul financier dont un bug se
traduit directement en perte (§4.7), pas de duplication de la formule
entre l'ouverture/gestion de position et l'analyse post-trade (qui *lit*
`trades.r_multiple_total` déjà calculé, ne le recalcule jamais).

**Réserve globale (§2.3) : pas un `CapitalManager`** : `apply_trade_result()`
ajoutée à `capital_manager.py` (fonction, pas méthode — une instance de
`CapitalManager` représente une seule enveloppe, jamais la réserve
globale partagée entre tous les actifs). La réserve elle-même n'est pas
un objet `CapitalManager` : son solde est un simple total, persisté via
`reserve_ledger.reserve_totale` (`envelope_store.py`) — réutiliser
`CapitalManager` échouait dès la construction (`initial_balance > 0`
exigé, alors qu'une réserve démarre légitimement à 0€).

**`trades.deal_id` ajouté hors §4.5** : le schéma CDC des `trades` ne
prévoit aucune colonne pour l'identifiant de position côté broker —
`executor.py` ne peut piloter (clôturer, resserrer le stop) une position
ouverte sans lui. Oubli du schéma d'origine, pas un choix délibéré du
CDC ; corrigé.

**TP1/TP2 lus depuis `signals`, leur statut "touché" dérivé de
`trade_partials`** : pas de colonnes dupliquées sur `trades` — l'état
"quel palier est déjà passé" est entièrement reconstructible depuis
l'historique déjà écrit (`trade_partials`), évite une source de vérité
parallèle qui pourrait diverger.

**Détection de remplissage d'ordre limite (`check_pending_fills`)** :
suppose que Capital.com conserve le même `dealId` entre l'ordre limite
et la position qui en résulte — comportement observé lors des tests
manuels de ce palier, **non revalidé sur un remplissage réel** (les
tests ont porté sur le placement/l'annulation d'ordres qui ne se
déclenchent jamais, pas sur un déclenchement effectif — voir le rapport
de fin de tâche). Point explicite à confirmer avant de faire confiance
à la boucle sans supervision prolongée.

**Bug réel trouvé pendant les tests — verrou SQLite** :
`_apply_management_action` appelait `envelope_store.persist_trade_result`
(qui ouvre sa propre transaction) depuis l'intérieur d'un
`with connection_scope(...)` déjà ouvert → `sqlite3.OperationalError:
database is locked`. Corrigé en séparant strictement les transactions
séquentielles, jamais imbriquées. Aucune transaction imbriquée n'existe
plus ailleurs dans `executor.py` (vérifié).

**Couverture** : les fonctions de décision/calcul pures d'`executor.py`
(`decide_entry`, `compute_tp_allocations`, `evaluate_position_management`
et ses fonctions internes, `compute_trailing_stop_level`) sont à 100%
(demande explicite d'Ismaël). L'orchestration I/O (`open_signal`,
`manage_open_trades`, `check_pending_fills`, `cancel_stale_working_orders`)
est à 92% de couverture globale du fichier — chemins d'erreur réseau et
cas de repli non exhaustivement testés, cohérent avec le traitement déjà
appliqué à `telegram_listener.run_listener()` au palier P1.

---

## 2026-08-16 — `go_nogo.py` non appelé dans la boucle démo

**Constat** : `risk_engine.evaluate_new_entry` prend `go_nogo_ok` comme
paramètre obligatoire et rejette toute entrée si `False`
(`GO_NOGO_LOCKED`). Appeler `go_nogo.evaluate_go_nogo()` dans
`run_executor_loop` (mode démo) bloquerait **systématiquement** toute
entrée, car sa condition `configured_environment == "live"` n'est
jamais vraie avec `CAPITAL_ENVIRONMENT=demo`.

**Décision** : `run_executor_loop` construit explicitement
`GoNoGoStatus(allowed=True, reason="mode démo — verrou réel non
applicable")` plutôt que d'appeler `evaluate_go_nogo()`. Justifié par le
diagramme d'architecture du CDC lui-même (§4.1) : le verrou Go/No-Go
n'est représenté que sur la branche "EXÉCUTION RÉELLE", jamais sur
"EXÉCUTION DÉMO continue" — les deux branches sont architecturalement
distinctes dès la conception du CDC, ce n'est pas une réinterprétation.

**Garde-fou structurel associé** : `run_executor_loop` utilise une
constante `_DEMO_BASE_URL` codée en dur, jamais dérivée de
`config.capital_environment` — ce module ne contient tout simplement
aucun chemin de code vers l'API réelle Capital.com, quelle que soit la
configuration. Le jour où un exécuteur réel sera construit (post Porte
B, §4.8), ce sera un module séparé avec son propre appel explicite à
`go_nogo.evaluate_go_nogo()`, pas une bascule de paramètre sur celui-ci.

---

## 2026-08-16 — Bug réel trouvé avant démarrage : migration de schéma manquante

**Constat** : avant de démarrer `run_executor_loop` sur le VPS (feu vert
d'Ismaël pour le test réel encadré), vérification de l'état de la base
existante — `trades` n'avait PAS la colonne `deal_id` ajoutée au palier
P2. `CREATE TABLE IF NOT EXISTS` (utilisé par `init_db()`) ne modifie
jamais une table déjà existante : la base du VPS avait été créée pendant
P1, avant l'ajout de `deal_id`. La première écriture
d'`executor.open_signal()` aurait échoué (`no such column: deal_id`) —
non détecté par les tests car ils créent systématiquement une base
neuve (`tmp_path`), jamais une base "ancienne" à migrer.

**Décision** : `init_db()` applique désormais une liste explicite de
migrations de colonnes (`_COLUMN_MIGRATIONS`) après la création des
tables, via `ALTER TABLE ... ADD COLUMN` conditionnel (vérifié par
`PRAGMA table_info`, jamais en double). Testé par simulation d'une
table `trades` pré-existante sans `deal_id`. Appliqué immédiatement sur
la base du VPS avant tout démarrage de la boucle.

**Portée** : ce mécanisme ne couvre que les migrations déjà connues au
moment où le code est écrit — toute future colonne ajoutée à une table
existante doit être déclarée dans `_COLUMN_MIGRATIONS`, pas seulement
dans `SCHEMA`.

---

## 2026-08-16 — Bug réel trouvé avant démarrage : stop garanti manquant dans `open_signal`

**Constat** : `CLAUDE.md` documentait déjà depuis le palier P0 que ce
compte démo exige un stop garanti (`guaranteedStop: true` +
`stopDistance`) pour les cryptos, avec la note "à vérifier si ça
s'applique aussi aux autres classes d'actifs". Lors des tests d'ordre
limite du palier P2 (voir entrée dédiée plus haut), la même exigence
s'est manifestée sur EURUSD — confirmant qu'elle n'est pas propre aux
cryptos. Or `executor.open_signal()` n'envoyait jamais `guaranteedStop`/
`stopDistance` à `place_limit_order()` : chaque ouverture aurait échoué
avec `error.vallidation.guaranteed-stop-loss.required`. Trouvé en
vérifiant l'état réel du compte juste avant le test encadré demandé par
Ismaël, pas par les tests automatisés (qui simulent l'API et n'avaient
jamais rencontré cette erreur réelle).

**Décision** : `_compute_guaranteed_stop_distance()` calcule la
distance à partir du stop déjà dimensionné par `risk_engine` (jamais
recalculé) et la compare au minimum imposé par `dealingRules` de
l'instrument. Si le stop budgété est **plus serré** que ce minimum,
retourne `None` et l'entrée est rejetée (`signals.statut = 'rejete'`)
plutôt que d'élargir silencieusement le stop — élargir aurait
directement augmenté le risque réel au-delà de ce que `risk_engine` a
calculé et approuvé, une violation de fait de l'invariant #2 (calcul
financier déterministe) même si le code semblait "juste s'adapter à une
contrainte du broker".

**Conséquence pratique** : certains signaux à stop très serré sur des
actifs à minimum de stop garanti élevé seront systématiquement rejetés
en l'état — un compromis assumé (sécurité du sizing) plutôt qu'un bug à
corriger dans l'autre sens.

---

## 2026-08-16 — Bug critique confirmé pendant le test réel encadré : dealId de la position ≠ dealId de l'ordre

**Constat** : lors du test réel encadré demandé par Ismaël (démarrage de
`run_executor_loop` sous supervision directe, un signal de test placé
manuellement sur BTCUSD proche du marché pour observer un remplissage
réel), l'ordre limite s'est bien exécuté — mais `check_pending_fills`
ne l'a jamais détecté. Vérification directe côté API
(`get_open_positions()`) : la position résultante a un `dealId` **différent**
de celui de l'ordre limite d'origine. Le seul lien entre les deux est
le champ `position.workingOrderId`, qui contient l'ancien `dealId` de
l'ordre. L'entrée `docs/DECISIONS.md` précédente sur ce point
("Capital.com conserve le même dealId") était une hypothèse non
vérifiée, explicitement signalée comme telle — elle s'est révélée
fausse à l'épreuve du réel, exactement le scénario que le test encadré
devait couvrir avant le passage en autonome.

**Décision** : `check_pending_fills` rapproche désormais les trades en
attente sur `position.workingOrderId` (pas `position.dealId`), et
**réécrit `trades.deal_id`** avec le nouveau `dealId` de la position dès
la détection du remplissage — indispensable, car toute gestion
ultérieure (`manage_open_trades`, clôtures, mise à jour de stop)
référence la position par ce `deal_id` stocké. Sans cette réécriture,
la position serait restée gérable en apparence mais toute tentative
de clôture/mise à jour aurait échoué contre un `dealId` inexistant.

**Position réelle concernée** : un trade BTCUSD long a été ouvert sur le
compte démo pendant ce test (taille ~0,029 BTC, stop garanti à 400
points). Corrigé et repris en gestion normale après ce fix — aucune
perte de suivi, la position était toujours réellement ouverte côté
broker pendant toute la durée du bug, seul le suivi côté base de
données était en retard.

**Point mineur, sans conséquence** : pendant le diagnostic, une
suppression manuelle inutile de 8 anciens signaux déjà `rejete` a
laissé des références orphelines dans `risk_decisions.signal_id` (via
la CLI sqlite3, hors contrainte de clé étrangère de l'application) —
aucune donnée de valeur perdue, juste un lien cassé sur des lignes déjà
terminales.

---

## 2026-08-16 — Bug réel trouvé pendant le test encadré : `trade_analyzer` jamais appelé

**Constat** : après avoir corrigé la détection de remplissage et laissé
le trade BTCUSD réel se clôturer (via un stop resserré manuellement en
base pour déclencher un vrai cycle de clôture sans attendre une
fermeture naturelle), `trade_analysis` restait vide. `trade_analyzer.py`
avait été entièrement construit et testé au tour précédent
(19 tests, garde-fou de sortie validé), mais **jamais appelé depuis
`executor.py`** — un oubli d'intégration, pas un défaut du module
lui-même.

**Décision** : `_apply_management_action` appelle
`trade_analyzer.analyze_closed_trade()` juste après avoir journalisé la
clôture complète (`statut = 'ferme'`), dans un `try/except` séparé : un
échec de l'analyse post-trade (LLM, réseau, garde-fou) ne remet jamais
en cause l'enregistrement de la clôture elle-même, déjà committée avant
cet appel. `run_executor_loop` construit le client Anthropic une seule
fois au démarrage (`config.anthropic_api_key`) et le transmet à travers
`manage_open_trades`.

**Validé sur le trade réel** : `analyze_closed_trade()` relancé
manuellement sur le trade BTCUSD (fermé avant ce correctif, donc sans
ligne `trade_analysis` au moment de la clôture) — partie déterministe
correcte (r_multiple=-0.034, denouement=sl_hit, durée=535s, jour_semaine
correctement dimanche) et résumé narratif factuel, sans jugement :
« Un signal d'entrée avec confiance maximale (1.0) a été exécuté le
samedi à 18h UTC [...]. Le stop loss a été atteint après 535 secondes
[...], générant une perte de -0.03R sur le risque défini. » Notification
envoyée via `audit_notifier`.

---

## 2026-08-20 — Palier P2.5 : Flux B (Hypothèse #1), architecture multi-source

Implémente `docs/HYPOTHESES.md` Hypothèse #1 (validée par Ismaël le
20/08/2026), phase 2 de son mandat. Regroupe les décisions d'architecture
nécessaires pour faire coexister deux flux (Station X, Flux B) sur la
même base de données, sans jamais mélanger leurs résultats.

**`src/trend_strategy.py` = le module `trend_strategy` du CDC** (§2.11,
§4.4), construit pour la première fois à ce palier. Logique pure
(`compute_regime`, `compute_donchian_channel`, `evaluate_entry`), 100%
couverte (demande explicite d'Ismaël, même règle que `risk_engine.py`).

**Process séparé, pas fusionné dans `executor.py`** : demande explicite
d'Ismaël ("la boucle tourne aux côtés des deux autres"). Écarté au
profit d'un process séparé : fusionner les deux flux dans une seule
boucle aurait été plus simple (un seul writer SQLite, pas de filtrage
par source nécessaire) mais contredisait l'instruction reçue. Conséquence
assumée : deux process Python indépendants écrivent concurremment sur le
même fichier SQLite — voir les points de vigilance ci-dessous.

**Filtrage par source sur les requêtes partagées** : `manage_open_trades`
et `check_pending_fills` acceptent désormais `include_sources`/
`exclude_sources`/`sources` — chaque boucle ne touche que ses propres
trades. `cancel_stale_working_orders` reste volontairement non filtrée
(elle n'agit que sur des ordres broker, pas des lignes DB détenues par
l'autre process ; une double tentative d'annulation du même ordre par
les deux boucles serait sans conséquence, juste une erreur API absorbée
par le `try/except` déjà en place).

**`envelopes.source` + reconstruction de la contrainte UNIQUE** : le
CDC ne prévoit qu'une enveloppe par (actif, mode). Une deuxième
dimension (source) est nécessaire pour que Station X et le Flux B aient
chacun leur propre solde sur un même actif (EURUSD, GBPUSD, USDJPY,
US30, ETHUSD sont concernés). `ALTER TABLE ADD COLUMN` ne peut pas
changer une contrainte UNIQUE existante en SQLite — `_migrate_envelopes_source()`
reconstruit la table (id préservés à l'identique pour ne pas casser les
références de `envelope_ledger`), testé sur une base simulant l'état
réel du VPS avant application. `trade_analysis.source` ajoutée de la
même façon (simple colonne, pas de contrainte à toucher).

**`_envelope_source_key()` : "hypothesis" vs "stationx", pas les valeurs
brutes de `signals.source`** : `trades.source`/`signals.source` stockent
pour Station X l'identifiant brut du canal Telegram (id numérique, voir
CLAUDE.md), jamais la chaîne littérale "stationx". Plutôt que de
réécrire `telegram_listener.py` (module P1 déployé et stable, hors
périmètre de cette demande) pour produire une étiquette propre, le
routage d'enveloppe normalise : tout ce qui n'est pas explicitement
`"hypothesis"` est traité comme `"stationx"`. `trade_analysis.source`
reçoit cette version normalisée (lisible), pas la valeur brute.

**Réserve globale non threadée en mémoire — relue avant chaque
clôture** : avec un seul process (avant ce palier), accumuler
`reserve_total` en mémoire d'un cycle à l'autre était sûr. Avec deux
process concurrents, ce cache pouvait devenir périmé si l'autre process
clôturait un trade gagnant entre deux lectures. `manage_open_trades` ne
retourne plus de `reserve_total` : chaque clôture complète relit
`load_reserve_total(db_path)` juste avant d'écrire. **Limite acceptée,
pas résolue par un verrou distribué** : une fenêtre de course résiduelle
subsiste entre la lecture et l'écriture (deux transactions SQLite
séparées, pas une seule atomique) — jugée négligeable au volume de
trades actuel (quelques trades/jour au mieux) et sans aucun capital réel
en jeu. À revisiter si le volume augmente significativement.

**Signaux du Flux B archivés comme `raw_messages` synthétiques** :
`signals.raw_message_id` est `NOT NULL REFERENCES raw_messages(id)` —
plutôt que d'assouplir cette contrainte (qui protège la traçabilité de
tout signal Station X), chaque signal du Flux B reçoit une ligne
`raw_messages` synthétique (`channel='trend_strategy'`, `telegram_msg_id`
= horodatage en millisecondes, `raw_text` = résumé lisible des valeurs
MA200/Donchian ayant déclenché le signal). Effet secondaire positif :
donne une trace auditable de *pourquoi* chaque signal du Flux B s'est
déclenché, pas seulement le résultat.

**`HYPOTHESIS_ASSETS` figée en constante** (US30, EURUSD, GBPUSD,
USDJPY, ETHUSD) : reflet de l'audit du 19-20/08/2026 (0 signal Station X
capté sur ces 5 actifs depuis le lancement de P1). Pas relue
dynamiquement depuis l'état de `signals` à chaque démarrage — si Station
X commence à publier sur l'un de ces actifs, les deux flux tourneraient
alors en parallèle dessus (enveloppes déjà séparées, aucun conflit
technique), mais la liste elle-même n'est ajustée que par une nouvelle
entrée datée dans `docs/HYPOTHESES.md`, jamais silencieusement.

**Tests** : 250 tests passent (241 + 9 nouveaux sur ce lot). 100% sur
`risk_engine`/`capital_manager`/`go_nogo`/`validator`/`trend_strategy`.
`trend_executor.py` (orchestration) testé avec des doubles, sans
exigence de couverture totale — même traitement que
`telegram_listener.run_listener`/`executor.run_executor_loop`.

---

## Rappel — écarts déjà actés au palier P0 (détail dans `CLAUDE.md`)

- **Broker OANDA → Capital.com** : entités OANDA UE routées vers OANDA TMS
  Brokers S.A., exclue de l'API v20. Décision prise avant l'ajout du CDC au
  dépôt ; §8.1/§5.2 du CDC mentionnent encore OANDA littéralement — aucun
  champ du schéma §4.5 n'est spécifique à un broker, aucun impact de ce
  pivot sur le modèle de données.
- **Compte Telegram personnel plutôt que dédié** (§5.2 : "compte Telegram
  dédié obligatoire") : lien d'invitation Station X irrécupérable pour un
  nouveau compte. 2FA activée en compensation. Risque résiduel documenté
  en détail dans `CLAUDE.md` (le fichier `.session` donne accès à
  l'intégralité du compte personnel, pas seulement à Station X).
