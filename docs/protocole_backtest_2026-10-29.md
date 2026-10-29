# Protocole de backtest hypothèse × actif — pré-enregistrement (29/10/2026, soir)

**Écrit et commité AVANT tout calcul de performance de ce mandat.** Aucun R, aucune espérance
par couple (H1-H5 × actif) n'a été calculé dans cette session avant ce commit. Démo uniquement.
Aucun paramètre n'est optimisé : les règles de production (`*_strategy_v2.evaluate_entry`, sorties
`executor.evaluate_position_management`, sizing `decide_entry`/`risk_engine`) sont rejouées TELLES
QUELLES, par `backtest_engine.replay_hypothesis` (aucune réimplémentation).

## 0. Divulgations (honnêteté)
- **Période de test déjà vue.** Le projet a déjà utilisé 2023-2026 pour d'autres analyses (comparaison de
  structures de sortie, sigma H1/H3, entonnoir H2, corrélations, bilan live du 29/10). Les règles de
  production n'ont pas été ajustées sur ce backtest-ci, mais le tiers « test » n'est PAS vierge.
  La fenêtre scellée 2023-01-01→2024-06-14 est consommée par ce mandat (décision du mandat : train = 2/3
  anciens, test = 1/3 récent). Conséquence : toute décision reste révisable par la surveillance live.
- **Connaissance a priori** : le live E1 (n=189) est agrégé négatif (−0,12 R/trade, IC contenant 0) ; l'adéquation
  structurelle du 29/10 a produit le mapping actuel. Rien de plus fin par couple n'a été lu.

## 1. Faisabilité par hypothèse (vérifiée dans le code)
| Hyp. | Entrée rejouable ? | Entrées de données | Statut |
|---|---|---|---|
| H1 `hypothesis1_strategy_v2` | oui (bougies HOUR) | HOUR | **incluse** |
| H2 `hypothesis2_strategy_v2` | oui | HOUR + HOUR_4 + DAY (alignés par horodatage, bougies closes seulement) | **incluse** ; CHFJPY exclue (DAY seulement depuis 2023-12-10 ; déjà exclue de H2-H5 le 29/08) |
| H3 `hypothesis3_strategy_v2` | oui | HOUR (pas de confirmation croisée en production) | **incluse** |
| H4 `hypothesis4_strategy_v2` | oui | HOUR | **incluse** |
| H5 `hypothesis5_strategy_v2` | oui | HOUR + DAY (filtre momentum 63 j, sans TP1/TP2, trailing) | **incluse** ; CHFJPY exclue (idem H2) |

Non modélisable à l'identique (limites, jamais approximées par des paramètres ajoutés) : filtre d'« heures chères »
(`expensive_hours_by_asset`, calculé sur les spreads live), plafond de cluster 50 € poolé en temps réel (mesuré
a posteriori, §7), refus broker de stops (modèle `simulator_fidelity.StopRefusalModel` utilisé), annonces macro.

## 2. Actifs et historique
Univers = liste blanche du système (9 actifs : GOLD, US100, US30, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD, CHFJPY).
Un instrument hors liste blanche exigerait une spécification de risque (`AssetSpec`) : hors mandat. Historique
broker local (bid/ask OHLC) du 2019-01-01 au 2026-10-29, ≥ 46 000 bougies HOUR par actif (7,8 ans), complété le
29/10 (rattrapage 429 terminé). Rien avant 2019-01-01 (spreads négatifs en 2017-2018).

## 3. Périodes
- Période totale : 2019-01-01 → 2026-10-29 (2 858 jours). **Train = 2/3 anciens : [2019-01-01, 2024-03-20[**
  (1 905 jours). **Test = 1/3 récent : [2024-03-20, 2026-10-29]**. Le test n'est jamais utilisé pour choisir ; toute
  décision se lit sur le test (le train n'est qu'informatif).
- Chaque couple est rejoué UNE fois sur toute la période (échauffement naturel de 220 bougies) ; les trades sont
  affectés à train/test/fenêtres par date d'ENTRÉE.
- **Walk-forward** : 4 fenêtres consécutives, paramètres fixes : W1 2019-01-01→2020-12-31, W2 2021→2022-12-31,
  W3 2023→2024-12-31, W4 2025-01-01→2026-10-29. Une fenêtre est « positive » si n ≥ 5 et R moyen net > 0 ;
  robuste = au moins 3 fenêtres sur 4 positives.

## 4. Modèle d'exécution et de coûts
- Signal calculé sur les seules bougies **closes** (fenêtre glissante de 220, identique au live ; test anti
  look-ahead §6). Ordre d'attente au niveau du signal (clôture), **valable pour la seule bougie suivante**
  (`entry_mode="level_fill"`, `backtest_engine.level_fill_price`) : type décidé comme en live par l'ouverture
  (LIMIT si le niveau est côté attente ; STOP si strictement au-delà du marché) ; rempli **seulement si la fourchette
  bid/ask de la bougie atteint le niveau** (sinon aucun trade) ; un STOP paie un glissement = 1 × spread ouverture ;
  un LIMIT n'est jamais exécuté mieux que son niveau. Limite assumée : en live l'ordre expire en 15 min (ici 1 h) :
  plus de remplissages que le live.
- Dans la bougie d'entrée comme ensuite : **stop testé d'abord au pire prix de la bougie**, cibles/trailing ensuite
  (ordre le plus défavorable). Sorties au niveau mid ± demi-spread ± glissement (1 × spread) ; spread = spread
  bid/ask **historique réel** de la bougie ; financement 1 bp par jour civil complet (toujours un coût). Refus de
  resserrement de stop par le broker : `StopRefusalModel`. Sizing : `decide_entry` avec enveloppe 500 €, 2 %/4 %.
- **R net de coûts** = R du moteur (coûts d'entrée/sortie/financement inclus).

## 5. Métriques (par couple, train / test / chaque fenêtre)
n ; R moyen net ; IC 95 % de Student (`src/surveillance.sample_stats`) ; p unilatéraux de Student ;
drawdown maximal de la courbe cumulée en R ; **concentration** = part du profit brut réalisée par les 3 meilleurs
trades ; taux de remplissage des ordres d'attente.

## 6. Contrôle de biais
- **Test automatique anti look-ahead** (`tests/test_backtest_no_lookahead.py` + vérification exécutée sur données
  réelles dans le script) : rejouer sur une série tronquée à la barre k doit donner exactement les mêmes trades
  clos avant k que le rejeu complet, pour chacune des 5 hypothèses.
- **Seuil d'échantillon** : conclusion seulement si n_test ≥ 30 ; sinon « non concluant ».
- Coûts toujours inclus ; aucun paramètre ajusté après lecture d'un résultat.

## 7. Décision (règles strictes, appliquées mécaniquement)
Famille de Holm : deux familles (une par sens de test), chacune de taille m = nombre de couples avec n_test ≥ 30,
α = 0,05, p unilatéraux de Student sur le R net du test.
- **CONCENTRER** : n_test ≥ 30 ET R_test > 0 ET p (H0 : R ≤ 0) significatif après Holm ET walk-forward robuste
  (≥ 3/4) ET concentration des 3 meilleurs trades ≤ 50 % du profit brut.
- **SUPPRIMER** (pour cette hypothèse) : n_test ≥ 30 ET R_test < 0 ET p (H0 : R ≥ 0) significatif après Holm.
- **NON CONCLUANT** : tout le reste → mapping inchangé pour ce couple, jamais présenté comme preuve.
- Mapping résultant par hypothèse = (mapping actuel − SUPPRIMER) ∪ CONCENTRER, au plus 4 actifs, aucune paire
  avec ρ > 0,60 (ρ de Pearson des R hebdomadaires sommés par actif, période test) ; en cas de conflit/dépassement
  on garde les meilleures statistiques t du test.
- Une hypothèse sans aucun couple CONCENTRER robuste : **proposition de suspension consignée, non appliquée**
  (décision d'Ismaël ; H2 reste active par décision du 05/10).
- Les suspensions existantes de `hypothesis_suspensions` ne sont jamais touchées.
- Mesures descriptives (sans effet sur la décision) : corrélations de R entre actifs d'une hypothèse ; effet du
  plafond de cluster 50 € (`simulator_fidelity.apply_cluster_cap`) sur les trades forex du test ; écarts avec
  `docs/adequation_paires_2026-10-29.md` (lu seulement, jamais modifié) : structure « adaptée » vs R_test < 0, et
  structure « inadaptée/douteuse/exclue » vs R_test > 0.

## 8. Application
Mapping modifié uniquement dans `src/hypothesis_asset_map.py` selon §7 ; époque P2 enregistrée au redémarrage ;
la surveillance n'utilise que l'époque en cours ; positions ouvertes sur actifs retirés gérées jusqu'à clôture.
