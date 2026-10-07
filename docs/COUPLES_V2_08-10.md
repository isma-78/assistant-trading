# Partie 4 — contrôle de la fenêtre scellée, période neuve, shadow de tous les couples (08/10/2026)

Branche `couples-v2-08-10` depuis `couples-08-10`. Aucun appel broker,
aucun push, aucun déploiement, aucune action VPS.

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
saut direct à l'étape 5** — étapes 3, 4 et 6 non exécutées (sans objet,
aucune période sur laquelle les appliquer).
