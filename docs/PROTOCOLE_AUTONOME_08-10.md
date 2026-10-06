# Protocole pré-enregistré — fidélité (config. actuelle), exécution, E2, déploiement autonome démo (08/10/2026)

**Écrit et commité AVANT tout appel broker, tout calcul et tout
déploiement de ce mandat.**

---

## 1. Fidélité — population et règles (config. ACTUELLEMENT déployée)

**Population** : trades `_v2` **réconciliés** (`statut='ferme'`,
`r_multiple_total` non NULL) ouverts **à partir du redémarrage corrigé
du 25/09/2026 19:35:00 UTC** (le correctif niveau/distance du stop,
commit `531c704` — plancher UNIFORME retenu pour les 4 hypothèses,
postérieur aux horodatages d'époque individuels H1 19:12:17Z/H2
19:12:26Z/H3 19:12:35Z/H4 19:12:44Z, qui ne portaient pas encore ce
correctif), jusqu'à la date du rafraîchissement (étape 2). **CHFJPY
comptée et rapportée À PART, jamais dans les métriques poolées**,
pour les 4 hypothèses (tightening par rapport au 07/10, où seule H2-H4
l'excluaient déjà — H1 l'exclut aussi ici, lecture prudente retenue :
CHFJPY n'a jamais fait l'objet d'un amendement formel d'univers de
verdict, voir bilan du 05/10).

**Appariement, tolérances, métriques, seuils** : **STRICTEMENT
identiques** à `docs/PROTOCOLE_FIDELITE_07-10.md` §1.3/§1.4/§1.5/§2,
jamais réécrits ici (référence, pas une redéfinition) :
- tolérance 2 bougies HOUR par hypothèse ;
- granularité = le trade fermé (jamais `signals`) ; **H2 explicitement
  par ÉPISODE** (décision 2) — un épisode = un trade fermé, le
  mécanisme un-à-la-fois empêchant structurellement la multiplication
  intra-épisode (même lecture que le 07/10 §1.3, appliquée ici sans
  modification) ;
- écart absolu moyen ≤ 0,30 R **ET** |biais signé| ≤ 0,15 R **ET**
  n ≥ 20 paires → « établie » ; n < 20 → « non établie (n insuffisant) »
  avec date estimée de n=20 (projection linéaire, ordre de grandeur) ;
  n ≥ 20 mais seuils dépassés → « non établie », valeurs rapportées
  telles quelles ;
- décomposition par cause **mesurée**, jamais un paramètre ajusté ;
  **une cause ≥ 20% de l'écart avec n ≥ 20 paires est rapportée et
  PROPOSÉE À UN MANDAT SÉPARÉ — jamais modélisée ici** (limite
  explicite de ce mandat : « aucune modification du simulateur »).

**H2** : mesurée exclusivement sur sa configuration actuellement
déployée (grille par défaut depuis 2026-09-25T18:14:16Z, E1 depuis
19:12:26Z, corrigée 19:35:00Z — même plancher que les autres), jamais
sur l'ancien combo retiré (série du 07/10, close, jamais remesurée ni
agrégée). Si n est trop faible pour conclure : dit explicitement, aucun
verdict forcé.

**Simulateur** : moteur actuel, **non modifié**, aucun rejeu du
walk-forward 2019-2022, aucune nouvelle variable ni candidate de
signal, aucune restriction d'actifs.

## 2. Intégrité du rafraîchissement

Sur chaque actif/résolution rafraîchi : fenêtre de **2 jours de
chevauchement** avec les données déjà locales. **Écart bid/ask médian
toléré : 1 tick du broker** (résolu par `instrument_specs.json`/
`AssetSpec`, jamais une valeur absolue uniforme entre actifs). Si
l'écart médian dépasse 1 tick sur la fenêtre de chevauchement : **le
rafraîchissement de CET actif/résolution est ABANDONNÉ**, les nouvelles
données ne sont PAS utilisées (ni écrites, ni fusionnées), consigné
comme tel, les anciennes données restent seules valides pour cet
actif/résolution. **Aucun fichier existant n'est jamais écrasé** —
écriture dans un fichier temporaire, fusion append-only après contrôle
réussi uniquement.

## 3. Exécution — métriques (lecture seule)

Par actif ET poolé, sur la population du §1 (configuration actuelle,
tous les trades/signaux, pas seulement les réconciliés — le taux de
remplissage et les échecs de placement ont besoin des tentatives, pas
seulement des trades fermés) :
- taux de remplissage (trades remplis / ordres placés) ;
- glissement d'entrée en TICKS de l'actif (`prix_entree_reel −
  prix_entree_prevu`, converti via le pas de cotation
  `AssetSpec`/`instrument_specs.json`) ;
- spread à l'entrée (`market_snapshots.spread` au signal) **vs spread
  médian de cet actif à cette heure UTC** (réutilise
  `src.spread_analysis.hourly_spread_by_asset`, inchangé) ;
- délai de remplissage (`trades.ouvert_at` du statut `en_attente` à
  `ouvert`, quand mesurable) ;
- taux de refus FINAL de resserrement de stop (déjà mesuré par actif
  dans le bilan du 05/10 — remesuré ici sur CETTE population) ;
- échecs `limit.price` (motif `limite_refusee`, A3) ;
- coût par trade en R (`trade_causal_decomposition.cout_entree +
  cout_sortie`, quand disponible).

**Aucun verdict de cellule (hypothèse, actif) à n < 30** — seuls les
chiffres POOLÉS (toutes cellules d'une même métrique) portent une
conclusion ; les cellules sous 30 sont rapportées comme observation
brute, jamais comme preuve.

**Coût/R par hypothèse** : rapporté avec `brut_min = 2 × √(coût₀ ×
MDE₀)` (coût₀ = coût moyen mesuré ci-dessus, MDE₀ = celui déjà calculé
le 05/10/07-10 pour cette hypothèse, jamais recalculé) — un **constat
de faisabilité** (la friction mesurée est-elle compatible avec un edge
démontrable à ce gate de puissance), **jamais une sélection ni un
verdict**.

## 4. E2 — retry adaptatif du resserrement de stop (paramètres figés)

- **3 tentatives maximum**, délais **2 s puis 5 s** entre elles.
- **Arrêt immédiat sur tout 429**, aucun appel supplémentaire pour
  cette tentative de resserrement (le trade garde son stop actuel,
  retenté au cycle suivant comme avant E2).
- **Chaque tentative ne peut QUE resserrer** : le nouveau stop proposé à
  chaque essai doit être STRICTEMENT plus protecteur ou égal au
  précédent de CETTE séquence de retry (jamais un repli vers une valeur
  moins protectrice) — revérifié par `risk_engine.evaluate_stop_update`
  à chaque tentative, invariant #5, jamais contourné.
- **Refus du broker** : le stop ACTUEL (déjà en place côté broker) reste
  inchangé — jamais une valeur par défaut, jamais une estimation.
- **Erreur inattendue du module** (exception non prévue par ses propres
  gardes) : **repli sur le comportement précédent** (une seule
  tentative, comme avant E2) **et journalisation** — jamais un crash de
  la boucle de gestion.

## 5. Critère de succès de E2 (mesure avant/après, PAS un A/B)

Sur **n ≥ 30 tentatives de resserrement après activation** (poolées,
toutes hypothèses) :
- **validé** si le taux de refus FINAL est inférieur d'**au moins 50%
  (relatif)** au taux de référence pré-activation (§5.3 de l'étape 5,
  consigné dans `docs/REFERENCE_E2_08-10.md` AVANT toute activation) ;
- **ZÉRO élargissement** observé (toute occurrence = échec immédiat du
  critère, indépendamment du taux de refus) ;
- **aucune hausse des 429** des exécuteurs par rapport à la référence
  (comparaison de taux, pas de compte brut, pour rester comparable à
  des durées différentes).

**Arrêt automatique de E2 (retour à OFF), un seul motif suffit** :
- un élargissement est observé (même un seul) ;
- une erreur répétée du module (**3 occurrences en 10 minutes**) ;
- les 429 des exécuteurs augmentent par rapport à la référence.

L'arrêt automatique est vérifié par `scripts/mesure_effet_e2.py`
(lecture seule), appelé en tâche planifiée tant que E2 est à ON (étape
7.F) — jamais une vérification manuelle ponctuelle une fois E2 activé.

## 6. Déploiement autonome démo — pré-conditions et retour arrière

**Pré-conditions, TOUTES requises avant tout déploiement** :
1. Environnement démo confirmé **par code** (`config.capital_
   environment == "demo"`), local ET sur le VPS, revérifié
   immédiatement avant chaque action d'écriture.
2. Sauvegarde de la base **vérifiable** : taille non nulle, fichier
   SQLite valide (`PRAGMA integrity_check`), une copie lisible
   (requête de comptage réussie).
3. Suite de tests **100% verte** (aucun `failed`, aucune erreur de
   collecte).
4. **Aucune position ouverte non réconciliée ni ordre en attente non
   suivi** — vérifié APRÈS le nettoyage de l'étape 6, jamais avant.
5. **État de repli connu** : le tag/hash du commit actuellement
   déployé sur le VPS, noté avant toute action.

**Surveillance** : 60 minutes après chaque redémarrage d'exécuteur.

**Critères de retour arrière automatique (un seul suffit)** :
exécuteur absent ou en boucle de crash ; exception non gérée (hors
celles déjà fail-safe du code existant) ; coupe-circuit déclenché par
une erreur de CODE (pas par une perte réelle légitime) ; stop élargi ;
ordre émis par le shadow (devrait être structurellement impossible,
vérifié explicitement) ; plafonds de cluster différents de la
référence (occupation incohérente avec les positions réellement
ouvertes) ; journaux non horodatés ; réconciliation en échec ; position
ouverte non réconciliée apparue après le redémarrage.

**Retour arrière** = restaure le tag précédent (`git checkout
<tag_repli>` sur le VPS), redémarre les exécuteurs de la version
précédente (`scripts/restart_process.sh all`), vérifie leur état
(`process_watchdog`), envoie une alerte Telegram, **puis STOP du
déploiement** — aucune nouvelle tentative autonome dans cette session.

## 7. Verrou d'arrêt d'urgence

Commande UNIQUE (lecture seule côté broker, écriture seulement dans
`system_state`/cron), à exécuter sur le VPS, qui remet E2 et le shadow
à OFF sans redéploiement ni redémarrage d'exécuteur :

```bash
cd ~/assistant-trading && crontab -l | grep -v -E "shadow_cycle|shadow_milestone|rapport_hebdo_shadow|mesure_fidelite_continue|mesure_effet_e2" | crontab - && venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute(\"INSERT INTO system_state (key, value, updated_at) VALUES ('e2_enabled', 'false', datetime('now')) ON CONFLICT(key) DO UPDATE SET value='false', updated_at=datetime('now')\"); c.commit()"
```

Effet : tous les crons de suivi/E2 retirés immédiatement (ils ne
relanceront rien au prochain tick) ; `system_state.e2_enabled='false'`
lu par `src/execution/stop_tightening_retry.py` au cycle suivant de
chaque exécuteur (repli immédiat sur le comportement d'une seule
tentative, sans attendre un redémarrage). Les 6 exécuteurs et le
shadow lui-même continuent de tourner (ce verrou ne les arrête pas,
seulement E2 et la surveillance automatisée).

## 8. Ce que ce protocole ne fait pas

Aucune modification de `risk_engine`/`validator`/`circuit_breaker`.
`executor.py` : seul le point d'appel du resserrement de stop peut être
touché (étape 5.2), jamais ailleurs. Aucun mode réel. Aucune nouvelle
variable ni candidate de signal, aucun rejeu du simulateur, aucune
restriction d'actifs, aucune promotion. E1/E3/E4 : analyse seule,
jamais activés. Aucune hypothèse arrêtée, suspendue ou retirée.
