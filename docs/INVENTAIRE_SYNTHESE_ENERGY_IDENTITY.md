# Synthèse contrôlée de structures — inventaire P/S/H pour `energy_identity`

## Contexte du sorry (Master, `Clay_NS_Master.lean:387`)

```
energy_identity (ν : ℝ) (hν : 0 < ν)
    (sol : ClassicalSolution ν)
    (hdec : ∀ t ∈ Set.Ico (0:ℝ) sol.horizon, HasSchwartzDecay (fun x => sol.u x t))
    (hint : ...) :
    ∀ t ∈ Set.Ico (0:ℝ) sol.horizon,
      kineticEnergy sol.u t
        + ∫ s in (0:ℝ)..t, dissipationRate sol.u ν s ∂volume
        = kineticEnergy sol.u 0
```

`ClassicalSolution ν` (Master:202) : `u`, `p`, `horizon`, `h_horizon : 0 < horizon`,
`momentum : SatisfiesMomentum u p ν`, `incompressibility : SatisfiesIncompressibility u`,
`regularity : HasClassicalRegularity u p horizon` (C¹ temps, C² espace, C¹ pression).

## Candidat : `relativized_energy_discharge` (Part02e, score 3.14)

Conclusion de la bonne forme (égalité intégrée ; la version `_framed` 3.95
conclut une inégalité `≤ 3 * ...` — mauvaise forme, écartée malgré son score).

| Trou | Type requis | Classe | Obtention |
|---|---|---|---|
| `u` | `VelField` | S1 | `sol.u` |
| `p` | `PresField` | S1 | `sol.p` |
| `T` | `ℝ` | S1 | `sol.horizon` |
| `hT` | `0 < T` | S1 | `sol.h_horizon` |
| `hmom` | `SatisfiesMomentum u p ν` | **P** | `sol.momentum` |
| `hinc` | `SatisfiesIncompressibility u` | **P** | `sol.incompressibility` |
| `hreg_t` | diff. en temps | S2 | `sol.regularity.1` (dépliage du `∧`) |
| `hreg_s1` | C¹ espace | S2 | `sol.regularity.2.1` |
| `hreg_s2` | C² espace | S2 | `sol.regularity.2.2.1` |
| `hreg3` | `ThirdOrderSpaceRegularity u T` (C³ !) | **H'** | NON dérivable de `HasClassicalRegularity` (C²) |
| `hreg_p1` | C¹ pression | S2 | `sol.regularity.2.2.2` |
| `hpreg2` | `PressureSecondOrderRegularity p T` (C² pression !) | **H'** | NON dérivable (C¹ → C²) ; nommé [R2] dans Part02b |
| `hG2` | `SchwartzPropagationHypothesis` | **H** | hypothèse nommée, absente du contexte |
| `hG3` | `PressureDecayHypothesis` | **H** | hypothèse nommée, absente du contexte |
| `hAnalytic` | `LocalEnergyAnalyticData u p ν T` | **H** | hypothèse indépendante du théorème, absente du contexte (correction 2026-10-01 : classé S4 par erreur — `localData_of_G2G3` construit `LocalEnergyData`, pas `LocalEnergyAnalyticData`) |
| pont concl. | `kineticEnergy`/`dissipationRate` vs formes de Frobenius | S5 | dépliage définitionnel |

## Classes

- **P** : projection directe d'un champ (déjà à portée du Fragment D si on l'y autorise).
- **S1** : terme du contexte (variable ou projection simple).
- **S2** : projection + dépliage d'une conjonction (`sol.regularity.1`, `.2.1`, …).
- **S4** : builder multi-étapes (réservé aux vrais builders prouvés ; `LocalEnergyAnalyticData`
  n'en est pas un — reclassé H le 2026-10-01).
- **S5** : pont définitionnel sur la conclusion (`kineticEnergy` = `½∫∑(u^i)²`).
- **H** : hypothèse nommée absente → résidu honnête, jamais synthétisée.
- **H'** : régularité supérieure non fournie par le Master (C³ espace, C² pression ;
  le Master exige C²/C¹ per Fefferman) → **vrai trou mathématique**, pas du câblage.
  Le bootstrap parabolique [G2] est la route nommée, non construite ici.

## Conséquence honnête anticipée

Même avec une plomberie parfaite (P+S1+S2+S5), Lean nommera le résidu :
cinq arguments manquants — `ThirdOrderSpaceRegularity` (H'), `PressureSecondOrderRegularity` (H'),
`SchwartzPropagationHypothesis` (H), `PressureDecayHypothesis` (H), `LocalEnergyAnalyticData` (H) —
plus l'éventuel pont de conclusion (S5). C'est la caractérisation machine-vérifiée de ce qui
sépare le Master de l'identité d'énergie — le diagnostic est le produit.

## Vérification Lean (2026-10-01)

Trois candidats soumis à Lean (`--verifier --max-candidats 3`), tous RÉFUTÉ :

- **Passe 1** : Lean nomme 2 défauts d'instrument — projections plates `.2`/`.3`/`.4`
  au lieu de `.2.1`/`.2.2.1`/`.2.2.2`, et `open` non rejoués (B3 manquant sur le
  chemin mono-lemme). Corrigés, tests de non-régression verrouillés.
- **Passe 2** : les squelettes passent la plomberie ; Lean nomme le vrai résidu —
  `Type mismatch` sur la conclusion : la forme Frobenius du lemme
  (`½∫∑(u^i)²`, `∫∑∑(∂_j u^i)²`) vs `kineticEnergy`/`dissipationRate` du Master.
  C'est le pont de conclusion (S5), non construit ici.

Verdict : `~/workspace/verdicts_chemins_verifiables/verdict_energy_identity_synthese_20261001.json`.
