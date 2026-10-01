"""Tests du chantier DURCISSEMENT-SONDES (v0.11.0, 2026-09-30).

Doctrine : un instrument qui échoue doit être durci, jamais abandonné ;
un instrument doit échouer bruyamment, jamais en silence.

ATTENTION NOM : `tests/test_durcissement.py` existe déjà (chantier v0.6.1,
durcissement de l'indexeur — à ne pas écraser). Ce fichier couvre le
chantier v0.11.0 « durcissement des sondes » et vit sous un nom distinct.

Tous les sabotages s'exécutent sur des sandboxes `tempfile` ou des
mocks restaurés : l'environnement réel n'est jamais modifié.
"""

import ast
import json
import os

import pytest

from phi_complexity import durcissement as D
from phi_complexity.durcissement import (
    EtatSonde,
    diagnostiquer_capteurs,
    detecter_capteur_sorry_aveugle,
    diff_contenu,
    empreinte_contenu,
    lancer_batterie,
    lancer_redteam,
    rendre_batterie_console,
    rendre_redteam_console,
    sabotage_baseline_corrompue,
    sabotage_enonce_modifie,
    sabotage_fichier_manquant,
    sabotage_grammaire_absente,
    sabotage_guillemet_orphelin,
    sabotage_registre_malforme,
    sabotage_sorry_cache,
    sabotage_version_desync,
    veille_durcie,
    verifier_coherence_version,
    verifier_perimetre,
)

RACINE_V110 = os.path.join(os.path.dirname(__file__), "..")


# ------------------------------------------------------------------
# 1. La batterie : 8 sabotages, 0 silence
# ------------------------------------------------------------------

def test_batterie_huit_sabotages():
    res = lancer_batterie()
    assert len(res) == 8, [r.nom for r in res]


def test_batterie_aucun_silence():
    """Invariant cardinal du chantier : chaque sabotage est bruyant."""
    res = lancer_batterie()
    silencieux = [r.nom for r in res if not r.bruyant]
    assert silencieux == [], f"sabotages silencieux : {silencieux}"


def test_batterie_ne_touche_pas_le_reel():
    """Les sabotages n'écrivent rien dans l'arbre v110."""
    dossier = os.path.join(RACINE_V110, "phi_complexity")
    avant = {}
    for racine, _dirs, fichiers in os.walk(dossier):
        for nom in fichiers:
            chemin = os.path.join(racine, nom)
            avant[chemin] = os.path.getmtime(chemin)
    lancer_batterie()
    lancer_redteam()
    apres = {}
    for racine, _dirs, fichiers in os.walk(dossier):
        for nom in fichiers:
            chemin = os.path.join(racine, nom)
            apres[chemin] = os.path.getmtime(chemin)
    assert set(avant) == set(apres), "des fichiers sont apparus/disparus"
    modifies = [c for c in avant if avant[c] != apres[c]]
    assert modifies == [], f"fichiers modifiés : {modifies}"


def test_provenance_sur_chaque_sabotage():
    res = lancer_batterie()
    for r in res:
        assert set(r.provenance) == {"methode", "perimetre", "date", "version_phi"}, r.nom
        assert r.provenance["methode"].endswith(r.nom), r.nom
        assert r.provenance["version_phi"] == D.VERSION


def test_sorry_cache_conforme():
    """sorry/admit en docstring/string/commentaire ≠ vrais trous."""
    r = sabotage_sorry_cache()
    assert r.bruyant
    assert "CONFORME" in r.details


def test_guillemet_orphelin_degrade_bruyant():
    r = sabotage_guillemet_orphelin()
    assert r.bruyant
    assert r.etat is EtatSonde.DEGRADE


def test_enonce_modifie_veille_aveugle_mais_durcie_bruyante():
    r = sabotage_enonce_modifie()
    assert r.bruyant
    assert "STABLE" in r.details  # l'angle mort est confirmé…
    assert "DÉGRADÉ" in r.details  # …et la veille durcie le pare


def test_version_desync_bruyant():
    r = sabotage_version_desync()
    assert r.bruyant
    assert r.etat is EtatSonde.DEGRADE


def test_rendu_console_mentionne_les_silences():
    texte = rendre_batterie_console(lancer_batterie())
    assert "Silences détectés" in texte


# ------------------------------------------------------------------
# 2. Détections unitaires
# ------------------------------------------------------------------

def test_diagnostic_capteurs_structure():
    d = diagnostiquer_capteurs("/tmp/inexistant_pour_test")
    capteurs = {x.capteur: x for x in d}
    assert "tree_sitter_language_pack" in capteurs
    assert "grammaire_lean" in capteurs
    for x in d:
        assert isinstance(x.etat, EtatSonde)  # typé, jamais booléen
        assert x.raison, x.capteur  # jamais de diagnostic muet


def test_guillemet_orphelin_detection(tmp_path):
    f = tmp_path / "Q.lean"
    f.write_text('def x : String := "oublie de fermer\nsorry\n')
    rapport = detecter_capteur_sorry_aveugle(str(f))
    assert rapport.etat is EtatSonde.DEGRADE
    assert "Q.lean" in rapport.raison or "guillemet" in rapport.raison.lower()


def test_guillemet_propre_pas_de_risque(tmp_path):
    f = tmp_path / "P.lean"
    f.write_text('def x : String := "tout est ferme"\ntheorem t : True := trivial\n')
    rapport = detecter_capteur_sorry_aveugle(str(f))
    assert rapport.etat is EtatSonde.OK


def test_diff_contenu_detecte_modification(tmp_path):
    f = tmp_path / "A.lean"
    f.write_text("theorem a : True := trivial\n")
    avant = empreinte_contenu(str(tmp_path))
    f.write_text("theorem a : True := by sorry\n")
    d = diff_contenu(avant, str(tmp_path))
    assert d["modifies"] == ["A.lean"]


def test_diff_contenu_silencieux_si_identique(tmp_path):
    (tmp_path / "A.lean").write_text("x\n")
    avant = empreinte_contenu(str(tmp_path))
    d = diff_contenu(avant, str(tmp_path))
    assert d["modifies"] == [] and d["ajoutes"] == [] and d["supprimes"] == []


def test_perimetre_detecte_debordement(tmp_path):
    from phi_complexity.veille import prendre_snapshot
    (tmp_path / "ok.lean").write_text("theorem o : True := trivial\n")
    enveloppe = prendre_snapshot(str(tmp_path))
    sub = tmp_path / "sous"
    sub.mkdir()
    (sub / "cache.lean").write_text("theorem c : True := trivial\n")
    rapport = verifier_perimetre(enveloppe, str(tmp_path))
    assert "sous/cache.lean" in rapport["manquants_enveloppe"]


def test_perimetre_ok_si_couvert(tmp_path):
    from phi_complexity.veille import prendre_snapshot
    (tmp_path / "ok.lean").write_text("theorem o : True := trivial\n")
    enveloppe = prendre_snapshot(str(tmp_path))
    rapport = verifier_perimetre(enveloppe, str(tmp_path))
    assert rapport["manquants_enveloppe"] == []


def test_coherence_version_sabotage(tmp_path):
    (tmp_path / "faux_mod.py").write_text('"""Mon module (v9.99.9)."""\n')
    (tmp_path / "bon_mod.py").write_text(
        '"""Mon module (v%s, historique v0.1.0 dedans)."""\n' % D.VERSION)
    inc = verifier_coherence_version(str(tmp_path))
    fichiers = {i["fichier"] for i in inc}
    assert "faux_mod.py" in fichiers
    assert "bon_mod.py" not in fichiers


def test_coherence_version_arbre_reel_pattern_s6():
    """Candidats S6 sur l'arbre réel : tripwire documenté, à trier.
    Si le parent les corrige, ce test échoue HONNÊTEMENT (mettre à jour)."""
    inc = verifier_coherence_version(
        os.path.join(RACINE_V110, "phi_complexity"))
    trouves = {i["fichier"]: i["docstring_annonce"] for i in inc
               if "durcissement" not in i["fichier"]}
    assert trouves == {
        "croyances.py": "v0.10.0",
        "langs/lean.py": "v0.7.0",
        "langs/python_native.py": "v0.8.0",
    }


# ------------------------------------------------------------------
# 3. veille_durcie : les trois états
# ------------------------------------------------------------------

def _enveloppe_minimale(tmp_path):
    from phi_complexity.veille import prendre_snapshot
    dossier = str(tmp_path)
    (tmp_path / "M.lean").write_text("theorem m : True := trivial\n")
    return dossier, prendre_snapshot(dossier)


def _capteurs_sains(monkeypatch):
    """Isole la logique de verdict des capteurs : l'état réel du pack
    tree-sitter varie selon la machine (absent le 2026-09-30)."""
    from phi_complexity.durcissement import DiagnosticCapteur
    sains = [
        DiagnosticCapteur(capteur="tree_sitter_language_pack",
                          etat=EtatSonde.OK, raison="mock test"),
        DiagnosticCapteur(capteur="grammaire_lean",
                          etat=EtatSonde.OK, raison="mock test"),
    ]
    monkeypatch.setattr(D, "diagnostiquer_capteurs", lambda dossier: sains)


def test_veille_durcie_ok(tmp_path, monkeypatch):
    _capteurs_sains(monkeypatch)
    dossier, enveloppe = _enveloppe_minimale(tmp_path)
    vd = veille_durcie(enveloppe, dossier)
    assert vd["etat"] == EtatSonde.OK.value, vd.get("raison_etat")
    assert vd["verdict_veille"] == "STABLE"


def test_veille_durcie_en_panne_si_enveloppe_boguee(tmp_path):
    vd = veille_durcie({"outil": "enveloppe vide"}, str(tmp_path))
    assert vd["etat"] == EtatSonde.EN_PANNE.value
    assert vd["raison"]  # la panne s'explique, jamais muette


def test_veille_durcie_degrade_si_symbole_perdu(tmp_path, monkeypatch):
    """Capteurs sains + symbole perdu : l'instrument est OK (il fait son
    travail) et c'est le VERDICT qui porte la dégradation détectée."""
    _capteurs_sains(monkeypatch)
    (tmp_path / "M.lean").write_text(
        "theorem m : True := trivial\ntheorem n : True := trivial\n")
    dossier = str(tmp_path)
    from phi_complexity.veille import prendre_snapshot
    enveloppe = prendre_snapshot(dossier)
    (tmp_path / "M.lean").write_text("theorem m : True := trivial\n")
    vd = veille_durcie(enveloppe, dossier)
    # n a disparu de la baseline → la veille le détecte, capteurs nominaux
    assert vd["etat"] == EtatSonde.OK.value
    assert vd["verdict_veille"] == "DÉGRADATION DÉTECTÉE"


def test_veille_durcie_ok_malgre_symbole_gagne(tmp_path, monkeypatch):
    """Contrat honnête de la veille : un symbole AJOUTÉ (non troué) n'est
    pas une dégradation — il est signalé en `symboles_gagnes`, verdict
    STABLE. Ce test fige ce contrat."""
    _capteurs_sains(monkeypatch)
    dossier, enveloppe = _enveloppe_minimale(tmp_path)
    (tmp_path / "M.lean").write_text(
        "theorem m : True := trivial\ntheorem n : True := trivial\n")
    vd = veille_durcie(enveloppe, dossier)
    assert vd["etat"] == EtatSonde.OK.value
    assert vd["verdict_veille"] == "STABLE"


def test_veille_durcie_reflete_capteurs_degrades(tmp_path):
    """Sans mock : si les capteurs sont dégradés (état réel de la
    machine), la veille durcie le dit au lieu de faire foi au verdict."""
    dossier, enveloppe = _enveloppe_minimale(tmp_path)
    vd = veille_durcie(enveloppe, dossier)
    etats_capteurs = {c["etat"] for c in vd["capteurs"]}
    if EtatSonde.DEGRADE.value in etats_capteurs:
        assert vd["etat"] == EtatSonde.DEGRADE.value


# ------------------------------------------------------------------
# 4. Red team : ≥ 3 tentatives, statuts typés
# ------------------------------------------------------------------

def test_redteam_au_moins_trois_tentatives():
    t = lancer_redteam()
    assert len(t) >= 3


def test_redteam_statuts_types():
    for x in lancer_redteam():
        assert x.statut in ("RÉSISTE", "DURCI", "OUVERT"), x.nom
        assert x.objectif and x.exhibit and x.nom
        assert set(x.provenance) == {"methode", "perimetre", "date",
                                     "version_phi"}


def test_redteam_r1_resiste_prose_nefface_pas():
    t = {x.nom: x for x in lancer_redteam()}
    assert t["R1_regression_deguisee"].statut == "RÉSISTE"


def test_redteam_r2_ouvert_honnete():
    """Le renommage déguisé TROMPE la veille : statut OUVERT assumé."""
    t = {x.nom: x for x in lancer_redteam()}
    assert t["R2_renommage_deguise"].statut == "OUVERT"


def test_redteam_r4_oracle_sans_verite():
    t = {x.nom: x for x in lancer_redteam()}
    assert t["R4_oracle_verite"].statut == "RÉSISTE"
    assert "motifs interdits trouvés=[]" in t["R4_oracle_verite"].exhibit


def test_redteam_rendu_console():
    texte = rendre_redteam_console(lancer_redteam())
    assert "RED TEAM" in texte
    assert "TROMPÉ" in texte or "a résisté" in texte


# ------------------------------------------------------------------
# 5. Gardes d'interdiction (code, pas seulement docs)
# ------------------------------------------------------------------

def _source_module():
    chemin = os.path.join(RACINE_V110, "phi_complexity", "durcissement.py")
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def test_aucun_flottant_dans_le_module():
    """Aucun littéral float dans le code (les mentions 'v0.11.0' des
    docstrings sont des chaînes, pas des flottants)."""
    arbre = ast.parse(_source_module())
    flottants = [n.value for n in ast.walk(arbre)
                 if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert flottants == []


def test_sorties_sans_score_ni_pa():
    """Les sorties rendues ne PRODUISENT ni score ni P(A (les mentions
    du motif interdit dans l'énoncé de R4 sont la garde elle-même)."""
    sorties = (rendre_batterie_console(lancer_batterie())
               + rendre_redteam_console(lancer_redteam())).lower()
    assert "p(a) =" not in sorties
    assert "p(a):" not in sorties
    assert "probabilité que" not in sorties
    assert "score =" not in sorties
    assert "score:" not in sorties


def test_pas_de_subprocess_dans_le_module():
    src = _source_module()
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "os.popen" not in src


def test_import_direct():
    """Le module s'importe directement (pas de subprocess déguisé)."""
    import importlib
    mod = importlib.import_module("phi_complexity.durcissement")
    assert mod.VERSION == D.VERSION
