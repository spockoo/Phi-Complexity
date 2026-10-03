"""
tests/test_garde_non_prescriptif.py — Garde constitutionnelle du témoin.

Règle ABSOLUE (Tomy) : l'instrument MONTRE, il ne DÉCIDE jamais.
Aucun module témoin (radar, sismique, registre d'observations) ne doit
émettre de langage prescriptif — ni dans son code (littéraux de chaînes),
ni dans ses sorties réelles (détecteurs exercés sur fixtures).

Comme la garde mission.py : MÉCANIQUE, pas disciplinaire. Si ce test
échoue, c'est que le témoin a commencé à juger — corriger le module,
jamais le test.
"""
import ast
import os

import pytest

from phi_complexity.radar import LISTE_NOIRE_PRESCRIPTIF

#: Modules témoins soumis à la garde (étendu à chaque phase).
MODULES_TEMOIN = ["radar", "sismique", "registre_observations"]


def _repertoire_paquet():
    import phi_complexity
    return os.path.dirname(phi_complexity.__file__)


def _litteraux(chemin):
    """Tous les littéraux de chaînes d'un module (via ast, stdlib)."""
    with open(chemin, encoding="utf-8") as fh:
        arbre = ast.parse(fh.read(), filename=chemin)
    chaines = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            chaines.append(noeud.value)
    return chaines


def _contient_prescriptif(texte):
    """Vrai si le texte contient une phrase prescriptive qui n'est pas
    la définition même de la liste noire."""
    bas = texte.lower()
    formes = {t.lower() for t in LISTE_NOIRE_PRESCRIPTIF}
    if bas.strip() in formes:
        return None  # c'est un élément de la définition même
    for interdit in LISTE_NOIRE_PRESCRIPTIF:
        if interdit in bas:
            return interdit
    return None


class TestGardeNonPrescriptif:
    def test_modules_temoin_existants(self):
        rep = _repertoire_paquet()
        for mod in MODULES_TEMOIN:
            assert os.path.isfile(os.path.join(rep, mod + ".py")), \
                f"module témoin manquant : {mod}"

    def test_statique_litteraux(self):
        """Aucun littéral de chaîne des modules témoins ne porte de
        langage prescriptif (hors définition de la liste noire)."""
        rep = _repertoire_paquet()
        for mod in MODULES_TEMOIN:
            chemin = os.path.join(rep, mod + ".py")
            for lit in _litteraux(chemin):
                interdit = _contient_prescriptif(lit)
                assert interdit is None, \
                    f"{mod}.py : langage prescriptif '{interdit}' dans " \
                    f"le littéral : {lit[:80]!r}"

    def test_dynamique_sorties_radar(self):
        """Les sorties réelles des détecteurs (console + JSON) ne
        portent aucun langage prescriptif."""
        import json
        import tempfile
        from phi_complexity.radar import (
            comparer, formater_console, scanner, vers_dict,
        )
        with tempfile.TemporaryDirectory() as tmp:
            avant = os.path.join(tmp, "avant")
            apres = os.path.join(tmp, "apres")
            os.mkdir(avant)
            os.mkdir(apres)
            open(os.path.join(avant, "a.lean"), "w",
                 encoding="utf-8").write("theorem foo : 1 = 1 := by rfl\n")
            open(os.path.join(apres, "a.lean"), "w",
                 encoding="utf-8").write(
                     "theorem foo : 2 = 2 := by sorry\n"
                     "axiom mon_axiome : True\n")
            open(os.path.join(apres, "b.lean"), "w",
                 encoding="utf-8").write("def dup : Nat := 1\n")
            open(os.path.join(apres, "c.lean"), "w",
                 encoding="utf-8").write("def dup : Nat := 2\n")
            obs = comparer(scanner(avant), scanner(apres))
            assert len(obs) >= 4  # la fixture doit exercer les détecteurs
            sorties = formater_console(obs) + "\n" + json.dumps(
                vers_dict(obs), ensure_ascii=False)
            for interdit in LISTE_NOIRE_PRESCRIPTIF:
                assert interdit not in sorties.lower(), \
                    f"langage prescriptif '{interdit}' dans une sortie radar"

    def test_liste_noire_non_vide(self):
        assert len(LISTE_NOIRE_PRESCRIPTIF) >= 5
