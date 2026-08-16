# ASSISTANT TRADING — Cahier des charges v4

**Statut** : spécification finale validée — remplace les v1, v2 et v3
**Date** : juillet 2026
**Porteur** : Ismaël — usage strictement personnel, capital propre, non commercialisé

---

# PARTIE 0 — CADRAGE

## 0.1 Objectif en deux temps

| Temps | Objectif | Capital | Durée | Critère de sortie |
|---|---|---|---|---|
| **A — Validation** | Mesurer si les signaux ont un edge réel | Démo, 500 €/actif | **4-6 semaines** | Espérance positive nette de coûts majorés sur ≥20-30 trades |
| **B — Réel limité** | Valider le pipeline en conditions réelles | Réel, montant limité, 1-2 actifs | 3 mois | Espérance confirmée sur ≥50 trades réels |
| **C — Montée** | Rendement | Réel, capital augmenté | — | Réévaluation continue |

**La démo ne s'arrête jamais.** Elle continue en parallèle du réel, sur tous les actifs, pour continuer à alimenter le modèle et évaluer les actifs non encore engagés.

## 0.2 Principe de coûts

Les coûts de fonctionnement (VPS, API LLM, sauvegardes) sont **financés hors capital de trading**. Le capital engagé n'a pas à les absorber ; les métriques de performance ne les intègrent pas.

## 0.3 Statut du canal Telegram

Station X est le **point de départ**, pas une dépendance permanente. L'architecture (champ `source` sur chaque signal, métriques par source) permet d'ajouter ou retirer des sources sans refonte. Le canal fournit :
- Des signaux exécutables (entrée/SL/TP)
- Des analyses quotidiennes (Matinale) exploitables pour l'entraînement du modèle dès le départ

## 0.4 Risques structurels assumés

1. **Les signaux peuvent n'avoir aucun edge.** Hypothèse par défaut ; la phase A existe pour trancher.
2. **La majorité des comptes CFD retail sont perdants** (70-85 % selon les affichages réglementaires des brokers).
3. **L'échantillon de 4-6 semaines est statistiquement faible** — il élimine le cas catastrophique, il ne prouve pas un edge. La confirmation vient en phase B.
4. **L'écart démo/réel érode l'espérance** — traité par majoration délibérée des coûts (§2.6).
5. **Le surapprentissage est le piège principal** de toute couche adaptative sur petit échantillon (§3.8).

---

# PARTIE 1 — PÉRIMÈTRE ET ACTIFS

## 1.1 Inclus

Ingestion Telegram (3 types de messages), classification, structuration LLM, données de marché OANDA, calendrier macro, moteur de risque déterministe, exécution démo continue + réelle parallèle, clôtures partielles, trailing ATR, métriques par actif et source, revue post-trade, moteur causal, apprentissage adaptatif, générateur d'hypothèses, allocation automatique, dashboard, contrôle par bot Telegram, hébergement VPS.

## 1.2 Exclu explicitement

- Gestion de capital pour un tiers (statut réglementé — §6.1)
- Commercialisation ou revente du système ou des signaux
- **Scalping** — incompatible avec la latence structurelle (§2.8)
- Reinforcement learning autonome (§3.9)
- Toute décision financière prise par un LLM (§4.2)

## 1.3 Liste blanche des actifs

Tout signal portant sur un actif hors liste est journalisé, notifié, **jamais exécuté**.

| Actif | Classe | Statut |
|---|---|---|
| XAUUSD (or) | Métal | Actif principal du canal |
| NAS100 | Indice | ⚠️ Sous réserve de taille minimale (§1.5) |
| US30 | Indice | ⚠️ Idem |
| EUR/USD | Forex majeur | Spread le plus serré |
| GBP/USD | Forex majeur | Volatilité supérieure |
| USD/JPY | Forex majeur | Couverture USD |
| BTC/USD | Crypto CFD | Spread large |
| ETH/USD | Crypto CFD | Partiellement décorrélé du BTC |

## 1.4 Ajout et retrait d'actifs

**Protocole** : tout nouvel actif entre **en démo uniquement**, avec sa propre enveloppe de 500 €. Il devient candidat au réel quand ses **propres métriques** passent les critères (§4.8) — jamais par héritage des performances d'un autre actif.

Commandes : `/ajouter_actif <symbole>`, `/retirer_actif <symbole>`, `/passer_reel <symbole>`, `/retirer_reel <symbole>`.

## 1.5 Inconnues bloquantes à lever avant tout code

| Inconnue | Impact si défavorable |
|---|---|
| Taille minimale OANDA sur NAS100 / US30 | Retrait de la liste blanche si incompatible avec l'enveloppe |
| Taille minimale BTC / ETH | Idem |
| Disponibilité crypto CFD (entité EU) | Retrait de la liste |
| Fermeture week-end du crypto | Ajustement des attentes horaires |

**Aucune spécification de dimensionnement n'est définitive avant vérification.**

## 1.6 Horaires réels

Aucun actif ne tourne réellement 24/7 : forex et indices fermés le week-end, crypto CFD fréquemment fermé le week-end chez les brokers. **Le système est 24/5.**

---

# PARTIE 2 — FINANCE ET TRADING

## 2.1 Le R-multiple — unité universelle

Le **R** est le montant risqué sur un trade (distance entrée→stop × taille de position).

- Stop touché = **−1R** par définition
- Sortie à 2× la distance du stop = **+2R**
- Espérance = moyenne des R sur les trades clôturés

Toutes les métriques, seuils et coupe-circuits s'expriment en R — valables quel que soit le régime de capital.

## 2.2 Relation taux de réussite / ratio gain-perte

```
Espérance (R) = (Taux_réussite × Gain_moyen_R) − ((1 − Taux_réussite) × 1R)
```

| Taux de réussite | Ratio gain/risque minimum pour +0,2R |
|---|---|
| 30 % | 4,0R |
| 50 % | 2,4R |
| 60 % | 2,0R |
| 80 % | 1,5R |

**Le taux de trades gagnants n'est pas un critère de décision** — il est manipulable via le placement de TP1 et pousse à couper les gains trop tôt. Affiché à titre indicatif uniquement.

## 2.3 Gestion du capital — enveloppes par actif

**Démo** : une enveloppe indépendante de **500 € par actif**, avec son historique, ses métriques, son coupe-circuit propres.

**Réel** : montant limité au démarrage, sur 1-2 actifs sélectionnés (§2.4).

### Dimensionnement — risque adaptatif

```
risque_par_trade = pourcentage_courant × enveloppe_actif

pourcentage_courant :
    2 %  par défaut
    4 %  UNIQUEMENT si l'actif remplit TOUTES ces conditions :
         - ≥ 50 trades clôturés sur cet actif
         - espérance nette ≥ +0,3R
         - profit factor ≥ 2,0
         - drawdown max ≤ 10 %
```

Le passage à 4 % est **automatique mais réversible** : si les métriques repassent sous les seuils, le système revient à 2 % sans intervention. Journalisé à chaque bascule.

À 500 € : **10 € de risque par trade** (2 %), **20 €** (4 %).

### Plafond d'exposition simultanée

Total engagé sur toutes positions ouvertes : **maximum 10 % de l'enveloppe**. Protège contre la concentration de risque corrélé (plusieurs positions sur la même devise, ou or + indices en régime risk-off).

### Réinvestissement — règle des 50 %

```
Trade GAGNANT sur l'actif X :
    enveloppe[X]     += 0,5 × gain_net
    reserve_globale  += 0,5 × gain_net    (sanctuarisée définitivement)

Trade PERDANT sur l'actif X :
    enveloppe[X]     −= perte_nette        (imputée en totalité)
```

La réserve est **globale et hors risque définitivement**, même en cas de série de pertes ultérieure.

### Transition vers un régime professionnel

```
SI enveloppe_actif ≥ 2 000 € :
    risque_par_trade = MIN(formule ci-dessus, 0,5 % × enveloppe_actif)
```

### Rechargement d'une enveloppe démo épuisée

Autorisé, mais **journalisé comme événement distinct** (`envelope_reload`). Les métriques de drawdown conservent la trace de l'épuisement. Un échec de mesure ne s'efface jamais.

## 2.4 Sélection des actifs pour le réel — score de confiance statistique

Score **calculé, jamais jugé par un LLM**. Déterministe, auditable, reproductible.

```
Conditions éliminatoires (toutes obligatoires) :
    ✓ nb_trades ≥ 20 (phase A) puis ≥ 50 (phase B)
    ✓ espérance nette > 0
    ✓ taille minimale broker compatible avec l'enveloppe
    ✓ spread médian < 15 % du stop typique de l'actif

Score de classement (actifs éligibles uniquement) :
    score = espérance_nette_R
          × facteur_échantillon      (√(nb_trades/50), plafonné à 1)
          × facteur_stabilité        (1 − drawdown_max/20%, plancher 0)
```

Le **facteur d'échantillon** est essentiel : il empêche qu'un actif avec 8 trades chanceux devance un actif avec 40 trades solides. La taille d'échantillon est toujours affichée à côté du score.

## 2.5 Allocation automatique du capital réel

Le système alloue vers les actifs les mieux classés, sous plafonds durs :
- **Maximum 2 actifs en réel** en phase B
- Aucun actif ne peut recevoir plus de 60 % du capital réel total
- Toute réallocation est **notifiée et journalisée**, appliquée automatiquement
- Un actif dont le score passe sous le seuil éliminatoire est **retiré du réel automatiquement** (positions ouvertes laissées se clôturer normalement)

## 2.6 Traitement des coûts — majoration délibérée

Toute simulation et métrique applique :
- Le spread **réellement observé** au moment du signal
- Un **slippage forfaitaire pénalisant** à l'entrée et à la sortie
- Les **frais de financement overnight** au-delà d'une journée
- Une **marge élargie** sur crypto et indices

Principe : métriques délibérément pessimistes. Un système qui passe les critères avec des coûts majorés a une marge.

## 2.7 Coupe-circuits

### Démo — par actif

Un actif qui casse ne gèle pas l'apprentissage des autres.

| Déclencheur | Portée | Action | Reprise |
|---|---|---|---|
| −2R cumulés dans la journée | Cet actif | Arrêt des entrées sur cet actif | Auto le lendemain |
| −5R cumulés dans la semaine | Cet actif | Arrêt complet sur cet actif | Manuelle |
| −12R depuis le plus haut | Cet actif | Arrêt + alerte | Manuelle |

### Réel — par actif ET global

Mêmes seuils par actif, plus un plafond global : **−3R cumulés dans la journée** toutes positions réelles confondues → arrêt de toutes les entrées réelles.

### Surcouche anomalie système

| Déclencheur | Action |
|---|---|
| 3 erreurs API consécutives | Pause générale |
| Écart anormal prix attendu / exécuté | Pause + alerte |
| Coupe-circuit sur ≥5 actifs simultanément | Pause générale — bug probable, pas marché |
| Aucun message capté depuis 7 jours | Alerte (canal inactif ou accès perdu) |

## 2.8 Latence et péremption

Délai structurel : 10-60 s (capture → extraction → validation → ordre). **C'est ce qui rend le scalping impossible.**

- **Fenêtre de péremption** : signal dont la zone d'entrée n'est plus atteignable dans une tolérance définie → rejeté
- **Ordres limite** dans la zone d'entrée, jamais au marché
- **Mesure systématique** du décalage prix-signal / prix-exécution

## 2.9 Calendrier économique

| Impact | Fenêtre | Effet |
|---|---|---|
| **Fort** (NFP, taux, CPI, banquiers centraux) | −30 min → +15 min | **Blocage total** sur la/les devise(s) concernée(s) |
| **Moyen** (PMI, balance commerciale) | ±15 min | **Taille réduite de 50 %** |
| **Faible** | — | Journalisé (variable statistique §3.8) |

**Clôture anticipée** : position exposée à une annonce forte, sous le breakeven à T−10 min → clôturée automatiquement. Position au-dessus du breakeven → laissée ouverte. Journalisée `cloture_anticipee_macro`, comptée séparément.

Ce mécanisme **ferme, il ne modifie jamais un stop** (invariant §4.2).

## 2.10 TP multiples

### Répartition

| Palier | Fraction | Rôle |
|---|---|---|
| **TP1** | **50 %** | Sécurise la moitié tôt — lisse la courbe de capital |
| **TP2** | **30 %** | Capture le mouvement principal |
| **TP3** | **20 %** | Laissé courir sous trailing |

### Calcul du R

- **R toujours calculé sur le risque initial**, jamais recalculé après clôture partielle
- **R total = Σ (fraction × R atteint au palier)**
- **SL déplacé au breakeven dès TP1 touché** — resserrer est autorisé, élargir est interdit

### TP3 — trailing déterministe

**Trailing = 2 × ATR(14)** sur l'unité de temps du signal, plancher au breakeven.

Un trailing serré (3-5 pips) est explicitement écarté : sur un actif dont le stop initial est de 30 pips, il coupe la position sur une simple respiration de marché.

Le coefficient est **affiné par la couche d'apprentissage** une fois 30+ trades accumulés. **Aucun trailing « au jugement du modèle »** — un trailing non déterministe rendrait impossible de distinguer une perte due à la stratégie d'une variation aléatoire du LLM.

## 2.11 Stratégie technique complémentaire

Source indépendante du canal, basée uniquement sur les prix — **zéro LLM, donc zéro risque d'hallucination ou d'injection**.

**Deux niveaux** :
1. **Régime de fond** : moyenne mobile longue (200) détermine le sens autorisé par actif
2. **Déclencheur** : règle réactive à l'intérieur du régime autorisé

**Avantage décisif : backtestable** sur des années de bougies OANDA en quelques minutes. Découpage temporel obligatoire (optimisation sur une période, validation sur une période non vue). 2-3 paramètres maximum, choisis a priori.

**Métriques calculées séparément par source** — si l'une a un edge et l'autre non, les mélanger masquerait ce fait.

## 2.12 Nombre de trades par jour

**Aucune limite.** Le seul frein est le coupe-circuit en R. Le plafond d'exposition simultanée (§2.3) reste actif — il répond à un besoin différent : la concentration de risque corrélé.

---

# PARTIE 3 — IA ET APPRENTISSAGE

## 3.1 Routage multi-modèles

La contrainte de souveraineté ne s'applique pas (usage strictement personnel, aucune donnée tierce). Le routage est optimisé sur le rapport pertinence/coût :

| Tâche | Fréquence | Modèle | Justification |
|---|---|---|---|
| **Extraction** (texte → JSON) | Élevée (des dizaines/jour) | Modèle rapide et économique | Tâche simple et cadrée, le coût unitaire domine |
| **Revue post-trade** | Moyenne (à chaque clôture) | Modèle rapide et économique | Tâche cadrée, volume significatif |
| **Analyse causale** (au coupe-circuit) | Faible | **Claude** | Raisonnement complexe sur données hétérogènes |
| **Génération d'hypothèses** | Trimestrielle | **Claude** | Raisonnement causal, enjeu élevé, volume négligeable |

**Aucun modèle ne décide quoi que ce soit** — invariant §4.2 inchangé.

## 3.2 Les trois types de messages

| Type | Contenu | Traitement |
|---|---|---|
| **Matinale** (quotidien) | Contexte, niveaux, structure, biais | Contexte uniquement — **jamais exécutable**. Alimente le biais de fond avec fenêtre de validité |
| **Signal** | Entrée / SL / TP1-2-3 | **Seul type exécutable**, après validation complète |
| **Suivi** (réponse) | « TP1 TOUCHÉ », « SL » | Rattaché au trade d'origine via `reply_to_msg_id` |

Le threading Telegram est **obligatoire**.

## 3.3 Méthodologie ICT du canal

Le canal raisonne en FVG (Fair Value Gap), retracements de Fibonacci (50 %, 61,8 %, 78,6 %), structures de marché.

**Conséquence** : les niveaux sont des **zones**, pas des points. L'extraction produit des intervalles ; la validation croisée raisonne en zones.

## 3.4 Gestion des contradictions internes

Le canal publie un champ « Sentiment » exprimant sa lecture de tendance, qui peut contredire le corps de l'analyse (cas observé : analyse Nasdaq baissière suivie de « Sentiment haussier »).

**Règle** : le système **ignore le champ sentiment**, conserve le biais du corps de l'analyse, et **notifie l'incohérence**. Il ne devine jamais laquelle est correcte.

## 3.5 Défense contre l'injection de prompt

Le canal est une **source non fiable par construction**.

- Prompt système instruit d'ignorer toute instruction contenue dans le message
- Message encadré par délimiteurs, présenté comme donnée, jamais comme instruction
- **Sortie contrainte par schéma strict** — toute réponse hors schéma est rejetée
- **Validation déterministe en aval** : actif en liste blanche, stop cohérent avec le sens, prix plausible vs marché réel, taille dans les plafonds
- Tout échec journalisé et alerté, jamais silencieusement ignoré

## 3.6 Gestion des hallucinations

- Température 0, instruction de renvoyer `null` plutôt que deviner
- **Score de confiance auto-déclaré**, seuil 0,75 → revue manuelle en dessous
- **Contrôle croisé déterministe** avec les prix réels — un stop incohérent est rejeté quelle que soit la confiance déclarée
- **Audit manuel intégral** des extractions les 3 premières semaines

## 3.7 Ce que l'apprentissage signifie réellement

**Un LLM n'apprend pas entre les appels.** Chaque requête est indépendante. Aucune mémoire des trades, aucune amélioration avec l'usage.

L'apprentissage vient **entièrement de la base de données et du code statistique**. Le LLM est un traducteur sans état, remplaçable sans rien perdre de l'acquis.

## 3.8 Protocole anti-surapprentissage

### Les 5 variables initiales — figées a priori

| # | Variable | Justification théorique |
|---|---|---|
| 1 | Alignement avec le biais de la Matinale | Cohérence interne de la méthode |
| 2 | Alignement avec la tendance technique objective | Suivre la tendance améliore-t-il le résultat ? |
| 3 | Ratio gain/risque planifié | Les setups ambitieux tiennent-ils ? |
| 4 | Proximité d'une annonce macro | Validité du filtre macro |
| 5 | Volatilité relative à l'entrée (ATR normalisé) | Le régime conditionne-t-il la réussite ? |

**Aucune variable supplémentaire sans justification théorique écrite préalable.** Ajouter des variables après avoir vu les données est du data dredging — interdit.

### Règles statistiques

- **≥ 10 trades par variable** avant tout test
- **Correction pour comparaisons multiples** sur tout test
- **Découpage temporel train/test** : règle apprise sur 1→N, validée sur N+1→M. Une règle qui ne tient pas en test est abandonnée, pas ré-ajustée
- Tout ajustement est **proposé, journalisé, validé manuellement** — jamais silencieux
- Tout ajustement appliqué est **horodaté**, permettant de recalculer les métriques par régime

### Sur les sources de données additionnelles

Les sources supplémentaires (données de traders publics, indicateurs macro, autres) sont **collectées et stockées dès que disponibles** — l'historique a de la valeur. Mais elles n'entrent dans les décisions qu'**une variable à la fois**, avec justification théorique et sous la règle des 10 trades/variable.

⚠️ **Ajouter massivement des variables sur un petit échantillon garantit de trouver des corrélations fausses.** Avec 50 trades et 20 variables, le système trouverait des patterns spectaculaires et faux. C'est le mode d'échec principal du trading algorithmique amateur.

## 3.9 Générateur d'hypothèses (Claude)

Répond à la demande de « stratégies proposées par l'IA », sous contrainte scientifique stricte.

### Cycle

```
1. GÉNÉRATION (trimestrielle, Claude)
   → Examine trades + contexte + sources disponibles
   → Formule une hypothèse AVEC justification causale explicite
   → Pré-enregistrée AVANT tout test (empêche l'ajustement a posteriori)

2. TEST (déterministe, sans LLM)
   → Uniquement sur données POSTÉRIEURES à la génération
   → Correction statistique proportionnelle au nombre d'hypothèses testées

3. VALIDATION (toi)
   → Hypothèse qui passe → proposition de règle
   → Jamais appliquée automatiquement
```

### Plafonds

- **3 hypothèses par cycle maximum.** Au-delà, la correction pour comparaisons multiples rend la barre inatteignable — tester 20 idées en fait « gagner » quelques-unes par pur hasard.
- **Cadence trimestrielle** (et non mensuelle) : à faible volume de trades, une hypothèse ne peut pas se trancher en 30 jours.
- **Fenêtre de test strictement prospective**, jamais sur les données ayant servi à formuler l'hypothèse.

### Promotion

Une hypothèse validée sur ≥10 trades prospectifs devient candidate à une **6e variable officielle**, avec sa justification versée au dossier.

### Attente réaliste

**1 à 3 hypothèses validées par an, au mieux.** La plupart resteront « non concluantes » — c'est le résultat honnête à ce volume de données, pas un échec du dispositif.

## 3.10 Revue post-trade

À chaque clôture, analyse **honnête sur son propre statut** :

- Construite **uniquement à partir des faits captés** — aucune cause inventée
- **Systématiquement étiquetée hypothèse non confirmée**. Un trade isolé ne prouve rien
- **Ne déclenche aucun ajustement**
- Utilité : **pédagogique** et **détection d'anomalie**

## 3.11 Moteur d'analyse causale

Déclenché automatiquement à chaque activation d'un coupe-circuit.

**Rassemble** : tous les trades de la période, contexte de marché, événements macro, scores de confiance, exposition corrélée entre trades perdants, références externes sur la même période.

**Produit trois catégories, traitées différemment** :

| Catégorie | Action |
|---|---|
| **Anomalie technique** (extraction aberrante, erreur d'exécution, slippage hors norme) | **Corrigée immédiatement** — maintenance, le seuil de volume ne s'applique pas |
| **Événement de marché généralisé** | **Aucune action** — c'est le marché, pas le système |
| **Hypothèse de pattern** | **Journalisée en attente** — ne devient proposition qu'au seuil de volume |

**Garde-fou non négociable** : une mauvaise journée, même parfaitement comprise, ne prouve jamais un pattern.

---

# PARTIE 4 — ARCHITECTURE

## 4.1 Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────┐
│  SOURCES                                                     │
│  [Telegram Station X]  [OANDA prix]  [Calendrier macro]      │
└──────┬──────────────────────┬──────────────┬─────────────────┘
       │                      │              │
┌──────▼──────────────────────▼──────────────▼─────────────────┐
│  INGESTION (aucune interprétation)                           │
│  telegram_listener (+threading) · market_data · macro_cal    │
└──────┬───────────────────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│  CLASSIFICATION (matinale / signal / suivi)                  │
└──────┬───────────────────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│  EXTRACTION (LLM — seule zone non déterministe)              │
│  zones et non points · schéma strict · température 0         │
└──────┬───────────────────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│  VALIDATION (100 % déterministe)                             │
│  liste blanche · cohérence · contrôle croisé prix réels      │
│  péremption · anti-injection · détection contradiction        │
└──────┬───────────────────────────────────────────────────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│  MOTEUR DE RISQUE (100 % déterministe — cœur critique)       │
│  sizing 2-4 % · coupe-circuits R · exposition · filtre macro │
│  TP multiples · trailing ATR                                 │
└──────┬───────────────────────────────────────────────────────┘
       │
       ├───────────────────────┬─────────────────────────────┐
┌──────▼────────────┐  ┌───────▼──────────────┐              │
│ EXÉCUTION DÉMO    │  │ EXÉCUTION RÉELLE     │              │
│ continue, tous    │  │ parallèle, 1-2 actifs│              │
│ actifs, 500 €/act │  │ (verrou Go/No-Go)    │              │
└──────┬────────────┘  └───────┬──────────────┘              │
       └───────────────────────┘                             │
                       │                                     │
┌──────────────────────▼──────────────────────────────────────┐
│  JOURNALISATION · MÉTRIQUES (par actif, par source)         │
│  SCORE DE CONFIANCE · ALLOCATION AUTO · REGISTRE CAPITAL    │
│  REVUE POST-TRADE · MOTEUR CAUSAL                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  APPRENTISSAGE (50 trades) · HYPOTHÈSES (trimestriel)       │
│  propose, n'applique jamais seul                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  SUPERVISION — Bot Telegram + Dashboard à la demande        │
└─────────────────────────────────────────────────────────────┘
```

## 4.2 Invariants non négociables

1. **Aucun LLM n'a accès au broker, au capital, ni au moteur de risque**
2. Tout calcul financier est **déterministe et testé unitairement**
3. Aucun signal ne devient un ordre sans validation déterministe complète
4. Le passage en réel est **verrouillé par code**, pas par discipline personnelle
5. Tout ordre est journalisé **avant** envoi et **après** confirmation
6. Les plafonds de risque ne sont **pas modifiables à chaud**, uniquement par redéploiement
7. **Un stop peut être resserré, jamais élargi.** Aucune moyenne à la baisse. Aucune augmentation de position perdante
8. Aucune donnée sensible en clair dans Git
9. **Fail-safe** : toute erreur non gérée arrête les entrées, ne les poursuit pas
10. Le score de confiance est **calculé, jamais jugé par un LLM**

## 4.3 Autonomie et supervision

**Le système exécute seul** : capture, extraction, validation, dimensionnement, ordres, gestion des TP, trailing, coupe-circuits, allocation entre actifs, notifications.

**Ton rôle** :
- Valider les **changements de règles** proposés par la couche d'apprentissage
- Valider les **hypothèses** promues en variables
- Décider des **injections de capital** et du passage réel
- Ajouter ou retirer des **actifs** et des **sources**
- Superviser via dashboard et notifications

**Aucune validation trade par trade** au-delà de la phase P2 (rodage).

## 4.4 Modules

| Module | Responsabilité | Déterministe |
|---|---|---|
| `telegram_listener` | Capture brute + threading | ✅ |
| `message_classifier` | Tri matinale / signal / suivi | ✅ |
| `market_data` | Prix, bougies, spread, ATR, MM via OANDA | ✅ |
| `macro_calendar` | Annonces, fenêtres de blocage et réduction | ✅ |
| `parser` | Texte → JSON via LLM (zones) | ❌ *(seule zone)* |
| `validator` | Liste blanche, schéma, cohérence, péremption, contradictions | ✅ |
| `risk_engine` | Sizing 2-4 %, coupe-circuits R, TP multiples, trailing ATR | ✅ |
| `trend_strategy` | Stratégie technique — 0 % LLM | ✅ |
| `executor` | Ordres OANDA démo + réel, clôtures partielles | ✅ |
| `capital_manager` | Enveloppes, réinvestissement 50 %, réserve, rechargements | ✅ |
| `confidence_scorer` | Score statistique par actif (§2.4) | ✅ |
| `allocator` | Allocation automatique du capital réel (§2.5) | ✅ |
| `db` | Persistance complète | ✅ |
| `metrics` | R-multiple, espérance, PF, drawdown — par actif et source | ✅ |
| `post_trade_review` | Analyse à la clôture | Mixte |
| `causal_analysis` | Approfondissement au coupe-circuit | Mixte |
| `adaptive_rules` | Statistiques par variable, propositions | ✅ |
| `hypothesis_engine` | Génération trimestrielle + test prospectif | Mixte |
| `dashboard` | Génération de la page à la demande | ✅ |
| `control_bot` | Bot Telegram de pilotage | ✅ |
| `go_nogo` | Vérification des critères, verrou du mode réel | ✅ |

## 4.5 Modèle de données

```sql
raw_messages      (id, telegram_msg_id, reply_to_msg_id, channel, received_at,
                   raw_text, message_type, processed)
signals           (id, raw_message_id, source, type, actif, sens,
                   entree_min, entree_max, stop_loss, tp1, tp2, tp3,
                   confiance, statut, raison_rejet, created_at)
market_snapshots  (id, signal_id, bid, ask, spread, atr, ma_longue,
                   tendance_fond, captured_at)
macro_events      (id, datetime, devise, intitule, impact, source)
trades            (id, signal_id, source, actif, mode, direction,
                   taille_initiale, prix_entree_prevu, prix_entree_reel,
                   slippage_entree, stop_loss_initial, stop_loss_courant,
                   risque_eur, pourcentage_risque_applique,
                   ouvert_at, ferme_at, r_multiple_total,
                   pnl_brut, couts, pnl_net, statut)
trade_partials    (id, trade_id, palier, fraction, prix_sortie,
                   r_atteint, motif, executed_at)
trade_features    (trade_id, align_matinale, align_tendance_fond,
                   ratio_gain_risque_prevu, proximite_macro, volatilite_relative)
envelopes         (id, actif, mode, capital_initial, capital_courant,
                   nb_rechargements, created_at, updated_at)
envelope_ledger   (id, envelope_id, trade_id, montant_avant, montant_apres,
                   type_mouvement, recorded_at)
reserve_ledger    (id, trade_id, montant_ajoute, reserve_totale, recorded_at)
confidence_scores (id, actif, calculated_at, nb_trades, esperance,
                   facteur_echantillon, facteur_stabilite, score, eligible)
allocations       (id, actif, montant_alloue, motif, decided_at)
post_trade_review (id, trade_id, faits_json, analyse_texte, created_at)
causal_analysis_log(id, declencheur, trades_concernes_ids, contexte_json,
                    categorie, analyse_texte, action_prise, created_at)
hypotheses        (id, generated_at, enonce, justification_causale,
                   fenetre_test_debut, nb_trades_test, resultat_stat,
                   statut, promue_variable, decided_at)
metrics_snapshot  (id, actif, source, calculated_at, nb_trades, esperance_r,
                   profit_factor, drawdown_courant, drawdown_max,
                   taux_reussite_indicatif)
rule_changes      (id, proposed_at, variable, constat_stat, ajustement_propose,
                   statut, validated_at, applied_at)
logs              (id, timestamp, level, module, message)
```

## 4.6 Dashboard

**Accès** : commande `/dashboard` du bot → génère une page HTML statique avec lien temporaire. Pas de page permanente, pas d'authentification à maintenir, pas de surface d'attaque exposée en continu.

**Style** : tableaux bruts, données lisibles, aucun branding.

**Contenu, dans l'ordre** :

| Bloc | Détail |
|---|---|
| Vue d'ensemble | Capital démo total, capital réel, réserve, nb actifs actifs, statut système |
| Par actif | Mode, montant engagé, espérance, PF, nb trades, drawdown, % de risque appliqué, statut Go/No-Go |
| Périodes | Gains/pertes semaine, mois, depuis le début — par actif et agrégé |
| Trades | Nombre réalisé par période, par actif, par source |
| Hypothèses | Formulées, testées, en attente, promues, taux de survie global |
| Classement | Actifs triés par score de confiance, **avec taille d'échantillon affichée à côté** |
| Décisions | Historique des ajouts/retraits d'actifs, passages en réel, réallocations |
| Historique complet | Tous les trades clôturés depuis le début, filtrable |

**Les actions (ajout/retrait d'actif, passage en réel) restent sur le bot Telegram**, pas sur la page web — canal déjà sécurisé, pas de nouvelle surface d'attaque avec accès au capital.

## 4.7 Exigences de test

**Couverture unitaire 100 % du moteur de risque et des métriques** avant toute exécution, y compris démo :
- Taille de position pour chaque actif et configuration de stop
- R-multiple sur clôtures partielles, cas limites, gaps
- Bascule 2 % ↔ 4 % aux valeurs exactes des seuils
- Coupe-circuits à la valeur exacte du seuil
- Trailing ATR : resserrement uniquement, jamais d'élargissement
- Verrou Go/No-Go : blocage effectif du mode réel
- Rejet des signaux malformés, hallucinés, hors liste blanche, ou contenant une injection

**Justification** : c'est le seul code dont un bug se traduit directement en perte financière.

## 4.8 Phasage

| Palier | Contenu | Durée | Capital |
|---|---|---|---|
| **P0** | Infra, VPS, sécurité, **vérification §1.5** | 1-2 sem | — |
| **P1** | Ingestion, classification, extraction, audit manuel des extractions | 1-2 sem | — |
| **P2** | Moteur de risque, enveloppes, exécution démo avec validation manuelle (rodage) | 1 sem | Démo |
| **P3** | **Démo autonome, tous actifs** — TP multiples, trailing, coupe-circuits | **4-6 sem** | Démo 500 €/actif |
| **P3.5** | **PORTE A** — espérance positive nette sur ≥20-30 trades ? | — | — |
| **P4** | **Réel limité**, 1-2 actifs sélectionnés par score. Démo continue en parallèle | 3 mois | Réel limité |
| **P4.5** | **PORTE B** — espérance confirmée sur ≥50 trades réels ? | — | — |
| **P5** | Montée du capital, stratégie technique, apprentissage N1, hypothèses | — | Réel |

**Date de décision** : arrêt ou poursuite tranché **9 mois après le premier signal capté**. Sans échéance, un projet de mesure devient un projet sans fin.

## 4.9 Verrou technique du mode réel

Évalué **par actif et par source**, jamais sur un agrégat mélangé.

```
Mode réel autorisé sur un actif UNIQUEMENT si :
  ✓ nb_trades_clotures_actif   ≥ 20 (porte A) / ≥ 50 (porte B)
  ✓ esperance_r_nette          > 0 (porte A) / ≥ +0,2R (porte B)
  ✓ profit_factor_net          ≥ 1,5
  ✓ drawdown_max               ≤ 15 %
  ✓ taille_min_broker          compatible avec le capital réel
  ✓ spread_median              < 15 % du stop typique
  ✓ validation_manuelle        confirmée par commande dédiée
Sinon → refus, message explicite, journalisation.
```

Vérifié **à chaque démarrage et avant chaque ordre**.

---

# PARTIE 5 — SÉCURITÉ

## 5.1 Modèle de menace

| Actif | Menace | Impact |
|---|---|---|
| Clés API OANDA | Vol → ordres frauduleux | **Financier direct** |
| Session Telegram | Vol → accès à **toute** la messagerie | **Vie privée majeure** |
| Clés API LLM | Vol → usage facturé | Financier limité |
| VPS | Compromission → tout ce qui précède | Critique |
| Canal Telegram | Contenu malveillant → injection | Financier |

## 5.2 Mesures obligatoires

**Compte broker**
- Clé API **restreinte au trading**, sans droit de retrait si l'option existe
- Compte **séparé**, approvisionné du seul montant tolérable en perte
- Authentification forte

**Session Telegram**
- ⚠️ Une session Telethon donne accès à **l'intégralité** du compte. **Compte Telegram dédié obligatoire**, abonné uniquement à Station X, sans conversation personnelle
- Fichier de session en permissions restrictives, jamais dans Git

**VPS**
- SSH par clé uniquement, mot de passe et root désactivés
- Pare-feu fermé par défaut, fail2ban
- Mises à jour de sécurité automatiques
- Utilisateur applicatif non privilégié

**Dépôt Git**
- `.gitignore` vérifié avant le premier commit
- **Dépôt privé obligatoire**
- Scan de secrets avant chaque push
- Toute clé exposée est **révoquée**, jamais simplement « effacée du commit »

**Sauvegardes**
- Base de données sauvegardée quotidiennement, chiffrée, hors VPS
- **La base est le projet** : la perdre, c'est perdre l'historique qui conditionne toute décision

---

# PARTIE 6 — CONFORMITÉ ET FISCALITÉ (France)

> Cadre informatif. Ni conseil financier, ni juridique, ni fiscal. Un expert-comptable doit confirmer le traitement applicable avant le passage en réel.

## 6.1 Ligne réglementaire

Tant que le système gère **exclusivement ton propre capital**, l'activité est privée et non réglementée.

Franchissent la ligne (agrément AMF/ACPR requis, risque pénal) :
- Gérer le capital d'un tiers, même un proche, même gratuitement
- Vendre, louer ou distribuer le système ou ses signaux
- Publier des recommandations d'investissement au public

**Ces usages sont exclus du périmètre (§1.2).**

## 6.2 Produit et levier

Forex, or, indices et crypto pour particuliers passent par des **CFD** : levier plafonné au niveau européen, protection contre le solde négatif, affichage obligatoire du pourcentage de clients perdants.

Le levier employé découle mécaniquement de la règle de risque — ce n'est pas un paramètre à régler.

## 6.3 Fiscalité

Les gains sur CFD pour un résident fiscal français relèvent en principe des plus-values de cession de valeurs mobilières, soumises au PFU (30 %), avec option possible pour le barème progressif.

- **Le PEA ne couvre pas les CFD**
- Un **compte chez un broker étranger doit être déclaré**
- Des règles spécifiques peuvent s'appliquer selon fréquence et volume

**Action requise** : consultation d'un expert-comptable **avant** le passage en réel.

## 6.4 Conditions d'utilisation du canal

Absence d'interdiction d'automatisation confirmée. À reconfirmer si les conditions évoluent. **Le contenu ne doit jamais être redistribué, republié ou revendu.**

---

# PARTIE 7 — EXPLOITATION

## 7.1 Commandes du bot

| Commande | Effet |
|---|---|
| `/etat` | Positions ouvertes, enveloppes, mode, statut par actif |
| `/dashboard` | Génère la page complète, lien temporaire |
| `/pause [actif]` | Stoppe les entrées (global ou par actif) |
| `/reprendre [actif]` | Réactive |
| `/stop_urgence` | Ferme tout, coupe le système |
| `/metriques [actif]` | Espérance, PF, drawdown, échantillon |
| `/enveloppes` | État de chaque enveloppe + réserve |
| `/classement` | Score de confiance par actif, avec échantillon |
| `/ajouter_actif <sym>` | Ajoute en démo |
| `/retirer_actif <sym>` | Retire de la démo |
| `/passer_reel <sym>` | Passe en réel (si verrou franchi) |
| `/retirer_reel <sym>` | Repasse en démo seule |
| `/propositions` | Ajustements de règles proposés |
| `/hypotheses` | État du cycle d'hypothèses |
| `/recharger <actif>` | Recharge une enveloppe démo (journalisé) |

## 7.2 Notifications automatiques

Ouverture et clôture de position (avec R-multiple), clôture partielle à chaque palier, déclenchement de coupe-circuit, bascule 2 %↔4 %, réallocation de capital, contradiction détectée dans une Matinale, signal hors liste blanche, échec d'extraction répété, absence de message depuis 7 jours, erreur API, franchissement d'un palier de métriques, actif atteignant les critères de passage en réel.

## 7.3 Rituels de supervision

- **Quotidien (2 min)** : lecture des notifications
- **Hebdomadaire (15 min)** : dashboard, métriques par actif, signaux rejetés
- **Mensuel (1 h)** : propositions d'ajustement, progression vers les portes
- **Trimestriel** : cycle d'hypothèses, revue de fond

## 7.4 Critères d'arrêt du projet

Le projet s'arrête — sans échec, avec un résultat utile — si :
- L'espérance est négative sur ≥30 trades démo à la porte A
- Le taux d'extraction fiable reste sous 70 % malgré les ajustements
- Le canal cesse d'être actif ou change radicalement de format, sans source de remplacement
- Aucun actif ne franchit les critères après 6 mois
- Le drawdown en réel atteint 15 %
- **La date de décision (9 mois) est atteinte sans franchissement de la porte B**

---

# PARTIE 8 — DÉMARRAGE

## 8.1 Prérequis bloquants

1. **Compte démo OANDA ouvert** et vérification §1.5 (tailles minimales)
2. **Compte Telegram dédié** créé et abonné à Station X
3. **Identifiants API Telegram** obtenus sur my.telegram.org
4. **Clés API LLM** obtenues
5. **VPS Linux** provisionné

## 8.2 Points restant ouverts

- Confirmation que le canal ne publie pas régulièrement hors liste blanche
- Paramètres exacts de la stratégie technique (§2.11) — à définir avant backtest
- Consultation expert-comptable à planifier avant P4

---

*Fin du cahier des charges v4.*

*Ce document est un cadre technique et méthodologique. Il ne constitue ni un conseil en investissement, ni une garantie de performance. Le trading de produits à effet de levier comporte un risque de perte substantiel, y compris de la totalité du capital engagé. La majorité des comptes CFD particuliers sont perdants.*
