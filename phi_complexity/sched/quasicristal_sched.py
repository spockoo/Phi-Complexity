#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox_mp.quasicristal_sched -- ordonnanceur aperiódique a strategie de densite
injectee (chantier ORDONNANCEUR, mission RUCHE-QUASICRISTAL-GRANULARITE).

PHASE 1 (livree) : squelette + interface + prescriptions §9.6.
PHASE 2 (livree, spec FONDATIONS-MATH
quasicristal_granularite/MATH_QUASICRISTAL_GRANULARITE.md) : l'algorithme
reel est implemente dans StrategieDensiteQuasicristal — affectation directe
par rotation sturmienne (rafales fines) + ordonnancement guide par densite
V1 (pipeline labo en stdlib pure) + routage auto vers le comportement
charge-aware existant pour les benches lourds heterogenes. L'interface
StrategieDensite.ordonner() est INCHANGEE ; deux capacites OPTIONNELLES
decouvertes par duck-typing (designer_worker, definir_n_workers) portent
l'affectation directe sans toucher au contrat.

1. POURQUOI (diagnostic §9.5 du rapport)
---------------------------------------
L'infra integree (pool persistant + Scheduler a scrutation) a ete REJETEE :
W1 1,083x vs prototype naif 1,474x. Deux sources mesurees :
  (a) latence de dispatch ~0,13-0,22 s/unite : le dispatch est A SCRUTATION
      (thread dispatch + moniteur pool a 0,1 s) — chaque unite peut attendre
      un cycle avant d'etre prise en charge ;
  (b) cout fixe ~0,30 s par map() : K passages `soumettre` + cycle
      ordonnanceur + attendre() + resultats() + health_check().
Tomy propose un ordonnancement quasicristallin (aperiodique, deterministe)
pour la granularite fine. Ce module en est le point d'integration.

2. PRESCRIPTIONS §9.6 -> CORRESPONDANCE
---------------------------------------
  §9.6.1 dispatch a reveil (pas de scrutation 0,1 s)
      -> _boucle_dispatch() : le thread dispatch DORT sur une Condition
         (self._cond.wait(timeout=...)) et ne se reveille QUE sur evenement
         (soumission, fin de bench, arret) ou a la prochaine echeance de
         detection de blocage (_prochaine_echeance). Latence de dispatch
         = reveil de thread (~ms), plus un quantum de 0,1 s.
         Test : test_dispatch_a_reveil (latence mediane < 0,06 s).
  §9.6.2 batch de soumission (un seul passage, pas K)
      -> soumettre_lot(benches) : un seul passage sous verrou, une seule
         notification au dispatch. Le dispatch assigne en outre EN BATCH
         (tous les candidats lancables par reveil, dans l'ordre de la
         strategie), pas un par cycle.
  §9.6.3 health_check() hors du chemin critique de map()
      -> CONCERNE BenchInfraRunner.map (orchestrateur.py) : NON MODIFIE en
         Phase 1 (changer meta["t_total"] altererait la semantique mesuree
         du protocole ; O5 = 0,07 ms, negligeable devant (a) et (b)).
         Decision parent/Tomy en phase d'integration.
  §9.6.4 routage comparant le fixe ~0,3 s au gain avant de paralleliser
      -> decision de routage (orchestrateur) : hors perimetre Phase 1,
         documente pour la Phase 2.

3. POINT D'INJECTION : LA STRATEGIE DE DENSITE (directive Tomy)
---------------------------------------------------------------
L'ordonnanceur NE CONNAIT PAS l'implementation de la carte de densite :
il recoit un objet StrategieDensite et l'interroge a chaque decision de
dispatch via UNE SEULE methode de requete :

    ordonner(candidats, maintenant) -> List[Bench]

`candidats` = benches PRETS ET LANCABLES (dependances satisfaites, budget
memoire et quota lourds deja verifies par l'ordonnanceur). La strategie
retourne les MEMES benches dans l'ordre de service souhaite. L'ordonnanceur
execute l'ordre, il ne l'interprete pas.

SEMANTIQUE DES PICS NON FIGEE (tranchee par la mesure, Phase 2) :
l'interface ne dit PAS si « pic de Bragg = servir en priorite » ou
« pic = repartir en antiphase ». C'est l'implementation Phase 2
(spec FONDATIONS-MATH : sommets discrets -> orbitales gaussiennes ->
densite continue -> pics de Bragg = regions de concentration, methode du
labo quasicristal) qui tranche, en retournant simplement un ordre.
Deux variantes documentees comme EXEMPLES non prescriptifs :
  - servir les pics en priorite (concentration = urgence) ;
  - repartir en antiphase (concentration = a etaler).
L'ordonnanceur n'en connait aucune : il dispatche l'ordre recu.

Contrat StrategieDensite (exige, verifie par les tests) :
  - DETERMINISTE : memes (candidats, maintenant) -> meme ordre.
    L'aperiodicite est deterministe (Sturmian), jamais du hasard.
  - SANS FAMINE : tout candidat finit par etre premier (la strategie par
    defaut le prouve par le vieillissement ; une strategie custom le
    garantit par contrat, verifie par test). Note : l'ordonnanceur assigne
    TOUS les lancables a chaque reveil (batch) — un ordre qui est une
    permutation complete ne peut pas affamer par construction.
  - RAPIDE et NON BLOQUANTE : appelee verrou _cond tenu ; ne doit ni
    dormir ni rappeler l'ordonnanceur (soumettre/attendre/arreter).
  - TOTALE : ne leve jamais en service (repli documente : a l'exception
    d'une strategie, l'ordonnanceur reprend l'ordre d'arrivee — SAUF
    QuasicristalNonImplemente, qui est fail closed, voir §5).

  Capacites OPTIONNELLES (Phase 2, duck-typing — une strategie qui ne les
  definit pas garde exactement le comportement Phase 1) :
  - designer_worker(bench, maintenant) -> Optional[int] : designation
    DIRECTE du worker (indice 0..n_workers-1), SANS passer par le min()
    sur les charges. C'est elle qui realise l'« affectation directe par
    rotation sturmienne » : l'ordre seul ne peut pas l'exprimer, car
    l'assignation historique (min charge + tie-break rotation) realise
    toujours, a charges egales, un cycle PERIODIQUE sur les workers —
    exactement la pathologie P1 (resonance) que l'aperiodique elimine.
    None / indice invalide / exception -> repli sur _assigner_worker
    (charge-aware). Meme contrat que ordonner : deterministe, rapide,
    totale (ne leve jamais).
  - definir_n_workers(n) : l'ordonnanceur l'appelle dans __init__ pour
    transmettre W (nombre de workers) a la strategie ; l'ordonnanceur
    gagne en cas de desaccord avec le constructeur.

4. LES 5 BUGS (§4 du rapport) : NON REINTRODUITS
------------------------------------------------
  §4.1 empoisonnement RLock (pipes dedies par worker) : ce module n'utilise
        QUE des threads + threading.Condition ; AUCUN verrou partage entre
        processus, AUCUN multiprocessing ici (l'isolation processus reste
        dans pool.py, pipes dedies, intacts). Non reintroduit par
        construction.
  §4.2 signaux au spawn : aucun spawn de processus dans ce module (threads
        daemon uniquement). N/A, documente.
  §4.3 race watchdog : la garde d'identite d'execution (_Execution) est
        reprise A L'IDENTIQUE de scheduler.py — `_en_cours.get(id) is not
        exe` dans _notifier_fin et dans la boucle _executer (code reutilise,
        pas recopie). Test : test_blocage_detecte_reassigne.
  §4.4 dispatch colle (worker 0) : _Worker.charge() avec compteurs collants
        reutilise TEL QUEL depuis scheduler.py. Test : priorites/equilibrage.
  §4.5 resource_tracker apres fork : aucun fork, aucun SharedMemory manipule
        ici (neutralisation worker-side dans pool.py, intacte). N/A.

5. PHASE 2 LIVREE (fail closed conserve)
----------------------------------------
StrategieDensiteQuasicristal implemente la spec FONDATIONS-MATH :
prete() -> True, ordonner() ne leve plus. Le fail closed SURVIT pour
toute strategie non prete : demarrer() refuse toujours une strategie
dont prete() -> False (QuasicristalNonImplemente) — verifie par test
avec un stub local.
  Modes : "auto" (defaut — routage : rafale fine -> rotation sturmienne,
  benches lourds heterogenes -> priorite+vieillissement charge-aware),
  "sturmian", "densite" (V1), "priorite" (comportement historique force).
  NON implementes (spec §6 : REFUTES) : V2 antiphase, batches φ naifs,
  identification mot-des-intervalles = sturmien (H10).

6. INTEGRATION (remplacable, meme interface)
-------------------------------------------
OrdonnanceurQuasicristal expose la meme interface publique que
scheduler.Scheduler : soumettre, demarrer, attendre, arreter, resultats,
surcout_ratio, borne_attente_max_s, M_lourds_simultanes — PLUS
soumettre_lot (prescription §9.6.2). Les types Bench/BenchResult/
BenchStatus/SubmitResult sont les MEMES objets (pas de conversion).

Point d'integration (ajout compatible dans orchestrateur.py, Phase 1) :
    Orchestrateur(..., fabrique_scheduler=fabrique_quasicristal(...))
La fabrique recoit (n_workers, ram_totale_mo, capacite_file,
tentatives_max) et retourne un objet d'interface Scheduler.
Par defaut (fabrique_scheduler=None) : comportement inchange (Scheduler).

Compatibilite : Python 3.11 et 3.12 (stdlib uniquement, comme
scheduler.py). Linux/Windows : threads uniquement, pas de fork ici.
"""

from __future__ import annotations

import abc
import cmath
import itertools
import math
import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from . import scheduler as _sched_mod  # import comme paquet
except ImportError:  # pragma: no cover - import direct depuis le dossier
    import scheduler as _sched_mod


# ---------------------------------------------------------------------------
# Types partages : les MEMES objets que scheduler.py (zero conversion).
# ---------------------------------------------------------------------------

Bench = _sched_mod.Bench
BenchResult = _sched_mod.BenchResult
BenchStatus = _sched_mod.BenchStatus
SubmitResult = _sched_mod.SubmitResult

# Mecanismes eprouves reutilises TELS QUELS (anti-regression, §4 du rapport) :
#   _Execution : garde d'identite anti-race (§4.3) ;
#   _Worker    : compteurs collants anti-dispatch-colle (§4.4).
_Execution = _sched_mod._Execution
_Worker = _sched_mod._Worker

__all__ = [
    "Bench", "BenchResult", "BenchStatus", "SubmitResult",
    "ALPHA_DEFAUT",
    "QuasicristalNonImplemente",
    "StrategieDensite",
    "StrategiePrioriteVieillissement",
    "StrategieDensiteQuasicristal",
    "OrdonnanceurQuasicristal",
    "fabrique_quasicristal",
]


# ---------------------------------------------------------------------------
# 2bis. Boite a outils quasicristalline 1D — stdlib pure (Phase 2).
# ---------------------------------------------------------------------------
# Portage fidele (verifie bit-a-bit contre le prototype numpy, ecarts
# <= 3.5e-14) de quasicristal_granularite/prototype_sturmian.py :
#   - rotation sturmienne : codage a W lettres de la rotation irrationnelle
#     (Def. 2.6 de la spec) — fmod exact, pas d'accumulation d'erreur ;
#   - pipeline labo : sommets discrets -> orbitales gaussiennes (meme noyau
#     spectral que densite_electronique.py : exp(-2 pi^2 sigma^2 f^2)) ->
#     densite continue -> Bragg (|FFT|^2, log1p, fftshift) -> pics.
# La FFT est un Cooley-Tukey radix-2 iteratif sur list[complex] (n_grille
# est une puissance de 2). Aucune dependance hors stdlib : ce module reste
# importable sur 3.11 et 3.12 sans numpy.

_PHI = (1.0 + math.sqrt(5.0)) / 2.0
#: Angle par defaut de la rotation : 1/phi. Justification (spec §2.2,
#: §3.3, Th. de Hurwitz) : phi est l'irrationnel le plus mal approchable
#: -> non-resonance maximale avec tout motif de cout periodique.
ALPHA_DEFAUT = 1.0 / _PHI


def _fft(a: List[complex]) -> List[complex]:
    """FFT radix-2 iterative (list[complex] -> list[complex]).

    n doit etre une puissance de 2, sinon ValueError."""
    n = len(a)
    if n == 0 or (n & (n - 1)):
        raise ValueError("FFT : n=%d n'est pas une puissance de 2" % n)
    bits = n.bit_length() - 1
    entree = [complex(x) for x in a]
    melange = [complex(0)] * n
    for i in range(n):
        r, x = 0, i
        for _ in range(bits):
            r = (r << 1) | (x & 1)
            x >>= 1
        melange[i] = entree[r]
    m = 2
    while m <= n:
        wm = cmath.exp(-2j * math.pi / m)
        for k in range(0, n, m):
            w = 1 + 0j
            for j in range(m // 2):
                t = w * melange[k + j + m // 2]
                u = melange[k + j]
                melange[k + j] = u + t
                melange[k + j + m // 2] = u - t
                w *= wm
        m *= 2
    return melange


def _ifft(a: List[complex]) -> List[complex]:
    n = len(a)
    conj = _fft([x.conjugate() for x in a])
    return [x.conjugate() / n for x in conj]


def _fftfreq(n: int, d: float = 1.0) -> List[float]:
    val = 1.0 / (n * d)
    n2 = (n - 1) // 2 + 1
    return [(i if i < n2 else i - n) * val for i in range(n)]


def _fftshift(a: list) -> list:
    p = len(a) // 2
    return a[p:] + a[:p]


def _histogramme_1d(xs: List[float], n: int, xmin: float, xmax: float
                    ) -> Tuple[List[float], float]:
    comptes = [0.0] * n
    dx = (xmax - xmin) / n
    for x in xs:
        i = int((x - xmin) / dx)
        if 0 <= i < n:
            comptes[i] += 1.0
    return comptes, dx


def _interp_lineaire(x: float, xp: List[float], fp: List[float]) -> float:
    """Interpolation lineaire sur grille uniforme croissante (cf. np.interp)."""
    n = len(xp)
    dx = xp[1] - xp[0]
    t = (x - xp[0]) / dx
    i = int(math.floor(t))
    if i < 0:
        return fp[0]
    if i >= n - 1:
        return fp[-1]
    f = t - i
    return fp[i] * (1.0 - f) + fp[i + 1] * f


def _flou_gaussien_spectral_1d(comptes: List[float], sigma_px: float
                               ) -> List[float]:
    """Flou gaussien periodique par FFT — MEME noyau que le labo
    (SimulationCoupeElectronique._flou_gaussien_spectral), en 1D :
    exp(-2 pi^2 sigma_px^2 f^2)."""
    n = len(comptes)
    freq = _fftfreq(n, d=1.0)
    noyau = [math.exp(-2.0 * (math.pi ** 2) * (sigma_px ** 2) * (f ** 2))
             for f in freq]
    spectre = _fft([complex(c) for c in comptes])
    floute = [s * k for s, k in zip(spectre, noyau)]
    return [max(0.0, z.real) for z in _ifft(floute)]


def _densite_orbitales_1d(positions: List[float], n_grille: int,
                         etendue: float, sigma: float
                         ) -> Tuple[List[float], List[float]]:
    """Synthese de la densite continue rho(x) : chaque sommet porte une
    orbitale gaussienne d'ecart-type sigma (cf. calculer_densite_electronique
    du labo). Retourne (centres_x, densite)."""
    comptes, dx = _histogramme_1d(positions, n_grille, -etendue, etendue)
    dens = _flou_gaussien_spectral_1d(comptes, sigma / dx)
    centres = [-etendue + dx / 2.0 + i * dx for i in range(n_grille)]
    return centres, dens


def _diffraction_bragg(densite: List[float]) -> List[float]:
    """Spectre de Bragg : |FFT(rho)|^2 en echelle log1p, centre (fftshift)."""
    spectre = _fftshift(_fft([complex(d) for d in densite]))
    return [math.log1p(abs(s) ** 2) for s in spectre]


def _pics_bragg(intensite: List[float], n_grille: int, etendue: float,
                n_pics: int = 6, seuil_rel: float = 0.35) -> List[dict]:
    """Pics locaux d'intensite > seuil_rel * max (hors DC), paires ±k
    dedupliquees. Frequences physiques f = k / (2*etendue)."""
    n = len(intensite)
    centre = n // 2
    imax = max(intensite)
    pics = []
    for i in range(1, n - 1):
        if i == centre:
            continue
        v = intensite[i]
        if (v > seuil_rel * imax and v >= intensite[i - 1]
                and v >= intensite[i + 1]):
            k = abs(i - centre)
            pics.append({"indice": i, "frequence": k / (2.0 * etendue),
                         "intensite": v})
    pics.sort(key=lambda p: p["intensite"], reverse=True)
    vus: Dict[float, dict] = {}
    for p in pics:
        cle = round(p["frequence"], 6)
        if cle not in vus or p["intensite"] > vus[cle]["intensite"]:
            vus[cle] = p
    dedup = sorted(vus.values(), key=lambda p: p["intensite"], reverse=True)
    return dedup[:n_pics]


def _slot_rotation(seq: int, W: int, alpha: float, rho: float) -> int:
    """Position de rotation pour l'indice seq : w(seq) = j ssi
    {seq*alpha + rho} in [j/W, (j+1)/W) (Def. 2.6 de la spec).
    fmod exact, pas d'accumulation d'erreur flottante (cf. prototype)."""
    x = math.fmod(seq * alpha + rho, 1.0)
    if x < 0.0:
        x += 1.0
    return int(x * W) % W


def _rotation_assign(n_tasks: int, W: int, alpha: float,
                     rho: float = 0.0) -> List[int]:
    """Codage a W lettres de la rotation irrationnelle (prototype §1).
    Aperiodique si alpha irrationnel ; frequences -> 1/W (Weyl, Th. 2.7)."""
    if W < 1:
        raise ValueError("W >= 1 requis")
    if W == 1:
        return [0] * n_tasks
    return [_slot_rotation(k, W, alpha, rho) for k in range(n_tasks)]


def _chaine_cut_project_1d(R: int = 60, alpha: float = None,
                           largeur_fenetre: float = None,
                           L: float = 40.0) -> List[float]:
    """Ensemble de Meyer 1D par cut-and-project Z^2 -> R (chaine de Fibonacci,
    cf. prototype §4). Utilitaire de validation du pipeline (test S4) :
    111 sommets pour R=60, intervalles exactement {0.525731112, 0.850650808}
    de ratio phi a 1e-9 pres."""
    if alpha is None:
        alpha = ALPHA_DEFAUT
    nrm = math.sqrt(1.0 + alpha * alpha)
    if largeur_fenetre is None:
        largeur_fenetre = (1.0 + alpha) / nrm
    ux, uy = 1.0 / nrm, alpha / nrm
    vx, vy = -alpha / nrm, 1.0 / nrm
    xs = []
    for i in range(-R, R + 1):
        for j in range(-R, R + 1):
            xp = i * ux + j * uy
            xo = i * vx + j * vy
            if abs(xo) <= largeur_fenetre / 2.0 and abs(xp) <= L:
                xs.append(xp)
    return sorted(xs)


# ---------------------------------------------------------------------------
# 3. Strategie de densite : le point d'injection.
# ---------------------------------------------------------------------------

class QuasicristalNonImplemente(NotImplementedError):
    """Levee quand on tente d'utiliser un stub Phase 1 dont l'algorithme
    (spec FONDATIONS-MATH) n'est pas encore implemente.

    Fail closed : jamais de repli silencieux vers un ordre arbitraire —
    l'ordonnanceur refuse de demarrer (voir OrdonnanceurQuasicristal)."""


class StrategieDensite(abc.ABC):
    """Point d'injection de la « carte de densite » (directive Tomy).

    L'ordonnanceur interroge la strategie SANS connaitre son implementation.
    Methode de requete unique : ordonner().

    La strategie recoit les benches PRETS ET LANCABLES (dependances
    satisfaites ; budget memoire global et quota de benches lourds deja
    verifies par l'ordonnanceur) et retourne l'ordre de service. Elle ne
    decide PAS de la faisabilite : l'ordonnanceur filtre avant ET re-verifie
    pendant l'assignation en batch (le budget est consomme au fil du batch).

    SEMANTIQUE DES PICS NON FIGEE : l'interface ne prescrit ni « pic =
    priorite » ni « pic = antiphase ». L'implementation Phase 2
    (sommets discrets -> orbitales gaussiennes -> densite continue ->
    pics de Bragg = regions de concentration, methode du labo
    quasicristal) tranche par la mesure et retourne simplement un ordre.
    """

    #: Nom documentaire de la strategie (traces, rapports).
    NOM = "abstraite"

    @abc.abstractmethod
    def ordonner(self, candidats: List[Bench], maintenant: float) -> List[Bench]:
        """Retourne les candidats dans l'ordre de dispatch souhaite.

        candidats : liste (copie) des benches prets et lancables, dans
            l'ordre d'arrivee. Ne PAS les muter (ni la liste, ni les
            objets) : l'ordonnanceur ne fait que lire l'ordre retourne.
        maintenant : temps monotone (time.monotonic()) au moment de la
            decision — la strategie peut s'en servir comme coordonnee
            temporelle deterministe.
        Retour : une PERMUTATION des candidats (memes bench_id, sans
            doublon ni omission). Tout autre retour -> repli sur l'ordre
            d'arrivee (documente, jamais silencieux : comptabilise).
        Contrat : deterministe, sans famine, rapide, non bloquante
            (appelee verrou _cond tenu — ne pas rappeler l'ordonnanceur).
        """
        raise NotImplementedError

    def prete(self) -> bool:
        """False tant que l'algorithme n'est pas implemente (stub Phase 1).

        L'ordonnanceur refuse de demarrer avec une strategie non prete
        (fail closed, QuasicristalNonImplemente)."""
        return True


class StrategiePrioriteVieillissement(StrategieDensite):
    """Strategie par defaut : reproduit EXACTEMENT l'ordre du Scheduler
    historique (priorite + vieillissement anti-famine).

        p_eff = priorite + vieillissement * (maintenant - soumis_a)

    Egalite -> _seq (ordre d'arrivee). Deterministe, sans famine prouvee
    (test F2 du Scheduler : 100/100 termines, attente <= borne).
    C'est le comportement preserve : l'ordonnanceur quasicristallin avec
    cette strategie se comporte comme scheduler.Scheduler, au dispatch
    a reveil et au batch pres."""

    NOM = "priorite+vieillissement"

    def __init__(self, vieillissement: float = 0.5) -> None:
        if vieillissement < 0:
            raise ValueError("vieillissement >= 0 requis")
        self.vieillissement = float(vieillissement)

    def ordonner(self, candidats: List[Bench], maintenant: float) -> List[Bench]:
        def _cle(b: Bench):
            return (-(b.priorite
                      + self.vieillissement * (maintenant - b._soumis_a)),
                    b._seq)
        return sorted(candidats, key=_cle)


class StrategieDensiteQuasicristal(StrategieDensite):
    """Ordonnanceur aperiodique (Phase 2 — spec FONDATIONS-MATH
    quasicristal_granularite/MATH_QUASICRISTAL_GRANULARITE.md).

    Deux mecanismes implementes, un routage, trois exclusions :

    1. ROTATION STURMIENNE (recommandee, prioritaire — spec §2, §4).
       Affectation directe : le bench de rang de soumission `seq` va au
       worker w(seq) = j ssi {seq*alpha + rho} in [j/W, (j+1)/W), avec
       alpha = 1/phi par defaut (Hurwitz : phi = le plus mal approchable
       -> non-resonance maximale). Remplace le min() sur les charges :
       aucun min() n'est evalue -> 0 egalite PAR CONSTRUCTION (immunite
       structurelle contre le bug "dispatch colle worker 0", pas
       heuristique) ; ne capture aucun motif de cout periodique q
       (Th. 4.4) ; ~10x mieux que l'aleatoire sur tous les q (S1).
       Proprietes : discrepance bornee par 1 (Th. 2.5), frequences -> 1/W
       (Weyl, Th. 2.7), aperiodique (Th. 2.4).
    2. DENSITE V1 (spec §5). Taches = sommets discrets (coordonnee :
       temps de soumission _soumis_a — les rafales temporelles sont les
       regions de concentration) -> noyau gaussien (meme noyau spectral
       que le labo : exp(-2 pi^2 sigma^2 f^2)) -> densite continue ->
       |FFT|^2 / log1p / fftshift -> pics de Bragg. Les taches sont
       servies par densite decroissante, chacune vers le worker de charge
       minimale (l'ordonnanceur realise l'etape LPT). Mesure : V1 ~ LPT
       (desequilibre 0,041 vs 0,019 ref. Graham, W=4) >> round-robin.
    3. ROUTAGE (mode "auto", defaut). Rafale de taches fines homogenes ->
       rotation sturmienne ; benches lourds heterogenes a couts connus
       disperses -> comportement charge-aware existant (priorite +
       vieillissement, spec §7 : le min() reste superieur dans ce regime).

    NON implementes (spec §6 : REFUTES) : V2 antiphase (0,132, ne bat pas
    V1) ; batches φ naifs (ne minimisent pas l'overhead) ; identification
    "mot des intervalles = sturmien" (H10 — rotation et ensemble modele
    gardes SEPARES).

    Determinisme : la rotation est une fonction PURE du rang de soumission
    (aucun etat, aucune lecture de charge) ; la densite ne depend que de
    l'historique des soumissions (mise a jour idempotente). Memes
    (candidats, maintenant) -> meme ordre, meme entre deux instances.

    Sans famine : l'ordonnanceur assigne TOUS les lancables a chaque reveil
    (batch) ; ordonner() retourne toujours une permutation complete.

    Rapide : rotation O(N log N) ; densite O(N log N) apres cache (FFT
    1024 ~ quelques ms, recalculee seulement quand l'historique change).
    """

    NOM = "quasicristal"

    MODES = ("auto", "sturmian", "densite", "priorite")

    def __init__(
        self,
        mode: str = "auto",
        alpha: Optional[float] = None,
        rho: float = 0.0,
        n_workers: Optional[int] = None,
        vieillissement: float = 0.5,
        seuil_fin_s: float = 10.0,
        seuil_dispersion_cv: float = 1.0,
        n_grille: int = 1024,
        fenetre_densite: int = 2048,
    ) -> None:
        if mode not in self.MODES:
            raise ValueError("mode : %s requis (recu %r)"
                             % ("|".join(self.MODES), mode))
        if n_workers is not None and n_workers < 1:
            raise ValueError("n_workers >= 1 requis")
        if n_grille < 64 or (n_grille & (n_grille - 1)):
            raise ValueError("n_grille : puissance de 2 >= 64 requise")
        if fenetre_densite < 1:
            raise ValueError("fenetre_densite >= 1 requise")
        self.mode = mode
        self.alpha = ALPHA_DEFAUT if alpha is None else float(alpha)
        self.rho = float(rho)
        #: W — ecrit par definir_n_workers() (l'ordonnanceur gagne).
        self.n_workers = n_workers
        self.vieillissement = float(vieillissement)
        #: Au-dela : un bench est "lourd" -> routage priorite.
        self.seuil_fin_s = float(seuil_fin_s)
        #: CV(duree_est_s) au-dela : couts "tres disperses" -> priorite.
        self.seuil_dispersion_cv = float(seuil_dispersion_cv)
        self._n_grille = int(n_grille)
        self._fenetre = int(fenetre_densite)
        # -- etat densite : _seq -> _soumis_a (mise a jour idempotente) --
        self._temps_vus: Dict[int, float] = {}
        self._version_densite = 0
        self._cache_densite: Optional[Tuple[int, List[float], List[float]]] = None
        #: Mode effectif du dernier appel a ordonner() (routage "auto"
        #: resolu) ; None avant le premier appel -> designer_worker = None.
        self._mode_effectif: Optional[str] = None

    # -- capacite optionnelle : l'ordonnanceur transmet W ------------------
    def definir_n_workers(self, n: int) -> None:
        if n < 1:
            raise ValueError("n_workers >= 1 requis")
        self.n_workers = int(n)

    def prete(self) -> bool:
        return True

    # -- routage -----------------------------------------------------------
    def _est_rafale_fine(self, candidats: List[Bench]) -> bool:
        """Rafale de taches fines homogenes ? (routage "auto").

        False des que : un cout estime depasse seuil_fin_s ("lourd"), ou
        les couts sont tres disperses (CV > seuil — le min() charge-aware
        reste superieur dans ce regime, spec §7). Deterministe."""
        ds = [b.duree_est_s for b in candidats]
        if max(ds) > self.seuil_fin_s:
            return False
        if len(ds) < 2:
            return True
        m = sum(ds) / len(ds)
        if m <= 0:
            return True
        var = sum((d - m) ** 2 for d in ds) / len(ds)
        return math.sqrt(var) / m <= self.seuil_dispersion_cv

    # -- rotation sturmienne ------------------------------------------------
    def _slot(self, seq: int) -> int:
        W = self.n_workers or 1
        return _slot_rotation(seq, W, self.alpha, self.rho)

    def designer_worker(self, bench: Bench, maintenant: float
                        ) -> Optional[int]:
        """Capacite optionnelle (Phase 2) : designation DIRECTE du worker.

        En mode sturmien effectif : l'indice du worker par rotation
        (fonction pure du rang de soumission — aucun min() sur des charges
        n'est evalue, 0 egalite par construction). Sinon None -> repli sur
        l'assignation charge-aware de l'ordonnanceur. Ne leve jamais."""
        try:
            if self._mode_effectif == "sturmian" and self.n_workers:
                return self._slot(bench._seq)
        except Exception:
            pass
        return None

    # -- densite V1 ----------------------------------------------------------
    def _noter_temps(self, candidats: List[Bench]) -> None:
        """Historique des soumissions (idempotent : rejouer les memes
        candidats ne change rien -> determinisme)."""
        for b in candidats:
            if b._seq not in self._temps_vus:
                self._temps_vus[b._seq] = float(b._soumis_a)
                self._version_densite += 1
        if len(self._temps_vus) > 2 * self._fenetre:
            garde = sorted(self._temps_vus)[-self._fenetre:]
            self._temps_vus = {s: self._temps_vus[s] for s in garde}
            self._version_densite += 1

    def _fenetre_courante(self) -> Tuple[List[int], float, float]:
        seqs = sorted(self._temps_vus)
        fen = seqs[-self._fenetre:]
        temps = [self._temps_vus[s] for s in fen]
        return fen, min(temps), max(temps)

    def _densite_courante(self) -> Tuple[List[float], List[float]]:
        """(centres, densite) sur la fenetre d'historique ; recalculee
        seulement quand l'historique change (cache par version)."""
        if (self._cache_densite is not None
                and self._cache_densite[0] == self._version_densite):
            return self._cache_densite[1], self._cache_densite[2]
        fen, tmin, tmax = self._fenetre_courante()
        temps = [self._temps_vus[s] for s in fen]
        if tmax > tmin:
            pos = [-1.0 + 2.0 * (t - tmin) / (tmax - tmin) for t in temps]
        else:
            pos = [0.0] * len(temps)
        G = self._n_grille
        comptes, dx = _histogramme_1d(pos, G, -1.0, 1.0)
        # sigma ~ 1/4 de l'espacement moyen (etalonne sur le labo : 0,2x).
        esp = 2.0 / max(1, len(pos) - 1) if len(pos) > 1 else 2.0
        dens = _flou_gaussien_spectral_1d(comptes, (0.25 * esp) / dx)
        centres = [-1.0 + dx / 2.0 + i * dx for i in range(G)]
        self._cache_densite = (self._version_densite, centres, dens)
        return centres, dens

    def _ordre_densite(self, candidats: List[Bench]) -> List[Bench]:
        """V1 : taches par densite decroissante (l'ordonnanceur assigne
        chacune au worker de charge minimale = etape LPT du prototype)."""
        centres, dens = self._densite_courante()
        _, tmin, tmax = self._fenetre_courante()

        def _pos(b: Bench) -> float:
            t = float(b._soumis_a)
            if tmax > tmin:
                return -1.0 + 2.0 * (t - tmin) / (tmax - tmin)
            return 0.0

        cles = [(-_interp_lineaire(_pos(b), centres, dens), b._seq, b)
                for b in candidats]
        cles.sort(key=lambda t: (t[0], t[1]))
        return [b for _, _, b in cles]

    def pics_bragg_courants(self, n_pics: int = 6,
                            seuil_rel: float = 0.35) -> List[Tuple[float, float]]:
        """Pics de Bragg du flux de soumissions : (frequence, intensite),
        par intensite decroissante. Introspection / diagnostic (les pics
        sont les periodicites du flux — cf. spec §5)."""
        _, dens = self._densite_courante()
        intensite = _diffraction_bragg(dens)
        pics = _pics_bragg(intensite, self._n_grille, 1.0, n_pics, seuil_rel)
        return [(p["frequence"], p["intensite"]) for p in pics]

    # -- requete principale ---------------------------------------------------
    def ordonner(self, candidats: List[Bench], maintenant: float
                 ) -> List[Bench]:
        candidats = list(candidats)
        if not candidats:
            self._mode_effectif = None
            return []
        self._noter_temps(candidats)
        mode = self.mode
        if mode == "auto":
            mode = ("sturmian" if self._est_rafale_fine(candidats)
                    else "priorite")
        self._mode_effectif = mode
        if mode == "sturmian":
            # L'ordre de service est l'ordre d'arrivee ; l'affectation
            # aperiodique est portee par designer_worker().
            return sorted(candidats, key=lambda b: b._seq)
        if mode == "densite":
            return self._ordre_densite(candidats)
        # "priorite" : comportement historique exact (spec §7 — superieur
        # pour les benches lourds heterogenes a couts disperses).
        def _cle(b: Bench):
            return (-(b.priorite
                      + self.vieillissement * (maintenant - b._soumis_a)),
                    b._seq)
        return sorted(candidats, key=_cle)


# ---------------------------------------------------------------------------
# 6. Ordonnanceur quasicristallin (dispatch a reveil + batch).
# ---------------------------------------------------------------------------

class OrdonnanceurQuasicristal:
    """Ordonnanceur aperiodique a strategie de densite injectee.

    MEME INTERFACE que scheduler.Scheduler (remplacable), PLUS
    soumettre_lot() (batch de soumission, §9.6.2) :

        soumettre(bench) -> SubmitResult
        soumettre_lot(benches) -> List[SubmitResult]
        demarrer() / attendre(timeout_s) -> bool / arreter()
        resultats() -> Dict[str, BenchResult]
        surcout_ratio() -> float
        borne_attente_max_s() -> float
        M_lourds_simultanes() -> int

    Differences assumees vs scheduler.Scheduler (documentees, mesurees) :
      - dispatch A REVEIL : pas de scrutation periodique. Le thread
        dispatch dort sur la Condition et se reveille sur evenement
        (soumission, fin de bench, arret) ou a la prochaine echeance de
        detection de blocage. Le parametre intervalle_surveillance_s est
        CONSERVE pour compatibilite de signature mais IGNORE par le
        dispatch (vestige : encore utilise comme quantum de repli dans les
        boucles worker reutilisees).
      - assignation EN BATCH : a chaque reveil, tous les candidats
        lancables sont assignes dans l'ordre de la strategie (pas un
        par cycle).
      - l'ordre de dispatch vient de la strategie injectee (defaut :
        priorite + vieillissement = comportement historique).

    Bugs §4 : voir docstring du module (reutilisation _Execution/_Worker
    de scheduler.py ; aucun multiprocessing ici).
    """

    def __init__(
        self,
        n_workers: int,
        ram_totale_mo: float,
        capacite_file: int = 200,
        seuil_lourd_mo: Optional[float] = None,
        facteur_blocage: float = 3.0,
        timeout_hb_s: float = 2.0,
        vieillissement: float = 0.5,
        intervalle_surveillance_s: float = 0.05,
        tentatives_max: int = 3,
        strategie: Optional[StrategieDensite] = None,
    ) -> None:
        if n_workers < 1:
            raise ValueError("n_workers >= 1 requis")
        if ram_totale_mo <= 0:
            raise ValueError("ram_totale_mo > 0 requis")
        self.n_workers = n_workers
        self.ram_totale_mo = float(ram_totale_mo)
        self.capacite_file = int(capacite_file)
        self.seuil_lourd_mo = (float(seuil_lourd_mo) if seuil_lourd_mo
                               else self.ram_totale_mo / 8.0)
        self.M_lourds = max(1, int(self.ram_totale_mo // self.seuil_lourd_mo))
        self.facteur_blocage = float(facteur_blocage)
        self.timeout_hb_s = float(timeout_hb_s)
        self.vieillissement = float(vieillissement)
        # Vestige de signature : le dispatch a reveil l'ignore ; les boucles
        # worker reutilisees (_Worker) l'utilisent comme quantum de repli.
        self.intervalle_surveillance_s = float(intervalle_surveillance_s)
        self.tentatives_max = int(tentatives_max)

        if strategie is None:
            strategie = StrategiePrioriteVieillissement(
                vieillissement=self.vieillissement)
        if not isinstance(strategie, StrategieDensite):
            raise TypeError("strategie : StrategieDensite requise, pas %s"
                            % type(strategie).__name__)
        self._strategie = strategie
        # Phase 2 : transmet W aux strategies qui l'acceptent (capacite
        # optionnelle definir_n_workers — l'ordonnanceur gagne en cas de
        # desaccord). Les strategies Phase 1 l'ignorent (duck-typing).
        lier = getattr(self._strategie, "definir_n_workers", None)
        if callable(lier):
            lier(self.n_workers)

        # -- etat partage (meme structure que scheduler.Scheduler) --
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._seq = itertools.count()

        self._benchs: Dict[str, Bench] = {}
        self._prets: List[Bench] = []
        self._bloques_dep: Dict[str, Bench] = {}
        self._termines: set = set()
        self._resultats: Dict[str, BenchResult] = {}
        self._en_cours: Dict[str, _Execution] = {}
        self._mem_utilisee_mo = 0.0
        self._lourds_en_cours = 0

        self._workers: List[_Worker] = []
        self._arrete = False
        self._rr_index = 0
        self._fil_dispatch: Optional[threading.Thread] = None
        self._t0 = 0.0
        self._surcout_s = 0.0
        self._n_dispatch = 0
        self._n_replis_strategie = 0   # replis sur ordre d'arrivee (jamais silencieux)

        self._prio_min_vue: Optional[int] = None
        self._prio_max_vue: Optional[int] = None

    # -- soumission ---------------------------------------------------------
    def _soumettre_interne(self, bench: Bench) -> SubmitResult:
        """Corps de soumettre() SANS notification (pour le batch)."""
        if bench.bench_id in self._benchs:
            return SubmitResult.ACCEPTE  # deja connu : idempotent
        if bench.mem_mo > self.ram_totale_mo:
            return SubmitResult.MEMOIRE_IMPOSSIBLE
        if any(d not in self._benchs for d in bench.dependances):
            return SubmitResult.DEPENDANCE_INCONNUE
        if len(self._benchs) >= self.capacite_file:
            return SubmitResult.FILE_PLEINE
        bench._soumis_a = time.monotonic()
        bench._seq = next(self._seq)
        self._benchs[bench.bench_id] = bench
        self._resultats[bench.bench_id] = BenchResult(
            bench_id=bench.bench_id, status=BenchStatus.EN_ATTENTE)
        p = bench.priorite
        self._prio_min_vue = (p if self._prio_min_vue is None
                             else min(self._prio_min_vue, p))
        self._prio_max_vue = (p if self._prio_max_vue is None
                             else max(self._prio_max_vue, p))
        if bench.dependances <= self._termines:
            self._prets.append(bench)
            self._resultats[bench.bench_id].status = BenchStatus.PRET
        else:
            self._bloques_dep[bench.bench_id] = bench
        return SubmitResult.ACCEPTE

    def soumettre(self, bench: Bench) -> SubmitResult:
        """Soumet un bench (meme semantique que scheduler.Scheduler)."""
        with self._cond:
            verdict = self._soumettre_interne(bench)
            self._cond.notify_all()  # reveil du dispatch (pas de scrutation)
            return verdict

    def soumettre_lot(self, benches: List[Bench]) -> List[SubmitResult]:
        """Batch de soumission (prescription §9.6.2).

        UN SEUL passage sous verrou et UNE SEULE notification au dispatch
        pour tout le lot — pas K allers-retours verrou/notify. C'est ce
        batch qui fait tomber le cout fixe ~0,30 s par map() mesure au §9.5.
        Semantique par bench identique a soumettre() (idempotence,
        MEMOIRE_IMPOSSIBLE, DEPENDANCE_INCONNUE, FILE_PLEINE).
        """
        benches = list(benches)
        with self._cond:
            verdicts = [self._soumettre_interne(b) for b in benches]
            if benches:
                self._cond.notify_all()
            return verdicts

    # -- cycle de vie -------------------------------------------------------
    def demarrer(self) -> None:
        """Demarre workers + dispatch. Fail closed : refuse une strategie
        non prete (stub Phase 1)."""
        if not self._strategie.prete():
            raise QuasicristalNonImplemente(
                "strategie %r non prete : algorithme en attente de la spec "
                "FONDATIONS-MATH (Phase 2)" % self._strategie.NOM)
        with self._cond:
            if self._fil_dispatch is not None:
                return
            self._t0 = time.monotonic()
            self._arrete = False
            for i in range(self.n_workers):
                w = _Worker(self, i)
                self._workers.append(w)
                w.demarrer()
            self._fil_dispatch = threading.Thread(
                target=self._boucle_dispatch, name="dispatch-qc", daemon=True)
            self._fil_dispatch.start()

    def attendre(self, timeout_s: Optional[float] = None) -> bool:
        """Bloque jusqu'a ce que tous les benches soumis soient termines.
        Retourne True si tout est termine (False = timeout)."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        with self._cond:
            while True:
                if len(self._termines) == len(self._benchs):
                    return True
                if deadline is not None:
                    reste = deadline - time.monotonic()
                    if reste <= 0:
                        return False
                    self._cond.wait(timeout=reste)
                else:
                    self._cond.wait()

    def arreter(self) -> None:
        with self._cond:
            self._arrete = True
            self._cond.notify_all()
        for w in self._workers:
            w.rejoindre()
        if self._fil_dispatch is not None:
            self._fil_dispatch.join(timeout=5.0)
            self._fil_dispatch = None
        self._workers = []

    # -- introspection ------------------------------------------------------
    def resultats(self) -> Dict[str, BenchResult]:
        with self._lock:
            return dict(self._resultats)

    def surcout_ratio(self) -> float:
        """Part du temps total consommee par l'ordonnanceur lui-meme
        (corps du dispatch, hors attentes — mesuree, pas estimee)."""
        total = time.monotonic() - self._t0 if self._t0 else 0.0
        if total <= 0:
            return 0.0
        return self._surcout_s / total

    def borne_attente_max_s(self) -> float:
        """Borne d'attente maximale (anti-famine).

        Pour la strategie par defaut (priorite + vieillissement), la borne
        est GARANTIE comme dans scheduler.Scheduler, SANS le terme de
        scrutation (dispatch a reveil : latence de reveil ~0, pas 0,1 s) :
            W_max = DeltaP/alpha + t_est_max * facteur_blocage
        Pour une strategie custom : INDICATIVE — le contrat
        StrategieDensite exige l'absence de famine (verifiee par test),
        mais l'ordonnanceur ne peut pas la prouver depuis ses seuls
        parametres.
        """
        if self._prio_min_vue is None or self._prio_max_vue is None:
            return 0.0
        delta = max(0, self._prio_max_vue - self._prio_min_vue)
        rattrapage = (delta / self.vieillissement
                      if self.vieillissement > 0 else float("inf"))
        pire_blocage = max((b.duree_est_s for b in self._benchs.values()),
                           default=0.0)
        pire_blocage *= self.facteur_blocage
        return rattrapage + pire_blocage

    def M_lourds_simultanes(self) -> int:
        return self.M_lourds

    @property
    def strategie(self) -> StrategieDensite:
        """La strategie de densite injectee (lecture seule)."""
        return self._strategie

    def replis_strategie(self) -> int:
        """Nombre de replis sur l'ordre d'arrivee (strategie en echec ou
        ordre invalide). Jamais silencieux : expose pour les rapports."""
        with self._lock:
            return self._n_replis_strategie

    # -- coeur : dispatch A REVEIL ------------------------------------------
    def _est_lourd(self, bench: Bench) -> bool:
        return bench.mem_mo >= self.seuil_lourd_mo

    def _lancable(self, bench: Bench) -> bool:
        """Contraintes de lancement (ordonnanceur) : budget memoire global
        + quota de benches lourds. Un bench non lancable attend SANS bloquer
        les autres (la strategie n'ordonne que les lancables)."""
        if self._mem_utilisee_mo + bench.mem_mo > self.ram_totale_mo:
            return False
        if self._est_lourd(bench) and self._lourds_en_cours >= self.M_lourds:
            return False
        return True

    def _ordonner_sans_panique(self, lancables: List[Bench],
                               maintenant: float) -> List[Bench]:
        """Interroge la strategie ; repli documente sur ordre d'arrivee.

        QuasicristalNonImplemente N'EST PAS capturee ici : elle est
        fail closed des demarrer(). Toute autre exception, ou un ordre
        qui n'est pas une permutation des candidats, -> repli sur
        l'ordre d'arrivee + compteur (jamais silencieux).
        """
        try:
            ordre = self._strategie.ordonner(list(lancables), maintenant)
        except QuasicristalNonImplemente:
            raise
        except Exception:
            ordre = None
        if not self._est_permutation(ordre, lancables):
            with self._lock:
                self._n_replis_strategie += 1
            return list(lancables)
        return list(ordre)

    @staticmethod
    def _est_permutation(ordre, lancables: List[Bench]) -> bool:
        if not isinstance(ordre, list) or len(ordre) != len(lancables):
            return False
        ids_attendus = {b.bench_id for b in lancables}
        ids_recus = [b.bench_id for b in ordre
                     if isinstance(b, Bench)]
        return len(ids_recus) == len(ids_attendus) and set(ids_recus) == ids_attendus

    def _prochaine_echeance(self) -> Optional[float]:
        """Delai (s) avant le prochain controle de blocage utile, ou None.

        Pour une execution, _Execution.est_bloquee() ne peut devenir vraie
        avant max(debut + facteur*duree_est, dernier_hb + timeout_hb)
        (conjonction des deux conditions). On se reveille a la plus proche
        echeance (+1 ms : inegalites strictes, anti-boucle). SANS execution
        en cours : None — le dispatch DORT jusqu'au prochain evenement
        (soumission, fin de bench, arret). C'est le dispatch A REVEIL :
        aucun quantum de scrutation sur le chemin critique.
        """
        if not self._en_cours:
            return None
        echeances = []
        for exe in self._en_cours.values():
            with exe.hb_lock:
                dernier_hb = exe.dernier_hb
            echeances.append(max(
                exe.debut + self.facteur_blocage * exe.bench.duree_est_s,
                dernier_hb + self.timeout_hb_s,
            ) + 1e-3)
        return max(0.0, min(echeances) - time.monotonic())

    def _boucle_dispatch(self) -> None:
        """Dispatch A REVEIL (prescription §9.6.1).

        Chaque reveil : (1) liberer les dependances satisfaites,
        (2) interroger la strategie sur les candidats lancables,
        (3) assigner EN BATCH dans l'ordre recu, (4) detecter les
        blocages, (5) se rendormir jusqu'au prochain evenement ou a la
        prochaine echeance de detection. Aucune scrutation periodique.

        Le surcout mesure EXCLUT l'attente sur la Condition (dormir n'est
        pas travailler) : t_debut est pris verrou tenu, apres le test
        d'arret, et le cumul a lieu avant le wait.
        """
        while True:
            with self._cond:
                if self._arrete:
                    return
                t_debut = time.perf_counter()
                maintenant = time.monotonic()
                progres = False  # un changement d'etat -> re-scan immediat
                # 1) liberer les benches dont les dependances sont satisfaites
                #    (un ECHOUE est clos -> libere ses dependants, comme
                #    dans scheduler.Scheduler : pas d'attente fantome).
                for bid in list(self._bloques_dep):
                    b = self._bloques_dep[bid]
                    if b.dependances <= self._termines:
                        del self._bloques_dep[bid]
                        self._prets.append(b)
                        self._resultats[bid].status = BenchStatus.PRET
                        progres = True
                # 2) candidats lancables -> ordre aperiódique via la strategie
                lancables = [b for b in self._prets if self._lancable(b)]
                ordre = self._ordonner_sans_panique(lancables, maintenant)
                # 3) assignation EN BATCH, dans l'ordre de la strategie.
                #    Le budget memoire / quota lourds est re-verifie et
                #    consomme au fil du batch (un bench non lancable attend
                #    sans bloquer les suivants).
                for b in ordre:
                    if not self._lancable(b):
                        continue
                    self._prets.remove(b)
                    self._mem_utilisee_mo += b.mem_mo
                    if self._est_lourd(b):
                        self._lourds_en_cours += 1
                    b._tentatives += 1
                    res = self._resultats[b.bench_id]
                    if res.tentatives == 0:
                        res.attente_s = maintenant - b._soumis_a
                    res.tentatives = b._tentatives
                    res.status = BenchStatus.EN_COURS
                    exe = _Execution(b, maintenant)
                    self._en_cours[b.bench_id] = exe
                    self._assigner_selon_strategie(b, exe, maintenant)
                    self._n_dispatch += 1
                    progres = True
                # 4) detection des workers bloques (heartbeat perime).
                for bid, exe in list(self._en_cours.items()):
                    if exe.est_bloquee(maintenant, self.facteur_blocage,
                                       self.timeout_hb_s):
                        self._gerer_blocage(bid, exe)
                        progres = True
                t_fin = time.perf_counter()
                self._surcout_s += (t_fin - t_debut)
                if progres:
                    # Un changement d'etat a rendu du travail disponible
                    # (assignation, dependance liberee, blocage refile) :
                    # re-scan IMMEDIAT, pas d'attente. Sans cela, un bench
                    # refile apres blocage dormirait jusqu'au prochain
                    # evenement — l'equivalent du cycle de scrutation qu'on
                    # a supprime. Pas de livelock : chaque `progres`
                    # consomme une transition d'etat finie (prets->en_cours,
                    # bloques_dep->prets, en_cours->prets/termines).
                    continue
                # 5) se rendormir : prochain evenement ou prochaine echeance
                #    de detection. AUCUNE scrutation periodique.
                #    (Le wait est HORS de la mesure de surcout : dormir
                #    n'est pas travailler.)
                delai = self._prochaine_echeance()
                self._cond.wait(timeout=delai)  # None = jusqu'a notify

    def _assigner_worker(self, bench: Bench, exe: _Execution) -> None:
        # Round-robin pondere (reprise de scheduler.Scheduler) : le worker
        # de charge() minimale gagne ; les compteurs collants de _Worker
        # evitent le dispatch colle sur le worker 0 (bug §4.4).
        def cle(w: _Worker) -> tuple:
            return (w.charge(), (w.worker_id - self._rr_index) % self.n_workers)
        cible = min(self._workers, key=cle)
        self._rr_index = (cible.worker_id + 1) % self.n_workers
        cible.deposer(bench, exe)

    def _assigner_selon_strategie(self, bench: Bench, exe: _Execution,
                                 maintenant: float) -> None:
        """Affectation dirigee par la strategie (Phase 2).

        Si la strategie expose designer_worker(bench, maintenant)
        (capacite optionnelle), l'indice retourne designe le worker
        DIRECTEMENT — c'est le mode "rotation sturmienne", qui REMPLACE le
        min() sur les charges (0 egalite par construction : aucun min()
        n'est evalue). Pourquoi l'ordre seul ne suffit pas : a charges
        egales, _assigner_worker realise toujours un cycle PERIODIQUE sur
        les workers — exactement la pathologie P1 (resonance) que
        l'aperiodique elimine (spec §4).
        None / indice invalide / exception -> repli sur _assigner_worker
        (charge-aware, comportement historique)."""
        designer = getattr(self._strategie, "designer_worker", None)
        if designer is not None:
            try:
                wid = designer(bench, maintenant)
            except Exception:
                wid = None
            if isinstance(wid, bool) or not isinstance(wid, int):
                wid = None
            elif not 0 <= wid < self.n_workers:
                wid = None
            if wid is not None:
                self._workers[wid].deposer(bench, exe)
                return
        self._assigner_worker(bench, exe)

    # -- fin d'execution ----------------------------------------------------
    def _notifier_fin(self, worker_id: int, bench: Bench, exe: _Execution,
                      valeur, erreur: Optional[str], duree_s: float) -> None:
        # Reprise A L'IDENTIQUE de scheduler.Scheduler : la garde
        # d'identite `_en_cours.get(id) is not exe` est le correctif de la
        # race watchdog (bug §4.3) — resultat d'une execution orpheline
        # (reassignee apres blocage) ignore, jamais double-comptabilise.
        with self._cond:
            if self._en_cours.get(bench.bench_id) is not exe:
                return  # execution orpheline : resultat ignore
            del self._en_cours[bench.bench_id]
            self._mem_utilisee_mo -= bench.mem_mo
            if self._est_lourd(bench):
                self._lourds_en_cours -= 1
            res = self._resultats[bench.bench_id]
            res.worker = worker_id
            res.duree_s = duree_s
            if erreur is None:
                res.status = BenchStatus.TERMINE
                res.value = valeur
                self._termines.add(bench.bench_id)
            else:
                if bench._tentatives < self.tentatives_max:
                    res.status = BenchStatus.BLOQUE
                    self._prets.append(bench)
                else:
                    res.status = BenchStatus.ECHOUE
                    res.error = erreur
                    self._termines.add(bench.bench_id)  # echec clos
            self._cond.notify_all()  # reveil du dispatch

    def _gerer_blocage(self, bench_id: str, exe: _Execution) -> None:
        # Reprise de scheduler.Scheduler : ressources liberees, bench
        # refile (priorite conservee, vieillissement aide), tentative++.
        bench = exe.bench
        del self._en_cours[bench_id]
        self._mem_utilisee_mo -= bench.mem_mo
        if self._est_lourd(bench):
            self._lourds_en_cours -= 1
        res = self._resultats[bench_id]
        res.status = BenchStatus.BLOQUE
        if bench._tentatives < self.tentatives_max:
            self._prets.append(bench)
        else:
            res.status = BenchStatus.ECHOUE
            res.error = "worker bloque : tentatives epuisees"
            self._termines.add(bench_id)
        self._cond.notify_all()

    # -- vol de travail (appele par les workers oisifs) ---------------------
    def _voler(self, voleur: _Worker):
        # Reprise de scheduler.Scheduler.
        with self._cond:
            donneurs = [w for w in self._workers
                        if w is not voleur and w.charge() > 1]
            if not donneurs:
                return None
            donneur = max(donneurs, key=lambda w: w.charge())
            return donneur.voler_queue()


# ---------------------------------------------------------------------------
# Adaptateur d'integration : fabrique pour Orchestrateur(fabrique_scheduler=...)
# ---------------------------------------------------------------------------

def fabrique_quasicristal(strategie: Optional[StrategieDensite] = None,
                          **options) -> Callable[..., OrdonnanceurQuasicristal]:
    """Fabrique compatible Orchestrateur(fabrique_scheduler=...).

    La fabrique recoit (n_workers, ram_totale_mo, capacite_file,
    tentatives_max) — les memes arguments que le Scheduler historique —
    et retourne un OrdonnanceurQuasicristal. `options` : parametres
    supplementaires du constructeur (facteur_blocage, timeout_hb_s, ...).

    Exemple :
        from quasicristal_sched import fabrique_quasicristal
        orch = Orchestrateur(n_workers=2,
                             fabrique_scheduler=fabrique_quasicristal())
        # Phase 2 :
        # orch = Orchestrateur(...,
        #     fabrique_scheduler=fabrique_quasicristal(
        #         strategie=StrategieDensiteQuasicristal()))
    """
    def _fabrique(n_workers: int, ram_totale_mo: float,
                  capacite_file: int, tentatives_max: int
                  ) -> OrdonnanceurQuasicristal:
        return OrdonnanceurQuasicristal(
            n_workers=n_workers,
            ram_totale_mo=ram_totale_mo,
            capacite_file=capacite_file,
            tentatives_max=tentatives_max,
            strategie=strategie,
            **options,
        )
    return _fabrique


# ---------------------------------------------------------------------------
# Demo fumee
# ---------------------------------------------------------------------------

def _demo() -> None:
    print("== demo quasicristal_sched : dispatch a reveil + batch ==")
    s = OrdonnanceurQuasicristal(n_workers=2, ram_totale_mo=7000)
    s.demarrer()

    def _dodo(ctx):
        ctx["heartbeat"]()
        time.sleep(0.2)
        return "ok"

    t0 = time.perf_counter()
    verdicts = s.soumettre_lot(
        [Bench("d%d" % i, _dodo, priorite=i % 2, mem_mo=32, duree_est_s=0.2)
         for i in range(6)])
    print("soumettre_lot :", [v.value for v in verdicts])
    print("attendre :", s.attendre(timeout_s=30.0),
          "en %.2f s" % (time.perf_counter() - t0))
    res = s.resultats()
    print("termines : %d/6, surcout : %.3f %%, replis strategie : %d" % (
        sum(1 for r in res.values() if r.status == BenchStatus.TERMINE),
        s.surcout_ratio() * 100.0, s.replis_strategie()))
    s.arreter()
    print("OK")


if __name__ == "__main__":
    _demo()
