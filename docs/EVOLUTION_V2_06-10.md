# Évolution des hypothèses V2 — test walk-forward + préparation shadow (06/10/2026)

Branche `evolution-06-10` (créée depuis `bilan-05-10`), rien poussé ni
déployé, aucune hypothèse `_v2` arrêtée, suspendue ou modifiée, aucun
appel broker effectué. 1413 tests passent, 100% de couverture sur les
modules critiques et sur tout le code nouveau de ce mandat.

Pré-enregistrement commité **avant tout calcul** :
`docs/PROTOCOLE_EVOLUTION_V2_06-10.md`, commit `f8b3be1` (2026-10-06
18:20:24 +0200).

---

## 1. Verdict en 5 lignes

**0/4 candidates validées, 1/4 indémontrable sur cette fenêtre, 3/4 non
validées.** H1 et H2 ont le résultat statistique le plus fort (diff
positif et significatif sur les deux folds, borne basse bootstrap > 0)
mais sont bloquées **uniquement** par la règle de fiabilité du
simulateur, jamais mesurée en R pour elles (prudence actée dans le
protocole, pas un résultat négatif). H3 a un signe instable entre les
deux folds (non validée sans ambiguïté). H4 a deux folds positifs mais
une borne basse négative et un MDE supérieur à l'effet attendu
(indémontrable, pas invalidée). H5 : aucune candidate, inchangée.
**Aucune candidate n'est proposée à la promotion** (règle du protocole
§7 : walk-forward validé ET forward confirmé requis, aucun des deux
n'est encore acquis).

## 2. Tableau H1-H4

Fenêtre 2019-2022, moteur corrigé, coûts réels, refus de resserrement +
plafond de cluster modélisés (A8). `n` = trades baseline (v2) du fold de
test ; `diff` = R_candidate(matché ou 0) − R_baseline, moyenné sur ces
mêmes `n` trades (protocole §5). brut_min = effet attendu, repris
littéralement de `docs/EVOLUTIONS_CANDIDATES.md` (05/10).

| Hyp. | Variante retenue (a / b) | n test (a / b) | E[R] baseline (a / b) | E[R] candidate (a / b) | D(a) | D(b) | D poolé | Borne basse (m=4) | MDE | brut_min | Fidélité | Statut |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 (fenêtre reprise ADX, grille {2,3,5,8,12,18}) | 2 / 2 | 899 / 865 | −0,165 / −0,035 | −0,048 / −0,019 | **+0,117** | **+0,016** | +0,067 | **+0,009** | 0,075 | 0,24 | non établie | **NON VALIDÉE** (bloquée par la fidélité seule) |
| H2 (transition confluence, grille {1,2,3,4,6,8}) | 2 / 2 | 686 / 794 | −0,304 / −0,216 | −0,001 / −0,001 | **+0,303** | **+0,215** | +0,256 | **+0,173** | 0,091 | 0,13 | non établie | **NON VALIDÉE** (bloquée par la fidélité seule) |
| H3 (ratio expansion volatilité, grille {1,0 … 2,0}) | 1,5 / 1,2 | 533 / 523 | −0,302 / −0,105 | −0,302 / −0,134 | 0,000 | −0,029 | −0,014 | −0,035 | 0,026 | 0,13 | non établie | **NON VALIDÉE** (signe instable) |
| H4 (seuil ADX, grille {15,20,25,30,35,40}) | 20 / 15 | 419 / 362 | −0,063 / −0,125 | −0,036 / −0,039 | +0,027 | +0,086 | +0,054 | −0,021 | **0,105** | 0,10 | non établie | **INDÉMONTRABLE sur cette fenêtre** (MDE > brut_min) |

**Contrôle année par année** : D(a)/D(b) ci-dessus = 2021/2022
séparément, déjà exigé par le protocole — repris tel quel, pas de calcul
supplémentaire.

**Lecture honnête de H3** : la variante retenue sur l'apprentissage du
fold a (ratio 1,5) n'a JAMAIS filtré un seul trade sur 2021 (diff
exactement 0, 0 trade non apparié des deux côtés) — à ce seuil, le
filtre est un no-op sur cette période. Le résultat négatif du fold b
(ratio 1,2, qui filtre réellement) domine donc la conclusion.

## 3. Rétrospective par hypothèse et statut de H5

**H1** — Cause des pertes (bilan 05/10) : 10/17 pertes = stop touché en
moins de 6h (entrée immédiatement invalidée) → qualité du signal, pas
d'exécution → candidate (reprise après croisement). Walk-forward : le
mécanisme fonctionne statistiquement (D>0 aux deux folds, borne basse
>0), mais la validation formelle est bloquée par la fidélité non
établie. Fidélité résiduelle : jamais mesurée en R pour H1 (A8 ne l'a
mesurée que pour H5) ; reste à modéliser : remplissage différé (47% au
1er ordre, bilan §2.4), financement.

**H2** — Cause : H2/v2 est un signal-ÉTAT vrai 98% des heures (diagnostic
26/09), pas un signal-événement → qualité du signal → candidate
(transition). Walk-forward : résultat le plus net des 4 (borne basse
+0,173R), même blocage fidélité. Fidélité résiduelle : 12% d'appariement
live/backtest mesuré au 25/09 (expliqué comme artefact du cycle de vie
signal-état, pas une divergence de logique), jamais remesuré sur E1 ;
reste à modéliser : la confluence multi-TF HOUR/HOUR_4/DAY elle-même.

**H3** — Cause : 9/16 pertes = stop <6h (médiane 3,8h) → qualité du
signal → candidate (expansion de volatilité). Walk-forward : signe
instable (0,0 puis −0,029R), **non validée sans ambiguïté** — le
mécanisme n'est pas confirmé sur cette fenêtre. Fidélité : jamais
mesurée en R ; reste à modéliser : le biais de sélection déjà identifié
dans L3/v2 (les mouvements sans pullback, les plus profitables, sont
exclus par construction de la logique de base, indépendamment de cette
candidate).

**H4** — Cause : 14/20 pertes = retournement qui échoue après ≥6h tenus
→ qualité du signal → candidate (filtre de force de tendance). Walk-forward :
les deux folds sont positifs mais la borne basse reste négative et le
MDE (0,105R) dépasse l'effet attendu (0,10R) — **indémontrable sur cette
fenêtre**, lecture explicitement distincte d'« invalidée ». Fidélité :
jamais mesurée ; reste à modéliser : la jambe OBV (volume tick Capital.com,
déjà signalée non fiable le 25/08/2026).

**H5** — Aucune candidate, aucune évolution (acté le 05/10, reconduit
sans changement le 06/10). Continue à trader en démo sans aucune
modification.

## 4. Ce qui est prêt pour le 10/10, et ce qu'Ismaël doit faire lui-même

**Prêt (code testé, branche `evolution-06-10`)** :
- Les 4 modules candidates (`hypothesis{1,2,3,4}_strategy_v3cand.py`),
  jamais appelés par aucun exécuteur réel.
- L'infrastructure shadow complète (`src/shadow_tracking.py`,
  `src/shadow_milestone.py`, `scripts/run_shadow_cycle.py`,
  `scripts/shadow_milestone_alerts.py`) : vérifiée par test qu'aucune
  méthode d'écriture broker n'est jamais appelée.
- `docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md` : procédure complète,
  **à exécuter après** le runbook principal du 05/10
  (`docs/RUNBOOK_REDEPLOIEMENT_10-10.md`), jamais avant ni en même temps.

**À faire par Ismaël** :
1. Fusionner `evolution-06-10` dans `main` (après ou avec `bilan-05-10`),
   pousser, `git pull` sur le VPS, tests.
2. Exécuter le runbook principal du 10/10 (correctifs A2-A10, A1/A5 à sa
   charge, redémarrage des 6 exécuteurs).
3. **Seulement ensuite**, exécuter `docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md`
   pour activer le suivi shadow des 4 candidates.
4. Décider si la règle de fiabilité du simulateur (protocole §4) doit
   être levée pour H1/H2 une fois une mesure réelle disponible (voir
   §6 ci-dessous) — question bloquante, aucune mesure n'existe encore.
5. Aucune autre décision requise immédiatement : aucune candidate n'est
   proposée à la promotion.

## 5. Décisions humaines à prendre

1. **H1 et H2 sont statistiquement positives et significatives sur
   2019-2022, mais bloquées par une règle de fidélité jamais mesurée
   pour elles** (pas par un résultat négatif) — faut-il prioriser la
   mesure de l'écart live/backtest en R pour H1/H2 (nécessite des
   dizaines de trades E1/E2 déjà clos avec sortie conforme) avant toute
   décision sur ces deux candidates ?
2. Confirmer que la règle de promotion (walk-forward validé ET forward
   confirmé, décision déjà actée le 05/10 et reconduite ici) reste la
   seule voie — aucune candidate ne peut donc être déployée avant au
   moins 8 semaines de shadow après l'activation du 10/10, même si la
   fidélité était établie entre-temps pour H1/H2.
3. Décider si H3 doit recevoir une nouvelle candidate à un cycle futur
   (son mécanisme actuel n'est pas confirmé) — aucune proposition
   n'est faite ici (une seule candidate par hypothèse par cycle, déjà
   consommée).
4. Confirmer le calendrier d'activation du shadow (immédiatement après
   le 10/10, ou décalé) — le protocole n'impose pas de délai, seulement
   un ordre (après le runbook principal).

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **Fidélité simulateur H1-H4** : aucune mesure de l'écart live/backtest
   en R n'existe pour ces 4 hypothèses (A8 du 05/10 ne l'a mesurée que
   pour H5, seule hypothèse à sortie conforme avant E1) — appliqué par
   prudence comme « non fiable », conformément au protocole §4 écrit
   avant ce calcul. C'est la SEULE raison du blocage de H1/H2.
2. **Shadow forward réel** : aucun signal shadow n'a été produit (aucun
   appel broker effectué dans ce mandat, conformément à l'interdiction) —
   le verdict forward (§4/§6 du protocole) ne pourra être calculé
   qu'après l'activation post-10/10 et au moins 8 semaines.
3. **Tolérance d'appariement de 2h pour H1** : la candidate H1 entre
   SYSTÉMATIQUEMENT plus tard que L1/v2 (par construction — elle attend
   une reprise après le croisement, parfois plusieurs heures). La
   tolérance de 2h du protocole (reprise de
   `_compare_live_vs_backtest_window.py`) sous-apparie donc
   probablement les trades H1 candidate à leur contrepartie baseline
   réelle — de nombreux trades candidate matchent 0 (comptés comme
   « non signalés »), ce qui est probablement trop strict pour H1
   spécifiquement. **Noté ici après coup, le protocole n'a pas été
   modifié rétroactivement** (règle du mandat). Le résultat H1
   (déjà positif et significatif malgré cela) est donc probablement
   une **borne pessimiste**, pas une surestimation.
4. **H3, variante fold a (ratio 1,5)** : no-op sur 2021 (jamais
   déclenché) — signale que la grille {1,0…2,0} est peut-être trop
   large à son extrémité haute pour cette hypothèse ; non recalculé
   (grille figée par le protocole avant tout calcul, jamais rejouée
   après coup).
5. **Correction de multiplicité m=44 pour le forward** : jamais
   appliquée dans ce mandat (aucun signal shadow encore produit) —
   seulement vérifiée dans le code (`decide_forward_verdict`, testé).
6. **Tests d'intégration réels sur compte démo séparé** pour
   `run_shadow_cycle.py`/`scripts/shadow_milestone_alerts.py` : non
   faits (règle CLAUDE.md, aucun ordre manuel ; et aucun appel broker
   n'était autorisé dans ce mandat). Couverts par des tests unitaires
   avec client mocké uniquement.
