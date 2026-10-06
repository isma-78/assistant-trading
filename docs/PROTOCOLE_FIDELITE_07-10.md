# Protocole pré-enregistré — fidélité du simulateur H1-H4 (07/10/2026)

**Écrit et commité AVANT tout calcul de ce mandat.** Aucune paire
live/backtest, aucun écart, aucune décomposition par cause n'a été
calculée avant le commit de ce fichier (hash noté dans
`docs/FIDELITE_07-10.md`).

**Contrainte majeure, constatée AVANT tout calcul, consignée ici par
prudence (ambiguïté résolue dans le sens le plus conservateur)** :
`data/historical/` (seule source de prix autorisée, aucun appel broker
n'est permis dans ce mandat) s'arrête au **2026-09-25T18:00:00 UTC**
(HOUR, uniforme sur les 9 actifs ; HOUR_4 ≈ 12:00 ; DAY ≈ 24/09) — borne
AVANT le début d'E1/E2 (25/09 19:12-19:35 UTC) et avant le retour de H2
à la grille par défaut (25/09 18:14:16 UTC). **Aucun trade de la
configuration ACTUELLEMENT déployée (E1 pour H1/H3/H4, E1 pour H2 à la
grille par défaut) ne peut donc être rejoué ni apparié.** La fidélité
mesurée dans ce mandat porte exclusivement sur la configuration
ANTÉRIEURE à E1 (TP fixe involontaire pour H1/H3/H4 ; ancien combo
EMA=20/RSI=55/N_TF=3/SCORE=1,0 pour H2) — jamais sur E1/E2. **Un statut
« fidélité établie » obtenu ici sera donc explicitement qualifié
d'« établie sur la configuration antérieure à E1, non vérifiée sur la
configuration actuelle » dans tout rapport qui s'appuie sur lui** — cette
restriction est actée MAINTENANT, avant tout chiffre, pas ajoutée après
coup pour atténuer un résultat gênant.

---

## 1. Définition de la fidélité par hypothèse (v2)

### 1.1 Univers et fenêtre de rejeu

Moteur corrigé (`backtest_engine.replay_hypothesis`), configuration
RÉELLEMENT déployée à chaque date (overrides H2 rejoués par période via
`rule_changes`, comme `scripts/_compare_live_vs_backtest_window.py`
20/08-25/09). Fenêtre de rejeu : `[2019-01-01, 2026-09-25T18:00:00[` —
jamais au-delà (donnée absente), jamais la fenêtre scellée
2023-01-01→2024-06-14 (garde par assertion dans le code), jamais avant
2019-01-01.

### 1.2 Inventaire des trades live (avant tout appariement)

Pour chaque source `hypothesis_v2`/`hypothesis2_v2`/`hypothesis3_v2`/
`hypothesis4_v2` : tous les trades `statut='ferme'` avec
`r_multiple_total` non NULL (réconciliés, y compris les 106 trades
récupérés lors de l'audit du 24/09 et les trades fermés normalement
depuis), **séparés en** :
- **éligibles à l'appariement** : `ouvert_at < 2026-09-25T18:00:00` ;
- **hors de portée (E1/E2)** : `ouvert_at >= 2026-09-25T18:00:00` —
  comptés, jamais appariés, jamais utilisés pour mesurer la fidélité ;
- **non réconciliés** (`ferme_non_reconcilie`) : comptés à part, jamais
  dans aucune moyenne.

### 1.3 Appariement

**Granularité : le TRADE fermé (jamais la ligne `signals` brute).**
Pour H2 en particulier, le mécanisme « un trade à la fois » fait que
chaque trade fermé représente déjà un épisode confirmé au sens du
diagnostic du 26/09/2026 (7009 signaux horaires pour 36 épisodes réels) —
comparer des trades plutôt que des lignes `signals` satisfait donc PAR
CONSTRUCTION l'exigence de ne pas apparier heure par heure, sans
mécanisme de regroupement supplémentaire (lecture prudente actée ici).

**Tolérance, FIGÉE maintenant, une seule valeur par hypothèse** (reprise
de la convention déjà établie dans ce projet,
`scripts/_compare_live_vs_backtest_window.py`/
`src/evolution_v2_test.MATCH_TOLERANCE_HOURS`) :

| Hypothèse | Tolérance d'appariement |
|---|---|
| H1 | 2 bougies HOUR (2 h) |
| H2 | 2 bougies HOUR (2 h) |
| H3 | 2 bougies HOUR (2 h) |
| H4 | 2 bougies HOUR (2 h) |

Appariement par (actif, sens, horodatage d'entrée ≤ tolérance), chaque
trade backtest consommé au plus une fois (même mécanique que
`src/evolution_v2_test.pair_candidate_against_baseline`, réutilisée
sans modification — baseline = live, candidate = backtest).

### 1.4 Métriques

- **Taux d'appariement** = paires / n trades live éligibles.
- **Écart absolu moyen en R** = moyenne de `|R_live − R_backtest|` sur
  les paires.
- **Biais signé moyen** = moyenne de `(R_live − R_backtest)` sur les
  paires (positif = le live a fait mieux que le backtest).
- **Taux de signaux sans pendant** : côté live (trade live sans
  contrepartie backtest ≤ tolérance) ET côté backtest (trade backtest
  sans contrepartie live), rapportés séparément.

### 1.5 Décomposition de l'écart par cause (mesurée, jamais un paramètre ajusté)

Six causes, estimées **indépendamment** (jamais une partition exclusive
qui somme à 100% — chevauchement/résidu possibles, rapportés tels
quels) :

1. **Remplissage/délai (entrée)** : `trade_causal_decomposition.
   cout_entree` du trade live (déjà en base, en R, écart prix limite
   demandé vs prix réellement rempli) — directement mesuré, exclu si la
   ligne est absente ou `invalide=1`.
2. **Spread/coût (sortie)** : `trade_causal_decomposition.cout_sortie`
   du trade live — même source, même règle d'exclusion.
3. **Refus de resserrement de stop** : rejoue le MÊME trade backtest
   avec `src.simulator_fidelity.StopRefusalModel` (taux déjà mesurés le
   05/10 sur E1, jamais réajustés ici) vs sans ce filtre — la
   contribution est la différence de R backtest (avec − sans).
4. **Plafond de cluster** : rejoue le portefeuille des 4 hypothèses
   backtest sur la fenêtre (`src.simulator_fidelity.apply_cluster_cap`,
   inchangé) — sa contribution s'exprime comme la part des trades
   BACKTEST sans pendant live qui auraient été bloqués par le plafond
   (un trade bloqué n'existe pas, il n'a pas de R à corriger ; cette
   cause explique des signaux sans pendant, jamais un écart de R par
   paire).
5. **Financement** : déjà TOUJOURS inclus dans le R backtest (modèle
   §2.6, taux plat `FINANCING_BPS_PER_DAY`, jamais une cause résiduelle
   par construction) ; comparé au taux RÉEL capturé
   (`financing_transactions`, 12 lignes connues du 28-29/08/2026)
   actif par actif/nuit quand une capture recouvre une nuit d'un trade
   pairé — sinon non mesurable pour ce trade, exclu de cette seule cause.
6. **Sortie (timing/étiquette)** : recalculée à partir de la MÊME
   trajectoire backtest déjà simulée (bar par bar) — le R qu'aurait
   rendu une clôture à 100% au premier événement de clôture partielle
   (TP1) au lieu de 50%/30%/20% (§2.10), comparé au R backtest réel ;
   capture directement l'effet du bug TP-fixe-involontaire (corrigé
   depuis le 25/09) sur la période pré-E1 mesurée ici — jamais une
   estimation tirée de données live.

**Part de l'écart expliquée par une cause** = somme des `|contribution|`
de cette cause sur les paires / somme de `|écart|` sur les paires.
Seules les causes ≥ 20% (règle 5 des décisions d'Ismaël) sont
candidates à la modélisation à l'étape 2 — **avec un taux/valeur
MESURÉ**, jamais un paramètre réglé pour améliorer un résultat.

## 2. Seuil de fiabilité

**Fidélité « établie »** pour une hypothèse si et seulement si :
écart absolu moyen ≤ 0,30 R **ET** |biais signé moyen| ≤ 0,15 R **ET**
n paires ≥ 20.

**n < 20** → « fidélité non établie (n insuffisant) », avec la date
estimée où n=20 sera atteint à la cadence d'appariement observée
(paires/jour sur la période mesurée, projetée linéairement — ordre de
grandeur, pas une prévision précise).

**n ≥ 20 mais seuils non respectés** → « fidélité non établie » (écart
ou biais trop grand), chiffres rapportés tels quels.

**Rappel acté au préambule** : même « établie », la fidélité mesurée ici
ne couvre QUE la configuration antérieure à E1 — jamais présentée comme
une vérification de la configuration actuellement déployée.

## 3. Validation absolue (décision 2 d'Ismaël, plus stricte que le 06/10)

Une candidate n'est **« éligible à la promotion »** que si, sur
2021+2022 poolé :
- **espérance nette ABSOLUE de la candidate (pas la différence) > 0**,
  **ET**
- **borne basse corrigée de la DIFFÉRENCE vs baseline > 0** (déjà
  calculée le 06/10, rejouée ici seulement si le simulateur est modifié
  à l'étape 2 — règle 4).

**Statuts possibles, mutuellement exclusifs** :
- **« éligible à la promotion »** : les deux conditions ci-dessus, ET
  fidélité établie pour l'hypothèse (règle 2 d'Ismaël : walk-forward
  validé ET forward confirmé ET espérance absolue positive — le forward
  reste de toute façon obligatoire, aucun statut ici ne promeut quoi que
  ce soit, rappelé à l'étape 3) ;
- **« améliore la v2 sans être rentable »** : borne basse de la
  différence > 0, espérance absolue candidate ≤ 0 — **jamais promue**,
  reste en shadow ;
- **« non validée »** : au moins une condition de validation échoue
  (signe instable, borne basse ≤ 0) sans que le MDE n'explique
  l'échec ;
- **« indémontrable sur cette fenêtre »** : MDE (calculé AVANT de lire
  la différence) > effet attendu (brut_min du 05/10) ;
- **« fidélité non établie »** : prévaut sur tout statut positif tant
  que l'hypothèse n'a pas une fidélité établie — un statut
  « éligible »/« améliore sans être rentable » calculé sous fidélité non
  établie est rapporté comme **provisoire**, jamais définitif.

## 4. Règle de rejeu si le simulateur est modifié

Si, et seulement si, une cause ≥ 20% est modélisée à l'étape 2 pour une
hypothèse : le walk-forward 2019-2022 (folds (a)/(b) du protocole du
06/10, `docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §5) de **cette hypothèse
seulement** est rejoué **UNE SEULE FOIS**, mêmes grilles, même règle de
sélection de variante sur l'apprentissage. L'ancien résultat (06/10) et
le nouveau sont rapportés côte à côte, jamais l'un sans l'autre. La
correction de Bonferroni des bornes basses passe à **m=8** (4 hypothèses
× 2 versions du simulateur) pour TOUTES les bornes basses rapportées
dans ce mandat (anciennes et nouvelles, recalculées à m=8 pour rester
comparables). **Aucun second rejeu**, quel que soit le résultat.

Si aucune cause n'atteint 20% pour une hypothèse : le simulateur n'est
pas modifié pour elle, le résultat du 06/10 reste seul valide (sa borne
basse est néanmoins recalculée à m=8 pour comparabilité avec les
hypothèses rejouées).

## 5. Critère d'arrêt de la modélisation

Une cause n'est modélisée que si elle explique ≥ 20% de l'écart absolu
mesuré (§1.5) pour l'hypothèse concernée. Aucune cause modélisée avec un
paramètre ajusté pour améliorer un résultat — uniquement des taux/valeurs
déjà mesurés sur le live (A8 du 05/10 pour le refus de resserrement et le
plafond de cluster, `financing_transactions` pour le financement, la
trajectoire backtest elle-même pour la structure de sortie).

## 6. Ce que ce protocole ne fait pas

Aucune modification de `risk_engine`/`validator`/`circuit_breaker`/
`executor`. Aucune nouvelle candidate, aucun re-balayage de grille,
aucune restriction d'actifs. Aucun appel broker — aucune bougie au-delà
du 2026-09-25T18:00:00 n'est chargée (assertion explicite dans le code
de mesure). H3 : aucune nouvelle candidate ce cycle (décision 3
d'Ismaël), reste en shadow. H5 : inchangée. Aucune hypothèse arrêtée,
suspendue ou retirée (décision 5).
