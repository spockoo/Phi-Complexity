# Discipline — Atteignabilité typée à trois zones

But de mission : isoler une unité de mesure 100 % fiable pour localiser les
directions où se trouve un sorry depuis une fonction (chantier approuvé par
Tomy le 2026-10-01).

## Plafond nommé

« La fonction f peut-elle décharger le sorry S ? » est indécidable en général.
Aucune unité *a priori* ne prédira le succès à 100 %. Le versant fiable est le
versant négatif : ce qui est prouvablement impossible, et ce que Lean tranche.

## Les trois zones

Pour un trou de type `T` du sorry et un terme candidat `t` (construit par
projections/dépliage depuis le contexte), le classifieur `_atteignabilite`
rend exactement une zone :

- **ATTEIGNABLE** : les types s'unifient après dépliage borné (profondeur ≤ 4)
  et permutation des lieurs. C'est le succès actuel de `_synthetiser_trou`.
  Ne garantit pas la preuve complète — seulement le remplissage du trou.
- **IMPOSSIBLE** : après dépliage complet *dans notre corpus de définitions*,
  les constructeurs de tête diffèrent et aucun des deux côtés n'a plus de
  définition dépliable. Prétention : Lean n'acceptera jamais ce câblage.
- **INCONNU** : la borne de profondeur est atteinte alors qu'il reste des
  définitions dépliables. Échec honnête — on ne prétend rien.

Règle d'or : INCONNU n'est jamais un IMPOSSIBLE déguisé. Le classifieur
préfère l'aveu d'ignorance au faux négatif.

## Solidité falsifiable (comment le 100 % est maintenu)

La prétention IMPOSSIBLE est une prédiction — donc réfutable. Protocole :

1. Pour chaque direction prédite IMPOSSIBLE `(T_trou, t_terme)`, générer le
   test minimal : `example <lieurs du sorry> : T_trou := t_terme`.
2. Soumettre à Lean. Attendu : RÉFUTÉ avec erreur de type.
3. Si Lean **accepte** (PROUVÉ) : violation de solidité → le classifieur est
   faux sur ce cas → chantier de durcissement immédiat (jamais d'abandon).
   Bonus : on a trouvé un câblage qui marche.

La solidité n'est pas déclarée une fois pour toutes : chaque campagne de
validation l'établit à nouveau, et chaque contre-exemple la fait progresser.
C'est le registre des paris (§8) appliqué à l'instrument lui-même.

## L'unité de mesure

Pour un sorry S : `directions_ouvertes(S) = ATTEIGNABLE + INCONNU`, compté
exactement sur l'ensemble des termes candidats. C'est une mesure exacte de
l'état du classifieur — 100 % fiable *en tant que mesure*. Quand le
classifieur durcit (INCONNU → IMPOSSIBLE validé), l'unité se resserre vers
l'ensemble réellement atteignable, sans jamais prétendre l'avoir atteint.

## Séquencement

1. Classifieur pur + tests (aucun changement de comportement du pipeline).
2. Annotation des directions dans `candidats_cablage` (mesure seulement).
3. Validation de solidité par Lean sur `energy_identity`.
4. Élagage (sauter la soumission Lean des IMPOSSIBLE) — **seulement après**
   solidité validée. Jamais avant.
5. Calibration empirique du registre des paris sur les campagnes.

## Anti-R1–R4

- Pas de constante absolue : la borne de profondeur est un paramètre, la
  mesure est relative au corpus réel.
- Pas d'explosion combinatoire : le classifieur est paresseux — il
  n'examine que les termes que la synthèse examine déjà.
- Leçon du 2026-10-01 : un instrument falsifiable qui ne tourne pas à
  l'échelle réelle est un instrument décoratif. Le dépliage naïf (un regex
  par nom de def et par niveau : 583 recherches par dépliage sur
  `energy_identity`) rendait la mesure inutilisable (>6 min pour un seul
  candidat). Durcissement : `_Deplieur` précompilé — un seul balayage
  combiné repère les noms présents, substitution séquentielle à sémantique
  identique (tests `test_temoin_*` + `test_deplieur_*`). Mesure complète
  d'`energy_identity` (3 candidats) : ~28 s.

## Commande de validation

```bash
cd ~/workspace/lean-navier-stokes
PYTHONPATH=~/workspace/phi-pub ~/workspace/venvs/phi/bin/python \
  -m phi_complexity.cli chemins-verifiables \
  --sorry energy_identity --max-candidats 1 --valider-solidite 8 \
  --timeout 600 --format json .
```

`--valider-solidite K` : échantillon stratifié (tourniquet sur les trous)
de K directions prédites IMPOSSIBLE par candidat, chacune soumise à Lean
via `fichier_validation_solidite`. Bilan : CONFIRMÉ / VIOLATION /
INCONCLUSIF (`interpreter_solidite` : PROUVÉ → VIOLATION ; RÉFUTÉ avec
type mismatch → CONFIRMÉ ; le reste → INCONCLUSIF, jamais une violation
déguisée ni une confirmation usurpée).
