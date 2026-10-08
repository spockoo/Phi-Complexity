# TOOLCHAIN Lean Mini — Spécification

**Date :** 2026-10-08
**Version Lean :** 4.34.0 (x86_64-unknown-linux-gnu)
**Statut :** prototype validé, mesures réelles

---

## Résumé

| | Complet | Mini | Ratio |
|---|---|---|---|
| Téléchargement | 580 Mo | **125 Mo** | **4,6×** |
| Extrait | 2,9 Go | **522 Mo** | **5,6×** |
| Fichiers | 17 738 | **1 956** | **9,1×** |

---

## 1. Binaires indispensables

| Binaire | Taille | Rôle | Requis ? |
|---|---|---|---|
| `lean` | 9 Ko (+ strip) | Élaborateur / vérificateur | ✅ OUI |
| `leanc` | 37 Ko | Driver C (wrapper clang) | ⚠️ Seulement si exécutables natifs |
| `clang` | 119 Ko | Compilateur C | ⚠️ Seulement si exécutables natifs |
| `ld.lld` | 6 Mo | Linker | ⚠️ Seulement si exécutables natifs |
| `llvm-ar` | 77 Ko | Archiveur | ❌ NON |
| `lake` | 14 Ko | Build system | ❌ NON (pour usage direct) |
| `leanchecker` | 77 Ko | Vérificateur externe | ❌ NON |
| `leanir` | 220 Ko | IR tools | ❌ NON |
| `leantar` | 2,7 Mo | Archiveur | ❌ NON |
| `leanmake` | 839 o | Make wrapper | ❌ NON |
| `cadical` | 1,1 Mo | Solveur SAT | ❌ NON |

**Réponse Q1 :** seul `lean` est indispensable pour élaborer/vérifier/`--run`.
`leanc`+`clang`+`ld.lld` seulement pour produire des exécutables natifs.

---

## 2. Bibliothèques partagées (ldd)

### Pour `lean` (obligatoires)
```
lib/lean/libInit_shared.so      (6 Ko — stub)
lib/lean/libleanshared.so       (164 Mo après strip, était 223 Mo)
lib/lean/libleanshared_1.so     (6 Ko — stub)
lib/lean/libleanshared_2.so     (6 Ko — stub)
```
+ système : libc, libpthread, libdl, libm, librt (fournis par l'OS)

### Pour `leanc`/`clang` (seulement si exécutables natifs)
```
lib/libclang-cpp.so.22.1        (65 Mo)
lib/libLLVM.so.22.1             (77 Mo)
lib/libc++.so.1.0               (1,3 Mo)
lib/libc++abi.so.1.0            (408 Ko)
lib/libunwind.so.1.0            (70 Ko)
```

### Pour le linkage natif (seulement si exécutables natifs)
```
lib/Scrt1.o, lib/crt1.o, lib/crti.o, lib/crtn.o, lib/Mcrt1.o, lib/gcrt1.o
lib/libgmp.a                    (GMP — grands nombres)
lib/libuv.a                     (libuv — IO)
include/lean/lean.h             (headers C, 364 Ko)
```

**Réponse Q2 :** 4 .so pour `lean`. 5 .so supplémentaires pour clang.

---

## 3. Fichiers `lib/` et `share/` requis au runtime

### `lib/lean/` — bibliothèque standard compilée

Pour compiler n'importe quel fichier `.lean` standard, **tout le répertoire `Init/`** est chargé transitivement (649 modules) :

| Variante | Taille | Requise ? | Pourquoi |
|---|---|---|---|
| `.olean` | 97 Mo | ✅ OUI | Termes élaborés |
| `.olean.private` | 249 Mo | ✅ OUI | Définitions privées (lean l'exige) |
| `.olean.server` | 13 Mo | ✅ OUI | Métadonnées serveur (lean l'exige) |
| `.ir` | 19 Mo | ❌ NON | IR intermédiaire (non lu au runtime) |
| `.ir.sig` | 2,6 Mo | ❌ NON | Signatures IR (non lues) |

**Éliminés :** `Lean/` (1,3 Go — le compilateur lui-même en Lean), `Std/` (348 Mo), `Lake/` (108 Mo).
Ces répertoires ne sont chargés que si le code les importe explicitement.

### `share/`
```
share/lean/lean.mk              (8 Ko — makefile helper)
```
Non requis pour `lean` direct. Utile seulement avec `leanmake`.

### `src/` (34 Mo)
Sources `.lean` du compilateur. **Non requis** au runtime.

**Réponse Q3 :** `Init/*.olean` + `.olean.private` + `.olean.server` uniquement.

---

## 4. Validation : Hello World

```lean
def main : IO Unit := IO.println "Hello, world!"
```

```
$ LEAN_PATH=mini/lib/lean mini/bin/lean --run hello.lean
Hello, world!   ✅
```

Test avancé (factorielle, List.range, map, interpolation) :
```
facts: [1, 1, 2, 6, 24, 120, 720, 5040, 40320, 362880]
somme: 409114   ✅
```

**Réponse Q4 :** OUI — le minimal compile et exécute (`--run`) correctement.

---

## 5. Taille finale

```
Complet téléchargé :  580 Mo  (lean-4.34.0-linux.tar.zst)
Complet extrait    : 2,9 Go  (17 738 fichiers)

Mini extrait       :  522 Mo  (1 956 fichiers)
Mini compressé     :  125 Mo  (tar.zst niveau 19)
```

**Réponse Q5 :** 125 Mo vs 580 Mo = **4,6× plus petit** au téléchargement.

---

## 6. Structure du mini

```
lean-mini/
├── bin/
│   └── lean                    # 9 Ko (strippé)
└── lib/
    └── lean/
        ├── libInit_shared.so   # 6 Ko
        ├── libleanshared.so    # 164 Mo (strippé, était 223 Mo)
        ├── libleanshared_1.so  # 6 Ko
        ├── libleanshared_2.so  # 6 Ko
        └── Init/               # 649 modules × 3 variantes
            ├── *.olean         # 97 Mo
            ├── *.olean.private # 249 Mo
            └── *.olean.server  # 13 Mo
```

**Total : 1 956 fichiers, 522 Mo.**

### Pour exécutables natifs (optionnel, +224 Mo)
```
├── bin/
│   ├── leanc, clang, ld.lld
├── lib/
│   ├── libclang-cpp.so.22.1    # 65 Mo
│   ├── libLLVM.so.22.1         # 77 Mo
│   ├── libc++.so.1.0, libc++abi.so.1.0, libunwind.so.1.0
│   ├── Scrt1.o, crt1.o, crti.o, crtn.o, Mcrt1.o, gcrt1.o
│   ├── libgmp.a, libuv.a
└── include/
    └── lean/                   # headers C
```

---

## 7. Limites connues

1. **Pas de `lake`** — le mini ne fait pas de gestion de projet. Usage direct `lean` uniquement.
2. **Pas de `Std`/`Lean`** — si le code fait `import Std` ou `import Lean`, il faut ajouter ces répertoires (1,6 Go supplémentaires).
3. **Pas d'exécutables natifs** par défaut — `--run` (interpréteur) fonctionne, mais pas de binaires standalone sans le tier C (+224 Mo).
4. **Linux x86_64 uniquement** — autres plateformes non testées.
5. **Strip** : les symboles de debug sont retirés. En cas de crash natif, pas de backtrace lisible.

---

## 8. Recommandation pour phi-complexity

**Protocole de téléchargement à la demande :**
1. Vérifier `~/.cache/phi-complexity/toolchains/lean-mini-4.34.0/`
2. Si absent : télécharger `lean-mini-4.34.0-linux.tar.zst` (125 Mo)
3. Vérifier SHA256 (à mesurer et épingler)
4. Extraire, valider `bin/lean --version`
5. Utiliser avec `LEAN_PATH=<cache>/lib/lean`

**Variable d'environnement :** `PHI_LEAN_MINI_BASE` pour miroir configurable (défaut : GitHub releases phi-complexity).

---

## Fichiers de test

- `hello.lean` — Hello World
- `test2.lean` — factorielle + List + interpolation (validation avancée)
- `trace_hello.log` — trace strace complète (3245 accès fichiers)
- `accessed_files.txt` — liste des 3245 fichiers accédés

---

*Toutes les mesures sont réelles, effectuées le 2026-10-08 sur cette VM.*
*Le prototype `mini3/` compile et exécute correctement.*
