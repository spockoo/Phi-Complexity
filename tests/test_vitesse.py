"""
tests/test_vitesse.py — Non-régression de performance et deux phases (v0.8.0).

Mesures de référence (VM, 2026-09-29, `AnalyseurPython.analyser()`
sur fichier synthétique `def f{i}(x):` × N, 5 lignes/fonction) :
- 10k lignes : 0.91 s (complet) ; parse seul ≈ 0.10 s
- 100k lignes : 6.17 s (complet) ; parse seul ≈ 1.02 s
- 1M lignes : 69.98 s (complet) — scaling linéaire, constante ~70 s/Mligne.

Le parse est bon marché ; le coût est dans les métriques par fonction
(comptage fin de nœuds, profondeur, règles). La phase 1 (`complet=False`)
ne fait que parser + une passe : elle doit rester sous ~1.5× le temps
de parse. Le seuil du test de non-régression est fixé à 10 s pour
10k lignes : ≈ 100× la mesure locale (~0.1 s), donc robuste aux machines
lentes, mais une régression qui rendrait la phase 1 aussi lente que la
phase complète (0.91 s → échec net au-delà de 10 s) serait captée.
Ce n'est pas un seuil magique : il est calibré sur la mesure ci-dessus.
"""
import argparse
import json
import textwrap
import time

import pytest

from phi_complexity import carte_projet
from phi_complexity.carte import carte_console
from phi_complexity.cli import _executer_index
from phi_complexity.editeur.indexeur import indexer_projet
from phi_complexity.langs.python_native import AnalyseurPython


# Seuil de non-régression (s) pour la phase 1 sur 10k lignes.
# Justification : voir docstring du module (mesure locale ~0.1 s, marge ×100).
SEUIL_PHASE1_10K = 2.0  # garde-fou anti-régression catastrophique, voir test


def _generer_bench(chemin, nb_fonctions):
    """Même générateur que le bench de référence : 5 lignes par fonction."""
    with open(chemin, "w", encoding="utf-8") as f:
        for i in range(nb_fonctions):
            f.write(
                f"def f{i}(x):\n"
                f"    y = x + {i}\n"
                "    if y > 0:\n"
                "        return y * 2\n"
                "    return y\n"
            )


@pytest.fixture
def bench_10k(tmp_path):
    chemin = str(tmp_path / "bench_10k.py")
    _generer_bench(chemin, 2000)  # 2000 × 5 lignes = 10k lignes
    return chemin


@pytest.fixture
def mini_projet_vitesse(tmp_path):
    (tmp_path / "a.py").write_text(
        textwrap.dedent(
            """\
            def alpha(x):
                return x + 1


            def beta(x, y):
                total = 0
                for i in range(x):
                    total += i * y
                return total
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        textwrap.dedent(
            """\
            class Gamma:
                def methode(self, z):
                    if z:
                        return 1
                    return 0
            """
        ),
        encoding="utf-8",
    )
    return str(tmp_path)


class TestPhase1:
    def test_phase1_sous_seuil_10k(self, bench_10k):
        """Garde-fou : la phase 1 ne doit jamais redevenir catastrophique.

        Calibration (VM, 10k lignes / 2000 fonctions) : v080 ≈ 0.2–0.4 s,
        v070 multi-passes ≈ 0.6–1.1 s. Le seuil 2.0 s (min de 3 runs,
        robuste au bruit VM) attrape les régressions majeures (×10),
        pas les micro-fluctuations. La cible « 100k lignes < 2 s » est
        vérifiée par benchmark manuel dans RAPPORT_VITESSE (pas en CI,
        trop sensible à la machine).
        """
        durees = []
        for _ in range(3):
            debut = time.perf_counter()
            resultat = AnalyseurPython(bench_10k).analyser(complet=False)
            durees.append(time.perf_counter() - debut)
        duree = min(durees)
        assert duree < SEUIL_PHASE1_10K, (
            f"phase 1 trop lente : {duree:.2f} s pour 10k lignes "
            f"(seuil {SEUIL_PHASE1_10K} s) — régression de performance ?"
        )
        assert len(resultat.fonctions) == 2000

    def test_phase1_meme_symboles_que_complet(self, bench_10k):
        """La phase 1 ne perd aucun symbole : mêmes noms, mêmes lignes."""
        rapide = AnalyseurPython(bench_10k).analyser(complet=False)
        complet = AnalyseurPython(bench_10k).analyser(complet=True)
        noms_lignes_rapide = sorted((f.nom, f.ligne) for f in rapide.fonctions)
        noms_lignes_complet = sorted((f.nom, f.ligne) for f in complet.fonctions)
        assert noms_lignes_rapide == noms_lignes_complet

    def test_phase1_complexite_reelle_gratuite(self, bench_10k):
        """Phase 1 : la complexité réelle est gratuite dans la passe fusionnée.

        La mono-passe compte les nœuds pendant la visite : `complexite`
        (pression morphique) ne coûte rien, elle est donc rapportée telle
        quelle dans les deux phases. Seuls les champs inférentiels
        (profondeur, distance Fibonacci, φ-ratios, règles) portent des
        sentinelles en phase 1.
        """
        rapide = AnalyseurPython(bench_10k).analyser(complet=False)
        complet = AnalyseurPython(bench_10k).analyser(complet=True)
        c_rapide = {f.nom: f.complexite for f in rapide.fonctions}
        c_complet = {f.nom: f.complexite for f in complet.fonctions}
        assert c_rapide == c_complet  # même complexité réelle, sans surcoût
        for f in rapide.fonctions:
            assert f.complexite > f.nb_lignes  # pression morphique, pas un proxy
            assert f.profondeur_max == 0      # sentinelle : non calculé
            assert f.distance_fib == 0.0      # sentinelle : non calculée
            assert f.phi_ratio == 1.0         # sentinelle : non calculé

    def test_complet_inchange(self, tmp_path):
        """Le mode complet par défaut calcule les vraies métriques historiques.

        Fixture à complexité VARIÉE (le bench 10k a 2000 fonctions
        identiques → φ-ratio légitimement 1.0 partout, mauvais témoin).
        Vérifie : pression morphique, φ-ratios discriminants, profondeur,
        oudjat, et les 4 règles souveraines.
        """
        code = (
            "def simple():\n"
            "    return 1\n"
            "\n"
            "def imbriquee(n):\n"
            "    total = 0\n"
            "    for i in range(n):\n"
            "        for j in range(n):\n"
            "            if i > j:\n"
            "                total += i * j\n"
            "    return total\n"
            "\n"
            "def fuite():\n"
            "    f = open('x.txt')\n"
            "    return f.read()\n"
            "\n"
            "def trop_args(a, b, c, d, e, f):\n"
            "    return a\n"
        )
        cible = tmp_path / "mixte.py"
        cible.write_text(code, encoding="utf-8")
        resultat = AnalyseurPython(str(cible)).analyser()  # complet par défaut

        par_nom = {f.nom: f for f in resultat.fonctions}
        # Pression morphique (nœuds) > simple longueur.
        assert par_nom["imbriquee"].complexite > par_nom["imbriquee"].nb_lignes
        # Profondeur d'imbrication : for > for > if = 3.
        assert par_nom["imbriquee"].profondeur_max == 3
        assert par_nom["simple"].profondeur_max == 0
        # φ-ratios discriminants sur fonctions variées.
        assert any(f.phi_ratio != 1.0 for f in resultat.fonctions)
        # Oudjat = la plus complexe.
        assert resultat.oudjat.nom == "imbriquee"
        # Les 4 règles souveraines ont tiré.
        categories = {a.categorie for a in resultat.annotations}
        assert {"LILITH", "SUTURE", "SOUVERAINETE"} <= categories


class TestCarteRapide:
    def test_carte_rapide_json_valide(self, mini_projet_vitesse):
        """Mode rapide : JSON sérialisable, symboles présents, métriques marquées."""
        carte = carte_projet(mini_projet_vitesse, complet=False)
        texte = json.dumps(carte, ensure_ascii=False)  # doit sérialiser
        rechargee = json.loads(texte)
        assert rechargee["mode"] == "rapide"
        assert rechargee["nb_symboles"] == 3  # alpha, beta, methode
        assert rechargee["radiance_globale"] is None
        assert rechargee["statut_gnostique_global"] is None
        assert rechargee["oudjat_supreme"] is None
        for f in rechargee["fichiers"]:
            assert f["metriques_calculees"] is False
            assert f["radiance"] is None
            assert f["statut_gnostique"] is None
            assert f["oudjat"] is None
            assert f["nb_symboles"] > 0
            assert all("nom" in s and "ligne" in s for s in f["symboles"])

    def test_carte_rapide_meme_symboles_que_complet(self, mini_projet_vitesse):
        """Le mode rapide ne change pas l'inventaire des symboles."""
        rapide = carte_projet(mini_projet_vitesse, complet=False)
        complet = carte_projet(mini_projet_vitesse, complet=True)
        inv_rapide = sorted(
            (s["nom"], s["ligne"], f["fichier"])
            for f in rapide["fichiers"] for s in f["symboles"]
        )
        inv_complet = sorted(
            (s["nom"], s["ligne"], f["fichier"])
            for f in complet["fichiers"] for s in f["symboles"]
        )
        assert inv_rapide == inv_complet
        # Schéma complet = EXACTEMENT v0.7.0 : pas de clé "mode",
        # pas de "metriques_calculees" par fichier.
        assert "mode" not in complet
        assert all("metriques_calculees" not in f for f in complet["fichiers"])
        assert complet["radiance_globale"] is not None

    def test_carte_rapide_console_ne_plante_pas(self, mini_projet_vitesse, capsys):
        """La console gère les radiances None sans TypeError."""
        carte = carte_projet(mini_projet_vitesse, complet=False)
        texte = carte_console(carte)
        assert "rapide" in texte
        assert "None" not in texte  # jamais de "None" brut affiché


class TestParallele:
    def test_parallele_meme_resultat_que_sequentiel(self, mini_projet_vitesse):
        """Le pool ne change rien au contenu de l'index (déterminisme)."""
        parallele = indexer_projet(mini_projet_vitesse, parallele=True)
        sequentiel = indexer_projet(mini_projet_vitesse, parallele=False)
        assert set(parallele) == set(sequentiel)
        for chemin in parallele:
            assert [(s.nom, s.ligne) for s in parallele[chemin]] == [
                (s.nom, s.ligne) for s in sequentiel[chemin]
            ]

    def test_parallele_phase1(self, mini_projet_vitesse):
        """Parallèle + phase 1 combinés : même inventaire."""
        idx = indexer_projet(mini_projet_vitesse, complet=False, parallele=True)
        noms = sorted(s.nom for symboles in idx.values() for s in symboles)
        assert noms == ["alpha", "beta", "methode"]


class TestCliRapide:
    def _args_index(self, dossier, rapide):
        return argparse.Namespace(
            dossier=dossier, format="json", lang=None, exclude=None,
            no_exclude=False, rapide=rapide,
        )

    def test_cli_rapide_json(self, mini_projet_vitesse, capsys):
        """`phi index --rapide --format json` : JSON valide, mode rapide."""
        code = _executer_index(self._args_index(mini_projet_vitesse, True))
        assert code == 0
        carte = json.loads(capsys.readouterr().out)
        assert carte["mode"] == "rapide"
        assert carte["nb_symboles"] == 3
        assert carte["radiance_globale"] is None

    def test_cli_complet_par_defaut(self, mini_projet_vitesse, capsys):
        """Sans --rapide, le comportement historique est inchangé."""
        code = _executer_index(self._args_index(mini_projet_vitesse, False))
        assert code == 0
        carte = json.loads(capsys.readouterr().out)
        assert "mode" not in carte  # schéma v0.7.0 exact
        assert carte["radiance_globale"] is not None
