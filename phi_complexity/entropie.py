"""entropie.py — Lentille entropique sur la Sonde B : le grand livre chiffré du rétrécissement.

Pour un mécanisme, la Sonde B rend la partition de l'espace des approches
(mortes / vivantes / inexplorées) et les réfutations qui la resserrent.
Ce module chiffre le resserrement en BITS :

- distribution empirique : poids = postérieurs des croyances (phi_complexity.croyances),
  normalisés sur les nœuds de la partition (appariement exact nom_symbole == nom_nœud ;
  repli documenté : médiane des postérieurs appariés) ;
- prior de référence « phidéien » : macro-partition binaire au nombre d'or, inspirée
  de la dérivation H_φ de Tomy (vérifiée 2026-09-30) — voir PRIOR PHIDÉIEN ci-dessous ;
- H_initial → chaque réfutation (masse annulée → renormalisation) avec son ΔH → H_finale,
  sommations en double-double certifié (phi_complexity.eft.AccumulateurCertifie).

╔══════════════════════════════════════════════════════════════════════╗
║ PRIOR PHIDÉIEN — construction exacte                                  ║
║ La dérivation de Tomy : partition {W₁, W₀} du cercle,                ║
║   p = μ(W₁) = φ⁻¹ = (√5−1)/2 ≈ 0.618034   (« l'ouvert »)              ║
║   1−p = μ(W₀) = φ⁻² = (3−√5)/2 ≈ 0.381966  (« le fermé »)              ║
║   H_φ = −p·log₂p − (1−p)·log₂(1−p) = (3−φ)·log₂φ ≈ 0.959419 bit.       ║
║ Transposition à la partition d'une sonde (N nœuds) :                  ║
║   OUVERT = zones CONDITIONNEL, NON-ATTAQUÉ et statuts bruts           ║
║            (là où le chemin peut encore se trouver) ;                 ║
║   FERMÉ  = zones RÉFUTÉ et DÉMONTRÉ                                   ║
║            (terminé — question close par un statut typé,               ║
║            ou déjà traversé : hors incertitude).                       ║
║   πᵢ = φ⁻¹/N_ouvert  si i ∈ OUVERT ; πᵢ = φ⁻²/N_fermé si i ∈ FERMÉ.    ║
║ Si une macro-classe est vide, sa masse est transférée à l'autre       ║
║ (le prior reste une distribution). Sur une partition binaire          ║
║ {ouvert, fermé}, H(prior phidéien) = H_φ exactement — c'est le         ║
║ test test_h_phi_phideien.                                             ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║ INTERDICTION FORMELLE (testée mécaniquement)                          ║
║ AUCUN score A/B unique. AUCUNE probabilité P(A). L'entropie mesure    ║
║ le resserrement de l'ATTENTION DE L'INSTRUMENT — jamais la            ║
║ probabilité que (A) soit vrai. Une réfutation est déjà absolue        ║
║ mathématiquement ; l'entropie n'ajoute que la COMPARABILITÉ           ║
║ (combien de masse d'attention chaque hypothèse terminée libère).                 ║
╚══════════════════════════════════════════════════════════════════════╝

Signe de ΔH (documenté, pas un bug) :
  ΔH > 0 : la réfutation a élagué un chemin secondaire — l'attention se concentre.
  ΔH < 0 : la réfutation a terminé le favori — l'attention se disperse. Signal d'alarme,
           pas une erreur : une observation surprenante augmente l'entropie, c'est
           le comportement standard (contre-exemple minimal dans la doc et les tests :
           (0.9, 0.05, 0.05), terminer 0.9 → H : 0.569 → 1.000).
  ΔH = 0 : masse nulle terminée, ou redistribution neutre.

PORTÉE DES BORNES (comme pour l'EFT bayésien) : les log₂ sont des flottants déjà
arrondis quand l'EFT les reçoit ; la borne certifiée ne couvre que les SOMMATIONS
(normalisation, H = −Σ w·log₂w), à entrées fixées. Pour ΔH = H_avant − H_après :
|ΔH_exact − ΔH_calc| ≤ borne(H_avant) + borne(H_après) (inégalité triangulaire).

LIMITES (en toutes lettres) :
- Les bits sont aux unités de l'instrument, aveugle au contenu des preuves comme
  tout phi-complexity : seule source, le registre + les croyances ; registre
  périmé ⇒ entropie périmée.
- L'appariement nœud ↔ symbole est exact et conservateur ; le repli médiane est
  exhibé (nb_appariés / nb_nœuds dans chaque sortie).
- Les obstructions non rattachables à un nœud sont exhibées comme telles, jamais
  silencieusement ignorées.
"""

import math
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .eft import AccumulateurCertifie
from .sondes import (
    CLES_INTERDITES,
    INTERDICTION_TEXTE,
    REGISTRE_DEFAUT,
    Hypothese,
    RegistreSondes,
    resoudre_mecanisme,
    sonder,
    zone_sonde,
)

# ────────────────────────────────────────────────────────
# CONSTANTES — le nombre d'or de la dérivation H_φ (Tomy, vérifiée 2026-09-30)
# ────────────────────────────────────────────────────────

PHI = (1.0 + math.sqrt(5.0)) / 2.0
P_OUVERT_PHIDEIEN = 1.0 / PHI          # φ⁻¹ ≈ 0.618034
P_FERME_PHIDEIEN = 1.0 / (PHI * PHI)   # φ⁻² ≈ 0.381966
H_PHI_ANALYTIQUE = (3.0 - PHI) * (math.log(PHI) / math.log(2.0))  # ≈ 0.959419 bit

# ────────────────────────────────────────────────────────
# ÉTALONS « BIT GÉNÉRATEUR » (étude Tomy, arithmétique vérifiée 2026-09-30)
# ────────────────────────────────────────────────────────
# 1. Coût de booléanisation : forcer la distribution dorée (φ⁻¹, φ⁻²) vers
#    l'équiprobable (1/2, 1/2) détruit exactement
#      ΔH = 1 − H_φ ≈ 0.0405812718 bits.
#    C'est l'étalon de ce que coûte l'effondrement d'une partition typée
#    en booléen — l'effondrement cargo-cult refusé par Tomy (DÉMONTRÉ /
#    CONDITIONNEL écrasés en un seul « prouvé »).
COUT_BOOLEANISATION_ETALON = 1.0 - H_PHI_ANALYTIQUE  # ≈ 0.0405812718 bit

# 2. Entropie topologique du golden shift : Perron-Frobenius sur A=[[1,1],[1,0]],
#    rayon spectral φ, d'où h_top = log₂φ ≈ 0.6942419136 bit/symbole.
#    Taux de croissance de référence pour les routes admissibles.
H_TOP_GOLDEN_SHIFT = math.log2(PHI)  # ≈ 0.6942419136

# 3. Verrou combinatoire : sous contrainte « pas de 11 consécutifs » (l'analogue
#    formel d'une réfutation = transition interdite), le nombre de routes
#    admissibles de longueur k vaut exactement N(k) = F_{k+2}
#    (vérifié : N(10) = F_12 = 144). Réponse instrumentale à R4 : explosion
#    combinatoire GUIDÉE (élagage par les réfutations), jamais naïve.

ZONES_FERMEES = {"RÉFUTÉ", "DÉMONTRÉ"}  # mort définitif ou déjà traversé

_RE_HNUM = re.compile(r"(?<![A-Za-z0-9_])(H\d+[a-z]?)(?![A-Za-z0-9_])")
_RE_BACKTICK = re.compile(r"`([^`]{1,80})`")

LIMITE_ENTROPIE = (
    "ENTROPIE SONDE B — limites : les bits mesurent le resserrement de "
    "l'ATTENTION DE L'INSTRUMENT, pas la probabilité que (A) soit vrai "
    "(instrument aveugle au contenu des preuves ; registre + croyances comme "
    "seules sources). Une réfutation est déjà absolue mathématiquement ; "
    "l'entropie n'ajoute que la comparabilité entre réfutations. "
    "ΔH < 0 = la réfutation a tué le favori (dispersion, signal d'alarme), "
    "ΔH > 0 = élagage d'un chemin secondaire (concentration). " + INTERDICTION_TEXTE
)

DOSSIER_DEFAUT = os.path.expanduser("~/workspace/lean-navier-stokes")


# ────────────────────────────────────────────────────────
# DISTRIBUTIONS
# ────────────────────────────────────────────────────────

def _posterieurs_symboles(dossier: str) -> Dict[str, float]:
    """nom_symbole → postérieur (mode défaut flottant, comme `phi chemins`).

    Lecture seule : ne modifie aucun JSON, aucun classement.
    """
    from .croyances import chemins_croyants
    carte = chemins_croyants(dossier, complet=True)
    return {s["nom"]: float(s["posterior"]) for s in carte.get("symboles", [])}


def posterieurs_symboles(dossier: str) -> Dict[str, float]:
    """Alias public de _posterieurs_symboles (pour ancrage.py : une seule
    indexation partagée entre tous les mécanismes de `phi ou-aller`)."""
    return _posterieurs_symboles(dossier)


def distribution_empirique(noeuds: List[Hypothese],
                           posterieurs: Dict[str, float]
                           ) -> Tuple[List[float], dict]:
    """Poids = postérieurs normalisés sur les nœuds.

    Appariement exact : nom_nœud == nom_symbole. Repli documenté pour les
    nœuds sans symbole : médiane des postérieurs appariés (jamais 0, jamais
    une précision inventée). Si AUCUN appariement : uniforme.
    """
    bruts: List[float] = []
    apparies = 0
    for h in noeuds:
        p = posterieurs.get(h.nom)
        if p is None:
            bruts.append(math.nan)  # marqué, remplacé ci-dessous
        else:
            apparies += 1
            bruts.append(max(p, 1e-12))
    apparies_vals = [b for b in bruts if not math.isnan(b)]
    if apparies_vals:
        repli = float(statistics.median(apparies_vals))
    else:
        repli = 1.0  # aucun appariement : uniforme (exhibé)
    bruts = [repli if math.isnan(b) else b for b in bruts]
    total = math.fsum(bruts)
    poids = [b / total for b in bruts] if total > 0 else [1.0 / len(bruts)] * len(bruts)
    meta = {
        "nb_noeuds": len(noeuds),
        "nb_apparies": apparies,
        "repli": ("mediane_posterieurs" if apparies_vals and apparies < len(noeuds)
                  else ("uniforme_aucun_appariement" if not apparies_vals else "aucun")),
        "valeur_repli": repli,
    }
    return poids, meta


def prior_phideien(noeuds: List[Hypothese]) -> List[float]:
    """Prior de référence au nombre d'or — construction exacte, voir module docstring."""
    n = len(noeuds)
    if n == 0:
        return []
    ouvert = [i for i, h in enumerate(noeuds) if zone_sonde(h) not in ZONES_FERMEES]
    ferme = [i for i in range(n) if i not in set(ouvert)]
    pi = [0.0] * n
    if ouvert and ferme:
        for i in ouvert:
            pi[i] = P_OUVERT_PHIDEIEN / len(ouvert)
        for i in ferme:
            pi[i] = P_FERME_PHIDEIEN / len(ferme)
    elif ouvert:
        for i in ouvert:
            pi[i] = 1.0 / len(ouvert)
    else:
        for i in ferme:
            pi[i] = 1.0 / len(ferme)
    return pi


# ────────────────────────────────────────────────────────
# ENTROPIE CERTIFIÉE (EFT)
# ────────────────────────────────────────────────────────

def _termes_entropie(poids: List[float]) -> List[float]:
    """Termes tᵢ = −wᵢ·log₂(wᵢ) (log₂ = capteur flottant, voir PORTÉE DES BORNES)."""
    termes = []
    for w in poids:
        if w <= 0.0:
            continue
        termes.append(-w * math.log2(w))
    return termes


def entropie_certifiee(poids: List[float]) -> Tuple[float, float]:
    """H = −Σ w·log₂w en double-double certifié → (valeur, borne).

    INVARIANT : |H_exact − (hi+lo)| ≤ borne, sous les hypothèses de eft.py
    (sommation à entrées fixées ; les log₂ sont des flottants amont).
    """
    acc = AccumulateurCertifie()
    for t in _termes_entropie(poids):
        acc.ajouter(t)
    hi, lo = acc.valeur()
    return hi + lo, acc.borne_erreur()


def renormaliser(poids: List[float], tues: List[int]) -> List[float]:
    """Annule la masse des indices tués, renormalise (EFT pour la somme)."""
    acc = AccumulateurCertifie()
    vivants = [w if i not in set(tues) else 0.0 for i, w in enumerate(poids)]
    for w in vivants:
        acc.ajouter(w)
    total = acc.valeur()[0] + acc.valeur()[1]
    if total <= 0.0:
        return [0.0] * len(poids)
    return [w / total for w in vivants]


# ────────────────────────────────────────────────────────
# RATTACHEMENT OBSTRUCTION → NŒUDS
# ────────────────────────────────────────────────────────

def _cles_obstruction(titre: str) -> List[str]:
    """Clés d'appariement d'une obstruction : H-numéros + noms entre backticks."""
    cles = _RE_HNUM.findall(titre)
    cles += [c.strip().strip("`") for c in _RE_BACKTICK.findall(titre)]
    return cles


def rattacher_obstruction(titre: str, noeuds: List[Hypothese]) -> List[int]:
    """Indices des nœuds tués par l'obstruction (appariement conservateur).

    Règles : H-numéro exact, ou nom exact entre backticks, ou nom de nœud
    apparaissant tel quel dans le titre (insensible à la casse sur les noms
    CamelCase/snake_case de longueur ≥ 4). Rien d'inventé.
    """
    tues: List[int] = []
    cles = _cles_obstruction(titre)
    titre_bas = titre.lower()
    for i, h in enumerate(noeuds):
        nom = h.nom
        if nom in cles:
            tues.append(i)
            continue
        # H-numéro du nœud lui-même (ex. nœud "H30")
        mh = _RE_HNUM.fullmatch(nom)
        if mh and mh.group(1) in cles:
            tues.append(i)
            continue
        if len(nom) >= 4 and nom.lower() in titre_bas:
            tues.append(i)
    return sorted(set(tues))


# ────────────────────────────────────────────────────────
# TRACE DU RESSERREMENT
# ────────────────────────────────────────────────────────

@dataclass
class EvenementResserrement:
    titre: str
    noeuds_tues: List[str] = field(default_factory=list)
    masse_tuee: float = 0.0
    delta_h_bits: float = 0.0
    borne_delta_h: float = 0.0
    rattache: bool = True
    note: str = ""

    def vers_dict(self) -> dict:
        # NOTE : aucune clé de CLES_INTERDITES ne doit jamais apparaître ici.
        return {
            "titre": self.titre,
            "noeuds_tues": list(self.noeuds_tues),
            "masse_tuee": self.masse_tuee,
            "delta_h_bits": self.delta_h_bits,
            "borne_delta_h": self.borne_delta_h,
            "rattache": self.rattache,
            "note": self.note,
        }


@dataclass
class ResultatEntropie:
    mecanisme: str
    type_mecanisme: str
    noeuds: List[str] = field(default_factory=list)
    zones: List[str] = field(default_factory=list)
    h_initial_bits: float = 0.0
    borne_h_initial: float = 0.0
    h_ref_phideien_bits: float = 0.0
    evenements: List[EvenementResserrement] = field(default_factory=list)
    h_finale_bits: float = 0.0
    borne_h_finale: float = 0.0
    meta_distribution: dict = field(default_factory=dict)
    booleanisation: dict = field(default_factory=dict)
    routes: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    integrite: dict = field(default_factory=dict)

    def vers_dict(self) -> dict:
        # NOTE : aucune clé de CLES_INTERDITES ne doit jamais apparaître ici.
        somme_deltas = math.fsum(e.delta_h_bits for e in self.evenements)
        return {
            "mecanisme": self.mecanisme,
            "type_mecanisme": self.type_mecanisme,
            "partition": [
                {"nom": n, "zone": z} for n, z in zip(self.noeuds, self.zones)
            ],
            "distribution": dict(self.meta_distribution),
            "h_initial_bits": self.h_initial_bits,
            "borne_h_initial": self.borne_h_initial,
            "h_ref_phideien_bits": self.h_ref_phideien_bits,
            "trace_resserrement": [e.vers_dict() for e in self.evenements],
            "h_finale_bits": self.h_finale_bits,
            "borne_h_finale": self.borne_h_finale,
            "additivite": {
                "somme_deltas_bits": somme_deltas,
                "h_initial_moins_finale_bits": self.h_initial_bits - self.h_finale_bits,
                "note": "Télescopage : ΣΔH = H_initial − H_finale par construction "
                        "(écart résiduel = arrondi flottant du chaînage).",
            },
            "lecture": (
                "ΔH > 0 : élagage d'un chemin secondaire (concentration). "
                "ΔH < 0 : la réfutation a tué le favori (dispersion — signal "
                "d'alarme, pas une erreur). Les bits mesurent l'attention de "
                "l'instrument, jamais P(A)."
            ),
            "booleanization_cost": dict(self.booleanisation),
            "routes_admissibles": dict(self.routes),
            "etalons_bit_generateur": {
                "cout_booleanisation_or_bits": COUT_BOOLEANISATION_ETALON,
                "h_top_golden_shift_bits_par_symbole": H_TOP_GOLDEN_SHIFT,
                "note": ("Étalons de l'étude « Bit Générateur » (Tomy, arithmétique "
                         "vérifiée 2026-09-30) : ils structurent la mesure de "
                         "l'instrument, ils ne prouvent rien sur les mathématiques."),
            },
            "notes": list(self.notes),
            "notes": list(self.notes),
            "integrite": dict(self.integrite),
            "limites": LIMITE_ENTROPIE,
            "interdiction": INTERDICTION_TEXTE,
            "horodatage": datetime.now(timezone.utc).isoformat(),
        }


def tracer_resserrement(noeuds: List[Hypothese], poids: List[float],
                       obstructions: List[Tuple[str, str]]
                       ) -> Tuple[List[EvenementResserrement], float, float,
                                  float, float, List[str]]:
    """Applique les événements dans l'ordre.

    Rend (événements, H_init, borne_init, H_finale, borne_finale, notes).
    Les obstructions non rattachables sont exhibées (rattache=False), jamais
    silencieusement ignorées.
    """
    notes: List[str] = []
    h_init, borne_init = entropie_certifiee(poids)
    evenements: List[EvenementResserrement] = []
    w = list(poids)
    h_courant, borne_courant = h_init, borne_init
    for titre, provenance in obstructions:
        tues = rattacher_obstruction(titre, noeuds)
        if not tues:
            evenements.append(EvenementResserrement(
                titre=titre, rattache=False,
                note=(f"Obstruction {provenance} non rattachable à un nœud de "
                      "la partition — exhibée, non comptée dans la trace."),
            ))
            notes.append(f"Non rattaché : {titre[:80]}")
            continue
        masse = math.fsum(w[i] for i in tues)
        w_apres = renormaliser(w, tues)
        h_apres, borne_apres = entropie_certifiee(w_apres)
        delta = h_courant - h_apres
        borne_delta = borne_courant + borne_apres  # inégalité triangulaire
        sens = ("concentration (élagage secondaire)" if delta > 0
                else ("dispersion — favori tué (alarme)" if delta < 0
                      else "neutre"))
        evenements.append(EvenementResserrement(
            titre=titre,
            noeuds_tues=[noeuds[i].nom for i in tues],
            masse_tuee=masse,
            delta_h_bits=delta,
            borne_delta_h=borne_delta,
            note=f"{provenance} · {sens}",
        ))
        w, h_courant, borne_courant = w_apres, h_apres, borne_apres
    return evenements, h_init, borne_init, h_courant, borne_courant, notes


def entropie_depuis_sonde(mecanisme: str, dossier: str = DOSSIER_DEFAUT,
                          chemin_registre: str = REGISTRE_DEFAUT,
                          registre: Optional["RegistreSondes"] = None,
                          posterieurs: Optional[Dict[str, float]] = None,
                          ) -> ResultatEntropie:
    """Point d'entrée : sonde → partition → distribution → trace. Lecture seule.

    `registre` et `posterieurs` peuvent être fournis pré-chargés (ancrage.py :
    une seule indexation des croyances pour N mécanismes — `phi ou-aller`).
    Comportement par défaut inchangé sinon.
    """
    if registre is None:
        registre = RegistreSondes().charger(chemin_registre)
    resolution = resoudre_mecanisme(mecanisme, registre)
    res = ResultatEntropie(mecanisme=mecanisme,
                           type_mecanisme=resolution["type"],
                           notes=list(resolution["notes"]))
    if resolution["type"] == "inconnu" or not resolution["trous"]:
        res.notes.append("Mécanisme non résolu ou sans trou rattaché : "
                         "aucune partition, aucune entropie (pas de crash).")
        return res
    sonde = sonder(mecanisme, registre, exact=False)
    noeuds = list(sonde.route_a) + list(sonde.terra_incognita)
    # Dédupliquer par nom (un nœud peut apparaître route + terra).
    vus, uniques = set(), []
    for h in noeuds:
        if h.nom not in vus:
            vus.add(h.nom)
            uniques.append(h)
    noeuds = uniques
    if not noeuds:
        res.notes.append("Partition vide : aucune entropie calculable.")
        return res
    res.noeuds = [h.nom for h in noeuds]
    res.zones = [zone_sonde(h) for h in noeuds]

    if posterieurs is None:
        posterieurs = _posterieurs_symboles(dossier)
    poids, meta = distribution_empirique(noeuds, posterieurs)
    res.meta_distribution = meta
    res.h_ref_phideien_bits, _ = entropie_certifiee(prior_phideien(noeuds))

    obstructions: List[Tuple[str, str]] = [
        (o.titre, f"registre §5 (ch.{o.chantier})" if o.chantier else "registre §5")
        for o in sonde.obstructions_b
    ]
    for r in sonde.refutes_journal_b:
        obstructions.append(
            (f"{r.get('identifiant', '?')} — {', '.join(r.get('verdicts', []))}",
             f"journal §12 (ch.{r.get('chantier', '?')})"))

    evenements, h_init, borne_init, h_finale, borne_finale, notes_tr = \
        tracer_resserrement(noeuds, poids, obstructions)
    res.notes.extend(notes_tr)
    res.h_initial_bits = h_init
    res.borne_h_initial = borne_init
    res.evenements = evenements
    res.h_finale_bits = h_finale
    res.borne_h_finale = borne_finale
    # Coût de booléanisation : masses empiriques agrégées par zone typée.
    res.booleanisation = cout_booleanisation(masses_par_zone(noeuds, poids))
    # Routes admissibles : modèle de référence en couches + étalon shift doré.
    res.routes = dag_couches_attaque(noeuds)
    # Note instrumentale : obstructions absentes du registre mais connues sur disque.
    res.notes.append(
        "Constat d'instrument : `BKM_Bypass_Refutation.lean` existe sur disque "
        "mais n'est mentionné NI au §5 NI au §12 du registre — la sonde (et donc "
        "cette lentille) ne peut pas le tracer. De même, l'obstruction 15.19 "
        "(« aucun préfacteur pur ne supprime M(T) », manuscrit v7) n'est pas "
        "identifiable au registre. Recommandation : les y enregistrer (décision "
        "Tomy — ce module ne touche au registre qu'en lecture).")
    return res


# ────────────────────────────────────────────────────────
# COÛT DE BOOLÉANISATION — ce que détruit l'effondrement en booléen
# ────────────────────────────────────────────────────────
# Théorème utilisé (inégalité de coarsening) : l'entropie d'une partition
# grossière est TOUJOURS ≤ celle de la partition fine. Donc
#   coût = H_4zones − H_booléen ≥ 0, avec égalité ssi l'effondrement est
#   déjà une identité sur le support.
# Choix de l'effondrement documenté : {"tient"} = DÉMONTRÉ + CONDITIONNEL
# contre {"ne tient pas"} = RÉFUTÉ + NON-ATTAQUÉ — exactement l'écrasement
# cargo-cult refusé (« DÉMONTRÉ/CONDITIONNEL en un seul prouvé »).

ZONES_TIENT = ("DÉMONTRÉ", "CONDITIONNEL")


def masses_par_zone(noeuds: List[Hypothese], poids: List[float]
                    ) -> Dict[str, float]:
    """Masse empirique agrégée par zone typée (DÉMONTRÉ/CONDITIONNEL/RÉFUTÉ/NON-ATTAQUÉ).

    Les statuts bruts (POINTEUR, PARAMÈTRE…) sont agrégés sous leur nom propre
    et IGNORÉS par l'effondrement booléen (documenté, jamais forcés).
    """
    masses: Dict[str, float] = {}
    for h, w in zip(noeuds, poids):
        z = zone_sonde(h)
        masses[z] = masses.get(z, 0.0) + w
    return masses


def cout_booleanisation(masses: Dict[str, float]) -> dict:
    """Coût en bits de l'effondrement typé → booléen + étalon-or.

    Rend un dict JSON-sérialisable (aucune clé interdite).
    """
    zones4 = ["DÉMONTRÉ", "CONDITIONNEL", "RÉFUTÉ", "NON-ATTAQUÉ"]
    m4 = [max(masses.get(z, 0.0), 0.0) for z in zones4]
    total = math.fsum(m4)
    if total <= 0.0:
        h4, b4, h2, b2, cout = 0.0, 0.0, 0.0, 0.0, 0.0
    else:
        p4 = [m / total for m in m4]
        h4, b4 = entropie_certifiee(p4)
        m_tient = m4[0] + m4[1]
        m_reste = m4[2] + m4[3]
        p2 = [m_tient / total, m_reste / total]
        h2, b2 = entropie_certifiee(p2)
        cout = h4 - h2  # ≥ 0 par coarsening (à l'arrondi près)
        if cout < 0 and cout > -1e-12:
            cout = 0.0  # bruit d'arrondi sous le seuil
    return {
        "masses_zones": {z: masses.get(z, 0.0) for z in zones4},
        "h_4_zones_bits": h4,
        "borne_h_4": b4,
        "h_booleen_bits": h2,
        "borne_h_booleen": b2,
        "booleanization_cost_bits": cout,
        "borne_cout": b4 + b2,
        "etalon_or_bits": COUT_BOOLEANISATION_ETALON,
        "ratio_vs_etalon": (cout / COUT_BOOLEANISATION_ETALON
                            if COUT_BOOLEANISATION_ETALON > 0 else 0.0),
        "effondrement": ("{DÉMONTRÉ, CONDITIONNEL} → « tient » contre "
                         "{RÉFUTÉ, NON-ATTAQUÉ} → « ne tient pas » "
                         "(écrasement cargo-cult refusé)"),
        "lecture": ("Combien d'étalons-or d'information détruit ici "
                    "l'effondrement en booléen. Le typage n'est pas du "
                    "décor : il porte des bits."),
    }


# ────────────────────────────────────────────────────────
# SHIFT DORÉ — étalon combinatoire exact (Perron-Frobenius)
# ────────────────────────────────────────────────────────

def fib_exact(n: int) -> int:
    """F_n exact (entiers arbitraires, doublement rapide). F_0=0, F_1=1."""
    if n < 0:
        raise ValueError("fib_exact : n ≥ 0 requis")
    def _fd(k: int) -> Tuple[int, int]:
        if k == 0:
            return (0, 1)
        a, b = _fd(k >> 1)
        c = a * ((b << 1) - a)
        d = a * a + b * b
        return (d, c + d) if k & 1 else (c, d)
    return _fd(n)[0]


def routes_golden_shift(k: int) -> int:
    """Nombre EXACT de routes binaires de longueur k sans « 11 » : F_{k+2}.

    C'est le modèle de référence des routes admissibles sous interdiction
    (une réfutation = transition interdite). Vérifié : N(10) = F_12 = 144.
    """
    if k < 0:
        raise ValueError("routes_golden_shift : k ≥ 0 requis")
    return fib_exact(k + 2)


def diagnostic_branchement(n_k: int, n_k_suivant: int) -> float:
    """Taux de branchement mesuré en bits/symbole : log₂(N_{k+1}/N_k).

    À comparer à h_top = log₂φ ≈ 0.6942 (l'étalon) : un DAG qui branche
    plus vite que l'étalon explore plus que le modèle à interdiction dorée.
    """
    if n_k <= 0 or n_k_suivant <= 0:
        raise ValueError("diagnostic_branchement : comptes > 0 requis")
    return math.log2(n_k_suivant / n_k)


def compter_routes_dag(predecesseurs: Dict[str, List[str]],
                       interdits: Optional[set] = None) -> dict:
    """Comptage EXACT (entiers) des routes source→puits d'un DAG, nœuds
    interdits retirés. Récurrence linéaire exacte par ordre topologique (Kahn).

    predecesseurs : nœud → liste de ses prédécesseurs directs.
    Rend {"nb_routes": int, "comptes": {nœud: int}, "interdits_retires": [...]}.
    Cycle détecté → ValueError documentée (jamais de résultat faux).
    """
    interdits = set(interdits or [])
    noeuds = [n for n in predecesseurs if n not in interdits]
    preds = {n: [p for p in predecesseurs[n] if p not in interdits] for n in noeuds}
    # Kahn
    indeg = {n: 0 for n in noeuds}
    succs: Dict[str, List[str]] = {n: [] for n in noeuds}
    for n in noeuds:
        for p in preds[n]:
            if p in indeg:
                succs[p].append(n)
                indeg[n] += 1
    file = sorted(n for n in noeuds if indeg[n] == 0)
    ordre: List[str] = []
    while file:
        n = file.pop(0)
        ordre.append(n)
        for s in sorted(succs[n]):
            indeg[s] -= 1
            if indeg[s] == 0:
                file.append(s)
    if len(ordre) != len(noeuds):
        raise ValueError("compter_routes_dag : cycle détecté — pas de comptage "
                         "sur un graphe non-DAG (résultat faux interdit).")
    comptes: Dict[str, int] = {}
    for n in ordre:
        if not preds[n]:
            comptes[n] = 1  # source : une route vide y mène
        else:
            comptes[n] = sum(comptes[p] for p in preds[n])
    puits = [n for n in noeuds if not succs[n]]
    total = sum(comptes[p] for p in puits)
    return {
        "nb_routes": total,
        "comptes": comptes,
        "puits": puits,
        "interdits_retires": sorted(interdits),
        "methode": "récurrence linéaire exacte (Kahn + DP), entiers arbitraires",
    }


def dag_couches_attaque(noeuds: List[Hypothese]) -> dict:
    """DAG de référence en couches : les couches sont les rangs d'attaque du
    registre (portes → murs → forteresses → hors-classe → non tagué), arêtes
    biparties complètes entre couches consécutives, nœuds RÉFUTÉ retirés
    (transitions interdites).

    C'est un MODÈLE DE RÉFÉRENCE exhibé comme tel — pas le graphe réel des
    dépendances (§3 du registre : art ASCII, non parsé honnêtement, voir doc).
    Il sert à chiffrer l'élagage : combien de routes l'interdiction retire.
    """
    from .sondes import _RANG_TAG  # source unique de vérité des rangs
    couches: Dict[int, List[str]] = {}
    for h in noeuds:
        if zone_sonde(h) == "RÉFUTÉ":
            continue  # transition interdite : nœud retiré
        r = _RANG_TAG.get(h.tag, 4)
        couches.setdefault(r, []).append(h.nom)
    rangs = sorted(couches)
    preds: Dict[str, List[str]] = {}
    for i, r in enumerate(rangs):
        for nom in sorted(couches[r]):
            preds[nom] = sorted(couches[rangs[i - 1]]) if i > 0 else []
    comptage = compter_routes_dag(preds, interdits=set())
    k = len(rangs)
    n_ref = routes_golden_shift(k)
    taux_mesure = None
    if k >= 2:
        # Taux par couche : (nb_routes)^{1/(k-1)} comparé à φ (taux PF).
        taux_mesure = math.log2(comptage["nb_routes"] ** (1.0 / (k - 1))) \
            if comptage["nb_routes"] > 0 else None
    return {
        "modele": ("couches = rangs d'attaque (portes→murs→forteresses→"
                   "hors-classe→non tagué) ; arêtes biparties complètes ; "
                   "nœuds RÉFUTÉ retirés — MODÈLE DE RÉFÉRENCE, pas le graphe réel"),
        "nb_couches": k,
        "tailles_couches": [len(couches[r]) for r in rangs],
        "nb_routes_admissibles": comptage["nb_routes"],
        "reference_golden_shift": {
            "k": k,
            "N_k_equals_F_k_plus_2": n_ref,
            "h_top_bits_par_symbole": H_TOP_GOLDEN_SHIFT,
        },
        "taux_branchement_mesure_bits": taux_mesure,
        "lecture": ("Le comptage est guidé par les interdictions (R4 : jamais "
                    "d'explosion naïve). h_top = log₂φ est l'étalon : un taux "
                    "mesuré au-dessus signale un branchement plus fort que le "
                    "modèle à interdiction dorée."),
    }


# ────────────────────────────────────────────────────────
# RENDU CONSOLE
# ────────────────────────────────────────────────────────

def rendre_entropie_console(res: ResultatEntropie) -> str:
    """Rendu lisible : H_initial → chaque réfutation et son ΔH → H_finale."""
    L = []
    L.append("╔══════════════════════════════════════════════════════════════╗")
    L.append("║         PHI-COMPLEXITY — LENTILLE ENTROPIQUE 📐               ║")
    L.append("║     le grand livre chiffré du rétrécissement (Sonde B)       ║")
    L.append("╚══════════════════════════════════════════════════════════════╝")
    L.append("")
    L.append(f"Mécanisme : {res.mecanisme}  [{res.type_mecanisme}]")
    if not res.noeuds:
        L.append("Aucune partition : " + " ; ".join(res.notes))
        return "\n".join(L)
    md = res.meta_distribution
    L.append(f"Partition : {len(res.noeuds)} nœuds "
             f"({md.get('nb_apparies', 0)} appariés aux croyances, "
             f"repli : {md.get('repli', '?')})")
    L.append("")
    L.append(f"  H_initial (attention de l'instrument) : "
             f"{res.h_initial_bits:.6f} bits  [borne ±{res.borne_h_initial:.2e}]")
    L.append(f"  H_ref phidéien (φ⁻¹ ouvert / φ⁻² fermé) : "
             f"{res.h_ref_phideien_bits:.6f} bits")
    L.append("")
    L.append("┌─ TRACE DU RESSERREMENT")
    if not res.evenements:
        L.append("│  (aucune obstruction rattachée — rien à resserrer)")
    for e in res.evenements:
        if not e.rattache:
            L.append(f"│  ○ {e.titre[:75]}")
            L.append(f"│      → {e.note}")
            continue
        signe = "+" if e.delta_h_bits >= 0 else ""
        L.append(f"│  ✗ {e.titre[:75]}")
        L.append(f"│      tue : {', '.join(e.noeuds_tues)} "
                 f"(masse {e.masse_tuee:.4f})")
        L.append(f"│      ΔH = {signe}{e.delta_h_bits:.6f} bits "
                 f"[borne ±{e.borne_delta_h:.2e}] — {e.note}")
    L.append("│")
    L.append(f"│  H_finale : {res.h_finale_bits:.6f} bits  "
             f"[borne ±{res.borne_h_finale:.2e}]")
    add = res.vers_dict()["additivite"]
    L.append(f"│  Télescopage : ΣΔH = {add['somme_deltas_bits']:.6f} ; "
             f"H_init − H_fin = {add['h_initial_moins_finale_bits']:.6f}")
    L.append("└─────────────────────────────────────────────────────────────")
    L.append("")
    # ── Coût de booléanisation ──
    bc = res.booleanisation or {}
    L.append("┌─ COÛT DE BOOLÉANISATION (typé → booléen)")
    if bc:
        L.append(f"│  masses : " + ", ".join(
            f"{z}={bc['masses_zones'][z]:.4f}" for z in
            ("DÉMONTRÉ", "CONDITIONNEL", "RÉFUTÉ", "NON-ATTAQUÉ")))
        L.append(f"│  H_4zones = {bc['h_4_zones_bits']:.6f} bits ; "
                 f"H_booléen = {bc['h_booleen_bits']:.6f} bits")
        L.append(f"│  coût = {bc['booleanization_cost_bits']:.6f} bits "
                 f"[borne ±{bc['borne_cout']:.2e}]")
        L.append(f"│  étalon-or (1−H_φ) = {bc['etalon_or_bits']:.6f} bits → "
                 f"ratio = {bc['ratio_vs_etalon']:.3f} étalons")
        L.append(f"│  {bc['effondrement']}")
    else:
        L.append("│  (partition vide — pas de coût calculable)")
    L.append("└─────────────────────────────────────────────────────────────")
    L.append("")
    # ── Routes admissibles ──
    rt = res.routes or {}
    L.append("┌─ ROUTES ADMISSIBLES (verrou combinatoire, R4 guidée)")
    if rt:
        L.append(f"│  modèle : {rt['modele'][:88]}…")
        L.append(f"│  {rt['nb_couches']} couches, tailles {rt['tailles_couches']} → "
                 f"{rt['nb_routes_admissibles']} routes admissibles (exact)")
        rg = rt["reference_golden_shift"]
        L.append(f"│  étalon shift doré : N({rg['k']}) = F_{rg['k']+2} = "
                 f"{rg['N_k_equals_F_k_plus_2']} ; "
                 f"h_top = log₂φ = {rg['h_top_bits_par_symbole']:.6f} bit/symbole")
        tm = rt.get("taux_branchement_mesure_bits")
        L.append(f"│  taux de branchement mesuré : "
                 f"{tm:.6f} bits/couche" if tm is not None else
                 "│  taux de branchement mesuré : n/a (une seule couche)")
        L.append(f"│  {rt['lecture'][:120]}…")
    else:
        L.append("│  (partition vide — pas de routes)")
    L.append("└─────────────────────────────────────────────────────────────")
    L.append("")
    for n in res.notes:
        L.append(f"  ‣ {n}")
    L.append("")
    L.append(INTERDICTION_TEXTE)
    L.append("Limites : " + LIMITE_ENTROPIE[:280] + "… (voir ENTROPIE_SONDEB_20260930.md)")
    return "\n".join(L)
