#!/usr/bin/env python3
"""
lecteur_phiast.py — Lecteur de référence du format AST binaire phi-complexity (v1).

Lit et valide les fichiers .phiast produits par ExportAst.lean (métaprogramme Lean).

Échoue BRUYAMMENT (PhiAstError) sur :
  - magic invalide (pas un .phiast)
  - version inconnue (refus de lire un format inconnu — jamais de lecture silencieuse)
  - fichier tronqué
  - tag hors plage (intégrité structurelle)
  - UTF-8 invalide

Mode validation (défaut) : traverse tout le fichier en streaming séquentiel,
compte les déclarations, vérifie l'intégrité structurelle, sans tout garder
en mémoire. Seuls des compteurs et histogrammes sont conservés.

Spécification : SPEC_FORMAT_AST_BINAIRE.md (v1, gelée le 2026-10-05).
Tous les entiers sont little-endian.

Usage :
    python3 lecteur_phiast.py fichier.phiast            # validation complète + statistiques
    python3 lecteur_phiast.py fichier.phiast <max_decls> # résumé itératif (n premières déclarations)

API (import) :
    from lecteur_phiast import lire_entete, iterer_declarations, FichierTronque
    # lire_entete(chemin) -> Entete(version, toolchain, n_decls, taille_entete)
    # iterer_declarations(chemin, max_decls=None) -> génère DeclarationLue
    #     (index, kind, nom, has_value, offset_fin) ; lève FichierTronque
    #     si l'instantané s'arrête avant les n_decls déclarations annoncées.
    # PhiAstReaderV2 : lecteur/validateur v2 (internage des chaînes +
    #     hash-consing des termes ; contrôles AM-03/04/05/06/07/11/16).
    # Migration typée (Étape 4/7 RUCHE-VALIDATION-TYPEE, 2026-10-07) :
    #     reader.constats_types(reference=None) -> [H1.1, H1.2, H1.3, H1.4]
    # Délègue à invariants.py (strtab_lisible, decls_parsables,
    #     noms_uniques, gap_body_strtab) ; retourne les 4 invariants
    #     structurels H1 sous forme de constats typés
    #     {nom, enonce, mesure, seuil, statut, ecart, details},
    #     statut ∈ les 7 de SPEC_VALIDATION_TYPEE.md §1
    #     (DÉMONTRÉ/CONDITIONNEL/ÉVIDENCE/EN-COURS/CONJECTURAL/RÉFUTÉ/ARCHIVÉ).
    # ADDITIVE : parse_header(), parse_decl(), validate() inchangés
    #     (compatibilité nettoyer_export.py et autres outils) ; la méthode
    #     n'utilise ni ne modifie l'état du curseur (chaque invariant ouvre
    #     sa propre vue lecture-seule). Un PhiAstError devient un constat
    #     typé (RÉFUTÉ avec offset exact), jamais une exception ici.
"""

import hashlib
import mmap
import os
import struct
import sys
import time

# Recursion : les Expr/Noms/Niveaux sont arborescents, parfois profonds.
# On utilise la récursion Python avec une limite haute, et on mesure la
# profondeur max atteinte pour le rapport.
sys.setrecursionlimit(1_000_000)

MAGIC = b"PHIAST01"
VERSION = 1
VERSION2 = 2
TRAILER_MAGIC = b"PHIAST02"

KIND_NAMES = ("def", "theorem", "axiom", "opaque",
              "inductif", "constructeur", "recursor", "quotient")
EXPR_NAMES = ("bvar", "fvar", "mvar", "sort", "const", "app",
              "lam", "forallE", "letE", "lit", "proj", "mdata")
LEVEL_NAMES = ("zero", "succ", "max", "imax", "param", "mvar")
BINDER_NAMES = ("implicit", "strictImplicit", "instImplicit", "default")


class PhiAstError(Exception):
    """Échec bruyant : le fichier n'est pas un .phiast v1 valide."""
    pass


class _Cursor:
    """Curseur rapide sur un buffer (mmap). Échoue sur dépassement."""
    __slots__ = ("buf", "pos", "size")

    def __init__(self, buf):
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def u8(self):
        p = self.pos
        if p >= self.size:
            raise PhiAstError(f"fichier tronqué (u8 à l'offset {p})")
        self.pos = p + 1
        return self.buf[p]

    def u16(self):
        p = self.pos
        if p + 2 > self.size:
            raise PhiAstError(f"fichier tronqué (u16 à l'offset {p})")
        self.pos = p + 2
        return struct.unpack_from("<H", self.buf, p)[0]

    def u32(self):
        p = self.pos
        if p + 4 > self.size:
            raise PhiAstError(f"fichier tronqué (u32 à l'offset {p})")
        self.pos = p + 4
        return struct.unpack_from("<I", self.buf, p)[0]

    def raw(self, n):
        p = self.pos
        if p + n > self.size:
            raise PhiAstError(f"fichier tronqué ({n} octets à l'offset {p})")
        self.pos = p + n
        return self.buf[p:p + n]

    def utf8(self):
        n = self.u16()
        r = self.raw(n)
        try:
            return bytes(r).decode("utf-8")
        except UnicodeDecodeError as e:
            raise PhiAstError(f"UTF-8 invalide ({n} octets à l'offset {self.pos - n}): {e}")


class PhiAstReader:
    """Lecteur/validateur streaming d'un fichier .phiast v1."""

    def __init__(self, path):
        self.path = path
        self._fd = open(path, "rb")
        self._mm = mmap.mmap(self._fd.fileno(), 0, access=mmap.ACCESS_READ)
        self.c = _Cursor(self._mm)
        self.toolchain = None
        self.n_decls = 0
        # Statistiques (seule chose conservée en mémoire en mode validation)
        self.st = {
            "decls": 0,
            "kinds": [0] * 8,
            "has_value": 0,
            "expr_tags": [0] * 12,
            "level_tags": [0] * 6,
            "name_tags": [0] * 3,
            "binder_infos": [0] * 4,
            "lit_tags": [0] * 2,
            "max_expr_depth": 0,
            "max_name_depth": 0,
            "max_level_depth": 0,
            "mdata_seen": 0,      # tag 11 : l'exporteur le déballe, ne devrait jamais apparaître
            "nondep_seen": [0, 0],  # letE nonDep : compteur [false, true]
        }

    def close(self):
        try:
            self._mm.close()
        finally:
            self._fd.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------------ en-tête

    def parse_header(self):
        """Valide magic + version (échec bruyant sinon), lit toolchain et n_decls."""
        magic = bytes(self.c.raw(8))
        if magic != MAGIC:
            raise PhiAstError(
                f"magic invalide: {magic!r} (attendu {MAGIC!r}) — "
                "ce n'est pas un fichier .phiast v1, lecture refusée")
        version = self.c.u32()
        if version != VERSION:
            raise PhiAstError(
                f"version inconnue: {version} (attendue {VERSION}) — "
                "refus de lire un format inconnu")
        self.toolchain = self.c.utf8()
        self.n_decls = self.c.u32()
        return self.toolchain, self.n_decls

    # ------------------------------------------------------------------ Noms

    def parse_name(self, depth=0):
        """Nom : tag u8 (0=anonymous, 1=str, 2=num), récursif sur le parent."""
        st = self.st
        if depth > st["max_name_depth"]:
            st["max_name_depth"] = depth
        tag = self.c.u8()
        if tag > 2:
            raise PhiAstError(
                f"tag de Nom inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["name_tags"][tag] += 1
        if tag == 0:
            return
        if tag == 1:
            self.parse_name(depth + 1)
            self.c.utf8()  # segment str (validé, non conservé en mode validation)
        else:
            self.parse_name(depth + 1)
            self.c.u32()  # segment num

    # ------------------------------------------------------------------ Niveaux

    def parse_level(self, depth=0):
        """Niveau : tag u8 0=zero, 1=succ, 2=max, 3=imax, 4=param(Nom), 5=mvar(Nom)."""
        st = self.st
        if depth > st["max_level_depth"]:
            st["max_level_depth"] = depth
        tag = self.c.u8()
        if tag > 5:
            raise PhiAstError(
                f"tag de Niveau inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["level_tags"][tag] += 1
        if tag == 1:
            self.parse_level(depth + 1)
        elif tag == 2 or tag == 3:
            self.parse_level(depth + 1)
            self.parse_level(depth + 1)
        elif tag == 4 or tag == 5:
            self.parse_name()

    # ------------------------------------------------------------------ Expr

    def parse_expr(self, depth=0):
        """Expr : tag u8 0..11, récursif. Valide la structure, ne construit pas d'arbre."""
        st = self.st
        if depth > st["max_expr_depth"]:
            st["max_expr_depth"] = depth
        tag = self.c.u8()
        if tag > 11:
            raise PhiAstError(
                f"tag d'Expr inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["expr_tags"][tag] += 1
        c = self.c
        d1 = depth + 1

        if tag == 0:          # bvar : u32
            c.u32()
        elif tag == 1:        # fvar : Nom
            self.parse_name()
        elif tag == 2:        # mvar : Nom
            self.parse_name()
        elif tag == 3:        # sort : Niveau
            self.parse_level()
        elif tag == 4:        # const : Nom + u32 n + n × Niveau
            self.parse_name()
            n = c.u32()
            for _ in range(n):
                self.parse_level()
        elif tag == 5:        # app : Expr Expr
            self.parse_expr(d1)
            self.parse_expr(d1)
        elif tag == 6 or tag == 7:  # lam / forallE : Nom Expr Expr u8 binderInfo
            self.parse_name()
            self.parse_expr(d1)
            self.parse_expr(d1)
            bi = c.u8()
            if bi > 3:
                raise PhiAstError(
                    f"binderInfo inconnu: {bi} à l'offset {c.pos - 1}")
            st["binder_infos"][bi] += 1
        elif tag == 8:        # letE : Nom Expr Expr Expr u8 nonDep
            self.parse_name()
            self.parse_expr(d1)
            self.parse_expr(d1)
            self.parse_expr(d1)
            nd = c.u8()
            if nd > 1:
                raise PhiAstError(
                    f"nonDep invalide: {nd} à l'offset {c.pos - 1} (attendu 0/1)")
            st["nondep_seen"][nd] += 1
        elif tag == 9:        # lit : u8 lit_tag + (u16 len + utf8)
            lt = c.u8()
            if lt > 1:
                raise PhiAstError(
                    f"lit_tag inconnu: {lt} à l'offset {c.pos - 1}")
            st["lit_tags"][lt] += 1
            c.utf8()  # nat décimal ou chaîne (validé, non conservé)
        elif tag == 10:       # proj : Nom u32 Expr
            self.parse_name()
            c.u32()
            self.parse_expr(d1)
        else:                 # tag == 11 : mdata (déballé à l'export — anomalie si vu)
            st["mdata_seen"] += 1
            self.parse_expr(d1)

    # ------------------------------------------------------------------ déclarations

    def parse_decl(self):
        """Une déclaration : kind u8, Nom, Expr (type), u8 has_value, [Expr value]."""
        st = self.st
        kind = self.c.u8()
        if kind > 7:
            raise PhiAstError(
                f"kind de déclaration inconnu: {kind} à l'offset {self.c.pos - 1}")
        st["kinds"][kind] += 1
        self.parse_name()
        self.parse_expr()      # type
        hv = self.c.u8()
        if hv > 1:
            raise PhiAstError(
                f"has_value invalide: {hv} à l'offset {self.c.pos - 1} (attendu 0/1)")
        if hv:
            st["has_value"] += 1
            self.parse_expr()  # value
        st["decls"] += 1

    def validate(self, progress_every=20000):
        """Traverse tout le fichier en streaming. Retourne les stats."""
        self.parse_header()
        for _ in range(self.n_decls):
            self.parse_decl()
            if progress_every and self.st["decls"] % progress_every == 0:
                print(f"  ... {self.st['decls']:,} / {self.n_decls:,} déclarations",
                      flush=True)
        # Octets restants = anomalie (fichier plus long qu'annoncé)
        rest = self.c.size - self.c.pos
        if rest != 0:
            raise PhiAstError(
                f"{rest} octets restants après {self.n_decls} déclarations "
                f"(fichier plus long qu'annoncé)")
        return self.st

    def report(self):
        """Rapport texte des statistiques."""
        st = self.st
        L = []
        L.append(f"Fichier      : {self.path}")
        L.append(f"Toolchain    : {self.toolchain}")
        L.append(f"Déclarations : {st['decls']:,} / {self.n_decls:,}")
        L.append("Kinds        : " + ", ".join(
            f"{KIND_NAMES[i]}={st['kinds'][i]:,}" for i in range(8) if st['kinds'][i]))
        L.append(f"Avec valeur  : {st['has_value']:,}")
        L.append("Expr tags    : " + ", ".join(
            f"{EXPR_NAMES[i]}={st['expr_tags'][i]:,}" for i in range(12) if st['expr_tags'][i]))
        L.append("Niveaux      : " + ", ".join(
            f"{LEVEL_NAMES[i]}={st['level_tags'][i]:,}" for i in range(6) if st['level_tags'][i]))
        L.append("Noms         : " + ", ".join(
            f"tag{i}={st['name_tags'][i]:,}" for i in range(3)))
        L.append("BinderInfo   : " + ", ".join(
            f"{BINDER_NAMES[i]}={st['binder_infos'][i]:,}" for i in range(4) if st['binder_infos'][i]))
        L.append(f"Littéraux    : nat={st['lit_tags'][0]:,}, str={st['lit_tags'][1]:,}")
        L.append(f"letE nonDep  : false={st['nondep_seen'][0]:,}, true={st['nondep_seen'][1]:,}")
        L.append(f"Profondeur max : expr={st['max_expr_depth']}, "
                 f"nom={st['max_name_depth']}, niveau={st['max_level_depth']}")
        if st["mdata_seen"]:
            L.append(f"ANOMALIE     : tag mdata (11) vu {st['mdata_seen']:,} fois "
                     f"(l'exporteur le déballe — ne devrait jamais apparaître)")
        else:
            L.append("mdata (11)   : 0 (conforme — l'exporteur le déballe)")
        return "\n".join(L)


# ================================================================
# Lecteur v2 (ajouté le 2026-10-06)
#
# Format v2 : internage des chaînes + hash-consing des termes.
# - Header : magic(8) + version(u32=2) + toolchain(u16+utf8) + n_decls(u32) + flags(u32)
# - Body : n_decls × Déclaration (noms = refs vers table des chaînes, tag 12 = ref terme)
# - Table des chaînes : u32 count + count × (u16 len + utf8)
# - Trailer (16 octets) : strtab_offset(u64) + magic "PHIAST02"
# ================================================================

EXPR_NAMES_V2 = EXPR_NAMES + ("ref",)

class PhiAstReaderV2:
    """Lecteur/validateur streaming d'un fichier .phiast v2."""

    def __init__(self, path):
        self.path = path
        self._fd = open(path, "rb")
        self._mm = mmap.mmap(self._fd.fileno(), 0, access=mmap.ACCESS_READ)
        self.c = _Cursor(self._mm)
        self.toolchain = None
        self.n_decls = 0
        self.flags = 0
        self.strtab = []  # liste des chaînes internées (index = id)
        self.st = {
            "decls": 0,
            "kinds": [0] * 8,
            "has_value": 0,
            "expr_tags": [0] * 13,  # + tag 12 ref
            "level_tags": [0] * 6,
            "name_tags": [0] * 3,
            "binder_infos": [0] * 4,
            "lit_tags": [0] * 2,
            "max_expr_depth": 0,
            "max_name_depth": 0,
            "max_level_depth": 0,
            "mdata_seen": 0,
            "nondep_seen": [0, 0],
            "refs": 0,  # nombre de refs de termes (tag 12)
            "str_refs": 0,  # nombre de refs vers la table des chaînes
        }

    def close(self):
        try:
            self._mm.close()
        finally:
            self._fd.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def parse_header(self):
        """Valide magic + version=2, lit toolchain, n_decls, flags."""
        magic = bytes(self.c.raw(8))
        if magic != MAGIC:
            raise PhiAstError(
                f"magic invalide: {magic!r} (attendu {MAGIC!r})")
        version = self.c.u32()
        if version != VERSION2:
            raise PhiAstError(
                f"version inconnue: {version} (attendue {VERSION2})")
        self.toolchain = self.c.utf8()
        self.n_decls = self.c.u32()
        self.flags = self.c.u32()
        # Charge la table des chaînes depuis le trailer
        self._load_strtab()
        return self.toolchain, self.n_decls

    def _load_strtab(self):
        """Lit le trailer (16 derniers octets) et charge la table des chaînes.

        AM-04 : la table est bornée par size-16 (jamais le trailer) et doit
        se terminer EXACTEMENT à size-16.
        """
        size = self.c.size
        if size < 16:
            raise PhiAstError("fichier trop petit pour le trailer v2")
        strtab_limit = size - 16  # la table ne doit jamais mordre sur le trailer
        # Trailer : strtab_offset(u64) + magic(8)
        off = struct.unpack_from("<Q", self.c.buf, strtab_limit)[0]
        tmagic = bytes(self.c.buf[size - 8:size])
        if tmagic != TRAILER_MAGIC:
            raise PhiAstError(
                f"magic du trailer invalide: {tmagic!r} (attendu {TRAILER_MAGIC!r})")
        if off >= strtab_limit:
            raise PhiAstError(
                f"offset de la table des chaînes invalide: {off} "
                f"(doit être < {strtab_limit} = taille {size} - trailer 16)")
        self._strtab_offset = off  # AM-03 : aboutement du body contre cet offset
        # Lit la table : u32 count + count × (u16 len + utf8)
        n = struct.unpack_from("<I", self.c.buf, off)[0]
        pos = off + 4
        tab = []
        for _ in range(n):
            if pos + 2 > strtab_limit:
                raise PhiAstError(
                    f"table des chaînes tronquée (déborde sur le trailer) "
                    f"à l'offset {pos}")
            ln = struct.unpack_from("<H", self.c.buf, pos)[0]
            pos += 2
            if pos + ln > strtab_limit:
                raise PhiAstError(
                    f"table des chaînes tronquée (déborde sur le trailer) "
                    f"à l'offset {pos} (chaîne de {ln} octets)")
            try:
                s = bytes(self.c.buf[pos:pos + ln]).decode("utf-8")
            except UnicodeDecodeError as e:
                raise PhiAstError(f"chaîne UTF-8 invalide dans la table: {e}")
            tab.append(s)
            pos += ln
        if pos != strtab_limit:
            raise PhiAstError(
                f"la table des chaînes se termine à l'offset {pos}, "
                f"attendu {strtab_limit} (= taille {size} - trailer 16)")
        self.strtab = tab

    def _get_str(self, sid):
        """Résout un id de chaîne."""
        if sid >= len(self.strtab):
            raise PhiAstError(
                f"id de chaîne invalide: {sid} (table: {len(self.strtab)} entrées)")
        self.st["str_refs"] += 1
        return self.strtab[sid]

    def parse_name(self, depth=0, retenir=False):
        """Nom v2 : tag u8 (0=anonymous, 1=str, 2=num), segments internés.

        Si retenir=True, retourne le nom pointillé (segments str + numéros
        décimaux joints par '.'). Cette forme est équivalente à
        `Name.toString` de l'exporteur pour l'ordre ET l'égalité : tout nom
        de déclaration partage le même préfixe `[anonymous].` (invariant
        d'ordre), et les segments numériques s'y rendent en décimal pur
        (convention déjà utilisée par `_lire_nom_iteratif`).
        Sans retenir (défaut), avance seulement et retourne None.
        """
        st = self.st
        if depth > st["max_name_depth"]:
            st["max_name_depth"] = depth
        tag = self.c.u8()
        if tag > 2:
            raise PhiAstError(
                f"tag de Nom inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["name_tags"][tag] += 1
        if tag == 0:
            return "" if retenir else None
        parent = self.parse_name(depth + 1, retenir)
        if tag == 1:
            sid = self.c.u32()
            seg = self._get_str(sid)  # valide l'id (borne la table)
        else:
            seg = str(self.c.u32())
        if not retenir:
            return None
        return seg if parent == "" else parent + "." + seg

    def parse_level(self, depth=0):
        """Niveau v2 : mêmes tags que v1, Noms v2."""
        st = self.st
        if depth > st["max_level_depth"]:
            st["max_level_depth"] = depth
        tag = self.c.u8()
        if tag > 5:
            raise PhiAstError(
                f"tag de Niveau inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["level_tags"][tag] += 1
        if tag == 1:
            self.parse_level(depth + 1)
        elif tag == 2 or tag == 3:
            self.parse_level(depth + 1)
            self.parse_level(depth + 1)
        elif tag == 4 or tag == 5:
            self.parse_name()

    def parse_expr(self, depth=0):
        """Expr v2 : tags 0-11 comme v1, tag 12 = ref (u64 offset)."""
        st = self.st
        if depth > st["max_expr_depth"]:
            st["max_expr_depth"] = depth
        tag = self.c.u8()
        if tag > 12:
            raise PhiAstError(
                f"tag d'Expr inconnu: {tag} à l'offset {self.c.pos - 1}")
        st["expr_tags"][tag] += 1
        c = self.c
        d1 = depth + 1

        if tag == 0:
            c.u32()
        elif tag == 1 or tag == 2:
            self.parse_name()
        elif tag == 3:
            self.parse_level()
        elif tag == 4:
            self.parse_name()
            n = c.u32()
            for _ in range(n):
                self.parse_level()
        elif tag == 5:
            self.parse_expr(d1)
            self.parse_expr(d1)
        elif tag == 6 or tag == 7:
            self.parse_name()
            self.parse_expr(d1)
            self.parse_expr(d1)
            bi = c.u8()
            if bi > 3:
                raise PhiAstError(
                    f"binderInfo inconnu: {bi} à l'offset {c.pos - 1}")
            st["binder_infos"][bi] += 1
        elif tag == 8:
            self.parse_name()
            self.parse_expr(d1)
            self.parse_expr(d1)
            self.parse_expr(d1)
            nd = c.u8()
            if nd > 1:
                raise PhiAstError(
                    f"nonDep invalide: {nd} à l'offset {c.pos - 1}")
            st["nondep_seen"][nd] += 1
        elif tag == 9:
            lt = c.u8()
            if lt > 1:
                raise PhiAstError(
                    f"lit_tag inconnu: {lt} à l'offset {c.pos - 1}")
            st["lit_tags"][lt] += 1
            sid = c.u32()
            self._get_str(sid)  # valide l'id (chaîne internée)
        elif tag == 10:
            self.parse_name()
            c.u32()
            self.parse_expr(d1)
        elif tag == 11:
            st["mdata_seen"] += 1
            self.parse_expr(d1)
        else:  # tag == 12 : ref vers un terme déjà émis
            st["refs"] += 1
            # AM-11 : borne AVANT lecture — jamais lire puis vérifier.
            if c.pos + 8 > c.size:
                raise PhiAstError(f"ref tronquée à l'offset {c.pos}")
            off = struct.unpack_from("<Q", c.buf, c.pos)[0]
            c.pos += 8
            # L'offset doit pointer avant la position actuelle (terme déjà émis)
            if off >= c.pos - 8:
                raise PhiAstError(
                    f"ref invalide: offset {off} >= position {c.pos - 8} "
                    f"(doit pointer vers un terme déjà émis)")

    def parse_decl(self):
        """Une déclaration v2. Retourne (nom, offset_début) pour AM-06/AM-07."""
        st = self.st
        decl_start = self.c.pos
        kind = self.c.u8()
        if kind > 7:
            raise PhiAstError(
                f"kind de déclaration inconnu: {kind} à l'offset {self.c.pos - 1}")
        st["kinds"][kind] += 1
        name = self.parse_name(retenir=True)
        self.parse_expr()
        hv = self.c.u8()
        if hv > 1:
            raise PhiAstError(
                f"has_value invalide: {hv} à l'offset {self.c.pos - 1}")
        if hv:
            st["has_value"] += 1
            self.parse_expr()
        st["decls"] += 1
        return name, decl_start

    def validate(self, progress_every=20000):
        """Traverse tout le fichier. Retourne les stats.

        Contrôles d'intégrité (échec bruyant, offset exact) :
        - AM-03 : la dernière déclaration se termine EXACTEMENT à strtab_offset
        - AM-06 : noms de déclarations triés (0 inversion tolérée)
        - AM-07 : aucun doublon de nom
        - AM-05 : flags cohérents avec le contenu
                  (bit0 ⟺ table non vide, bit1 ⟺ refs de termes présentes)
        """
        self.parse_header()
        # La fin du body = début de la table des chaînes (offset du trailer)
        body_end = self._strtab_offset
        prev_name = None
        for i in range(self.n_decls):
            name, decl_start = self.parse_decl()
            # AM-06 / AM-07 : ordre trié strict (l'exporteur trie par
            # Name.toString ; cf. SPEC "Ordre des déclarations : trié par nom")
            if prev_name is not None:
                if name < prev_name:
                    raise PhiAstError(
                        f"noms de déclarations non triés : {prev_name!r} "
                        f"(décl #{i - 1}) > {name!r} (décl #{i}, "
                        f"offset {decl_start}) — 0 inversion tolérée")
                if name == prev_name:
                    raise PhiAstError(
                        f"nom de déclaration dupliqué : {name!r} "
                        f"(décl #{i}, offset {decl_start}) — 0 doublon toléré")
            prev_name = name
            if progress_every and self.st["decls"] % progress_every == 0:
                print(f"  ... {self.st['decls']:,} / {self.n_decls:,} déclarations",
                      flush=True)
        # AM-03 : aboutement — le body se termine EXACTEMENT à strtab_offset
        if self.c.pos != body_end:
            raise PhiAstError(
                f"aboutement rompu : le body se termine à l'offset "
                f"{self.c.pos}, attendu {body_end} (début de la table des "
                f"chaînes) — écart {self.c.pos - body_end:+d} octet(s)")
        # AM-05 : confrontation flags ↔ contenu
        n_strings = len(self.strtab)
        refs = self.st["refs"]
        if bool(self.flags & 1) != (n_strings > 0):
            etat = "vide" if n_strings == 0 else "de %d entrée(s)" % n_strings
            raise PhiAstError(
                "flags incohérents : bit0=%d (flags=0x%x) mais table des "
                "chaînes %s — bit0 ⟺ n_strings > 0"
                % (1 if self.flags & 1 else 0, self.flags, etat))
        if bool(self.flags & 2) != (refs > 0):
            raise PhiAstError(
                "flags incohérents : bit1=%d (flags=0x%x) mais %d ref(s) de "
                "termes — bit1 ⟺ refs > 0"
                % (1 if self.flags & 2 else 0, self.flags, refs))
        return self.st

    def report(self):
        """Rapport texte des statistiques v2."""
        st = self.st
        L = []
        L.append(f"Fichier      : {self.path}")
        # AM-16 : empreinte du fichier validé (identification exacte)
        L.append(f"SHA256       : {hashlib.sha256(self._mm).hexdigest()}")
        L.append(f"Version      : 2 (à la volée)")
        L.append(f"Toolchain    : {self.toolchain}")
        L.append(f"Flags        : 0x{self.flags:x}")
        L.append(f"Déclarations : {st['decls']:,} / {self.n_decls:,}")
        L.append("Kinds        : " + ", ".join(
            f"{KIND_NAMES[i]}={st['kinds'][i]:,}" for i in range(8) if st['kinds'][i]))
        L.append(f"Avec valeur  : {st['has_value']:,}")
        L.append("Expr tags    : " + ", ".join(
            f"{EXPR_NAMES_V2[i]}={st['expr_tags'][i]:,}" for i in range(13) if st['expr_tags'][i]))
        L.append(f"Refs termes  : {st['refs']:,} (tag 12)")
        L.append(f"Refs chaînes : {st['str_refs']:,} (table: {len(self.strtab):,} chaînes)")
        L.append("Niveaux      : " + ", ".join(
            f"{LEVEL_NAMES[i]}={st['level_tags'][i]:,}" for i in range(6) if st['level_tags'][i]))
        L.append(f"Profondeur max : expr={st['max_expr_depth']}, "
                 f"nom={st['max_name_depth']}, niveau={st['max_level_depth']}")
        if st["mdata_seen"]:
            L.append(f"ANOMALIE     : tag mdata (11) vu {st['mdata_seen']:,} fois")
        return "\n".join(L)

    def constats_types(self, reference=None):
        """Invariants H1.1–H1.4 sous forme de constats typés.

        Mission RUCHE-VALIDATION-TYPEE — Étape 4/7 (migration du lecteur
        de référence vers le pattern typé). Spécification :
        SPEC_VALIDATION_TYPEE.md §1 (les 7 statuts) et §2 (H1.1–H1.4).

        Délègue aux fonctions canoniques de `invariants.py`
        (strtab_lisible, decls_parsables, noms_uniques, gap_body_strtab)
        appliquées à `self.path`, dans l'ordre de mesure obligatoire
        H1.1 → H1.2 → H1.3 → H1.4 (spec §2 : strtab_offset vient de H1.1,
        p_fin de H1.2, le scan H1.3 roule sur le parse H1.2, H1.4 compare
        les deux).

        Retourne la liste des 4 constats. Chaque constat est un dict à
        exactement 7 clés : {nom, enonce, mesure, seuil, statut, ecart,
        details}, avec statut ∈ {DÉMONTRÉ, CONDITIONNEL, ÉVIDENCE,
        EN-COURS, CONJECTURAL, RÉFUTÉ, ARCHIVÉ}. Règle d'or mécanisée
        par invariants.py : un statut sans mesure exhibée est refusé
        (replié en EN-COURS) — jamais de booléen déguisé. Un PhiAstError
        levé par la mesure devient un constat RÉFUTÉ avec offset exact,
        jamais une exception ici.

        Paramètre :
            reference : chemin optionnel d'un export de référence,
                transmis à H1.4 (gap_body_strtab) pour la forensique
                comparative des gaps (SHA-256) — cf. spec §2/H1.4 et
                DIAGNOSTIC_GAP792.md.

        Garanties :
        - ADDITIVE : n'utilise ni ne modifie l'état du lecteur — chaque
          invariant ouvre sa propre vue lecture-seule (mmap) du fichier ;
          le curseur self.c est intouché (vérifiable : self.c.pos
          identique avant/après l'appel) ;
        - ne remplace PAS validate() : celui-ci conserve son contrat
          historique (échec bruyant par PhiAstError, verdict booléen).
          Le typage est l'affaire de l'orchestrateur valider_typé.py.

        Coût : chaque invariant re-parse le fichier (H1.2/H1.3/H1.4
        parcourent tout le body ; H1.4 calcule des SHA-256 en streaming,
        body jamais chargé en RAM). Sur un export de plusieurs Go,
        compter des minutes, pas des secondes — c'est un instrument de
        mesure, pas un filtre rapide.
        """
        # Import paresseux : invariants.py importe lecteur_phiast.py à son
        # tour ; un import en tête de module créerait un cycle.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from invariants import (strtab_lisible, decls_parsables,
                                noms_uniques, gap_body_strtab)
        return [strtab_lisible(self.path),
                decls_parsables(self.path),
                noms_uniques(self.path),
                gap_body_strtab(self.path, reference=reference)]


# ================================================================
# API itérative (phase 2, ajoutée le 2026-10-06)
#
#   lire_entete(chemin) -> Entete
#   iterer_declarations(chemin, max_decls=None) -> génère DeclarationLue
#
# Complète le validateur ci-dessus (qui ne conserve que des statistiques) :
# l'itérateur retient le nom de chaque déclaration, fonctionne en flux, et
# signale explicitement la troncature (FichierTronque) — le cas normal d'un
# fichier en cours d'écriture par l'exporteur Lean.
# Lecture seule, sans verrou ; mmap fige la taille à l'ouverture
# (instantané : la croissance ultérieure du fichier n'est pas vue).
# Les parseurs de cette section sont ITÉRATIFS (pas de récursion Python),
# contrairement au validateur historique qui utilise la récursion.
# ================================================================

class FichierTronque(PhiAstError):
    """L'en-tête annonce plus de déclarations que l'instantané n'en contient."""

    def __init__(self, message, declarations_lues):
        super().__init__(message)
        self.declarations_lues = declarations_lues


class Entete(object):
    """En-tête validée d'un fichier .phiast v1."""
    __slots__ = ("version", "toolchain", "n_decls", "taille_entete")

    def __init__(self, version, toolchain, n_decls, taille_entete):
        self.version = version
        self.toolchain = toolchain
        self.n_decls = n_decls
        self.taille_entete = taille_entete

    def __repr__(self):
        return ("Entete(version=%r, toolchain=%r, n_decls=%r, taille_entete=%r)"
                % (self.version, self.toolchain, self.n_decls,
                   self.taille_entete))


class DeclarationLue(object):
    """Une déclaration itérée : métadonnées + nom, sans l'arbre d'expression."""
    __slots__ = ("index", "kind", "nom", "has_value", "offset_fin")

    def __init__(self, index, kind, nom, has_value, offset_fin):
        self.index = index          # int, 0-based
        self.kind = kind            # str, ex. "theorem"
        self.nom = nom              # str, ex. "Lean.Elab.Term.elabTerm"
        self.has_value = has_value  # bool
        self.offset_fin = offset_fin  # int, octet juste après la déclaration

    def __repr__(self):
        return ("DeclarationLue(index=%r, kind=%r, nom=%r, has_value=%r, "
                "offset_fin=%r)" % (self.index, self.kind, self.nom,
                                    self.has_value, self.offset_fin))


def _lire_nom_iteratif(c, retenir):
    """Lit un Nom sur le curseur c (itératif, sans récursion).

    Le format écrit : tag, puis le Nom parent, puis le segment.
    On accumule les tags en attente puis on lit les segments dans l'ordre
    du flux (le plus interne d'abord). Si retenir, retourne la forme
    pointillée (convention d'affichage de Lean : Name.num -> parent.n),
    sinon avance seulement et retourne None.
    """
    pile = []
    while True:
        tag = c.u8()
        if tag == 0:  # anonymous : fin de la chaîne de parents
            break
        if tag > 2:
            raise PhiAstError(
                f"tag de Nom inconnu: {tag} à l'offset {c.pos - 1}")
        pile.append(tag)
    if not retenir:
        for tag in reversed(pile):
            if tag == 1:
                c.utf8()
            else:
                c.u32()
        return None
    parts = []
    for tag in reversed(pile):
        if tag == 1:
            parts.append(c.utf8())
        else:
            parts.append(str(c.u32()))
    return ".".join(parts)


def _sauter_niveau(c):
    """Avance le curseur sur un Niveau complet (itératif, sans le construire)."""
    pile = []
    while True:
        tag = c.u8()
        if tag == 0:      # zero
            enf = 0
        elif tag == 1:    # succ(Niveau)
            enf = 1
        elif tag == 2 or tag == 3:  # max / imax
            enf = 2
        elif tag == 4 or tag == 5:  # param(Nom) / mvar(Nom)
            _lire_nom_iteratif(c, False)
            enf = 0
        else:
            raise PhiAstError(
                f"tag de Niveau inconnu: {tag} à l'offset {c.pos - 1}")
        if enf == 0:
            while pile:
                pile[-1] -= 1
                if pile[-1] > 0:
                    break
                pile.pop()
            else:
                return
        else:
            pile.append(enf)


def _controle_binder_info(v, offset):
    if v > 3:
        raise PhiAstError(
            f"binderInfo inconnu: {v} à l'offset {offset}")


def _controle_nondep(v, offset):
    if v > 1:
        raise PhiAstError(
            f"nonDep invalide: {v} à l'offset {offset} (attendu 0/1)")


def _sauter_expr(c):
    """Avance le curseur sur une Expr complète (itératif, sans la construire).

    Pile explicite de cadres [enfants_restants, controle_suffixe] : quand un
    cadre se termine, son octet suffixe éventuel (binderInfo / nonDep, placé
    APRÈS les enfants par le format) est lu et validé, puis la complétion
    remonte au parent.
    """
    pile = []
    while True:
        tag = c.u8()
        if tag > 11:
            raise PhiAstError(
                f"tag d'Expr inconnu: {tag} à l'offset {c.pos - 1}")
        ctrl = None
        if tag == 0:            # bvar : u32
            c.u32()
            enf = 0
        elif tag == 1 or tag == 2:  # fvar / mvar : Nom
            _lire_nom_iteratif(c, False)
            enf = 0
        elif tag == 3:          # sort : Niveau
            _sauter_niveau(c)
            enf = 0
        elif tag == 4:          # const : Nom + u32 n + n × Niveau
            _lire_nom_iteratif(c, False)
            for _ in range(c.u32()):
                _sauter_niveau(c)
            enf = 0
        elif tag == 5:          # app : Expr Expr
            enf = 2
        elif tag == 6 or tag == 7:  # lam / forallE : Nom Expr Expr u8
            _lire_nom_iteratif(c, False)
            enf = 2
            ctrl = _controle_binder_info
        elif tag == 8:          # letE : Nom Expr Expr Expr u8
            _lire_nom_iteratif(c, False)
            enf = 3
            ctrl = _controle_nondep
        elif tag == 9:          # lit : u8 lit_tag + (u16 len + utf8)
            lt = c.u8()
            if lt > 1:
                raise PhiAstError(
                    f"lit_tag inconnu: {lt} à l'offset {c.pos - 1}")
            c.utf8()
            enf = 0
        elif tag == 10:         # proj : Nom u32 Expr
            _lire_nom_iteratif(c, False)
            c.u32()
            enf = 1
        else:                   # tag == 11 : mdata (déballé à l'export ;
            enf = 1             # ne devrait jamais apparaître — on le saute)
        if enf == 0:
            # Noeud terminé : remonter (la complétion d'un enfant décrémente
            # le parent, en cascade ; la pile vide = racine terminée).
            while pile:
                pile[-1][0] -= 1
                if pile[-1][0] > 0:
                    break
                _, controle = pile.pop()
                if controle is not None:
                    controle(c.u8(), c.pos - 1)
            else:
                return
        else:
            pile.append([enf, ctrl])


def _lire_une_declaration(c, index):
    """Lit une déclaration complète : métadonnées + nom, exprs sautées."""
    kind_tag = c.u8()
    if kind_tag > 7:
        raise PhiAstError(
            f"kind de déclaration inconnu: {kind_tag} à l'offset {c.pos - 1}")
    nom = _lire_nom_iteratif(c, True)
    _sauter_expr(c)  # type
    hv = c.u8()
    if hv > 1:
        raise PhiAstError(
            f"has_value invalide: {hv} à l'offset {c.pos - 1} (attendu 0/1)")
    if hv:
        _sauter_expr(c)  # value
    return DeclarationLue(index, KIND_NAMES[kind_tag], nom, bool(hv), c.pos)


def lire_entete(chemin):
    """Lit et valide l'en-tête d'un fichier .phiast. Retourne un Entete.

    Échoue bruyamment (PhiAstError) sur magic invalide, version inconnue
    ou en-tête tronqué. Lecture seule, sans verrou.
    Supporte v1 et v2 (la taille de l'en-tête diffère : v2 a un champ flags).
    """
    with open(chemin, "rb") as f:
        fixe = f.read(14)  # magic(8) + version(u32) + len(toolchain)(u16)
        if len(fixe) < 14:
            raise PhiAstError(
                f"fichier tronqué dans l'en-tête ({len(fixe)} octet(s))")
        magic = fixe[:8]
        if magic != MAGIC:
            raise PhiAstError(
                f"magic invalide: {magic!r} (attendu {MAGIC!r}) — "
                "ce n'est pas un fichier .phiast, lecture refusée")
        version = struct.unpack("<I", fixe[8:12])[0]
        if version not in (VERSION, VERSION2):
            raise PhiAstError(
                f"version inconnue: {version} (attendues {VERSION}, {VERSION2}) — "
                "refus de lire un format inconnu")
        n_len = struct.unpack("<H", fixe[12:14])[0]
        raw_tc = f.read(n_len)
        if len(raw_tc) < n_len:
            raise PhiAstError("fichier tronqué dans le toolchain de l'en-tête")
        try:
            toolchain = raw_tc.decode("utf-8")
        except UnicodeDecodeError as e:
            raise PhiAstError(f"toolchain UTF-8 invalide : {e}")
        raw_n = f.read(4)
        if len(raw_n) < 4:
            raise PhiAstError("fichier tronqué dans n_decls de l'en-tête")
        n_decls = struct.unpack("<I", raw_n)[0]
        taille = 14 + n_len + 4
        if version == VERSION2:
            raw_f = f.read(4)
            if len(raw_f) < 4:
                raise PhiAstError("fichier tronqué dans flags de l'en-tête v2")
            taille += 4
        return Entete(version, toolchain, n_decls, taille)


def iterer_declarations(chemin, max_decls=None):
    """Génère des DeclarationLue en flux, en ordre du fichier.

    Lève FichierTronque si l'instantané s'arrête avant les n_decls
    déclarations annoncées par l'en-tête (cas normal d'un fichier en cours
    d'écriture). Lève PhiAstError sur tag inconnu ou octets excédentaires
    après n_decls déclarations (la SPEC v1 ne définit pas de footer : un
    fichier complet se termine exactement après sa dernière déclaration).
    Lecture seule, sans verrou ; mmap fige un instantané à l'ouverture.
    """
    fd = open(chemin, "rb")
    try:
        try:
            mm = mmap.mmap(fd.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:
            raise PhiAstError("fichier vide : 0 octet")
        try:
            c = _Cursor(mm)
            # --- en-tête (validée, bruyante) ---
            magic = bytes(c.raw(8))
            if magic != MAGIC:
                raise PhiAstError(
                    f"magic invalide: {magic!r} (attendu {MAGIC!r}) — "
                    "ce n'est pas un fichier .phiast v1, lecture refusée")
            version = c.u32()
            if version != VERSION:
                raise PhiAstError(
                    f"version inconnue: {version} (attendue {VERSION}) — "
                    "refus de lire un format inconnu")
            c.utf8()  # toolchain (validée, non conservée ici)
            n_decls = c.u32()
            total = n_decls if max_decls is None else min(n_decls, max_decls)
            for i in range(total):
                if c.pos >= c.size:
                    raise FichierTronque(
                        f"fichier tronqué : {i} déclaration(s) lisible(s) "
                        f"sur {n_decls} annoncée(s)", i)
                try:
                    yield _lire_une_declaration(c, i)
                except PhiAstError as e:
                    # _Cursor signale le dépassement par "fichier tronqué..."
                    if str(e).startswith("fichier tronqué"):
                        raise FichierTronque(
                            f"fichier tronqué : {i} déclaration(s) lisible(s) "
                            f"sur {n_decls} annoncée(s) — {e}", i)
                    raise
            if max_decls is None and c.pos != c.size:
                raise PhiAstError(
                    f"{c.size - c.pos} octet(s) excédentaire(s) après "
                    f"{n_decls} déclarations (la SPEC v1 ne définit pas "
                    "de footer)")
        finally:
            mm.close()
    finally:
        fd.close()


def _main_resume(path, max_decls):
    """Résumé itératif : n déclarations, dernière lue, octets consommés."""
    t0 = time.perf_counter()
    try:
        entete = lire_entete(path)
    except (PhiAstError, OSError) as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 1
    # Le mode itératif n'est supporté que pour v1 (les parseurs itératifs
    # sont spécifiques au format v1). Pour v2, utiliser la validation complète.
    if entete.version == VERSION2:
        print(f"fichier     : {path}")
        print("magic       : PHIAST01 OK")
        print(f"version     : {entete.version} (v2)")
        print(f"toolchain   : {entete.toolchain}")
        print(f"n_decls     : {entete.n_decls:,} (annoncées)")
        print("statut : mode résumé non supporté pour v2 — utilisez la validation complète")
        print("         (sans l'argument max_decls)")
        return 2
    print(f"fichier     : {path}")
    print("magic       : PHIAST01 OK")
    print(f"version     : {entete.version}")
    print(f"toolchain   : {entete.toolchain}")
    print(f"n_decls     : {entete.n_decls:,} (annoncées)")

    n_lues = 0
    octets = entete.taille_entete
    derniere = None
    statut = "COMPLET"
    detail = ""
    code = 0
    try:
        for d in iterer_declarations(path, max_decls):
            n_lues += 1
            derniere = d
            octets = d.offset_fin
    except FichierTronque as e:
        statut = "TRONQUÉ"
        detail = str(e)
    except PhiAstError as e:
        statut = "ERREUR FORMAT"
        detail = str(e)
        code = 1
    except OSError as e:
        statut = "ERREUR IO"
        detail = str(e)
        code = 1

    dt = time.perf_counter() - t0
    print(f"déclarations lues : {n_lues:,}")
    if derniere is not None:
        print(f"dernière déclaration : [{derniere.kind}] {derniere.nom}")
    print(f"octets consommés : {octets:,}")
    print(f"statut : {statut}" + (f" — {detail}" if detail else ""))
    if dt > 0 and n_lues:
        print(f"durée : {dt:.1f} s ({n_lues / dt:,.0f} déclarations/s)")
    return code


def main(argv):
    if len(argv) not in (2, 3) or argv[1] in ("-h", "--help"):
        print(f"Usage : {argv[0]} fichier.phiast [max_decls]",
              file=sys.stderr)
        print("  sans max_decls : validation complète + statistiques",
              file=sys.stderr)
        print("  avec max_decls : résumé itératif (n premières déclarations)",
              file=sys.stderr)
        return 2
    path = argv[1]
    if not os.path.isfile(path):
        print(f"ERREUR : fichier introuvable : {path}", file=sys.stderr)
        return 2
    if len(argv) == 3:
        try:
            max_decls = int(argv[2])
            if max_decls < 0:
                raise ValueError
        except ValueError:
            print("ERREUR : max_decls doit être un entier >= 0",
                  file=sys.stderr)
            return 2
        return _main_resume(path, max_decls)
    size = os.path.getsize(path)
    print(f"Validation de {path} ({size / (1024**2):.1f} Mo)...")
    # Détecte la version (octets 8-12)
    with open(path, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[:8] != MAGIC:
            print(f"ÉCHEC : magic invalide", file=sys.stderr)
            return 1
        ver = struct.unpack("<I", hdr[8:12])[0]
    ReaderCls = PhiAstReaderV2 if ver == VERSION2 else PhiAstReader
    if ver not in (VERSION, VERSION2):
        print(f"ÉCHEC : version inconnue {ver}", file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    try:
        with ReaderCls(path) as r:
            r.validate()
            dt = time.perf_counter() - t0
            print(r.report())
            print(f"Temps        : {dt:.1f} s")
            print(f"Débit        : {size / dt / (1024**2):.2f} Mo/s")
            print(f"Vitesse      : {r.st['decls'] / dt:,.0f} déclarations/s")
            print("RÉSULTAT     : OK — intégrité structurelle vérifiée")
            return 0
    except PhiAstError as e:
        dt = time.perf_counter() - t0
        print(f"ÉCHEC ({dt:.1f} s) : {e}", file=sys.stderr)
        return 1
    except RecursionError:
        print("ÉCHEC : profondeur de récursion excessive "
              "(fichier pathologique ou bug)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
