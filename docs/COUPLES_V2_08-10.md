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
