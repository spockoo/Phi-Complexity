#!/usr/bin/env python3
"""
lecteur_zz.py — Lecteur lazy du format `.zz` (body `.phiast` v2 compressé).

Lit un `<base>.phiast.zz` + son manifeste `<base>.phiast.zz.json` (+ sidecar
`<base>.phiast.zz.hdr`, voir SPEC_FORMAT_ZZ.md §5) et expose les déclarations
EXACTEMENT comme le `.phiast` d'origine, sans jamais le tenir en mémoire
en entier : les 15 chunks zlib-9 sont décompressés à la demande, un à la fois.

Échoue BRUYAMMENT (ZzError, chunk fautif nommé avec son offset) sur :
  - manifeste invalide (offsets non contigus, sommes incohérentes)
  - chunk corrompu (zlib.error) ou de taille décompressée != `in`
  - sha256 du body décompressé != manifeste (RÉFUTÉ explicite)
  - tag inconnu, UTF-8 invalide, ref hors body, sidecar invalide

Portable Windows dès le départ : pas de os.pread, pas de os.O_DIRECTORY ;
lecture positionnée = lseek + read en boucle ; os.O_BINARY sous garde hasattr.

API (import) :
    from lecteur_zz import lire_entete, iterer_declarations, LecteurZZ, ZzError
    # lire_entete(chemin_zz, tete=None) -> Entete  (classe de lecteur_phiast)
    # iterer_declarations(chemin_zz, tete=None, max_decls=None)
    #     -> génère DeclarationLue (classe de lecteur_phiast) ; lève
    #        FichierTronque si le body contient moins que n_decls déclarations.
    # LecteurZZ(chemin_zz, tete=None) : context manager.
    #     .lire_entete() .iterer_declarations(max_decls=None)
    #     .indexer() -> [offset body de chaque déclaration]
    #     .lire_declaration(i, index=None) -> DeclarationLue (accès aléatoire)
    #     .verifier_sha256_body() -> hexdigest (lève ZzError si != manifeste)
    #     .constats_types() -> [H1.1..H1.4] constats typés (jamais de booléen nu)
    # tete : chemin du sidecar `.zz.hdr` OU du `.phiast` d'origine (lecture
    #        seule des régions [0, body_start) et [strtab_offset, fin)).
    #        Par défaut : `<chemin_zz>.hdr`, sinon `<chemin_zz sans .zz>`.

Usage :
    python3 lecteur_zz.py <fichier.phiast.zz> [max_decls]   # résumé itératif
    python3 lecteur_zz.py --extraire-sidecar <fichier.phiast.zz> [chemin.phiast]

Spécification : SPEC_FORMAT_ZZ.md.
"""

import bisect
import hashlib
import json
import os
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from .lecteur_phiast import (  # noqa: E402
    Entete, DeclarationLue, FichierTronque, KIND_NAMES,
    MAGIC, TRAILER_MAGIC, VERSION2, PhiAstError,
)

MANIFESTE_SUFFIXE = ".json"
SIDECAR_SUFFIXE = ".hdr"

# Les 7 statuts typés (SPEC_VALIDATION_TYPEE.md §1) — jamais de booléen nu.
STATUTS = ("DÉMONTRÉ", "CONDITIONNEL", "ÉVIDENCE", "EN-COURS",
           "CONJECTURAL", "RÉFUTÉ", "ARCHIVÉ")


class ZzError(Exception):
    """Échec bruyant : le .zz / son manifeste / son sidecar est invalide.

    Le message nomme TOUJOURS le chunk fautif (index + offset dans le .zz)
    quand c'est un chunk qui est en cause. Jamais de devinette silencieuse.
    """


def _pread_portable(fd, n, offset):
    """Lecture positionnée portable (os.pread n'existe pas sur Windows)."""
    if hasattr(os, "pread"):
        return os.pread(fd, n, offset)
    os.lseek(fd, offset, os.SEEK_SET)
    morceaux = []
    restant = n
    while restant > 0:
        k = os.read(fd, restant)
        if not k:
            break
        morceaux.append(k)
        restant -= len(k)
    return b"".join(morceaux)


def _open_rb(chemin):
    """open() binaire portable (os.O_BINARY sous garde hasattr)."""
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows : mode binaire obligatoire
    return os.open(chemin, flags)


def _lire_plage(fd, debut, n, quoi):
    """Lit exactement n octets à l'offset debut (boucle)."""
    buf = bytearray(n)
    mv = memoryview(buf)
    r = 0
    while r < n:
        k = _pread_portable(fd, n - r, debut + r)
        if not k:
            raise ZzError(
                "%s tronqué : %d/%d octets lus à l'offset %d"
                % (quoi, r, n, debut + r))
        mv[r:r + len(k)] = k
        r += len(k)
    return bytes(buf)


# ================================================================ manifeste

class Manifeste(object):
    """Manifeste `.zz.json` chargé et validé (contiguïté, sommes, bornes)."""

    def __init__(self, chemin_zz):
        mpath = chemin_zz + MANIFESTE_SUFFIXE
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                man = json.load(f)
        except (OSError, ValueError) as e:
            raise ZzError("manifeste illisible (%s) : %s" % (mpath, e))
        try:
            self.body_start = int(man["body_start"])
            self.body_len = int(man["body_len"])
            self.niveau = int(man["niveau"])
            self.chunk_mo = int(man["chunk_mo"])
            self.n_attendus = int(man["n_chunks_attendus"])
            self.chunks = man["chunks"]
            self.sha256_body = man["resultat"]["sha256_body"]
        except (KeyError, TypeError, ValueError) as e:
            raise ZzError("manifeste incomplet (%s) : champ manquant/invalide "
                          "(%s)" % (mpath, e))
        self._valider(mpath, chemin_zz)
        # Sommes cumulées des `in` : pour localiser un offset body -> chunk.
        self._cum_in = [0]
        for c in self.chunks:
            self._cum_in.append(self._cum_in[-1] + c["in"])

    def _valider(self, mpath, chemin_zz):
        ch = self.chunks
        if not isinstance(ch, list) or not ch:
            raise ZzError("manifeste (%s) : table des chunks vide/absente — "
                          "RÉFUTÉ" % mpath)
        if len(ch) != self.n_attendus:
            raise ZzError("manifeste (%s) : %d chunks, %d attendus — RÉFUTÉ"
                          % (mpath, len(ch), self.n_attendus))
        if ch[0]["offset"] != 0:
            raise ZzError("manifeste (%s) : le chunk 0 ne commence pas à "
                          "l'offset 0 (offset=%r) — RÉFUTÉ"
                          % (mpath, ch[0]["offset"]))
        total_in = 0
        for i, c in enumerate(ch):
            try:
                ci, ci_in, ci_out, ci_off = (int(c["chunk"]), int(c["in"]),
                                            int(c["out"]), int(c["offset"]))
            except (KeyError, TypeError, ValueError):
                raise ZzError("manifeste (%s) : chunk %d incomplet — RÉFUTÉ"
                              % (mpath, i))
            if ci != i:
                raise ZzError("manifeste (%s) : chunk d'index %r à la "
                              "position %d — RÉFUTÉ" % (mpath, ci, i))
            if i > 0:
                prev = ch[i - 1]
                if ci_off != int(prev["offset"]) + int(prev["out"]):
                    raise ZzError(
                        "manifeste (%s) : chunks non contigus entre %d et %d "
                        "(offset %d attendu %d) — RÉFUTÉ"
                        % (mpath, i - 1, i, ci_off,
                           int(prev["offset"]) + int(prev["out"])))
            if ci_in <= 0 or ci_out <= 0 or ci_off < 0:
                raise ZzError("manifeste (%s) : chunk %d avec in/out/offset "
                              "invalide (%r) — RÉFUTÉ" % (mpath, i, c))
            total_in += ci_in
        if total_in != self.body_len:
            raise ZzError("manifeste (%s) : Σ in = %d != body_len %d — RÉFUTÉ"
                          % (mpath, total_in, self.body_len))
        dernier = ch[-1]
        taille_zz = int(dernier["offset"]) + int(dernier["out"])
        try:
            taille_reelle = os.path.getsize(chemin_zz)
        except OSError as e:
            raise ZzError("fichier .zz illisible (%s) : %s" % (chemin_zz, e))
        if taille_reelle != taille_zz:
            raise ZzError("fichier .zz (%s) : taille %d != taille annoncée "
                          "par le manifeste %d — RÉFUTÉ (tronqué ou "
                          "étendu)" % (chemin_zz, taille_reelle, taille_zz))
        # n_chunks_attendus == ceil(body_len / chunk_mo·10⁶)
        attendu = (self.body_len + self.chunk_mo * 1000000 - 1) // (
            self.chunk_mo * 1000000)
        if self.n_attendus != attendu:
            raise ZzError("manifeste (%s) : n_chunks_attendus %d != "
                          "ceil(body_len/chunk) %d — RÉFUTÉ"
                          % (mpath, self.n_attendus, attendu))

    def chunk_pour_offset(self, off_body):
        """Index du chunk contenant l'offset body off_body (0-based)."""
        if off_body < 0 or off_body >= self.body_len:
            raise ZzError("offset body %d hors bornes [0, %d) — RÉFUTÉ"
                          % (off_body, self.body_len))
        return bisect.bisect_right(self._cum_in, off_body) - 1


# ================================================================ sidecar / tête

class Tete(object):
    """Header + table des chaînes, lus depuis le sidecar `.zz.hdr` ou le
    `.phiast` d'origine (lecture seule des régions hors body)."""

    def __init__(self, chemin_zz, tete, man):
        self.chemin = self._resoudre(chemin_zz, tete)
        self._charger(man)

    @staticmethod
    def _resoudre(chemin_zz, tete):
        if tete:
            if not os.path.isfile(tete):
                raise ZzError("source de tête introuvable : %s" % tete)
            return tete
        sidecar = chemin_zz + SIDECAR_SUFFIXE
        if os.path.isfile(sidecar):
            return sidecar
        if chemin_zz.endswith(".zz"):
            phiast = chemin_zz[:-3]
            if os.path.isfile(phiast):
                return phiast
        raise ZzError(
            "ni sidecar (%s) ni .phiast d'origine trouvés pour %s — "
            "impossible de lire l'en-tête et la table des chaînes"
            % (sidecar, chemin_zz))

    def _charger(self, man):
        fd = _open_rb(self.chemin)
        try:
            bs = man.body_start
            bl = man.body_len
            # --- header : [0, body_start)
            header = _lire_plage(fd, 0, bs, "en-tête")
            self._parse_header(header, bs)
            # --- strtab + trailer : [strtab_offset, fin)
            # Taille totale = strtab_offset + len(strtab+trailer).
            strtab_offset = bs + bl
            try:
                taille = os.fstat(fd).st_size
            except OSError as e:
                raise ZzError("fstat impossible sur %s : %s" % (self.chemin, e))
            if self.chemin.endswith(".zz" + SIDECAR_SUFFIXE) or \
                    self.chemin.endswith(SIDECAR_SUFFIXE):
                # Sidecar : header (bs) ++ strtab+trailer.
                if taille < bs + 16:
                    raise ZzError("sidecar trop petit (%s, %d octets) — RÉFUTÉ"
                                  % (self.chemin, taille))
                queue = _lire_plage(fd, taille - 16, 16, "trailer du sidecar")
                strtab_blob = _lire_plage(fd, bs, taille - bs - 16,
                                         "table des chaînes du sidecar")
            else:
                # .phiast d'origine : la région commence à strtab_offset.
                if taille < strtab_offset + 16:
                    raise ZzError(".phiast trop petit pour le trailer (%s) — "
                                  "RÉFUTÉ" % self.chemin)
                queue = _lire_plage(fd, taille - 16, 16, "trailer")
                strtab_blob = _lire_plage(fd, strtab_offset,
                                         taille - strtab_offset - 16,
                                         "table des chaînes")
            off_trailer = struct.unpack("<Q", queue[:8])[0]
            if queue[8:] != TRAILER_MAGIC:
                raise ZzError("magic du trailer invalide (%r) dans %s — RÉFUTÉ"
                              % (queue[8:], self.chemin))
            if off_trailer != strtab_offset:
                raise ZzError("trailer incohérent dans %s : strtab_offset=%d "
                              "mais body_start+body_len=%d — RÉFUTÉ"
                              % (self.chemin, off_trailer, strtab_offset))
            self.strtab = self._parse_strtab(strtab_blob)
        finally:
            os.close(fd)

    def _parse_header(self, header, bs):
        if len(header) < 14:
            raise ZzError("en-tête tronqué (%d octets) — RÉFUTÉ" % len(header))
        if header[:8] != MAGIC:
            raise ZzError("magic invalide (%r) — RÉFUTÉ" % header[:8])
        version = struct.unpack("<I", header[8:12])[0]
        if version != VERSION2:
            raise ZzError("version %d != 2 — le .zz ne couvre que des body "
                          "v2 — RÉFUTÉ" % version)
        tc_len = struct.unpack("<H", header[12:14])[0]
        if 14 + tc_len + 8 > len(header):
            raise ZzError("en-tête tronqué dans toolchain/n_decls/flags — "
                          "RÉFUTÉ")
        try:
            self.toolchain = header[14:14 + tc_len].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ZzError("toolchain UTF-8 invalide : %s — RÉFUTÉ" % e)
        self.n_decls = struct.unpack("<I", header[14 + tc_len:18 + tc_len])[0]
        self.flags = struct.unpack("<I", header[18 + tc_len:22 + tc_len])[0]
        if 22 + tc_len != bs:
            raise ZzError("body_start manifeste (%d) != 22+len(toolchain) "
                          "(%d) — RÉFUTÉ" % (bs, 22 + tc_len))

    @staticmethod
    def _parse_strtab(blob):
        if len(blob) < 4:
            raise ZzError("table des chaînes tronquée — RÉFUTÉ")
        n = struct.unpack("<I", blob[:4])[0]
        pos = 4
        tab = []
        for _ in range(n):
            if pos + 2 > len(blob):
                raise ZzError("table des chaînes tronquée à l'entrée %d — "
                              "RÉFUTÉ" % len(tab))
            ln = struct.unpack("<H", blob[pos:pos + 2])[0]
            pos += 2
            if pos + ln > len(blob):
                raise ZzError("table des chaînes tronquée (chaîne %d, %d "
                              "octets) — RÉFUTÉ" % (len(tab), ln))
            try:
                tab.append(blob[pos:pos + ln].decode("utf-8"))
            except UnicodeDecodeError as e:
                raise ZzError("chaîne UTF-8 invalide dans la table "
                              "(entrée %d) : %s — RÉFUTÉ" % (len(tab), e))
            pos += ln
        if pos != len(blob):
            raise ZzError("la table des chaînes se termine à %d, attendu %d "
                          "— RÉFUTÉ" % (pos, len(blob)))
        return tab


# ================================================================ flux lazy

class _FluxBody(object):
    """Curseur sur le body décompressé à la demande, chunk par chunk.

    Ne tient qu'UN chunk décompressé en mémoire (plus une petite queue lors
    du chevauchement d'une déclaration sur deux chunks — transitoire).
    `pos` est l'offset absolu dans le body [0, body_len).
    """

    def __init__(self, lecteur):
        self.lecteur = lecteur
        self.pos = 0
        self._buf = b""
        self._base = 0  # offset body du début de _buf

    def _charger_chunk(self, i):
        data = self.lecteur._lire_chunk(i)
        self._buf = data
        self._base = self.lecteur.man._cum_in[i]

    def _assurer(self, n):
        """Garantit n octets lisibles depuis pos (chevauchement géré)."""
        if self.pos + n <= self._base + len(self._buf):
            return
        if self.pos + n > self.lecteur.man.body_len:
            raise ZzError(
                "body tronqué : lecture de %d octet(s) à l'offset body %d "
                "(body_len %d) — RÉFUTÉ"
                % (n, self.pos, self.lecteur.man.body_len))
        # Chevauchement : queue restante + chunk suivant concaténés.
        # Le chunk à charger est celui du PREMIER OCTET NON COUVERT
        # (base+len(buf)), pas celui de pos : quand pos tombe exactement
        # sur une frontière de chunk, chunk_pour_offset(pos) désigne déjà
        # le nouveau chunk — charger i+1 sauterait un chunk entier.
        j = self.lecteur.man.chunk_pour_offset(self._base + len(self._buf))
        queue = self._buf[self.pos - self._base:]
        suivant = self.lecteur._lire_chunk(j)
        self._buf = queue + suivant
        self._base = self.pos

    def _lire(self, n):
        self._assurer(n)
        o = self.pos - self._base
        r = self._buf[o:o + n]
        self.pos += n
        return r

    def u8(self):
        return self._lire(1)[0]

    def u16(self):
        return struct.unpack("<H", self._lire(2))[0]

    def u32(self):
        return struct.unpack("<I", self._lire(4))[0]

    def u64(self):
        return struct.unpack("<Q", self._lire(8))[0]


# ================================================================ lecteur

class LecteurZZ(object):
    """Lecteur lazy d'un `.zz` (+ manifeste + sidecar/`.phiast` pour la tête).

    Même API de lecture que lecteur_phiast (Entete, DeclarationLue,
    FichierTronque partagés). Ne décompresse qu'un chunk à la fois.
    """

    def __init__(self, chemin_zz, tete=None):
        self.chemin_zz = chemin_zz
        self.man = Manifeste(chemin_zz)
        self.tete = Tete(chemin_zz, tete, self.man)
        self.strtab = self.tete.strtab
        self._fd = _open_rb(chemin_zz)
        self._cache_chunk = {}  # au plus 1 chunk : {index: bytes}
        # Renseigné par indexer() / constats_types() après un passage
        # complet : {"n": k, "noms": [...]} — k déclarations valides
        # excédentaires non comptées par le header (GAP792 connu).
        self.excedentaires = None

    def close(self):
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._cache_chunk.clear()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------------------------------------------------------- bas niveau

    def _lire_chunk(self, i):
        """Décompresse le chunk i (cache d'1 chunk). Lève ZzError nommé si
        corrompu : index, offset dans le .zz, in/out attendus."""
        if i in self._cache_chunk:
            return self._cache_chunk[i]
        c = self.man.chunks[i]
        comp = _lire_plage(self._fd, c["offset"], c["out"],
                           "chunk %d du .zz" % i)
        try:
            data = zlib.decompress(comp)
        except zlib.error as e:
            raise ZzError("chunk %d CORROMPU (offset %d dans le .zz, %d "
                          "octets compressés) : %s — RÉFUTÉ"
                          % (i, c["offset"], c["out"], e))
        if len(data) != c["in"]:
            raise ZzError("chunk %d : %d octets décompressés, %d attendus "
                          "(offset %d dans le .zz) — RÉFUTÉ"
                          % (i, len(data), c["in"], c["offset"]))
        self._cache_chunk.clear()
        self._cache_chunk[i] = data
        return data

    def verifier_sha256_body(self):
        """SHA-256 du body décompressé en flux (1 chunk à la fois).

        Retourne le hexdigest. Lève ZzError RÉFUTÉ si != manifeste.
        """
        sha = hashlib.sha256()
        for i in range(len(self.man.chunks)):
            sha.update(self._lire_chunk(i))
        hexd = sha.hexdigest()
        if hexd != self.man.sha256_body:
            raise ZzError(
                "sha256 du body décompressé %s != manifeste %s — RÉFUTÉ "
                "(body corrompu ou manifeste mensonger)"
                % (hexd, self.man.sha256_body))
        return hexd

    # ------------------------------------------------------- tête / en-tête

    def lire_entete(self):
        """Retourne un Entete (classe de lecteur_phiast)."""
        return Entete(VERSION2, self.tete.toolchain, self.tete.n_decls,
                      self.man.body_start)

    # ------------------------------------------------------- parse v2 (itératif)

    def _nom_segment(self, tag, v, pos_avant):
        if tag == 1:
            if v >= len(self.strtab):
                raise ZzError("id de chaîne invalide : %d (table : %d "
                              "entrées) à l'offset body %d — RÉFUTÉ"
                              % (v, len(self.strtab), pos_avant))
            return self.strtab[v]
        return str(v)

    def _lire_nom(self, flux, retenir):
        """Nom v2 : tag u8 (0=anonymous, 1=str→u32 sid, 2=num→u32), itératif."""
        pile = []
        while True:
            tag = flux.u8()
            if tag == 0:
                break
            if tag > 2:
                raise ZzError("tag de Nom inconnu : %d à l'offset body %d — "
                              "RÉFUTÉ" % (tag, flux.pos - 1))
            pile.append(tag)
        if not retenir:
            for _ in reversed(pile):
                flux.u32()
            return None
        parts = []
        for tag in reversed(pile):
            pos_avant = flux.pos
            v = flux.u32()
            parts.append(self._nom_segment(tag, v, pos_avant))
        return ".".join(parts)

    def _sauter_niveau(self, flux):
        """Niveau v2 : mêmes tags que v1 (0..5), itératif."""
        pile = []
        while True:
            tag = flux.u8()
            if tag == 0:
                enf = 0
            elif tag == 1:
                enf = 1
            elif tag == 2 or tag == 3:
                enf = 2
            elif tag == 4 or tag == 5:
                self._lire_nom(flux, False)
                enf = 0
            else:
                raise ZzError("tag de Niveau inconnu : %d à l'offset body %d "
                              "— RÉFUTÉ" % (tag, flux.pos - 1))
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

    def _sauter_expr(self, flux):
        """Expr v2 : tags 0..11 comme v1, tag 12 = ref (u64 offset fichier).

        Itératif (pile explicite), avec suffixes binderInfo/nonDep validés
        après les enfants, comme lecteur_phiast._sauter_expr.
        """
        body_start = self.man.body_start
        pile = []  # cadres [enfants_restants, controle_suffixe]
        while True:
            tag = flux.u8()
            if tag > 12:
                raise ZzError("tag d'Expr inconnu : %d à l'offset body %d — "
                              "RÉFUTÉ" % (tag, flux.pos - 1))
            ctrl = None
            if tag == 0:                      # bvar : u32
                flux.u32()
                enf = 0
            elif tag == 1 or tag == 2:        # fvar / mvar : Nom
                self._lire_nom(flux, False)
                enf = 0
            elif tag == 3:                    # sort : Niveau
                self._sauter_niveau(flux)
                enf = 0
            elif tag == 4:                    # const : Nom + n × Niveau
                self._lire_nom(flux, False)
                for _ in range(flux.u32()):
                    self._sauter_niveau(flux)
                enf = 0
            elif tag == 5:                    # app : Expr Expr
                enf = 2
            elif tag == 6 or tag == 7:        # lam / forallE
                self._lire_nom(flux, False)
                enf = 2
                ctrl = self._controle_binder
            elif tag == 8:                    # letE
                self._lire_nom(flux, False)
                enf = 3
                ctrl = self._controle_nondep
            elif tag == 9:                    # lit : u8 + u32 sid
                lt = flux.u8()
                if lt > 1:
                    raise ZzError("lit_tag inconnu : %d à l'offset body %d — "
                                  "RÉFUTÉ" % (lt, flux.pos - 1))
                pos_avant = flux.pos
                sid = flux.u32()
                if sid >= len(self.strtab):
                    raise ZzError("id de chaîne invalide : %d à l'offset body "
                                  "%d — RÉFUTÉ" % (sid, pos_avant))
                enf = 0
            elif tag == 10:                   # proj : Nom u32 Expr
                self._lire_nom(flux, False)
                flux.u32()
                enf = 1
            elif tag == 11:                   # mdata (déballé à l'export)
                enf = 1
            else:                             # tag == 12 : ref -> terme émis
                pos_avant = flux.pos
                off = flux.u64()
                # Coordonnées fichier d'origine : la ref doit pointer dans le
                # body, vers un terme déjà émis (avant pos_avant, en octets
                # fichier = body_start + offset body).
                pos_fichier = body_start + pos_avant
                if off < body_start or off >= pos_fichier:
                    raise ZzError("ref invalide : offset %d hors [%d, %d) "
                                  "(doit pointer vers un terme déjà émis du "
                                  "body) — RÉFUTÉ"
                                  % (off, body_start, pos_fichier))
                enf = 0
            if enf == 0:
                while pile:
                    pile[-1][0] -= 1
                    if pile[-1][0] > 0:
                        break
                    _, controle = pile.pop()
                    if controle is not None:
                        controle(flux.u8(), flux.pos - 1)
            else:
                pile.append([enf, ctrl])
            if enf == 0 and not pile:
                return

    @staticmethod
    def _controle_binder(v, offset):
        if v > 3:
            raise ZzError("binderInfo inconnu : %d à l'offset body %d — RÉFUTÉ"
                          % (v, offset))

    @staticmethod
    def _controle_nondep(v, offset):
        if v > 1:
            raise ZzError("nonDep invalide : %d à l'offset body %d — RÉFUTÉ"
                          % (v, offset))

    def _lire_declaration(self, flux, index):
        """Une déclaration v2 : métadonnées + nom, exprs sautées."""
        decl_start = flux.pos
        kind = flux.u8()
        if kind > 7:
            raise ZzError("kind de déclaration inconnu : %d à l'offset body "
                          "%d — RÉFUTÉ" % (kind, flux.pos - 1))
        nom = self._lire_nom(flux, True)
        self._sauter_expr(flux)               # type
        hv = flux.u8()
        if hv > 1:
            raise ZzError("has_value invalide : %d à l'offset body %d — RÉFUTÉ"
                          % (hv, flux.pos - 1))
        if hv:
            self._sauter_expr(flux)           # value
        return DeclarationLue(index, KIND_NAMES[kind], nom, bool(hv),
                              self.man.body_start + flux.pos), decl_start

    def iterer_declarations(self, max_decls=None):
        """Génère des DeclarationLue en flux, par chunks lazy.

        Lève FichierTronque si le body contient moins que n_decls
        déclarations. `offset_fin` est en coordonnées fichier d'origine
        (body_start + offset body), comme le lecteur du .phiast brut.
        """
        flux = _FluxBody(self)
        flux._charger_chunk(0)
        n = self.tete.n_decls
        total = n if max_decls is None else min(n, max_decls)
        for i in range(total):
            if flux.pos >= self.man.body_len:
                raise FichierTronque(
                    "body épuisé : %d déclaration(s) lisible(s) sur %d "
                    "annoncée(s)" % (i, n), i)
            try:
                decl, _ = self._lire_declaration(flux, i)
            except ZzError as e:
                if "tronqué" in str(e):
                    raise FichierTronque(
                        "body tronqué : %d déclaration(s) lisible(s) sur %d "
                        "annoncée(s) — %s" % (i, n, e), i)
                raise
            yield decl

    def _analyser_queue(self, flux):
        """Analyse honnête de la queue du body après les n_decls déclarations.

        Retourne (k, noms) où k = nombre de déclarations complètes et
        valides parsables dans la queue. Ne lève ZzError que si la queue
        est VRAIMENT corrompue (ne se parse pas en déclarations complètes
        aboutissant exactement à body_len) — un écart de comptage connu
        (GAP792 : header sous-compte de 8, voir DIAGNOSTIC_GAP792.md)
        n'est pas une corruption.
        """
        k = 0
        noms = []
        while flux.pos < self.man.body_len:
            pos_avant = flux.pos
            try:
                decl, _ = self._lire_declaration(flux, self.tete.n_decls + k)
            except ZzError as e:
                raise ZzError("queue du body corrompue après %d déclaration(s) "
                              "excédentaire(s) (offset body %d) : %s — RÉFUTÉ"
                              % (k, pos_avant, e))
            noms.append(decl.nom)
            k += 1
            if k > 100000:
                raise ZzError("queue anormale : >100000 déclarations "
                              "excédentaires — RÉFUTÉ")
        return k, noms

    def _finir_passage(self, flux):
        """Clôture un passage complet : aboutement exact ou queue analysée.

        Met à jour self.excedentaires = {"n": k, "noms": [...]}.
        Ne lève que sur corruption réelle de la queue.
        """
        if flux.pos == self.man.body_len:
            self.excedentaires = {"n": 0, "noms": []}
        else:
            k, noms = self._analyser_queue(flux)
            self.excedentaires = {"n": k, "noms": noms}
        return self.excedentaires

    # ------------------------------------------------------- index / accès

    def indexer(self):
        """Un passage : offsets body (0-based) de chaque déclaration.

        Coût : une itération complète (~1 chunk en mémoire à la fois).
        Retourne une liste de n_decls int (208 105 × 8 o ≈ 1,7 Mo).
        """
        flux = _FluxBody(self)
        flux._charger_chunk(0)
        offsets = []
        for i in range(self.tete.n_decls):
            if flux.pos >= self.man.body_len:
                raise FichierTronque(
                    "indexation : body épuisé à la déclaration %d sur %d"
                    % (i, self.tete.n_decls), i)
            _, decl_start = self._lire_declaration(flux, i)
            offsets.append(decl_start)
        # Aboutement : exact, ou queue excédentaire analysée honnêtement
        # (GAP792 : le header sous-compte — ce n'est pas une corruption).
        self._finir_passage(flux)
        return offsets

    def lire_declaration(self, i, index=None):
        """Accès aléatoire : la déclaration d'index i (0-based).

        `index` = liste de indexer() (reconstruit sinon — coûteux).
        Ne décompresse que le chunk contenant la déclaration.
        """
        n = self.tete.n_decls
        if i < 0 or i >= n:
            raise ZzError("index de déclaration %d hors [0, %d) — RÉFUTÉ"
                          % (i, n))
        if index is None:
            index = self.indexer()
        off = index[i]
        ci = self.man.chunk_pour_offset(off)
        data = self._lire_chunk(ci)
        flux = _FluxBody(self)
        flux._buf = data
        flux._base = self.man._cum_in[ci]
        flux.pos = off
        decl, _ = self._lire_declaration(flux, i)
        return decl

    # ------------------------------------------------------- constats typés

    @staticmethod
    def _constat(nom, enonce, mesure, seuil, statut, ecart, details):
        assert statut in STATUTS, "statut non typé : %r" % (statut,)
        return {"nom": nom, "enonce": enonce, "mesure": mesure,
                "seuil": seuil, "statut": statut, "ecart": ecart,
                "details": details}

    def constats_types(self):
        """Les 4 invariants structurels H1 sur la vue virtuelle
        (header + body lazy + strtab), sans le .phiast complet.

        Retourne [{nom, enonce, mesure, seuil, statut, ecart, details}],
        statut ∈ les 7 de SPEC_VALIDATION_TYPEE.md. Un échec de lecture
        devient un constat RÉFUTÉ avec offset exact, jamais une exception.
        """
        constats = []
        # H1.1 — strtab lisible (déjà parsée à l'ouverture ; on la revalide)
        try:
            n_str = len(self.strtab)
            assert n_str > 0
            c1 = self._constat("H1.1", "strtab_lisible", n_str, "> 0",
                               "DÉMONTRÉ", 0,
                               "%d chaînes internées, parsées sans erreur "
                               "depuis %s" % (n_str, self.tete.chemin))
        except (ZzError, AssertionError) as e:
            c1 = self._constat("H1.1", "strtab_lisible", 0, "> 0",
                               "RÉFUTÉ", 1, str(e))
        constats.append(c1)
        # H1.2/H1.3/H1.4 — un seul passage : parsabilité, unicité/tri, gap.
        try:
            flux = _FluxBody(self)
            flux._charger_chunk(0)
            prev_nom = None
            n = 0
            for i in range(self.tete.n_decls):
                decl, _ = self._lire_declaration(flux, i)
                if prev_nom is not None:
                    if decl.nom < prev_nom:
                        raise ZzError("noms non triés : %r > %r (décl #%d) — "
                                      "RÉFUTÉ" % (prev_nom, decl.nom, i))
                    if decl.nom == prev_nom:
                        raise ZzError("nom dupliqué : %r (décl #%d) — RÉFUTÉ"
                                      % (decl.nom, i))
                prev_nom = decl.nom
                n += 1
            if flux.pos != self.man.body_len:
                # Queue non vide : l'analyser honnêtement au lieu de
                # crier à la corruption (GAP792 — voir DIAGNOSTIC_GAP792.md).
                k, noms_q = self._analyser_queue(flux)
                self.excedentaires = {"n": k, "noms": noms_q}
            else:
                self.excedentaires = {"n": 0, "noms": []}
            constats.append(self._constat(
                "H1.2", "decls_parsables", n, "== n_decls",
                "DÉMONTRÉ" if n == self.tete.n_decls else "RÉFUTÉ",
                abs(n - self.tete.n_decls),
                "%d déclarations parsées en flux lazy (1 chunk à la fois)" % n))
            constats.append(self._constat(
                "H1.3", "noms_uniques", n, "triés stricts", "DÉMONTRÉ", 0,
                "0 inversion, 0 doublon sur %d noms" % n))
            exc = self.excedentaires["n"]
            if exc == 0:
                constats.append(self._constat(
                    "H1.4", "gap_body_strtab", flux.pos, "== body_len",
                    "DÉMONTRÉ", 0,
                    "le body se termine exactement à body_len=%d (aboutement)"
                    % self.man.body_len))
            else:
                constats.append(self._constat(
                    "H1.4", "gap_body_strtab", "%d+%d" % (n, exc),
                    "== n_decls (+ excédentaires)", "ÉVIDENCE", exc,
                    "aboutement à body_len=%d via %d déclaration(s) "
                    "excédentaire(s) valide(s) non comptée(s) par le header "
                    "(GAP792 connu — DIAGNOSTIC_GAP792.md) : %s"
                    % (self.man.body_len, exc,
                       ", ".join(self.excedentaires["noms"][:8]))))
        except (ZzError, FichierTronque) as e:
            for nom, enonce in (("H1.2", "decls_parsables"),
                                ("H1.3", "noms_uniques"),
                                ("H1.4", "gap_body_strtab")):
                constats.append(self._constat(nom, enonce, 0, "n/a",
                                              "RÉFUTÉ", 1, str(e)))
        return constats


# ================================================================ API module

def _resoudre_tete(chemin_zz, tete):
    return tete  # la résolution (sidecar puis .phiast) est dans Tete._resoudre


def lire_entete(chemin_zz, tete=None):
    """Lit et valide l'en-tête via le sidecar/le .phiast. Retourne un Entete.

    Échoue bruyamment (ZzError) sur manifeste/sidecar invalide.
    Lecture seule, sans verrou.
    """
    with LecteurZZ(chemin_zz, tete) as r:
        return r.lire_entete()


def iterer_declarations(chemin_zz, tete=None, max_decls=None):
    """Génère des DeclarationLue en flux lazy, en ordre du fichier.

    Même contrat que lecteur_phiast.iterer_declarations : lève FichierTronque
    si le body contient moins que n_decls déclarations, ZzError (chunk fautif
    nommé) sur corruption. Lecture seule, sans verrou.
    """
    with LecteurZZ(chemin_zz, tete) as r:
        for decl in r.iterer_declarations(max_decls=max_decls):
            yield decl


def extraire_sidecar(chemin_zz, chemin_phiast=None, tete=None):
    """Construit `<chemin_zz>.hdr` = header ++ strtab+trailer (lecture seule
    du .phiast). Le .zz existant n'est jamais modifié."""
    man = Manifeste(chemin_zz)
    if chemin_phiast is None:
        src = None
        try:
            with open(chemin_zz + MANIFESTE_SUFFIXE, encoding="utf-8") as f:
                src = json.load(f).get("src")
        except (OSError, ValueError):
            pass
        if src and os.path.isfile(src):
            chemin_phiast = src
        elif chemin_zz.endswith(".zz") and os.path.isfile(chemin_zz[:-3]):
            chemin_phiast = chemin_zz[:-3]
        else:
            raise ZzError("aucun .phiast source trouvé pour extraire le "
                          "sidecar de %s" % chemin_zz)
    fd = _open_rb(chemin_phiast)
    try:
        taille = os.fstat(fd).st_size
        strtab_offset = man.body_start + man.body_len
        if taille < strtab_offset + 16:
            raise ZzError(".phiast trop petit pour le trailer — RÉFUTÉ")
        queue = _lire_plage(fd, taille - 16, 16, "trailer")
        if queue[8:] != TRAILER_MAGIC or \
                struct.unpack("<Q", queue[:8])[0] != strtab_offset:
            raise ZzError("trailer incohérent — RÉFUTÉ")
        header = _lire_plage(fd, 0, man.body_start, "en-tête")
        strtab = _lire_plage(fd, strtab_offset, taille - strtab_offset,
                             "strtab+trailer")
    finally:
        os.close(fd)
    dst = chemin_zz + SIDECAR_SUFFIXE
    # Écriture atomique (jamais de sidecar à moitié écrit).
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(strtab)
    os.replace(tmp, dst)
    sha = hashlib.sha256()
    with open(dst, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            sha.update(b)
    return {"sidecar": dst, "octets": man.body_start + len(strtab),
            "sha256": sha.hexdigest()}


def _main_resume(chemin_zz, max_decls, tete=None):
    t0 = time.perf_counter()
    with LecteurZZ(chemin_zz, tete) as r:
        ent = r.lire_entete()
        print("Fichier      : %s" % chemin_zz)
        print("Manifeste    : %d chunks, sha256_body=%.16s…"
              % (len(r.man.chunks), r.man.sha256_body))
        print("Version      : %d (body lazy)" % ent.version)
        print("Toolchain    : %s" % ent.toolchain)
        print("Déclarations : %d annoncées" % ent.n_decls)
        print("Tête         : %s" % r.tete.chemin)
        n = 0
        dernier = None
        for decl in r.iterer_declarations(max_decls=max_decls):
            n += 1
            dernier = decl
        dt = time.perf_counter() - t0
        print("Lues         : %d en %.1f s (%.0f décl/s)"
              % (n, dt, n / dt if dt > 0 else 0))
        if dernier is not None:
            print("Dernière     : #%d %s %s (fin @ %d)"
                  % (dernier.index, dernier.kind, dernier.nom,
                     dernier.offset_fin))


# ================================================================ mode serveur

def _envoyer(out, rep):
    out.write(json.dumps(rep, ensure_ascii=False) + "\n")
    out.flush()


def _serveur(chemin_zz):
    """Boucle REPL JSON-lines (voir SPEC_FFI_ZZ_LEAN.md §1).

    stdin  : une requête JSON par ligne.
    stdout : une réponse JSON par ligne (flush après chacune).
    Toute erreur -> {"ok": false, "erreur": "..."} (jamais de plantage
    silencieux, jamais de devinette). Lecture seule du .zz.
    """
    r = LecteurZZ(chemin_zz)  # valide le manifeste à l'ouverture
    index = None
    inp, out = sys.stdin, sys.stdout
    try:
        for ligne in inp:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                q = json.loads(ligne)
            except ValueError as e:
                _envoyer(out, {"ok": False,
                               "erreur": "JSON invalide : %s" % e})
                continue
            op = q.get("op")
            try:
                if op == "entete":
                    e = r.lire_entete()
                    rep = {"ok": True, "version": e.version,
                           "toolchain": e.toolchain, "n_decls": e.n_decls,
                           "body_start": e.taille_entete,
                           "sha256_body": r.man.sha256_body,
                           "n_chunks": len(r.man.chunks)}
                elif op == "decl":
                    if index is None:
                        index = r.indexer()
                    d = r.lire_declaration(int(q["index"]), index=index)
                    rep = {"ok": True, "decl": _decl_json(d)}
                elif op == "plage":
                    if index is None:
                        index = r.indexer()
                    a, b = int(q["debut"]), int(q["fin"])
                    if not (0 <= a <= b <= r.tete.n_decls):
                        raise ZzError("plage [%d, %d) hors [0, %d] — RÉFUTÉ"
                                      % (a, b, r.tete.n_decls))
                    if b - a > 1000:
                        raise ZzError("plage > 1000 déclarations — RÉFUTÉ")
                    rep = {"ok": True, "decls": [
                        _decl_json(r.lire_declaration(i, index=index))
                        for i in range(a, b)]}
                elif op == "sha256":
                    hexd = r.verifier_sha256_body()  # lève si != manifeste
                    rep = {"ok": True, "sha256_body": hexd}
                elif op == "constats":
                    rep = {"ok": True, "constats": r.constats_types()}
                elif op == "quitter":
                    _envoyer(out, {"ok": True})
                    break
                else:
                    rep = {"ok": False,
                           "erreur": "op inconnue : %r" % (op,)}
            except (ZzError, PhiAstError, FichierTronque,
                    ValueError, KeyError, IndexError) as e:
                rep = {"ok": False,
                       "erreur": "%s: %s" % (type(e).__name__, e)}
            _envoyer(out, rep)
    finally:
        r.close()


def _decl_json(d):
    return {"index": d.index, "kind": d.kind, "nom": d.nom,
            "has_value": d.has_value, "offset_fin": d.offset_fin}

def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: lecteur_zz.py <fichier.phiast.zz> [max_decls]")
        print("       lecteur_zz.py --extraire-sidecar <fichier.phiast.zz> "
              "[chemin.phiast]")
        print("       lecteur_zz.py --serveur <fichier.phiast.zz>  "
              "(protocole JSON-lines pour Lean)")
        return 2
    if argv[1] == "--extraire-sidecar":
        if len(argv) < 3:
            print("usage: lecteur_zz.py --extraire-sidecar <fichier.phiast.zz> "
                  "[chemin.phiast]")
            return 2
        info = extraire_sidecar(argv[2], argv[3] if len(argv) > 3 else None)
        print(json.dumps(info, indent=2))
        return 0
    if argv[1] == "--serveur":
        # Protocole JSON-lines pour Lean (SPEC_FFI_ZZ_LEAN.md §1).
        if len(argv) < 3:
            print("usage: lecteur_zz.py --serveur <fichier.phiast.zz>")
            return 2
        try:
            _serveur(argv[2])
        except (ZzError, PhiAstError) as e:
            print("ÉCHEC : %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 1
        return 0
    max_decls = int(argv[2]) if len(argv) > 2 else None
    try:
        _main_resume(argv[1], max_decls)
    except (ZzError, PhiAstError, FichierTronque) as e:
        print("ÉCHEC : %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
