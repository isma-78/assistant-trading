# Rapport de mission — Volets A/B/C (24/09/2026)

Suite à l'audit du 24/09/2026 (`docs/Audit_Bilan_H1-H5_24-09-2026.md`).
Détail technique complet (code cité, preuves de test, données brutes)
dans `docs/DECISIONS.md`, entrées du 24/09/2026 — ce document en est la
synthèse orientée décision. **Aucun déploiement, aucune écriture en
production, aucune pré-registration validée n'a eu lieu** : tout ce qui
suit est proposé, rien n'est appliqué.

---

## 1. Bugs corrigés — preuve de test, statut de déploiement

Deux bugs distincts isolés avec preuve code + données, jamais confondus
dans un correctif unique.

### Bug 1 — échecs de placement (`stop_refuse`/`autre_echec_placement`) concentrés sur GOLD/BTCUSD/ETHUSD

**Cause** : le seuil de stop garanti exigé par Capital.com est une bande
dynamique (déjà documentée le 28-29/08/2026 pour la mise à jour de stop),
jamais fiable depuis le seul instantané statique lu à l'ouverture. Le
correctif "réessai avec la valeur exacte divulguée par le broker" existait
déjà pour `update_position_stop`, jamais porté au placement initial.

**Fix** : réessai unique dans `executor.py::open_signal`, taille
recalculée via `decide_entry()` (jamais un second calcul de risque
local). Ne s'applique jamais hors stop garanti ; n'accepte la valeur
divulguée que si elle est strictement plus large que ce qui a déjà été
tenté.

**Preuve** : 3 tests (`tests/test_executor.py`), vérifiés en échec sur le
code d'avant (`git stash`) et en succès après. 1229 tests au total
passent, couverture 100% inchangée sur `risk_engine`/`capital_manager`/
`go_nogo`/`validator`.

**Statut** : code écrit, **non commité, non déployé**.

### Bug 2 — le stop au breakeven après TP1 n'était jamais transmis au broker

**Cause** : `_apply_management_action` mettait à jour `stop_loss_courant`
en base locale sans jamais appeler `client.update_position_stop()` — le
stop réel chez Capital.com restait à son niveau d'origine après TP1.
Signature en production : les 45 trades qui touchent TP1 s'arrêtent
TOUS à exactement un seul palier, jamais de second.

**Fix** : logique de plafonnement extraite dans `_push_stop_to_broker()`,
appelée par le trailing ET par la clôture partielle TP1 — jamais
dupliquée. Invariant #3 inchangé (le garde-fou de non-élargissement
existant est réutilisé tel quel).

**Preuve** : 2 tests, même discipline (échec confirmé sur le code
d'avant, succès après).

**Statut** : code écrit, **non commité, non déployé**.

### Anomalie trouvée, NON corrigée — décision requise avant tout code

`place_limit_order` n'attache **aucun stop** (ni niveau ni distance)
pour tout instrument où le stop garanti n'est pas exigé — la majorité de
la liste blanche hors GOLD/BTCUSD/ETHUSD/GBPUSD/US100. Ces positions
n'ont RIEN pour les protéger côté broker tant qu'aucun mécanisme local
(trailing, TP1) n'a posé un stop. Touche une décision de risque
(quel stop poser, sous quelle forme) — **aucun code écrit**, voir
décision 3 ci-dessous.

---

## 2. Jeu de données récupéré depuis Capital.com — n recalculé

**Méthode** : `scripts/fetch_capital_history.py` (lecture seule,
`GET /history/activity`, aucun ordre) exécuté depuis le VPS (pas ce
poste — le `.env` local n'a pas les identifiants du compte H5) sur les 5
comptes démo, du 30/08 au 24/09/2026. 1136 activités brutes récupérées.
Rapprochement avec les trades locaux `ferme_non_reconcilie`/`ouvert` par
`dealId` — R réel recalculé depuis les prix RÉELS du broker
(`openPrice`/`level`) et le stop initial de notre propre décision de
risque (invariant #2 : jamais recopié, toujours recalculé).

**106 des 116 trades non-reconciliés retrouvent un R réel et vérifiable**
(10 sans aucune activité de clôture trouvée côté broker). Un trade
(ETHUSD, H5, +13,28R) vérifié manuellement événement par événement dans
`docs/DECISIONS.md` — trailing suivi correctement sur 3 jours, clôture
par stop légitime.

### n recalculé par hypothèse (v2, tous actifs confondus)

| Hypothèse | n avant (audit du 24/09 matin) | n après récupération | Total R | Seuil n≥30 |
|---|---|---|---|---|
| H1 (`hypothesis_v2`) | 0 | **28** | +2,45R | Pas atteint (proche) |
| H2 (`hypothesis2_v2`) | 0 | **18** | +6,23R | Pas atteint |
| H3 (`hypothesis3_v2`) | 0 | **20** | -4,45R | Pas atteint |
| H4 (`hypothesis4_v2`) | 0 | **20** | -9,72R | Pas atteint |
| H5 (`hypothesis5_v2`) | 0 | **19** | +26,79R | Pas atteint |

**Aucune hypothèse n'atteint le seuil n≥30 — aucun de ces chiffres ne
doit être lu comme un verdict.** Le détail par (hypothèse, actif), tous
sous n=10, est dans `docs/DECISIONS.md` (Volet B) et le CSV d'audit.
H5's total très positif (+26,79R sur seulement 19 trades) est
dominé par 3-4 trades à fort R (trailing gagnant) — signe d'une
distribution à forte asymétrie (queue épaisse), pas d'un edge stable
démontré sur un si petit échantillon.

**Décision requise avant toute action** : ces 106 R ne sont PAS écrits
dans la base de production (`trades.r_multiple_total`/`statut`) — voir
décision 2 ci-dessous.

---

## 3. Décision proposée sur H2 — arrêt immédiat, en attente de validation

**Constat, indépendant du Volet A/B** : le combo actuellement en
production pour H2 (`EMA=20/RSI=55/N_TF=3/SCORE=1,0`) a été déclaré
**« mort »** par écrit le 01/09/2026 (`docs/Prompt_Recalibration_01-09.md`)
puis reconfirmé négatif (24 combos sur 24) le 02/09/2026 — et tourne
pourtant, inchangé, depuis son redémarrage du 03/09/2026, sans qu'aucune
décision explicite et datée n'ait été prise pour le maintenir ainsi
après ce verdict.

**Proposition** (non tranchée ici, trois options) :
1. Revenir aux valeurs par défaut de grille (comme H1/H3/H4/H5) ;
2. Arrêter H2 spécifiquement (aucune collecte forward tant qu'aucun
   combo n'est justifié théoriquement) ;
3. Documenter consciemment la décision de le laisser tourner pour la
   seule collecte forward — mais datée et explicite, pas un vestige du
   29/08 jamais révisé.

Les 18 trades H2 nouvellement reconciliés (§2, total +6,23R) ne changent
rien à ce constat : ils tournent sur un combo que la recherche du projet
a lui-même désavoué, leur résultat ne peut pas être traité comme une
validation de ce combo (invariant #6 — jugé sur des données
postérieures à une justification théorique, jamais l'inverse).

**Ceci est une décision de configuration de risque/exécution en direct
— invariant #4, validation explicite d'Ismaël requise avant tout
changement.**

---

## 4. Volet C — pourquoi la boucle n'a pas pu démarrer cette session

Le mandat (C1-C7) exige une justification théorique écrite AVANT de
regarder les données qui la confirmeraient (invariant #6). Cette
condition est **structurellement impossible à remplir honnêtement
maintenant** pour les cinq hypothèses :

- **H2** : hors périmètre de la boucle C par instruction explicite —
  c'est une décision d'arrêt (§3), pas un sujet d'évolution.
- **H1, H3, H4** : closes négatives sur échantillon bien doté
  (recherche). Toute idée les concernant est une NOUVELLE hypothèse
  (H6+), explicitement bloquée tant que la clause de suspension du
  02/09/2026 n'est pas tranchée par Ismaël (invariant #5) — je ne l'ai
  pas tranchée, je ne peux pas la trancher.
- **H5** : verdict recherche non concluant (pas négatif) — la seule
  candidate théoriquement éligible à un nouveau cycle. Mais **j'ai déjà
  regardé ses données reconciliées** (§2, +26,79R sur 19 trades) avant
  d'écrire quoi que ce soit — toute justification théorique que
  j'écrirais maintenant serait contaminée par cette observation, exactement
  le piège qui a coûté H2 (invariant #6, cité textuellement dans le
  mandat). Je ne propose donc **aucune pré-registration pour H5 dans ce
  rapport.**

**Aucune idée C1/C2 en attente de pré-registration n'est livrée ici** —
en livrer une maintenant, sur cette donnée déjà vue, violerait la règle
même qui rend la boucle dynamique sûre. La bonne manière de démarrer la
boucle C : (a) trancher les décisions 1, 5 et 7 ci-dessous, (b) écrire
la justification théorique d'une hypothèse H5 AVANT de regarder tout
nouveau trade postérieur à cette date, (c) ne juger que sur les trades
qui suivent.

---

## 5. Calendrier estimé pour atteindre n≥30 par hypothèse

Rythme observé sur les 25 jours écoulés (n reconcilié ÷ 25 jours,
hypothèse simplificatrice de rythme constant — le pipeline de
réconciliation était cassé pendant toute cette période, un rythme futur
avec les Bugs 1/2 corrigés serait probablement plus rapide, jamais plus
lent) :

| Hypothèse | n actuel | Rythme (n/jour) | Jours pour n≥30 | Date estimée |
|---|---|---|---|---|
| H1 | 28 | 1,12 | **~2 jours** | ~26/09/2026 |
| H2 | 18 | 0,72 | ~17 jours | ~11/10/2026 (sans objet si arrêté, §3) |
| H3 | 20 | 0,80 | ~13 jours | ~07/10/2026 |
| H4 | 20 | 0,80 | ~13 jours | ~07/10/2026 |
| H5 | 19 | 0,76 | ~15 jours | ~09/10/2026 |

**À prendre avec prudence** : ce rythme suppose que les Bugs 1/2 restent
NON corrigés (94% d'échec de placement) — une fois corrigés (§1),
le rythme réel de trades OUVERTS devrait augmenter sensiblement, ces
délais sont donc probablement des majorants, pas une prédiction fine.

---

## 6. Récapitulatif — décisions nécessitant une validation explicite d'Ismaël

1. **H2** : que faire du combo « mort » toujours en production (§3).
2. **Écrire ou non les 106 R reconciliés dans la base de production**
   (`trades.r_multiple_total`/`statut`) — change immédiatement ce que
   `metrics.py`/`confidence_scorer.py`/le dashboard/le compteur de
   comparaisons multiples rapportent.
3. **Autoriser ou non l'investigation** de l'absence totale de stop sur
   les instruments non-garantis (Bug 1, anomalie non corrigée) — touche
   une décision de risque, même à titre diagnostique (invariant #4).
4. **Autoriser le déploiement des Bugs 1/2 corrigés** (code prêt, testé,
   non déployé) — fenêtre supervisée, procédure de rollback dans
   `docs/DECISIONS.md`.
5. **Suspension H1/H3/H4** : la clause du 02/09/2026 reste à trancher
   avant toute nouvelle hypothèse (H6+) sur ces trois (invariant #5).
6. **Réparer le script de fidélité simulateur**, cassé depuis le
   29/08/2026 (mentionné dans l'audit du matin, toujours vrai).
7. **Réexaminer la règle « hypothèse close reste en démo pour la
   collecte forward »** (29/08/2026) : son postulat (accumulation de
   trades) ne s'est vérifié qu'après correction manuelle de ce rapport
   — sans les Bugs 1/2 corrigés, elle ne produit presque rien.

Aucune proposition de ce rapport ne touche à un seuil de confiance, un
plafond de risque, ou un fichier de configuration de production.
