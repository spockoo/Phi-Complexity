"""tests/test_ancrage.py — Tests de l'ancrage entropique dans la chaîne des sondes.

Chantier ANCRAGE-ENTROPIE (2026-09-30) : la lentille (entropie.py) est cousue
à la chaîne des sondes (sondes.py) par IMPORT DIRECT (jamais de subprocess) —
sections `entropie` par nœud, doubles verdicts rattachés au niveau nœud
(ch.139), et `phi ou-aller` (liste d'attention ordonnée, pas un score).

Stratégie : registre réel + postérieurs réels (indexation partagée, une fois
par session de tests). L'INTERDICTION FORMELLE est testée par balayage des
CLÉS du JSON (pas de la prose : le texte de l'interdiction contient « P(A) »
en toutes lettres — le test vise les clés, jamais ce texte).
"""
import os
import sys

import pytest

from phi_complexity import ancrage, sondes
from phi_complexity.ancrage import (
    attacher_entropie,
    construire_ou_aller,
    rendre_ou_aller_console,
    section_entropie_noeud,
)
from phi_complexity.core import VERSION
from phi_complexity.entropie import (
    COUT_BOOLEANISATION_ETALON,
    DOSSIER_DEFAUT,
    posterieurs_symboles,
)
from phi_complexity.sondes import (
    CLES_INTERDITES,
    REGISTRE_DEFAUT,
    Obstruction,
    RegistreSondes,
    est_double_verdict,
    rendre_sonde_console,
    sonder,
    versions_double_verdict,
)

REGISTRE = REGISTRE_DEFAUT


@pytest.fixture(scope="module")
def registre():
    assert os.path.isfile(REGISTRE), f"registre introuvable : {REGISTRE}"
    return RegistreSondes().charger(REGISTRE)


@pytest.fixture(scope="module")
def posterieurs():
    assert os.path.isdir(DOSSIER_DEFAUT), f"dossier introuvable : {DOSSIER_DEFAUT}"
    return posterieurs_symboles(DOSSIER_DEFAUT)


@pytest.fixture(scope="module")
def sonde_ancree(registre, posterieurs):
    s = sonder("energy_identity", registre)
    return attacher_entropie(s, registre, posterieurs)


def _cles(d):
    """Toutes les clés d'un JSON (récursif) — le balayage porte sur les CLÉS,
    jamais sur la prose (le texte de l'interdiction contient « P(A) »)."""
    if isinstance(d, dict):
        for k, v in d.items():
            yield k
            yield from _cles(v)
    elif isinstance(d, list):
        for v in d:
            yield from _cles(v)


def _assert_pas_de_cles_interdites(d, contexte):
    cles = {str(k).lower() for k in _cles(d)}
    violees = cles & {c.lower() for c in CLES_INTERDITES}
    assert not violees, f"clés interdites ({contexte}) : {violees}"


# ────────────────────────────────────────────────────────
# 1. ANCRAGE — sections entropie par nœud (import direct)
# ────────────────────────────────────────────────────────

class TestAncrageSections:
    def test_sonde_json_expose_section_entropie_mecanisme(self, sonde_ancree):
        d = sonde_ancree.vers_dict()
        assert "entropie" in d
        ent = d["entropie"]
        for champ in ("h_initial_bits", "trace_resserrement", "h_finale_bits",
                      "booleanization_cost", "routes_admissibles"):
            assert champ in ent, f"champ manquant : {champ}"

    def test_noeuds_ont_section_entropie(self, sonde_ancree):
        d = sonde_ancree.vers_dict()
        noeuds = d["sonde_a"]["route_vers_inconditionnel"]
        assert noeuds, "route vide — rien à ancrer"
        pleines = 0
        for n in noeuds:
            assert "entropie" in n, f"nœud sans section : {n['nom']}"
            sec = n["entropie"]
            if "h_initial_bits" in sec:
                pleines += 1
                assert "booleanization_cost" in sec
                bc = sec["booleanization_cost"]
                assert "booleanization_cost_bits" in bc
                assert "ratio_vs_etalon" in bc
                # Le ratio est bien en étalons-or (coût / 0.0405812718).
                assert abs(bc["ratio_vs_etalon"]
                           - bc["booleanization_cost_bits"] / COUT_BOOLEANISATION_ETALON) < 1e-9
            else:
                # Nœud non résolvable : section vide DOCUMENTÉE, pas un crash.
                assert "note" in sec, f"section ni pleine ni documentée : {n['nom']}"
        assert pleines > 0, "aucune section pleine — l'ancrage ne chiffre rien"

    def test_section_entropie_noeud_champs(self, registre, posterieurs):
        sec = section_entropie_noeud("BKM_criterion", registre, posterieurs)
        for champ in ("h_initial_bits", "h_finale_bits", "delta_h_total_bits",
                      "trace_resserrement", "booleanization_cost",
                      "routes_admissibles", "nb_noeuds_partition"):
            assert champ in sec, f"champ manquant : {champ}"

    def test_ancrage_sans_subprocess(self):
        # Contrainte dure : l'ancrage se fait par import direct, jamais en
        # lançant un sous-processus. On cherche l'USAGE (imports, appels),
        # pas le mot dans les commentaires (« jamais de subprocess »).
        import re
        motifs = [r"^\s*(import|from)\s+subprocess\b", r"\bsubprocess\.[A-Za-z]",
                  r"\bos\.system\s*\(", r"\bos\.popen\s*\(", r"\bPopen\s*\("]
        for module in (ancrage, sondes):
            lignes = open(module.__file__, encoding="utf-8").read().splitlines()
            usages = [l for l in lignes
                      if not l.strip().startswith(("#", '"""', "'''", "*"))
                      and any(re.search(m, l) for m in motifs)]
            assert not usages, f"usage subprocess dans {module.__name__} : {usages}"

    def test_ancrage_ne_crashe_pas_sur_noeud_inconnu(self, registre, posterieurs):
        sec = section_entropie_noeud("mecanisme_qui_n_existe_pas_xyz", registre,
                                     posterieurs)
        assert isinstance(sec, dict)  # section vide documentée, pas de crash

    def test_sonde_python_sans_entropie_reste_rapide_et_pure(self, registre):
        # sonder() seul n'attache PAS la lentille (pas de récursion :
        # entropie_depuis_sonde appelle sonder()).
        s = sonder("energy_identity", registre)
        assert s.entropie == {}
        assert all(h.section_entropie is None for h in s.route_a)


# ────────────────────────────────────────────────────────
# 2. DOUBLE VERDICT ch.139 — rattaché au niveau nœud
# ────────────────────────────────────────────────────────

class TestDoubleVerdict139:
    def test_obstruction_139_est_double_verdict(self, registre):
        s = sonder("energy_identity", registre)
        ob139 = [o for o in s.obstructions_b if o.chantier == "139"]
        assert ob139, "obstruction ch.139 introuvable en Sonde B"
        assert est_double_verdict(ob139[0])
        assert "RÉFUTÉ" in ob139[0].verdicts
        assert "DÉMONTRÉ" in ob139[0].verdicts

    def test_versions_parsees_depuis_titre(self, registre):
        s = sonder("energy_identity", registre)
        ob139 = [o for o in s.obstructions_b if o.chantier == "139"][0]
        versions = versions_double_verdict(ob139)
        par_version = {v["version"]: v["statut"] for v in versions}
        assert par_version.get("inconditionnelle") == "RÉFUTÉ"
        assert par_version.get("conditionnelle") == "DÉMONTRÉ"

    def test_double_verdict_rattache_au_mecanisme_lui_meme(self, registre):
        s = sonder("TransferDissipationContinuity", registre)
        assert s.doubles_verdicts, "aucun double verdict rattaché"
        dv = s.doubles_verdicts[0]
        assert dv["portee"] == "mecanisme"
        par_version = {v["version"]: v["statut"] for v in dv["versions"]}
        assert par_version["inconditionnelle"] == "RÉFUTÉ"
        assert par_version["conditionnelle"] == "DÉMONTRÉ"

    def test_double_statut_sur_noeud_de_route(self, registre):
        # Le nœud n'est plus laissé NON-ATTAQUÉ avec le verdict en Sonde B
        # seule : il porte explicitement les deux versions.
        s = sonder("energy_identity", registre)
        noeud = next(h for h in s.route_a
                     if h.nom == "TransferDissipationContinuity")
        assert noeud.double_statut is not None
        par_version = {v["version"]: v["statut"]
                       for v in noeud.double_statut["versions"]}
        assert par_version["inconditionnelle"] == "RÉFUTÉ"
        assert par_version["conditionnelle"] == "DÉMONTRÉ"

    def test_double_statut_expose_en_json(self, registre):
        s = sonder("energy_identity", registre)
        d = s.vers_dict()
        assert "doubles_verdicts" in d and d["doubles_verdicts"]
        noeuds = d["sonde_a"]["route_vers_inconditionnel"]
        noeud = next(n for n in noeuds
                     if n["nom"] == "TransferDissipationContinuity")
        assert "double_statut" in noeud

    def test_obstruction_simple_sans_double_verdict(self):
        ob = Obstruction(titre="H26 — RÉFUTÉE", verdicts=["RÉFUTÉ"])
        assert not est_double_verdict(ob)
        ob2 = Obstruction(titre="H5 — DÉMONTRÉE", verdicts=["DÉMONTRÉ"])
        assert not est_double_verdict(ob2)

    def test_console_exhibe_doubles_verdicts(self, registre):
        console = rendre_sonde_console(sonder("TransferDissipationContinuity",
                                              registre))
        assert "DOUBLES VERDICTS" in console
        assert "RÉFUTÉ" in console and "DÉMONTRÉ" in console


# ────────────────────────────────────────────────────────
# 3. PHI OU-ALLER — liste d'attention ordonnée (pas un score)
# ────────────────────────────────────────────────────────

class TestOuAller:
    def test_ou_aller_tri_decroissant(self, registre, posterieurs):
        data = construire_ou_aller(registre, posterieurs)
        couts = [e["cout_booleanisation_bits"] for e in data["entrees"]]
        assert couts == sorted(couts, reverse=True), "tri non décroissant"
        assert len(couts) >= 40  # 6 sorrys + hypothèses §1

    def test_ou_aller_six_sorrys_presents(self, registre, posterieurs):
        data = construire_ou_aller(registre, posterieurs)
        noms = {e["mecanisme"] for e in data["entrees"]}
        for sorry in ("local_existence", "maximalTime_pos", "local_uniqueness",
                      "energy_identity", "leray_existence", "BKM_criterion"):
            assert sorry in noms, f"sorry manquant : {sorry}"

    def test_ou_aller_tete_de_liste_bkm(self, registre, posterieurs):
        # Propriété structurelle : BKM_criterion porte le plus grand coût
        # de booléanisation élémentaire de la liste (tête de liste).
        # 2026-09-30 : la magnitude absolue dépend du registre vivant
        # (valait ≈ 11.2 étalons-or, vaut 7.79 après la campagne 137/138/139
        # qui a enrichi le registre) — on teste le rang, pas la valeur,
        # pour ne pas coupler le test à l'évolution légitime du registre.
        data = construire_ou_aller(registre, posterieurs)
        tete = data["entrees"][0]
        assert tete["mecanisme"] == "BKM_criterion"
        ratios = [e["ratio_etalons_or"] for e in data["entrees"]]
        assert tete["ratio_etalons_or"] == max(ratios)
        assert tete["ratio_etalons_or"] > 0

    def test_ou_aller_deterministe(self, registre, posterieurs):
        a = [e["mecanisme"] for e in construire_ou_aller(registre, posterieurs)["entrees"]]
        b = [e["mecanisme"] for e in construire_ou_aller(registre, posterieurs)["entrees"]]
        assert a == b

    def test_ou_aller_pas_de_cles_interdites(self, registre, posterieurs):
        data = construire_ou_aller(registre, posterieurs)
        _assert_pas_de_cles_interdites(data, "ou-aller JSON")

    def test_ou_aller_console_interdiction_et_veto(self, registre, posterieurs):
        console = rendre_ou_aller_console(construire_ou_aller(registre, posterieurs))
        assert "INTERDICTION" in console
        assert "veto" in console.lower()
        assert "pas un score" in console.lower()

    def test_ou_aller_double_verdict_signale(self, registre, posterieurs):
        data = construire_ou_aller(registre, posterieurs)
        par_nom = {e["mecanisme"]: e for e in data["entrees"]}
        assert par_nom["energy_identity"]["double_verdict"] is True


# ────────────────────────────────────────────────────────
# 4. INTERDICTION FORMELLE + STABILITÉ
# ────────────────────────────────────────────────────────

class TestInterdictionEtStabilite:
    def test_sonde_ancree_pas_de_cles_interdites(self, sonde_ancree):
        _assert_pas_de_cles_interdites(sonde_ancree.vers_dict(), "sonde ancrée")

    def test_json_sonde_stable_hors_ajouts(self, registre):
        # Critère (a) : le JSON existant n'est modifié que par les ajouts
        # mandatés — section `entropie`, `doubles_verdicts`, `double_statut`.
        d = sonder("energy_identity", registre).vers_dict()
        cles_haut = set(d)
        attendues = {"mecanisme", "type_mecanisme", "trous", "statut_mecanisme",
                     "doubles_verdicts", "entropie", "sonde_a", "sonde_b",
                     "notes", "integrite", "limites", "interdiction",
                     "version_phi", "horodatage"}
        assert cles_haut == attendues, f"écart : {cles_haut ^ attendues}"
        noeud = d["sonde_a"]["route_vers_inconditionnel"][0]
        cles_noeud = set(noeud)
        attendues_noeud = {"nom", "statut", "zone", "trous", "contenu", "tag",
                           "cout_estime", "chantiers", "fichier_ligne", "source"}
        # Seuls ajouts autorisés : double_statut / entropie (ancrage).
        assert cles_noeud - attendues_noeud <= {"double_statut", "entropie"}

    def test_flottants_seulement_sous_entropie(self, sonde_ancree):
        # Garde-fort : hors sections `entropie`, aucun flottant (pas de
        # scalaire lisible comme score) ; sous `entropie`, des bits légitimes.
        def _parcourir_hors_entropie(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "entropie":
                        continue
                    if isinstance(v, float):
                        yield v
                    yield from _parcourir_hors_entropie(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _parcourir_hors_entropie(v)
        assert list(_parcourir_hors_entropie(sonde_ancree.vers_dict())) == []

    def test_version_non_bumpee(self):
        assert VERSION == "0.11.0"

    def test_changelog_entree_ancrage(self):
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "CHANGELOG.md")
        texte = open(chemin, encoding="utf-8").read()
        assert "ou-aller" in texte and "ANCRAGE" in texte
