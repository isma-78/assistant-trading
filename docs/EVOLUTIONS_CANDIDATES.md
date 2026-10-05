# Évolutions candidates (étape 4.3 du mandat du 05/10/2026)

**Horodatage d'écriture : 2026-10-05 (commit noté dans `docs/APPLICATION_05-10.md`).**

Statut : **candidates seulement**. Rien n'est appliqué. Elles ne servent
ni à la sélection d'actifs (protocole `docs/PROTOCOLE_EVOLUTION_05-10.md`)
ni à son test. Chacune a été suscitée par des trades live DÉJÀ VUS (03/09 →
05/10) : ces trades ne pourront jamais servir à la juger. Une candidate ne
peut être jugée que sur des trades **postérieurs à son horodatage**, après
pré-enregistrement complet, validation d'Ismaël et déploiement sous un
libellé de source `_v3` (nouvelle époque, compteur à zéro). Au plus une
candidate par hypothèse.

## Constats de la rétrospective (qualité du signal, causes d'exécution écartées)

Trades live fermés du 03/09 au 05/10, toutes époques `_v2`. Les pertes
liées à l'exécution (stop non resserré, structure TP fixe involontaire
avant E1) relèvent des correctifs A, pas d'ici.

| Hyp. | Fermés | Pertes | Stop initial < 6 h | Stop initial ≥ 6 h | Durée médiane des pertes |
|---|---|---|---|---|---|
| H1 | 35 | 17 | 10 | 7 | 5,8 h |
| H2 | 29 | 7 | 0 | 7 | 93 h |
| H3 | 26 | 16 | 9 | 7 | 3,8 h |
| H4 | 31 | 20 | 6 | 14 | 12,5 h |
| H5 | 23 | 16 | 6 | 6 (+2 stop non resserré, +2 autres) | 8,2 h |

## Gate de puissance commun (m = 40, décision 2)

z₁₋₀,₀₅/₄₀ = 3,023. MDE = (3,023 + 0,842) × σ / √n.
- σ ≈ 1 R (H1-H4) : MDE = 0,611 R à n = 40 ; 0,547 R à n = 50 ; 0,531 R à n = 53.
- σ ≈ 4 R (H5) : MDE = 2,44 R à n = 40 ; 2,12 R à n = 53.
- brut_min = 2 √(coût₀ × MDE₀), avec coût₀ = coût moyen mesuré par trade
  (`trade_causal_decomposition`, entrée + sortie). À n = 40 : H1 0,24 R ;
  H2 0,14 R ; H3 0,13 R ; H4 0,10 R ; H5 0,19 R.

Lecture honnête : aucune de ces candidates n'a d'effet attendu documenté
qui approche 0,5-0,6 R par trade. À un jalon de 40-53 trades, un résultat
non significatif sera **indémontrable**, pas invalidé.

## Candidates

### H1/L1 — entrée sur reprise après le croisement ADX (au lieu de l'entrée au croisement)
- Constat : 10 pertes sur 17 sont des stops initiaux touchés en moins de 6 h.
- Mécanisme : l'ADX de Wilder est un indicateur lissé et retardé. Son
  franchissement de seuil survient souvent à la fin d'une impulsion, au
  moment où un retracement est le plus probable. Attendre la première
  reprise dans le sens de la pente (clôture au-delà du plus haut ou plus
  bas de la bougie de signal, dans une fenêtre fixe) filtre les
  croisements d'épuisement.
- Budget : 3/5 → 4/5. Si une liste d'actifs v3 est déployée pour H1 :
  5/5, plafond atteint.
- Gate : verdict à n ≥ 40 (ou 50 si 5 variables), MDE 0,61 R (0,55 R),
  brut_min 0,24 R. Source prévue `hypothesis_v3`.

### H2/L2 — signal-événement : alignement NOUVELLEMENT établi
- Constat : le signal est un ÉTAT vrai dans 98 % des heures (diagnostic du
  26/09). Le moment d'entrée dépend de la fin du trade précédent, pas du
  marché.
- Mécanisme : une confluence multi-échelle n'a de contenu informationnel
  qu'à sa formation (début d'un mouvement coordonné). Un état persistant
  est déjà intégré au prix. N'entrer que sur la transition « non aligné →
  aligné » (au plus k bougies, k figé) rend la logique sélective sans
  nouvel indicateur.
- Budget : 4/5 → 5/5. **Impossible si une liste d'actifs v3 est déployée
  pour H2** (6/5) : dans ce cas, la candidate n'existe pas.
- Gate : n ≥ 53, MDE 0,53 R, brut_min 0,13 R. Source prévue `hypothesis2_v3`.

### H3/L3 — pas de pullback quand la volatilité est en expansion
- Constat : 9 pertes sur 16 sont des stops initiaux touchés en moins de
  6 h (médiane 3,8 h).
- Mécanisme : un retracement qui survient pendant une expansion de
  volatilité (ATR court > ATR long) a plus de chances d'être un
  retournement qu'une pause de liquidité (regroupement de volatilité).
  Le pullback est la respiration d'une tendance calme.
- Budget : 3/5 → 4/5 (5/5 si une liste v3 est déployée pour H3).
- Gate : n ≥ 40, MDE 0,61 R, brut_min 0,13 R. Source prévue `hypothesis3_v3`.

### H4/L4 — filtre de force de tendance (divergence seulement hors tendance forte)
- Constat : 14 pertes sur 20 surviennent après avoir tenu au moins 6 h
  (retournement qui échoue).
- Mécanisme : idée déjà identifiée le 25/09 (« différée après le verdict
  E1 ») : une divergence de retournement échoue plus souvent en tendance
  forte, où le momentum domine. Elle n'est retenue qu'avec ADX(14) sous
  un seuil figé.
- Budget : 3/5 → 4/5 (5/5 si une liste v3 est déployée pour H4).
- Gate : n ≥ 40, MDE 0,61 R, brut_min 0,10 R. **Ne pas empiler sur E1 avant
  son verdict** (règle du 25/09). Source prévue `hypothesis4_v3`.

### H5/L5 — aucune candidate
- E2 (filtre TSMOM) n'a que 7 trades (6 pertes) : y empiler une nouvelle
  idée rendrait l'effet de chacune inattribuable (même leçon que le
  23/08). Budget 4/5, ou 5/5 si une liste v3 est déployée.
- Les pertes « stop non resserré » relèvent de l'exécution (A8/A10).
