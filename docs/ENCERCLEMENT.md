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

## Les trois angles

Chaque angle est une *proposition* de câblage soumise à Lean via un
fichier de test minimal (même en-tête B8–B11 que la validation de
solidité : union des télescopes, imports, opens, univers). L'angle ne
prétend rien ; **seul Lean tranche** :

1. **DEPLIAGE_PROFOND** — déplie les définitions au-delà de la borne du
   classifieur (`_PROF_DEPLIAGE`). Le dépliage étant une égalité
   définitionnelle, un verdict Lean sur la forme dépliée vaut pour la
   forme originale.
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

- **ENCERCLE** — ≥1 angle décisif (RÉFUTÉ par mismatch). Lean a tranché :
  la direction est inscrite au registre des impossibilités (fiche :
  angle décisif nommé). C'est Lean qui promeut, pas une analyse
  statique — même discipline que `--valider-solidite`.
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
