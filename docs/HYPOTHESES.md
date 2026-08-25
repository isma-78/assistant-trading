# Journal des hypothèses — Flux B (stratégie technique interne)

Ce fichier journalise les hypothèses de trading **propres au système**
(indépendantes du canal Station X), conformément au §2.11 du CDC
("Stratégie technique complémentaire") et à l'invariant #10
(anti-surapprentissage). Chaque entrée est écrite et datée **avant toute
observation de résultat** — c'est la règle, pas une formalité : une
hypothèse rédigée après avoir regardé les trades qu'elle aurait produits
n'a aucune valeur, elle serait ajustée sans le savoir sur le bruit.

**Règle de modification** : aucune hypothèse de ce fichier n'est jamais
ajustée en cours de route sur la base de ses résultats. Toute évolution
(paramètre, principe, actifs concernés) exige une **nouvelle entrée
datée** ci-dessous et un redéploiement — jamais un ajustement automatique
ni une modification en direct du code déjà en production. L'entrée
d'origine reste dans ce fichier, jamais réécrite ni supprimée : l'historique
des hypothèses a de la valeur en soi (§3.8).

---

## Hypothèse #1 — 20/08/2026

**Statut** : proposée, en attente de validation d'Ismaël. Aucun code
d'exécution n'existe encore pour cette hypothèse à la date de rédaction.

### Contexte

Depuis le lancement du palier P1, Station X n'a publié aucun signal
structuré sur 5 des 8 actifs de la liste blanche : US30, EURUSD, GBPUSD,
USDJPY, ETHUSD. Cette hypothèse vise à générer des signaux d'entrée
déterministes propres au système sur ces 5 actifs, pour accumuler des
données de trading démo indépendamment du canal — sans copier ni deviner
le style de Station X, et sans mélanger les résultats des deux sources
(voir Phase 2, enveloppe et étiquetage séparés).

### Principe retenu : filtre de tendance (MA200) + déclencheur de
rupture de canal (Donchian)

Architecture en deux niveaux, reprenant exactement la structure prescrite
par le CDC §2.11 ("régime de fond" + "déclencheur") :

**Niveau 1 — Régime de fond (filtre de tendance)**
Moyenne mobile simple sur 200 périodes (bougies horaires). Le régime
n'autorise qu'un seul sens :
- Prix de clôture courant > MA(200) → régime haussier, **long autorisé
  uniquement**
- Prix de clôture courant < MA(200) → régime baissier, **short autorisé
  uniquement**
- Égalité stricte (cas dégénéré, improbable en pratique) → aucun régime,
  aucune entrée ce cycle

*Justification théorique* : le filtrage par moyenne mobile longue est
l'une des approches systématiques les plus documentées en finance
empirique — la prime de momentum/tendance est répliquée dans de
nombreuses études indépendantes, sur des classes d'actifs et des
périodes très variées (ex. Moskowitz, Ooi & Pedersen, *"Time Series
Momentum"*, Journal of Financial Economics, 2012 ; usage classique en
allocation tactique, ex. Faber, *"A Quantitative Approach to Tactical
Asset Allocation"*, 2007, qui utilise précisément une MA(200) comme
filtre de régime sur indices/actions). Le choix de 200 périodes n'est
pas un paramètre libre ici : c'est la valeur **explicitement prescrite
par le CDC lui-même** (§2.11 : "moyenne mobile longue (200)"), pas un
choix a priori que je pourrais ajuster.

**Niveau 2 — Déclencheur (rupture de canal de Donchian)**
À l'intérieur du régime autorisé, entrée à la rupture d'un canal de
Donchian sur **N périodes** (bougies horaires) :
- Régime haussier + clôture > plus haut des N dernières bougies (hors
  bougie courante) → **entrée long**, au niveau de la rupture
- Régime baissier + clôture < plus bas des N dernières bougies → **entrée
  short**, au niveau de la rupture

Le stop initial est placé sur la **borne opposée du même canal** (plus
bas des N bougies pour un long, plus haut pour un short) — pas un
paramètre supplémentaire, la même fenêtre N sert aux deux usages.

*Justification théorique* : la rupture de canal de Donchian est le
mécanisme d'entrée central du système *"Turtle Trading"* (Richard
Dennis & William Eckhardt, 1983), l'un des systèmes de trading
systématique les plus documentés et étudiés publiquement (voir Curtis
Faith, *"Way of the Turtle"*, 2007, pour le détail des règles
originales). L'association filtre de tendance long terme + déclencheur
de rupture court terme est une architecture standard décrite dans la
littérature de trading systématique (ex. Andreas Clenow, *"Following the
Trend"*, 2012 ; Perry Kaufman, *"Trading Systems and Methods"*, éditions
successives). Aucun des deux principes n'est inventé pour cette
hypothèse.

### Paramètres exacts (choisis a priori, avant observation)

| Paramètre | Valeur | Statut |
|---|---|---|
| Période de la MA de régime | 200 (bougies horaires) | **Fixe**, prescrite littéralement par le CDC §2.11 — pas une variable ajustable de cette hypothèse |
| Résolution des bougies | HOUR | Fixe, réutilise la convention déjà en place dans `market_data.py`/`executor.py` (ATR de trailing) |
| **Période du canal de Donchian (N)** | **20** (bougies horaires) | **Seul paramètre réglable de cette hypothèse** — valeur reprise du système "Turtle" original (système d'entrée court terme à 20 jours), pas ajustée sur ce projet |

**Un seul paramètre réglable au total pour cette hypothèse.** Le stop
réutilise la même fenêtre N que le déclencheur — aucun paramètre
supplémentaire pour le placement du stop.

### Budget de variables (invariant #10) — vérifié avant de fixer ce paramètre

L'invariant #10 plafonne à 5 variables sur l'ensemble du projet. Deux
lectures coexistent et sont distinguées ici pour être précis.

**Citation exacte, `docs/CDC_v4.md` §3.8** (traçabilité demandée par
Ismaël — pour qu'elle ne serve pas plus tard de prétexte à empiler des
variables sans relire le texte source) :

> ### Les 5 variables initiales — figées a priori
>
> | # | Variable | Justification théorique |
> |---|---|---|
> | 1 | Alignement avec le biais de la Matinale | Cohérence interne de la méthode |
> | 2 | Alignement avec la tendance technique objective | Suivre la tendance améliore-t-il le résultat ? |
> | 3 | Ratio gain/risque planifié | Les setups ambitieux tiennent-ils ? |
> | 4 | Proximité d'une annonce macro | Validité du filtre macro |
> | 5 | Volatilité relative à l'entrée (ATR normalisé) | Le régime conditionne-t-il la réussite ? |
>
> **Aucune variable supplémentaire sans justification théorique écrite
> préalable.** Ajouter des variables après avoir vu les données est du
> data dredging — interdit.

Cette liste de 5 est explicitement nommée et numérotée dans le CDC ; son
usage déclaré (§4.4, colonne "Déterministe" du module `adaptive_rules` ;
§3.7 : *"L'apprentissage vient entièrement de la base de données et du
code statistique"*) est celui d'un **futur module d'apprentissage
adaptatif, pas encore construit**, qui recherchera des corrélations
entre ces 5 variables et le résultat des trades — c'est ce contexte
précis (recherche de corrélation post-hoc sur un historique de trades)
que la règle des 10 trades/variable et l'interdiction du data dredging
visent. Le paramètre N=20 de cette hypothèse n'est **pas** une variable
de ce type au sens strict du §3.8 : c'est un paramètre d'une **règle de
décision d'entrée fixée a priori** (§2.11), jamais recalibré sur les
résultats qu'il produit — la distinction est faite ici explicitement,
avec la source exacte, pour qu'elle reste vérifiable et ne soit jamais
présumée par la suite. 0/5 variables du §3.8 consommées à ce jour
(`adaptive_rules` n'existe pas encore).
- **Les seuils de décision déjà en place**, au sens plus large où
  Ismaël l'entend pour ce budget (tout paramètre qui aurait pu être
  choisi autrement et qui pourrait faire l'objet d'un ajustement a
  posteriori s'il n'était pas figé) :
  - `confidence_threshold` (0,75) — seuil d'acceptation du risk_engine,
    déjà en place pour le flux Station X
  - `STALENESS_FRACTION_OF_STOP_DISTANCE` (0,5, `validator.py`) — seuil
    de péremption introduit au palier P2, documenté dans
    `docs/DECISIONS.md`

  Ces deux seuils sont **communs à tout signal** quelle que soit sa
  source (Station X ou Flux B) — cette hypothèse ne les modifie pas, ne
  les contourne pas, et n'en ajoute pas de nouveaux : un signal du Flux B
  passera par les mêmes portes, avec les mêmes seuils, que le flux
  Station X (voir Phase 2). Non comptés comme "consommés par cette
  hypothèse" puisqu'ils préexistaient et s'appliquent uniformément —
  mais listés ici pour la transparence du budget global, comme demandé.
  Non retenus dans ce compte : la période de l'ATR (14, convention
  Wilder standard déjà utilisée dans `market_data.py`, pas un choix
  ajusté pour ce projet), le délai de péremption d'ordre limite
  (`LIMIT_ORDER_EXPIRY_SECONDS`, un délai opérationnel, pas une variable
  de décision de trading), et les fractions TP1/TP2/TP3 (50/30/20%,
  prescrites littéralement par le CDC §2.10).

**Bilan** : cette hypothèse introduit **1 nouvelle variable** (N=20, le
canal de Donchian). Avec confidence_threshold et STALENESS_FRACTION déjà
en place (2), le total des seuils de décision "ajustables" du projet
passe à 3/5 dans la lecture large — sous le plafond, avec de la marge
avant tout risque de sur-ajustement croisé.

### Score de confiance du signal

Un signal du Flux B est **entièrement déterministe** (pas d'extraction
de texte, pas d'incertitude d'analyse) : si les conditions de régime et
de rupture sont remplies, le signal est complet par construction.
`confidence = 1.0` systématiquement — pas un jugement, pas une nouvelle
variable, juste le reflet du fait qu'il n'y a rien à évaluer entre "la
règle est remplie" et "elle ne l'est pas" (contrairement à un signal
Station X où l'extraction peut être incomplète).

`boosted` reste `False` pour le Flux B, comme pour Station X actuellement
— le mécanisme d'éligibilité au risque doublé (§2.3, 2%→4%) dépend d'un
historique par actif (`confidence_scorer.py`, non construit à ce jour) ;
aucun des deux flux n'a de chemin de code vers `boosted=True` pour
l'instant.

### Actifs concernés

US30, EURUSD, GBPUSD, USDJPY, ETHUSD — les 5 actifs de la liste blanche
sans signal Station X à ce jour. **Une seule logique, appliquée
identiquement aux 5** — aucune variante par actif.

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- Ne modifie, ne remplace, ni ne concurrence le flux Station X existant
  (enveloppe séparée en Phase 2)
- N'implique aucun LLM à aucune étape de la décision (invariant #1) —
  filtre de régime et déclencheur sont des comparaisons numériques pures
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée ci-dessous
- Ne produit aucune conclusion statistique avant 10 trades minimum par
  variable réglable (donc **10 trades minimum** ici, une seule variable)
  — en dessous de ce seuil, les trades sont journalisés, jamais
  interprétés

---

## 2026-08-20 — Sortie sur profit : trailing Donchian dès l'ouverture

**Constat** (remonté par Ismaël en observant les deux premiers trades
réels du Flux B, GBPUSD et US30) : `evaluate_entry()` ne calcule jamais
de TP1/TP2/TP3 — seulement `entry_price`/`stop_price`. Or la gestion de
position d'`executor.py` (partagée avec Station X) ne déclenche son
trailing ATR qu'après TP1 **et** TP2 touchés. Sans TP, `tp1_hit` ne
devient jamais vrai : le trailing ne s'activait jamais pour ce flux.
Seul le stop initial fixe pouvait clôturer un trade — aucune prise de
gain, aucun mécanisme pour laisser courir un trend gagnant. C'était un
oubli d'implémentation du palier P2.5, jamais une décision actée (rien
en ce sens dans ce document ni dans `docs/DECISIONS.md` avant
aujourd'hui).

**Décision (arbitrage demandé à Ismaël entre deux options, tranché par
lui) : trailing sur le canal de Donchian(20), pas de TP fixe, pas de
trailing ATR.**

Raisonnement retenu :
- **Fidélité au principe théorique déjà cité plus haut** (Turtle
  Trading) : le système d'origine ne sort pas sur un take-profit fixe,
  il sort sur un signal de retournement du marché lui-même — c'est le
  canal (mobile) qui pilote la sortie, pas un ratio gain/risque choisi a
  priori.
- **Zéro variable supplémentaire** (invariant #10, budget déjà tenu à
  jour dans ce document) : réutilise strictement N=20, le seul paramètre
  déjà fixé pour cette hypothèse, et la même fonction de canal
  (`compute_donchian_channel`) qui sert déjà au déclencheur et au stop
  initial. Un trailing ATR aurait introduit un second mécanisme de
  sortie indépendant reposant sur un paramètre (ATR(14)) déjà utilisé
  ailleurs mais jamais pour ce rôle précis dans cette hypothèse — moins
  parcimonieux, sans justification théorique propre à l'Hypothèse #1.
  Des ratios TP1/TP2/TP3 façon Station X auraient, eux, introduit des
  ratios gain/risque choisis arbitrairement (pas de justification
  théorique écrite préalable, exigée par l'invariant #10).

**Mécanique** (`src/trend_strategy.compute_trailing_stop_channel`,
module critique, 100% couvert) : à chaque cycle de gestion, recalcule le
canal de Donchian(20) sur les bougies horaires courantes et propose le
nouveau stop = borne opposée du canal (bas du canal pour un long, haut
pour un short) — exactement la même règle que le stop initial, juste
réévaluée en continu. Le candidat passe par `max()`/`min()` en interne
PUIS par `risk_engine.evaluate_stop_update()` côté appelant (même
double ceinture que le trailing ATR de Station X) : un stop ne peut
jamais être élargi (invariant #5), jamais seulement par construction
interne. Si l'historique de bougies est insuffisant à un cycle donné,
le stop reste inchangé (fail-safe, invariant #7) plutôt que d'échouer.

Câblé dans `executor._evaluate_position_management` sur le critère
`state.tp1 is None` — qui identifie sans ambiguïté un trade du Flux B
(aucun signal Station X n'omet tp1, voir `parser.py`) — actif dès
l'ouverture, sans attendre un TP1/TP2 qui n'existera jamais pour ce
flux. `manage_open_trades` récupère désormais `DONCHIAN_PERIOD + 1`
bougies (au lieu de 20) pour que ce calcul dispose d'assez d'historique.

**Appliqué rétroactivement aux deux trades déjà ouverts** (GBPUSD id=6,
US30 id=7, ouverts le 20/08/2026 avant ce correctif) — le déploiement du
code corrigé suffit, aucune migration de données n'était nécessaire : le
trailing se recalcule à partir de l'état déjà en base (`stop_loss_courant`)
à chaque cycle suivant.

Tests : `tests/test_trend_strategy.py` (fonction pure, 100% couverte) +
`tests/test_executor.py` (branche Flux B de `_evaluate_position_management`,
même exigence que le reste de ce module critique).

---

## Hypothèse #3 — 20/08/2026

**Statut** : proposée, en attente de validation d'Ismaël. **Non testée,
aucun code d'exécution n'existe encore.** Nommée "#3" (pas "#2") pour
rester cohérente avec le compte démo Capital.com dédié qu'Ismaël a déjà
créé sous ce nom ("hypothèse 3") — un compte "hypothèse 2" existe aussi,
réservé à une future hypothèse distincte, sans lien avec celle-ci.

### Contexte

L'Hypothèse #1 (MA200 + Donchian(20) sur bougies horaires) tourne en
production depuis le 20/08/2026. Cette hypothèse teste si le **même
principe** — régime de fond + rupture de canal, exactement les mêmes
deux niveaux — se comporte différemment sur une **résolution de bougie
plus courte**. Objectif explicite : isoler cette question sur un compte
démo Capital.com **séparé** (identifiants et `accountId` déjà préparés,
voir `docs/DECISIONS.md`), pour qu'un résultat bon ou mauvais ne
contamine jamais les statistiques de l'Hypothèse #1 ni celles de Station
X — trois populations de trades strictement indépendantes.

### Principe retenu : identique à l'Hypothèse #1, seule la résolution de bougie change

**Confirmé explicitement, comme demandé** : aucun autre paramètre ne
bouge. Même architecture à deux niveaux prescrite par le CDC §2.11
(reprise à l'identique de l'Hypothèse #1, non redupliquée en détail ici) :

- **Niveau 1 — régime** : MA(200) sur clôtures, même règle de sens
  unique (long si prix > MA, short si prix < MA, aucune entrée à
  l'égalité stricte).
- **Niveau 2 — déclencheur** : rupture du canal de Donchian(20), même
  calcul de stop initial sur la borne opposée du canal, même mécanique
  de trailing sur ce canal une fois la position ouverte (voir l'entrée
  du 20/08/2026 ci-dessus sur la sortie sur profit — s'applique à
  l'identique ici, aucune raison d'en changer pour ce seul changement de
  résolution).

**Seul changement** : les bougies passent de `HOUR` à **`MINUTE_15`**
(résolution confirmée valide contre l'API Capital.com réelle le
20/08/2026, en lecture seule — `MINUTE15`/`MIN_15` échouent, seul
`MINUTE_15` est accepté). M5 a été considéré et écarté — voir réserves
ci-dessous.

### M15 plutôt que M5 — justification et réserves écrites AVANT tout test

**Choix retenu : M15.**

*Justification théorique* : le principe (filtre de tendance long terme +
déclencheur de rupture court terme) vient de la littérature déjà citée
pour l'Hypothèse #1 (momentum systématique, Turtle Trading). Cette
littérature ne prescrit aucune résolution de bougie précise — mais deux
réserves, écrites ici a priori, avant tout regard sur les données
produites par ce choix :

1. **Bruit et coût proportionnel du spread.** Sur M5, chaque bougie
   représente un mouvement de prix bien plus petit qu'en H1 — le spread
   (fixe en points pour la plupart des instruments de la liste blanche)
   devient une fraction bien plus importante du mouvement typique
   capturé par le canal de Donchian(20). Un stop resserré agressivement
   par un canal qui se redessine toutes les 5 minutes risque d'être
   touché par du bruit de spread/exécution plutôt que par un vrai
   retournement. M15 réduit ce risque sans l'éliminer — seule une
   observation réelle (jamais avant 10 trades, invariant #10) pourra le
   confirmer ou l'infirmer, jamais un ajustement a priori supplémentaire.
2. **Affaiblissement de la justification théorique du filtre MA(200) à
   mesure que la résolution raccourcit** — réserve supplémentaire,
   trouvée en préparant cette proposition, pas seulement celle déjà
   discutée sur le bruit. La littérature citée pour l'Hypothèse #1
   (Faber 2007 notamment) applique MA(200) à des **clôtures
   quotidiennes** (~200 jours de bourse, proche d'un an) — un régime de
   fond au sens propre. Sur bougies horaires (Hypothèse #1), MA(200)
   couvre déjà seulement ~8,3 jours, un écart déjà assumé dans l'entrée
   du 20/08/2026 ci-dessus. Sur **M15, MA(200) ne couvre plus que ~2
   jours** — à ce stade, le filtre ne capture plus vraiment un "régime
   de fond" au sens de la littérature citée, plutôt une micro-tendance
   très récente. Le CDC §2.11 prescrit littéralement "moyenne mobile
   longue (200)" sans préciser de résolution, donc ce choix reste
   conforme à la lettre du CDC — mais la solidité théorique de
   l'argument s'érode à mesure que la résolution raccourcit. Point de
   vigilance à surveiller lors de l'analyse des résultats, pas un
   obstacle à la validation de cette proposition.

M5 aurait aggravé les deux réserves sans justification théorique
supplémentaire pour compenser — écarté sur cette base, pas sur des
données (aucune donnée regardée avant cette décision).

### Paramètres exacts (choisis a priori, avant observation)

| Paramètre | Valeur | Statut |
|---|---|---|
| Période de la MA de régime | 200 (bougies M15) | **Fixe**, réutilise exactement le paramètre de l'Hypothèse #1 (lui-même prescrit littéralement par le CDC §2.11) — pas un nouveau choix |
| **Résolution des bougies** | **M15** (`MINUTE_15`) | **Seul paramètre réellement nouveau de cette hypothèse** — voir justification et réserves ci-dessus |
| Période du canal de Donchian (N) | 20 (bougies M15) | **Fixe**, réutilise exactement le paramètre de l'Hypothèse #1 (système "Turtle" original) — pas un nouveau choix, la valeur numérique ne change pas, seule l'unité de temps qu'elle mesure change |

### Budget de variables (invariant #10) — vérifié avant de proposer cette hypothèse

Repart du bilan de l'Hypothèse #1 (3/5 en lecture large : `confidence_
threshold`, `STALENESS_FRACTION_OF_STOP_DISTANCE`, N=20 Donchian de
l'Hypothèse #1 — voir citation exacte du §3.8 dans l'entrée ci-dessus,
non redupliquée ici).

**MA(200) et N=20 ne sont PAS de nouvelles variables** : valeurs
identiques à l'Hypothèse #1, même justification théorique, jamais
recalibrées — comptées une seule fois, déjà faites.

**Le choix de la résolution (M15) EST compté comme une nouvelle
variable** — décision distincte de l'Hypothèse #1, qui aurait pu être
tranchée autrement (M5, M30, H4...) et qui a fait l'objet d'un
arbitrage théorique ci-dessus. Compter cette décision comme "gratuite"
sous prétexte qu'elle réutilise les mêmes valeurs numériques (200, 20)
serait sous-évaluer le budget réel du projet.

**Bilan** : cette hypothèse introduit **1 nouvelle variable** (choix de
résolution M15). Total du projet après validation de cette proposition :
**4/5** dans la lecture large (`confidence_threshold` + `STALENESS_
FRACTION` + N=20 Hypothèse #1 + résolution M15 Hypothèse #3) — **il ne
resterait qu'une seule variable disponible** pour tout le reste du
projet (y compris une future Hypothèse #2). À garder en tête avant toute
proposition ultérieure.

### Score de confiance du signal

Identique à l'Hypothèse #1 : signal entièrement déterministe,
`confidence = 1.0` systématiquement, `boosted = False` (même raison —
`confidence_scorer.py` non construit).

### Actifs concernés

**Proposition : les mêmes 8 actifs que l'Hypothèse #1** (GOLD, US100,
US30, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD), pas un sous-ensemble.
Raisonnement : la restriction initiale de l'Hypothèse #1 à 5 actifs
visait à éviter le chevauchement avec Station X sur le même compte —
cette contrainte ne s'applique pas ici, l'Hypothèse #3 tourne sur un
compte Capital.com **totalement séparé** (`accountId` distinct, ciblage
explicite déjà en place, voir `docs/DECISIONS.md`), sans risque de
collision ni avec Station X ni avec l'Hypothèse #1. Aucune raison
structurelle de restreindre — **à confirmer ou modifier par Ismaël**.

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- Ne modifie, ne remplace, ni ne concurrence l'Hypothèse #1 ni Station X
  — compte, enveloppes et statistiques strictement séparés dès la
  conception (pas une étape ultérieure comme ça l'a été pour le Flux B
  à sa création)
- N'implique aucun LLM à aucune étape de la décision (invariant #1)
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée ci-dessous
- Ne produit aucune conclusion statistique avant **10 trades minimum**
  (un seul paramètre réglable propre à cette hypothèse, le choix de
  résolution)
- **Aucun code d'exécution tant que cette proposition n'est pas validée
  par Ismaël** — pas de module de détection, pas de process exécuteur,
  pas de câblage des identifiants déjà préparés (lecture seule
  uniquement à ce jour)

---

## Hypothèse #2 (ICT / Smart Money Concepts) — 20/08/2026

**Statut** : proposée, en attente de validation d'Ismaël. **Non testée,
aucun code d'exécution n'existe encore.** Rigueur volontairement plus
poussée que pour l'Hypothèse #3, sur demande explicite : l'ICT n'a pas
de définition mathématique canonique unique, contrairement au
Donchian/MA200 (systèmes publics, formellement documentés depuis les
années 1980-2010) — c'est le point faible assumé de cette proposition,
traité explicitement ci-dessous plutôt que masqué.

**Correction préalable** : aucune entrée ICT ni aucune réserve n'existait
avant aujourd'hui dans ce fichier (vérifié par recherche exhaustive, pas
supposé). Le §3.3 du CDC ("Méthodologie ICT du canal") documente le
style de raisonnement de **Station X lui-même** (FVG, Fibonacci,
structures de marché, en zones plutôt qu'en points) — utile ici comme
**vocabulaire de référence déjà présent dans le projet** (repris tel
quel pour rester cohérent avec `parser.py`/`market_data`), mais ce n'est
la spécification d'aucune stratégie autonome préexistante.

### Contexte

Teste si un filtre de confluence inspiré de l'ICT (zone de retracement
Fibonacci + zone de déséquilibre de prix, FVG) améliore la sélectivité
d'une entrée, par rapport au déclencheur "rupture brute" de l'Hypothèse
#1. Compte Capital.com dédié ("hypothèse 2", déjà créé), totalement
isolé de Station X, de l'Hypothèse #1 et de l'Hypothèse #3.

### Principe : deux options, honnêtement comparées — aucune n'élimine totalement l'arbitraire

Les 5 points demandés sont traités un par un. Pour chacun, la définition
purement géométrique (sans seuil) est séparée de la partie qui exige un
choix.

#### 1. Définition précise d'un FVG valide — entièrement déterministe, aucun seuil nécessaire

Sur 3 bougies consécutives `C1, C2, C3` (même unité de temps que le
reste de la logique, voir point 5) :

- **FVG haussier** si `low(C3) > high(C1)` — la zone de déséquilibre est
  `[high(C1), low(C3)]`.
- **FVG baissier** si `high(C3) < low(C1)` — la zone est
  `[high(C3), low(C1)]`.

C'est la définition standard à 3 bougies (comparaison stricte de prix,
aucun jugement visuel). **Aucun seuil de taille minimale n'est proposé**
— une lecture stricte de l'invariant #10 : ajouter un filtre "FVG
significatif seulement" introduirait un seuil sans justification
théorique écrite a priori. La contrepartie assumée : des FVG
minuscules, proches du bruit de marché, seront comptés au même titre
que des FVG larges. Recherchée sur la même fenêtre que le canal (voir
point 5) — pas une fenêtre séparée, pas un nouveau paramètre.

#### 2. Ancrage des retracements Fibonacci — LE point sans réponse unique, deux options présentées

Aucune définition canonique n'existe pour "quel swing utiliser" — c'est
un jugement discrétionnaire dans la pratique ICT courante. Deux façons
de le rendre déterministe, avec des coûts différents (voir budget,
point 5) :

**Option A — réutiliser le canal de Donchian(20) déjà construit pour
l'Hypothèse #1** comme proxy d'ancrage (borne haute = plus haut des 20
dernières bougies, borne basse = plus bas). **Ce n'est PAS un swing
structurel ICT au sens strict** (un swing ICT est un point de
retournement local, pas une extrémité glissante sur N bougies) — c'est
une **simplification assumée**, choisie parce qu'elle ne coûte aucun
paramètre supplémentaire (réutilise N=20, déjà budgété par
l'Hypothèse #1) et réutilise du code déjà testé à 100%
(`compute_donchian_channel`).

**Option B — détection de swings par fractale** (Bill Williams,
*Trading Chaos*, 1995 — convention algorithmique standard, indépendante
de l'ICT mais largement reprise par la communauté ICT elle-même faute
de mieux) : un plus haut de swing à la bougie `i` est confirmé si
`high(i) > high(i-K)...high(i-1)` ET `high(i) > high(i+1)...high(i+K)`
(symétrique pour un plus bas). **Introduit un nouveau paramètre K**
(classiquement K=2, la "fractale à 5 bougies") — plus fidèle à la
notion ICT de swing réel, mais un paramètre de plus au budget.

**Aucune des deux n'est "la bonne" réponse — c'est un arbitrage, pas une
équation.** Recommandation : Option A par défaut (budget), sauf si tu
juges que la fidélité au concept prime sur le budget de variables — à
trancher explicitement ci-dessous.

#### 3. Cassure de structure — honnêtement, PAS résolue de façon satisfaisante par l'option A

Sous l'**Option A**, il n'y a **pas de détection dédiée de cassure de
structure** (pas de séquence de plus hauts/plus bas façon BOS/CHoCH) —
le filtre de régime MA(200), déjà en place pour l'Hypothèse #1, sert de
proxy grossier ("biais structurel"), sans jamais confirmer une vraie
cassure au sens ICT. **C'est un angle mort assumé de l'Option A**, pas
une omission cachée.

Sous l'**Option B**, la cassure de structure devient définissable
proprement une fois les swings fractals identifiés : **BOS** (poursuite)
= clôture au-delà du dernier swing dans le sens du biais déjà établi ;
**CHoCH** (retournement) = clôture au-delà du dernier swing dans le
sens opposé au biais établi. Déterministe, mais dépend du paramètre K
du point 2.

#### 4. Règle d'entrée et de sortie découlant des définitions ci-dessus (Option A détaillée)

Régime (inchangé, réutilisé de l'Hypothèse #1) : MA(200) sur clôtures
horaires, même règle de sens unique.

Canal (inchangé, réutilisé) : Donchian(20), bornes `haut`/`bas`,
excluant la bougie courante.

**Zone de confluence** (retracement 61,8 %–78,6 %, valeurs **prescrites
littéralement par le CDC §3.3** — pas un choix pour cette hypothèse) :
- Régime haussier : zone = `[bas + 0,214×(haut−bas), bas + 0,382×(haut−bas)]`
  (équivalent à 61,8 %–78,6 % de retracement depuis le haut du canal)
- Régime baissier : zone = `[bas + 0,618×(haut−bas), bas + 0,786×(haut−bas)]`

**Entrée** (long, régime haussier) : clôture courante dans la zone
ci-dessus **ET** un FVG haussier (point 1), détecté sur les 20 dernières
bougies, dont l'intervalle `[high(C1), low(C3)]` chevauche cette zone
(chevauchement géométrique standard, aucun seuil). Entrée au niveau de
la clôture, en ordre limite (§2.8, comme partout ailleurs dans le
projet). Symétrique pour un short en régime baissier.

**Stop initial** : borne opposée du même canal — identique à
l'Hypothèse #1, aucun paramètre supplémentaire.

**Sortie** : trailing sur le même canal de Donchian(20), **réutilise
tel quel `compute_trailing_stop_channel`** (module critique déjà
100% couvert) — aucune nouvelle logique de sortie à écrire.

*Simplification assumée* : contrairement à la pratique ICT
discrétionnaire courante (qui attend souvent une "bougie de réaction"
dans la zone avant d'entrer), cette règle entre dès que la condition
géométrique est remplie, sans confirmation supplémentaire — nécessaire
pour rester déterministe, au prix d'une fidélité réduite à la pratique
réelle.

#### 5. Budget de variables (invariant #10) — cumul H1 + H3 + H2

Repart du bilan de l'Hypothèse #3 : 4/5 en lecture large
(`confidence_threshold`, `STALENESS_FRACTION_OF_STOP_DISTANCE`, N=20
Donchian de l'Hypothèse #1, résolution M15 de l'Hypothèse #3).

**Résolution horaire (HOUR), pas une nouvelle variable** : cette
proposition reste délibérément sur l'unité de temps déjà établie par
l'Hypothèse #1 (pas de changement de résolution) — l'expérimentation de
résolution reste l'exclusivité de l'Hypothèse #3, pour ne pas cumuler
deux nouvelles variables de résolution différentes sur deux hypothèses.

**Ratios de Fibonacci (61,8 %/78,6 %), pas une nouvelle variable** :
prescrits littéralement par le CDC §3.3 — même statut que MA(200) pour
l'Hypothèse #1 (valeur fixée par le CDC, jamais un choix a priori de ma
part).

**FVG, pas une nouvelle variable** : définition purement géométrique
(point 1), aucun seuil.

**Sous l'Option A (recommandée)** : N=20 déjà budgété par l'Hypothèse #1,
réutilisé tel quel. **Cette hypothèse introduirait donc 0 nouvelle
variable.** Total du projet resterait à **4/5**, avec 1 slot de marge
pour la suite.

**Sous l'Option B** : le paramètre K (fenêtre de la fractale) est
**1 nouvelle variable**. Total du projet passerait à **5/5 — le
plafond exact**, sans plus aucune marge pour quoi que ce soit d'autre
sur l'ensemble du projet après validation.

### Score de confiance du signal

Identique aux Hypothèses #1 et #3 : entièrement déterministe,
`confidence = 1.0`, `boosted = False`.

### Actifs concernés

**Proposition : les mêmes 8 actifs que les Hypothèses #1 et #3** — même
raisonnement que pour l'Hypothèse #3 (compte Capital.com totalement
séparé, aucun risque de collision). À confirmer ou modifier.

### Limites et angles morts assumés (demande explicite d'Ismaël)

- **L'ancrage Fibonacci (point 2) n'a pas de réponse non-arbitraire** —
  c'est un choix entre deux conventions, pas une valeur dérivée
  objectivement. Présenté comme tel, pas masqué.
- **Sous l'Option A, aucune cassure de structure n'est réellement
  détectée** (point 3) — le filtre MA(200) sert de proxy, imparfait par
  construction pour ce rôle précis.
- **Aucun filtre de taille minimale sur les FVG** (point 1) — accepte du
  bruit potentiellement significatif plutôt que d'introduire un seuil
  non justifié théoriquement.
- **Aucune confirmation de réaction de prix avant l'entrée** (point 4) —
  simplification nécessaire pour rester déterministe, au prix d'une
  fidélité réduite à la pratique ICT réelle.
- **La littérature ICT elle-même n'a pas le même statut que celle citée
  pour l'Hypothèse #1** (Moskowitz/Ooi/Pedersen, Faber, Turtle Trading —
  études publiées, revues, ou système historique documenté) : c'est une
  méthodologie de pratique retail, largement diffusée mais jamais
  formalisée ni validée dans une littérature académique équivalente.
  Cette hypothèse teste une **formalisation de ma conception**, pas une
  règle ICT canonique — à savoir avant de juger le résultat.

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- Ne modifie, ne remplace, ni ne concurrence Station X, l'Hypothèse #1
  ou l'Hypothèse #3 — compte, enveloppes et statistiques strictement
  séparés dès la conception
- N'implique aucun LLM à aucune étape de la décision (invariant #1)
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée ci-dessous
- Ne produit aucune conclusion statistique avant **10 trades minimum**
  par variable réglable introduite (0 sous l'Option A, 10 minimum sous
  l'Option B pour le paramètre K)
- **Aucun code d'exécution tant que cette proposition n'est pas validée
  par Ismaël** — ni module de détection, ni process exécuteur, ni
  câblage des identifiants déjà préparés (lecture seule uniquement à ce
  jour)

### Décisions à trancher avant tout code

1. **Option A ou B** pour l'ancrage Fibonacci / la détection de swing
   (impacte directement le budget de variables : 4/5 vs 5/5 pour le
   projet entier).
2. **Actifs concernés** : les 8, ou un sous-ensemble.
3. Si Option A : acceptable que la "cassure de structure" (point 3) ne
   soit qu'un proxy MA(200), sans détection dédiée ?

---

## 2026-08-21 — Budget de variables : correction du modèle (H1/H3/H2)

**Objection reçue d'Ismaël** (posée sur H3, à appliquer à H2 si elle
tient) : le §3.8 encadre des variables testées par corrélation sur une
même population de trades (`adaptive_rules`, les 5 variables listées
plus haut) — alors que H1, H3 et H2 sont chacune une "stratégie
technique complémentaire" au sens du §2.11, avec son propre budget de
paramètres, jamais mélangé au budget des 5 variables de sélection de
signal. Reconsidéré sous cet angle, pas simplement assumé.

**Vérifié par citation exacte, pas supposé** — deux passages de
`docs/CDC_v4.md`, jamais relus sous cet angle avant aujourd'hui,
tranchent la question :

> §2.11 : « **2-3 paramètres maximum, choisis a priori**. [...]
> **Métriques calculées séparément par source** — si l'une a un edge et
> l'autre non, les mélanger masquerait ce fait. »

> §3.9, Promotion : « Une hypothèse validée sur ≥10 trades prospectifs
> devient candidate à **une 6e variable officielle**, avec sa
> justification versée au dossier. »

Le second passage est décisif : une hypothèse *devient candidate* à une
6e variable **après** sa validation prospective — cette formulation
n'a de sens que si ses paramètres ne sont **pas déjà comptés** parmi
les 5 initiales avant leur promotion (on ne peut pas déjà faire partie
des 5 et "devenir" la 6e). Le CDC distingue donc explicitement deux
budgets, que mon calcul initial avait fusionnés à tort :

- **§3.8 (`adaptive_rules`, 5 variables)** : recherche de corrélation
  post-hoc sur l'historique de trades déjà accumulé, correction
  multiple-comparaisons + 10 trades/variable. **0/5 consommé à ce
  jour** — `adaptive_rules` n'existe pas encore, aucune hypothèse n'y a
  jamais touché. Seule une hypothèse **promue** (§3.9, ≥10 trades
  prospectifs validés) y entrerait, comme 6e variable et suivantes.
- **§2.11 (stratégie technique complémentaire), 2-3 paramètres maximum
  PAR INSTANCE** : chaque hypothèse (H1, H3, H2, une future) a son
  propre budget indépendant, jamais partagé avec les autres.

**Bilan corrigé de chaque hypothèse** (les sections "Budget de
variables" d'origine, plus haut dans ce fichier, restent inchangées —
convention de ce fichier, une entrée n'est jamais réécrite — mais sont
remplacées en pratique par ce qui suit) :

| Hypothèse | Paramètre propre (§2.11, cap 2-3) | Budget §3.8 touché |
|---|---|---|
| H1 | N=20 (Donchian) — 1/3 | 0 (aucune promotion) |
| H3 | Résolution M15 — 1/3 (MA200 et N=20 réutilisés à l'identique de H1, jamais "dépensés" une seconde fois) | 0 |
| H2 (Option B) | K=2 (fractale) — 1/3 (régime, canal, ratios Fibonacci réutilisés/fixés par le CDC §3.3) | 0 |

**Aucune des trois hypothèses n'est donc "la dernière place disponible"
pour une autre** — la tension affichée dans les entrées d'origine
("H3 consomme le seul slot restant", "H2 atteindrait 5/5, le plafond
exact") était une conséquence du modèle de budget erroné, pas une
contrainte réelle du CDC.

**Contrainte réelle mise en lumière par cette correction, jusque-là
sous-pondérée** : §3.9 plafonne à **3 hypothèses par cycle maximum**
(« au-delà, la correction pour comparaisons multiples rend la barre
inatteignable ») — c'est CE plafond, pas un compte de variables, qui
encadre combien d'hypothèses peuvent tourner en parallèle. H1 + H3 + H2
= exactement 3, pile au plafond une fois H2 validée. Une éventuelle 4e
hypothèse **prédictive** proposée pendant que ces trois sont encore en
observation buterait sur ce plafond — à traiter explicitement le jour
venu, pas une décision à prendre seul. Voir `docs/DECISIONS.md`
(21/08/2026) sur le 4e compte "synthèse" évoqué par Ismaël : il ne
s'agit PAS d'une 4e hypothèse prédictive et il ne consomme donc pas ce
plafond — la distinction précise est faite là-bas.

**Ce que cette correction NE change PAS** : le principe, la résolution
M15 et les réserves théoriques de H3 restent solides et non retouchés
(demande explicite d'Ismaël) ; le principe, les définitions
FVG/Fibonacci/K et les angles morts assumés de H2 restent inchangés.
Seule la comptabilité du budget était fausse.

---

## 2026-08-21 — Hypothèse #2 : décision finale d'Ismaël

Sur la base de la correction de modèle de budget ci-dessus (condition
qu'Ismaël avait posée explicitement avant de trancher) :

1. **Option B retenue** (détection de swings par fractale, Bill
   Williams, K=2) — devient le paramètre propre de H2 (1/3 de son
   budget §2.11, voir tableau ci-dessus), ne consomme aucun budget
   partagé avec H1/H3. Conséquence directe : la cassure de structure
   (BOS/CHoCH, point 3 de l'entrée du 20/08/2026) devient définissable
   proprement à partir des swings fractals, plus un simple proxy
   MA(200) comme sous l'Option A.
2. **Actifs** : les mêmes 8 que H1/H3 (GOLD, US100, US30, EURUSD,
   GBPUSD, USDJPY, BTCUSD, ETHUSD) — confirmé, pas de sous-ensemble.
3. **Absence de confirmation avant entrée** (pas de bougie de réaction
   attendue dans la zone de confluence avant d'entrer, point 4 de
   l'entrée du 20/08/2026) — acceptée telle quelle pour cette première
   version. Reste documentée comme **limitation explicite**, pas
   compensée par de la complexité supplémentaire à ce stade : si les
   résultats prospectifs montrent que cette absence de confirmation
   dégrade la sélectivité, ce sera le constat d'une future entrée
   datée, jamais un ajustement silencieux en cours de route.

**Statut réel à partir de cette date : validée par Ismaël dans son
principe** — l'entrée d'origine du 20/08/2026 ci-dessus garde sa
mention « proposée, en attente de validation » intacte (convention de
ce fichier, une entrée n'est jamais réécrite) ; c'est cette entrée-ci
qui fait foi sur le statut réel. **Aucun code d'exécution n'existe
encore** : construction (module de détection, process exécuteur dédié,
câblage des identifiants du compte "hypothèse 2" déjà créé) en attente
d'un feu vert explicite d'Ismaël, distinct de cette validation de
principe.

---

## 2026-08-21 — Hypothèse #3 : validée et déployée

Feu vert explicite d'Ismaël reçu (« toutes les questions préalables
étant résolues »), après validation du modèle de budget corrigé
ci-dessus. **Statut réel à partir de cette date : validée et déployée**
(l'entrée d'origine du 20/08/2026 garde sa mention "proposée, en attente
de validation" intacte, convention de ce fichier). Code, tests (100% sur
la logique de décision, réutilisée telle quelle de l'Hypothèse #1) et
déploiement détaillés dans `docs/DECISIONS.md` (21/08/2026).

## 2026-08-21 — Hypothèse #2 : règles écrites nécessaires à l'implémentation de l'Option B

La décision du 21/08/2026 ci-dessus retient l'Option B, mais la
proposition du 20/08/2026 ne détaillait la règle d'entrée complète
(point 4) que pour l'Option A. Écrire du code exécutable a exigé trois
choix concrets, **pré-enregistrés ici avant toute observation de
résultat** (aucune donnée regardée avant ces choix), pas dérivés d'une
règle ICT canonique (aucune n'existe pour ces points précis) :

1. **Sélection de la jambe d'impulsion** : parmi les swings fractals
   CONFIRMÉS de la fenêtre récente, on prend le dernier swing bas
   confirmé, puis le PREMIER swing haut confirmé plus récent que lui
   (régime haussier — symétrique en régime baissier : dernier swing
   haut, puis premier swing bas plus récent). Aucune paire valide → pas
   de signal.
2. **Fenêtre de recherche** des swings et des FVG : les 20 dernières
   bougies (`DONCHIAN_PERIOD`, réutilisé — PAS un nouveau paramètre,
   même raisonnement que le point 1 de la proposition d'origine pour les
   FVG), plus une marge de `2×FRACTAL_K` (4) bougies pour permettre la
   confirmation des swings en bord de fenêtre.
3. **BOS/CHoCH (point 3 de la proposition) implémenté et testé, mais
   PAS câblé comme condition d'entrée** dans cette première version —
   la proposition validée ne l'exigeait pas explicitement (elle
   définissait seulement la capacité comme "devenant disponible" sous
   l'Option B). L'ajouter comme filtre supplémentaire serait une
   complexité non validée par Ismaël.

Stop initial : le swing opposé à la direction (bas pour un long, haut
pour un short) — traduction directe de "stop = borne opposée du canal"
une fois le canal remplacé par la jambe de swings. Sortie : trailing sur
`compute_trailing_stop_channel`, inchangé (décision inconditionnelle de
la proposition d'origine, valable pour les deux options).

**Décision finale confirmée par Ismaël le 21/08/2026** (voir entrée
ci-dessus) : Option B, 8 actifs, absence de confirmation avant entrée
acceptée. Code construit et testé à 100% (`src/ict_strategy.py`,
34 tests) — **PAS déployé en production** : identifiants Capital.com
dédiés à ce compte manquants, voir `docs/DECISIONS.md` (21/08/2026) pour
le détail complet et les options pour Ismaël.

---

## 2026-08-21 — Hypothèse #2 : déployée

Identifiants Capital.com dédiés reçus d'Ismaël, `hypothesis2_executor`
démarré avec succès (voir `docs/DECISIONS.md` pour le détail, y compris
un bug générique de `switch_account()` trouvé et corrigé au premier
démarrage — sans rapport avec cette hypothèse spécifiquement). 8
enveloppes créées, séparées de Station X/H1/H3. **Statut réel à partir
de cette date : en autonomie.** L'entrée d'origine du 20/08/2026 et la
décision finale du 21/08/2026 ci-dessus gardent leur texte intact
(convention de ce fichier) — c'est cette entrée-ci qui fait foi sur le
statut opérationnel.

---

## Hypothèse #4 (retour à la moyenne, Bandes de Bollinger) — 21/08/2026

**Statut** : proposée, en attente de validation d'Ismaël. **Non testée,
aucun code d'exécution n'existe encore.** Pré-enregistrée avant toute
observation de données H4 (aucune n'existe).

### ⚠️ Point à trancher avant tout autre chose : le plafond des 3 hypothèses par cycle (§3.9)

Signalé explicitement le 21/08/2026 (voir `docs/DECISIONS.md`, correction
du modèle de budget) comme un scénario à traiter "le jour venu, pas une
décision à prendre seul" — **ce jour est arrivé**. Citation exacte,
`docs/CDC_v4.md` §3.9 : *« 3 hypothèses par cycle maximum. Au-delà, la
correction pour comparaisons multiples rend la barre inatteignable —
tester 20 idées en fait "gagner" quelques-unes par pur hasard. »*

H1 + H3 + H2 = déjà 3, exactement au plafond. H4 serait une **4e
hypothèse prédictive simultanée**. Deux lectures honnêtement en tension,
aucune tranchée ici :
- **Lecture littérale** : §3.9 décrit le générateur d'hypothèses
  officiel trimestriel, non construit à ce jour — H1 à H4 ne sont pas
  produites par ce processus, donc le plafond ne s'applique pas
  littéralement à elles.
- **Lecture par l'esprit du texte** : le risque statistique que ce
  plafond protège (comparaisons multiples, "trouver un pattern par pur
  hasard" en testant trop d'idées à la fois) ne dépend en rien de
  savoir si les hypothèses viennent d'un générateur formel ou sont
  écrites à la main — H1/H2/H3/H4 tournant simultanément sur le même
  volume de trades exposent au même risque, sous une autre étiquette.

Cette tension n'a pas été résolue unilatéralement. **Aucune conséquence
sur ce qui suit** : la construction de la logique de détection ci-dessous
n'engage aucune exécution réelle (pas d'identifiants H4), donc aucun
risque statistique n'est couru tant que ce point n'est pas tranché — mais
il doit l'être avant tout passage en exécution, même démo.

### Contexte

H1/H3 (suivi de tendance) et H2 (confluence ICT) testent tous la
continuation directionnelle à l'intérieur du régime de fond. H4 teste
l'hypothèse inverse à court terme : à l'intérieur du même régime, une
extension de prix au-delà de 2 écarts-types de sa moyenne courte (20
périodes) tend à revenir vers cette moyenne avant de reprendre sa
direction de fond — un retour à la moyenne à court terme, jamais un
pari contre le régime lui-même.

*Justification théorique* (écrite par Ismaël avant toute donnée H4,
reproduite ici) : contrairement aux trois autres hypothèses, la sortie
est un take-profit fixe (pas de trailing) — par construction, ça produit
des clôtures plus rapides et plus fréquentes (gains réguliers plutôt que
rares et grands), sans toucher à la fréquence d'entrée ni au timeframe
(donc aucune dérive vers le scalping exclu par le CDC §1.2/§2.8).

*Littérature* — mêmes réserves de calibrage que pour H2 (proposition du
20/08/2026) : les Bandes de Bollinger elles-mêmes sont un outil
d'analyse technique largement diffusé et documenté (John Bollinger,
*"Bollinger on Bollinger Bands"*, 2001) mais relèvent d'une convention
de pratique, pas d'une étude académique à comité de lecture comme les
citations de l'Hypothèse #1 (Moskowitz/Ooi/Pedersen, Faber) — honnêteté
à conserver en jugeant les résultats. Le phénomène de retour à la
moyenne à court terme sur les rendements a par ailleurs une littérature
académique propre (ex. Jegadeesh, *"Evidence of Predictable Behavior of
Security Returns"*, Journal of Finance, 1990, sur les renversements à
court terme) — distincte de l'outil Bollinger lui-même, citée ici comme
support du principe général, pas comme validation de cette
implémentation précise.

### Principe retenu : MA(200) de régime + Bandes de Bollinger(20, 2σ) comme déclencheur de retour à la moyenne

Même architecture à deux niveaux que H1/H2/H3 (§2.11) :

**Niveau 1 — Régime de fond** : identique à H1/H2/H3, `trend_strategy.
compute_regime` réutilisé tel quel (MA200 horaire) — **pas recalculé
différemment**, pas un nouveau paramètre.

**Niveau 2 — Déclencheur** : Bandes de Bollinger sur 20 bougies horaires
(bande médiane = moyenne mobile simple des 20 dernières clôtures ;
bandes haute/basse = médiane ± 2 écarts-types). On ne fade **jamais**
contre le régime de fond, seulement les extensions à court terme à
l'intérieur du régime autorisé :
- Régime haussier (clôture > MA200) → achat autorisé **uniquement** si
  la clôture courante touche ou dépasse la bande basse.
- Régime baissier → vente autorisée **uniquement** au toucher de la
  bande haute.

### Deux choix de calcul non spécifiés par la proposition — signalés, pas masqués (même exigence que pour l'Hypothèse #2)

1. **"Largeur de bande" pour le stop — ambiguïté réelle.** La
   proposition dit "stop fixe à 1× la largeur de bande à l'entrée" sans
   préciser si "largeur" désigne l'écart complet bande haute − bande
   basse (= 4 écarts-types) ou le demi-écart médiane→bande (= 2
   écarts-types) — un facteur 2 d'écart entre les deux lectures, avec un
   impact direct et important sur le ratio gain/risque (avec TP à la
   médiane à 2σ de l'entrée : R:R ≈ 1:2 si stop = 4σ, R:R ≈ 1:1 si
   stop = 2σ). **Choix retenu par défaut ici, à confirmer** : largeur
   complète (4σ), lecture la plus littérale du mot "largeur". Codé comme
   une constante nommée (`STOP_WIDTH_MULTIPLIER` appliqué à la largeur
   complète), donc trivial à inverser si l'autre lecture est voulue.
2. **Écart-type population vs échantillon.** Convention Bollinger
   standard (et celle de la plupart des plateformes de graphiques) :
   écart-type de **population** (division par 20, pas par 19). Retenu
   ici. Écart avec la version "échantillon" : facteur √(20/19) ≈ 1,026,
   soit ~2,6% — mineur comparé au point 1, mais un choix réel, pas une
   évidence.

### Paramètres exacts (choisis a priori, avant observation)

| Paramètre | Valeur | Statut |
|---|---|---|
| Période de la MA de régime | 200 (bougies horaires) | **Fixe**, réutilisée à l'identique de H1/H2/H3 — pas un nouveau choix |
| **Config des bandes** (période + multiplicateur d'écart-type) | SMA(20), ±2σ | **Paramètre #1** — convention Bollinger standard, non arbitraire (voir littérature ci-dessus), période et multiplicateur toujours utilisés ensemble, jamais ajustés séparément |
| **Multiplicateur de largeur pour le stop** | 1× (voir ambiguïté ci-dessus sur "largeur") | **Paramètre #2** |
| Take-profit | Bande médiane (SMA20) **au moment de l'entrée**, figée — jamais recalculée en continu | Découle mécaniquement du paramètre #1, **pas un 3e paramètre libre** |
| Résolution des bougies | HOUR | Fixe, identique à H1 |
| Trailing | **Aucun** — stop fixe après ouverture (conforme invariant #5 : un stop qui ne bouge jamais ne peut jamais être élargi) | Décision explicite, contrairement à H1/H2/H3 |

**2 paramètres propres à cette hypothèse**, dans la limite des 2-3
imposée par §2.11 — 1 slot de marge.

### Simplification assumée à documenter (demande explicite d'Ismaël)

**Aucun filtre de force de tendance** (type ADX) pour éviter le fade en
régime très directionnel, où une extension à 2σ peut n'être que le
début d'un mouvement plus large plutôt qu'un excès à corriger. Limite
connue, volontairement pas ajoutée comme 3e paramètre a priori — à
surveiller dans les résultats, pas un obstacle à la validation de cette
proposition.

### Budget de variables (invariant #10) — vérifié avant de proposer cette hypothèse

Modèle corrigé du 21/08/2026 (voir `docs/HYPOTHESES.md` ci-dessus et
`docs/DECISIONS.md`) : chaque hypothèse a son propre budget de 2-3
paramètres (§2.11), jamais partagé avec les 5 variables de sélection de
signal du §3.8 (`adaptive_rules`, toujours à 0/5, jamais touché par
aucune hypothèse à ce jour) ni avec le budget des autres hypothèses.
H4 : 2/3 propres. Voir la section dédiée plus haut sur le plafond
séparé du §3.9 (3 hypothèses par cycle) — un point distinct, non résolu.

### Score de confiance du signal

Identique aux Hypothèses #1/#2/#3 : entièrement déterministe,
`confidence = 1.0`, `boosted = False`.

### Actifs concernés

Les 8 actifs de la liste blanche, comme H1/H2/H3 — aucune variable de
sélection supplémentaire.

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- Ne fade jamais contre le régime de fond (MA200) — uniquement les
  extensions à court terme à l'intérieur du régime autorisé.
- N'implique aucun LLM à aucune étape de la décision (invariant #1).
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée.
- Ne produit aucune conclusion statistique avant **10 trades minimum
  par variable réglable** (2 variables ici → 20 trades minimum avant
  toute interprétation, invariant #10).
- **Aucun identifiant Capital.com câblé** — compte démo H4 pas encore
  configuré (Ismaël les fournira directement, jamais dans la
  conversation, même principe que H2/H3).
- **Aucune exécution réelle, même démo** — voir la question ouverte sur
  le plafond §3.9 en tête de cette entrée, à trancher avant.

### Décisions à trancher avant toute exécution (même démo)

1. Le plafond des 3 hypothèses par cycle (§3.9) s'applique-t-il à H4 —
   lecture littérale (générateur non construit, ne s'applique pas) ou
   lecture par l'esprit du texte (le risque de comparaisons multiples
   s'applique quelle que soit l'origine des hypothèses) ?
2. "Largeur de bande" pour le stop : écart complet (4σ, retenu par
   défaut ici) ou demi-écart (2σ) ?
3. Écart-type population (retenu par défaut) ou échantillon ?
4. Intégration avec le moteur générique (`technical_strategy_
   executor.py`) — voir `docs/DECISIONS.md` : la génération de signal
   s'y intègre presque proprement (ajout mineur), la **gestion de
   position ne s'y intègre pas** en l'état (nécessite une nouvelle
   branche dans `executor._evaluate_position_management`, un module
   critique à 100% de couverture partagé par les 4 flux existants) —
   décrit en détail, pas implémenté aujourd'hui.

---

## Hypothèse #4 : décisions d'Ismaël — 21/08/2026

Les quatre points laissés ouverts par la proposition initiale (section
"⚠️ Point à trancher" et "Décisions à trancher" ci-dessus) sont tranchés.
Cette entrée ne modifie rien de ce qui précède, elle vient s'ajouter
après (convention append-only du fichier).

**1. Plafond §3.9 ("3 hypothèses par cycle maximum")** : Hypothèse #4
autorisée en exécution DÉMO (aucun capital réel engagé, statistiques déjà
isolées par source, voir `envelopes.source`). Décision permanente et
explicite, applicable à toute future hypothèse (H1, H2, H3, H4 ou
au-delà) : **toute évaluation ou validation d'un résultat** (passage en
réel, comparaison entre hypothèses, décision d'arrêt/poursuite) devra
désormais appliquer la correction statistique pour comparaisons
multiples calibrée sur **4 hypothèses simultanées, jamais 3** — le risque
que le plafond §3.9 protège (trouver un pattern par pur hasard en testant
trop d'idées à la fois) se matérialise au moment de JUGER un résultat,
pas à l'exécution démo sans risque. Aucune hypothèse ne sera promue ou
comparée sans cette correction à 4. Voir `docs/DECISIONS.md` (21/08/2026)
pour l'implémentation de cette règle le jour où `hypothesis_engine`
(§3.9) ou une comparaison inter-hypothèses sera construite.

**2. "Largeur de bande" pour le stop** : DEMI-largeur de bande (médiane
-> bande = 2σ), pas l'écart complet (4σ) retenu par erreur dans la
proposition initiale. Avec le TP à la bande médiane (2σ de la clôture
d'entrée) et ce choix, stop et cible sont désormais symétriques : R:R ≈
1:1, au lieu du 2:1 défavorable de la première version (le stop était
deux fois plus large que la cible, un déséquilibre non intentionnel).
`STOP_WIDTH_MULTIPLIER` dans `src/mean_reversion_strategy.py` reste à
1.0, mais s'applique désormais à `(bande_haute - bande_basse) / 2` au
lieu de `bande_haute - bande_basse`.

**3. Écart-type** : population confirmé sans changement (convention
Bollinger standard).

**4. Intégration avec le moteur générique** : la 3e branche de dispatch
a été construite dans `executor._evaluate_position_management`
(`ManagementActionType.CLOSE_FULL_TP`), sans toucher aux deux branches
existantes (Station X, Flux B) — tests de non-régression dédiés dans
`tests/test_executor.py`. `hypothesis4_executor.py` câblé sur le même
modèle que H2/H3 (`technical_strategy_executor.run_technical_strategy_
loop`). **Exécution réelle toujours NON activée** : aucun identifiant
`.env`, aucun déploiement VPS — en attente des identifiants du compte
démo H4, qu'Ismaël fournira directement (jamais dans la conversation).
Détail complet dans `docs/DECISIONS.md`.

---

## 2026-08-23 — Hypothèse #5 : sortie progressive sur l'entrée ICT de l'Hypothèse #2 — proposée et validée par Ismaël dans la même demande

Entrée écrite et datée **avant toute observation de résultat** (aucun
trade H5 n'existe à ce jour, base de production vérifiée) — pré-
enregistrement au sens de l'en-tête de ce fichier. Contrairement aux
entrées H1-H4, la proposition et la validation d'Ismaël arrivent dans le
même message : les paramètres ci-dessous sont donc directement les
paramètres retenus, pas une proposition en attente.

### Question posée — complémentaire à H2, pas une nouvelle théorie d'edge

H2 teste si la confluence ICT/SMC (swings fractals K=2, zone de
Fibonacci 61,8-78,6%, FVG) a un edge à l'**entrée**, avec une sortie
tout-ou-rien (trailing Donchian(20) perpétuel dès l'ouverture, comme
H1/H3). H5 teste une question différente et complémentaire : sur la
**même entrée ICT**, est-ce qu'une **sortie** qui sécurise
progressivement les gains (TP1/TP2 fixes, seuls 20% de la position
continuent de courir sous trailing) produit des résultats plus
réguliers (moins de variance, plus de gains concrétisés) qu'une sortie
tout-en-trailing — même si le R total moyen peut être plus faible.

**Une seule variable change entre H2 et H5 : la sortie.** L'entrée est
rigoureusement identique (même fonction, mêmes paramètres, réutilisée
sans modification) — comparaison propre, cause isolée. C'est une
question sur le *mécanisme de sortie*, indépendante de la validité de
l'edge d'entrée ICT lui-même (H2 reste la seule source de vérité sur ce
point).

### Conception — réutilisation intégrale, aucune nouvelle logique de décision

- **Entrée** : `ict_strategy.evaluate_entry` réutilisée À L'IDENTIQUE
  (import direct, jamais redupliquée), paramètres H2 inchangés
  (`FRACTAL_K=2`, Fibonacci 61,8%/78,6%, régime MA(200), résolution
  horaire). Aucun nouveau paramètre d'entrée — K=2 reste "dépensé" une
  seule fois par H2 (même précédent que H3 réutilisant MA200/Donchian(20)
  de H1 sans re-consommer son budget, voir la correction de modèle de
  budget du 21/08/2026 plus haut dans ce fichier).
- **Sortie** : mécanisme §2.10 déjà construit pour Station X — TP1
  50%/TP2 30%/TP3 20% sous trailing 2×ATR(14), stop déplacé au
  breakeven dès TP1 touché, resserrement uniquement (invariant #5).
  **Vérifié avant d'écrire cette entrée (voir `docs/DECISIONS.md`,
  23/08/2026) : ce mécanisme se branche sur `signals.tp1`/`tp2`
  seulement — aucune modification d'`executor._evaluate_position_
  management` n'a été nécessaire.**
- **Paramètres réellement nouveaux (§2.11, cap 2-3 propres à cette
  hypothèse)** : la distance de TP1 et TP2, exprimées en multiples de R
  (R = risque initial du trade, jamais recalculé après une clôture
  partielle, §2.1) :

| Paramètre | Valeur | Statut |
|---|---|---|
| Distance TP1 | **1R** | **Paramètre #1** — valeur ronde, choisie a priori, non ajustée aux données |
| Distance TP2 | **2R** | **Paramètre #2** — idem |
| TP3 (reliquat 20%) | Aucune cible fixe, trailing 2×ATR(14) dès TP2 touché | Découle mécaniquement du mécanisme §2.10 réutilisé, pas un 3e paramètre libre |
| Régime/entrée ICT | Identique à H2 (K=2, Fibonacci, FVG, MA200) | Réutilisé, budget déjà consommé par H2 |
| Résolution des bougies | HOUR | Fixe, identique à H2 — H5 ne teste pas de changement de résolution |
| Actifs | Les 8 de la liste blanche (GOLD, US100, US30, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD) | Identique à H1/H2/H3/H4 |
| Source dédiée | `hypothesis5` | Compte démo Capital.com séparé, statistiques isolées (§2.11) |

**2 paramètres propres à cette hypothèse**, dans la limite des 2-3
imposée par §2.11 — 1 slot de marge, même situation que H4.

### Ce que cette hypothèse teste précisément (et ce qu'elle ne teste pas)

- Teste : régularité du profil de gains (variance, taux de trades
  clôturés en gain net) avec sortie progressive vs. sortie tout-en-
  trailing, sur une entrée d'edge identique à H2.
- Ne teste PAS : un nouvel edge d'entrée (déjà couvert par H2), une
  distance de TP différente de 1R/2R (pourrait faire l'objet d'une H6+
  future, jamais un ajustement de cette entrée), un mécanisme de sortie
  hybride autre que celui déjà câblé pour Station X.
- Conséquence attendue et acceptée d'avance : le R total moyen de H5
  peut être structurellement inférieur à celui de H2 sur le même flux de
  signaux (sécuriser tôt plafonne le haut de la distribution) — ce n'est
  pas un signe d'échec de l'hypothèse, c'est exactement le compromis
  qu'elle mesure.

### Budget de variables (invariant #10)

Modèle du 21/08/2026 (§2.11 vs §3.8, voir plus haut dans ce fichier) :
chaque hypothèse a son propre budget de 2-3 paramètres, jamais partagé.
H5 : 2/3 propres (TP1 R, TP2 R). 0/5 consommé sur `adaptive_rules`
(§3.8) — aucune promotion à ce jour, comme les 4 autres.

### Plafond §3.9 et correction pour comparaisons multiples

Décision permanente déjà actée le 21/08/2026 (entrée H4 ci-dessus,
confirmée par Ismaël dans cette même demande) : H5 autorisée en
exécution **DÉMO uniquement** (aucun capital réel engagé, statistiques
déjà isolées par source), même régime que H4. **Toute future
évaluation ou validation d'un résultat (promotion en réel, comparaison
entre hypothèses, décision d'arrêt/poursuite) devra désormais appliquer
la correction statistique pour comparaisons multiples calibrée sur 5
hypothèses simultanées (H1-H5), jamais 4** — voir `docs/DECISIONS.md`
(23/08/2026) pour l'application de cette mise à jour.

### Score de confiance du signal

Identique aux Hypothèses #1/#2/#3/#4 : entièrement déterministe,
`confidence = 1.0`, `boosted = False` (hérité du signal ICT sous-jacent,
jamais recalculé par H5).

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- N'introduit aucune nouvelle logique de détection d'entrée — délègue
  entièrement à `ict_strategy.evaluate_entry`.
- N'implique aucun LLM à aucune étape de la décision (invariant #1).
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée.
- Ne produit aucune conclusion statistique avant **10 trades minimum
  par variable réglable** (2 variables ici → 20 trades minimum avant
  toute interprétation, invariant #10) — comparaison H2 vs H5 incluse.
- **Aucun identifiant Capital.com câblé, aucune exécution réelle même
  démo** — en attente des identifiants du compte démo H5 dédié,
  qu'Ismaël fournira directement dans le terminal, jamais dans la
  conversation (même principe que H2/H3/H4).

Détail complet de l'implémentation (vérification de la réutilisation
sans duplication, tests, couverture) dans `docs/DECISIONS.md`
(23/08/2026).

---

## 2026-08-23 — Hypothèse #2 : bascule du régime de fond (MA200 -> structure BOS/CHoCH)

Décision d'Ismaël, appliquée le jour même. Cette entrée documente
UNIQUEMENT le changement du **régime** (niveau 1 de l'architecture à
deux niveaux §2.11) — le **déclencheur** (niveau 2 : swings fractals
K=2, zone de Fibonacci 61,8-78,6 %, FVG) reste rigoureusement inchangé.

### Motivation — théorique, pas empirique

Cohérence avec une vraie lecture de structure de marché ICT/Smart Money
Concepts, plutôt qu'un filtre de tendance générique (MA200) emprunté à
une autre famille d'hypothèses (H1/H3, trend-following classique).
`classify_structure_break` (BOS/CHoCH) était déjà codée et testée à
100% depuis la version d'origine de ce module (20-21/08/2026) mais
jamais branchée comme condition de décision — la proposition d'origine
notait explicitement : "**capacité disponible et testée, pas encore une
condition de décision**". Décidé AVANT tout résultat réel : aucun des 2
trades H2 déjà en base (ouverts sous l'ancien régime MA200) n'a été
regardé pour prendre cette décision — vérifié en écrivant cette entrée
avant de consulter la base de production.

### Traduction mécanique — `compute_structural_regime` (nouvelle fonction, `src/ict_strategy.py`)

`classify_structure_break(current_close, swing_highs, swing_lows, bias)`
exige un `bias` en entrée (elle CLASSE une cassure relative à un biais
donné, elle ne DÉTECTE pas un régime à partir de rien). Traduction
choisie, stateless (même contrat que `trend_strategy.compute_regime`
qu'elle remplace — aucun biais mémorisé d'un appel à l'autre) : un biais
CANDIDAT est essayé dans chaque sens ; celui qui produit un BOS (la
clôture courante dépasse le dernier swing confirmé dans le sens testé)
est retenu comme régime. Les deux tests (`bias="long"` donnant BOS, et
`bias="short"` donnant BOS) sont mutuellement exclusifs — aucune
ambiguïté, jamais besoin d'examiner les branches CHoCH séparément (un
CHoCH sous un biais donné correspond toujours au BOS symétrique sous le
biais opposé, même cassure).

**Conséquence structurelle découverte et documentée, pas corrigée** :
une clôture strictement À L'INTÉRIEUR de la zone de retracement de
Fibonacci d'une jambe ne peut, par construction algébrique, jamais
constituer un BOS/CHoCH de CETTE MÊME jambe (la zone 61,8-78,6 % est
toujours strictement comprise entre le swing bas et le swing haut de sa
propre jambe — prouvé et vérifié en exécutant le code réel avant
d'écrire cette entrée, voir docs/DECISIONS.md). Un signal H2 valide
exige donc une cassure structurelle RÉCENTE et DISTINCTE de la jambe
d'entrée elle-même (typiquement une cassure plus locale, formée après
l'extrémité de la jambe, pendant le repli). C'est un filtre nettement
plus strict que MA200 (qui ne dépendait d'aucune action récente du
prix) — la fréquence des signaux H2 va mécaniquement baisser. Accepté
comme le prix normal d'une lecture de structure plus fidèle à la
théorie ICT, pas un défaut à corriger.

### Séparation des trades pré/post-bascule — `trades.regime_type`

Les 2 trades H2 déjà en base ont été ouverts sous MA200, avant ce
changement — ils restent étiquetés comme tels, jamais mélangés aux
futurs trades H2 structurels dans une même statistique. Nouvelle colonne
`trades.regime_type` (`"ma200"` | `"structural_bos_choch"` | `NULL` pour
Station X, sans notion de régime) rendant cette séparation **vérifiable
en base**, pas seulement documentée ici — détail de la migration/
rétro-remplissage dans docs/DECISIONS.md.

### Ce qui NE change PAS

Le déclencheur (K=2, Fibonacci, FVG), le stop initial (swing opposé), la
sortie (trailing Donchian(20) de trend_strategy, réutilisé tel quel), le
budget de variables de H2 (K=2 reste son seul paramètre propre, 1/3),
les 8 actifs, la résolution horaire.

---

## 2026-08-23 — Hypothèse #5 : REDÉFINIE (confluence ICT + momentum RSI) — remplace l'entrée du même jour ci-dessus, jamais déployée sous l'ancienne définition

Pré-enregistrement écrit et daté **avant toute observation de
résultat** — aucun trade H5 n'a jamais existé, sous quelque définition
que ce soit (vérifié en base avant d'écrire cette entrée). Cette
entrée REMPLACE intégralement l'entrée "Hypothèse #5" plus haut dans ce
fichier (même date) : ce n'est PAS un ajustement sur des résultats
(convention de ce fichier, "Règle de modification" en en-tête) —
c'est une redéfinition avant toute donnée, décidée par Ismaël dans la
même conversation que la proposition d'origine.

### Ce qui change par rapport à l'entrée remplacée

| | Ancienne définition (remplacée) | Nouvelle définition (retenue) |
|---|---|---|
| Régime | MA200 (hérité de l'ancien H2) | **Structurel** (BOS/CHoCH, hérité du nouveau H2 — voir entrée ci-dessus) |
| Déclencheur | Confluence ICT de H2 seule | Confluence ICT de H2 **ET** RSI(14) franchissant 50 dans le même sens, même bougie |
| Sortie | §2.10 (TP1 1R/TP2 2R/trailing 20%) | **Inchangée** |

### Régime

Structurel, identique au nouveau régime de l'Hypothèse #2
(`ict_strategy.compute_structural_regime`) — jamais MA200. Hérité,
jamais recalculé différemment ici : aucun nouveau paramètre.

### Déclencheur

Confluence ICT de H2 (swings fractals K=2, zone de Fibonacci
61,8-78,6 %, FVG chevauchant la zone) **ET** momentum RSI(14)
franchissant le seuil 50 dans le sens de la structure, **au même
moment** (sur la même bougie que la confluence ICT) — les deux
conditions doivent être réunies pour qu'un signal se déclenche.
`ict_strategy.evaluate_entry` (régime + confluence, incluant déjà le
nouveau régime structurel) est réutilisée à l'identique ; seul le filtre
RSI est ajouté.

**Limite assumée, documentée explicitement (pas une simplification
cachée)** : combiner confluence ICT et momentum RSI dans une même
condition empêche d'attribuer un résultat à l'un ou l'autre facteur
séparément — si H5 sur- ou sous-performe H2, impossible de savoir
laquelle des deux couches explique l'écart. Accepté sciemment : H5
teste la COMBINAISON comme hypothèse à part entière, pas chacun des
deux facteurs isolément. Une hypothèse future pourrait tester le RSI
seul (sans confluence ICT) si ce résultat combiné le justifie — pas
construite ici, une nouvelle entrée le jour venu.

### Sortie (inchangée par rapport à la version remplacée)

Mécanisme §2.10 déjà construit pour Station X : TP1 50 % à 1R, TP2 30 %
à 2R, TP3 20 % sous trailing 2×ATR(14) plancher breakeven, stop déplacé
au breakeven dès TP1 touché (resserrement seulement, invariant #5). R
toujours calculé sur le risque initial DE CE TRADE H5, jamais recalculé
après une clôture partielle (§2.1).

### Paramètres exacts (choisis a priori, avant observation)

| Paramètre | Valeur | Statut |
|---|---|---|
| Régime structurel (BOS/CHoCH) | Hérité de H2 | Réutilisé, pas un nouveau choix |
| Confluence ICT (K=2, Fibonacci, FVG) | Hérité de H2 | Réutilisé, pas un nouveau choix |
| **Config RSI** (période + seuil de franchissement) | 14, 50 | **Paramètre #1** — conventions RSI standard (Wilder, période 14 ; seuil médian 50, ligne de partage momentum haussier/baissier), regroupées en un seul paramètre — même convention que la "config des bandes" de l'Hypothèse #4 |
| **Distance TP1** | 1R | **Paramètre #2** — valeur ronde, choisie a priori |
| **Distance TP2** | 2R | **Paramètre #3** — idem |
| TP3 (reliquat 20%) | Trailing 2×ATR(14) dès TP2 touché | Découle mécaniquement du mécanisme §2.10 réutilisé |
| Résolution des bougies | HOUR | Fixe, identique à H2 |
| Actifs | Les 8 de la liste blanche | Identique à H1/H2/H3/H4 |
| Source dédiée | `hypothesis5` | Compte démo Capital.com séparé |

**3 paramètres propres à cette hypothèse — pile au plafond des 2-3
imposé par §2.11, aucune marge.**

### Budget de variables (invariant #10)

Modèle du 21/08/2026 (§2.11 vs §3.8) : chaque hypothèse a son propre
budget de 2-3 paramètres, jamais partagé. H5 : 3/3 propres (config RSI,
TP1 R, TP2 R) — le régime structurel et la confluence ICT sont hérités
de H2, jamais "dépensés" une seconde fois (même précédent que H3
réutilisant MA200/Donchian(20) de H1). 0/5 consommé sur `adaptive_rules`
(§3.8) — aucune promotion à ce jour, comme les 4 autres.

### Indépendance vis-à-vis de Station X (vérifiée, pas seulement affirmée)

Aucune valeur (TP, R, niveau, prix) n'est lue depuis les signaux réels
ou la table `signals` de Station X — uniquement la mécanique (fractions
50/30/20, formule de trailing 2×ATR) du mécanisme §2.10, appliquée aux
propres entrée/stop/ATR de H5, calculés à partir des bougies H5
elles-mêmes. Détail de la vérification (lecture du code, aucune requête
touchant Station X) dans docs/DECISIONS.md.

### Score de confiance du signal

Identique aux autres hypothèses : entièrement déterministe,
`confidence = 1.0`, `boosted = False`.

### Ce que cette hypothèse NE fait PAS (rappel des garde-fous)

- N'introduit aucune nouvelle logique de détection de régime ou de
  confluence — délègue entièrement à `ict_strategy.evaluate_entry`.
- N'implique aucun LLM à aucune étape de la décision (invariant #1).
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution = nouvelle entrée datée.
- Ne produit aucune conclusion statistique avant **10 trades minimum
  par variable réglable** (3 variables ici → 30 trades minimum avant
  toute interprétation, invariant #10) — comparaison H2 vs H5 incluse.

### Plafond §3.9 et correction pour comparaisons multiples

Inchangé par rapport à l'entrée remplacée : H5 autorisée en démo
uniquement, correction pour comparaisons multiples calibrée sur **5
hypothèses simultanées (H1-H5), jamais 4** pour toute future validation.

Détail complet de l'implémentation (RSI de Wilder, tests, couverture,
vérification en direct sur le VPS) dans `docs/DECISIONS.md` (23/08/2026).

---

## 2026-08-23 — Hypothèses #2 et #3 : sortie basculée vers TP1/TP2/trailing (décision explicite d'Ismaël)

Décision d'Ismaël, prise et appliquée le jour même — assumée pleinement,
va à l'encontre de la recommandation de préserver H3 comme copie exacte
de H1 (entrée du 20/08/2026 ci-dessus : « identique à l'Hypothèse #1,
seule la résolution de bougie change », dont l'intérêt explicite était
une isolation "timeframe seule" pour une comparaison propre avec H1).
Détail complet de l'implémentation, de la vérification "prospectif
uniquement", et des tests dans `docs/DECISIONS.md` (23/08/2026).

**Ce qui change** : sortie uniquement — TP1 50 % à 1R / TP2 30 % à 2R /
TP3 20 % sous trailing 2×ATR(14), le mécanisme §2.10 déjà en place pour
Station X et H5, réutilisé sans nouvelle logique. Régime/déclencheur de
H2 (structure BOS/CHoCH + confluence ICT) et de H3 (MA200 + Donchian(20))
strictement inchangés.

**Ce qui ne change pas** : H1 reste en trailing Donchian(20) pur, seul
témoin restant de ce mécanisme — la seule partie de l'isolation
d'origine qui survit à cette décision.

**Budget de variables** : aucun nouveau paramètre de DÉCISION pour H2 ou
H3 (TP1=1R/TP2=2R sont les mêmes valeurs déjà retenues pour H5, pas un
second choix indépendant) — un changement de MÉCANISME de sortie, pas
une nouvelle variable réglable au sens de l'invariant #10.

---

## 2026-08-23 — Couche session/multi-timeframe sur H1-H5 — PROPOSITION, PAS VALIDÉE, AUCUN CODE CONSTRUIT

Demandée par Ismaël comme ajout PAR-DESSUS le déclencheur existant de
chaque hypothèse (jamais un remplacement, jamais une fusion vers une
logique générique commune) : chaque hypothèse garde son propre principe
(H2 confluence ICT, H3 rupture Donchian, H4 Bollinger, H5 ICT+RSI).

Trois ajouts proposés, non encore implémentés :
1. Analyse à l'ouverture des sessions (Asie 00h/Londres 08h/New York
   13h UTC par défaut) — périmètre exact (quelles hypothèses) non
   tranché, voir ci-dessous.
2. Confirmation de régime (tendance/contre-tendance) via un indicateur
   technique ET un indice boursier combinés.
3. Exécution de l'entrée sur M15/M30 (au lieu de la bougie horaire
   actuelle de H1/H2/H4/H5) — périmètre et choix M15 vs M30 non
   tranchés.

### Collision de nommage à lever avant toute chose

Le prompt d'Ismaël emploie "H1" dans deux sens différents dans la même
demande : "H1/H4" (point 1) désigne très probablement l'Hypothèse #1 et
l'Hypothèse #4 ; "au lieu de H1" (point 3) désigne la bougie horaire
(convention M15/M30/H1). Lecture retenue pour cette proposition, à
confirmer explicitement par Ismaël avant tout code — une mauvaise
interprétation ici fausserait tout le reste.

### Périmètre — non tranché

Les points 1 et 3 s'appliquent-ils uniquement à H1/H4 (nommées au point
1), ou aux 5 hypothèses ? Le point 2 (confirmation de régime) est
discuté pour H2/H3/H4/H5 dans la demande d'Ismaël — H1 non mentionnée
explicitement mais suivrait logiquement la même lecture que H3 (même
mécanisme Donchian). Pas de décision prise ici.

### H3 (Donchian) et H4 (Bollinger) — lecture confirmée

H3 : rupture de canal dans le sens du régime = continuation = tendance
par construction. H4 : toucher de bande opposée au régime = retour à la
moyenne = contre-tendance par construction. Cohérent avec les mécanismes
déjà en place, rien à trancher ici.

### H2/H5 (confluence ICT) — trois options présentées, aucune tranchée

La confluence ICT (retracement Fibonacci dans le sens du régime
structurel, confirmé par un FVG) n'est ni une rupture pure (tendance) ni
un fading pur (contre-tendance) — une continuation après repli, dans une
structure déjà établie.

- **Option A** : assimiler à "tendance" (toujours dans le sens du
  régime structurel). Simple, aucun nouveau paramètre, mais mélange deux
  mécaniques d'entrée réellement différentes (rupture vs retracement)
  sous une même étiquette pour une future analyse.
- **Option B** : troisième catégorie de régime ("continuation
  structurée"), distincte de tendance/contre-tendance. Honnête
  analytiquement, mais exige une logique de confirmation séparée pour
  H2/H5 (pas juste un binaire tendance/contre-tendance).
- **Option C** : ne pas appliquer la confirmation tendance/contre-
  tendance à H2/H5 — leur régime structurel joue déjà ce rôle de filtre
  directionnel, une seconde confirmation serait redondante. Économe en
  budget (voir ci-dessous), H2/H5 recevraient alors seulement les points
  1/3 si ceux-ci s'appliquent à elles.

### Règle de combinaison indicateur technique + indice boursier — proposée

**ET strict** (indicateur technique ET indice boursier doivent
concorder) proposé comme règle simple, fixée a priori, sans nouveau
seuil réglable. OU écarté (trop permissif, l'actif et un indice large
sont déjà souvent corrélés — un OU filtrerait à peine plus que
l'indicateur seul). Pondération écartée (introduirait un seuil de score
à fixer, contraire à "simple et fixée a priori" ; ET est déjà une
pondération binaire égale, seuil = accord total, sans paramètre
supplémentaire).

**Ouvert** : quel indice boursier précisément ? US100/US30 déjà dans la
liste blanche (aucune nouvelle source de données nécessaire), mais lequel
utiliser, pour quel actif/quelle session — un seul indice universel ou
un indice par session (aucun indice asiatique dans la liste blanche
actuelle) ? Non tranché, pas assez d'éléments pour proposer un choix
unique sans deviner.

### Budget de variables (§2.11) — ALERTE, dépassement probable pour plusieurs hypothèses

État actuel : H1 1/3, H2 1/3, H3 1/3, H4 2/3, **H5 3/3 (déjà au
plafond, zéro marge)**. Trois nouveaux paramètres candidats identifiés :
config sessions (bornes horaires, groupées en un seul paramètre comme
la config Bollinger de H4 — **mais les bornes citées ici, 00h/08h/13h,
diffèrent légèrement de celles déjà en place dans `session_marker.py`
pour la collecte, 00h/07h/13h — à harmoniser, pas à faire coexister**),
choix de l'indice boursier, choix du timeframe d'exécution (M15/M30).

**H5 ne peut recevoir cette couche sans dépasser le plafond**, quel que
soit le périmètre exact retenu (déjà à 3/3). H4 (2/3) et H2 (1/3)
risquent également un dépassement selon combien des 3 ajouts leur
sont appliqués. H3 reste dans le plafond si le point 3 (déjà M15) ne
lui est pas appliqué. Signalé, non corrigé — le choix du périmètre
(section précédente) et la question de savoir si les bornes de session
strictement fixes comptent comme un paramètre "réglable" au sens de
l'invariant #10 restent à trancher par Ismaël.

### Discipline confirmée pour la suite

Trades existants gardent leurs `regime_type`/`exit_type` d'origine,
jamais mélangés avec de futurs trades sous cette couche. Aucun code,
aucun test, aucune migration DB écrits pour cette proposition — attente
de la validation d'Ismaël avant toute implémentation.

---

## 2026-08-23 — Couche session/multi-timeframe : les 4 points tranchés par Ismaël, conception finale — TOUJOURS AUCUN CODE, en attente de validation finale

Les 4 décisions d'Ismaël sur l'entrée précédente :
1. **Périmètre** : H2/H3/H4/H5 uniquement — **H1 exclue intégralement**,
   inchangée (`trend_executor.py` non touché).
2. **H2/H5** : option C — pas de confirmation tendance/contre-tendance
   (déjà couvertes par leur régime structurel BOS/CHoCH). Reçoivent
   uniquement la fenêtre de session et l'exécution M15/M30.
3. **Indice boursier** : US30/US100 (déjà liste blanche) pour
   Londres/New York ; aucun indice pour la session Asie, indicateur
   technique seul.
4. **Budget §2.11** : les bornes de session fixes (00h/08h/13h UTC) ne
   comptent pas dans le budget 2-3 paramètres — faits calendaires,
   même principe que les fenêtres macro fixes du §2.9.

Sur cette base, conception complète ci-dessous — **une ambiguïté
subsiste et est signalée, pas résolue silencieusement** (voir "Point
restant" plus bas).

### Mécanique de la fenêtre de session (précision, découle de la décision #4)

Gate binaire sur l'heure UTC courante de la boucle : évaluation d'entrée
autorisée uniquement si `heure_utc ∈ {0, 8, 13}` (l'heure pleine
suivant chaque ouverture — 00h00-00h59, 08h00-08h59, 13h00-13h59), pas
une fenêtre à largeur réglable séparée — cohérent avec la décision #4
(fait calendaire fixe), évite d'introduire un second paramètre de
largeur non demandé.

### Régime de fond commun H3/H4 — mécanisme proposé pour résoudre "quel indicateur technique"

Ni la demande initiale ni les 4 décisions ne précisent QUEL indicateur
technique sert de confirmation — un nouvel indicateur (ex. ADX) exigerait
sa propre justification théorique a priori (invariant #10) avant d'être
seulement envisagé. **Proposition pour éviter d'en introduire un** :
réutiliser `trend_strategy.compute_regime` (déjà niveau 1 commun à
H1/H3/H4, MA200) tel quel, appliqué à l'indice de confirmation au lieu
d'un nouvel indicateur — combiné en ET avec le régime déjà calculé sur
l'actif (§2.10 de la proposition précédente). Zéro nouvelle logique de
calcul, zéro nouveau type d'indicateur : uniquement une seconde
application de la même fonction, à un second instrument.

- **Session Asie (0h)** : régime MA200 de l'actif seul (déjà existant,
  niveau 1 — aucun changement, la confirmation "technique seule" de la
  décision #3 est donc un pass-through, sans filtrage additionnel pour
  cette session).
- **Sessions Londres/NY (8h/13h)** : régime MA200 de l'actif ET régime
  MA200 de l'indice (US30 ET US100, les deux — pas un choix entre les
  deux, cohérent avec le ET déjà retenu pour la règle de combinaison,
  jamais une règle différente pour un cas particulier) doivent
  concorder.
- **Mécanisme partagé entre H3 et H4**, pas deux calculs séparés : les
  deux utilisent déjà `compute_regime` comme niveau 1 (H4 : "identique à
  H1/H3", voir son entrée d'origine) — la confirmation par indice est la
  MÊME extension pour les deux, jamais un second choix indépendant.
- **H3 (tendance)** : la rupture de canal Donchian ne se déclenche que
  si ce régime confirmé (actif + indice) est établi.
- **H4 (contre-tendance)** : le toucher de bande Bollinger ne se
  déclenche que sous le même régime confirmé — H4 fade une extension
  COURT TERME à l'intérieur d'un régime de fond LONG TERME toujours
  confirmé, jamais le régime lui-même (cohérent avec son garde-fou
  d'origine : "ne fade jamais contre le régime de fond").

**Point restant, pas tranché par les 4 décisions** : cette lecture
(réutiliser `compute_regime` plutôt qu'un nouvel indicateur) est ma
proposition pour éviter d'introduire une variable non justifiée a
priori — à confirmer explicitement avant tout code, ce n'est pas
automatiquement ce qu'"indicateur technique" désignait dans la demande
d'origine.

### M15/M30 — exécution de l'entrée

Choix entre les deux non tranché par la demande d'origine. Proposition :
**M15**, pas M30 — déjà la résolution de H3 (aucun troisième palier de
temps à maintenir dans le projet), écart minimal. M30 écarté comme
alternative (pas de raison théorique de préférer un troisième palier
inédit à une valeur déjà éprouvée en production).

- **H2, H4** : passent de HOUR à M15 pour l'évaluation d'entrée (leur
  résolution actuelle, HOUR, est remplacée).
- **H3** : déjà M15 — **aucun changement**, ce point est un no-op pour
  elle.
- **H5** : voir "Blocage budget" ci-dessous — pas appliqué en l'état.

### Budget de variables final, par hypothèse

| Hypothèse | Budget existant | Nouveau(x) paramètre(s) | Total | Statut |
|---|---|---|---|---|
| H1 | 1/3 (Donchian N=20) | — (exclue) | 1/3 | inchangée |
| H2 | 1/3 (K=2) | +1 (timeframe M15, HOUR→M15) | 2/3 | dans le budget |
| H3 | 1/3 (Donchian N=20) | +1 (indices de confirmation US30+US100, partagé avec H4, compté une seule fois) | 2/3 | dans le budget |
| H4 | 2/3 (config bandes + multiplicateur stop) | +0 (indices, déjà comptés côté H3) +1 (timeframe M15) | 3/3 | **au plafond, zéro marge** |
| H5 | 3/3 (config RSI + TP1 R + TP2 R, déjà au plafond) | +1 (timeframe M15) | **4/3** | **DÉPASSE LE PLAFOND** |

Le partage du paramètre "indices de confirmation" entre H3 et H4 (compté
une seule fois, comme la réutilisation MA200/Donchian(20) de H3 vis-à-vis
de H1) est un jugement de ma part, pas un précédent identique — à
confirmer : la valeur ET le mécanisme sont strictement identiques pour
les deux, mais c'est la première fois que DEUX hypothèses INTRODUISENT
un même paramètre simultanément plutôt que l'une réutilisant l'acquis de
l'autre.

### Blocage budget non résolu par les 4 décisions : H5 dépasse le plafond

Même après l'option C (aucune confirmation de régime pour H5) et la
gratuité des bornes de session (décision #4), **le seul ajout du
changement de timeframe (HOUR→M15) fait passer H5 de 3/3 à 4/3** —
au-delà du plafond 2-3 de §2.11. La fenêtre de session, elle, ne coûte
rien (décision #4) et peut s'appliquer à H5 sans problème.

**Deux options, je ne tranche pas** :
- **Option 1** : H5 exclue du changement de timeframe (reste HOUR),
  comme H1 est exclue de toute la couche — H5 reçoit alors UNIQUEMENT la
  fenêtre de session, reste à 3/3, aucun dépassement.
- **Option 2** : le plafond est explicitement dépassé pour H5 par
  décision assumée d'Ismaël (comme le §3.9 "3 hypothèses par cycle" a
  déjà été explicitement dépassé en démo pour H4/H5, voir l'entrée du
  21/08/2026) — dans ce cas, le journaliser comme un écart assumé, pas
  un oubli.

### Résumé de la conception finale par hypothèse

- **H1** : totalement inchangée.
- **H2** : confluence ICT inchangée (entrée) ; ajoute fenêtre de session
  (0h/8h/13h, gratuite) + exécution M15 (HOUR→M15, 1 nouveau paramètre).
  Aucune confirmation de régime (option C). Budget 2/3.
- **H3** : rupture Donchian inchangée (entrée) ; ajoute fenêtre de
  session (gratuite) + confirmation de régime actif+indice (US30 ET
  US100 pour Londres/NY, technique seul pour Asie — 1 nouveau paramètre
  partagé avec H4). Timeframe déjà M15, aucun changement. Budget 2/3.
- **H4** : Bollinger/contre-tendance inchangé (entrée) ; ajoute fenêtre
  de session (gratuite) + même confirmation de régime que H3 (partagée,
  déjà comptée) + exécution M15 (HOUR→M15, 1 nouveau paramètre). Budget
  3/3, au plafond.
- **H5** : ICT+RSI inchangé (entrée) ; ajoute fenêtre de session
  (gratuite) uniquement. Exécution M15 **en attente de la décision
  Option 1/2 ci-dessus** avant d'être confirmée ou écartée.

### Discipline inchangée

Trades existants gardent leurs `regime_type`/`exit_type` d'origine,
jamais mélangés. Aucun code, aucun test, aucune migration DB — en
attente de la validation finale d'Ismaël (incluant le point restant sur
l'indicateur technique et le blocage budget H5 ci-dessus) avant toute
construction.

---

## 2026-08-23 — Couche session/multi-timeframe : PRÉ-ENREGISTREMENT FINAL, décision d'Ismaël maintenue après mise en garde — CONSTRUITE

Décision d'Ismaël, **maintenue après plusieurs mises en garde** de ma
part sur la perte de la structure de comparaison isolée H1/H3
construite le jour même — il l'assume pleinement. Ce pré-enregistrement
fixe la conception AVANT toute donnée sous cette nouvelle couche
(aucun trade H2/H3/H4/H5 n'a encore été généré sous elle au moment de
cette entrée) ; l'implémentation qui a suivi est détaillée dans
`docs/DECISIONS.md`.

Résolution des deux derniers points laissés ouverts par l'entrée
précédente :

**Indicateur technique de confirmation** : réutilisation de
`trend_strategy.compute_regime` (MA200), appliqué à l'indice de
confirmation — confirmée, pas de nouvel indicateur. C'est une
**confirmation d'alignement directionnel entre marchés** (le régime de
l'indice concorde-t-il avec celui de l'actif ?), **PAS un
classificateur de force de tendance** (un ADX ou équivalent
mesurerait autre chose) — clarification explicite d'Ismaël, à ne
jamais présenter comme équivalent dans une future analyse.

**Budget H5** : le dépassement (3/3 → 4/3, causé par le seul ajout du
timeframe M15) est **explicitement assumé par Ismaël**, maintenu après
mise en garde — H5 reçoit la fenêtre de session ET le passage à M15
(pas l'option "H5 exclue du timeframe" envisagée dans l'entrée
précédente). Traité comme un écart assumé, même précédent que le
dépassement déjà accepté du plafond §3.9 pour H4/H5.

### Règle de combinaison US30/US100 — précisée

US30 et US100 ne peuvent pas se confirmer eux-mêmes (un instrument ne
confirme jamais son propre régime) : US30 confirmé par US100 seul, et
inversement. Les 6 autres actifs de la liste blanche confirmés par les
DEUX indices combinés, en ET strict (les deux doivent concorder avec le
régime de l'actif) — extension directe du ET déjà retenu pour toute la
couche, jamais une règle différente pour ce cas particulier.

### Conception finale construite

- **H1** : totalement inchangée, exclue de toute la couche.
  `trend_executor.py` non modifié, testé en régression stricte
  (`test_run_trend_loop_untouched_by_session_multi_timeframe_layer`).
- **H2** : confluence ICT inchangée. Ajoute fenêtre de session (0h/8h/
  13h UTC) + exécution M15 (au lieu de HOUR). Aucune confirmation de
  régime (option C). Budget 2/3.
- **H3** : rupture Donchian inchangée. Ajoute fenêtre de session +
  confirmation de régime croisée (US30 ET US100 pour Londres/NY,
  indicateur technique seul pour l'Asie) — H3 "tendance", ne se
  déclenche que si le régime confirmé concorde. Résolution déjà M15,
  aucun changement. Budget 2/3.
- **H4** : Bollinger/contre-tendance inchangé. Ajoute fenêtre de
  session + même confirmation de régime que H3 (partagée, mécanisme
  identique) + exécution M15 (au lieu de HOUR) — H4 "contre-tendance",
  fade une extension court terme À L'INTÉRIEUR du régime confirmé,
  jamais contre lui. Budget 3/3, au plafond.
- **H5** : ICT+RSI inchangé. Ajoute fenêtre de session + exécution M15
  (au lieu de HOUR). Aucune confirmation de régime (option C). Budget
  **4/3, dépassement explicitement assumé** (voir ci-dessus).

### Ce que cette couche n'est PAS (rappel, demande explicite d'Ismaël)

Pas une fusion des 4 hypothèses en une méga-stratégie générique. Chaque
hypothèse garde intégralement son propre déclencheur (confluence ICT
pour H2, rupture Donchian pour H3, Bollinger pour H4, ICT+RSI pour H5) —
seule une couche de TIMING (fenêtre de session, résolution d'exécution)
et, pour H3/H4 seulement, de CONFIRMATION CROISÉE est partagée. Aucune
des quatre logiques d'entrée n'a été modifiée par cette couche.

### Discipline confirmée

Trades H2/H3/H4/H5 déjà en base avant cette couche gardent leurs
`regime_type`/`exit_type` d'origine ; nouvelle colonne INDÉPENDANTE
`trades.timing_layer` (`NULL` pour H1/Station X, jamais concernées ;
`"aucune"` rétro-rempli pour les trades antérieurs ; `"session_multi_
tf"` pour les nouveaux) rend cette séparation vérifiable en base, pas
seulement documentée. Détail complet de l'implémentation, des tests, du
déploiement et de la vérification en direct dans `docs/DECISIONS.md`
(23/08/2026).

---

## Exemption crypto de la couche session/multi-timeframe (23/08/2026)

Demande explicite d'Ismaël, après constat en direct que la couche
session/multi-timeframe (ci-dessus) bloquait toute génération de signal
crypto (BTCUSD, ETHUSD) hors des 3 fenêtres UTC (0h/8h/13h) sur H2/H3/
H4/H5, alors que le marché crypto ne ferme jamais — contrairement au
forex/indices/GOLD, pour qui la fenêtre de session a un sens (heures de
marché réelles). Message d'Ismaël (verbatim, reformulé pour la clarté
typographique) : "modifie uniquement pour la crypto, pour toutes les
hypothèses — la logique et la stratégie restent les mêmes, juste les
heures d'analyse et de déclenchement changent pour la crypto : une
analyse en continu des marchés."

**Décision** : BTCUSD/ETHUSD sont exemptés, pour H2/H3/H4/H5
uniquement (même périmètre que la couche elle-même, H1 toujours
exclue), de deux mécanismes :
1. La fenêtre de génération de signaux (0h/8h/13h UTC) —
   `_should_generate_signals` retourne toujours True pour la crypto,
   quelle que soit l'heure. Le déclencheur propre à chaque hypothèse
   (confluence ICT pour H2, Donchian pour H3, Bollinger pour H4,
   ICT+RSI pour H5) est réévalué à chaque itération de boucle (~60s),
   comme avant l'introduction de la couche de session.
2. La confirmation de régime croisée (H3/H4 uniquement) —
   `regime_confirmation.confirm_regime` retourne toujours True pour la
   crypto. Sans cette seconde exemption, la première aurait été
   neutralisée en pratique : la branche défensive "heure hors session"
   de `_confirm_regime` (fail-closed, jamais censée être atteinte en
   usage normal) serait devenue le cas normal pour la crypto la plupart
   du temps, rejetant silencieusement la quasi-totalité des signaux
   crypto d'H3/H4.

**Raison de la seconde exemption (US30/US100)** : les indices de
confirmation (US30, US100) sont eux-mêmes liés à des heures de marché
actions, structurellement proches de la fenêtre de session Londres/NY —
contrairement au forex/GOLD/indices, la crypto n'a pas d'horloge de
session à faire correspondre à celle des indices de confirmation. Les
associer en continu (24/7 crypto contre heures de marché actions
partielles) n'aurait pas de rationnel théorique a priori — écarté plutôt
que codé sans justification (invariant #10).

**Le déclencheur propre à chaque hypothèse (Donchian, Bollinger,
confluence ICT, ICT+RSI) n'est pas modifié pour la crypto** — seule la
CADENCE d'évaluation change (continue au lieu de 3 fenêtres/jour), et
pour H3/H4 le filtre de confirmation croisée est levé pour ces deux
actifs. Aucune nouvelle donnée historique n'est utilisée : `get_candles`
interrogeait déjà, avant comme après ce changement, les bougies les
plus récentes disponibles à chaque appel — "analyse en continu... et de
l'historique" (formulation d'Ismaël) se traduit ici par une évaluation
plus fréquente sur les mêmes données de marché, pas par un nouvel
indicateur ni une fenêtre d'historique différente.

**Budget §2.11** : traité comme les bornes de session fixes déjà
tranchées non comptées (précédent explicite ci-dessus) — la liste des
actifs crypto (BTCUSD, ETHUSD) est un fait structurel fixe (la
composition de la liste blanche §1.2), pas une variable ajustée ou
ajustable sur la base des résultats de trading. Aucun changement aux
budgets déjà comptés par hypothèse (H2 2/3, H3 2/3, H4 3/3, H5 4/3
dépassement déjà assumé).

Implémentation, tests (100% sur `regime_confirmation.py`, cas ajoutés
sur `_should_generate_signals`) et déploiement détaillés dans
`docs/DECISIONS.md` (23/08/2026).

---

## Correction de la couche session/multi-timeframe : recalibration, pas porte (23/08/2026, fin de journée)

**Écrite et datée AVANT tout test de cette conception corrigée**, comme
pour toute évolution de ce fichier (règle en tête de document). Remplace
le principe de la couche session/multi-timeframe déployée plus tôt le
même jour et de son exemption crypto ajoutée dans la foulée — les deux
sont documentées comme remplacées, pas supprimées silencieusement de
l'historique (voir `docs/DECISIONS.md`).

### Principe corrigé

La fenêtre de session (0h/8h/13h UTC) n'est plus une porte sur la
génération de signaux, pour aucun actif :
- Le déclencheur propre à chaque hypothèse (Donchian pour H3, confluence
  ICT pour H2/H5, toucher de bande Bollinger pour H4) est évalué à
  CHAQUE cycle (~60s), toute la journée, sur les 8 actifs — aucun
  blocage en dehors des 3 fenêtres, pour aucun actif, y compris
  BTCUSD/ETHUSD.
- Pour H3 et H4 (confirmation tendance/contre-tendance) : la
  confirmation de régime (alignement US30/US100) devient un CONTEXTE
  calculé/rafraîchi aux 3 ouvertures de session, qui reste actif jusqu'au
  prochain rafraîchissement — un trigger ne devient un trade que si le
  régime actuellement actif en cache correspond à ce que l'hypothèse
  exige. Elle n'est plus recalculée à la volée pour chaque signal
  individuel.
- Pour H2 et H5 (régime déjà structurel et continu via BOS/CHoCH) :
  aucun rôle utile pour la fenêtre de session — retirée complètement
  pour elles (elles n'ont jamais eu de confirmation croisée non plus,
  option C, inchangé).

### Crypto — plus de cas particulier

L'exemption crypto (BTCUSD/ETHUSD pass-through sur la génération ET sur
la confirmation croisée, ajoutée plus tôt le 23/08/2026) devient
redondante sous ce principe : les 6 autres actifs reçoivent désormais le
même traitement continu que la crypto recevait déjà. Retirée du code
(`regime_confirmation.CRYPTO_ASSETS`/`confirm_regime`,
`technical_strategy_executor._should_generate_signals`).

### Application rétroactive — profit-taking uniquement, trades H2/H3 encore ouverts

Écart assumé par rapport à la discipline "prospectif uniquement" déjà
suivie pour la bascule TP1/TP2/TP3 de H2/H3 du même jour (décision
explicite d'Ismaël, cette fois) : toute position H2/H3 encore OUVERTE au
moment de ce déploiement (jamais les trades déjà clôturés) est basculée
vers le mécanisme de profit-taking défini pour son hypothèse (TP1(1R)/
TP2(2R)/reliquat trailing pour H2/H3 — H4 est déjà TP fixe depuis son
origine, H5 déjà TP1/TP2/trailing depuis son origine, aucun des deux
n'a de trade en `trailing_pur` à convertir). TP1/TP2 calculés à partir
de l'entrée et du stop INITIAL déjà enregistrés pour chaque trade (jamais
recalculés selon son évolution depuis l'ouverture) — cohérent avec
l'invariant #7 (passer au breakeven à TP1 est un resserrement, jamais un
élargissement) et avec les formules déjà définies (mêmes
`TP1_R_MULTIPLE`/`TP2_R_MULTIPLE` que `hypothesis2_strategy.py`/
`hypothesis3_strategy.py`, aucun ajustement basé sur le résultat de ces
trades précis). Ces trades reçoivent `exit_type = "tp_partiel_
retroactif"`, jamais fusionné avec `trailing_pur` ni avec `tp_partiel`
classique (les trades ouverts APRÈS la bascule prospective du même jour,
qui avaient tp1/tp2 dès l'origine). Détail par trade (id, ancien/nouveau
exit_type, tp1/tp2 calculés) dans `docs/DECISIONS.md`.

### Ce qui reste inchangé

- H1 totalement intacte, exclue de toute cette couche.
- Exécution M15/M30 pour H2/H3/H4/H5 (dépassement du plafond §2.11 pour
  H5 toujours assumé, inchangé).
- Confirmation par indice (réutilisation de `compute_regime`/MA200)
  documentée pour ce qu'elle fait réellement — alignement directionnel,
  jamais présentée comme classificateur de force de tendance.
- Budget §2.11 : traité comme la fenêtre de session elle-même déjà
  tranchée non comptée (précédent explicite) — un changement de
  MÉCANISME de rafraîchissement (aux 3 ouvertures plutôt qu'à la volée)
  n'introduit aucune variable ajustable supplémentaire, mêmes indices,
  mêmes règles ET, mêmes constantes `SESSION_OPEN_HOURS_UTC`.

Implémentation, tests (100% sur `regime_confirmation.py`), rapport par
trade de l'application rétroactive et déploiement détaillés dans
`docs/DECISIONS.md` (23/08/2026).

---

## 2026-08-24 — Hypothèse #5 : V3, retrait de la confluence ICT (régime structurel + RSI seuls) — remplace l'entrée "REDÉFINIE" du 23/08/2026 ci-dessus

Demande explicite d'Ismaël, motivée par une observation opérationnelle
**pas un résultat de trade** (cohérent avec la "Règle de modification"
en en-tête et invariant #10) : la version V2 (régime structurel +
confluence ICT complète + RSI, trois conditions réunies) n'a produit
**AUCUN signal, donc aucun trade**, en ~26h de fonctionnement en
production depuis son déploiement le 23/08/2026 après-midi (vérifié en
base le 24/08/2026 : `SELECT COUNT(*) FROM signals WHERE
source='hypothesis5'` -> 0). Pas un ajustement sur des données
observées — H5 n'a produit aucune donnée à ajuster.

### Ce qui change par rapport à l'entrée remplacée (V2, 23/08/2026)

| | V2 (remplacée) | V3 (retenue) |
|---|---|---|
| Régime | Structurel (BOS/CHoCH, hérité de H2) | **Inchangé** |
| Déclencheur | Confluence ICT de H2 (Fibonacci+FVG) **ET** RSI(14) franchissant 50, même bougie | Régime structurel **ET** RSI(14) franchissant 50, même bougie — **confluence ICT retirée** |
| Sortie | §2.10 (TP1 1R/TP2 2R/trailing 20%) | **Inchangée** |

### Déclencheur (V3)

Régime structurel confirmé (BOS/CHoCH, `ict_strategy.
compute_structural_regime`, réutilisé) **ET** momentum RSI(14)
franchissant le seuil 50 dans le sens de la structure, au même moment
(entre l'avant-dernière et la dernière bougie fournies — condition de
franchissement strict, **inchangée depuis la V2**,
`hypothesis5_strategy._rsi_just_crossed_threshold`). La confluence ICT
(swings fractals K=2 comme ancrage, zone de Fibonacci 61,8-78,6 %, FVG
chevauchant la zone) est retirée : c'était la composante la plus
restrictive des trois conditions de la V2. `ict_strategy.evaluate_entry`
(régime + jambe + confluence complète, utilisée par la V2) est
remplacée par `ict_strategy.compute_structural_entry` (nouvelle,
régime + jambe SEULS, extraite de `evaluate_entry` le 24/08/2026 pour
cet usage précis — voir docs/DECISIONS.md ; `evaluate_entry` elle-même,
utilisée par H2, reste strictement inchangée, vérifiée par régression).

**Rationale du retrait** : éviter le doublon avec H2 (qui reste régime +
confluence ICT complète, inchangée) — H5 devient une hypothèse
distincte (régime + momentum RSI), pas une redite de H2 avec RSI ajouté
par-dessus. La confluence ICT était par construction la couche la plus
restrictive (H2 seule, qui la conserve, produit déjà peu de signaux —
voir l'entrée du 23/08/2026 sur la bascule du régime structurel, décrite
comme "un filtre nettement plus strict que MA200").

**Précision sur la demande d'origine** : la demande de cette révision
mentionnait une "fenêtre de 3 bougies déjà en place" pour le
franchissement RSI. Vérifié en relisant le code avant d'écrire cette
entrée : ce n'est pas exact — `_rsi_just_crossed_threshold` compare
uniquement les deux dernières bougies (franchissement strict), jamais
une fenêtre de 3, ni en V2 ni maintenant. Conservé tel quel (rien à
élargir qui existait déjà) ; une fenêtre de tolérance plus large serait
un nouveau paramètre à justifier séparément (invariant #10), non fait
ici faute de demande explicite sur ce point précis.

### Sortie (inchangée)

Mécanisme §2.10 déjà construit pour Station X, sans aucune modification :
TP1 50 % à 1R, TP2 30 % à 2R, TP3 20 % sous trailing 2×ATR(14) plancher
breakeven, stop déplacé au breakeven dès TP1 touché.

### Paramètres exacts

| Paramètre | Valeur | Statut |
|---|---|---|
| Régime structurel (BOS/CHoCH) | Hérité de H2 | Réutilisé, pas un nouveau choix |
| Confluence ICT (K=2, Fibonacci, FVG) | — | **Retirée** (n'est plus une condition d'entrée) |
| **Config RSI** (période + seuil de franchissement) | 14, 50 | **Paramètre #1**, inchangé |
| **Distance TP1** | 1R | **Paramètre #2**, inchangé |
| **Distance TP2** | 2R | **Paramètre #3**, inchangé |
| Résolution des bougies | MINUTE_15 | Inchangée (couche session/multi-timeframe du 23/08/2026, toujours 4/3 — dépassement déjà assumé) |
| Actifs | Les 8 de la liste blanche | Inchangé |
| Source dédiée | `hypothesis5` | Inchangée |

**Toujours 3 paramètres propres à cette hypothèse — le retrait de la
confluence ICT ne change pas le compte (elle était héritée de H2,
jamais comptée dans le budget propre de H5) — le dépassement 4/3 déjà
assumé pour la résolution M15 reste inchangé, voir entrée du
23/08/2026.**

### Fréquence de signaux attendue (estimation, pas une donnée observée)

Retirer la couche la plus restrictive (confluence ICT à trois
conditions géométriques : swings fractals confirmés + fenêtre Fibonacci
étroite [61,8-78,6%] + FVG chevauchant) augmente mécaniquement la
fréquence de signaux par rapport à la V2 (qui en a produit 0 en 26h) —
il ne reste plus que deux conditions (régime structurel + RSI), toutes
deux nettement moins restrictives individuellement que la géométrie
Fibonacci/FVG. Pas de chiffre précis avancé ici (aucun backtest
disponible sur ce projet, voir invariant #10 — pas de sur-ajustement a
priori) : à observer en production, comme pour toute autre hypothèse.
Attendu néanmoins plus fréquent que H2 (qui garde la confluence ICT
complète) et plus rare que H3 (régime seul, sans second filtre) — H5 se
positionne entre les deux par construction.

### Indépendance vis-à-vis de Station X

Inchangée : aucune valeur lue depuis les signaux réels ou la table
`signals` de Station X — voir docs/DECISIONS.md pour la vérification.

### Ce que cette révision NE change PAS

- Aucune modification de `ict_strategy.evaluate_entry` (H2) —
  comportement vérifié strictement inchangé par régression.
- Aucune modification du mécanisme de sortie §2.10.
- Aucun nouveau paramètre au sens de l'invariant #10 (voir ci-dessus).
- Ne sera jamais ajustée automatiquement sur la base de ses propres
  résultats — toute évolution future = nouvelle entrée datée.

### Plafond §3.9 et correction pour comparaisons multiples

Inchangé : correction calibrée sur 5 hypothèses simultanées (H1-H5),
jamais 4.

Implémentation, tests (100% sur `ict_strategy.py`/
`hypothesis5_strategy.py`), déploiement et vérification en direct
détaillés dans `docs/DECISIONS.md` (24/08/2026).

---

## 2026-08-24 (soir) — Backtest rétrospectif (§2.11) : PRÉ-ENREGISTREMENT, écrit avant toute donnée backtest générée

Décidé par Ismaël après proposition présentée et discutée le même jour
(voir `docs/DECISIONS.md` pour l'historique complet de la discussion,
les deux vérifications empiriques préalables et les options écartées).
Écrit et daté **avant de lancer le téléchargement d'historique ou tout
calcul de backtest** — aucune donnée backtest n'existe encore au moment
où cette entrée est écrite, conformément à la règle de ce fichier.

### Objet

Rejouer, sur historique Capital.com, la mécanique EXACTE (régime/
déclencheur/sortie) de chacune des 5 hypothèses telles qu'actuellement
déployées — **aucune modification de leur logique** :
- H1 : `trend_strategy.evaluate_entry` (MA200 + Donchian(20), HOUR)
- H2 : `ict_strategy.evaluate_entry` (structurel + confluence ICT, M15)
- H3 : `hypothesis3_strategy` (= H1 + TP1/TP2, régime croisé requis, M15)
- H4 : `mean_reversion_strategy.evaluate_entry` (MA200+Bollinger, régime
  croisé requis, M15)
- H5 : `hypothesis5_strategy.evaluate_entry` V3 (structurel + RSI, M15)

Objectif : accumuler des données statistiques supplémentaires par
(actif, hypothèse) pour alimenter `confidence_scorer.py`, avec un
mécanisme d'influence sur le live limité et à sens unique (voir
"Mécanisme d'influence" ci-dessous) — jamais un remplacement de
l'apprentissage sur trades réels/démo, un complément.

### Vérifications empiriques préalables (résultats factuels, 24/08/2026)

Effectuées avant toute décision de conception, sur le compte démo
principal, lecture seule, aucun ordre :
- **Profondeur d'historique disponible** : recherche dichotomique sur
  `/prices/EURUSD` (paramètres `from`/`to`) entre 365 jours (disponible)
  et 1825 jours/5 ans (indisponible, `error.prices.not-found`).
  Limite trouvée entre **718 et 730 jours en arrière** (~2 ans tout
  juste, précision ~12 jours, jugée suffisante) — le compte **démo**
  Capital.com ne conserve donc qu'environ **2 ans** d'historique, pas
  "des années" au sens large envisagé par le §2.11 du CDC (qui prévoyait
  d'ailleurs des bougies OANDA, broker devenu inutilisable pour ce
  projet, voir CLAUDE.md).
- **Plafond de `max` par requête** : confirmé à **1000 bougies**
  (`max=1500` -> `error.invalid.max`, HTTP 400).
- **`from`/`to` fonctionnent** sur `/prices/{epic}` — non exposés par
  `capital_client.get_prices` aujourd'hui (ajout nécessaire, voir
  `docs/DECISIONS.md`), mais acceptés par l'API brute.
- **Bid/ask disponibles séparément** à chaque point OHLC de l'historique
  (`openPrice.bid`/`openPrice.ask`, etc.) — pas seulement un prix médian
  comme le retourne `market_data.get_candles` aujourd'hui. Permet un
  spread réellement observé pour le §2.6, pas une approximation
  forfaitaire.
- **Portée du rate-limit (429)** : rafale de 16 requêtes rapprochées sur
  le compte principal -> 429. Requête immédiate suivante sur le compte
  "hypothèse 2" (clé API distincte, même identifiant de connexion
  probable) -> 429 également. **La limite n'est pas isolée par clé
  API** — un téléchargement en masse partage le même budget que les 6
  process live, quelle que soit la clé utilisée.

### Résolution historique par hypothèse (corrige une hypothèse initiale erronée)

Vérifié dans le code réel avant de concevoir quoi que ce soit (voir
`docs/DECISIONS.md`, session du 24/08/2026, aprem) : **aucune hypothèse
n'utilise aujourd'hui deux résolutions de bougies différentes** — la
couche "session/multi-timeframe" du 23/08/2026 ne fait cohabiter que
deux CADENCES de rafraîchissement sur une résolution UNIQUE (le contexte
de régime croisé H3/H4 est recalculé sur les mêmes bougies M15 que le
déclencheur, juste avec un cache rafraîchi seulement 3x/jour au lieu de
chaque cycle). En conséquence, le backtest a besoin, par hypothèse :

| Hypothèse | Historique nécessaire |
|---|---|
| H1 | HOUR de l'actif uniquement |
| H2, H5 | M15 de l'actif uniquement |
| H3, H4 | M15 de l'actif **+** M15 de US30 **+** M15 de US100 (mêmes bougies M15, deux instruments de plus, jamais une résolution différente) |

`CANDLE_COUNT` (=220, `technical_strategy_executor.py`) réutilisée telle
quelle comme fenêtre glissante fournie à `entry_fn` — **exactement** ce
que le live fournit à chaque cycle (`get_candles(..., count=CANDLE_COUNT)`),
jamais plus, jamais moins : fidélité au comportement live, pas seulement
absence d'anticipation.

### Téléchargement en masse (`scripts/download_historical_data.py`)

- Script séparé, ponctuel, manuel — **jamais appelé depuis les 6
  boucles live**. Persiste sur disque (`data/historical/`, format JSON
  par `(epic, résolution)`) — un calcul de backtest ultérieur ne refait
  aucun appel réseau.
- Pagination `from`/`to` par fenêtres de ≤1000 bougies (plafond dur
  mesuré ci-dessus), en remontant depuis aujourd'hui jusqu'à la limite
  de profondeur mesurée (~2 ans, ou jusqu'au premier `error.prices.
  not-found` rencontré, ce qui vient en premier — pas de valeur figée en
  dur, le script s'arrête sur le signal réel du broker).
- Réutilise `src/retry.py` (`retry_with_backoff`) par page. **Throttle
  explicite en plus du retry** : 1 requête toutes les 7-10s (rafale à
  429 mesurée à 16 requêtes rapprochées — reste très large en dessous),
  le retry absorbe l'échec ponctuel, le throttle évite de le provoquer.
  Aucune isolation par clé API dédiée (le rate-limit est partagé, voir
  vérification ci-dessus) — le compte principal suffit, une clé
  "dédiée" n'aurait rien isolé.
- Conserve bid/ask (pas seulement le prix médian) pour permettre le
  spread réellement observé au §2.6.
- **Vérification en direct obligatoire après exécution** : comparer le
  taux de 429/cycles sautés des 6 process live sur la fenêtre du
  téléchargement à une fenêtre de référence sans téléchargement — pas
  supposé, mesuré (voir `docs/DECISIONS.md` pour le résultat).

### Prévention du biais d'anticipation

- Fenêtre glissante stricte : `entry_fn` n'est jamais appelée qu'avec
  les `CANDLE_COUNT` dernières bougies dont la clôture est ≤ l'instant
  simulé T — jamais une bougie postérieure, y compris pour les indices
  de confirmation US30/US100 (H3/H4), toujours évalués au même instant
  T que l'actif, jamais une bougie plus récente qu'eux.
- Cadence de rafraîchissement du régime croisé (H3/H4) répliquée à
  l'identique via `technical_strategy_executor._should_refresh_regime_
  context` (réutilisée telle quelle, pure, déjà testée) — le cache n'est
  recalculé qu'aux mêmes 3 heures UTC fixes (0h/8h/13h) que le live,
  jamais à chaque bougie.
- **Prix d'exécution simulé = ouverture de la bougie SUIVANT celle qui
  déclenche le signal** (pas la clôture de la bougie déclenchante) —
  approximation standard, cohérente avec le délai structurel réel de
  10-60s du §2.8 ("ce qui rend le scalping impossible"). Si le signal se
  déclenche sur la dernière bougie disponible de l'historique (aucune
  bougie suivante), aucun trade simulé n'est généré — jamais une
  approximation sur une bougie qui n'existe pas.
- `decide_entry` (validator + risk_engine, **inchangée**, réutilisée
  telle quelle) reste l'unique porte d'entrée — la tolérance de
  péremption du §2.8 (déjà codée dans `validator.py`) s'applique donc
  naturellement à l'écart entre le prix du signal et le prix d'ouverture
  de la bougie suivante, sans nouvelle logique de péremption spécifique
  au backtest.

### Convention de résolution intra-bougie (ambiguïté stop/cible dans la même bougie)

Une bougie OHLC ne dit pas dans quel ordre le prix a touché un niveau
donné pendant l'intervalle. Convention retenue, **pessimiste par
construction** (cohérent avec le principe du §2.6, "métriques
délibérément pessimistes") : pour chaque bougie de gestion d'une
position ouverte, le stop est testé EN PREMIER (avec le point le plus
défavorable de la bougie — bas pour un long, haut pour un short) ; s'il
n'est pas touché, la cible/le trailing sont testés ensuite (avec le
point le plus favorable — haut pour un long, bas pour un short). Si les
deux étaient plausibles dans la même bougie, le stop l'emporte toujours
dans cette simulation — jamais l'inverse.

### Coûts réalistes (§2.6)

- **Spread** : bid/ask réellement observés dans l'historique (pas de
  spread forfaitaire, disponible et plus fidèle — voir vérification
  empirique ci-dessus). Entrée long = payé à l'ask ; sortie long = reçu
  au bid (symétrique en short) — au moment (bougie) exact de l'exécution
  simulée, jamais une moyenne ou une valeur d'un autre instant.
- **Slippage forfaitaire pénalisant** : fixé a priori à **100% du
  spread observé au même instant** (jamais calibré sur un résultat de
  backtest) — double le coût de franchissement réel par rapport au
  spread seul, choix délibérément pessimiste et simple à justifier
  (pas de table de constantes arbitraires par actif). Appliqué dans le
  même sens défavorable que le spread, à l'entrée ET à la sortie.
- **Financement overnight** : fixé a priori à **1 point de base
  (0,01%) du prix d'entrée par jour civil complet** au-delà du jour
  d'ouverture, TOUJOURS un coût (jamais un crédit, même quand le
  financement réel pourrait favoriser une direction — encore plus
  pessimiste que la réalité, volontaire). Appliqué comme un décalage de
  prix défavorable supplémentaire au moment de la sortie.
- Ces trois constantes (multiplicateur de slippage, taux de financement)
  sont des choix d'ingénieur a priori, pas des mesures — documentées
  comme telles, jamais ajustées après avoir vu un résultat de backtest
  (invariant #10).

### Séparation stricte (mode + source + seuils d'éligibilité)

- **Source dédiée par hypothèse**, jamais la source live réutilisée :
  `hypothesis_backtest` (H1), `hypothesis2_backtest`, `hypothesis3_backtest`,
  `hypothesis4_backtest`, `hypothesis5_backtest`. Ajoutées aux 4 copies
  de `_normalize_source` (`metrics.py`, `circuit_breaker_store.py`,
  `executor.py`, `confidence_scorer.py`), vérifié par
  `tests/test_source_normalization_consistency.py` (existant, étendu).
- **Enveloppe séparée** par `(actif, source_backtest)`, via
  `envelope_store.load_or_create_envelope` réutilisée telle quelle,
  démarrée au même `envelope_initial` que le live.
- **Réserve globale JAMAIS touchée par le backtest** : `capital_manager.
  apply_trade_result` est réutilisée pour la règle des 50% (fidélité de
  l'évolution de l'enveloppe backtest elle-même), mais la part "réserve"
  qu'elle retourne est accumulée dans un total **simulé, local au run de
  backtest, jamais écrit dans `reserve_ledger`** — `reserve_ledger` est
  globale et partagée entre tous les actifs (§2.3), la polluer avec un
  montant simulé serait irréversible et fausserait la vraie réserve.
  Écart assumé, documenté ici explicitement.
- `market_snapshots` (bid/ask/spread) rempli pour chaque signal backtest
  généré — ferme, pour les seules sources backtest, le gap documenté
  dans `confidence_scorer.py` (spread médian toujours indéterminé faute
  de données) ; **le live n'est pas touché**, le gap y reste ouvert.
- **Seuils d'éligibilité backtest distincts et plus élevés** que le
  live : `PHASE_A_MIN_TRADES_BACKTEST = 60` (vs 20 en live),
  `PHASE_B_MIN_TRADES_BACKTEST = 150` (vs 50 en live) — facteur ~3,
  choisi a priori (un fill simulé n'a pas la fiabilité d'un fill réel :
  ni slippage réellement mesuré au tick, ni confirmation broker, ni
  cohérence de session garantie au-delà de ce que l'historique
  fournit). Ajoutés à `confidence_scorer.py` comme paramètres optionnels
  de `evaluate_confidence`/`compute_confidence_score`/`check_min_trades`
  (défaut = constantes live existantes, comportement live inchangé par
  construction quand ils ne sont pas fournis explicitement).

### Mécanisme d'influence sur le live — Option B, retenue par Ismaël

Nouveau garde-fou dans `executor.open_signal`, **avant** `decide_entry`,
à la même position que le blocage coupe-circuit déjà présent (avant
tout calcul de sizing) :
- Pour un signal sur une des 5 sources hypothèse (`hypothesis`,
  `hypothesis2`, `hypothesis3`, `hypothesis4`, `hypothesis5` — jamais
  `stationx`, hors périmètre de cette demande), on calcule le score
  backtest de confiance du couple `(actif, source_backtest correspondante)`
  via `confidence_scorer.compute_confidence_score` avec les seuils
  BACKTEST (plus élevés, voir ci-dessus).
- **Couple sans assez de données backtest** (`eligible=False`, quelle
  qu'en soit la raison — trop peu de trades, spread indisponible,
  taille incompatible) : le garde-fou ne fait **rien**, aucun blocage,
  comportement actuel strictement inchangé. C'est le cas de TOUS les
  couples tant qu'aucun backtest n'a encore été exécuté — le
  déploiement de ce mécanisme n'a donc, par construction, **aucun
  impact tant que `scripts/run_retrospective_backtest.py` n'a pas
  tourné**.
- **Couple éligible ET espérance nette backtest ≤ 0** : le signal live
  est rejeté, journalisé dans `risk_decisions` (raison dédiée,
  `backtest_confidence_gate`), **avant** tout calcul de sizing —
  `decide_entry` n'est jamais appelé. Aucune augmentation de risque
  possible par ce mécanisme (il ne fait que refuser, jamais moduler à
  la hausse) ; `risk_engine.py` n'est pas modifié.
- **Couple éligible ET espérance nette backtest > 0** : aucun effet,
  le signal continue son chemin normal vers `decide_entry`.
- Seuil retenu : **espérance nette ≤ 0 R**, pas un seuil positif plus
  exigeant — un backtest qui confirme une espérance négative est un
  signal fort (même mécanique, échantillon plus large) ; un seuil plus
  strict resterait à documenter séparément s'il devait être introduit
  plus tard (pas fait ici, hors périmètre de cette demande).

### Ce que ce mécanisme NE fait PAS

- Ne module jamais le sizing à la hausse (`boosted`/Option A explicitement
  écartée par Ismaël pour cette phase).
- Ne modifie jamais `risk_engine.py`.
- N'affecte jamais Station X.
- Ne s'applique qu'aux signaux qui atteignent déjà `open_signal` — ne
  change rien au déclencheur des stratégies elles-mêmes.
- Ne mélange jamais les statistiques backtest et live dans le même
  calcul de `confidence_scorer` (sources strictement séparées).

### Budget de variables (invariant #10)

Deux nouvelles constantes a priori pour le backtest lui-même
(multiplicateur de slippage = 1.0, taux de financement = 1bp/jour) et
deux seuils d'éligibilité backtest (60/150 trades) — aucun de ces
chiffres n'est un paramètre de DÉCLENCHEMENT d'une hypothèse (le budget
§2.11 de chaque hypothèse reste inchangé, ces constantes vivent dans
l'infrastructure de backtest, pas dans `trend_strategy.py`/
`ict_strategy.py`/etc.), documentées ici comme choix d'ingénieur a
priori, jamais ajustées sur un résultat.

Implémentation, tests, déploiement et vérification en direct (y compris
l'impact du téléchargement sur le taux de succès live) rapportés dans
`docs/DECISIONS.md` une fois le code construit.

---

## 2026-08-25 — PRÉ-ENREGISTREMENT : évolution de H2/H3/H4/H5 par entraînement/validation temporelle sur le backtest existant

Écrit et daté **avant tout calcul sur les données** (candidats et
critère de succès figés ici, avant de regarder un seul résultat
d'entraînement) — conformément au §3.8 ("aucune variable supplémentaire
sans justification théorique écrite préalable", "tout ajustement est
proposé... jamais silencieux"). Décision d'Ismaël, méthode validée
avant construction.

**H1 hors périmètre, intacte** — aucune ligne de `trend_strategy.py`
n'est modifiée par ce chantier, vérifié explicitement ci-dessous pour
chaque hypothèse touchée.

### Découpage temporel (mesuré sur l'historique déjà téléchargé, aucun nouveau téléchargement)

Bougies M15 (H2-H5) disponibles du 2024-06-14T11:00 au 2026-08-24T17:30
UTC (801 jours). Coupure à 2/3 : **2025-11-30T15:20 UTC**, arrondie à
**2025-12-01T00:00 UTC** pour lisibilité.

- **ENTRAÎNEMENT** : 2024-06-14 → 2025-12-01 (~534 jours, ~2/3)
- **VALIDATION** : 2025-12-01 → 2026-08-24 (~267 jours, ~1/3), **jamais
  consultée avant la sélection du candidat**

Coûts : modèle le plus pessimiste déjà construit (`SLIPPAGE_SPREAD_
MULTIPLIER = 1.0`, 100% du spread en plus du spread réel), cohérent
avec §2.6.

### Ce qui reste inchangé (aucune ligne modifiée dans ce chantier)

- Couche session/multi-timeframe (cadence de rafraîchissement du
  régime croisé H3/H4, `technical_strategy_executor.py`/
  `regime_confirmation.py`).
- Mécanisme de sortie §2.10 (TP1 50%/TP2 30%/TP3 20% trailing, ou TP
  fixe pour H4) — `executor._evaluate_position_management`.
- Garde-fou Option B (`executor._check_backtest_confidence_gate`).
- `trend_strategy.py` (MA_PERIOD, DONCHIAN_PERIOD, `evaluate_entry`,
  `compute_regime`) — **partagé avec H1** (régime de H4 via
  `compute_regime`, déclencheur de H3 via `evaluate_entry`) : exclu de
  toute modification, y compris "pour H3" ou "pour H4" — un changement
  ici toucherait H1 par ricochet.
- `ict_strategy.FRACTAL_K`/confluence — **partagé entre H2 et H5** (H5
  réutilise `compute_structural_entry`) : exclu de toute modification
  pour la même raison (un changement "pour H2" toucherait H5 par
  ricochet, et inversement).

### Portée exacte des paramètres explorés (aucun nouveau, seulement re-réglés)

Chaque paramètre ci-dessous existe DÉJÀ dans le budget §2.11 de son
hypothèse (voir `docs/HYPOTHESES.md`, entrées antérieures) — cette
exploration ne fait varier que sa VALEUR, jamais son existence. Aucun
nouveau degré de liberté introduit.

| Hypothèse | Paramètre(s) explorés | Fichier | Statut budget |
|---|---|---|---|
| H2 | `TP1_R_MULTIPLE`/`TP2_R_MULTIPLE` | `hypothesis2_strategy.py` | valeur re-réglée, budget inchangé |
| H3 | `TP1_R_MULTIPLE`/`TP2_R_MULTIPLE` | `hypothesis3_strategy.py` | valeur re-réglée, budget inchangé |
| H4 | `BOLLINGER_STD_MULTIPLIER`, `STOP_WIDTH_MULTIPLIER` | `mean_reversion_strategy.py` | valeur re-réglée, budget inchangé |
| H5 | `RSI_PERIOD`, `TP1_R_MULTIPLE`/`TP2_R_MULTIPLE` | `hypothesis5_strategy.py` | valeur re-réglée, budget inchangé |

### Décision explicite : PAS de paramétrage par-actif dans ce chantier

Un paramètre différent par actif multiplierait les degrés de liberté
par 8 (point de vigilance d'Ismaël) — écarté pour cette évolution : un
seul jeu de valeurs par hypothèse, partagé sur les 8 actifs, exactement
comme aujourd'hui. La décision train/validation se fait donc sur les
trades **regroupés (poolés) des 8 actifs**, pas actif par actif — cohérent
avec le fait que le paramètre lui-même est partagé. Le détail par actif
est rapporté pour information, jamais pour trancher.

### Candidats par hypothèse — choisis sur justification théorique, AVANT tout calcul

**H2** (actuel : TP1=1.0R, TP2=2.0R) :
- A (référence) : TP1=1.0R, TP2=2.0R
- B : TP1=0.5R, TP2=1.5R — prise de profit plus rapide, cohérente avec
  un déclencheur déjà très sélectif (confluence ICT complète) où
  chaque signal mérite d'être sécurisé tôt plutôt que d'espérer un
  mouvement prolongé sur un échantillon rare.

**H3** (actuel : TP1=1.0R, TP2=2.0R, hérite du déclencheur H1 Donchian(20)+MA200) :
- A (référence) : TP1=1.0R, TP2=2.0R
- B : TP1=0.5R, TP2=1.5R — même rationale que H2-B, déclencheur non
  sélectif (simple cassure de canal) où sécuriser tôt limite l'érosion
  par retournement.
- C : TP1=1.5R, TP2=3.0R — hypothèse inverse : si la cassure capture un
  vrai prolongement de tendance, des cibles plus larges laissent courir
  le mouvement avant que le tiers restant ne passe sous trailing.

**H4** (actuel : Bollinger(20, 2.0σ), stop_width=1.0×) :
- A (référence) : 2.0σ, stop_width=1.0
- B : 2.5σ — bande plus large, ne retient que des écarts statistiquement
  plus extrêmes (moins de signaux, sélection plus stricte du retour à
  la moyenne).
- C : stop_width=1.5× — stop élargi, motivé par le reproche classique
  fait aux systèmes de retour à la moyenne naïfs : un stop trop serré
  sort la position juste avant que le retour attendu ne se produise.

**H5** (actuel : RSI(14)/50, TP1=1.0R, TP2=2.0R, hérite du régime structurel H2) :
- A (référence) : RSI(14), TP1=1.0R, TP2=2.0R
- B : RSI(9) — lecture de momentum plus réactive, capte le
  franchissement du seuil 50 plus tôt dans le mouvement.
- C : TP1=0.5R, TP2=1.5R — même rationale que H2-B/H3-B.

### Sélection du candidat (entraînement SEUL, avant tout contact avec la validation)

Pour chaque hypothèse : rejeu de CHAQUE candidat sur la période
d'ENTRAÎNEMENT uniquement, sur les 8 actifs, trades poolés. Le
candidat retenu est celui avec l'espérance nette la plus élevée **et**
strictement positive, avec au moins `PHASE_B_MIN_TRADES_BACKTEST` (150,
déjà défini dans `confidence_scorer.py`) trades poolés sur
l'entraînement. Si aucun candidat ne satisfait ces deux conditions :
**aucun candidat n'est retenu pour cette hypothèse, la validation n'est
jamais consultée** (inutile de tester sur la validation un candidat
déjà disqualifié sur l'entraînement — évite de gaspiller la seule
tentative de validation autorisée).

Nombre de candidats considérés sur l'entraînement, écrit ici avant
résultat : **2 pour H2, 3 pour H3, 3 pour H4, 3 pour H5** — le candidat
retenu (le cas échéant) et le nombre exact considéré seront rapportés
dans `docs/DECISIONS.md`, y compris si c'est la référence (A) qui gagne
(un résultat honnête, pas un échec de la démarche).

### Critère de succès en validation (figé ici, jamais ajusté après coup)

Réutilise `PHASE_A_MIN_TRADES_BACKTEST` (60, déjà défini dans
`confidence_scorer.py`) plutôt qu'un nouveau seuil inventé : le
candidat retenu sur l'entraînement **VALIDE** si et seulement si, sur
la période de VALIDATION seule (jamais entraînement), poolé sur les 8
actifs :
- nombre de trades ≥ 60, **et**
- espérance nette strictement positive.

**Un seul essai de validation par candidat retenu — jamais
d'itération.** Si le critère échoue, l'hypothèse reste en pause (garde-
fou Option B déjà actif, base réelle déjà à jour) — pas de nouveau
candidat testé contre cette même validation, pas de nouvelle tentative
dans ce chantier. Un échec est un résultat, journalisé comme tel dans
`docs/DECISIONS.md`, pas juste les succès.

### Ce que cette évolution NE fait PAS

- Ne modifie jamais `trend_strategy.py`/`ict_strategy.FRACTAL_K` — H1
  intacte, H2/H5 non entrelacées entre elles par ce chantier.
- Ne modifie jamais la couche session/multi-timeframe, le mécanisme de
  sortie §2.10, ni le garde-fou Option B.
- N'introduit aucun paramétrage par-actif (décision explicite ci-dessus).
- N'augmente jamais le budget de variables d'une hypothèse — re-règle
  des valeurs déjà comptées, n'en ajoute aucune.

Implémentation (script d'évaluation entraînement/validation, jamais
committé comme partie du pipeline de production — outil de recherche
ponctuel comme `calibrate_pip_value.py`), résultats complets (tous les
candidats, pas seulement le gagnant), tests, déploiement des candidats
validés (le cas échéant) et rapport des échecs dans `docs/DECISIONS.md`.

---

## 2026-08-25 (suite) — PRÉ-ENREGISTREMENT : cycle 2 de l'évolution H3/H4/H5 — axe timeframe, correction statistique, cycle autonome avec application automatique

Écrit et daté **avant tout calcul sur les données**, suite à la demande
explicite d'Ismaël de « débloquer » le cycle autonome et faire varier le
timeframe (et les autres paramètres déjà explorés) « sur la base de
l'historique et de la compréhension du marché ». Trois clarifications
obtenues avant d'écrire ce pré-enregistrement :

1. **H1 reste hors périmètre** — inchangé par rapport au cycle 1.
2. **Application automatique dès validation** — un candidat qui valide
   est appliqué en direct SANS attendre une confirmation d'Ismaël à
   chaque cycle. **Écart explicite et assumé au §3.9 du CDC**, qui dit
   littéralement « Validation (toi) [...] Jamais appliquée
   automatiquement » — remplacé ici par une validation automatique
   déterministe (seuils fixés à l'avance, jamais un jugement LLM),
   couvert par l'autonomie déléguée du 16/08/2026 (préambule de
   `docs/DECISIONS.md`). Journalisé ici comme écart, pas silencieux.
3. **Le timeframe varie sur toute la chaîne**, y compris la couche de
   confirmation croisée US30/US100 (H3/H4) — réouvre le gel décidé au
   cycle 1. La couche session (fenêtres horaires, exemption crypto)
   n'est PAS concernée : aucune hypothèse théorique n'a été formulée sur
   elle, seule la RÉSOLUTION des bougies (entrée + confirmation) varie.

### Deuxième écart CDC, trouvé en écrivant ce pré-enregistrement (pas cherché) : §3.9 est un mécanisme PROSPECTIF, pas rétrospectif

Le §3.9 du CDC prescrit un test « uniquement sur données POSTÉRIEURES à
la génération » (prospectif, trimestriel, ≥10 trades réels). Tout ce
chantier — cycle 1 compris — utilise au contraire le backtest
RÉTROSPECTIF du §2.11 (historique 2024-2026, découpage entraînement/
validation temporel, mais les deux bornes sont dans le passé). C'est le
mécanisme §2.11, pas littéralement le §3.9, qui alimente cette
évolution — le nom « cycle autonome (§3.9) » utilisé dans la conversation
pour désigner ce chantier est une étiquette commode, pas une conformité
littérale au §3.9. Assumé explicitement : le volume de trades prospectifs
réel (quelques dizaines par mois au mieux) rendrait un test purement
prospectif inexploitable à l'échelle de temps souhaitée par Ismaël ; le
rétrospectif reste le seul mécanisme disponible avec un volume
suffisant. Les deux garanties que le §3.9 visait à préserver sont
conservées sous une autre forme : **correction statistique proportionnelle
au nombre d'hypothèses/candidats testés** (voir ci-dessous, exigée
explicitement par le §3.9 ET l'invariant #10) et **le plafond de 3
hypothèses par cycle** (voir ci-dessous).

### Plafond CDC « 3 hypothèses par cycle » — appliqué littéralement

4 hypothèses seraient explorables (H2/H3/H4/H5). **H2 est reportée au
prochain cycle trimestriel**, pas incluse dans celui-ci : volume déjà
extrêmement faible au cycle 1 (n=9 trades poolés sur 1.5 an
d'entraînement, les deux candidats) — passer en HOUR réduirait encore ce
volume (moins de bougies, un déclencheur déjà rare), sans base
statistique exploitable même avec correction. Ce cycle couvre **H3, H4,
H5 — exactement 3, plafond CDC respecté à la lettre.**

### Découpage temporel (identique au cycle 1, aucune donnée nouvelle)

Même CUTOFF que le cycle 1 : **2025-12-01T00:00 UTC** — ENTRAÎNEMENT
avant, VALIDATION après, jamais consultée avant sélection. Bougies HOUR
déjà téléchargées (2024-05-14 → 2026-08-24, 8 actifs) — aucun nouveau
téléchargement nécessaire, aucun appel réseau dans ce chantier.

### Ce qui reste inchangé (aucune ligne modifiée par ce chantier)

- Mécanisme de sortie §2.10, garde-fou Option B, `trend_strategy.py`
  (H1), `ict_strategy.FRACTAL_K` (partagé H2/H5, H2 hors périmètre ce
  cycle de toute façon) — mêmes exclusions que le cycle 1.
- Couche session (fenêtres horaires, exemption crypto) —
  `technical_strategy_executor.py`/`session_windows.py` non touchés :
  seule la résolution des bougies varie, pas la logique de fenêtrage.
- Risk caps, `risk_engine.py`, `capital_manager.py`, `go_nogo.py` —
  **jamais modifiables par ce cycle, sous aucune condition** (invariant
  #6, rappelé explicitement car ce chantier introduit une application
  automatique pour la première fois).

### Candidats — axe timeframe, choisis sur justification théorique AVANT tout calcul

**H3** (Donchian breakout + confirmation croisée US30/US100, actuel M15/M15) :
- A (référence) : entrée M15, confirmation M15
- B : entrée HOUR, confirmation HOUR — hypothèse : une cassure de canal
  lue en H1 filtre le bruit intrajournalier qui produit les faux
  signaux déjà documentés en M15.
- C : entrée M15, confirmation HOUR — hypothèse mixte : garder la
  réactivité d'entrée en M15 tout en lisant le régime de fond sur une
  échelle plus lente et moins bruitée. **Rendu possible par le
  correctif d'alignement du 25/08/2026** (pointeur par horodatage,
  supporte nativement des résolutions différentes entre bougies propres
  et bougies de confirmation — jamais testé avant ce correctif).

**H4** (retour à la moyenne Bollinger + confirmation croisée, actuel M15/M15) :
- A (référence) : entrée M15, confirmation M15
- B : entrée HOUR, confirmation HOUR — hypothèse : le retour à la
  moyenne est classiquement plus fiable sur une échelle plus lente
  (moins de faux signaux de bande dépassée par du bruit pur).
- C : entrée M15, confirmation HOUR — même rationale mixte que H3-C.

**H5** (régime structurel + RSI(14)/50, actuel M15, pas de confirmation croisée) :
- A (référence) : M15
- B : HOUR — hypothèse : un croisement RSI/50 lu en H1 capte un
  changement de momentum réel plutôt qu'une oscillation intrajournalière.

**8 candidats au total ce cycle** (3+3+2), tous sur les paramètres déjà
au budget §2.11 existant de leur hypothèse (résolution des bougies —
degré de liberté déjà implicite dans le choix initial de chaque
hypothèse, jamais un nouveau paramètre ajouté au budget).

### Sélection sur entraînement — correction statistique pour comparaisons multiples (exigée par §3.9 ET invariant #10)

Le cycle 1 utilisait un seuil simple (espérance ponctuelle > 0). Avec
plus de candidats testés par hypothèse ce cycle, un seuil ponctuel seul
gonfle le risque de faux positif (« tester plusieurs idées en fait
gagner une par hasard », §3.9 littéral). Correction de Bonferroni à
l'intérieur de chaque famille d'hypothèse (m = nombre de candidats de
cette hypothèse ce cycle) :

- Pour chaque candidat, calcul de la moyenne et de l'écart-type des
  R-multiples poolés sur les 8 actifs (entraînement), erreur-type
  SE = écart-type / racine(n).
- Seuil corrigé unilatéral : z = quantile normal(1 - 0.05/m) — H3/H4
  (m=3) : z≈2.128 ; H5 (m=2) : z≈1.960.
- **Un candidat qualifie si et seulement si** : n ≥
  `PHASE_B_MIN_TRADES_BACKTEST` (150, seuil déjà existant, réutilisé) ET
  (moyenne − z×SE) > 0 — la borne basse de l'intervalle corrigé doit être
  strictement positive, pas seulement la moyenne ponctuelle. **Critère
  strictement plus exigeant que le cycle 1** — assumé : plus de candidats
  testés, barre plus haute, exactement l'esprit du §3.9.
- Candidat retenu par hypothèse : le qualifiant avec l'espérance
  ponctuelle la plus élevée. Si aucun ne qualifie : aucun candidat
  retenu, validation jamais consultée — identique au cycle 1.

### Critère de succès en validation (inchangé par rapport au cycle 1)

`PHASE_A_MIN_TRADES_BACKTEST` (60) trades poolés minimum, espérance
nette strictement positive, un seul essai, jamais itéré. Pas de
correction supplémentaire ici : un seul candidat par hypothèse atteint
cette étape (déjà le candidat unique retenu sur l'entraînement).

### Mécanisme d'application automatique — conçu maintenant, exercé seulement si un candidat valide

Deux natures de changement, deux mécanismes :

- **Paramètres déjà dynamiques par attribut de module** (TP1/TP2,
  RSI_PERIOD, écart-type Bollinger, multiple de stop) : nouvelle table
  `hypothesis_parameter_overrides` (audit complet : candidat, espérance
  entraînement/validation, n, horodatage). Nouveau module
  `src/hypothesis_params.py` : `apply_overrides(module, hypothesis,
  db_path)`, appelé une fois au DÉMARRAGE de chaque `hypothesisN_
  executor.py` (pas en cours de run — un changement de paramètre ne
  prend effet qu'après un redémarrage explicite, cohérent avec le
  principe "code-locked" du §4.2/invariant #4). Défaillant/absent =
  aucun effet, valeurs codées en dur du module inchangées (fail-safe,
  invariant #7).
- **Résolution des bougies** (littérale dans chaque `hypothesisN_
  executor.py`) : le cycle édite ce littéral, fait tourner la suite de
  tests complète, commit + push + déploiement + redémarrage du process
  concerné UNIQUEMENT si les tests passent au vert. Toute étape en échec
  interrompt le cycle avant tout changement effectif côté VPS (fail-safe),
  notifie Telegram, journalise l'échec.
- **Jamais** : `risk_engine.py`, `capital_manager.py`, `go_nogo.py`, les
  plafonds de risque, la couche session, le garde-fou Option B.
- **Notification Telegram systématique** à chaque cycle (candidats
  testés, résultat par hypothèse, application ou non) — même patron que
  les notifications déjà en place.
- **Cadence trimestrielle** : ce cycle (cycle 2) tourne maintenant, à la
  demande explicite d'Ismaël. Les cycles suivants tournent automatiquement
  tous les ~90 jours (crontab VPS), jamais plus rapproché — plafond CDC
  respecté à la lettre malgré l'application automatique.

Implémentation, résultats complets (tous les candidats), tests,
déploiement de ce qui valide (le cas échéant), rapport honnête des échecs
dans `docs/DECISIONS.md` — même discipline que le cycle 1.

**Résultat (25/08/2026, `logs/evaluate_timeframe_cycle.log`) : les 8
candidats ont une espérance ponctuelle NÉGATIVE sur l'entraînement — la
correction Bonferroni n'a même pas eu besoin de rejeter un candidat
positif-mais-fragile, aucun n'était positif pour commencer. Aucun
candidat qualifié, validation jamais consultée, pour H3, H4 et H5.
Détail chiffré complet dans `docs/DECISIONS.md`.**

**Correction sur la cadence trimestrielle automatique** : la section
ci-dessus envisageait un crontab VPS relançant ce mécanisme tous les
~90 jours. En l'écrivant plus haut, avant de le construire, réalisation
qu'un cron ne peut mécaniser QUE l'étape TEST (déterministe) et
l'application automatique — jamais l'étape GÉNÉRATION du §3.9
("Examine trades + contexte... formule une hypothèse AVEC justification
causale explicite"), qui exige un raisonnement neuf à chaque cycle, pas
une grille de candidats figée rejouée indéfiniment sur les mêmes
données déjà rejetées. **Pas de crontab construit.** Le mécanisme
d'application automatique (`hypothesis_params.py`), lui, est construit
et opérationnel dès maintenant, prêt pour ce cycle-ci comme pour tout
cycle futur.

**Correction de cadence — 25/08/2026, instruction explicite d'Ismaël :
10 jours, pas trimestriel.** Remplace l'échéance ~2026-11-25 ci-dessus
par **2026-09-04 (10 jours après le cycle 2)**. **Troisième écart CDC,
explicitement assumé** : le §3.9 dit littéralement « Cadence
trimestrielle (et non mensuelle) : à faible volume de trades, une
hypothèse ne peut pas se trancher en 30 jours » — 10 jours est encore
plus rapproché que le mensuel que le CDC écarte explicitement pour cette
raison. Nuance qui rend cet écart défendable plutôt qu'aveugle : cet
argument du CDC vise un test PROSPECTIF (accumuler assez de nouveaux
trades réels pour trancher), or ce mécanisme est RÉTROSPECTIF (§2.11,
déjà journalisé comme deuxième écart ci-dessus) — chaque cycle rejoue
l'historique déjà existant, il n'attend pas l'accumulation de nouveaux
trades prospectifs pour avoir un échantillon suffisant. La contrainte de
volume du §3.9 ne s'applique donc pas de la même façon ici. **Ce qui
reste pleinement contraignant, à 10 jours comme à 90** : l'étape
GÉNÉRATION exige un raisonnement neuf, jamais une grille rejouée
mécaniquement — voir la règle explicite ci-dessous.

### Ce qu'un cycle à 10 jours doit faire quand il n'y a rien de neuf à proposer

Un cycle qui n'a aucune nouvelle justification théorique à écrire **doit
conclure "rien à tester ce cycle-ci"**, journalisé comme tel dans
`docs/DECISIONS.md` (même statut qu'un résultat négatif — une absence de
candidat n'est pas un échec de la démarche, c'est le résultat honnête
quand rien de nouveau n'a changé). Ce qui compte comme "neuf",
concrètement — au moins UNE des conditions suivantes doit être vraie
pour qu'un cycle propose des candidats :
- Un volume de trades RÉELS (pas backtest) significativement plus
  important qu'au cycle précédent est apparu sur une hypothèse/actif
  donné, permettant un nouveau regard causal sur un comportement observé
  en direct (pas seulement en rétrospectif).
- Une observation de marché ou de régime nouvelle, avec une
  justification causale écrite AVANT tout calcul (même exigence que les
  cycles 1/2).
- Une instruction explicite d'Ismaël pointant une piste précise.
- Un résultat d'investigation (ex. l'écart live/backtest demandé le
  25/08/2026, voir `docs/DECISIONS.md`) révèle un axe non encore
  exploré.

**Ce qui NE justifie PAS un nouveau candidat** : le simple fait que 10
jours se sont écoulés. Rejouer la même grille déjà rejetée (cycles 1/2 :
19 candidats négatifs sur H2-H5) sans justification neuve serait
exactement le "tester 20 idées en fait gagner une par hasard" que le
§3.9 met en garde contre — inventer une justification a posteriori pour
se conformer au calendrier est explicitement écarté ici, par écrit,
avant que la tentation ne se présente.

**Aucun mécanisme automatique ne déclenche ce contrôle** (`CronCreate`
de cette session est strictement local à la session en cours et expire
de toute façon avant 10 jours — jamais une solution fiable pour une
échéance qui doit survivre au-delà d'une conversation). L'échéance du
2026-09-04 est une date documentée, à vérifier à la prochaine
conversation sur ce sujet — pas un déclenchement garanti sans
intervention d'Ismaël.

---

## 2026-08-25 (suite 4) — PRÉ-ENREGISTREMENT : cycle 3, H4/H5 seules, espace de recherche élargi (FVG/Fibonacci/structure/RSI/Bollinger en combinaison)

Écrit et daté **avant tout calcul sur les données**, demande explicite
d'Ismaël. H2/H3 hors périmètre de ce cycle (pas mentionnées dans la
demande). H1 toujours hors périmètre (aucune ligne de `trend_strategy.py`
concernée).

### Clarification de citation, avant tout le reste

La demande cite « §3.8 : 5 variables maximum » comme plafond du budget
de variables PAR HYPOTHÈSE. Vérifié en relisant `docs/CDC_v4.md` : le
§3.8 du CDC énumère littéralement **5 variables FIXES pour la revue
post-trade** (alignement Matinale, alignement tendance technique, ratio
gain/risque planifié, proximité macro, volatilité ATR normalisée) — un
mécanisme distinct de `trade_analysis.py`, **pas** le budget de
paramètres d'ENTRÉE d'une hypothèse technique. Le texte qui gouverne
littéralement CE budget-là est le **§2.11** : « 2-3 paramètres maximum,
choisis a priori ». Point déjà noté et sciemment dépassé au fil des
paliers précédents (H5 à 3/3 puis 4/3 avec la résolution, H4 à 2/2 puis
davantage avec le cycle 2 — voir historique ci-dessous).

**Décision, pas une correction silencieuse de la demande d'Ismaël** :
le nombre « 5 » est retenu comme **nouveau plafond opérationnel de
variables par hypothèse pour ce cycle**, dans l'esprit du §3.8 (« aucune
variable supplémentaire sans justification théorique écrite préalable »)
appliqué par analogie au budget §2.11, PAS comme une citation littérale
exacte de ce paragraphe précis. Ce point est repris dans l'entrée
CDC-écart ci-dessous.

### Comptage du budget déjà consommé — méthodologie et chiffres

Convention de comptage réutilisée telle qu'établie par le projet
lui-même (voir `hypothesis5_strategy.py`, commentaire budget) : deux
sous-choix étroitement couplés comptent comme **une seule variable**
(ex. « config RSI » = période + seuil ensemble, « config Bollinger » =
période + multiplicateur d'écart-type ensemble, « config résolution » =
résolution d'entrée + résolution de confirmation ensemble — ce ne sont
pas des degrés de liberté indépendants, ils se fixent conjointement),
tandis que des choix indépendants (TP1 et TP2, par exemple) comptent
séparément — exactement la convention déjà utilisée dans le commentaire
budget de `hypothesis5_strategy.py` ("§2.11, cap 2-3, exactement 3/3").

**H4** (`mean_reversion_strategy.py`) :
| # | Variable | Origine |
|---|---|---|
| 1 | Config Bollinger (période + écart-type) | Conception initiale (21/08/2026) |
| 2 | Multiple de largeur du stop | Conception initiale (21/08/2026) |
| 3 | Config résolution (entrée + confirmation croisée) | Cycle 2 (25/08/2026) |

**H4 : 3/5 consommées avant ce cycle.**

**H5** (`hypothesis5_strategy.py`) :
| # | Variable | Origine |
|---|---|---|
| 1 | TP1 (multiple de R) | Conception initiale (23/08/2026) |
| 2 | TP2 (multiple de R) | Conception initiale (23/08/2026) |
| 3 | Config RSI (période + seuil) | Conception initiale (23/08/2026) |
| 4 | Résolution des bougies | V3 (24/08/2026), déjà au-delà du plafond §2.11 d'origine (2-3), assumé alors |

**H5 : 4/5 consommées avant ce cycle.**

Le régime structurel BOS/CHoCH (`ict_strategy`, `FRACTAL_K`) et la
couche session/multi-timeframe restent **partagés/gelés**, jamais
comptés une seconde fois dans le budget d'une hypothèse qui les
réutilise telle quelle (même règle que H3 réutilisant MA200/Donchian(20)
de H1 sans "dépenser" une variable H3 dessus — voir correction du modèle
de budget du 21/08/2026).

### Conséquence directe du plafond (5), appliquée AVANT de choisir les candidats

**H5 a le moins de marge (1 variable restante, 4/5).** Aucune
combinaison à 2 nouveaux outils simultanés (ex. « FVG+Fibonacci comme
DEUX degrés de liberté séparés ») n'est possible sans dépasser 5. **H4 a
2 variables de marge (3/5)**, mais ce cycle n'en consomme volontairement
qu'**UNE** par hypothèse (symétrie, prudence — voir "discipline
renforcée" demandée) : **1 nouvelle variable ajoutée par hypothèse ce
cycle, pas plus**, malgré la marge théorique plus large de H4.
**Après ce cycle : H4 à 4/5, H5 à 5/5 (plafond atteint, plus aucune
variable supplémentaire pour H5 sans en retirer une d'abord — à
respecter strictement au cycle 4).**

Ceci répond explicitement à la consigne : "si H4 ou H5 a déjà atteint ou
dépassé ce plafond... n'ajoute pas de nouvelle variable sans clôturer
d'abord une ancienne" — ni H4 ni H5 n'avait ATTEINT 5 avant ce cycle
(3/5 et 4/5), l'ajout d'UNE variable chacune reste donc dans le budget,
mais amène H5 exactement à la limite — **aucune dérogation improvisée,
le calcul est fait et écrit ici avant tout candidat choisi.**

### Candidats — UNE nouvelle variable par hypothèse, justification théorique écrite avant tout calcul

**H4 — nouvelle variable : confluence RSI(14) au toucher de bande**
(réutilise `hypothesis5_strategy.compute_rsi`, aucune nouvelle fonction
de calcul créée) :
- A (référence, config actuelle) : régime MA200 + toucher de bande de
  Bollinger opposée au régime — inchangé.
- B : A **ET** RSI(14) < 30 pour une entrée longue (bande basse),
  RSI(14) > 70 pour une entrée courte (bande haute). Seuils 30/70 :
  convention RSI standard (survente/surachat), choisis a priori, jamais
  ajustés aux données. **Justification théorique** : un toucher de bande
  seul ne distingue pas un étirement statistique réel (retour à la
  moyenne probable) d'un toucher de bande pendant une poursuite de
  tendance forte (le mode d'échec classique du retour à la moyenne,
  cohérent avec la sévérité déjà observée de H4 aux cycles 1/2) — exiger
  en plus un épuisement de momentum (RSI extrême) filtre ce second cas.

**H5 — nouvelle variable : confluence ICT complète (Fibonacci + FVG) réintroduite comme UN seul filtre combiné**
(réutilise `ict_strategy.compute_fibonacci_zone`/`find_fvgs`, EXACTEMENT
la même logique que `ict_strategy._evaluate_entry`, Hypothèse #2 —
aucune nouvelle fonction de calcul créée) :
- A (référence, V3 actuelle) : régime structurel BOS/CHoCH + RSI(14)
  franchissant 50 dans le sens du régime — inchangé.
- B : A **ET** clôture courante dans la zone de retracement Fibonacci de
  la jambe **ET** un FVG dans le sens du régime chevauchant cette même
  zone — reprend EXACTEMENT la confluence ICT complète de la V2 de H5
  (retirée le 24/08/2026 faute de signal en ~26h de LIVE, jamais évaluée
  statistiquement sur 1,5 an d'historique d'entraînement). **Justification
  théorique** : l'absence de signal en direct sur 26h n'est pas un
  résultat statistique (aucun trade, donc rien à mesurer, cohérent avec
  l'invariant #10 — la V3 avait été motivée par cette absence, pas par
  un résultat observé) — un rejeu sur l'historique complet permet de
  savoir si cette confluence produit ne serait-ce qu'un échantillon
  exploitable, question jamais tranchée jusqu'ici.

**Fibonacci et FVG comptent comme UNE seule variable** (§ méthodologie
de comptage ci-dessus, "config étroitement couplée" — reprend un
mécanisme unique déjà existant tel quel, aucun nouveau seuil ni nouvelle
fonction, exactement le même statut que "config RSI" ou "config
Bollinger" ailleurs dans ce budget).

**4 candidats au total ce cycle** (2 par hypothèse) — nombre volontairement
réduit (cycle 2 en avait jusqu'à 3 par hypothèse) : chaque candidat
introduit cette fois un changement de LOGIQUE, pas seulement une valeur
re-réglée, risque de faux positif plus consequential, discipline plus
stricte justifiée.

### Classification évolution vs nouvelle hypothèse — tranchée AVANT tout calcul, pas après coup

**Les deux candidats B (H4 et H5) sont des NOUVELLES HYPOTHÈSES, jamais
des évolutions.** Critère mécanique, pas un jugement au cas par cas :
`src/hypothesis_params.py::apply_overrides` ne peut modifier que la
VALEUR d'un attribut de module DÉJÀ lu par la fonction d'entrée
existante (`setattr`, jamais une branche de code nouvelle) — ajouter une
condition RSI ou une confluence ICT à `evaluate_entry` exige de modifier
le CODE de la fonction elle-même, ce que ce mécanisme ne peut
structurellement pas faire. **Conséquence, actée avant tout résultat** :
même si un candidat B qualifie sur l'entraînement ET valide sur la
validation, **il ne sera JAMAIS auto-déployé par ce cycle** — candidat B
qualifié = proposition documentée, en attente d'une validation manuelle
explicite d'Ismaël avant toute mise en application, quel que soit le
résultat statistique.

### Découpage temporel, correction statistique, critères (identiques aux cycles 1/2, réutilisés tels quels)

Même CUTOFF (2025-12-01T00:00 UTC), même modèle de coûts
(`SLIPPAGE_SPREAD_MULTIPLIER=1.0`). Correction Bonferroni intra-hypothèse
(m=2 candidats chacune → z≈1.9604, alpha global 0.05, identique au calcul
H5 du cycle 2) : qualifie sur l'entraînement seul si n ≥
`PHASE_B_MIN_TRADES_BACKTEST` (150) ET (moyenne − z×SE) > 0. Le
qualifiant à l'espérance la plus élevée (au plus un par hypothèse, ici
au plus B puisque A est la référence déjà connue négative des cycles
précédents) passe UNE FOIS sur la validation : PASS si n ≥
`PHASE_A_MIN_TRADES_BACKTEST` (60) ET espérance nette > 0.

### Écart CDC explicite — le gabarit "2-3 paramètres a priori" du §2.11 est dépassé, à formaliser

**Quatrième écart CDC de cette semaine, journalisé ici, pas silencieux.**
Le §2.11 fixe littéralement "2-3 paramètres maximum, choisis a priori"
pour la stratégie technique complémentaire. H4 (3 avant ce cycle, 4
après) et H5 (4 avant ce cycle, 5 après) dépassent déjà ce gabarit
depuis plusieurs paliers (H5 explicitement depuis le 24/08/2026). Ce
cycle continue sur cette voie, avec la demande explicite d'Ismaël
d'utiliser un plafond alternatif de 5 (repris du §3.8 par analogie, pas
une citation littérale — voir clarification en tête de cette entrée).
**Ceci n'est pas remis en cause ici** — décision assumée d'Ismaël,
appliquée avec la même discipline anti-surapprentissage (correction
statistique, découpage temporel, justification a priori) que le gabarit
d'origine. **Reste à faire, explicitement noté, pas oublié** : une
future mise à jour du CDC v4 devrait remplacer le "2-3 paramètres" du
§2.11 par le plafond de 5 réellement appliqué depuis le 25/08/2026 (ou
un autre nombre qu'Ismaël retiendrait), pour que le texte de référence
cesse de désigner un budget déjà dépassé en pratique.

Implémentation, résultats complets, tests, classification de chaque
candidat qualifié, rapport honnête des échecs dans `docs/DECISIONS.md`
— même discipline que les cycles 1/2.

---

*Prochaine entrée : réservée à toute évolution future de l'Hypothèse #1,
#2, #3, #4, #5, ou du backtest rétrospectif — jamais une modification de
ce qui précède.*
