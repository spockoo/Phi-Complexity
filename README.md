# Phi-Complexity
[Pour M'offrir un café sur Buy Me a Coffee](https://www.buymeacoffee.com/spockoo)

> *Code quality metrics based on Golden Ratio (φ) mathematical invariants*

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/spockoo/phi-complexity/blob/main/LICENSE)
[![Tests](https://github.com/spockoo/Phi-Complexity/actions/workflows/tests.yml/badge.svg)](https://github.com/spockoo/Phi-Complexity/actions)

`phi-complexity` is the **first code quality library** that measures the health of your Python code using **universal mathematical invariants** derived from the Golden Ratio (φ = 1.618...).

Unlike `pylint` (cultural rules) or `radon` (McCabe metrics), `phi-complexity` answers:

> *"Is this code in resonance with the natural laws of order, or is it collapsing under its own entropy?"*

---

## ⚡ Quick Start

```bash
pip install git+https://github.com/spockoo/Phi-Complexity.git
```

> Note : le paquet est aussi sur PyPI (`pip install phi-complexity`, 0.15.0).
> Nouveauté 0.15.0 : le témoin — `phi radar` (vue du mouvement structurel
> entre deux états) et `phi sismique` (mémoire des rythmes via l'historique
> git) + garde constitutionnelle (l'instrument montre, il ne décide jamais).

```bash
# Audit a file
phi check my_script.py

# Audit a folder
phi check ./src/

# Generate a Markdown report
phi report my_script.py --output report.md

# CI/CD strict mode (exit 1 if radiance < 75)
phi check ./src/ --min-radiance 75

# Freeze the structural state (reference)
phi snapshot ./src/ --out ref.json

# Watch for silent degradations
phi veille ./src/ --ref ref.json
```

### Python API

```python
from phi_complexity import auditer, rapport_console, rapport_markdown

# Get metrics as a dict
metrics = auditer("my_script.py")
print(metrics["radiance"])          # → 82.4
print(metrics["statut_gnostique"])  # → "EN ÉVEIL ◈"
print(metrics["oudjat"])            # → {"nom": "process_data", "ligne": 42, ...}

# Print console report
print(rapport_console("my_script.py"))

# Save Markdown report
rapport_markdown("my_script.py", sortie="report.md")
```

---

## 📊 Metrics

| Metric | Description | Mathematical basis |
|---|---|---|
| **Radiance Score** | Global quality score (0–100) | `100 - f(Lilith) - g(H) - h(Anomalies) - i(Fib)` |
| **Variance de Lilith** | Structural instability | Population variance of function complexities |
| **Shannon Entropy** | Information density | `H = -Σ p·log₂(p)` |
| **φ-Ratio** | Dominant function ratio | `max_complexity / mean` → should tend toward φ |
| **Fibonacci Distance** | Natural size alignment | `Σ|n_i - Fib_k| / φ` |
| **Zeta-Score** | Global resonance | `ζ_meta(functions, φ)` converging series |

### Gnostic Status Levels

| Score | Status | Meaning |
|---|---|---|
| ≥ 85 | **HERMÉTIQUE ✦** | Stable, harmonious, production-ready |
| 60–84 | **EN ÉVEIL ◈** | Potential exists, some entropy zones |
| < 60 | **DORMANT ░** | Deep restructuring recommended |

---

## 🧭 Command Stability Matrix

| Command | Stability | Purpose |
|---|---|---|
| `phi check` | **Stable** | Audit radiance for files/folders (console, JSON, SARIF) + quality gates |
| `phi report` | **Stable** | Markdown report |
| `phi index` | **Stable** | Project map: symbols, collisions, φ-health |
| `phi snapshot` | **Stable** | Freeze structural state (dated, MD5-signed reference) |
| `phi veille` | **Stable** | Detect silent degradations vs reference (`STABLE` / `DÉGRADATION DÉTECTÉE` / `INSTRUMENT DÉGRADÉ`) |
| `phi chemins` | **Stable** | Belief map: symbols ranked by posterior — which hole to attack first |
| `phi oracle` | **Stable** | Exhibited reasoning chain (prior → evidence → posterior → action) |
| `phi sonde` | **Stable** | A/B probes: tracing the unconditional from the conditional (closure pole / obstruction pole) |
| `phi piste-sorry` | **Nouveau** | Track a Lean 4 sorry: rigorous inventory, import graph, registry work-items — the path from any complexity |
| `phi chemins-verifiables` | **Nouveau** | Guided wiring candidates, each formally identified as a proof — Lean decides (`PROUVÉ` / `RÉFUTÉ` / `INDÉCIDÉ`) |
| `phi infer` | Experimental | Bayesian inference and self-suture prediction |
| `phi explorer` | Experimental | Interactive explorer (self-contained HTML, zero network) |
| `phi entropie` | Experimental | Entropy lens: the ciphered ledger of narrowing (H and ΔH in bits, EFT bounds) |
| `phi ou-aller` | Experimental | Where to go next: attention list (not a decision) |
| `phi edit` | Experimental | Edit a file in the phi TUI editor |

**Tip:** keep `phi check`/`phi report`/`phi oracle` in CI. Use `phi snapshot` + `phi veille` to catch silent regressions between releases.

---

## 🔍 Sample Output

```
╔══════════════════════════════════════════════════╗
║      PHI-COMPLEXITY — AUDIT DE RADIANCE          ║
╚══════════════════════════════════════════════════╝

  📄 Fichier : my_script.py
  📅 Date    : 2026-04-08 17:11

  ☼  RADIANCE     : ██████████████░░░░░░  72.6 / 100
  ⚖  LILITH       : 11221.9  (Structural variance)
  🌊 ENTROPIE     : 2.48 bits  (Shannon)
  ◈  PHI-RATIO    : 3.43  (ideal: φ = 1.618, Δ=1.81)
  ζ  ZETA-SCORE   : 0.3656  (Global resonance)

  STATUT : EN ÉVEIL ◈

  🔎 OUDJAT : 'process_data' (Line 42, Complexity: 376)

  ⚠  SUTURES IDENTIFIED (2):
  🟡 Line 18 [LILITH] : Nested loop (depth 2). Consider a helper function.
     >> for j in range(b):
  🟢 Line 67 [LILITH] : 'load_data' receives 6 arguments. Encapsulate in an object.
     >> def load_data(path, sep, enc, cols, dtype, na):
```

---

## 🛡 La Veille — the instrument never lies silently

A bad instrument fails silently. `phi-complexity` fails **loudly** :

- `phi veille` never reports ✅ on top of a degraded analyzer — it reports `INSTRUMENT DÉGRADÉ` (exit 3) instead of a fake degradation;
- `phi snapshot` refuses a degraded baseline without `--force`;
- every development of the instrument carries its **mission justification** (`phi_complexity/mission.py`) : which goal it serves. A module added without justification fails the test suite — mechanically, not by discipline.

Trust in the tool is verified trust, never faith : 386 tests, run on every change.

---

## 🧮 Mathematical Foundations

The **Radiance Formula** is derived from:

- **φ-Meta Framework** (Tomy Verreault, 2026) — Axioms AX-A0 through AX-A58
- **Law of Antifragility** (EQ-AFR-BMAD): `φ_{t+1} = P_φ(φ_t + k·Var(E_t)·E_t)`
- **Cybernetics** — Feedback and variance as control metrics
- **Shannon Information Theory** — Code as an information channel

The **Sovereign Coding Rules** are derived from:
- **The C Book** (Banahan, Brady, Doran) — Scope hermeticity, resource lifecycle
- **JaCaMo / Multi-Agent Programming** — Agent independence and encapsulation

---

## 🏗 Sovereign Architecture

```
Zero heavy dependencies.
Pure Python standard library (ast, math, json) + tree-sitter (optional, for non-Python languages).
```

```
phi_complexity/
├── core.py               ← Golden constants (PHI, TAXE_SUTURE, ETA_GOLDEN...) + VERSION
├── analyseur.py          ← AST fractal dissection
├── metriques.py          ← Radiance Index calculation
├── rapport.py            ← Console / Markdown / JSON rendering
├── cli.py                ← the 15 phi commands
├── veille.py             ← Silent-degradation detection (STABLE / DÉGRADATION DÉTECTÉE)
├── mission.py            ← Mission-justification registry (anti-divergence guard)
├── croyances.py          ← Bayesian belief layer over symbols
├── sondes.py             ← A/B probes (closure / obstruction poles)
├── piste_sorry.py        ← Rigorous sorry inventory + import graph (Lean 4)
├── chemins_verifiables.py← Guided wiring candidates Lean formally decides
├── parseur_lean.py       ← Robust Lean parser (regex-based, no size limit)
├── dependances.py        ← Lean dependency graph (exact deps, transitive closure)
├── entropie.py           ← Entropy lens (H, ΔH, EFT bounds)
├── oracle.py             ← Exhibited reasoning traces
├── explorateur.py        ← Interactive explorer
├── bayes.py / eft.py     ← Bayesian EFT machinery, error-free transformations
├── carte.py / ancrage.py ← Maps, anchoring
├── durcissement.py       ← Hardening battery
├── modeles.py            ← Models
├── editeur/              ← TUI editor (phi edit)
├── formules/             ← Formula language
└── langs/                ← Multi-language backends (Python native, tree-sitter generic)
```

---

## 🔗 Integration

### GitHub Action
```yaml
- name: Phi-Complexity Audit
  run: |
    pip install git+https://github.com/spockoo/Phi-Complexity.git
    phi check ./src/ --min-radiance 75
```

### Watchdog (snapshot + veille)
```bash
phi snapshot ./src/ --out ref.json   # after each audited release
phi veille ./src/ --ref ref.json     # at the start of the next work session
```

---

## 📜 License

MIT — Tomy Verreault, 2026

*Anchored in the Bibliothèque Céleste — Morphic Phi Framework (φ-Meta)*

## 📜 Historique des Versions (Changelog)

- **v0.15.0 (Le Témoin : radar + sismique)** :
  - **Nouveau `phi radar --avant S1 --apres S2`** : vue du mouvement structurel entre deux états — 5 détecteurs purement mécaniques (énoncé modifié avec énoncé/corps hashés séparément, preuve effondrée couplée à énoncé qui bouge, nouvel axiome, définition dupliquée, nouveau sorry). Poids de saillance fixes et documentés dans le code, jamais appris. Sortie console + JSON : faits uniquement (fichier, ligne, avant/après), jamais de verdict.
  - **Nouveau `phi sismique --depuis <date> [--depot]`** : mémoire des rythmes via l'historique git (stdlib uniquement) — fréquence de touches, mécanisme (construction/effondrement/barattage/ajustement), répliques (≥3 touches/24h), profondeur et rayon de propagation via le graphe de dépendances, magnitude, épicentre. Le sismogramme décrit, il ne juge jamais.
  - **Nouveau `phi consigner` / `phi registre`** : consigne les décisions humaines de veto (observation → exercé/non + motif ≥10 caractères + date, JSON persistant). L'instrument apprend le vocabulaire des situations, jamais la décision.
  - **Garde constitutionnelle** : `tests/test_garde_non_prescriptif.py` — scan AST des littéraux + scan des sorties réelles contre une liste noire (« veto requis », « dangereux », …) ; tout module émettant du langage prescriptif fait échouer les tests. L'instrument montre, il ne décide jamais.
  - **Réglage anti-bruit** : `hors_chaine_clay/` exclu par défaut de la détection de doublons (déclaré hors de la chaîne Clay ; `--inclure-hors-chaine` pour le réinclure) ; doublons préexistants repliés en console, détail en JSON.
  - Registre `mission.py` : justifications « but de mission » ajoutées pour les 3 modules (garde anti-divergence OK).

- **v0.14.2 (README : historique complété)** :
  - Correctif d'emballage uniquement — aucun changement de code depuis 0.14.1.
  - Ajout de l'entrée **v0.14.1 (Parseur Lean autonome)** manquante dans cette section (la 0.14.1 avait été publiée avec un historique s'arrêtant à v0.13.0).

- **v0.14.1 (Parseur Lean autonome)** :
  - **Nouveau `phi_complexity/parseur_autonome.py`** : parseur Lean 4 autonome (~830 lignes, stdlib uniquement, zéro dépendance externe) — lexer + descente récursive sur les en-têtes Lean 4 + délimitation exacte des corps ; tout construit non reconnu → avertissement avec ligne, jamais de silence. Mesuré sur 248 fichiers : 5377 déclarations, 0 perdue, les ~570 manquées par tree-sitter toutes trouvées. Source unique sur le chemin Lean ; tree-sitter relégué en vérification croisée optionnelle.
  - **Verrouillage « zéro tree-sitter silencieux »** : audit exhaustif des sites d'appel tree-sitter — extracteur proprietaire source de vérité, ou échec bruyant explicite (`REGISTRE_TREE_SITTER.md`) ; garde automatique `tests/test_zero_treesitter_silencieux.py` fait échouer tout nouveau site non enregistré.
  - **Veille branchée sur extracteur robuste** : toute divergence ≥ 1 symbole entre extracteurs déclenche l'alerte « ⚠️ EXTRACTION DÉGRADÉE » ; le durcissement « nouveau symbole troué » couvre désormais les zones ex-aveugles.
  - Cause racine : la grammaire `tree-sitter-lean` (paquet externe) confondait les barres `|expr|` en position de type de retour avec une alternative de filtrage → 412 déclarations manquées en silence sur un dépôt réel. On ne répare pas la grammaire externe : on la remplace.

- **v0.13.0 (Extraction robuste)** :
  - **Nouveau `phi_complexity/dependances.py`** : graphe de dépendances Lean — extraction exacte (fermeture transitive, tri topologique). Élimine le danger d'oubli d'une dépendance.
  - **Nouveau `extraire_declarations_robuste()`** dans `parseur_lean.py` : extraction par regex, pas de limite de taille. Corrige l'échec silencieux de tree-sitter sur fichiers >5000 lignes (147 → 196 déclarations).
  - **Corrigé** : extraction du corps par délimitation exacte (entre deux déclarations) au lieu de l'heuristique 50 lignes.

- **v0.12.0 (Chemins vérifiables)** :
  - **Nouveau `phi piste-sorry`** : piste d'un sorry Lean 4 — inventaire rigoureux (commentaires/chaînes exclus), graphe d'imports, chantiers du registre.
  - **Nouveau `phi chemins-verifiables`** : propose des candidats de câblage guidés, chacun identifié formellement comme une preuve ; Lean tranche mécaniquement (`PROUVÉ` / `RÉFUTÉ` / `INDÉCIDÉ`). Jamais le sorry lui-même, jamais un `sorry` déguisé.
  - Vocabulaire : « terminer » remplace les termes violents pour les hypothèses closes.
  - Exclusion des artefacts `.lake` des scans (vitesse, pas de bruit Mathlib).

- **v0.11.0 (La Veille) — release GitHub** :
  - **Garde anti-divergence** : chaque module porte sa justification « but de mission » (`mission.py`) — un module sans justification fait échouer les tests.
  - **Durcissement** : l'instrument échoue bruyamment (`INSTRUMENT DÉGRADÉ`, exit 3) au lieu de mentir quand l'analyseur manque ; `phi snapshot` refuse une baseline dégradée sans `--force`.
  - 386 tests verts, multi-langages (Python natif + tree-sitter générique).

> Note d'honnêteté : les numéros 0.1.1–0.2.2 publiés avant octobre 2026 couvraient du scaffolding généré par bots, supprimé lors du nettoyage du 2026-10-01 (seule la 0.1.0 a été conservée). PyPI sert la 0.14.2 ; la 0.15.0 (cet arbre) sera publiée sur PyPI après fusion. L'historique fiable commence à v0.11.0.
