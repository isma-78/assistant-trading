# Corrections des hypothèses H1-H5 et défauts d'exécution — pré-enregistrement (29/10/2026)

Écrit et commité AVANT l'implémentation. Suit `docs/adequation_paires_2026-10-29.md`
(commit `041264c`). Compte DÉMO uniquement ; verrou du réel intact ; aucun stop
n'est élargi ; plafonds de risque inchangés.

## 1. Constat de départ (ère E1/E2, trades réconciliés, n=189 ; détail dans le bilan)

R moyen par hypothèse (IC 95 % bootstrap) : H1 +0,065 (n=28), H2 −0,056 (n=83),
H3 −0,284 (n=36), H4 −0,111 (n=23), H5 −0,406 (n=19). **Aucun IC ne exclut 0** :
aucune hypothèse n'est significativement négative. Total −0,123 R/trade
(−23,3 R), IC [−0,28 ; +0,04].

## 2. Statut des hypothèses

Règle pré-enregistrée de **suspension** (appliquée à l'avenir, jamais à
l'ère qui l'a inspirée) : une hypothèse est suspendue si, sur les seuls
trades ouverts APRÈS la date de début post-correction, avec n ≥ 15
réconciliés sur ses actifs retenus, la **borne haute** de l'IC 95 % bootstrap
du R moyen est < 0. Revue à chaque palier n = 15, 30, 53.

| Hyp. | Décision | Raison |
|---|---|---|
| H1 | **Conservée** | Seule à R moyen > 0 ; aucun défaut propre. |
| H2 | **Conservée, active** (décision du 05/10) | Actifs recalculés (§3). |
| H3 | **Conservée sous surveillance** | Pire R après H5, mais IC contient 0 ; pertes dominées par coût (b) et persistance (a), traitées par le périmètre d'actifs. |
| H4 | **Conservée** | IC contient 0 ; seule hypothèse de retournement (prémisse P<1 testée structurellement). |
| H5 | **Conservée sous surveillance** | Idem H3 ; n=19 insuffisant pour suspendre (règle ci-dessus). |

**Aucune variable de stratégie n'est modifiée** (budget de 5 variables par
hypothèse intact) : aucune cause de perte ne se formule comme une règle
générale applicable avant l'entrée ET soutenue par ≥10 trades par variable sans
tomber dans le sur-ajustement. Les contrefactuels (e) « stop trop serré »
exigeraient d'élargir un stop : **interdit**, donc non actionnable.

## 3. Changement de périmètre (mapping hypothèse → actifs d'ENTRÉE)

Le périmètre d'actifs n'est pas une variable de stratégie (même statut que
l'ajout de CHFJPY le 28/08). Issu mécaniquement des règles du fichier
d'adéquation (scores, vetos sur deux fenêtres, ρ ≤ 0,60, ≤ 4 actifs,
tie-break coût/ATR), calculé par `scripts/_adequation_structurelle_29-10.py` :

H1 → GBPUSD, USDJPY · H2 → GBPUSD, USDJPY · H3 → GBPUSD, USDJPY ·
H4 → US30, EURUSD · H5 → GBPUSD, USDJPY.

**Garde-fou de sécurité** : seules les ENTRÉES sont restreintes. La gestion
des positions déjà ouvertes (stops, trailing, enveloppes, réconciliation)
continue sur l'ensemble des 9 actifs — jamais d'orphelin.

Risque assumé et divulgué : les actifs retenus ont un historique E1 pauvre
(n=19) et défavorable (−0,48 R) ; la règle interdit de décider sur ce
résultat (échantillon insuffisant). Conséquence pratique : le flux de trades
chute, l'évaluation sera lente.

## 4. Défauts d'exécution corrigés (règles générales, avant l'entrée)

1. **Filtre de coût à l'entrée** : refuser l'entrée si spread / distance du stop
   > 0,10 (coût d'une aller-retour > 0,10 R avant glissement et financement).
   Théorie : un coût > 0,10 R consomme une espérance typique de 0,1-0,2 R
   (fichier d'adéquation §1). Observé en E1 : 34 trades au-dessus du seuil,
   −0,18 R/trade (descriptif, non utilisé pour fixer le seuil).
2. **Ordre limite déjà exécutable** : 208/208 refus `limit.price` d'E1 étaient
   « exécutable immédiatement » (niveau d'entrée déjà franchi côté marché ;
   un niveau de cassure est sémantiquement un ordre STOP, pas LIMIT). Correctif
   sûr : ne pas envoyer un ordre que le broker refusera ; le setup est
   re-tenté au cycle suivant (comportement actuel inchangé sur les remplissages,
   moins d'appels, moins de pression 429).
   **Non implémenté (décision prudente, journalisée)** : passer les entrées de
   cassure en ordre STOP natif — exige un test d'écriture broker que je ne
   peux pas faire sans risque ; à valider par Ismaël sur le démo (étape
   documentée dans le bilan).
3. Aucune modification du stop garanti ni de ses bandes (min/max) : les
   `stoploss.minvalue/maxvalue` restent traités par le mécanisme de
   réessai existant (jamais d'élargissement d'une position ouverte).

## 5. Évaluation future

Début « post-correction » = horodatage de redémarrage des exécuteurs sur le code
corrigé (enregistré dans `hypothesis_epochs`, époque `P1_29-10`). Toute
évaluation future ne porte que sur les trades ouverts après cet instant.

## 6. Suite (29/10/2026, soir) — mandat « 3 points d'attention » ; DÉMO uniquement

Époque d'évaluation inchangée : `P1_29-10` (début 2026-10-29T19:03:55Z, non réenregistrée).
Les changements ci-dessous modifient l'EXÉCUTION (type d'ordre) et la robustesse, aucun
paramètre de stratégie, aucun plafond de risque, aucun stop. Déployés le 29/10 à 20:38-20:39Z
(HEAD VPS `2f7f814`, merge de la branche `suite-29-10`), watchdog : 8 processus « up ».

### 6.1 Ordres STOP d'entrée natifs
- **Où** : les hypothèses fixent leur niveau d'entrée = clôture de la dernière bougie ; quand le marché
  l'a déjà dépassé, un LIMIT est refusé (208 refus `limit.price` en E1). `executor.open_signal` choisit
  désormais, par code (`entry_filters.choose_entry_order_type`) : niveau côté attente => LIMIT (inchangé) ;
  niveau STRICTEMENT au-delà du marché (achat > ask, vente < bid) => ordre STOP natif (`capital_client.
  place_stop_entry_order`, `POST /workingorders`, `type: STOP`) ; niveau exactement sur le prix => refus.
- **Invariants** : stop garanti attaché à la création (ValueError avant tout appel réseau sinon ; pas de
  distance attachable => refus `niveau_deja_franchi`, comme avant) ; validation en amont inchangée
  (risk_engine, capital_manager, validator, péremption : le niveau doit rester à moins de 50 % de la
  distance de stop du prix) ; sens du stop vérifié (`stop_protection_rejection`) ; aucun code ne
  modifie un stop d'un ordre existant ; Station X inchangée (LIMIT).
- **Fail-safe** : erreur broker inattendue sur un STOP (hors 429 et refus de stop gérés) => coupe-circuit
  `_stop_entry_orders_tripped` : ce process n'envoie plus de STOP (retour au refus) jusqu'à redémarrage ;
  désactivable par config (`STOP_ENTRY_ORDERS_ENABLED`) + redéploiement. Expiration/annulation : les
  mêmes circuits que les LIMIT (`cancel_stale_working_orders`, test dédié).
- **Test d'intégration démo** (`scripts/test_integration_ordre_stop.py`, compte démo H5, 29/10 ~21:17 CET) :
  EURUSD, taille minimale 100, **BUY STOP à 1,15105** (ask 1,11752 +3 %) puis **SELL STOP à 1,08394**,
  `stopDistance` 0,01151 / 0,01084 garanti. Résultat : `dealStatus: ACCEPTED`, `orderType: STOP`,
  `guaranteedStop: true`, présent dans `/workingorders`, puis annulé et absent. Le broker renvoie la distance
  du stop BUY avec un signe négatif (`-0,01151`) = stop sous le niveau : convention broker, côté perdant.
  Aucun ordre n'a été rempli.
- **Limites constatées** : (a) un STOP ne se remplit qu'au retour du prix au niveau : le taux de remplissage
  réel reste à mesurer sur les trades post-P1_29-10 ; (b) glissement possible au déclenchement ; (c) ni
  stop garanti exigé par le broker ni distance attachable => pas d'ordre STOP (refus) ; (d) pas de STOP pour
  les niveaux au-delà de 50 % de la distance de stop du prix (péremption du validateur, volontairement inchangée).

### 6.2 Erreur « Les fractions doivent sommer à 1.0 » (trend_executor, trade 17028)
- **Cause racine** (pas le symptôme) : trade H1/ETHUSD à 3 paliers ; tp1 (0,138) et tp2 (0,083) ont péri (ordres
  non remplis), le runner (0,056) est resté en attente. `_settle_unfilled_legs`, appelée à la péremption, s'arrête
  tant qu'un palier est en attente, et n'était **jamais rappelée au remplissage** du runner : `taille_initiale`
  restait 0,277 au lieu de 0,056, donc la fraction de la clôture du runner = 0,056/0,277 = 0,2022 ≠ 1. Le calcul
  du R lève à juste titre (fail-safe conservé) et recommençait à chaque cycle depuis le 15/10.
- **Correctif** : `_check_pending_leg_fills` rappelle `_settle_unfilled_legs` après chaque remplissage (taille
  ramenée à la taille réellement ouverte, risque jamais augmenté). Test de non-régression (reproduit la séquence
  péremption → remplissage → clôture) vérifié ÉCHOUANT sans le correctif. Test que des fractions incohérentes
  lèvent toujours (le trade reste `ouvert` et bloque l'actif).
- **Trade 17028 réparé** de façon déterministe (`scripts/reparer_fractions_paliers.py` : taille := paliers
  remplis, fractions remises à l'échelle, somme vérifiée à 1e-6, sinon marqué `anomalie_technique`) puis finalisé
  par le code normal : statut `ferme`, R = +1,1564 (21:37 CET). Il est antérieur à P1_29-10 : hors évaluation.
  Dry-run VPS : c'était le seul trade concerné. Commande : `venv/bin/python scripts/reparer_fractions_paliers.py
  data/assistant_trading.db [--apply]`.

### 6.3 Actualisation des bougies (429 depuis le 11/10)
- **Cause** : le cron dominical (`rafraichir_historique.py`) échouait dès `POST /session` (429) sans aucune
  nouvelle tentative (3 dimanches : 11, 18, 25/10), et s'arrêtait au premier signe de 429 des exécuteurs.
- **Correctif** : `src/retry.retry_with_exponential_backoff` (jitter complet, plancher = en-tête `Retry-After`
  exposé par `CapitalApiError.retry_after`, plafond 120 s/600 s) sur login, switch_account et chaque requête de
  prix ; débit ≥ 5 s entre requêtes et entre actifs ; pause de 60 s (4 fois) au lieu d'un abandon si les
  exécuteurs signalent un 429 ; traitement actif par actif avec reprise (combinaison déjà à jour => 0 appel).
  Tests mockés du backoff (délais exacts, Retry-After, plafond, erreur non retentable).
- **Rattrapage** : exécuté le 29/10 20:41-20:42Z, 27 appels sur 200, 27/27 combinaisons « ok » (HOUR, HOUR_4,
  DAY), aucun 429. **MAE/MFE relancé** (`data/analysis_29-10/out_maj/`, mêmes 189 trades E1) : couverture
  66/189 -> 189/189 ; MAE médiane 1,04 R, MFE médiane 1,01 R ; sur les 106 perdants, MFE médiane 0,45 R. Les
  résultats de `out/` (bilan) ne sont pas remplacés.

### 6.4 Surveillance automatique (règles : `docs/regles_surveillance_2026-10-29.md`, commit avant tout code)
- `src/surveillance.py` (100 % couvert) : Student unilatéral + Holm sur 5 hypothèses, n ≥ 15, suspension si borne
  haute de l'IC < 0 (équivalence testée). Suspension écrite dans `hypothesis_suspensions` + ligne `logs`
  (module `surveillance`) ; plus aucune entrée générée (`technical_strategy_executor`) ni acceptée
  (`open_signal`, motif `hypothese_suspendue`) ; les positions ouvertes restent gérées. Erreur de lecture =
  suspendu (fail-safe). Le code ne lève jamais une suspension : levée = acte humain explicite (redéploiement).
- Contrôle quotidien : cron VPS `30 6 * * *` `scripts/controle_surveillance.py` ; état lisible
  `data/surveillance/etat_AAAA-MM-JJ.md` (hypothèse × actif : n, R moyen, IC 95 %, statut). Premier état : 0 trade
  post-P1_29-10, tout « non concluant ».

### 6.5 Retour arrière (si un STOP se comporte de façon inattendue)
```powershell
ssh assistant@163.172.189.239 "cd assistant-trading && git checkout 00617d5 && scripts/restart_process.sh all"
# Le cron de surveillance et le correctif fractions disparaissent avec le retour ; la table hypothesis_suspensions reste (inoffensive).
# Désactiver seulement les STOP : STOP_ENTRY_ORDERS_ENABLED = False dans src/executor.py + commit + redéploiement.
```
