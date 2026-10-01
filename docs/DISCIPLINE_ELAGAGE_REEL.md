# Discipline de l'élagage réel — 2026-10-01

## But de mission

Le chantier d'atteignabilité typée (PR #227) a livré un classifieur qui
*annote* les directions mais n'en *élague* aucune, et nomme `IMPOSSIBLE`
des prédictions statiques dont seules 16/118 ont été falsifiées par Lean.
Ce chantier ferme cet écart : **toute direction élaguée de la recherche
l'a été parce que Lean l'a rejetée, et le nom le dit.**

## Nomenclature (statuts typés, jamais booléens)

| Statut | Sens | Autorité |
|---|---|---|
| `ATTEIGNABLE` | l'unification réussit après dépliage borné | analyse statique |
| `CANDIDAT_IMPOSSIBLE` | mismatch après normalisation complète ; **prédiction falsifiable** | analyse statique |
| `INCONNU` | au-delà de la borne de dépliage | analyse statique |
| `IMPOSSIBLE_VALIDÉ` | soumis à Lean (`example : T := t`), **RÉFUTÉ** | Lean |

Règle d'airain : **seul Lean promeut `CANDIDAT_IMPOSSIBLE` en
`IMPOSSIBLE_VALIDÉ`.** Aucun raccourci statique.

## Registre des impossibilités validées

- Fichier : `~/.phi/impossibles_valides.json` (hors dépôt — c'est un
  état local de falsification, pas du code).
- Clé v2 : `v2 ␀ empreinte_énoncé[16] ␀ sorry ␀ candidat ␀ trou ␀
  type_normalisé ␀ terme_normalisé` → fiche `{schema: 2, sorry,
  candidat, trou, type_trou, terme, raison, fichier_verification,
  empreinte_enonce, date}`.
- L'empreinte (sha256 de l'énoncé normalisé du sorry) lie chaque
  validation à SON énoncé : si le corpus évolue, les anciennes
  validations ne sont plus réutilisées silencieusement.
- Entrées v1 (sans empreinte) : comptées comme *legacy*, **non
  réutilisées** pour l'élagage — à re-valider via `--valider-solidite`.
- Corruption/illisibilité/non-inscriptibilité : `ErreurRegistre`
  **visible** (bilan + stderr), jamais `{}` silencieux ; écriture
  atomique (temporaire + `os.replace`).
- `candidats_cablage` consulte le registre : toute direction
  `IMPOSSIBLE_VALIDÉ` est **retirée du pool** avant la mesure.
- `directions_ouvertes = ATTEIGNABLE + INCONNU + CANDIDAT_IMPOSSIBLE`
  (les candidats non encore falsifiés restent ouverts — on ne
  prétend pas ce que Lean n'a pas tranché).

## Boucle de feedback

`--valider-solidite K` :
1. échantillonne K `CANDIDAT_IMPOSSIBLE` par candidat (stratifié par trou) ;
2. les soumet à Lean ;
3. tout `CONFIRMÉ` (RÉFUTÉ par Lean) est **inscrit au registre** ;
4. toute `VIOLATION` (PROUVÉ par Lean) déclenche le durcissement
   du classifieur (la prédiction statique était fausse).

## Garde-fous

- L'élagage ne touche jamais aux `ATTEIGNABLE` ni aux `INCONNU`.
- Le registre est local et réversible : `phi` ne supprime jamais une
  entrée sans ordre explicite ; une entrée périmée se révoque, ne
  s'efface pas en silence.
- R1–R4 inchangés : pas de constantes absolues, pas d'explosion
  combinatoire non guidée (l'échantillonnage reste stratifié et borné).
