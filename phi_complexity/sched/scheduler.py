#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordonnanceur de benches pour sandbox_mp (chantier 4 — RUCHE-SANDBOX-MP).

Conception (details in DESIGN_SCHEDULER.md):
  - file de priorite avec vieillissement (anti-famine),
  - N workers avec deques locales + vol de travail (work-stealing),
  - budget memoire global + plafond de benches memoire-lourds,
  - detection de worker bloque (heartbeat) + reassignation,
  - backpressure : FILE_PLEINE au-dela de la capacite.

Compatibilite : Python 3.11 et 3.12 (stdlib uniquement).
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Statuts
# ---------------------------------------------------------------------------

class BenchStatus(str, Enum):
    EN_ATTENTE = "EN_ATTENTE"      # soumis, en file
    PRET = "PRET"                  # dependances satisfaites
    EN_COURS = "EN_COURS"          # en execution sur un worker
    TERMINE = "TERMINE"
    ECHOUE = "ECHOUE"
    BLOQUE = "BLOQUE"              # worker bloque detecte (reassigne)


class SubmitResult(str, Enum):
    ACCEPTE = "ACCEPTE"
    FILE_PLEINE = "FILE_PLEINE"
    MEMOIRE_IMPOSSIBLE = "MEMOIRE_IMPOSSIBLE"   # mem > RAM totale : jamais lancable
    DEPENDANCE_INCONNUE = "DEPENDANCE_INCONNUE"


@dataclass
class BenchResult:
    bench_id: str
    status: BenchStatus
    value: Any = None
    error: Optional[str] = None
    worker: Optional[int] = None
    tentatives: int = 0
    attente_s: float = 0.0   # temps file avant 1er lancement
    duree_s: float = 0.0     # duree de la derniere tentative


@dataclass
class Bench:
    """Un bench soumis a l'ordonnanceur.

    fn(ctx) -> Any : ctx est un dict {'heartbeat': callable, 'bench_id': str,
                                       'tentative': int}.
    """
    bench_id: str
    fn: Callable[[Dict[str, Any]], Any]
    priorite: int = 0                      # plus grand = plus prioritaire
    mem_mo: float = 64.0                   # memoire estimee (Mo)
    duree_est_s: float = 1.0               # duree estimee (s)
    dependances: Set[str] = field(default_factory=set)
    # -- champs internes --
    _soumis_a: float = field(default=0.0, repr=False)
    _tentatives: int = field(default=0, repr=False)
    _seq: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self.dependances = set(self.dependances)


# ---------------------------------------------------------------------------
# Ordonnanceur
# ---------------------------------------------------------------------------

class Scheduler:
    def __init__(
        self,
        n_workers: int,
        ram_totale_mo: float,
        capacite_file: int = 200,
        seuil_lourd_mo: Optional[float] = None,
        facteur_blocage: float = 3.0,
        timeout_hb_s: float = 2.0,
        vieillissement: float = 0.5,      # points de priorite gagnes par seconde
        intervalle_surveillance_s: float = 0.1,
        tentatives_max: int = 3,
    ) -> None:
        if n_workers < 1:
            raise ValueError("n_workers >= 1 requis")
        if ram_totale_mo <= 0:
            raise ValueError("ram_totale_mo > 0 requis")
        self.n_workers = n_workers
        self.ram_totale_mo = float(ram_totale_mo)
        self.capacite_file = int(capacite_file)
        # Un bench est "lourd" s'il depasse seuil_lourd_mo ; M = combien tiennent dans la RAM.
        self.seuil_lourd_mo = float(seuil_lourd_mo) if seuil_lourd_mo else self.ram_totale_mo / 8.0
        self.M_lourds = max(1, int(self.ram_totale_mo // self.seuil_lourd_mo))
        self.facteur_blocage = float(facteur_blocage)
        self.timeout_hb_s = float(timeout_hb_s)
        self.vieillissement = float(vieillissement)
        self.intervalle_surveillance_s = float(intervalle_surveillance_s)
        self.tentatives_max = int(tentatives_max)

        # -- etat partage --
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._seq = itertools.count()

        self._benchs: Dict[str, Bench] = {}          # tous les benches connus
        self._prets: List[Bench] = []               # file de priorite (scan lineaire)
        self._bloques_dep: Dict[str, Bench] = {}     # en attente de dependances
        self._termines: Set[str] = set()
        self._resultats: Dict[str, BenchResult] = {}
        self._en_cours: Dict[str, "_Execution"] = {} # bench_id -> execution
        self._mem_utilisee_mo = 0.0
        self._lourds_en_cours = 0

        self._workers: List["_Worker"] = []
        self._arrete = False
        self._rr_index = 0
        self._fil_dispatch: Optional[threading.Thread] = None
        self._t0 = 0.0
        self._surcout_s = 0.0                        # temps CPU de l'ordonnanceur
        self._n_dispatch = 0

        # bornes de priorite vues (pour la borne de famine documentee)
        self._prio_min_vue: Optional[int] = None
        self._prio_max_vue: Optional[int] = None

    # -- soumission ---------------------------------------------------------
    def soumettre(self, bench: Bench) -> SubmitResult:
        with self._cond:
            if bench.bench_id in self._benchs:
                return SubmitResult.ACCEPTE  # deja connu : idempotent
            if bench.mem_mo > self.ram_totale_mo:
                return SubmitResult.MEMOIRE_IMPOSSIBLE
            if any(d not in self._benchs for d in bench.dependances):
                return SubmitResult.DEPENDANCE_INCONNUE
            total = len(self._benchs)
            if total >= self.capacite_file:
                return SubmitResult.FILE_PLEINE
            bench._soumis_a = time.monotonic()
            bench._seq = next(self._seq)
            self._benchs[bench.bench_id] = bench
            self._resultats[bench.bench_id] = BenchResult(bench_id=bench.bench_id,
                                                          status=BenchStatus.EN_ATTENTE)
            p = bench.priorite
            self._prio_min_vue = p if self._prio_min_vue is None else min(self._prio_min_vue, p)
            self._prio_max_vue = p if self._prio_max_vue is None else max(self._prio_max_vue, p)
            if bench.dependances <= self._termines:
                bench_status = BenchStatus.PRET
                self._prets.append(bench)
            else:
                bench_status = BenchStatus.EN_ATTENTE
                self._bloques_dep[bench.bench_id] = bench
            self._resultats[bench.bench_id].status = bench_status
            self._cond.notify_all()
            return SubmitResult.ACCEPTE

    # -- cycle de vie -------------------------------------------------------
    def demarrer(self) -> None:
        with self._cond:
            if self._fil_dispatch is not None:
                return
            self._t0 = time.monotonic()
            self._arrete = False
            for i in range(self.n_workers):
                w = _Worker(self, i)
                self._workers.append(w)
                w.demarrer()
            self._fil_dispatch = threading.Thread(target=self._boucle_dispatch,
                                                  name="dispatch", daemon=True)
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
        """Part du temps total consommee par l'ordonnanceur lui-meme."""
        total = time.monotonic() - self._t0 if self._t0 else 0.0
        if total <= 0:
            return 0.0
        return self._surcout_s / total

    def borne_attente_max_s(self) -> float:
        """Borne documentee d'attente maximale (anti-famine).

        Avec vieillissement alpha (pts/s) et ecart de priorite Delta :
        un bench rattrape n'importe quel concurrent en Delta/alpha secondes,
        puis devient le plus prioritaire. Ajouter la duree d'un cycle de
        dispatch et la pire duree d'un bench en cours (un bench lance
        monopolise son worker au pire duree_est * facteur_blocage avant
        d'etre declare bloque et libere).
        """
        if self._prio_min_vue is None or self._prio_max_vue is None:
            return 0.0
        delta = max(0, self._prio_max_vue - self._prio_min_vue)
        rattrapage = delta / self.vieillissement if self.vieillissement > 0 else float("inf")
        pire_blocage = max((b.duree_est_s for b in self._benchs.values()), default=0.0)
        pire_blocage *= self.facteur_blocage
        return rattrapage + self.intervalle_surveillance_s + pire_blocage

    def M_lourds_simultanes(self) -> int:
        return self.M_lourds

    # -- boucle de dispatch (coeur de l'ordonnanceur) -----------------------
    def _priorite_effective(self, bench: Bench, maintenant: float) -> float:
        return bench.priorite + self.vieillissement * (maintenant - bench._soumis_a)

    def _est_lourd(self, bench: Bench) -> bool:
        return bench.mem_mo >= self.seuil_lourd_mo

    def _boucle_dispatch(self) -> None:
        while True:
            t_debut = time.perf_counter()
            with self._cond:
                if self._arrete:
                    return
                maintenant = time.monotonic()
                # 1) liberer les benches dont les dependances sont satisfaites
                for bid in list(self._bloques_dep):
                    b = self._bloques_dep[bid]
                    if b.dependances <= self._termines:
                        del self._bloques_dep[bid]
                        self._prets.append(b)
                        self._resultats[bid].status = BenchStatus.PRET
                # 2) choisir le meilleur candidat lancable (memoire + quota lourds)
                meilleur: Optional[Bench] = None
                meilleure_prio = float("-inf")
                for b in self._prets:
                    if self._mem_utilisee_mo + b.mem_mo > self.ram_totale_mo:
                        continue  # backpressure memoire : attend, ne bloque pas les autres
                    if self._est_lourd(b) and self._lourds_en_cours >= self.M_lourds:
                        continue  # quota de benches lourds sature
                    p = self._priorite_effective(b, maintenant)
                    if p > meilleure_prio or (p == meilleure_prio and b._seq < (meilleur._seq if meilleur else 1 << 62)):
                        meilleur, meilleure_prio = b, p
                if meilleur is not None:
                    self._prets.remove(meilleur)
                    self._mem_utilisee_mo += meilleur.mem_mo
                    if self._est_lourd(meilleur):
                        self._lourds_en_cours += 1
                    meilleur._tentatives += 1
                    res = self._resultats[meilleur.bench_id]
                    if res.tentatives == 0:
                        res.attente_s = maintenant - meilleur._soumis_a
                    res.tentatives = meilleur._tentatives
                    res.status = BenchStatus.EN_COURS
                    exe = _Execution(meilleur, maintenant)
                    self._en_cours[meilleur.bench_id] = exe
                    self._assigner_worker(meilleur, exe)
                    self._n_dispatch += 1
                # 3) detection de workers bloques (heartbeat perime)
                for bid, exe in list(self._en_cours.items()):
                    if exe.est_bloquee(maintenant, self.facteur_blocage, self.timeout_hb_s):
                        self._gerer_blocage(bid, exe)
                # 4) reveiller les workers oisifs (vol de travail gere cote worker)
                self._cond.notify_all()
            t_fin = time.perf_counter()
            self._surcout_s += (t_fin - t_debut)
            time.sleep(self.intervalle_surveillance_s)

    def _assigner_worker(self, bench: Bench, exe: "_Execution") -> None:
        # round-robin sur les deques locales : le moins charge gagne ;
        # a charge egale, rotation (pas toujours le worker 0).
        def cle(w: "_Worker") -> tuple:
            return (w.charge(), (w.worker_id - self._rr_index) % self.n_workers)
        cible = min(self._workers, key=cle)
        self._rr_index = (cible.worker_id + 1) % self.n_workers
        cible.deposer(bench, exe)

    # -- fin d'execution ----------------------------------------------------
    def _notifier_fin(self, worker_id: int, bench: Bench, exe: "_Execution",
                      valeur: Any, erreur: Optional[str], duree_s: float) -> None:
        with self._cond:
            if self._en_cours.get(bench.bench_id) is not exe:
                return  # execution orpheline (reassignee apres blocage) : resultat ignore
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
                    # nouvel essai : refile avec priorite intacte (vieillissement aide)
                    res.status = BenchStatus.BLOQUE
                    self._prets.append(bench)
                else:
                    res.status = BenchStatus.ECHOUE
                    res.error = erreur
                    self._termines.add(bench.bench_id)  # echec definitif = clos
            self._cond.notify_all()

    def _gerer_blocage(self, bench_id: str, exe: "_Execution") -> None:
        """Un worker ne repond plus : on libere ses ressources et on refile le bench."""
        bench = exe.bench
        del self._en_cours[bench_id]
        self._mem_utilisee_mo -= bench.mem_mo
        if self._est_lourd(bench):
            self._lourds_en_cours -= 1
        res = self._resultats[bench_id]
        res.status = BenchStatus.BLOQUE
        if bench._tentatives < self.tentatives_max:
            self._prets.append(bench)   # reassigne a un autre worker au prochain cycle
        else:
            res.status = BenchStatus.ECHOUE
            res.error = "worker bloque : tentatives epuisees"
            self._termines.add(bench_id)
        self._cond.notify_all()

    # -- vol de travail (appele par les workers oisifs) ---------------------
    def _voler(self, voleur: "_Worker") -> Optional[tuple]:
        with self._cond:
            donneurs = [w for w in self._workers if w is not voleur and w.charge() > 1]
            if not donneurs:
                return None
            donneur = max(donneurs, key=lambda w: w.charge())
            return donneur.voler_queue()


class _Execution:
    """Une tentative d'execution d'un bench sur un worker."""
    def __init__(self, bench: Bench, debut: float) -> None:
        self.bench = bench
        self.debut = debut
        self.dernier_hb = debut
        self.hb_lock = threading.Lock()

    def heartbeat(self) -> None:
        with self.hb_lock:
            self.dernier_hb = time.monotonic()

    def est_bloquee(self, maintenant: float, facteur: float, timeout_hb: float) -> bool:
        ecoule = maintenant - self.debut
        if ecoule < facteur * self.bench.duree_est_s:
            return False
        with self.hb_lock:
            silence = maintenant - self.dernier_hb
        return silence > timeout_hb


class _Worker:
    def __init__(self, sched: Scheduler, worker_id: int) -> None:
        self.sched = sched
        self.worker_id = worker_id
        self._deque: deque = deque()
        self._dq_lock = threading.Lock()
        self._fil: Optional[threading.Thread] = None
        # Compteurs "collants" : evitent que le dispatch re-empile sur le
        # meme worker entre le depot et le demarrage reel (le worker vide
        # sa deque en quelques ms, bien avant l'iteration suivante).
        self._a_demarrer = 0   # deposes, pas encore pris en charge
        self._occupe = False   # en train d'executer un bench

    def demarrer(self) -> None:
        self._fil = threading.Thread(target=self._boucle, name=f"worker-{self.worker_id}",
                                     daemon=True)
        self._fil.start()

    def rejoindre(self) -> None:
        if self._fil is not None:
            self._fil.join(timeout=10.0)

    def charge(self) -> int:
        with self._dq_lock:
            return len(self._deque) + self._a_demarrer + (1 if self._occupe else 0)

    def deposer(self, bench: Bench, exe: _Execution) -> None:
        with self._dq_lock:
            self._deque.append((bench, exe))
            self._a_demarrer += 1
        with self.sched._cond:
            self.sched._cond.notify_all()

    def voler_queue(self) -> Optional[tuple]:
        with self._dq_lock:
            if self._deque:
                return self._deque.pop()   # vole par la queue (le moins urgent local)
            return None

    def _boucle(self) -> None:
        while True:
            with self.sched._cond:
                if self.sched._arrete:
                    return
            travail = self._prendre()
            if travail is None:
                with self.sched._cond:
                    self.sched._cond.wait(timeout=self.sched.intervalle_surveillance_s)
                continue
            bench, exe = travail
            with self._dq_lock:
                self._a_demarrer -= 1
                self._occupe = True
            try:
                self._executer(bench, exe)
            finally:
                with self._dq_lock:
                    self._occupe = False

    def _prendre(self) -> Optional[tuple]:
        with self._dq_lock:
            if self._deque:
                return self._deque.popleft()
        # deque locale vide : tenter le vol de travail
        return self.sched._voler(self)

    def _executer(self, bench: Bench, exe: _Execution) -> None:
        resultat: List[Any] = [None]
        erreur: List[Optional[str]] = [None]

        def cible() -> None:
            try:
                ctx = {"heartbeat": exe.heartbeat,
                       "bench_id": bench.bench_id,
                       "tentative": bench._tentatives}
                resultat[0] = bench.fn(ctx)
            except Exception as e:  # noqa: BLE001 - un bench ne doit jamais tuer le worker
                erreur[0] = f"{type(e).__name__}: {e}"

        t0 = time.monotonic()
        fil = threading.Thread(target=cible, daemon=True)
        fil.start()
        # Le worker attend la fin ; le moniteur (dispatch) peut declarer le
        # blocage pendant ce temps et reassigner le bench ailleurs.
        while fil.is_alive():
            with self.sched._cond:
                if self.sched._arrete:
                    break
                # si l'execution a ete reassignee, abandonner ce fil (orphelin)
                if self.sched._en_cours.get(bench.bench_id) is not exe:
                    break
            fil.join(timeout=self.sched.intervalle_surveillance_s)
        duree = time.monotonic() - t0
        if self.sched._en_cours.get(bench.bench_id) is exe:
            # toujours l'execution de reference : notifier
            self.sched._notifier_fin(self.worker_id, bench, exe,
                                     resultat[0], erreur[0], duree)
        # sinon : orphelin apres blocage, resultat ignore (la reassignment decide)
