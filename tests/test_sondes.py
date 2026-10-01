"""tests/test_sondes.py — Tests des sondes A/B (opt-in, lecture seule du registre).

Stratégie : le registre est une donnée réelle
(REGISTRE_HYPOTHESES_20260929.md). Les tests vérifient le parsing contre des
faits connus du registre, la défense contre les entrées malformées, les trois
zones (mort / vivant / terra incognita), et l'INTERDICTION FORMELLE (aucun
score A/B, aucune P(A)) par balayage récursif des sorties JSON.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from phi_complexity import sondes
from phi_complexity.sondes import (
    CLES_INTERDITES,
    RegistreSondes,
    REGISTRE_DEFAUT,
    resoudre_mecanisme,
    sonder,
    rendre_sonde_console,
    zone_sonde,
)

REGISTRE = REGISTRE_DEFAUT


@pytest.fixture(scope="module")
def registre():
    assert os.path.isfile(REGISTRE), f"registre introuvable : {REGISTRE}"
    return RegistreSondes().charger(REGISTRE)


# ────────────────────────────────────────────────────────
# PARSING — faits connus du registre
# ────────────────────────────────────────────────────────

class TestParsingTable:
    def test_h29_conditionnelle_mur(self, registre):
        h = registre.hypotheses["H29"]
        assert h.statut == "CONDITIONNEL"      # transition §1bis (ch.83/90)
        assert h.tag == "mur (dur)"            # reclassifiée 🧱
        assert 4 in h.trous

    def test_h5_demontee_par_delta(self, registre):
        assert registre.hypotheses["H5"].statut == "DÉMONTRÉ"

    def test_h7_cout_et_tag(self, registre):
        h = registre.hypotheses["H7"]
        assert h.cout == "2 000–5 000"
        assert h.tag == "mur (dur)"
        # Reclassifiée NOMMÉ → CONDITIONNELLE au ch.45 (2026-09-30,
        # résidu nommé MildClassicalResidue) — le test suit le registre
        # vivant, pas l'inverse. (Fix 2026-10-01 : l'attente NOMMÉ
        # était périmée.)
        assert h.statut == "CONDITIONNEL"

    def test_h3_forteresse(self, registre):
        assert registre.hypotheses["H3"].tag == "mur (dur, forteresse)"
        # alias par nom exact (table §1)
        assert registre.hypotheses["KatoBilinearData"].tag == "mur (dur, forteresse)"

    def test_h30_porte_technique(self, registre):
        h = registre.hypotheses["H30"]
        assert h.tag == "technique (molle)"
        assert h.statut == "CONDITIONNEL"

    def test_h34_hors_classe(self, registre):
        h = registre.hypotheses["H34"]
        assert h.tag == "hors-classe (contenu Clay)"
        assert 2 in h.trous

    def test_h29b_noeud_cree_par_delta(self, registre):
        h = registre.hypotheses["H29b"]
        assert h.statut == "CONDITIONNEL"
        assert 4 in h.trous  # propagation via « Débloque » §4

    def test_obstruction_ssup(self, registre):
        obs = [o for o in registre.obstructions if "sSup" in o.titre]
        assert len(obs) == 1
        assert 2 in obs[0].trous
        assert obs[0].interdit  # l'interdit est exhibé

    def test_obstruction_h26_refutee(self, registre):
        obs = [o for o in registre.obstructions if "H26" in o.titre]
        assert len(obs) == 1
        assert "RÉFUTÉ" in obs[0].verdicts
        assert obs[0].vice  # le vice exact est exhibé

    def test_journal_ch138_parse(self, registre):
        e = registre.journal["138"]
        # 2026-09-30 : l'entrée Ch.138 du journal a été enrichie par l'exécution
        # de la DÉCISION 138 (maximalTime en ℝ≥0∞, blast radius sur les 6 sorrys) ;
        # les trous cités sont désormais [1..6], pas seulement la trilogie Picard.
        assert e.trous == [1, 2, 3, 4, 5, 6]
        assert "maximalTime_pos_refuted" in e.identifiants


class TestParsingDefensif:
    REGISTRE_MALFORME = """\
# REGISTRE DE TEST — entrées malformées volontaires

## §1. TABLE MAÎTRESSE

| # | Hypothèse | Fichier:ligne | Statut (ch.82) | Trous | Contenu | Référence | Classe |
|---|---|---|---|---|---|---|---|
| H99 | `HypotheseBizarre` | `f.lean:1` | NOMMÉ | 4 | du contenu | ref | 🧱 |
| H100 | ligne incomplète

## §5. OBSTRUCTIONS

### Titre sans aucun verdict typé
Du corps sans statut.
"""

    def test_malforme_ne_crashe_pas_et_compte(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                          delete=False, encoding="utf-8") as f:
            f.write(self.REGISTRE_MALFORME)
            chemin = f.name
        try:
            r = RegistreSondes().charger(chemin)
        finally:
            os.unlink(chemin)
        # H99 est parsée malgré le reste ; l'obstruction sans verdict est
        # COMPTÉE comme non parsée, pas crashée.
        assert r.hypotheses["H99"].nom == "HypotheseBizarre"
        assert r.entrees_non_parsees >= 1

    def test_fichier_absent_leve_une_erreur_claire(self):
        with pytest.raises((FileNotFoundError, OSError)):
            RegistreSondes().charger("/chemin/qui/n/existe/pas.md")


# ────────────────────────────────────────────────────────
# SONDE A — route vers l'inconditionnel
# ────────────────────────────────────────────────────────

class TestSondeA:
    def test_energy_identity_exhibe_les_quatre(self, registre):
        s = sonder("energy_identity", registre)
        noms = [h.nom for h in s.route_a]
        for cible in ("TransferDissipationContinuity",
                      "ParabolicGainHypothesis",
                      "NewtonianPotentialHypothesis",
                      "RealTransferData"):
            assert cible in noms, f"{cible} absent de la route"

    def test_maximaltime_route_contient_bddabove(self, registre):
        s = sonder("maximalTime_pos", registre)
        noms = [h.nom for h in s.route_a]
        assert any("BddAbove" in n for n in noms)

    def test_mecanisme_ne_se_sonde_pas_lui_meme(self, registre):
        s = sonder("energy_identity", registre)
        assert "energy_identity" not in [h.nom for h in s.route_a]

    def test_distance_fermeture_statuts_types(self, registre):
        s = sonder("local_existence", registre)
        assert s.distance_fermeture, "la distance de fermeture ne doit pas être vide"
        for h in s.distance_fermeture:
            assert h.statut not in ("DÉMONTRÉ", "ÉLIMINÉ")

    def test_chantier_comme_mecanisme(self, registre):
        s = sonder("138", registre)
        assert s.type_mecanisme == "chantier"
        # 2026-09-30 : voir test_journal_ch138_parse — entrée enrichie par la
        # décision 138, trous cités [1..6].
        assert s.trous == [1, 2, 3, 4, 5, 6]

    def test_mecanisme_inconnu_sans_crash(self, registre):
        s = sonder("hypothese_qui_n_existe_pas_xyz", registre)
        assert s.type_mecanisme == "inconnu"
        assert s.route_a == [] and s.obstructions_b == []
        assert any("non résolu" in n for n in s.notes)


# ────────────────────────────────────────────────────────
# SONDE B — inconditionnel négatif
# ────────────────────────────────────────────────────────

class TestSondeB:
    def test_maximaltime_exhibe_ssup(self, registre):
        s = sonder("maximalTime_pos", registre)
        titres = [o.titre for o in s.obstructions_b]
        assert any("sSup" in t for t in titres)

    def test_maximaltime_exhibe_refutation_journal(self, registre):
        s = sonder("maximalTime_pos", registre)
        idents = [r["identifiant"] for r in s.refutes_journal_b]
        assert "maximalTime_pos_refuted" in idents

    def test_pas_de_verdict_voisin_aberrant(self, registre):
        # Leçon instrumentale : « RÉFUTÉE par `g4_kinetic_shape_differ` »
        # ne réfute PAS g4_kinetic_shape_differ — le verdict doit être
        # syntaxiquement attaché à l'identifiant.
        s = sonder("energy_identity", registre)
        idents = [r["identifiant"] for r in s.refutes_journal_b]
        assert "g4_kinetic_shape_differ" not in idents
        assert "Real.smoothTransition" not in idents


# ────────────────────────────────────────────────────────
# TROIS ZONES — terra incognita
# ────────────────────────────────────────────────────────

class TestTroisZones:
    def test_zone_h29_vivant(self, registre):
        assert zone_sonde(registre.hypotheses["H29"]) == "CONDITIONNEL"

    def test_zone_h5_ferme(self, registre):
        assert zone_sonde(registre.hypotheses["H5"]) == "DÉMONTRÉ"

    def test_zone_h26_mort(self, registre):
        assert zone_sonde(registre.hypotheses["H26"]) == "RÉFUTÉ"

    def test_terra_incognita_energy_identity(self, registre):
        s = sonder("energy_identity", registre)
        assert s.terra_incognita, "la terra incognita ne doit pas être vide"
        noms = [h.nom for h in s.terra_incognita]
        # Nœud identifiable au registre : H27, NOMMÉ table §1, jamais attaqué.
        assert "SchwartzPropagationHypothesis" in noms
        h27 = registre.hypotheses["H27"]
        assert h27.statut == "NOMMÉ" and not h27.chantiers
        for h in s.terra_incognita:
            assert zone_sonde(h) == "NON-ATTAQUÉ"

    def test_terra_incognita_dans_distance_fermeture(self, registre):
        s = sonder("local_existence", registre)
        noms_dist = {h.nom for h in s.distance_fermeture}
        for h in s.terra_incognita:
            assert h.nom in noms_dist

    def test_zone_presente_dans_json(self, registre):
        d = sonder("energy_identity", registre).vers_dict()
        zones = {n["zone"] for n in d["sonde_a"]["route_vers_inconditionnel"]}
        assert "NON-ATTAQUÉ" in zones
        assert isinstance(d["sonde_a"]["terra_incognita"], list)
        assert len(d["sonde_a"]["terra_incognita"]) > 0


# ────────────────────────────────────────────────────────
# INTERDICTION FORMELLE — aucun score A/B, aucune P(A)
# ────────────────────────────────────────────────────────

def _parcourir(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("cle", k)
            yield from _parcourir(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _parcourir(v)
    elif isinstance(obj, float):
        yield ("flottant", obj)


def _parcourir_hors_entropie(obj):
    """_parcourir en sautant les sous-arbres `entropie` (bits d'attention
    légitimes : h_initiale, ΔH, coûts — jamais des scores ni des P(A))."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "entropie":
                continue  # section instrumentale : flottants légitimes
            yield ("cle", k)
            yield from _parcourir_hors_entropie(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _parcourir_hors_entropie(v)
    elif isinstance(obj, float):
        yield ("flottant", obj)


class TestInterdiction:
    def test_aucune_cle_interdite_dans_json(self, registre):
        for mec in ("energy_identity", "maximalTime_pos", "H29", "138"):
            d = sonder(mec, registre).vers_dict()
            cles = {v.lower() for k, v in _parcourir(d)
                    if k == "cle" and isinstance(v, str)}
            violees = cles & CLES_INTERDITES
            assert not violees, f"clés interdites pour {mec} : {violees}"

    def test_aucun_flottant_dans_json(self, registre):
        # Garde-fort mécanique : aucun scalaire flottant ne peut être lu
        # comme un score ou une probabilité.
        #
        # ÉVOLUTION 2026-09-30 (chantier ANCRAGE-ENTROPIE) : les sections
        # `entropie` (nœuds + mécanisme, mode --format json) contiennent
        # légitimement des flottants — ce sont des BITS d'attention de
        # l'instrument (h_initiale, ΔH, coûts), pas des scores ni des P(A).
        # Le garde-fort porte donc partout SAUF sous les sections `entropie` ;
        # l'interdiction scores/P(A) reste testée sur les CLÉS (test_..._cles).
        d = sonder("energy_identity", registre).vers_dict()
        flottants = [v for k, v in _parcourir_hors_entropie(d)
                     if k == "flottant"]
        assert flottants == []

    def test_interdiction_dans_docstring_et_sortie(self, registre):
        assert "INTERDICTION FORMELLE" in sondes.__doc__
        assert "aucun score a/b" in sondes.INTERDICTION_TEXTE.lower()
        console = rendre_sonde_console(sonder("H29", registre))
        assert "INTERDICTION" in console
        d = sonder("H29", registre).vers_dict()
        assert "interdiction" in d

    def test_pas_de_pa_dans_console(self, registre):
        # « P(A) » ne peut apparaître que dans la ligne d'interdiction
        # elle-même (qui la nomme pour l'interdire), jamais comme résultat.
        console = rendre_sonde_console(sonder("energy_identity", registre))
        for ligne in console.splitlines():
            if "P(A)" in ligne:
                assert "INTERDICTION" in ligne


# ────────────────────────────────────────────────────────
# INTÉGRITÉ — mode exact
# ────────────────────────────────────────────────────────

class TestIntegrite:
    def test_exact_double_passe_concordante(self, registre):
        s = sonder("energy_identity", registre, exact=True)
        integ = s.integrite
        assert integ["mode"].startswith("exact")
        assert integ["passes"] == 2
        assert integ["concordantes"] is True
        assert len(integ["md5_registre"]) == 32

    def test_mode_standard_sans_double_passe(self, registre):
        s = sonder("energy_identity", registre, exact=False)
        assert "passes" not in s.integrite


# ────────────────────────────────────────────────────────
# CLI — phi sonde
# ────────────────────────────────────────────────────────

class TestCLI:
    V110 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _phi(self, *args):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "phi_complexity.cli", *args],
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.V110, env=env,
        )

    def test_sonde_console_exit_0(self):
        p = self._phi("sonde", "energy_identity")
        assert p.returncode == 0, p.stderr[-500:]
        assert "SONDE A" in p.stdout and "SONDE B" in p.stdout
        assert "TERRA INCOGNITA" in p.stdout

    def test_sonde_json_exit_0_et_valide(self):
        p = self._phi("sonde", "maximalTime_pos", "--format", "json")
        assert p.returncode == 0, p.stderr[-500:]
        d = json.loads(p.stdout)
        assert d["mecanisme"] == "maximalTime_pos"
        assert "terra_incognita" in d["sonde_a"]

    def test_sonde_registre_introuvable(self):
        p = self._phi("sonde", "H29", "--registre", "/tmp/nonexistent.md")
        assert p.returncode == 1
