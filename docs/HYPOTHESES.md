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

*Prochaine entrée : réservée à toute évolution future de l'Hypothèse #1,
#2 ou #3 — jamais une modification de ce qui précède.*
