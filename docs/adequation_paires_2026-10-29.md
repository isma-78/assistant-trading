# Adéquation structurelle paires/actifs × hypothèses H1-H5 — protocole pré-enregistré (29/10/2026)

**Écrit et commité AVANT toute statistique de performance par paire/actif.**
Aucune espérance, aucun R par couple (hypothèse × actif) n'a été calculé
ni lu avant ce commit. Seules des statistiques agrégées par hypothèse
(docs/BILAN_05-10.md) étaient connues.

**Divulgation (honnêteté)** : les mesures de marché 2019-2022 (coût/ATR,
VR8, regroupement de volatilité — `data/snapshots/couples_structurelles_08-10.json`,
prix seuls, jamais des résultats de trading) ont été lues AVANT d'écrire les
seuils ci-dessous. Garde-fou : (a) chaque seuil est justifié théoriquement
ci-dessous, pas ajusté actif par actif ; (b) toute classification doit être
CONFIRMÉE sur une fenêtre de marché non utilisée pour la fixer
(2024-06-15 → fin des données locales, prix seuls). Si les deux fenêtres
divergent, l'actif est « douteux » (donc retiré : moins d'actifs, mieux
adaptés).

## 1. Caractéristiques structurelles requises, par hypothèse

Mesures (prix MID horaires, `src/structural_characteristics.py`) :
- **C = coût/ATR** : spread médian de clôture / ATR(14). Un trade perd ≈ 1
  spread ; avec un stop de k×ATR (k≈1,5-2,5 : H1 k=2, H4 k=1,5, H3
  buffer 0,5 ATR), le coût en R ≈ C/k. Financement et glissement s'y ajoutent.
- **P = persistance VR8** : ratio de variance à 8 heures (1 = marche
  aléatoire ; >1 tendance, <1 retour à la moyenne). Erreur-type ≈ 0,02
  (n≈24 000 barres, q=8).
- **V = regroupement de volatilité** : autocorrélation de |r| (lag 1).

| Hyp. | Nature | Exigence structurelle | Justification théorique |
|---|---|---|---|
| H1 régime ADX/MA | continuation de tendance | P élevée, C faible | Une entrée de continuation suppose que le mouvement engagé se prolonge ; sur un actif à retour à la moyenne le même signal achète les sommets. |
| H2 confluence EMA/Ichimoku/RSI multi-TF | continuation de tendance | P élevée, C faible | La confluence suppose qu'un alignement multi-horizons persiste. |
| H3 pullback en tendance | continuation + entrée de précision | P élevée, **C très faible** | Le pullback est mesuré en fraction d'ATR avec stop serré : le coût en R est plus lourd (stop = buffer 0,5 ATR sous le niveau). |
| H4 divergence prix/RSI+OBV | retournement | P **faible** (<1), **C très faible** | Un retournement n'est exploitable que si les mouvements ne persistent pas indéfiniment ; stop serré (1,5 ATR) donc coût sensible. |
| H5 compression → expansion | cassure de volatilité (E2 : sens du momentum 63 j) | V élevé, P non <1, C faible | La compression précède une expansion seulement si la volatilité se regroupe en régimes ; la cassure doit se poursuivre. |

Autres critères (qualitatifs, non scorés, mais appliqués par les filtres déjà
en place) : **sessions de liquidité** (indices/forex : Europe+US ; GOLD :
Londres/NY ; crypto : 24/7 mais liquidité faible le week-end — le spread
y explose), **sensibilité aux annonces** (indices, USD, GOLD sensibles à NFP/CPI/FOMC → filtre
d'heures chères existant), **gaps** (indices à l'ouverture). Non mesurés, donc non scorés.

## 2. Score structurel (0 à 2 par critère)

| Critère | Score 2 | Score 1 | Score 0 | VETO (inadapté) |
|---|---|---|---|---|
| C (toutes) | ≤ 0,05 | ≤ 0,08 | > 0,08 | > 0,15 (≈0,075-0,10 R perdu par trade avant glissement/financement) ; **H3 et H4 : veto > 0,10** |
| P (H1,H2,H3,H5) | ≥ 1,00 | ≥ 0,95 | < 0,95 | < 0,93 (≈3,5 erreurs-types sous 1 : retour à la moyenne établi, contredit la prémisse) |
| P (H4) | ≤ 0,93 | ≤ 0,97 | > 0,97 | > 1,07 (≈3,5 e.t. au-dessus de 1) |
| V (H5 seulement) | ≥ 0,30 | ≥ 0,25 | < 0,25 | < 0,15 |

Score total = somme. **Adapté** = aucun veto, sur les DEUX fenêtres de mesure
(2019-2022 ET 2024-06-15 → fin locale), ET score (fenêtre ancienne) ≥ 50 % du
maximum (≥2/4 ; H5 : ≥3/6). Sinon **douteux** (retiré) ; veto = **inadapté**.

## 3. Corrélations et plafond d'actifs

- ρ de Pearson des rendements log horaires (fenêtre 2019-2022 ; recoupée sur
  la fenêtre récente). Deux actifs avec ρ > 0,60 ne sont **jamais** actifs
  ensemble pour une même hypothèse : on garde le meilleur score (égalité :
  C le plus faible).
- **Plafond : 4 actifs par hypothèse**, issus de clusters différents.
- CHFJPY : exclue de H2-H5 par pré-enregistrement du 29/08/2026 (inchangé) ;
  évaluée structurellement pour H1 seulement.

## 4. Performance : protocole (uniquement trades réconciliés, ère E1/E2)

- Population : trades `statut='ferme'`, `anomalie_technique` NULL, R présent,
  ouverts depuis le début d'époque (`hypothesis_epochs`), CHFJPY exclue de
  H2-H5. Les trades hors de ces critères sont comptés, jamais utilisés.
- Split temporel **chronologique** : train = 60 % les plus anciens (par
  `ouvert_at`), test = 40 % les plus récents. Les décisions ne s'appuient que
  sur le TEST ; le train sert à repérer, jamais à décider.
- Par couple : n, R moyen, IC 95 % bootstrap (10 000, graine 20261029).
  Test unilatéral H0 : « R moyen ≥ 0 » (bootstrap centré) pour les couples
  avec n_test ≥ 10 ; **correction de Holm** sur cette famille.
- Règles : structurellement inadapté/douteux → SUPPRIMER ; adapté ET n_test ≥ 10
  ET R_test ≥ 0 ET non significativement négatif (Holm) → CONCENTRER ;
  négatif significatif après Holm avec n_test ≥ 10 → SUPPRIMER ; sinon
  **NON CONCLUANT** (décision sur le critère structurel seul, jamais présenté
  comme preuve). Non concluant + adapté → CONSERVÉ.
- Les anciennes ères (v1, v2 pré-E1 : sortie TP fixe involontaire ou combo
  retiré) sont décrites à part, jamais mélangées aux statistiques.

## 5. Corrections d'hypothèses (étape 4)

Une variable à la fois, justification écrite et commitée avant implémentation,
jugée uniquement sur les trades postérieurs au commit, 5 variables max par
hypothèse. Un stop n'est jamais élargi.
