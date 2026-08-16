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
