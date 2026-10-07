# Partie 4 — contrôle de la fenêtre scellée, période neuve, shadow de tous les couples (08/10/2026)

Branche `couples-v2-08-10` depuis `couples-08-10`. Aucun appel broker,
aucun push, aucun déploiement, aucune action VPS.

## Verdict en 5 lignes

Fenêtre scellée respectée : **oui** (étiquette seule, aucune lecture
hors fenêtre — vérifié par recalcul, pas seulement par lecture du
code). Période neuve disponible : **non** — le bloc candidat
2024-06-15→2026-08-29 est déjà intégralement « fenêtre brûlée »
(comparaisons de structures de sortie, sigma H1/H3, entonnoir de
signaux H2 M15, corrélations). Hypothèses validées/non validées/
indémontrables sur 5 : **sans objet** (étapes 3/4 sautées faute de
période neuve, conformément à l'étape 2.3 du mandat). Shadow étendu à
tous les couples (45, étape 5), non branché au déploiement. Re-test
forward pré-enregistré (étape 6), dates estimées très lointaines sur la
seule cadence live — l'activation du shadow est la condition pratique
pour le rendre atteignable.

## Étape 1 — Contrôle de la fenêtre scellée

**Verdict : étiquette seule, aucune lecture hors fenêtre.**

- `scripts/_couples_structurelles_08-10.py::DESCRIPTIVE_WINDOW =
  ("2019-01-01", "2023-01-01")`, borne de fin **EXCLUSIVE**
  (`start <= time < end`) — vérifié par recalcul, jamais supposé depuis
  le code seul : la bougie la plus récente réellement chargée est datée
  du **2022-12-30** (actifs fermés le week-end) ou du **2022-12-31**
  (BTCUSD/ETHUSD, 7j/7). Aucune bougie de 2023, aucune de la fenêtre
  scellée.
- Un premier essai de comparaison (borne de fin `"2022-12-31"` au lieu
  de `"2023-01-01"`) a lui-même produit un écart artefactuel sur
  BTCUSD/ETHUSD (exclut à tort le 31/12/2022 lui-même, qui appartient à
  2022) — n passant de 34846/34843 à 34824/34821, coût/ATR variant de
  ~0,5 à ~2,7 % selon l'actif. Diagnostiqué comme une erreur de borne de
  la comparaison elle-même, pas une lecture hors fenêtre du calcul
  original. Consigné dans `docs/DECISIONS.md` (entrée du 08/10/2026)
  sans enjolivement, y compris ce faux pas.
- Seul le TITRE du tableau §2 de `docs/COUPLES_08-10.md` était ambigu
  (« 2019-2023 » au lieu de « jusqu'au 2023-01-01 exclu, soit 2019-2022
  inclus ») — corrigé sur cette branche, aucune valeur recalculée ni
  changée.
- Assertion de bornes explicite ajoutée dans
  `scripts/_couples_structurelles_08-10.py::load_bars` (en plus des
  gardes déjà présentes pour la fenêtre scellée et le 2019-01-01) ;
  rejoué après l'ajout, résultats identiques bit à bit à ceux déjà
  publiés.

## Étape 2 — Inventaire de la période neuve

**Données locales (2024-06-15 → 2026-08-29)** : continues et de bonne
qualité pour les 9 actifs × 3 résolutions (HOUR, HOUR_4, DAY) —
vérifié par recalcul des écarts entre bougies consécutives, aucune
anomalie (écart maximal observé : 3j02h pour les actifs fermés le
week-end, 17h pour BTCUSD/ETHUSD, cohérent avec une fermeture normale,
aucun trou). Aucun chevauchement de calcul avec le rafraîchissement du
08/10 (Partie 1), qui ne touchait que la queue 25/09→07/10.

**Cette période a-t-elle déjà servi ? OUI, extensivement — et sous un
nom déjà établi dans ce projet : « la fenêtre BRÛLÉE ».** Recherche
textuelle dans `docs/DECISIONS.md` (jamais supposé, trouvé par
grep) : le terme « fenêtre brûlée »/« fenêtre BRÛLÉE », DÉSIGNANT
EXPLICITEMENT 2024-06-14 → (diverses dates « aujourd'hui », jusqu'à
2026-08-26/28 selon la session), apparaît **à répétition** pour des
mesures de STRATÉGIE, pas seulement des prix :
- Comparaison de structures de sortie H1/H3/H4/H5 (`scripts/_exit_
  structure_comparison.py`, 128 rejeux, 2024-06-14→2026-08-28).
- Corrélations de rendements horaires des 8 actifs (13 004 bougies
  communes).
- Entonnoir de signaux H2 en MINUTE_15 (~473 000 bougies poolées,
  plusieurs sessions).
- Mesure de sigma/cible H1 (`scripts/_measure_h1_sigma_and_target.py`,
  2024-06-14→2026-08-26).
- Mesure de sigma H3/HOUR_4 (2024-06-14→2025-12-01, n=112) — avec une
  conclusion explicite déjà écrite (docs/DECISIONS.md, ligne ~5267) que
  l'« edge » apparent découvert sur cette fenêtre « n'est pas stable,
  c'est [du bruit] ».

**Verdict : AUCUNE période neuve n'existe.** Le bloc candidat
(2024-06-15→2026-08-29) est intégralement couvert par ce qui est déjà
déclaré « brûlé » dans ce projet — même logique que la fenêtre scellée
d'origine (2023-01-01→2024-06-14) : une fois qu'un résultat de
stratégie en a été tiré et publié, tout le bloc est fermé, pas
seulement les jours exacts touchés. **Conformément à l'étape 2.3,
saut direct à l'étape 5** — étapes 3 et 4 non exécutées (sans objet,
aucune période sur laquelle les appliquer). Étape 6 (re-test FORWARD,
indépendante de l'existence d'une période neuve PASSÉE) exécutée
ci-dessous.

## Étape 5 — Shadow de tous les couples

Étendu `src/shadow_tracking.py` (jamais dupliqué, la mécanique
générique `run_shadow_cycle` déjà 100% couverte est réutilisée telle
quelle) : nouveau registre `SHADOW_COUPLES`, un couple par (hypothèse
`_v2` ACTUELLEMENT déployée × actif de `src.asset_whitelist.ASSET_
WHITELIST`, 9 actifs CHFJPY comprise) = **45 couples**, étiquette
`<source>_shadow_couples` (ex. `hypothesis2_v2_shadow_couples`). Couvre
par construction les couples qui seraient bloqués par le plafond de
cluster en réel : le suivi virtuel ne le consulte jamais (même principe
que `SHADOW_CANDIDATES`, déjà établi).

- `src/couples_forward_report.py` (nouveau, 100% de couverture, 10
  tests) : par couple, n shadow/réel (trades fermés, réconciliés), IC
  bootstrap à 95% par blocs calendaires (semaine ISO) de chaque côté,
  avancement vers n=30/50/100 (shadow+réel combinés), taux de rejet par
  plafond de cluster PAR COUPLE (jamais un seul taux global).
- `scripts/run_shadow_couples_cycle.py` (nouveau, lecture seule côté
  broker, aucune méthode d'écriture jamais appelée — vérifié par test,
  pas seulement par lecture du code) : un cycle shadow pour les 45
  couples, réutilise `run_shadow_cycle` sans modification.
- `scripts/rapport_couples_forward.py` (nouveau, lecture seule, 5
  tests, 100% de couverture) : rapport hebdomadaire (prévu chaque
  lundi), écrit `docs/SUIVI_COUPLES/AAAA-MM-JJ.md`, aucune action
  automatique.
- **Non branché au déploiement.** Activation ajoutée à
  `docs/CHECKLIST_10-10_UNIQUE.md` (qui n'existait pas encore — ce
  mandat l'a créé, avec une note honnête sur l'état réel du
  déploiement de la Partie 2, jamais entamé faute d'autorisation
  d'écriture broker accordée à l'agent), conditionnée explicitement à
  la validation préalable de la surveillance de 60 minutes du
  déploiement principal ET du shadow `_v3cand` déjà existant.

## Étape 6 — Re-test forward pré-enregistré

**Règle écrite maintenant, avant toute donnée forward :** le test
structurel (mêmes caractéristiques et sens attendus que
`docs/PROTOCOLE_COUPLES_08-10.md` §0bis, même corrélation de Spearman +
permutation à 10 000 tirages, Bonferroni m=5) sera rejoué **UNE SEULE
FOIS** par hypothèse sur les trades FORWARD (réels + shadow
`_shadow_couples` combinés, mêmes actifs) quand :
1. au moins **6 actifs** atteignent le seuil de n≥100 trades combinés
   (réel+shadow) pour cette hypothèse, **ET**
2. au moins **8 semaines** se sont écoulées depuis l'activation
   effective de `scripts/run_shadow_couples_cycle.py` (T0, voir
   `shadow_epochs`),
le plus tardif des deux conditions. **Aucun regard avant.** Si l'une
des deux conditions n'est jamais remplie : jamais de test, jamais de
regard anticipé, consigné comme tel au prochain mandat qui vérifierait
l'état.

**Dates estimées (ordre de grandeur, cadence LIVE SEULE — shadow pas
encore activé, aucune donnée shadow disponible pour affiner)** :
projection linéaire depuis les n live connus de l'étape 4 de
`docs/COUPLES_08-10.md` (Partie 3), extrapolés à n=100 par actif — un
seuil bien plus élevé que les n=30 déjà estimés à plusieurs mois.
**Avertissement explicite** : ces dates sont un majorant pessimiste —
l'activation du shadow sur 45 couples (étape 5) ajoutera un volume de
signaux bien supérieur au live seul, et raccourcira ces délais de façon
non quantifiable avant d'avoir observé au moins quelques semaines de
cadence shadow réelle.

| Hyp. | Actif le plus avancé (n live) | n=100 estimé (live seul, ordre de grandeur) |
|---|---|---|
| H1 | GOLD/US100 (n=3) | > 2030 (cadence live actuelle trop faible) |
| H2 | GOLD (n=6) | ~2028-2029 |
| H3 | BTCUSD (n=3) | > 2030 |
| H4 | GOLD/ETHUSD (n=3) | > 2030 |
| H5 | ETHUSD (n=3) | > 2030 |

Conclusion honnête : **sans l'activation du shadow_couples, le re-test
forward n'est pas atteignable à un horizon pertinent.** C'est
précisément la justification de l'étape 5.

## Étapes 3 et 4 — sans objet

Non exécutées : l'étape 2 a conclu à l'absence de toute période neuve,
ce qui, selon l'étape 2.3 du mandat, saute directement à l'étape 5.
Aucun tableau par hypothèse (actifs éligibles, corrélation, p corrigé,
statut), aucun couple « à surveiller » issu d'un passage unique sur une
période neuve — ces livrables n'existent pas pour cette Partie 4 (ils
existent déjà pour la fenêtre 2019-2022, voir `docs/COUPLES_08-10.md`
de la Partie 3, jamais rejouée ici).

## Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **Bornes exactes de la « fenêtre brûlée »** : les citations trouvées
   dans `docs/DECISIONS.md` donnent des dates de fin variables
   (2025-12-01, 2026-08-26, 2026-08-28, « aujourd'hui ») selon la
   session — je n'ai pas reconstruit un inventaire jour par jour de
   quelle sous-période précise a servi à quelle mesure (hors périmètre :
   la conclusion « tout le bloc est brûlé » ne change pas selon ce
   détail, même logique que la fenêtre scellée d'origine).
2. **Dates du re-test forward (étape 6)** : estimées sur la seule
   cadence LIVE actuelle (aucune donnée shadow_couples disponible, le
   suivi n'étant pas encore activé) — un majorant très pessimiste,
   explicitement signalé comme tel, pas une prévision fiable.
3. **`is_donchian_trailing=True` pour H5 dans `SHADOW_COUPLES`** :
   repris de la même déduction (docstring du module) que la Partie 3,
   jamais revérifié par une autre source indépendante dans ce mandat.
4. **Aucune exécution réelle de `run_shadow_couples_cycle.py`** contre
   un broker (même en lecture) dans ce mandat — vérifié uniquement par
   tests unitaires (mocks complets), conformément aux limites de ce
   mandat (aucun appel broker autorisé).
