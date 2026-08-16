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
