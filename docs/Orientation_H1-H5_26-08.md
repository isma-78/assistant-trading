# ASSISTANT TRADING — Orientation stratégique H1→H5 et prompt d'exécution

**Date** : 26/08/2026 — **Statut** : révision critique de la session précédente + orientation définitive
**À lire avant tout** : ce document contredit une partie de ce que j'ai produit plus tôt aujourd'hui.

---

# PARTIE 0 — AUTOCRITIQUE DE MA RÉPONSE PRÉCÉDENTE

Quatre erreurs, dont une majeure.

### 1. J'ai construit l'outil au lieu de poser la question (erreur principale)

J'ai passé la session à écrire `evolution_engine.py` — une machine à **relancer des recherches de paramètres** — alors que la donnée déjà présente dans `DECISIONS.md` démontrait que **cette recherche est mathématiquement incapable d'aboutir**. J'ai automatisé la boucle au lieu de vérifier si la boucle pouvait fonctionner. Le module reste utile, mais il arrive au mauvais moment et pour le mauvais usage.

### 2. Je n'ai pas fait le calcul de puissance statistique

Il était calculable en 30 secondes depuis les chiffres que j'avais sous les yeux. Il est décisif. Je ne l'ai pas fait.

### 3. Je n'ai pas questionné le modèle de coûts

J'ai lu `backtest_engine.py` ligne par ligne, y compris `entry_execution_price`, sans relever qu'il **applique du slippage à un ordre limite** — ce qui est structurellement impossible. Un ordre limite est exécuté à ton prix ou mieux, ou pas du tout. C'est une erreur de conception visible à l'œil nu pour quiconque a passé des ordres.

### 4. Le candidat H1/HOUR_4 que j'ai pré-enregistré est faible

Je l'ai choisi parce que c'était l'étape suivante évidente, pas parce que c'était la meilleure. Et je l'ai inscrit au registre permanent du projet, ce qui rend sa rétractation coûteuse. **Il doit être remplacé avant d'être exécuté** (procédure au Chantier 4).

### Ce qui tient

Refus de la boucle par trade ; refus de rejouer des axes déjà négatifs ; déclenchement manuel ; H1-dans-Option-B identifié comme conception d'origine et non bug ; couverture 100 %.

---

# PARTIE 1 — DIAGNOSTIC : POURQUOI UNE SEMAINE DE RECHERCHE N'A RIEN DONNÉ

## Constat A — La question posée est hors de portée des données (décisif)

L'effet minimum détectable à 95 % / puissance 80 % vaut `MDE = 2,485 × σ / √n`. Avec σ(R) ≈ 1,0 :

| n (échantillon) | Effet minimum détectable |
|---|---|
| 150 (seuil d'entraînement utilisé) | **0,203 R** |
| 205 (H3, stop ATR×20) | 0,174 R |
| 2 934 (H3, échantillon complet) | 0,046 R |

Or les espérances **brutes** (coûts nuls — le plafond théorique absolu) valent :

| Configuration | Espérance brute | n nécessaire pour la valider |
|---|---|---|
| H3 global M15 | +0,0237 R | **11 000 trades** |
| H5 global M15 | +0,0139 R | **32 000 trades** |
| H3 stop ATR×20 (train) | +0,0142 R | **30 700 trades** |
| **H3 sur HOUR_4 (train)** | **+0,1341 R** | **344 trades** |

**Toute la semaine a consisté à demander si un effet de 0,014 R franchissait un seuil de détection de 0,20 R.** C'est un écart d'un facteur 12. Le protocole a correctement répondu « non » à chaque fois — il ne pouvait rien répondre d'autre.

## Constat B — Le modèle de coûts surestime d'un facteur ~3

Dans `backtest_engine.py` :

```
entrée (long) = open_ask + spread × 1,0   = mid + 1,5 spread
sortie (long) = niveau − spread/2 − spread × 1,0 = niveau − 1,5 spread
                                          → 3,0 spreads par aller-retour
```

La réalité, avec la conception actuelle (§2.8, `place_limit_order` — **ordres limite uniquement**) :

- **Entrée limite** : exécutée à ton prix ou mieux. Un ordre limite ne subit **jamais** de slippage défavorable. Coût réel ≈ 0,5 spread.
- **Sortie stop** : ordre au marché, slippage réel possible. ≈ 0,5 spread + slippage.
- **Sortie TP** : ordre limite. Pas de slippage défavorable.

Soit **≈ 1,0 spread aller-retour + slippage réel sur la seule jambe stop**, contre 3,0 spreads facturés. Le modèle facture du slippage sur deux jambes qui, par construction, ne peuvent pas slipper.

**Mais attention — corriger cela ne sauve rien sur M15** :

| | brut | net actuel | net si coût ÷ 3 |
|---|---|---|---|
| H3 | +0,0237 R | −0,1012 R | **−0,0179 R** |
| H5 | +0,0139 R | −0,2092 R | **−0,0605 R** |
| H4 | −0,0199 R | −0,2878 R | −0,1092 R |

Le modèle est faux et doit être corrigé pour que les décisions futures soient justes. Il n'est pas la cause de l'échec.

## Constat C — Le stop ATR×20 a détruit l'échantillon pour un gain cosmétique

Objectif affiché : ramener coût/R sous 5 %. Mécanisme réel : élargir R au dénominateur. Le ratio baisse, l'edge par trade ne monte pas, et **n s'effondre de 2 934 à 205** (−93 %) parce que les positions durent bien plus longtemps et se heurtent au « un trade actif à la fois ».

Résultat : on a échangé 93 % de la puissance statistique contre une amélioration d'affichage. Le MDE est passé de 0,046 R à 0,174 R — **la question est devenue quatre fois moins répondable qu'avant le « correctif »**.

## Constat D — Le signal ignoré : HOUR_4 a été rejeté quatre fois pour la mauvaise raison

| Test | Résultat | Verdict appliqué |
|---|---|---|
| H3 HOUR_4 (25/08) | **+0,1341 R**, n=121 | rejeté — n < 150 |
| H3 HOUR_4 sans BTC (26/08) | **+0,1350 R**, n=101 | rejeté — n < 150 |
| H5 HOUR_4 (25/08) | +0,0513 R, n=134 | rejeté — n < 150 |

**H3 sur HOUR_4 est la seule configuration de toute la semaine dont la taille d'effet (~0,134 R) se situe dans la zone exploitable** — c'est-à-dire supérieure au coût réel et validable avec un échantillon atteignable (344 trades, pas 11 000).

Elle a été écartée **non pas sur son mérite, mais sur une contrainte de découpage d'échantillon**. C'est l'inverse du raisonnement à tenir : quand un effet fort apparaît avec trop peu d'observations, on va chercher des observations, on ne jette pas l'effet.

## Constat E — Erreurs de conception « Smart Money » (secondaire mais réel)

- **Filtre de session supprimé le 23/08** pour toutes les hypothèses. H2, H3, H5 sont dérivées d'ICT, méthodologie explicitement ancrée dans le temps (killzones Londres ~07:00-10:00 UTC, New York ~12:00-15:00 UTC). Détecter des FVG/BOS/CHoCH à 02:00 UTC sur US30 génère des signaux dans un régime où la prémisse — le flux institutionnel qui crée puis comble les inefficiences — ne tient pas. C'est une dilution structurelle de l'edge, pas une hypothèse post-hoc.
- **Cibles en multiples de R fixes sur des setups ICT.** Les cibles ICT sont des poches de liquidité (plus haut/bas de séance précédente, equal highs/lows, FVG opposé). TP1=1R / TP2=2R est une structure de suiveur de tendance appliquée à une méthode chercheuse de liquidité.
- **Piège de la confluence** (visible sur H2, n=1-2 sur 2 ans) : chaque confluence ajoutée multiplie les probabilités à la baisse. Régime × canal × Fibonacci × FVG × MA200 × fractale K=2 → l'échantillon tend vers zéro, et l'on se garantit de ne jamais rien pouvoir valider. **Un filtre qui réduit n réduit ta capacité à savoir si tu as raison.**

---

# PARTIE 2 — ORIENTATION PAR HYPOTHÈSE

## Règle transversale à adopter — porte de puissance (avant tout test)

> **Aucun test n'est lancé sans avoir calculé, avant, le MDE de l'échantillon attendu. Si l'effet espéré est inférieur au MDE, le test n'est pas exécuté** — il ne peut produire qu'un « non » non informatif, et il consomme du budget de comparaisons multiples pour rien.

Corollaire opérationnel, à écrire dans la méthodologie : **une configuration n'est candidate que si son effet espéré est ≥ 0,10 R.** En dessous, le coût réel (0,03–0,08 R après correction) mange l'edge et l'échantillon nécessaire dépasse ce que le projet produira jamais.

| Hyp. | Statut | Orientation | Priorité |
|---|---|---|---|
| **H3** | Meilleur brut ICT ; **HOUR_4 = +0,134 R** | **Chantier principal.** Résoudre le manque d'échantillon sur HOUR_4 (historique + actifs), pas chercher un paramètre. | **1** |
| **H1** | Suiveur de tendance ; brut positif sur USDJPY/GBPUSD/EURUSD | Seule famille à edge documenté sur des décennies. Élargir l'univers d'actifs (n ↑ et décorrélation), pas ajouter des filtres. US30 abandonné. | **2** |
| **H2** | n = 1-2 sur 2 ans | **Audit mécanique, pas évaluation.** n=1 sur ~50 000 bougies M15 est un niveau de restriction anormal — suspicion de bug, pas de sélectivité. Aucune conclusion possible avant. | 3 |
| **H5** | Brut +0,0139 R ; budget **5/5 saturé** | **Gelée.** Variante strictement dominée par H3 (même prémisse ICT, edge plus faible, aucune marge de variable). Elle héritera du verdict de H3 sans consommer de cycle. | gel |
| **H4** | Brut **négatif** (−0,0199 R) | **Close. Ne pas rouvrir.** La Branche B était le bon appel. | close |

**Les deux chantiers 1 et 2 ne sont pas des recherches de paramètres.** Ce sont des chantiers d'**acquisition de données** : le blocage n'est pas « quel réglage », c'est « pas assez d'observations là où l'effet est fort ».

---

# PARTIE 3 — PROMPT À COLLER DANS CLAUDE CODE

> Un seul chantier par session. Coller le bloc ci-dessous tel quel dans la session Claude Code connectée au VPS.

```
Assistant Trading — Chantier prioritaire : H3 sur HOUR_4, résoudre le
manque d'échantillon.

CONTEXTE — À VÉRIFIER AVANT D'AGIR

Relis docs/DECISIONS.md et docs/HYPOTHESES.md (entrées du 25 et 26/08).
Trois constats issus d'une revue quantitative externe, à confirmer ou
infirmer toi-même avant de continuer — si l'un est faux, dis-le et
arrête-toi :

1. PUISSANCE STATISTIQUE. MDE = 2,485 × sigma / racine(n), à 95%
   unilatéral et 80% de puissance. Avec sigma(R) ~ 1,0 : n=150 ne
   détecte qu'un effet >= 0,203R. Les espérances brutes M15 de H3
   (+0,0237R) et H5 (+0,0139R) exigeraient respectivement ~11 000 et
   ~32 000 trades pour être validées. Toute la recherche de la semaine
   portait donc sur des effets 10 à 15 fois sous le seuil de détection
   de son propre protocole.

2. HOUR_4 A ÉTÉ REJETÉ POUR LA MAUVAISE RAISON. H3 sur HOUR_4 :
   +0,1341R (n=121) le 25/08, +0,1350R (n=101) sans BTCUSD le 26/08.
   Rejeté les deux fois sur n<150, jamais sur son mérite. C'est la
   SEULE configuration de la semaine dont la taille d'effet est dans la
   zone exploitable : elle ne demanderait que ~344 trades pour être
   validée, contre 11 000 pour la version M15.

3. LE STOP ATR×20 A DÉTRUIT L'ÉCHANTILLON. Il abaisse le ratio coût/R
   en gonflant R au dénominateur, sans augmenter l'edge par trade, et
   fait chuter n de 2934 à 205 (-93%). Le MDE passe de 0,046R à 0,174R.
   Cet axe est abandonné, pas rejoué.

OBJECTIF DE CE CHANTIER

Amener H3/HOUR_4 à un échantillon d'entraînement >= 150 trades SANS
toucher à un seul paramètre de la stratégie. Le blocage est un manque
d'observations, pas un mauvais réglage. Deux leviers, dans cet ordre :

LEVIER 1 — Étendre l'historique.
  Vérifie jusqu'où Capital.com sert de l'historique HOUR_4 (l'API peut
  remonter plus loin que les 2 ans actuellement téléchargés). Si oui,
  télécharge le maximum disponible via scripts/download_historical_data.py
  --resolutions HOUR_4. Reporte-moi la profondeur réelle obtenue par
  actif AVANT de lancer le moindre backtest.

LEVIER 2 — Élargir l'univers d'actifs.
  Uniquement si le levier 1 ne suffit pas. Ajouter des actifs augmente n
  et diversifie ; ce n'est PAS un paramétrage par-actif (décision du
  25/08 maintenue : tous les actifs restent poolés, mêmes paramètres).
  Propose-moi une liste d'instruments disponibles chez Capital.com,
  cohérents avec la logique H3 (régime croisé US30/US100), et laisse-moi
  trancher avant de télécharger.

CONTRAINTES NON NÉGOCIABLES

- PRÉ-ENREGISTREMENT D'ABORD. Écris l'entrée docs/HYPOTHESES.md AVANT
  tout calcul : leviers retenus, découpage temporel (le CUTOFF devra
  être recalculé si l'historique s'allonge — annonce la règle de calcul
  avant de connaître les données), critère de qualification, et le MDE
  attendu pour le n visé. Invariant #10.
- AUCUN nouveau paramètre de stratégie. Le déclencheur H3 (MA200 +
  Donchian(20) + confirmation de régime croisée) reste strictement
  inchangé. Ce chantier ne consomme AUCUNE variable au budget.
- PORTE DE PUISSANCE. Avant de lancer le backtest, calcule et affiche
  le MDE de l'échantillon que tu vas obtenir. Si le MDE dépasse 0,10R,
  arrête-toi et dis-le — inutile de lancer un test qui ne peut pas
  conclure.
- Mesure sigma(R) réellement observé sur les trades du backtest plutôt
  que de supposer 1,0, et recalcule le MDE avec la valeur mesurée.
- Un seul essai de validation, jamais deux.
- AUCUN déploiement automatique dans ce chantier. Tu me rapportes le
  résultat, je tranche. (Écart explicite à la règle d'auto-déploiement
  du 25/08 : elle avait été accordée pour des ajustements de paramètre,
  pas pour un changement de résolution sur un historique élargi.)

LIVRABLES

1. Profondeur d'historique HOUR_4 réellement disponible, par actif.
2. n d'entraînement atteignable pour H3/HOUR_4, et MDE correspondant
   avec sigma mesuré.
3. Si et seulement si le MDE le permet : résultat entraînement puis, si
   qualifié, un unique essai de validation.
4. Entrées docs/HYPOTHESES.md (pré-enregistrement) et docs/DECISIONS.md
   (résultat), comme d'habitude.

Si un des trois constats du contexte est faux, dis-le-moi et arrête-toi
avant d'exécuter quoi que ce soit.
```

---

# PARTIE 4 — CHANTIERS SUIVANTS (un par session, dans cet ordre)

### Chantier 2 — Recalibrer le modèle de coûts sur les exécutions réelles

Ne pas remplacer une opinion (3 spreads) par une autre (1 spread). **Mesurer.** La base contient des exécutions démo réelles : comparer `trades.prix_entree_reel` au mid au moment du signal donne le slippage réellement subi, par actif et par type de jambe (entrée limite vs sortie stop vs sortie TP).

Point de méthode : c'est une **correction de fidélité de simulation**, pas un paramètre de stratégie — elle ne consomme aucun budget de variable, mais elle invalide rétroactivement les chiffres nets de la semaine, qui devront être recalculés. Le dire explicitement dans le journal.

### Chantier 3 — H1 : élargir l'univers plutôt que filtrer

Même logique que le chantier 1. Le suivi de tendance est la seule famille à edge robuste documenté hors échantillon ; il se renforce par la **diversification entre marchés décorrélés**, pas par l'ajout de confluences. US30 sort (pas d'edge même brut). Corriger d'abord les coûts (chantier 2), puis mesurer sur un univers élargi.

### Chantier 4 — Rétracter proprement le pré-enregistrement H1/HOUR_4 du 26/08

Il est au registre permanent. Ne pas l'effacer : écrire une entrée qui le **supersède**, en disant pourquoi (candidat choisi par facilité, pas sur une taille d'effet plausible ; remplacé par le chantier 3). L'intégrité du registre vaut plus que l'élégance de son contenu.

### Chantier 5 — H2 : audit mécanique du déclencheur

n=1-2 sur ~50 000 bougies. Instrumenter le déclencheur pour compter combien de fois **chaque** condition passe isolément, puis en conjonction. Objectif : distinguer un bug d'une conjonction de confluences trop restrictive. Aucune conclusion sur H2 avant ce diagnostic.

### Chantier 6 — Le filtre killzone (seulement si H3/HOUR_4 échoue)

Hypothèse à effet large, donc testable avec puissance sur l'échantillon M15 complet (n≈2900, MDE≈0,046 R) : l'espérance à l'intérieur des killzones Londres/New York est-elle matériellement supérieure à celle du reste de la journée ? Utiliser les **bornes ICT standard, non optimisées** (07:00–10:00 et 12:00–15:00 UTC) — une borne choisie sur les données serait de l'optimisation déguisée.

---

# PARTIE 5 — CE QUE JE NE PEUX PAS TE PROMETTRE

L'analyse ci-dessus explique pourquoi la semaine a échoué et où chercher ensuite. Elle **ne dit pas** que H3/HOUR_4 va fonctionner.

Ce que les données disent : c'est la seule piste dont la taille d'effet soit à la fois supérieure au coût réel et validable avec un échantillon atteignable. Ce qu'elles ne disent pas : que +0,134 R sur 121 trades survivra à 344 trades. Un effet mesuré sur un petit échantillon régresse le plus souvent vers la moyenne — c'est le comportement attendu, pas l'exception.

Le scénario le plus probable reste que HOUR_4 s'affaisse aussi une fois l'échantillon suffisant. Dans ce cas la réponse honnête du projet sera : **aux spreads CFD retail, aucune des cinq hypothèses ne produit d'edge exploitable** — ce qui est un résultat, mesuré proprement, et le livrable annoncé de la phase A.
