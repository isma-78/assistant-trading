# Rapport final — mandat autonome du 08/10/2026, Partie 1

Branche `fidelite-08-10`. Protocole : `docs/PROTOCOLE_AUTONOME_08-10.md`
(commit `7ce6ada`, inchangé). Journal des lectures prudentes :
`docs/AUTONOMIE_08-10.md`. Checkpoint détaillé : `docs/CHECKPOINT_08-10.md`.
Aucun déploiement, aucun push, aucune action VPS, aucune action broker
hors lecture de prix en démo (budget respecté).

## 1. Verdict en 5 lignes

Rafraîchissement réussi : **oui** (27/27 combinaisons, 27/200 appels,
aucun 429). Fidélité établie : **0/4** (H1-H4 toutes "non établie, n
insuffisant" — n paires 6/4/5/6). Causes dominantes : la **sortie
(timing/étiquette)** domine pour les 4 hypothèses (part de l'écart
≥38%, jusqu'à 327% pour H1) ; le **refus de resserrement de stop**
domine spécifiquement pour H2 (65%). E2 prêt : **oui**, module construit,
testé (100%), OFF par défaut, jamais activé dans cette Partie 1.

## 2. Tableau H1-H4 (fidélité)

| Hyp. | n live | n paires | Appariement | Écart absolu | Biais signé | Cause dominante (part) | Statut | n=20 estimé |
|---|---|---|---|---|---|---|---|---|
| H1 | 8 | 6 | 75,0% | 0,083 R | +0,023 R | sortie timing/étiquette (327%*) | non établie (n insuffisant) | 2026-10-19 |
| H2 | 12 | 4 | 33,3% | 0,784 R | -0,700 R | refus resserrement stop (65%) | non établie (n insuffisant) | 2026-12-03 |
| H3 | 7 | 5 | 71,4% | 0,607 R | +0,095 R | sortie timing/étiquette (39%) | non établie (n insuffisant) | 2026-11-12 |
| H4 | 10 | 6 | 60,0% | 0,309 R | +0,126 R | sortie timing/étiquette (65%) | non établie (n insuffisant) | 2026-10-25 |

\* Les causes sont mesurées **indépendamment** (protocole §1.5, jamais une
partition exclusive qui somme à 100%) : une part >100% signifie que la
contribution mesurée dépasse l'écart net sur un échantillon à n très
faible (6 paires) — ordre de grandeur, jamais une preuve. CHFJPY : **0
trade fermé** sur la fenêtre pour les 4 hypothèses, rien à rapporter à
part. Causes ≥20% rapportées et **proposées à un mandat séparé** (jamais
modélisées ici, aucune modification du simulateur).

## 3. Remplissage, spread, financement

- **Remplissage/délai (entrée)** : part de l'écart mesurée via
  `trade_causal_decomposition.cout_entree`, couverture **37/37** trades
  éligibles poolés (H1 8/8, H2 12/12, H3 7/7, H4 10/10). Coût moyen
  d'entrée : H1 -0,0020 R, H2 -0,0025 R, H3 -0,0018 R, H4 -0,0006 R.
- **Spread/coût (sortie)** : couverture **34/37** (H1 8/8, H2 11/12, H3
  7/7, H4 8/10). Coût moyen de sortie : H1 -0,023 R, H2 -0,013 R, H3
  +0,005 R, H4 -0,003 R.
- **Financement** : couverture **0/37**. Les 12 lignes connues de
  `financing_transactions` datent toutes du 28-29/08/2026, avant le
  plancher de population (25/09 19:35) — **A4 (capture financement) non
  corrigé**, cohérent avec le constat du bilan du 05/10. Jamais comblé
  (hors périmètre).

## 4. Qualité d'exécution

**Matrice (hypothèse, actif)** : 20 cellules, **aucune n'atteint n=30**
(max observé : H4/US30, n=25). Détail complet dans
`data/snapshots/execution_metrics_08-10.json`. Seule métrique poolée
exploitable : **taux de remplissage poolé = 84,9%** (45 remplis / 53
remplis+péremptions, sur n=129 lignes trades, 76 échecs de placement
exclus du dénominateur par construction, protocole §3).

**Coût/R vs `brut_min`** (MDE0 réutilisé du jalon n=30/σ≈1R du bilan du
05/10, jamais recalculé) :

| Hyp. | n | coût0 (R) | brut_min (R) |
|---|---|---|---|
| H1 | 8 | -0,0250 | 0,2624 |
| H2 | 12 | -0,0147 | 0,2017 |
| H3 | 7 | +0,0029 | 0,0890 |
| H4 | 10 | -0,0033 | 0,0957 |

Constat de faisabilité uniquement (jamais un verdict) : la friction
mesurée reste faible par rapport au gate de puissance à ce stade.

**5 dégradations les plus coûteuses identifiées** (R quand mesurable,
€ approximé via le risque moyen par trade ≈9,85€) :
1. **Échecs de placement non disambigués** : 76 occurrences poolées sur
   129 lignes (59%), tous classés `autre_echec_placement` — A3 (motif
   dédié `limite_refusee`) est déployé mais **0 occurrence**, le motif
   dominant du 05/10 (limit.price) n'explique donc plus la majorité des
   échecs sur cette fenêtre. Cause exacte non disambiguée sans accès aux
   logs VPS. Pas de coût en R (aucun capital risqué sur un ordre jamais
   rempli) mais un coût d'opportunité majeur en volume.
2. **Sortie (timing/étiquette)** : cause dominante pour les 4
   hypothèses (étape 3), ordre de grandeur de quelques dizaines d'euros
   sur l'échantillon actuel (n<10 par hypothèse — jamais une conclusion
   ferme).
3. **Refus de resserrement de stop (H2 spécifiquement)** : 65% de part
   de l'écart, référence poolée 19,75% (toutes hypothèses, voir
   `docs/REFERENCE_E2_08-10.md`) — directement adressé par E2 (construit,
   jamais activé ici).
4. **Péremption marché** : 8 occurrences poolées — volume modéré,
   cohérent avec le §3.2 du bilan du 05/10.
5. **Coût d'entrée/sortie mesuré** : le plus faible des 5, H1 -0,025R
   (~2€ sur 8 trades), H2 -0,015R (~1,75€ sur 12 trades) — pas le moteur
   du résultat, cohérent avec le constat déjà fait le 05/10.

**E2** : construit (`src/execution/stop_tightening_retry.py`), OFF par
défaut, 18 tests, 100% de couverture. Taux de référence pré-activation :
**19,75% poolé** (`docs/REFERENCE_E2_08-10.md`). `scripts/mesure_effet_e2.py`
écrit (surveillance lecture seule, arrêt automatique), 21 tests, 100%.
`docs/PATCH_EXECUTOR_E2_PROPOSE.diff` écrit, vérifié (`git apply --check`,
`py_compile`, 125 tests `test_executor.py` sur une copie temporaire
supprimée), **jamais appliqué** au dépôt réel.

**E1/E3/E4 — analyse seule.** Ces trois étiquettes ne sont définies
NULLE PART ailleurs dans le dépôt (recherche explicite avant d'écrire
quoi que ce soit, consignée dans `docs/AUTONOMIE_08-10.md`) — seul E2
est défini par le protocole §4. Lecture prudente retenue : traitées
comme des étiquettes de protocole pour les 3 frictions non-E2 les plus
coûteuses de cette étape, jamais comme des mécanismes préexistants.

- **E1 (candidat)** — mitigation des échecs de placement
  `autre_echec_placement`. Problème chiffré : 76/129 lignes (59%) de la
  population. Gain potentiel : si la moitié de ces échecs devenait des
  trades remplis, le taux de remplissage poolé passerait de 84,9% à
  ~90% (ordre de grandeur, cause non disambiguée). Invariant qui
  interdit l'activation : la cause exacte n'est pas connue (nécessite
  les fichiers de log VPS, hors périmètre) ; toute action sur la logique
  d'entrée engagerait l'invariant #3 (validation déterministe complète
  avant ordre), hors périmètre d'un mandat "analyse seule, aucune
  modification de l'exécuteur hors le point d'appel E2".
- **E3 (candidat)** — seuil adaptatif de placement limite, **déduit de
  la distribution du spread** (jamais des R, conformément à l'énoncé).
  Problème chiffré : écart signal vs référence horaire mesuré par actif
  (étape 4) — ex. GOLD +0,116, BTCUSD -6,58 (unités natives, jamais
  poolées entre actifs). Gain potentiel : non chiffrable sur cette
  fenêtre (0 occurrence `limite_refusee`, le problème qu'un tel seuil
  adresserait ne s'est pas manifesté depuis le 25/09). Invariant qui
  interdit l'activation : changer le niveau limite proposé à l'entrée
  est une décision d'exécution qui engage l'invariant #3, hors périmètre
  de ce mandat (seul le point d'appel du resserrement de stop peut être
  touché, protocole §8).
- **E4 (candidat)** — ajustement du stop CIBLE initial (avant la
  première tentative) à partir de l'historique des bornes divulguées par
  le broker, plutôt qu'un retry sur la même cible (ce que fait E2).
  Problème chiffré : 19,75% de refus poolé pré-E2. Gain potentiel : du
  même ordre que le critère de succès de E2 (réduction relative de 50%),
  mais par un mécanisme distinct, non cumulé ici. Invariant qui interdit
  l'activation : invariant #5 (jamais un élargissement) — calibrer cet
  ajustement sans données réelles post-E2 serait un paramètre réglé à
  l'aveugle, contraire à l'invariant #10 (toute nouvelle règle doit être
  justifiée avant de regarder les données, jamais après).

## 5. Impact sur le statut des candidates de signal

**Aucune promotion.** Les 4 hypothèses restent en configuration
actuellement déployée, fidélité non établie. Conditions pour devenir
évaluables (n=20 paires, projection linéaire, ordre de grandeur) : H1
**2026-10-19**, H2 **2026-12-03**, H3 **2026-11-12**, H4 **2026-10-25**.
Ces dates supposent une cadence d'appariement constante — non garanti,
ordre de grandeur seulement (protocole §1). Aucune cause n'a été
modélisée (aucune n'était autorisée à l'être dans ce mandat), donc aucun
changement du simulateur ne vient accélérer ou ralentir ces projections.

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **Pas d'accès VPS dans cette Partie 1.** Conséquences : (a) le live
   utilisé aux étapes 3/4 est l'instantané local `data/snapshots/
   prod_07-10.db` (relevé 06/10/2026 ~19:51 UTC), pas la base de
   production à la date réelle de ce mandat — les n rapportés sont une
   **borne basse** ; (b) le taux de refus FINAL de resserrement de stop
   n'a pas pu être remesuré sur cette population (journalisé uniquement
   dans les fichiers de log du VPS avant ce mandat) — seuls les chiffres
   du 05/10 (`BILAN_05-10.md` §2.3) restent disponibles, jamais présentés
   comme remesurés ; (c) le délai de remplissage n'est pas mesurable
   avec le schéma actuel (`trades` n'a pas de colonne dédiée à l'instant
   de placement) — l'approximation tentée donne ~0s pour tous les trades,
   un artefact, jamais un vrai délai.
2. **Cause exacte des 76 échecs `autre_echec_placement`** non
   disambiguée — nécessiterait les fichiers de log VPS (le motif A3
   `limite_refusee`, déjà déployé, n'a matché aucun cas sur cette
   fenêtre).
3. **`mesure_effet_e2.py` jamais exercé contre de vraies données
   post-activation** (E2 n'a jamais été activé dans cette Partie 1) —
   validé uniquement par ses propres tests unitaires (mock complet).
4. **E1/E3/E4** ne sont définis nulle part ailleurs dans le dépôt —
   traités comme des étiquettes pour les 3 frictions non-E2 les plus
   coûteuses de cette étape (lecture prudente, consignée avant ce
   rapport), jamais comme des mécanismes préexistants retrouvés.
5. **Suite de tests complète** : relancée en fin de mandat
   (`pytest -q`), résultat brut ci-dessous.

```
$ pytest -q
........................................................................ [ 18%]
........................................................................ [ 23%]
........................................................................ [ 28%]
........................................................................ [ 32%]
........................................................................ [ 37%]
........................................................................ [ 42%]
........................................................................ [ 47%]
........................................................................ [ 51%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 65%]
........................................................................ [ 70%]
........................................................................ [ 75%]
........................................................................ [ 80%]
........................................................................ [ 84%]
........................................................................ [ 89%]
........................................................................ [ 94%]
........................................................................ [ 98%]
................                                                         [100%]
1528 passed in 233.32s (0:03:53)
```

(1458 avant ce mandat + 70 nouveaux tests de cette Partie 1 : 31
`test_rafraichir_historique.py` (étape 1) + 18 `test_stop_tightening_
retry.py` + 21 `test_mesure_effet_e2.py` (étape 5) = 1528. Aucun échec,
aucune régression sur les 1458 tests préexistants.)
