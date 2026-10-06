# Fidélité du simulateur H1-H4, validation absolue, automatisation du suivi shadow (07/10/2026)

Branche `fidelite-07-10` (depuis `evolution-06-10`), rien poussé ni
déployé, aucun appel broker, aucune hypothèse arrêtée/suspendue/retirée.
1458 tests passent, 100% de couverture sur le code nouveau et sur les
modules critiques. `risk_engine.py`, `validator.py`, `circuit_breaker.py`,
`executor.py`, `.env` : **non modifiés** (vérifié par `git diff` contre
`evolution-06-10`).

Pré-enregistrement commité **avant tout calcul** :
`docs/PROTOCOLE_FIDELITE_07-10.md`, commit `02cc47f` (2026-10-06
19:57:22 +0200).

**Contrainte majeure, constatée avant tout chiffre** : `data/historical/`
(seule source de prix autorisée) s'arrête au **2026-09-25T18:00:00
UTC** — avant E1/E2. La fidélité mesurée ici porte exclusivement sur la
configuration ANTÉRIEURE à E1 (TP fixe involontaire pour H1/H3/H4,
ancien combo retiré pour H2), **jamais** sur la configuration
actuellement déployée.

---

## 1. Verdict en 5 lignes

**Fidélité établie pour 0 hypothèse sur 4.** H1, H3, H4 ont n<20 paires
ET dépassent déjà les seuils d'écart/biais indépendamment de n ; H2 n'a
que 3 paires (configuration retirée, ne grandira plus). **0 candidate
éligible, 1 « améliore la v2 sans être rentable » (H2), 2 non validées
(H1, H3), 1 indémontrable sur cette fenêtre (H4).** Cause dominante de
l'écart live/backtest partout : la structure de sortie TP-fixe (bug déjà
corrigé le 25/09) — **délibérément non modélisée** (la modéliser
calibrerait le simulateur sur une configuration obsolète). Aucune
modification du simulateur dans ce mandat ; les résultats du 06/10
restent seuls valides, leurs bornes basses recalculées à m=8 pour
comparabilité.

## 2. Tableau H1-H4 — fidélité

| Hyp. | Période live retenue | n live éligible | n backtest | n paires | Taux d'appariement | Écart absolu moyen | Biais signé | Cause dominante (part) | Fidélité |
|---|---|---|---|---|---|---|---|---|---|
| H1 | 2026-08-30→2026-09-23 (TP fixe involontaire) | 28 | 64 | 19 | 68% | 0,672 R | **+0,182 R** (>0,15) | sortie (37,7%) ; refus resserrement (13,3%, <20%) | **non établie** (n<20 ET biais hors seuil) |
| H2 | 2026-09-03→2026-09-24 (ancien combo, retiré) | 22 | 10 | **3** | 14% | 0,154 R (≤0,30) | −0,130 R (≤0,15) | sortie (109%, n=3 très instable) | **non établie** (n largement insuffisant — ne grandira plus sous cette config) |
| H3 | 2026-09-03→2026-09-22 | 21 | 40 | 12 | 57% | **0,516 R** (>0,30) | +0,001 R | refus resserrement (54,7%) ; sortie (96,7%) | **non établie** (n<20 ET écart hors seuil) |
| H4 | 2026-09-03→2026-09-23 | 21 | 23 | 10 | 48% | **0,465 R** (>0,30) | −0,082 R | sortie (49,4%) ; refus resserrement (20,3%) | **non établie** (n<20 ET écart hors seuil) |

n=20 estimé (projection linéaire, ordre de grandeur) : H1 **2026-10-08**,
H3 **2026-10-20**, H4 **2026-10-29**. **H2 : non estimé** — l'ancien
combo est retiré depuis le 25/09, cette configuration ne produira plus
aucune nouvelle paire ; une date projetée n'aurait aucune valeur.
`remplissage_delai_entree`/`spread_cout_sortie` : non mesurables pour la
quasi-totalité des paires (`trade_causal_decomposition` n'a jamais été
calculée pour le lot réconcilié du 24/09, qui constitue l'essentiel de
cet échantillon). `financement` : non mesuré séparément (déjà inclus
dans le R backtest par construction, capture réelle trop clairsemée — 12
lignes du 28-29/08 — pour recouvrir les nuits des paires mesurées).
`plafond de cluster` : 12/137 trades backtest (portefeuille poolé des 4)
auraient été bloqués — explique une partie des signaux « sans pendant
live », jamais un écart de R par paire.

**Étape 2 — aucune modélisation.** Le refus de resserrement clear 20%
pour H3/H4 mais **est déjà modélisé depuis le 05/10 (A8)** — présent
sans changement dans le walk-forward du 06/10. La structure de sortie
clear 20% partout mais **n'est pas modélisée délibérément** : elle
reflète le bug TP-fixe-involontaire, corrigé en production depuis le
25/09 (Option B) — la modéliser rendrait le simulateur fidèle à une
configuration qui n'existe plus, contre-productif pour juger le forward
des candidates. **Aucune cause n'est donc modélisée pour aucune
hypothèse** → règle 4 : pas de rejeu nécessaire, le 06/10 reste seul
valide, borne basse recalculée à m=8 uniquement.

## 3. Tableau des candidates — ancien (m=4) vs recalculé (m=8)

| Hyp. | Variantes (a/b) | Lower bound m=4 (06/10) | Lower bound m=8 (07/10, test seul rejoué) | MDE | D(a) | D(b) | Espérance absolue candidate (poolée) | Statut provisoire | **Statut final** |
|---|---|---|---|---|---|---|---|---|---|
| H1 | 2/2 | +0,009 | **−0,014** | 0,082 | +0,100 | +0,002 | −0,031 R | non validée | **fidélité non établie** |
| H2 | 2/2 | +0,173 | **+0,298** | 0,091 | +0,396 | +0,360 | **−0,004 R** | **améliore la v2 sans être rentable** | **fidélité non établie** |
| H3 | 1,5/1,2 | −0,035 | −0,034 | 0,027 | 0,000 | −0,023 | −0,233 R | non validée (signe instable) | **fidélité non établie** |
| H4 | 20/15 | −0,021 | −0,036 | 0,106 | +0,015 | +0,081 | −0,031 R | indémontrable sur cette fenêtre | **fidélité non établie** |

Écarts m=4→m=8 dus à la stochasticité propre de `StopRefusalModel`
(graine par instance, pas globale au processus) — un rejeu avec les
mêmes variantes ne reproduit pas les mêmes trades filtrés bit à bit,
jamais une divergence de logique. **Résultat le plus notable : H2**
réduit massivement la perte moyenne de la baseline (−0,38R environ) à
quasiment neutre (−0,004R), différence très significative (borne basse
m=8 nettement positive) — mais n'atteint pas elle-même la rentabilité
absolue, donc reste « améliore sans être rentable », jamais éligible.
**Contrôle année par année** : D(a)/D(b) ci-dessus, déjà exigé,
consistant pour H1/H2/H4 (même signe) ; H3 instable (0,0 puis négatif).

## 4. Ce qui est prêt pour le 10/10, et ce qu'Ismaël doit faire lui-même

**Prêt (code testé, branche `fidelite-07-10`)** :
- `scripts/run_shadow_cycle.py`/`scripts/shadow_milestone_alerts.py`
  (déjà prêts depuis le 06/10, inchangés).
- `scripts/rapport_hebdo_shadow.py` : résumé hebdomadaire par candidate
  (n, espérances, différence, IC m=44, MDE, jalons, taux de rejet par
  plafond de cluster), écrit `docs/SUIVI_SHADOW/AAAA-MM-JJ.md`, envoie
  un résumé Telegram, aucune action automatique.
- `scripts/mesure_fidelite_continue.py` : remesure mensuelle de la
  fidélité (même protocole), alerte Telegram au premier franchissement
  de n=20 par hypothèse, aucune action automatique.
- `docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md` mis à jour : 4 crons (cycle
  shadow, alertes shadow, rapport hebdo, remesure mensuelle), commandes
  exactes, retour arrière.

**À faire par Ismaël** :
1. Fusionner `fidelite-07-10` (après/avec `evolution-06-10`) dans `main`,
   pousser, pull VPS, tests.
2. Exécuter le runbook principal du 10/10, puis l'addendum shadow
   (cycle + alertes), **immédiatement après** (décision 4 : pas de
   décalage).
3. Installer le cron du rapport hebdomadaire dès l'activation du shadow.
4. **Ne PAS installer le cron de remesure mensuelle avant que
   `data/historical/` soit rafraîchi au-delà du 25/09 18:00 UTC** —
   sinon il ne ferait que remesurer la même fenêtre pré-E1, sans valeur
   ajoutée. Planifier ce rafraîchissement (hors de portée ici, appel
   broker requis) est un prérequis distinct.
5. Prioriser la mesure de fidélité H1 puis H2 (décision 1) dès que des
   données post-25/09 seront disponibles — H1 est le plus proche du
   seuil n=20, H2 ne progressera plus jamais sous sa configuration
   actuelle (il faudra mesurer sa fidélité sur la grille par défaut/E1,
   une toute nouvelle série, jamais les 3 paires ici).

## 5. Décisions humaines à prendre

1. **H2 « améliore la v2 sans être rentable »** : la différence est
   large et significative (borne basse m=8 = +0,298R) mais l'espérance
   absolue reste négative (−0,004R) — faut-il l'intégrer en candidate
   de référence pour un futur cycle (ex. combinée à une autre idée qui
   relèverait l'espérance absolue), ou la laisser simplement s'accumuler
   en shadow sans suite ?
2. Confirmer qu'aucune mesure de fidélité ne doit être tentée sur les 3
   paires H2 restantes (configuration retirée) — ou souhaite-t-on une
   mesure dédiée de la fidélité de la configuration H2 **actuellement
   déployée** (grille par défaut/E1) dès que des données post-25/09
   existeront, comme une série séparée ?
3. Le rafraîchissement de `data/historical/` (nécessaire à toute mesure
   de fidélité post-E1, et à la remesure mensuelle) implique des appels
   à l'API Capital.com (`scripts/download_historical_data.py`,
   throttlés, mêmes identifiants que les exécuteurs live) — à planifier
   et autoriser séparément, hors de ce mandat.
4. Calendrier du prochain cycle de candidates (H1/H3/H4, puisque H2 est
   dans un état particulier) : attendre le forward shadow (≥8 semaines)
   avant toute nouvelle idée, conformément à la règle anti-empilement
   déjà actée.

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **Fidélité sur la configuration actuellement déployée (E1/E2)** :
   impossible sans rafraîchir `data/historical/` au-delà du
   2026-09-25T18:00:00 UTC, ce qui exige un appel à l'API Capital.com —
   explicitement interdit dans ce mandat. La mesure produite ici porte
   sur une configuration partiellement ou entièrement obsolète pour les
   4 hypothèses.
2. **`trade_causal_decomposition` quasi vide sur l'échantillon mesuré** :
   cette table n'a jamais été calculée pour le lot des 106 trades
   réconciliés le 24/09 (l'essentiel de l'échantillon ici) — les causes
   « remplissage/délai » et « spread/coût » restent donc non mesurables
   pour la plupart des paires, pas seulement sous le seuil de 20%.
3. **H2, n=3** : toute statistique sur cet échantillon (biais, part de
   cause) est rapportée mais **non interprétable de façon fiable** — la
   part de 109% pour la cause « sortie » notamment, signalée comme
   instable, pas comme un résultat solide.
4. **Financement** : non comparé trade par trade (capture réelle trop
   clairsemée, 12 lignes du 28-29/08, pour recouvrir les nuits des
   paires mesurées, qui s'étalent sur tout septembre) — bordé
   analytiquement comme déjà inclus dans le modèle backtest, jamais une
   cause résiduelle par construction, mais pas vérifié empiriquement
   contre le taux réel sur CET échantillon précis.
5. **Scripts d'automatisation (étape 4)** : testés uniquement avec des
   bases SQLite temporaires construites pour les tests et des appels
   broker mockés (aucun appel réel n'étant autorisé) — jamais exécutés
   en conditions réelles sur le VPS.
6. **Stochasticité de `StopRefusalModel` entre scripts** : les écarts
   m=4→m=8 (§3) mélangent l'effet du changement de m et celui du tirage
   aléatoire différent — non séparés (aurait exigé un troisième rejeu,
   explicitement interdit par la règle 4, « aucun second rejeu »).
