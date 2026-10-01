# Encerclement — cerner les mécanismes sans les fermer au premier contact

## Doctrine

Une fermeture au premier contact ne mesure que la trivialité du
cadavre : un câblage qui meurt dès le premier test Lean n'était pas un
mécanisme sensible. La sensibilité se révèle par la **résistance** —
c'est ce qui survit quand on l'encercle qui mérite l'attention, car
c'est là que peuvent se cacher les vrais chemins de preuve (ou les
obstructions profondes).

L'INCONNU du classifieur est l'aveu d'ignorance honnête : « borne de
dépliage atteinte » ou « mismatch non décisif ». L'encerclement en fait
un objet de travail : chaque direction INCONNU est sondée sous
plusieurs angles Lean **avant tout verdict**. On ne ferme rien au
premier contact — on cerne.

## Les quatre angles

Chaque angle est une *proposition* de câblage soumise à Lean via un
fichier de test minimal (même en-tête B8–B11 que la validation de
solidité : union des télescopes, imports, opens, univers). L'angle ne
prétend rien ; **seul Lean tranche**. Sondé en premier, toujours :

0. **CÂBLAGE_DIRECT** — le câblage original soumis tel quel à
   l'élaborateur. C'est la vérité de terrain : l'élaborateur Lean est
   strictement plus fort que l'analyse statique du classifieur (ex. la
   paire conservatrice ∀/→ : le classifieur dit INCONNU là où Lean
   tranche). Si Lean accepte, l'INCONNU cachait un vrai câblage ; s'il
   RÉFUTE par mismatch, l'incertitude est levée. Les angles suivants
   n'explorent que les cas où l'élaborateur reste non concluant.

1. **DEPLIAGE_PROFOND** — déplie les définitions au-delà de la borne du
   classifieur (`_PROF_DEPLIAGE`). Le dépliage étant une égalité
   définitionnelle, un verdict Lean sur la forme dépliée vaut pour la
   forme originale (transfert noté dans le dossier).
2. **COERCION** — ascription explicite `((terme : Tterme) : Ttrou)` quand
   les têtes forment une paire de coercition connue (ℕ → ℤ → ℚ → ℝ → ℂ…).
   Si Lean élabore la coercition, l'INCONNU cachait un câblage réel.
3. **SOUS_TERMES** — tête et applications partielles d'un terme composé
   (`f x y` → `f`, `f x`). Une chaîne mal formée donne un angle
   NON_CONCLUANT honnête, jamais un faux verdict.

## Statuts typés du dossier

Le dossier de circonscription (`~/.phi/dossiers_encerclement.json`,
clé v2 liée à l'énoncé, écriture atomique, échecs visibles — même
discipline que le registre) porte un statut typé, jamais un booléen :

- **ENCERCLE** — le câblage original est RÉFUTÉ par Lean (angle
  CÂBLAGE_DIRECT, ou DEPLIAGE_PROFOND avec transfert par égalité
  définitionnelle). Lean a tranché : la direction est inscrite au
  registre des impossibilités (fiche : angle décisif nommé). C'est Lean
  qui promeut, pas une analyse statique — même discipline que
  `--valider-solidite`. Le mismatch d'une variante (coercition,
  sous-terme) ne transfère jamais à l'original : il documente la
  frontière, il ne ferme rien.
- **RESISTANT** — encerclement complet, aucun verdict décisif.
  Mécanisme sensible : **escaladé** (console + JSON), jamais enterré.
- **ATTEIGNABLE_TROUVE** — un angle a PROUVÉ le câblage. Piste de
  preuve réelle : **escaladée**, jamais inscrite au registre des
  impossibilités.

Même avec un premier angle décisif, **tous les angles applicables sont
sondés** avant le verdict final (test verrouillé :
`test_on_ne_ferme_pas_au_premier_contact`). Encercler ≠ tuer vite.

## Budgets (R4)

`--encercler K` : au plus K angles par direction.
`--max-encercles M` : au plus M directions INCONNU par candidat
(échantillon stratifié par trou, même discipline que
`--valider-solidite`). Pas d'explosion combinatoire non guidée : chaque
angle est un build Lean borné par `--timeout`.

## Ce que l'encerclement n'est pas

- Pas une preuve : un dossier ENCERCLE dit « ce câblage précis est
  impossible, vérifié par Lean », pas « on sait prouver le sorry ».
- Pas un substitut au jugement : les RESISTANT et ATTEIGNABLE_TROUVE
  sont escaladés vers l'humain — l'instrument ordonne l'attention, il
  ne décide pas.

## Durcissements issus de l'application (2026-10-01)

L'application aux 5 sorrys (409 INCONNU sondés) a révélé et fait
corriger trois défauts d'instrument :

1. **CÂBLAGE_DIRECT en type brut** — le dépliage textuel capturait les
   lieurs (ex. `HasSchwartzDecay` capturant `u`), produisant des types
   mal scopés que Lean rejetait pour le scaffolding, pas le câblage.
   L'élaborateur Lean déplie lui-même avec l'hygiène correcte.
2. **Projections invalides** — `_couper_conjonction` découpait au `∧`
   sous les binders, générant `data.decay.1` sur une fonction (rejet
   Lean : « Invalid projection »). Un corps commençant par un binder
   n'est plus découpé. 22 dossiers RESISTANT documentent le bug et
   sa correction.
3. **Élagage et déduplication** — le registre n'était consulté que pour
   les CANDIDAT_IMPOSSIBLE (les INCONNU encerclés n'étaient pas
   élagués) ; l'échantillonnage re-sondait les directions avec dossier.
   Corrigés : le registre est consulté pour les deux zones, et les
   directions déjà encerclées sont exclues de l'échantillon.

## Limites connues

- **0 ATTEIGNABLE_TROUVE en pratique** : la capacité à détecter un vrai
  câblage caché dans l'INCONNU est testée (mock) mais pas encore
  démontrée sur le corpus réel.
- Les angles DEPLIAGE_PROFOND, COERCION, SOUS_TERMES n'ont jamais été
  décisifs en pratique sur les 5 sorrys — sondes de couverture, pas
  instruments validés.
