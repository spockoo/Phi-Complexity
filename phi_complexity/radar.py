"""
radar.py — Le témoin : vue du mouvement structurel entre deux états.

Directive unique (Tomy) : démontrer formellement la conjecture de
Navier-Stokes suivant les critères de l'institut Clay.
Troisième directive (Tomy, 2026-10-01) : développer phi-complexity
librement SOUS CONDITION que ça serve la directive unique.

Règle CONSTITUTIONNELLE (garde testée dans tests/test_garde_non_prescriptif.py) :
le radar MONTRE des faits structurels, il ne DÉCIDE jamais. Aucune
observation ne porte de langage prescriptif (voir LISTE_NOIRE_PRESCRIPTIF
ci-dessous). Le tri est humain ; l'instrument ordonne l'attention, il ne
tranche pas.

Détecteurs (purement mécaniques, poids de saillance FIXES et documentés) :
- enonce_change (10) : l'énoncé d'une déclaration a changé (le corps peut
  avoir changé aussi) — le changement le plus à risque qui existe ;
- effondrement_preuve (9) : corps ≥5 lignes → ≤2 lignes COUPLÉ à un énoncé
  qui bouge — on signale la paire, pas un verdict ;
- nouvel_axiome (9) : déclaration `axiom` présente après, absente avant ;
- doublon (8) : même nom défini dans ≥2 fichiers de l'état APRÈS
  (cas réel : partialDeriv/HasSchwartzDecay entre Clay_NS_Part0 et
  IBP_Stokes) ;
- nouveau_sorry (8) : `sorry` (identifiant, hors commentaires/chaînes)
  dans le corps après, absent avant ;
- symbole_perdu (5) : déclaration présente avant, absente après ;
- corps_change (1) : corps modifié, énoncé identique (refactorisation
  de preuve normale) ;
- symbole_gagne (1) : déclaration présente après, absente avant
  (hors axiom/sorry déjà couverts).
"""

import hashlib
import os
from dataclasses import dataclass, field

from .parseur_autonome import parse, tokenize

#: Phrases prescriptives INTERDITES dans toute sortie du radar.
#: Le radar décrit, il ne prescrit jamais. Garde mécanique :
#: tests/test_garde_non_prescriptif.py.
LISTE_NOIRE_PRESCRIPTIF = (
    "veto requis",
    "veto requise",
    "exige un veto",
    "necessite un veto",
    "nécessite un veto",
    "dangereux",
    "dangereuse",
    "doit etre",
    "doit être",
    "inacceptable",
    "suspect",
    "suspecte",
    "alert",
)

#: Poids de saillance FIXES — documentés ici, auditables, jamais appris.
#: L'ordre de présentation suit la saillance décroissante ; le tri
#: (qu'est-ce qui mérite le veto ?) reste intégralement humain.
POIDS_SAILLANCE = {
    "enonce_change": 10,
    "effondrement_preuve": 9,
    "nouvel_axiome": 9,
    "doublon": 8,
    "nouveau_sorry": 8,
    "symbole_perdu": 5,
    "corps_change": 1,
    "symbole_gagne": 1,
}

#: Seuil d'effondrement : corps d'au moins 5 lignes réduit à 2 ou moins.
SEUIL_EFFONDREMENT_AVANT = 5
SEUIL_EFFONDREMENT_APRES = 2

#: Préfixes de chemins (relatifs, avec / final pour les dossiers) exclus de
#: la DÉTECTION DE DOUBLONS par défaut — pas du scan : un énoncé modifié
#: dans hors_chaine_clay reste signalé, seul le bruit des variantes
#: expérimentales dupliquées entre elles est écarté.
#: Raison documentée : hors_chaine_clay est déclaré HORS de la chaîne Clay
#: (instrumenté à côté, jamais dedans) ; ses doublons internes sont des
#: variantes d'exploration, pas des risques pour la chaîne.
#: L'exclusion est visible et désactivable (--inclure-hors-chaine) : le
#: témoin ne cache jamais, il cadre.
EXCLUS_DOUBLONS_DEFAUT = ("hors_chaine_clay/",)


@dataclass
class Fiche:
    """Faits structurels d'une déclaration dans un état."""
    fichier: str
    nom: str
    kind: str
    ligne: int
    hash_enonce: str
    hash_corps: str
    enonce_court: str
    corps_court: str
    nb_lignes_corps: int
    a_sorry: bool
    est_axiom: bool


@dataclass
class Observation:
    """Un fait structurel observé entre deux états. Descriptif uniquement."""
    kind: str
    fichier: str
    ligne: int
    nom: str
    saillance: int
    avant: str = ""
    apres: str = ""


def _normaliser(texte: str) -> str:
    """Normalise pour le hash : insensible à la mise en forme."""
    return " ".join(texte.split())


def _md5(texte: str) -> str:
    return hashlib.md5(_normaliser(texte).encode("utf-8")).hexdigest()


def _enonce(decl) -> str:
    """L'en-tête de la déclaration (tout ce qui précède `:=`)."""
    texte = decl.texte
    corps = decl.corps or ""
    if corps and texte.endswith(corps):
        tete = texte[: -len(corps)]
    else:
        tete = texte
    tete = tete.rstrip()
    if tete.endswith(":="):
        tete = tete[:-2].rstrip()
    return tete


def _a_sorry(corps: str) -> bool:
    """Vrai si le corps contient l'identifiant `sorry`
    (commentaires et chaînes exclus par le tokenizer)."""
    if not corps:
        return False
    toks, _, _ = tokenize(corps)
    return any(t.type == "ident" and t.text == "sorry" for t in toks)


def _fiche(fichier_rel: str, decl) -> Fiche:
    enonce = _enonce(decl)
    corps = decl.corps or ""
    return Fiche(
        fichier=fichier_rel,
        nom=decl.nom,
        kind=decl.kind,
        ligne=decl.ligne,
        hash_enonce=_md5(enonce),
        hash_corps=_md5(corps),
        enonce_court=_normaliser(enonce)[:160],
        corps_court=_normaliser(corps)[:160],
        nb_lignes_corps=corps.count("\n") + (1 if corps.strip() else 0),
        a_sorry=_a_sorry(corps),
        est_axiom=(decl.kind == "axiom"),
    )


def _fichiers_lean(racine: str):
    """Collecte les .lean sous racine (fichier ou dossier), hors .lake."""
    if os.path.isfile(racine):
        return [racine] if racine.endswith(".lean") else []
    trouves = []
    for dirpath, dirnames, filenames in os.walk(racine):
        dirnames[:] = [d for d in dirnames if d != ".lake"]
        for fn in sorted(filenames):
            if fn.endswith(".lean"):
                trouves.append(os.path.join(dirpath, fn))
    return trouves


def scanner(racine: str) -> dict:
    """Parse un état (dossier ou fichier .lean) → {(fichier, nom): Fiche}.

    Ne lève jamais sur un fichier illisible : le parseur autonome
    avertit au lieu d'échouer.
    """
    fiches = {}
    base = racine if os.path.isdir(racine) else os.path.dirname(racine) or "."
    for chemin in _fichiers_lean(racine):
        try:
            with open(chemin, encoding="utf-8") as fh:
                code = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(chemin, base)
        for decl in parse(code).declarations:
            fiches[(rel, decl.nom)] = _fiche(rel, decl)
    return fiches


def _obs(kind: str, fiche: Fiche, avant: str = "", apres: str = "") -> Observation:
    return Observation(
        kind=kind,
        fichier=fiche.fichier,
        ligne=fiche.ligne,
        nom=fiche.nom,
        saillance=POIDS_SAILLANCE[kind],
        avant=avant,
        apres=apres,
    )


def comparer(avant: dict, apres: dict,
              exclure_doublons=EXCLUS_DOUBLONS_DEFAUT) -> list:
    """Compare deux états scannés → observations rangées par saillance.

    Détecteurs purement mécaniques ; chaque observation porte les faits
    (fichier, ligne, avant/après), jamais de jugement.
    `exclure_doublons` : préfixes écartés de la détection de doublons
    (pas des autres détecteurs). Passer () pour tout inclure.
    """
    observations = []
    cles_avant = set(avant)
    cles_apres = set(apres)

    for cle in sorted(cles_avant & cles_apres):
        fa, fp = avant[cle], apres[cle]
        enonce_bouge = fa.hash_enonce != fp.hash_enonce
        corps_bouge = fa.hash_corps != fp.hash_corps
        if enonce_bouge:
            observations.append(_obs(
                "enonce_change", fp,
                avant=f"«{fa.enonce_court}»",
                apres=f"«{fp.enonce_court}»"))
            if (fa.nb_lignes_corps >= SEUIL_EFFONDREMENT_AVANT
                    and fp.nb_lignes_corps <= SEUIL_EFFONDREMENT_APRES):
                observations.append(_obs(
                    "effondrement_preuve", fp,
                    avant=f"corps {fa.nb_lignes_corps} lignes",
                    apres=f"corps {fp.nb_lignes_corps} lignes"))
        elif corps_bouge:
            observations.append(_obs(
                "corps_change", fp,
                avant=f"corps {fa.nb_lignes_corps} lignes",
                apres=f"corps {fp.nb_lignes_corps} lignes"))
        if fp.a_sorry and not fa.a_sorry:
            observations.append(_obs(
                "nouveau_sorry", fp,
                avant="sans sorry",
                apres=f"sorry en {fp.fichier}:{fp.ligne}"))

    for cle in sorted(cles_apres - cles_avant):
        fp = apres[cle]
        if fp.est_axiom:
            observations.append(_obs(
                "nouvel_axiome", fp,
                avant="absent",
                apres=f"axiom {fp.nom}"))
        elif fp.a_sorry:
            observations.append(_obs(
                "nouveau_sorry", fp,
                avant="absent",
                apres=f"sorry en {fp.fichier}:{fp.ligne}"))
        else:
            observations.append(_obs(
                "symbole_gagne", fp,
                avant="absent",
                apres=f"{fp.kind} {fp.nom}"))

    for cle in sorted(cles_avant - cles_apres):
        fa = avant[cle]
        observations.append(_obs(
            "symbole_perdu", fa,
            avant=f"{fa.kind} {fa.nom}",
            apres="absent"))

    # Doublons : même nom défini dans ≥2 fichiers de l'état APRÈS.
    # Les fichiers sous un préfixe exclu n'y participent pas (voir
    # EXCLUS_DOUBLONS_DEFAUT) ; leurs changements restent détectés par
    # les autres détecteurs.
    def _exclu(fichier):
        return any(fichier.startswith(p) for p in exclure_doublons)
    par_nom = {}
    for (fichier, nom), fiche in apres.items():
        if _exclu(fichier):
            continue
        par_nom.setdefault(nom, []).append(fiche)
    fichiers_avant_par_nom = {}
    for (fichier, nom) in avant:
        if _exclu(fichier):
            continue
        fichiers_avant_par_nom.setdefault(nom, set()).add(fichier)
    for nom in sorted(par_nom):
        fiches = par_nom[nom]
        fichiers = sorted({f.fichier for f in fiches})
        if len(fichiers) >= 2:
            rep = fiches[0]
            preexistait = len(fichiers_avant_par_nom.get(nom, set())) >= 2
            observations.append(Observation(
                kind="doublon",
                fichier=", ".join(fichiers),
                ligne=rep.ligne,
                nom=nom,
                saillance=POIDS_SAILLANCE["doublon"],
                avant="doublon préexistant" if preexistait else "défini dans 1 fichier",
                apres=f"défini dans {len(fichiers)} fichiers : "
                      + ", ".join(fichiers),
            ))

    observations.sort(key=lambda o: (-o.saillance, o.kind, o.nom))
    return observations


_LIBELLES = {
    "enonce_change": "ÉNONCÉ MODIFIÉ",
    "effondrement_preuve": "PREUVE EFFONDRÉE + ÉNONCÉ MODIFIÉ",
    "nouvel_axiome": "NOUVEL AXIOME",
    "doublon": "DÉFINITION DUPLIQUÉE",
    "nouveau_sorry": "NOUVEAU SORRY",
    "symbole_perdu": "SYMBOLE PERDU",
    "corps_change": "CORPS MODIFIÉ (énoncé identique)",
    "symbole_gagne": "SYMBOLE GAGNÉ",
}


def formater_console(observations: list) -> str:
    """Rendu console : faits uniquement, aucun langage prescriptif.

    Les doublons PRÉEXISTANTS (bruit d'historique) sont repliés en une
    ligne de décompte — le détail reste en --format json. Les NOUVEAUX
    doublons (nom passé de 1 à ≥2 fichiers) sont listés en entier : ce
    sont eux qui méritent le regard humain.
    """
    nouveaux = [o for o in observations
                if not (o.kind == "doublon" and o.avant == "doublon préexistant")]
    preexistants = len(observations) - len(nouveaux)
    lignes = [f"🔭 RADAR — {len(nouveaux)} observations structurelles"
              + (f" (+{preexistants} doublons préexistants repliés)"
                 if preexistants else "")]
    for o in nouveaux:
        libelle = _LIBELLES.get(o.kind, o.kind)
        lignes.append(f"[{o.saillance:2d}] {libelle}  {o.fichier}:{o.ligne}"
                      f"  {o.nom}")
        if o.avant:
            lignes.append(f"     avant : {o.avant}")
        if o.apres:
            lignes.append(f"     après : {o.apres}")
    return "\n".join(lignes)


def vers_dict(observations: list) -> dict:
    return {"observations": [
        {"kind": o.kind, "fichier": o.fichier, "ligne": o.ligne,
         "nom": o.nom, "saillance": o.saillance,
         "avant": o.avant, "apres": o.apres}
        for o in observations]}
