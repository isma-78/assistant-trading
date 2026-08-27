# ASSISTANT TRADING — Prompts d'exécution, chantiers 2 à 6

**Un chantier par session Claude Code.** Coller le bloc tel quel.
Suite du document `Orientation_H1-H5_26-08.md` (chantier 1 = H3/HOUR_4).

---

## Arbre de décision — ordre et conditionnalité

```
Chantier 1  H3 / HOUR_4 — échantillon            (déjà fourni)
     │
     ├── Chantier 2  Modèle de coûts, recalibré sur fills réels   ← indépendant, à faire tôt
     ├── Chantier 3  H2 — audit mécanique du déclencheur          ← indépendant
     ├── Chantier 4  H1 — univers élargi + rétractation 26/08     ← après chantier 2
     │
     └── selon le résultat du chantier 1 :
            ├─ H3/HOUR_4 QUALIFIE  → Chantier 5  H5 / HOUR_4
            └─ H3/HOUR_4 ÉCHOUE    → Chantier 6  Killzone (dernier axe théorique)
```

**Chantiers 5 et 6 s'excluent mutuellement.** Si H3 échoue avec un effet de 0,134 R, H5 avec 0,051 R n'a aucune chance : on ne l'exécute pas.

---

# CHANTIER 2 — Recalibrer le modèle de coûts sur les exécutions réelles

```
Assistant Trading — Chantier : recalibrer le modèle de coûts §2.6 sur
les exécutions réellement observées.

PROBLÈME IDENTIFIÉ (à vérifier toi-même avant d'agir)

Dans src/backtest_engine.py :
  entry_execution_price : long -> open_ask + spread × SLIPPAGE_SPREAD_
    MULTIPLIER(1.0) = mid + 1,5 spread
  exit_execution_price  : long -> niveau − spread/2 − spread × 1.0
    = niveau − 1,5 spread
  Soit 3,0 spreads par aller-retour, comptés depuis le mid.

Or §2.8 impose des ordres LIMITE uniquement (capital_client.
place_limit_order). Un ordre limite est exécuté à son prix ou mieux, ou
pas du tout : il ne peut pas subir de slippage défavorable. Le modèle
facture donc du slippage sur des jambes qui, par construction, ne
peuvent pas slipper — entrée limite et sortie TP limite. Seule la sortie
au stop est un ordre marché susceptible de slipper.

Confirme ou infirme cette lecture du code avant de continuer. Si je me
trompe, dis-le et arrête-toi.

OBJECTIF

Remplacer une hypothèse de coût par une MESURE. Ne remplace pas
"3 spreads" par "1 spread" sur mon avis : mesure le coût réellement
subi sur les exécutions démo déjà enregistrées en base.

MÉTHODE

1. Extrais, par actif ET par type de jambe, l'écart entre le prix
   d'exécution réel et le prix théorique attendu :
   - Entrée limite : trades.prix_entree_reel vs le prix limite demandé
     et vs le mid au moment du signal (market_snapshots).
   - Sortie au stop : prix de clôture réel vs stop_loss courant.
   - Sortie TP (partielles) : voir trade_partials, prix réel vs niveau TP.
2. Reporte pour chaque (actif, type de jambe) : n, médiane, moyenne,
   écart-type et 90e percentile du slippage observé, en unités de prix
   ET en fraction du spread moyen de l'actif.
3. Compare le résultat au modèle actuel (1,0 × spread sur chaque jambe).

CAVEAT CRITIQUE — À TRAITER EXPLICITEMENT, PAS À IGNORER

Ces fills viennent d'un compte DÉMO. Les moteurs démo remplissent
souvent de façon idéalisée (au prix demandé, sans slippage, sans refus
en cas de trou de liquidité). Une calibration sur données démo donne
donc une BORNE BASSE du coût réel, pas le coût réel.

Conséquence obligatoire : ne propose pas un modèle calibré au plus
juste. Propose un modèle mesuré PLUS une marge de sécurité explicite,
et documente le raisonnement. Si les fills démo montrent un slippage
strictement nul sur toutes les jambes, dis-le clairement — c'est le
signe que la donnée démo ne peut pas servir à calibrer le slippage, et
il faudra alors garder une hypothèse a priori conservatrice sur la
seule jambe stop.

CONTRAINTES

- PRÉ-ENREGISTREMENT d'abord dans docs/HYPOTHESES.md : méthode de
  mesure et règle de décision (quelle marge de sécurité, décidée AVANT
  de voir les chiffres). Invariant #10.
- Ceci est une correction de FIDÉLITÉ DE SIMULATION, pas un paramètre
  de stratégie : elle ne consomme aucune variable au budget §2.11.
  Écris-le explicitement dans le journal pour qu'aucune session future
  ne recompte ce chantier comme une variable dépensée.
- backtest_engine.py est un module critique : couverture 100% exigée
  après modification, tests produits en même temps que le code.
- CONSÉQUENCE RÉTROACTIVE À ANNONCER : tous les chiffres NETS produits
  du 24 au 26/08 deviennent invalides avec le nouveau modèle. Ne les
  efface pas. Ajoute une entrée qui dit lesquels doivent être
  recalculés, et recalcule au minimum le tableau coût/R par couple
  (référence de conception du 25/08).
- AUCUN déploiement, aucun changement de stratégie dans ce chantier.

LIVRABLES

1. Tableau du slippage réel mesuré par (actif, type de jambe), avec n.
2. Verdict sur l'utilisabilité des fills démo pour cette calibration.
3. Nouveau modèle de coûts proposé + marge de sécurité justifiée.
4. Tableau coût/R recalculé, comparé à celui du 25/08.
5. Entrées docs/HYPOTHESES.md et docs/DECISIONS.md.
```

---

# CHANTIER 3 — H2 : audit mécanique du déclencheur

```
Assistant Trading — Chantier : audit mécanique du déclencheur H2.
Diagnostic uniquement, aucune évaluation de performance.

PROBLÈME

Le diagnostic coût/edge du 25/08 rapporte pour H2 : n = 0 à 2 trades
par actif sur 2 ans d'historique M15. Sur ~50 000 bougies par actif,
c'est un niveau de restriction anormal. Aucune conclusion de
performance n'est possible sur un tel échantillon — et aucune n'a été
tirée, correctement.

L'hypothèse à trancher n'est donc PAS "H2 est-elle rentable" mais
"pourquoi H2 ne se déclenche-t-elle presque jamais". Deux causes
possibles, à distinguer par la mesure :
  (a) un bug — une condition qui n'est jamais vraie, un comparateur
      inversé, un index décalé, une fenêtre mal dimensionnée ;
  (b) une conjonction de confluences trop restrictive — chaque filtre
      est correct isolément, mais leur produit tend vers zéro.

MÉTHODE — INSTRUMENTATION, PAS OPTIMISATION

1. Instrumente hypothesis2_strategy.evaluate_entry (et ict_strategy si
   H2 le réutilise) pour compter, sur l'historique M15 complet des 8
   actifs, combien de fois CHAQUE condition élémentaire est satisfaite
   ISOLÉMENT : régime, fractale K=2, cassure de structure, niveaux de
   Fibonacci, FVG, MA200, et toute autre condition présente.
2. Compte ensuite les conjonctions cumulatives, dans l'ordre où le code
   les évalue : cond1, puis cond1 ET cond2, puis cond1 ET cond2 ET
   cond3, etc. Cela montre exactement à quelle étape l'entonnoir se
   ferme.
3. Compte séparément les signaux GÉNÉRÉS et les trades COMPLÉTÉS —
   l'écart mesure ce que le garde-fou "un seul trade actif à la fois"
   absorbe (mécanisme déjà identifié comme significatif le 25/08).
4. Rapporte le tout sous forme d'entonnoir chiffré, par actif et poolé.

RÈGLE DE LECTURE, FIXÉE AVANT DE VOIR LES CHIFFRES

- Une condition satisfaite 0 fois sur 50 000 bougies = presque
  certainement un bug. À investiguer ligne par ligne, pas à assouplir.
- Une condition satisfaite souvent isolément mais dont la conjonction
  avec la précédente tombe à ~0 = piège de confluence. C'est une
  décision de conception à me remonter, pas à corriger seul.

CONTRAINTES

- AUCUN paramètre modifié dans ce chantier. Aucun assouplissement de
  seuil, aucun candidat testé, aucune mesure d'espérance. C'est un
  diagnostic ; toute modification issue de ce diagnostic fera l'objet
  d'un chantier séparé, pré-enregistré.
- Si tu trouves un bug réel, NE LE CORRIGE PAS dans la foulée :
  rapporte-le d'abord avec la preuve (la ligne, la condition, le
  compte). Je tranche ensuite. Même discipline que pour le bug de
  positions simultanées du 21/08.
- Script de recherche ponctuel, aucune écriture DB, aucun appel réseau.

LIVRABLE

Entonnoir chiffré complet + verdict argumenté (bug / confluence /
les deux), dans docs/DECISIONS.md. Aucune décision de refonte prise
dans ce chantier.
```

---

# CHANTIER 4 — H1 : univers élargi + rétractation du pré-enregistrement du 26/08

```
Assistant Trading — Chantier H1 : deux volets, une seule session.
À exécuter APRÈS le chantier de recalibration des coûts.

VOLET A — Rétracter proprement le pré-enregistrement H1/HOUR_4 du 26/08

Une entrée du 26/08 dans docs/HYPOTHESES.md pré-enregistre un chantier
H1 avec un unique candidat "B_HOUR_4". Ce candidat a été choisi parce
qu'il était l'étape suivante évidente, pas parce que sa taille d'effet
était plausible — le même axe avait déjà échoué pour H3 et H5.

NE SUPPRIME PAS cette entrée et ne la modifie pas. Écris une entrée
NOUVELLE qui la SUPERSÈDE explicitement, en disant pourquoi. L'intégrité
du registre de pré-enregistrement vaut plus que l'élégance de son
contenu : un registre qu'on réécrit après coup ne protège plus de rien.

VOLET B — Élargir l'univers d'actifs de H1

RAISONNEMENT

H1 (Donchian(20) + MA200, HOUR) est la seule hypothèse du projet
appartenant à une famille dont l'edge est documenté hors échantillon
sur des décennies (suivi de tendance / time-series momentum). Le
diagnostic du 26/08 donne un edge BRUT positif sur USDJPY, GBPUSD et
EURUSD ; US30 n'a pas d'edge même à coût nul.

Le suivi de tendance se renforce par la DIVERSIFICATION entre marchés
peu corrélés, pas par l'ajout de filtres. L'action à mener est donc
d'augmenter le nombre de marchés, pas de raffiner le déclencheur.

MÉTHODE

1. US30 sort du pool H1 de ce chantier (Branche B, pas d'edge brut).
2. Propose-moi une liste d'instruments disponibles chez Capital.com,
   compatibles avec la logique H1 (elle n'exige aucune confirmation
   croisée, contrairement à H3/H4 — elle est donc portable sur
   n'importe quel instrument liquide). Vise la DÉCORRÉLATION :
   devises, matières premières, indices de zones différentes.
   Attends ma validation avant tout téléchargement.
3. Rejoue H1 inchangée sur l'univers élargi, avec le modèle de coûts
   RECALIBRÉ du chantier précédent.

PIÈGE STATISTIQUE À TRAITER EXPLICITEMENT — NE PAS L'IGNORER

Ajouter des actifs corrélés augmente n sans augmenter l'INFORMATION.
US30 et US100 sont deux indices actions américains : leurs trades ne
sont pas des observations indépendantes, et les traiter comme telles
sous-estime l'erreur-type et fabrique de la significativité qui
n'existe pas.

Obligation : calcule et rapporte la matrice de corrélation des
rendements par trade entre actifs, et estime une taille d'échantillon
EFFECTIVE tenant compte de cette corrélation. Utilise le n effectif,
jamais le n brut, dans le calcul de la borne basse et du MDE. Si tu ne
sais pas estimer proprement le n effectif, dis-le et propose une
approche conservatrice explicite plutôt qu'un chiffre flatteur.

CONTRAINTES

- PRÉ-ENREGISTREMENT d'abord : univers retenu, découpage temporel,
  critère de qualification, MDE attendu sur n effectif.
- AUCUN nouveau paramètre de stratégie. Élargir l'univers ne consomme
  aucune variable au budget §2.11 — écris-le explicitement.
- Aucun paramétrage par-actif : tous les actifs restent poolés sur les
  mêmes paramètres (décision du 25/08, maintenue).
- PORTE DE PUISSANCE : calcule le MDE avec sigma(R) MESURÉ et n
  EFFECTIF avant de conclure. Si le MDE dépasse l'effet observé,
  dis-le au lieu de conclure.
- Un seul essai de validation. Aucun déploiement automatique : tu
  rapportes, je tranche.

LIVRABLES

1. Entrée de supersession du pré-enregistrement du 26/08 (volet A).
2. Liste d'instruments proposée, en attente de ma validation.
3. Après validation : matrice de corrélation, n brut vs n effectif,
   MDE, puis résultat entraînement et, si qualifié, validation unique.
```

---

# CHANTIER 5 — H5 / HOUR_4  *(seulement si H3/HOUR_4 a qualifié)*

```
Assistant Trading — Chantier H5 sur HOUR_4.
NE PAS EXÉCUTER si H3/HOUR_4 n'a pas qualifié au chantier 1.

CONDITION D'ENTRÉE — VÉRIFIE-LA AVANT TOUT

Ce chantier n'a de sens que si H3/HOUR_4 a qualifié. Raison : H3 sur
HOUR_4 mesure +0,1341R, H5 sur HOUR_4 mesure +0,0513R. Si l'effet le
plus fort des deux ne survit pas à un échantillon suffisant, le plus
faible n'a aucune chance. Si H3 a échoué, arrête-toi et dis-le.

POINT DE BUDGET — CORRECTION D'UNE ERREUR DE MA PART

Il a été écrit ailleurs que H5, étant à 5/5 variables, ne peut plus
bouger. C'est faux et il faut le corriger dans le journal : le plafond
de l'invariant #10 bloque l'ajout de variables NOUVELLES, pas le
RE-RÉGLAGE d'une variable déjà comptée. Or "résolution des bougies" est
déjà la variable #4 de H5 depuis la V3 du 24/08. Passer sa valeur de
MINUTE_15 à HOUR_4 ne consomme donc AUCUNE variable supplémentaire :
H5 reste à 5/5, elle ne passe pas à 6/5.

Vérifie ce raisonnement dans docs/HYPOTHESES.md avant de l'appliquer,
et corrige explicitement l'affirmation erronée dans docs/DECISIONS.md.

OBJECTIF

Rejouer H5 inchangée sur HOUR_4, avec l'historique étendu obtenu au
chantier 1 et le modèle de coûts recalibré, pour déterminer si son
effet de +0,0513R survit à un échantillon suffisant.

CONTRAINTES

- PRÉ-ENREGISTREMENT d'abord. Candidat unique (m=1) : HOUR_4 contre la
  référence M15 actuelle. Aucun autre axe exploré dans ce chantier.
- PORTE DE PUISSANCE, appliquée strictement : à +0,0513R, il faut
  environ 2 350 trades pour valider à 95% / puissance 80%. Calcule le n
  réellement atteignable AVANT de lancer le backtest. Si tu ne peux pas
  atteindre cet ordre de grandeur, ARRÊTE-TOI et dis-le — ne lance pas
  un test qui ne peut pas conclure, et ne baisse pas le seuil pour
  produire un résultat.
- Mesure sigma(R) réel plutôt que de supposer 1,0, et recalcule le n
  requis avec la valeur mesurée. Si sigma est nettement inférieur à 1,0
  (structure TP1/TP2 à variance réduite), le n requis baisse
  proportionnellement au carré — c'est le seul chemin qui rend ce
  chantier faisable, rapporte-le honnêtement dans un sens comme dans
  l'autre.
- Déclencheur (structure ICT + RSI) strictement inchangé. TP1/TP2
  inchangés.
- Un seul essai de validation. Aucun déploiement automatique.
```

---

# CHANTIER 6 — Filtre killzone sur H3  *(seulement si H3/HOUR_4 a échoué)*

```
Assistant Trading — Chantier : filtre killzone sur H3.
NE PAS EXÉCUTER si H3/HOUR_4 a qualifié au chantier 1 (dans ce cas,
c'est le chantier H5 qui suit).

JUSTIFICATION THÉORIQUE — ÉCRITE AVANT TOUTE DONNÉE (invariant #10)

Le 23/08, la fenêtre de session a été retirée comme porte sur la
GÉNÉRATION de signaux, pour toutes les hypothèses (voir la docstring
de technical_strategy_executor.py et docs/DECISIONS.md). Depuis, H2, H3
et H5 — toutes trois dérivées d'ICT — génèrent des signaux 24h sur 24.

La méthodologie ICT / Smart Money est explicitement ancrée dans le
temps : les killzones de Londres et de New York sont les fenêtres où se
concentre la participation institutionnelle. La prémisse causale des
setups ICT (un flux institutionnel qui crée des inefficiences de prix
puis revient les combler) ne tient pas à 02:00 UTC sur un indice
américain. Générer ces signaux hors killzone dilue mécaniquement l'edge
vers zéro en mélangeant des observations où la prémisse tient et des
observations où elle ne tient pas.

C'est une hypothèse théorique antérieure aux données, issue de la
méthodologie elle-même, pas un motif trouvé dans les chiffres.

POURQUOI CE TEST EST FAISABLE ALORS QUE LES AUTRES NE L'ÉTAIENT PAS

C'est une hypothèse à EFFET LARGE testée sur l'échantillon COMPLET
(n≈2900 pour H3 sur M15, MDE≈0,046R), et non un réglage marginal testé
sur un sous-échantillon réduit. C'est la première fois de tout ce cycle
que la taille d'effet attendue et la puissance disponible sont
compatibles.

MÉTHODE

1. Sur l'historique M15 complet de H3, configuration ACTUELLE inchangée
   (pas de stop ATR×20 — cet axe est abandonné, il détruisait
   l'échantillon), partitionne les trades en deux groupes selon
   l'heure UTC du SIGNAL :
     - dans killzone : 07:00-10:00 UTC (Londres) ou 12:00-15:00 UTC (NY)
     - hors killzone : tout le reste
2. Calcule pour chaque groupe : n, espérance nette, sigma(R), borne
   basse à 95%.
3. Teste la différence entre les deux groupes, pas seulement le signe
   de chacun.

BORNES NON OPTIMISÉES — RÈGLE STRICTE

Utilise EXACTEMENT les bornes ci-dessus (07:00-10:00 et 12:00-15:00
UTC), qui sont les killzones ICT standard, définies hors de ce projet.
N'explore AUCUNE variante d'horaire, n'ajuste AUCUNE borne, même de 30
minutes, même si le résultat est proche du seuil. Une borne choisie sur
les données serait de l'optimisation déguisée et invaliderait tout le
test. Si les bornes standard ne produisent rien, la réponse est "non",
pas "essayons 06:30".

CONTRAINTES

- PRÉ-ENREGISTREMENT d'abord : bornes, critère, MDE attendu.
- BUDGET : le filtre de session compte comme UNE variable pour H3.
  Vérifie le budget actuel de H3 dans docs/HYPOTHESES.md et confirme
  qu'il reste de la marge sous le plafond de 5 AVANT de lancer quoi que
  ce soit. Si H3 est au plafond, arrête-toi et dis-le.
- Un seul essai de validation, un seul jeu de bornes, aucune reprise.
- Aucun déploiement automatique : tu rapportes, je tranche.

LIVRABLE

Comparaison chiffrée dans/hors killzone (n, espérance, sigma, borne
basse) + verdict. Si l'écart est net et favorable, ce sera la première
piste théorique validée du projet — rapporte-le sans l'amplifier, avec
sa taille d'échantillon et son intervalle.
```

---

# Rappel — les trois règles à ne pas relâcher

1. **Porte de puissance avant chaque test.** Calculer le MDE de l'échantillon attendu ; si l'effet espéré est en dessous, ne pas lancer le test.
2. **Pré-enregistrement avant toute donnée.** Un registre réécrit après coup ne protège plus de rien.
3. **Un filtre qui réduit `n` réduit ta capacité à savoir si tu as raison.** Chaque confluence ajoutée doit être payée en échantillon, et ce prix doit être calculé avant, pas constaté après.
