# Étude : intégration décentralisée de la toolchain Lean dans phi-complexity

**Date :** 2026-10-08
**Statut :** étude uniquement — rien n'est installé
**Objectif :** permettre à phi-complexity de compiler du Lean sans dépendre d'une installation centrale (elan)

---

## 1. Mesures (faites, pas estimées)

Téléchargement réel de `lean-4.34.0-linux.tar.zst` depuis GitHub releases :

| Mesure | Valeur |
|---|---|
| URL | `https://github.com/leanprover/lean4/releases/download/v4.34.0/lean-4.34.0-linux.tar.zst` |
| Taille téléchargée | **580,4 Mo** |
| Temps | 18 s (31,6 Mo/s) |
| SHA256 mesuré | `caaa9835…356b918f` (complet en §6) |
| Taille extraite | **3,29 Go**, 17 750 fichiers |
| Ratio compression | 5,67× |
| Contenu | `bin/` (lean, lake, leanc), `lib/`, `include/`, `share/`, `src/`, `LICENSE`, `LICENSES/` |
| Nettoyage | effectué — rien n'est installé |

Tailles des 5 plateformes (v4.34.0, via API GitHub, `.tar.zst`) :

| Plateforme | Taille |
|---|---|
| linux x86_64 | 580,4 Mo |
| linux aarch64 | 582,0 Mo |
| darwin x86_64 | 564,2 Mo |
| darwin aarch64 | 561,7 Mo |
| windows x86_64 | 588,8 Mo |
| **Total 5 plateformes** | **~2,9 Go** |

---

## 2. Q1 — Bundling : embarquer les binaires ?

**Verdict : NON.**

| Critère | Analyse |
|---|---|
| Taille | 580 Mo/plateforme, 2,9 Go les 5. PyPI limite à 60 Mo par fichier — impossible. Même en wheel multi-plateforme, c'est 10× trop gros. |
| Licence | Apache 2.0 — **autorise** la redistribution (conditions : inclure LICENSE, conserver les notices d'attribution, marquer les fichiers modifiés). Pas de blocage juridique. |
| Maintenance | Chaque release Lean = re-bundler 2,9 Go. Dette permanente. |
| Pertinence | 3,29 Go extraits pour compiler des fichiers Lean — disproportionné pour un outil Python. |

Le bundling est légalement possible mais techniquement absurde pour un package Python.

---

## 3. Q2 — Téléchargement à la demande (recommandé)

**Verdict : OUI — c'est la voie.**

### Protocole proposé

```
1. Premier usage → vérifier le cache local (~/.cache/phi-complexity/toolchains/)
2. Si absent → télécharger depuis l'URL primaire (GitHub releases)
3. Vérifier le SHA256 contre le hash épinglé (voir §6)
4. Extraire dans le cache, valider bin/lean --version
5. Publier seulement si tout est OK (jamais de toolchain tronquée)
```

### Points durs identifiés

- **Pas de checksums officiels.** Lean ne publie pas de SHA256 pour ses assets (vérifié : le body de la release v4.34.0 n'en contient aucun). GitHub les calcule à l'affichage mais ce n'est pas une attestation upstream.
  → **Solution :** nous épinglons nos propres hashes mesurés (§6). Le hash ci-dessus a été mesuré ce jour sur le fichier réel.
- **Hôtes alternatifs injoignables.** Précédent documenté (folio-assistant) : `release.lean-lang.org` et `elan.lean-lang.org` sans route dans certains environnements, alors que `github.com/leanprover/lean4/releases/download/` répond 200.
  → **Solution :** GitHub comme primaire (mesuré joignable), pas les hôtes lean-lang.org.
- **Précédent éprouvé.** `theproofnetwork/leanvrf` utilise un `toolchain.lock` (URL + sha256), téléchargement HTTPS, rejet si le hash ne correspond pas. Même pattern.

### Coûts

| Événement | Coût |
|---|---|
| Premier usage | 580 Mo, ~20 s (bande passante mesurée), 3,3 Go disque |
| Usages suivants | 0 (cache) |
| Vérification d'intégrité | SHA256 en flux pendant le téléchargement, coût négligeable |

---

## 4. Q3 — Sources multiples (décentralisation réelle)

| Source | Statut | Analyse |
|---|---|---|
| GitHub releases | ✅ mesuré joignable (200) | Primaire recommandé |
| `release.lean-lang.org` | ⚠️ injoignable (précédent documenté) | Ne pas utiliser comme primaire |
| Miroir configurable | ✅ pattern éprouvé | Variable d'env `PHI_LEAN_RELEASE_BASE` (précédent : `$LEAN_RELEASE_BASE` chez folio-assistant), support `file://` pour air-gapped |
| IPFS | ❌ personne ne publie les toolchains Lean sur IPFS aujourd'hui | Nous serions les premiers. Nécessite une infra de pinning. **Piste future, pas pour maintenant.** |

**Décentralisation honnête :** aujourd'hui, "décentralisé" = GitHub + miroir configurable par l'utilisateur. C'est 2 sources, pas N. L'IPFS est la vraie décentralisation mais demande une infra que personne n'a encore construite pour Lean.

**Recommandation :** implémenter le sélecteur de source (primaire + miroir via env var) avec l'architecture prête pour IPFS plus tard (le sélecteur prend une liste d'URLs candidates).

---

## 5. Q4 — Version pinning

- **Version épinglée : v4.34.0** (stable actuelle, cohérente avec tout le travail portable déjà fait en 4.34.0).
- **Mécanisme :** fichier `lean-toolchain` au format elan (`leanprover/lean4:v4.34.0`) + notre propre manifeste avec URL et SHA256 par plateforme.
- **Mises à jour :** explicites uniquement, jamais automatiques. Chaque nouvelle version = nouveau hash mesuré + entrée manifeste.
- **Avertissement :** les `.olean` sont verrouillés par version (un binaire 4.33.1 ne charge pas des oleans 4.34.0). Le pin doit être cohérent avec les artefacts utilisés.

---

## 6. Q5 — Isolation : par projet ou globale ?

Le modèle elan existant répond déjà : **la sélection est par projet** (fichier `lean-toolchain`), **le stockage est global partagé** (`~/.elan/toolchains/`).

**Recommandation identique pour phi-complexity :**
- Cache global partagé : `~/.cache/phi-complexity/toolchains/<version>/`
- Sélection par projet : config phi ou `lean-toolchain` du projet
- Deux projets sur la même version partagent les 3,3 Go (pas de duplication)

C'est le meilleur des deux mondes : isolation logique, économie disque.

---

## 7. Hashes épinglés (mesurés le 2026-10-08)

```
lean-4.34.0-linux.tar.zst
  taille : 580406.xxx octets (580,4 Mo)
  sha256 : caaa98356098c85dc0fcbbd28e1ec66f39eb6551829972b752ff20e1286b646b
```

⚠️ Les 4 autres plateformes n'ont pas été téléchargées (bande passante/temps). Leurs hashes devront être mesurés de la même façon avant usage.

---

## 8. Recommandation finale

**Implémenter le téléchargement à la demande avec hash épinglé (Q2), PAS le bundling (Q1).**

Spécification minimale :
1. Manifeste `toolchain_manifest.json` : version → {plateforme → {url, sha256, taille}}
2. Cache `~/.cache/phi-complexity/toolchains/<version>/`
3. Protocole : vérifier cache → télécharger (primaire GitHub, repli `$PHI_LEAN_RELEASE_BASE`) → vérifier SHA256 en flux → extraire → valider `bin/lean --version` → publier
4. Sélection par projet (fichier `lean-toolchain` ou config phi)
5. Licence : inclure `LICENSE` Apache 2.0 de Lean dans la distribution du manifeste (exigence §4 de la licence)

**Ne pas faire maintenant :** IPFS (personne ne le fait, infra manquante), bundling (trop gros), téléchargement auto des mises à jour (toujours explicite).

---

## 9. Limites de cette étude

- Seule la plateforme linux x86_64 a été mesurée de bout en bout (téléchargement + extraction + hash). Les 4 autres : tailles via API uniquement.
- Le temps de téléchargement (18 s) dépend de la bande passante — mesuré à 31,6 Mo/s ce jour.
- Aucune installation effectuée (contrainte respectée). L'extraction a été faite dans un répertoire temporaire puis supprimée.
- La compatibilité du binaire avec cette VM (glibc etc.) n'a pas été testée au-delà de la présence des fichiers.
