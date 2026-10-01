"""tests/test_entropie.py — Lentille entropique sur la Sonde B.

Doctrine : l'erreur minimisée fait foi. Chaque test est une affirmation
revérifiable par commande : `python3 -m pytest tests/test_entropie.py -q`.

Point d'honnêteté central (documenté dans ENTROPIE_SONDEB_20260930.md) :
ΔH ≥ 0 sur réfutation est FAUX en général — contre-exemple (0.9, 0.05, 0.05),
terminer le nœud à 0.9 → H : 0.569 → 1.000 (ΔH < 0 : la réfutation a terminé le favori,
l'attention se disperse — signal d'alarme, pas une erreur). Les tests ci-dessous
vérifient le ΔH SIGNÉ et sa sémantique documentée, pas une positivité inventée.
"""

import json
import math
import os

import pytest

from phi_complexity.entropie import (
    H_PHI_ANALYTIQUE,
    P_FERME_PHIDEIEN,
    P_OUVERT_PHIDEIEN,
    PHI,
    distribution_empirique,
    entropie_certifiee,
    entropie_depuis_sonde,
    prior_phideien,
    rattacher_obstruction,
    rendre_entropie_console,
    renormaliser,
    tracer_resserrement,
)
from phi_complexity.sondes import (
    CLES_INTERDITES,
    Hypothese,
    REGISTRE_DEFAUT,
    registre_disponible,
)

# Sondes opt-in : sans la chaîne Lean, les tests couplés au registre
# se taisent (skip) au lieu d'échouer. Les tests purs restent actifs.
necessite_registre = pytest.mark.skipif(
    not registre_disponible(),
    reason="sonde opt-in : registre Lean absent de cette machine",
)
CHAINE_LEAN = os.path.dirname(REGISTRE_DEFAUT)


def _h(nom: str, statut: str = "CONDITIONNEL") -> Hypothese:
    return Hypothese(nom=nom, statut=statut)


# ────────────────────────────────────────────────────────
# H_φ — la dérivation de Tomy, reproduite à 1e-9 près
# ────────────────────────────────────────────────────────

def test_h_phi_valeur_analytique():
    # H_φ = (3−φ)·log₂φ ≈ 0.959419 bit (document PDF de Tomy, vérifié 2026-09-30).
    assert abs(H_PHI_ANALYTIQUE - 0.959419) < 1e-6


def test_h_phi_phideien():
    # Sur une partition binaire {ouvert, fermé}, le prior phidéien vaut
    # exactement (φ⁻¹, φ⁻²) et son entropie DOIT valoir H_φ à 1e-9 près.
    noeuds = [_h("ouvert", "CONDITIONNEL"), _h("ferme", "RÉFUTÉ")]
    pi = prior_phideien(noeuds)
    assert abs(pi[0] - P_OUVERT_PHIDEIEN) < 1e-15
    assert abs(pi[1] - P_FERME_PHIDEIEN) < 1e-15
    h, borne = entropie_certifiee(pi)
    assert abs(h - H_PHI_ANALYTIQUE) < 1e-9
    assert borne >= 0.0


def test_prior_phideien_somme_un():
    noeuds = [_h("a"), _h("b", "NON-ATTAQUÉ"), _h("c", "RÉFUTÉ"), _h("d", "DÉMONTRÉ")]
    pi = prior_phideien(noeuds)
    assert abs(math.fsum(pi) - 1.0) < 1e-12
    # macro-classes : ouvert = a, b (φ⁻¹/2 chacun) ; fermé = c, d (φ⁻²/2 chacun)
    assert abs(pi[0] - P_OUVERT_PHIDEIEN / 2) < 1e-15
    assert abs(pi[2] - P_FERME_PHIDEIEN / 2) < 1e-15


def test_prior_phideien_classe_vide():
    # Si une macro-classe est vide, sa masse est transférée (reste une distribution).
    noeuds = [_h("a"), _h("b")]
    pi = prior_phideien(noeuds)
    assert abs(math.fsum(pi) - 1.0) < 1e-12
    assert all(p == pytest.approx(0.5) for p in pi)


# ────────────────────────────────────────────────────────
# Entropie certifiée (EFT)
# ────────────────────────────────────────────────────────

def test_entropie_uniforme():
    n = 8
    h, borne = entropie_certifiee([1.0 / n] * n)
    assert abs(h - 3.0) < 1e-9  # log₂(8) = 3 bits
    assert borne >= 0.0


def test_entropie_noeud_unique():
    h, _ = entropie_certifiee([1.0])
    assert h == pytest.approx(0.0, abs=1e-12)


def test_entropie_borne_couvre():
    # |H_eft − H_flottant| ≤ borne (portée EFT : la sommation, à entrées fixées).
    poids = [0.5, 0.25, 0.125, 0.0625, 0.0625]
    h_eft, borne = entropie_certifiee(poids)
    h_flottant = -math.fsum(w * math.log2(w) for w in poids)
    assert abs(h_eft - h_flottant) <= borne + 1e-15
    assert borne < 1e-20  # ordre de grandeur attendu (~n·u²)


def test_distribution_normalisee():
    noeuds = [_h("a"), _h("b"), _h("c")]
    poids, meta = distribution_empirique(noeuds, {"a": 9.4, "b": 2.7, "c": 5.1})
    assert abs(math.fsum(poids) - 1.0) < 1e-12
    assert meta["nb_apparies"] == 3


def test_distribution_repli_mediane():
    # Nœud sans symbole → médiane des postérieurs appariés (exhibé, pas inventé).
    noeuds = [_h("a"), _h("fantome")]
    poids, meta = distribution_empirique(noeuds, {"a": 4.0})
    assert meta["nb_apparies"] == 1
    assert meta["repli"] == "mediane_posterieurs"
    assert poids[0] == pytest.approx(poids[1])  # médiane d'un seul = lui-même


def test_distribution_aucun_appariement_uniforme():
    noeuds = [_h("x"), _h("y")]
    poids, meta = distribution_empirique(noeuds, {})
    assert meta["repli"] == "uniforme_aucun_appariement"
    assert poids == pytest.approx([0.5, 0.5])


# ────────────────────────────────────────────────────────
# Rattachement obstruction → nœuds (conservateur)
# ────────────────────────────────────────────────────────

def test_rattachement_hnumero():
    noeuds = [_h("H26"), _h("AutreHypothese")]
    tues = rattacher_obstruction("H26 `TimeLocalizationHypothesis` — RÉFUTÉE (ch.94)",
                                 noeuds)
    assert tues == [0]


def test_rattachement_nom_backtick():
    noeuds = [_h("TransferDissipationContinuity"), _h("AutreHypothese")]
    tues = rattacher_obstruction("`TransferDissipationContinuity` (H2) — SONDE", noeuds)
    assert tues == [0]


def test_rattachement_conservateur_vide():
    noeuds = [_h("SchwartzPropagationHypothesis")]
    assert rattacher_obstruction("[G4] — incompatibilité de norme (trou 4)", noeuds) == []


# ────────────────────────────────────────────────────────
# Trace du resserrement : signe, téslescopage, monotonie du support
# ────────────────────────────────────────────────────────

def test_obstruction_non_rattachable_exhibee():
    # Une obstruction sans clé rattachable n'est ni comptée ni ignorée en silence.
    poids = [0.9, 0.05, 0.05]
    noeuds = [_h("alpha"), _h("beta"), _h("gamma")]
    evs, h_init, _, h_fin, _, notes = tracer_resserrement(
        noeuds, poids, [("obstruction générique sans clé (ch.1)", "test")])
    assert evs[0].rattache is False
    assert evs[0].delta_h_bits == 0.0
    assert h_fin == pytest.approx(h_init)  # rien tué : H inchangé


def test_delta_signe_semantique():
    # Même distribution, titres rattachables : le signe distingue les deux cas.
    poids = [0.9, 0.05, 0.05]
    noeuds = [_h("favori"), _h("secondaire"), _h("c")]
    evs, h_init, _, h_fin, _, _ = tracer_resserrement(
        noeuds, poids, [("tue `favori`", "test"), ("tue `secondaire`", "test")])
    assert evs[0].rattache and evs[1].rattache
    assert evs[0].delta_h_bits < 0, "terminer le favori (0.9) DOIT disperser (ΔH<0)"
    assert "alarme" in evs[0].note
    # Après la fin du favori : (0.5, 0.5) ; terminer "secondaire" → (1.0) : H → 0.
    assert evs[1].delta_h_bits > 0, "élaguer ensuite concentre (ΔH>0)"


def test_telescopage_additif():
    poids = [0.5, 0.3, 0.15, 0.05]
    noeuds = [_h("a"), _h("b"), _h("c"), _h("d")]
    evs, h_init, _, h_fin, _, _ = tracer_resserrement(
        noeuds, poids, [("tue `b`", "t1"), ("tue `d`", "t2")])
    somme = math.fsum(e.delta_h_bits for e in evs)
    assert somme == pytest.approx(h_init - h_fin, abs=1e-9)


def test_support_monotone():
    # Le support (nœuds vivants) ne fait que rétrécir — le resserrement est monotone
    # en support même quand ΔH change de signe.
    poids = [0.9, 0.05, 0.05]
    noeuds = [_h("favori"), _h("b"), _h("c")]
    w = list(poids)
    supports = [sum(1 for x in w if x > 0)]
    for titre in ("tue `favori`", "tue `b`"):
        idx = rattacher_obstruction(titre, noeuds)
        w = renormaliser(w, idx)
        supports.append(sum(1 for x in w if x > 0))
    assert supports == sorted(supports, reverse=True)


def test_masse_tuee_exacte():
    poids = [0.5, 0.3, 0.2]
    noeuds = [_h("a"), _h("b"), _h("c")]
    evs, _, _, _, _, _ = tracer_resserrement(noeuds, poids, [("tue `b`", "t")])
    assert evs[0].masse_tuee == pytest.approx(0.3)


def test_renormaliser_tout_tue():
    w = renormaliser([0.5, 0.5], [0, 1])
    assert w == [0.0, 0.0]
    h, _ = entropie_certifiee([x for x in w if x > 0] or [1.0])
    assert h == pytest.approx(0.0, abs=1e-12)


# ────────────────────────────────────────────────────────
# Intégration : mécanisme inconnu, interdiction, bandeau
# ────────────────────────────────────────────────────────

@necessite_registre
def test_mecanisme_inconnu_ne_crash_pas():
    res = entropie_depuis_sonde("zz_mecanisme_inexistant_zzz",
                                chemin_registre=REGISTRE_DEFAUT,
                                dossier=CHAINE_LEAN)
    assert res.noeuds == []
    assert res.type_mecanisme == "inconnu"


def _balayer_cles_interdites(obj, trouves):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CLES_INTERDITES:
                trouves.append(k)
            _balayer_cles_interdites(v, trouves)
    elif isinstance(obj, list):
        for v in obj:
            _balayer_cles_interdites(v, trouves)


@necessite_registre
def test_interdiction_json():
    # Aucune clé de CLES_INTERDITES dans toute la sortie JSON — comme les sondes.
    res = entropie_depuis_sonde("BKM_criterion",
                                chemin_registre=REGISTRE_DEFAUT,
                                dossier=CHAINE_LEAN)
    d = res.vers_dict()
    json.dumps(d, ensure_ascii=False)  # sérialisable
    trouves: list = []
    _balayer_cles_interdites(d, trouves)
    assert trouves == [], f"clés interdites trouvées : {trouves}"


@necessite_registre
def test_console_bandeau_interdiction():
    res = entropie_depuis_sonde("BKM_criterion",
                                chemin_registre=REGISTRE_DEFAUT,
                                dossier=CHAINE_LEAN)
    texte = rendre_entropie_console(res)
    assert "INTERDICTION FORMELLE" in texte
    assert "jamais P(A)" in texte or "aucune probabilité P(A)" in texte
    assert "ΔH" in texte


@necessite_registre
def test_bkm_criterion_trace_reelle():
    # Application registry-grounded : la sonde B de BKM_criterion porte H21.
    res = entropie_depuis_sonde("BKM_criterion",
                                chemin_registre=REGISTRE_DEFAUT,
                                dossier=CHAINE_LEAN)
    assert len(res.noeuds) > 0
    rattaches = [e for e in res.evenements if e.rattache]
    assert len(rattaches) >= 1  # au moins H21
    # Télescopage exact (le signe de chaque ΔH est libre et documenté) :
    somme = math.fsum(e.delta_h_bits for e in res.evenements)
    assert somme == pytest.approx(res.h_initial_bits - res.h_finale_bits, abs=1e-9)


@necessite_registre
def test_determinisme():
    kw = dict(chemin_registre=REGISTRE_DEFAUT,
              dossier=CHAINE_LEAN)
    r1 = entropie_depuis_sonde("BKM_criterion", **kw)
    r2 = entropie_depuis_sonde("BKM_criterion", **kw)
    assert r1.h_initial_bits == r2.h_initial_bits
    assert r1.h_finale_bits == r2.h_finale_bits
    assert [e.delta_h_bits for e in r1.evenements] == [e.delta_h_bits for e in r2.evenements]


# ──────────────────────────────────────────────────────────────
# §8. ÉTALONS « BIT GÉNÉRATEUR » (étude Tomy, arithmétique vérifiée 2026-09-30)
# ──────────────────────────────────────────────────────────────
import random as _random

from phi_complexity.entropie import (
    COUT_BOOLEANISATION_ETALON,
    H_TOP_GOLDEN_SHIFT,
    compter_routes_dag,
    cout_booleanisation,
    dag_couches_attaque,
    diagnostic_branchement,
    fib_exact,
    masses_par_zone,
    routes_golden_shift,
)


def test_etalon_booleanisation_valeur():
    # ΔH = 1 − H_φ ≈ 0.0405812718 (H_φ = 0.9594187282, vérifié indépendamment).
    assert abs(H_PHI_ANALYTIQUE - 0.9594187282) < 1e-9
    assert abs(COUT_BOOLEANISATION_ETALON - 0.0405812718) < 1e-9
    assert abs(COUT_BOOLEANISATION_ETALON - (1.0 - H_PHI_ANALYTIQUE)) < 1e-12


def test_etalon_h_top_valeur():
    # h_top = log₂φ ≈ 0.6942419136 (Perron-Frobenius sur [[1,1],[1,0]]).
    assert abs(H_TOP_GOLDEN_SHIFT - 0.6942419136) < 1e-9
    assert abs(H_TOP_GOLDEN_SHIFT - math.log2(PHI)) < 1e-12


def test_cout_booleanisation_positif_aleatoire():
    # Théorème de coarsening : l'effondrement ne crée jamais d'information.
    rng = _random.Random(20260930)
    for _ in range(50):
        masses = {z: rng.random() * 3 for z in
                  ("DÉMONTRÉ", "CONDITIONNEL", "RÉFUTÉ", "NON-ATTAQUÉ")}
        bc = cout_booleanisation(masses)
        assert bc["booleanization_cost_bits"] >= -1e-12
        assert bc["h_4_zones_bits"] >= bc["h_booleen_bits"] - 1e-12
        assert bc["borne_cout"] < 2e-28


def test_cout_booleanisation_nul_si_deja_booleen():
    # Tout dans {DÉMONTRÉ} vs {RÉFUTÉ} : l'effondrement est l'identité.
    bc = cout_booleanisation({"DÉMONTRÉ": 0.6, "RÉFUTÉ": 0.4})
    assert abs(bc["booleanization_cost_bits"]) < 1e-12
    # Tout d'un seul côté : H_4 = H_2 = 0.
    bc2 = cout_booleanisation({"NON-ATTAQUÉ": 1.0})
    assert abs(bc2["booleanization_cost_bits"]) < 1e-12
    assert abs(bc2["h_4_zones_bits"]) < 1e-12


def test_cout_booleanisation_vide():
    bc = cout_booleanisation({})
    assert bc["booleanization_cost_bits"] == 0.0
    assert bc["ratio_vs_etalon"] == 0.0


def test_fib_exact_et_golden_shift():
    assert fib_exact(0) == 0 and fib_exact(1) == 1 and fib_exact(12) == 144
    assert fib_exact(100) == 354224848179261915075
    # N(10) = F_12 = 144 — la valeur vérifiée de l'étude.
    assert routes_golden_shift(10) == 144
    # Récurrence linéaire exacte N(k) = N(k-1) + N(k-2).
    for k in range(2, 30):
        assert routes_golden_shift(k) == routes_golden_shift(k - 1) + routes_golden_shift(k - 2)
    with pytest.raises(ValueError):
        routes_golden_shift(-1)


def test_compter_routes_dag_chemin_simple():
    # a → b → c : une seule route.
    r = compter_routes_dag({"a": [], "b": ["a"], "c": ["b"]})
    assert r["nb_routes"] == 1
    assert r["comptes"] == {"a": 1, "b": 1, "c": 1}


def test_compter_routes_dag_diamant():
    # a → b, a → c, b → d, c → d : deux routes.
    r = compter_routes_dag({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
    assert r["nb_routes"] == 2
    assert r["comptes"]["d"] == 2


def test_compter_routes_dag_interdits_elaguent():
    preds = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    plein = compter_routes_dag(preds)
    elague = compter_routes_dag(preds, interdits={"b"})
    assert plein["nb_routes"] == 2
    assert elague["nb_routes"] == 1  # la réfutation a tué une route
    assert elague["interdits_retires"] == ["b"]


def test_compter_routes_dag_golden_shift():
    # DAG en couches du shift doré : couches {0_i, 1_i}, arêtes 0→0, 0→1, 1→0
    # (« pas de 11 »). Le compteur générique doit retrouver F_{k+2} exactement.
    for k in (1, 2, 5, 10):
        preds = {}
        for i in range(k):
            preds[f"z{i}"] = [f"z{i-1}", f"u{i-1}"] if i else []
            preds[f"u{i}"] = [f"z{i-1}"] if i else []
        r = compter_routes_dag(preds)
        assert r["nb_routes"] == routes_golden_shift(k) == fib_exact(k + 2), k


def test_compter_routes_dag_cycle_refuse():
    with pytest.raises(ValueError, match="cycle"):
        compter_routes_dag({"a": ["b"], "b": ["a"]})


def test_diagnostic_branchement_converge_vers_h_top():
    # log₂(F_{k+3}/F_{k+2}) → log₂φ quand k grand (taux de Perron-Frobenius).
    for k in (10, 30, 60):
        taux = diagnostic_branchement(fib_exact(k + 2), fib_exact(k + 3))
        assert abs(taux - H_TOP_GOLDEN_SHIFT) < 10 ** (-k // 6), k
    with pytest.raises(ValueError):
        diagnostic_branchement(0, 5)


def _noeuds_synthetiques():
    return [
        Hypothese(nom="h_ouverte", statut="CONDITIONNEL", tag="technique (molle)"),
        Hypothese(nom="h_dure", statut="EN-COURS", tag="mur (dur)"),
        Hypothese(nom="h_forteresse", statut="CONJECTURAL", tag="mur (dur, forteresse)"),
        Hypothese(nom="h_morte", statut="RÉFUTÉ", tag="mur (dur)"),
        Hypothese(nom="h_terra", statut="NOMMÉ (récolté au journal)", tag="non tagué",
                  source="journal §12 (Ch.139)"),
    ]


def test_masses_par_zone_agrege():
    noeuds = _noeuds_synthetiques()
    poids = [0.5, 0.25, 0.125, 0.0625, 0.0625]
    m = masses_par_zone(noeuds, poids)
    # zone_sonde : EN-COURS/CONJECTURAL → CONDITIONNEL ; récolté au journal
    # sans attaque → NON-ATTAQUÉ ; RÉFUTÉ → RÉFUTÉ.
    assert abs(m["CONDITIONNEL"] - (0.5 + 0.25 + 0.125)) < 1e-12
    assert abs(m["RÉFUTÉ"] - 0.0625) < 1e-12
    assert abs(m["NON-ATTAQUÉ"] - 0.0625) < 1e-12


def test_dag_couches_attaque_modele_reference():
    noeuds = _noeuds_synthetiques()
    rt = dag_couches_attaque(noeuds)
    # Couches : rang 0 {h_ouverte}, rang 1 {h_dure} (h_morte RÉFUTÉE retirée),
    # rang 2 {h_forteresse}, rang 4 {h_terra} → 1×1×1×1 = 1 route.
    assert rt["nb_couches"] == 4
    assert rt["tailles_couches"] == [1, 1, 1, 1]
    assert rt["nb_routes_admissibles"] == 1
    assert rt["reference_golden_shift"]["N_k_equals_F_k_plus_2"] == fib_exact(6)
    assert "MODÈLE DE RÉFÉRENCE" in rt["modele"]
    assert rt["taux_branchement_mesure_bits"] is not None


@necessite_registre
def test_booleanisation_et_routes_sur_mecanisme_reel():
    kw = dict(chemin_registre=REGISTRE_DEFAUT,
              dossier=CHAINE_LEAN)
    res = entropie_depuis_sonde("BKM_criterion", **kw)
    bc = res.booleanisation
    assert bc["booleanization_cost_bits"] >= -1e-12
    assert abs(bc["etalon_or_bits"] - COUT_BOOLEANISATION_ETALON) < 1e-15
    assert abs(bc["ratio_vs_etalon"]
               - bc["booleanization_cost_bits"] / COUT_BOOLEANISATION_ETALON) < 1e-9
    rt = res.routes
    assert rt["nb_routes_admissibles"] >= 1
    assert rt["reference_golden_shift"]["h_top_bits_par_symbole"] == H_TOP_GOLDEN_SHIFT
    d = res.vers_dict()
    assert "booleanization_cost" in d and "routes_admissibles" in d
    assert "etalons_bit_generateur" in d
    assert abs(d["etalons_bit_generateur"]["cout_booleanisation_or_bits"]
               - 0.0405812718) < 1e-9
