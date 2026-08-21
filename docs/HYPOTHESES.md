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

*Prochaine entrée : réservée à toute évolution future de l'Hypothèse #1,
#2, #3 ou #4 — jamais une modification de ce qui précède.*
