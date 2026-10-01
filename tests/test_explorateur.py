"""tests/test_explorateur.py — Chantier EXPLORATEUR (2026-09-30).

L'explorateur fusionne typographie des symboles + analyse en une surface
HTML autonome. Ces tests verrouillent :
- l'interdiction formelle (aucun score A/B, aucune P(A)) jusque dans le JSON
  embarqué ;
- le garde-fort flottants (sections documentées uniquement) ;
- l'autonomie offline (zéro dépendance réseau) ;
- la non-mutation des objets Hypothese partagés ;
- l'exactitude du simulateur sur un DAG jouet (comptage manuel de contrôle) ;
- l'erreur propre sur mécanisme inconnu ;
- l'import direct (aucun subprocess dans explorateur.py).
"""
import json
import re

import pytest

from phi_complexity.explorateur import (
    SECTIONS_FLOTTANTS_OK,
    construire_donnees_explorateur,
    construire_graphe,
    construire_simulateur,
    donnees_vers_json_embarquable,
    explorer_vers_fichier,
    generer_html,
)
from phi_complexity.entropie import compter_routes_dag
from phi_complexity.sondes import (
    CLES_INTERDITES,
    Hypothese,
    RegistreSondes,
    REGISTRE_DEFAUT,
    registre_disponible,
)

# Sondes opt-in : sans la chaîne Lean, les classes couplées au registre
# se taisent (skip) au lieu d'échouer. Les classes pures restent actives.
necessite_registre = pytest.mark.skipif(
    not registre_disponible(),
    reason="sonde opt-in : registre Lean absent de cette machine",
)


def _noeuds_jouet():
    """Petit DAG à comptage manuel : couches {0:[A,B], 1:[C], 2:[D]}.

    Arêtes biparties complètes entre couches non vides consécutives :
    A→C, B→C, C→D → 2 routes (A→C→D, B→C→D).
    Retirer A → 1 route (Δ−1). Retirer C → le nœud et ses arêtes sont
    supprimés ; A, B, D deviennent orphelins (sources ET puits) → 3 routes
    dégénérées (Δ+1 — sémantique exacte de compter_routes_dag : les
    successeurs orphelins deviennent des sources, exhibée, pas un bug).
    """
    return [
        {"nom": "A", "statut": "CONDITIONNEL", "zone": "CONDITIONNEL",
         "tag": "technique (molle)"},
        {"nom": "B", "statut": "CONDITIONNEL", "zone": "CONDITIONNEL",
         "tag": "technique (molle)"},
        {"nom": "C", "statut": "CONDITIONNEL", "zone": "CONDITIONNEL",
         "tag": "mur (dur)"},
        {"nom": "D", "statut": "DÉMONTRÉ", "zone": "DÉMONTRÉ",
         "tag": "mur (dur, forteresse)"},
        {"nom": "M", "statut": "RÉFUTÉ", "zone": "RÉFUTÉ",
         "tag": "technique (molle)"},  # retiré du comptage (transition interdite)
    ]


def _donnees_jouet():
    route = [_noeuds_jouet()[0], _noeuds_jouet()[1]]
    terra = [_noeuds_jouet()[2]]
    g = construire_graphe(route, terra, [])
    noeuds = []
    for brut in g["noeuds_bruts"]:
        h = brut["_h"]
        noeuds.append({
            "id": brut["_id"], "nom": h["nom"], "bande": brut["_bande"],
            "x": brut["_x"], "y": brut["_y"], "statut": h["statut"],
            "zone": h["zone"], "trous": [], "contenu": "nœud jouet",
            "tag": h["tag"], "cout_estime": "", "chantiers": [],
            "fichier_ligne": "", "source": "test",
            "double_statut": None, "entropie": None, "oracle": None,
        })
    return {
        "mecanisme": "jouet", "type_mecanisme": "test",
        "statut_mecanisme": "test", "trous": [], "doubles_verdicts": [],
        "entropie": {"h_initial_bits": 1.5, "delta_h_total_bits": 0.25},
        "graphe": {"noeuds": noeuds, "aretes": g["aretes"],
                   "largeur": g["largeur"], "hauteur": g["hauteur"],
                   "bandes": g["bandes"]},
        "simulateur": construire_simulateur(noeuds),
        "notes": [], "integrite": {},
        "disclaimer": "Carte, pas territoire.",
        "interdiction": "INTERDICTION FORMELLE : aucun score A/B unique, aucune P(A).",
        "oracle_dossier": [],
        "couleurs_zones": {"CONDITIONNEL": "#b7791f"},
        "couleur_defaut": "#3b5bdb",
        "limites": "limites",
        "version_phi": "0.11.0", "horodatage": "2026-09-30T00:00:00+00:00",
    }


def _json_embarque(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="phi-donnees">(.*?)</script>',
                  html, re.S)
    assert m, "JSON embarqué introuvable dans le HTML"
    return json.loads(m.group(1))


def _cles_recursif(obj, trouvees):
    if isinstance(obj, dict):
        for k, v in obj.items():
            trouvees.add(k)
            _cles_recursif(v, trouvees)
    elif isinstance(obj, list):
        for v in obj:
            _cles_recursif(v, trouvees)


class TestSimulateurJouet:
    """Le simulateur recompte EXACTEMENT — contrôle manuel."""

    def test_comptage_avant(self):
        s = construire_simulateur(_noeuds_jouet())
        assert s["avant"]["nb_routes"] == 2
        assert s["avant"]["tailles_couches"] == [2, 1, 1]

    def test_what_if_manuel(self):
        s = construire_simulateur(_noeuds_jouet())
        par_nom = {h["nom"]: h for h in s["hypotheses"]}
        assert set(par_nom) == {"A", "B", "C"}  # D démontré, M réfuté : pas de bouton
        assert par_nom["A"]["routes_apres"] == 1
        assert par_nom["A"]["delta"] == -1
        assert par_nom["B"]["routes_apres"] == 1
        # Retirer C : A, B, D orphelins → 3 routes dégénérées (Δ+1).
        assert par_nom["C"]["routes_apres"] == 3
        assert par_nom["C"]["delta"] == +1

    def test_coherence_avec_compter_routes_dag(self):
        # Le simulateur délègue à compter_routes_dag : même fonction, même résultat.
        preds = {"A": [], "B": [], "C": ["A", "B"], "D": ["C"]}
        assert compter_routes_dag(preds, interdits=set())["nb_routes"] == 2
        assert compter_routes_dag(preds, interdits={"A"})["nb_routes"] == 1

    def test_cycle_message_propre(self):
        with pytest.raises(ValueError, match="cycle"):
            compter_routes_dag({"X": ["Y"], "Y": ["X"]})

    def test_vide_sans_crash(self):
        s = construire_simulateur([])
        assert s["avant"]["nb_routes"] == 0
        assert s["hypotheses"] == []


class TestInterdiction:
    """Aucun score A/B, aucune P(A) — ni dans le HTML ni dans le JSON embarqué."""

    def test_cles_interdites_absentes(self):
        d = _donnees_jouet()
        html = generer_html(d)
        embarque = _json_embarque(html)
        cles = set()
        _cles_recursif(embarque, cles)
        assert cles & CLES_INTERDITES == set()

    def test_mots_interdits_absents_du_html(self):
        # Balayage textuel : les tokens ne doivent apparaître ni comme clés
        # JSON ni dans la prose (l'interdiction elle-même dit « P(A) » en
        # toutes lettres — on la formule donc sans parenthèses ici).
        d = _donnees_jouet()
        html = generer_html(d)
        for tok in ("score_a", "score_b", "p_a", "p_b", "probabilite"):
            assert tok not in html.lower(), f"token interdit trouvé : {tok}"


class TestGardeFortFlottants:
    """Flottants uniquement sous les sections documentées."""

    def test_flottants_sous_sections_autorisees(self):
        d = _donnees_jouet()
        html = generer_html(d)
        embarque = _json_embarque(html)
        viols = []

        def _fl(o, ancetres):
            if isinstance(o, dict):
                for k, v in o.items():
                    a = ancetres | {k}
                    if isinstance(v, float) and not (a & SECTIONS_FLOTTANTS_OK):
                        viols.append((k, v))
                    _fl(v, a)
            elif isinstance(o, list):
                for v in o:
                    _fl(v, ancetres)

        _fl(embarque, set())
        assert viols == [], f"flottants hors sections : {viols}"


class TestOffline:
    """Le HTML est autonome : zéro dépendance réseau/CDN."""

    def test_aucune_ressource_reseau(self):
        d = _donnees_jouet()
        html = generer_html(d)
        # Seule URL autorisée : le namespace XML/SVG (identifiant, jamais fetché).
        sans_ns = html.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in sans_ns
        assert "https://" not in sans_ns
        assert "@import" not in sans_ns
        assert "XMLHttpRequest" not in sans_ns
        assert re.search(r"<script[^>]+src=", sans_ns) is None
        assert re.search(r"<link[^>]+href=", sans_ns) is None

    def test_echappement_fermeture_script(self):
        d = _donnees_jouet()
        d["graphe"]["noeuds"][0]["contenu"] = "</script><script>alert(1)</script>"
        html = generer_html(d)
        # La première fermeture </script> après l'ouverture du JSON doit être
        # la vraie : aucun contenu du registre ne peut la devancer.
        m = re.search(r'<script type="application/json" id="phi-donnees">(.*?)</script>',
                      html, re.S)
        assert m and "</script>" not in m.group(1)
        _ = json.loads(m.group(1))  # JSON toujours valide


@necessite_registre
class TestNonMutation:
    """Les objets Hypothese du registre ne sont jamais mutés."""

    def test_registre_intact_apres_construction(self):
        registre = RegistreSondes().charger(REGISTRE_DEFAUT)
        avant = {k: (v.statut, v.section_entropie, list(v.trous))
                 for k, v in registre.hypotheses.items()}
        construire_donnees_explorateur("energy_identity", registre=registre,
                                       dossier=None, avec_oracle=False)
        apres = {k: (v.statut, v.section_entropie, list(v.trous))
                 for k, v in registre.hypotheses.items()}
        assert avant == apres


@necessite_registre
class TestIntegration:
    """Un mécanisme réel du registre, de bout en bout (sans indexation dossier)."""

    def test_energy_identity(self):
        d = construire_donnees_explorateur("energy_identity", dossier=None,
                                           avec_oracle=False)
        assert d["mecanisme"] == "energy_identity"
        noeuds = d["graphe"]["noeuds"]
        assert len(noeuds) > 10
        bandes = {n["bande"] for n in noeuds}
        assert bandes == {"route", "terra_incognita", "voies_mortes"}
        # La route est une chaîne : n-1 arêtes pour n nœuds de route.
        n_route = sum(1 for n in noeuds if n["bande"] == "route")
        assert len(d["graphe"]["aretes"]) == n_route - 1
        assert d["simulateur"]["avant"]["nb_routes"] > 0
        assert "veto de Tomy" in d["disclaimer"]
        html = generer_html(d)
        assert "H30" in html  # un nom de nœud réel est présent
        assert 'id="simulateur"' in html

    def test_mecanisme_inconnu_erreur_propre(self):
        with pytest.raises(ValueError, match="[Ii]nconnu"):
            construire_donnees_explorateur("mecanisme_qui_n_existe_pas_xyz",
                                           dossier=None, avec_oracle=False)

    def test_explorer_vers_fichier(self, tmp_path):
        sortie = str(tmp_path / "explorateur_test.html")
        chemin = explorer_vers_fichier("BKM_criterion", dossier=None,
                                       sortie=sortie, avec_oracle=False)
        assert chemin == sortie
        html = open(sortie, encoding="utf-8").read()
        assert "BKM_criterion" in html
        _ = _json_embarque(html)  # JSON valide et extractible


class TestImportDirect:
    """explorateur.py : import direct uniquement, jamais de subprocess."""

    def test_pas_de_subprocess(self):
        import phi_complexity.explorateur as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "import subprocess" not in src
        assert "from subprocess" not in src
        for appel in ("subprocess.run", "subprocess.Popen", "subprocess.call",
                      "subprocess.check_output", "subprocess.check_call"):
            assert appel not in src
        assert "os.system" not in src
        assert "os.popen" not in src
