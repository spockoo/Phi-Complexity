"""sondes.py — Sondes A/B de phi-complexity : tracer l'inconditionnel à partir du conditionnel.

Deux sondes lisent le même registre vivant des hypothèses
(REGISTRE_HYPOTHESES_20260929.md, statuts typés DÉMONTRÉ / CONDITIONNEL /
ÉVIDENCE / EN-COURS / CONJECTURAL / RÉFUTÉ / ÉLIMINÉ / ARCHIVÉ — jamais de
booléen) et l'interrogent depuis deux pôles :

- SONDE A (pôle fermeture) : partir du statut actuel d'un mécanisme
  (hypothèse nommée, sorry du Master, chantier) et tracer la ROUTE vers
  l'inconditionnel — la chaîne des hypothèses nommées dont il dépend, chacune
  exhibée avec statut typé, tag mur/technique, coût estimé, chantiers liés.
  La « distance de fermeture » est le DAG fini de ce qui doit tomber, dans
  l'ordre d'attaque du registre (§4).
- SONDE B (pôle obstruction) : tracer l'inconditionnel NÉGATIF — les verdicts
  RÉFUTÉ / ÉLIMINÉ sur le chemin du mécanisme, exhibés avec leur contenu
  (ce qui est mort définitivement, et pourquoi en une ligne), et comment ils
  resserrent l'espace restant.
- TROISIÈME ZONE — TERRA INCOGNITA (précision Tomy 2026-09-30) : en plus des
  chemins morts et vivants, la sonde A cartographie les nœuds du DAG qui
  n'ont JAMAIS été attaqués (aucun chantier, aucun verdict). Chaque nœud
  porte sa zone : DÉMONTRÉ | CONDITIONNEL | RÉFUTÉ | NON-ATTAQUÉ.
  La carte montre le territoire inexploré — pas qu'il contient un passage.

╔══════════════════════════════════════════════════════════════════════╗
║ INTERDICTION FORMELLE                                                ║
║ AUCUN score A/B unique. AUCUNE probabilité P(A). Les sondes          ║
║ exhibent des chaînes typées ; elles ne jugent pas. Tout scalaire     ║
║ résumant « à quel point on est proche de (A) » serait l'effondrement ║
║ booléen refusé — c'est de la tricherie instrumentale.                ║
║ Cette interdiction est testée mécaniquement (tests/test_sondes.py :  ║
║ balayage récursif des clés interdites dans toute sortie JSON).        ║
╚══════════════════════════════════════════════════════════════════════╝

Comportement par défaut INCHANGÉ : les sondes sont une sous-commande neuve
(`phi sonde`) ; elles ne modifient AUCUN JSON existant (bayes, croyances,
veille). Le flag `--exact` / `PHI_EFT=1` active la vérification renforcée :
double passe de parsing indépendante + concordance des comptes + MD5 du
registre en sortie. Pour les sondes, l'exactitude porte sur l'INTÉGRITÉ DU
PARSING (aucune arithmétique flottante n'est en jeu ici).

LIMITES (écrites en toutes lettres, comme pour l'EFT et l'oracle) :
- La sonde montre la ROUTE vers l'inconditionnel, pas sa praticabilité.
  Une hypothèse taguée « mur (dur) » n'est pas une case à cocher.
- Aveugle au contenu des preuves, comme tout phi-complexity : le registre
  est la seule source ; si le registre est périmé, la sonde est périmée.
- Les tags mur/technique viennent des classes du registre (§1/§4), pas d'un
  jugement de phi. Sans info au registre : « non tagué ».
"""

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .core import VERSION


# ────────────────────────────────────────────────────────
# TROIS ZONES (précision Tomy 2026-09-30)
# ────────────────────────────────────────────────────────
# En plus des chemins RÉFUTÉ (morts) et CONDITIONNEL (vivants), la sonde
# cartographie la TERRA INCOGNITA : les mécanismes nommés au registre qui
# n'ont JAMAIS été attaqués (aucun chantier, aucun verdict, aucune
# transition). La carte montre le territoire inexploré — pas qu'il contient
# un passage (non-garantie explicite, voir Sondes_AB_20260930.md).
#
# zone_sonde : DÉMONTRÉ | CONDITIONNEL | RÉFUTÉ | NON-ATTAQUÉ | <statut brut>
# Les statuts auxiliaires (POINTEUR, PARAMÈTRE, INCONNU…) gardent leur
# statut typé d'origine : on ne les force dans aucune des quatre zones.

def zone_sonde(h: "Hypothese") -> str:
    """Zone d'un nœud du DAG : mort, vivant, fermé, ou terra incognita."""
    s = h.statut
    if s == "DÉMONTRÉ":
        return "DÉMONTRÉ"
    if s in ("RÉFUTÉ", "ÉLIMINÉ"):
        return "RÉFUTÉ"
    if s == "NON-ATTAQUÉ":
        return "NON-ATTAQUÉ"
    if s == "CONDITIONNEL":
        return "CONDITIONNEL"
    if s == "NOMMÉ" or s.startswith("NOMMÉ "):
        # Nommé mais jamais attaqué ? → terra incognita.
        # Attaqué (chantiers ou transition §1bis) mais non fermé ? → vivant.
        # Récolté au journal : le chantier cité est celui où l'objet a été
        # NOMMÉ, pas attaqué comme cible → terra incognita par défaut.
        if h.source.startswith("journal §12"):
            return "NON-ATTAQUÉ"
        if not h.chantiers and h.source in ("table §1", "faisabilité §4"):
            return "NON-ATTAQUÉ"
        return "CONDITIONNEL"
    if s in ("EN-COURS", "CONJECTURAL", "ÉVIDENCE"):
        return "CONDITIONNEL"
    return s  # POINTEUR, PARAMÈTRE, INCONNU… : statut brut conservé


# ────────────────────────────────────────────────────────
# CONSTANTES
# ────────────────────────────────────────────────────────

STATUTS_TYPES = [
    "DÉMONTRÉ", "CONDITIONNEL", "ÉVIDENCE", "EN-COURS", "CONJECTURAL",
    "RÉFUTÉ", "ÉLIMINÉ", "ARCHIVÉ",
    # statuts auxiliaires du registre (inventaire, pas fermeture)
    "NOMMÉ", "POINTEUR", "PARAMÈTRE",
]

# Formes féminisées / variantes rencontrées dans le registre → canonique.
_NORMALISATION_STATUT = {
    "DÉMONTRÉE": "DÉMONTRÉ", "DÉMONTRÉES": "DÉMONTRÉ",
    "CONDITIONNELLE": "CONDITIONNEL", "CONDITIONNELLES": "CONDITIONNEL",
    "NOMMÉE": "NOMMÉ", "NOMMÉES": "NOMMÉ",
    "RÉFUTÉE": "RÉFUTÉ", "RÉFUTÉES": "RÉFUTÉ",
    "ÉLIMINÉE": "ÉLIMINÉ", "ÉLIMINÉES": "ÉLIMINÉ",
    "ARCHIVÉE": "ARCHIVÉ",
    "PROUVÉE": "DÉMONTRÉ", "PROUVÉ": "DÉMONTRÉ",  # « obstruction prouvée »
    "LEVÉE": "ÉLIMINÉ",  # levée par refactorisation : ne bloque plus
}

# Les six trous du Master, numérotation canonique du registre lui-même
# (« Statuts des trous après campagne », en-tête du registre).
SORRY_VERS_TROU = {
    "local_existence": 1,
    "maximalTime_pos": 2,
    "local_uniqueness": 3,
    "energy_identity": 4,
    "leray_existence": 5,
    "BKM_criterion": 6,
}

# Classe du registre → tag sonde. « non tagué » si le registre ne dit rien.
TAG_PAR_EMOJI = {
    "🚪": "technique (molle)",
    "🧱": "mur (dur)",
    "🏰": "mur (dur, forteresse)",
    "⚖️": "hors-classe (contenu Clay)",
}
TAG_NON_TAGUE = "non tagué"

# Ordre d'attaque (§4) : les portes d'abord, les forteresses en dernier.
_RANG_TAG = {
    "technique (molle)": 0,
    "mur (dur)": 1,
    "mur (dur, forteresse)": 2,
    "hors-classe (contenu Clay)": 3,
    "non tagué": 4,
}

# Statuts qui ne bloquent plus la fermeture (route A).
STATUTS_LEVES = {"DÉMONTRÉ", "ÉLIMINÉ"}

# Clés interdites dans TOUTE sortie JSON (testées mécaniquement).
CLES_INTERDITES = {
    "score", "scores", "score_a", "score_b", "scoreab", "note_a", "note_b",
    "probabilite", "proba", "p_a", "p_b", "pA", "pB",
    "classement_ab", "indice_victoire", "closeness",
}

INTERDICTION_TEXTE = (
    "INTERDICTION FORMELLE : aucun score A/B unique, aucune probabilité P(A). "
    "Les sondes exhibent des chaînes typées ; elles ne jugent pas."
)

LIMITE_SONDES = (
    "SONDES A/B — limites : la sonde A montre la ROUTE vers l'inconditionnel, "
    "pas sa praticabilité (un « mur (dur) » n'est pas une case à cocher). "
    "Aveugle au contenu des preuves comme tout phi-complexity : seule source, "
    "le registre ; registre périmé ⇒ sonde périmée. Tags mur/technique issus "
    "des classes du registre, pas d'un jugement de phi. " + INTERDICTION_TEXTE
)

REGISTRE_DEFAUT = os.path.expanduser(
    "~/workspace/lean-navier-stokes/REGISTRE_HYPOTHESES_20260929.md"
)


def registre_disponible() -> bool:
    """Le registre Lean réel est-il présent sur cette machine ?

    Les sondes sont opt-in par conception : elles sondent la vraie chaîne
    Lean quand elle est là, et se taisent (skip) sinon — jamais d'échec
    sur une machine sans la chaîne (ex. CI publique).
    """
    return os.path.isfile(REGISTRE_DEFAUT)


# ────────────────────────────────────────────────────────
# MODÈLE DE DONNÉES
# ────────────────────────────────────────────────────────

@dataclass
class Hypothese:
    """Une hypothèse nommée du registre, avec son état typé."""
    nom: str
    statut: str = "INCONNU"
    trous: List[int] = field(default_factory=list)
    contenu: str = ""
    tag: str = TAG_NON_TAGUE
    cout: str = ""
    chantiers: List[str] = field(default_factory=list)
    fichier_ligne: str = ""
    source: str = ""  # "table §1" | "delta §1bis" | "faisabilité §4" | "journal §12"
    # Ancrage entropique (chantier ANCRAGE-ENTROPIE, 2026-09-30) :
    # - double_statut : verdict scindé (ex. ch.139 : version inconditionnelle
    #   RÉFUTÉE + version conditionnelle DÉMONTRÉE) — jamais un seul statut.
    # - section_entropie : mesures de la lentille (bits d'attention).
    # Les deux restent None hors rattachement : le JSON par défaut est inchangé.
    double_statut: Optional[dict] = None
    section_entropie: Optional[dict] = None

    def vers_dict(self) -> dict:
        d = {
            "nom": self.nom,
            "statut": self.statut,
            "zone": zone_sonde(self),
            "trous": list(self.trous),
            "contenu": self.contenu,
            "tag": self.tag,
            "cout_estime": self.cout,
            "chantiers": list(self.chantiers),
            "fichier_ligne": self.fichier_ligne,
            "source": self.source,
        }
        if self.double_statut is not None:
            d["double_statut"] = dict(self.double_statut)
        if self.section_entropie is not None:
            d["entropie"] = dict(self.section_entropie)
        return d


@dataclass
class Obstruction:
    """Un verdict négatif inconditionnel : ce qui est mort, et pourquoi."""
    titre: str
    verdicts: List[str] = field(default_factory=list)  # ex. ["RÉFUTÉ"]
    trous: List[int] = field(default_factory=list)
    chantier: str = ""
    vice: str = ""       # vice exact, une ligne
    interdit: str = ""   # ce qu'il est interdit de faire désormais
    remplace: str = ""   # la voie de remplacement, si elle existe
    ancrage: str = ""

    def vers_dict(self) -> dict:
        return {
            "titre": self.titre,
            "verdicts": list(self.verdicts),
            "trous": list(self.trous),
            "chantier": self.chantier,
            "vice_exact": self.vice,
            "interdit": self.interdit,
            "remplace": self.remplace,
            "ancrage": self.ancrage,
        }


@dataclass
class EntreeJournal:
    """Une entrée du journal des chantiers (§12) : statuts + identifiants récoltés."""
    chantier: str
    statut_affiche: str = ""
    verdicts: List[str] = field(default_factory=list)
    trous: List[int] = field(default_factory=list)
    identifiants: List[str] = field(default_factory=list)
    resume: str = ""
    corps: str = ""  # corps brut (tronqué) : voisinage des identifiants


@dataclass
class ResultatSonde:
    """Le résultat complet d'une interrogation A/B."""
    mecanisme: str
    type_mecanisme: str          # "sorry" | "hypothese" | "chantier" | "inconnu"
    trous: List[int] = field(default_factory=list)
    statut_mecanisme: str = ""
    route_a: List[Hypothese] = field(default_factory=list)
    distance_fermeture: List[Hypothese] = field(default_factory=list)
    levees: List[Hypothese] = field(default_factory=list)
    terra_incognita: List[Hypothese] = field(default_factory=list)
    obstructions_b: List[Obstruction] = field(default_factory=list)
    refutes_journal_b: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    integrite: dict = field(default_factory=dict)
    # Ancrage entropique (chantier ANCRAGE-ENTROPIE, 2026-09-30) :
    # - entropie : section lentille du mécanisme (remplie par ancrage.py via
    #   import direct d'entropie.py — jamais dans sonder() lui-même, qui
    #   resterait récursif : entropie_depuis_sonde appelle sonder()).
    # - doubles_verdicts : verdicts scindés rattachés au niveau nœud
    #   (ex. ch.139 : CONDITIONNEL sous domination + RÉFUTÉ inconditionnel).
    entropie: dict = field(default_factory=dict)
    doubles_verdicts: List[dict] = field(default_factory=list)
    # Analyse d'impact native (chantier PHI-NATIF-B, 2026-10-08) :
    # - impact : section « dépendants + score de risque » calculée par
    #   impact_sonde.analyser_impact quand le mécanisme sondé correspond
    #   à un symbole Python (fonction, classe, méthode, module).
    #   Remplie par le CLI (jamais dans sonder() lui-même, qui reste
    #   pur registre) ; {} = désactivée (--sans-impact).
    impact: dict = field(default_factory=dict)

    def vers_dict(self) -> dict:
        # NOTE : aucune clé de CLES_INTERDITES ne doit jamais apparaître ici.
        return {
            "mecanisme": self.mecanisme,
            "type_mecanisme": self.type_mecanisme,
            "trous": list(self.trous),
            "statut_mecanisme": self.statut_mecanisme,
            "doubles_verdicts": [dict(dv) for dv in self.doubles_verdicts],
            "entropie": dict(self.entropie),
            "impact": dict(self.impact),
            "sonde_a": {
                "route_vers_inconditionnel": [h.vers_dict() for h in self.route_a],
                "distance_fermeture": [h.vers_dict() for h in self.distance_fermeture],
                "levees": [h.vers_dict() for h in self.levees],
                "terra_incognita": [h.vers_dict() for h in self.terra_incognita],
                "avertissement": (
                    "La route est exhibée, pas sa praticabilité : un "
                    "« mur (dur) » n'est pas une case à cocher. La terra "
                    "incognita montre où l'on n'est PAS allé — pas qu'il y "
                    "a un passage."
                ),
            },
            "sonde_b": {
                "inconditionnel_negatif": [o.vers_dict() for o in self.obstructions_b],
                "refutations_journal": list(self.refutes_journal_b),
                "lecture": (
                    "Ces voies sont mortes définitivement ; l'espace où "
                    "l'inconditionnel positif peut se trouver est resserré d'autant."
                ),
            },
            "notes": list(self.notes),
            "integrite": dict(self.integrite),
            "limites": LIMITE_SONDES,
            "interdiction": INTERDICTION_TEXTE,
            "version_phi": VERSION,
            "horodatage": datetime.now(timezone.utc).isoformat(),
        }


# ────────────────────────────────────────────────────────
# PARSING DÉFENSIF DU REGISTRE
# ────────────────────────────────────────────────────────

_RE_SECTION = re.compile(r"^##\s+(§\S*)\.?\s*(.*)$")
_RE_LIGNE_TABLEAU = re.compile(r"^\s*\|.*\|\s*$")
_RE_SEPARATEUR = re.compile(r"^\s*\|[\s\-|:]+\|\s*$")
_RE_HNUM = re.compile(r"\[?(?<![A-Za-z0-9_])(H\d+[a-z]?)(?![A-Za-z0-9_])\]?")
_RE_BACKTICK = re.compile(r"`([^`]+)`")
_RE_TROU = re.compile(r"[Tt]rous?\s+(\d+)")
_RE_CHANTIER = re.compile(r"[Cc]h(?:antier)?\.?\s*([A-Za-z0-9]+)")
_RE_SORRY = re.compile(r"sorry\s+([a-zA-Z_][a-zA-Z0-9_]*)")
# Verdict typé attaché à un identifiant : « `X` : RÉFUTÉ », « `X` — **ÉLIMINÉ** »,
# « (`X`, DÉMONTRÉ) ».
_RE_VERDICT_ATTACHE = re.compile(
    r"`([^`]{3,80}?)`\s*[:\u2014\u2013\-,]\s*\*{0,2}([A-Z\u00c0-\u00de\u00c9\u00c8\u00ca][A-Z\u00c0-\u00de\u00c9\u00c8\u00ca\-]*)"
)
# Termes de métier du registre : pas des mécanismes, jamais dans la route.
TERMES_EXCLUS_ROUTE = {"sSup", "sorryAx", "sorry", "axioms"}


def _normaliser_statut(brut: str) -> str:
    """Extrait le premier statut typé d'une cellule, normalisé au canonique."""
    for mot in re.findall(r"[A-ZÀ-ÞÉÈÊ\-]+", brut):
        if mot in _NORMALISATION_STATUT:
            return _NORMALISATION_STATUT[mot]
        if mot in STATUTS_TYPES:
            return mot
    return ""


def _statut_apres_fleche(cellule: str) -> str:
    """Statut le plus récent : après la dernière flèche de transition."""
    partie = cellule.rsplit("→", 1)[-1] if "→" in cellule else cellule
    return _normaliser_statut(partie)


def _extraire_trous(cellule: str) -> List[int]:
    """Trous servis : tous les entiers de la cellule (« 1 (→2, 3) » → [1,2,3])."""
    vus, res = set(), []
    for m in re.finditer(r"\d+", cellule):
        n = int(m.group(0))
        if n not in vus and 1 <= n <= 9:
            vus.add(n)
            res.append(n)
    return sorted(res)


def _nom_principal(cellule: str) -> Tuple[str, List[str]]:
    """Premier identifiant backtick = nom principal ; les autres = alias."""
    ids = _RE_BACKTICK.findall(cellule)
    ids = [i.strip() for i in ids if i.strip()]
    if ids:
        return ids[0], ids[1:]
    # repli : numéro H (ex. « H9 | domination Duhamel–Bochner »)
    m = _RE_HNUM.search(cellule)
    if m:
        return m.group(1), []
    return "", []


def _decouper_cellules(ligne: str) -> List[str]:
    return [c.strip() for c in ligne.strip().strip("|").split("|")]


class RegistreSondes:
    """Parseur défensif du registre vivant. Ne crashe jamais sur une entrée
    malformée : les entrées non parsées sont COMPTÉES et signalées."""

    def __init__(self) -> None:
        self.hypotheses: Dict[str, Hypothese] = {}
        self.obstructions: List[Obstruction] = []
        self.journal: Dict[str, EntreeJournal] = {}
        self.entrees_parsees = 0
        self.entrees_non_parsees = 0
        self.md5 = ""
        self.chemin = ""

    # ——— chargement ———

    def charger(self, chemin: str) -> "RegistreSondes":
        self.chemin = chemin
        with open(chemin, "r", encoding="utf-8") as f:
            texte = f.read()
        self.md5 = hashlib.md5(texte.encode("utf-8")).hexdigest()
        sections = self._decouper_sections(texte)
        self._parser_table_maitresse(sections.get("§1", ""))
        self._parser_delta(sections.get("§1bis", ""))
        self._parser_faisabilite(sections.get("§4", ""))
        self._parser_journal(sections.get("§12", ""))
        self._parser_obstructions(sections.get("§5", ""))
        return self

    def _decouper_sections(self, texte: str) -> Dict[str, str]:
        sections, cle, buf = {}, "", []
        for ligne in texte.splitlines():
            m = _RE_SECTION.match(ligne)
            if m:
                if cle:
                    sections[cle] = "\n".join(buf)
                cle = m.group(1).rstrip(".")
                buf = [ligne]
            elif cle:
                buf.append(ligne)
        if cle:
            sections[cle] = "\n".join(buf)
        return sections

    # ——— §1 : table maîtresse ———

    def _parser_table_maitresse(self, section: str) -> None:
        for ligne in section.splitlines():
            if not _RE_LIGNE_TABLEAU.match(ligne) or _RE_SEPARATEUR.match(ligne):
                continue
            cellules = _decouper_cellules(ligne)
            if len(cellules) < 8:
                continue
            if not _RE_HNUM.match(cellules[0].strip()):
                continue  # ligne d'en-tête répétée
            try:
                numero = _RE_HNUM.search(cellules[0]).group(1)
                nom, _alias = _nom_principal(cellules[1])
                nom = nom or numero
                statut = _statut_apres_fleche(cellules[3]) or "NOMMÉ"
                trous = _extraire_trous(cellules[4])
                contenu = re.sub(r"\s+", " ", cellules[5]).strip()
                emoji = next((e for e in TAG_PAR_EMOJI if e in cellules[7]), "")
                tag = TAG_PAR_EMOJI.get(emoji, TAG_NON_TAGUE)
                h = Hypothese(
                    nom=nom, statut=statut, trous=trous, contenu=contenu,
                    tag=tag, fichier_ligne=cellules[2].strip("` "),
                    source="table §1",
                )
                self.hypotheses[numero] = h
                if nom != numero:
                    self.hypotheses[nom] = h
                # chantiers cités dans la cellule statut (« ch.87 »)
                for mc in _RE_CHANTIER.findall(cellules[3]):
                    if mc not in h.chantiers:
                        h.chantiers.append(mc)
                self.entrees_parsees += 1
            except Exception:
                self.entrees_non_parsees += 1

    # ——— §1bis : transitions de statut (le plus récent fait foi) ———

    def _parser_delta(self, section: str) -> None:
        for ligne in section.splitlines():
            if not _RE_LIGNE_TABLEAU.match(ligne) or _RE_SEPARATEUR.match(ligne):
                continue
            cellules = _decouper_cellules(ligne)
            if len(cellules) < 5 or not _RE_HNUM.search(cellules[0]):
                continue
            try:
                numero = _RE_HNUM.search(cellules[0]).group(1)
                qualificatif = re.search(r"\(([^)]+)\)", cellules[0])
                transition = cellules[1]
                nouveau = _statut_apres_fleche(transition)
                chantier = cellules[2].strip()
                ancrage = re.sub(r"\s+", " ", cellules[3]).strip()
                if qualificatif and "remplacement" in qualificatif.group(1):
                    # Le remplacement est un objet distinct : ne pas écraser.
                    base = self.hypotheses.get(numero)
                    if base is not None:
                        base.chantiers.append(f"{chantier} (remplacement)")
                    self.entrees_parsees += 1
                    continue
                h = self.hypotheses.get(numero)
                if h is None:
                    # Nouveau nœud (ex. [H29b]) : créer l'entrée.
                    h = Hypothese(nom=numero, source="delta §1bis")
                    self.hypotheses[numero] = h
                if nouveau:
                    h.statut = nouveau
                if chantier and chantier not in h.chantiers and chantier != "—":
                    h.chantiers.append(chantier)
                if ancrage:
                    h.fichier_ligne = ancrage[:160]
                h.source = "table §1 + delta §1bis"
                self.entrees_parsees += 1
            except Exception:
                self.entrees_non_parsees += 1

    # ——— §4 : faisabilité — coûts + tags d'ordre d'attaque ———

    def _parser_faisabilite(self, section: str) -> None:
        tag_courant = TAG_NON_TAGUE
        for ligne in section.splitlines():
            m = re.match(r"^###\s+(.+)$", ligne)
            if m:
                titre = m.group(1)
                emoji = next((e for e in TAG_PAR_EMOJI if e in titre), "")
                tag_courant = TAG_PAR_EMOJI.get(emoji, TAG_NON_TAGUE)
                continue
            if not _RE_LIGNE_TABLEAU.match(ligne) or _RE_SEPARATEUR.match(ligne):
                continue
            cellules = _decouper_cellules(ligne)
            if len(cellules) < 4:
                continue
            if cellules[0].strip().lower() in ("hypothèse", "hypothese"):
                continue  # ligne d'en-tête du tableau, pas une entrée
            try:
                nom_h = _RE_HNUM.search(cellules[0])
                nom_bt, _ = _nom_principal(cellules[0])
                cle = nom_h.group(1) if nom_h else nom_bt
                if not cle:
                    self.entrees_non_parsees += 1
                    continue
                cout = re.sub(r"\s+", " ", cellules[1]).strip()
                if cout == "—" or not cout:
                    cout = ""
                statut4 = _normaliser_statut(cellules[3])
                h = self.hypotheses.get(cle)
                if h is None:
                    h = Hypothese(nom=nom_bt or cle, source="faisabilité §4")
                    self.hypotheses[cle] = h
                    if nom_bt and nom_bt != cle:
                        self.hypotheses[nom_bt] = h
                # §4 n'écrase le statut que s'il est plus précis et non vide ;
                # §1bis reste la source de vérité des transitions.
                if statut4 and h.statut in ("NOMMÉ", "INCONNU", ""):
                    h.statut = statut4
                if cout and not h.cout:
                    h.cout = cout
                # Le tag d'ordre d'attaque (§4) prime sur l'emoji §1.
                if tag_courant != TAG_NON_TAGUE:
                    h.tag = tag_courant
                # Propagation des trous via la colonne « Débloque »
                # (ex. [H29b] débloque « H29, H30 » → trous de H29/H30).
                for mh2 in _RE_HNUM.finditer(cellules[2]):
                    href = self.hypotheses.get(mh2.group(1))
                    if href:
                        for t in href.trous:
                            if t not in h.trous:
                                h.trous.append(t)
                h.trous.sort()
                self.entrees_parsees += 1
            except Exception:
                self.entrees_non_parsees += 1

    # ——— §5 : obstructions ———

    def _parser_obstructions(self, section: str) -> None:
        # Découpe par en-têtes ### ; le titre peut courir sur 2 lignes
        # (ex. « … — SONDE : version\ninconditionnelle RÉFUTÉE, … »).
        morceaux = re.split(r"(?m)^###\s+", section)
        for morceau in morceaux[1:]:
            lignes = morceau.splitlines()
            if not lignes:
                continue
            titre_parts = [lignes[0].strip()]
            idx = 1
            while (idx < len(lignes) and lignes[idx].strip()
                   and not lignes[idx].startswith(("`", "-", "**", "|", "#"))
                   and not titre_parts[-1].endswith(")")):
                titre_parts.append(lignes[idx].strip())
                idx += 1
            titre = " ".join(titre_parts)
            corps = "\n".join(lignes[idx:])
            try:
                verdicts = []
                for mot in re.findall(r"[A-ZÀ-ÞÉÈÊ\-]+", titre):
                    n = _NORMALISATION_STATUT.get(mot, mot if mot in STATUTS_TYPES else "")
                    if n and n not in verdicts:
                        verdicts.append(n)
                if not verdicts:
                    self.entrees_non_parsees += 1
                    continue
                trous = sorted({int(x) for x in _RE_TROU.findall(titre + " " + corps[:600])})
                mch = re.search(r"\(chantier (\d+)[^)]*\)", titre) or _RE_CHANTIER.search(titre)
                chantier = mch.group(1) if mch else ""
                # Rattachement aux trous via le n° H du titre (ex. H26 → trou 4).
                mh = _RE_HNUM.search(titre)
                if mh:
                    href = self.hypotheses.get(mh.group(1))
                    if href:
                        for t in href.trous:
                            if t not in trous:
                                trous.append(t)
                # … ou via les chantiers cités → leurs trous au journal.
                for mc in set(_RE_CHANTIER.findall(titre)):
                    ent = self.journal.get(mc)
                    if ent:
                        for t in ent.trous:
                            if t not in trous:
                                trous.append(t)
                trous.sort()

                def _champ(marque: str) -> str:
                    # Deux formes au registre : « **Marque :** valeur » et
                    # « **Marque : valeur.** » (tout en gras).
                    m = re.search(
                        r"\*\*" + re.escape(marque) + r"\s*:\*\*\s*(.+?)"
                        r"(?=\n\*\*|\n###\s|\n##\s|\Z)",
                        corps, re.DOTALL,
                    )
                    if not m:
                        m = re.search(
                            r"\*\*" + re.escape(marque) + r"\s*:\s*(.+?)\*\*",
                            corps, re.DOTALL,
                        )
                    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

                vice = _champ("Vice exact")
                interdit = _champ("Interdit")
                remplace = _champ("Remplace")
                # Repli : première phrase du corps si aucun champ typé.
                if not vice and not interdit:
                    premier = corps.strip().split("\n\n")[0]
                    vice = re.sub(r"\s+", " ", premier)[:280]
                self.obstructions.append(Obstruction(
                    titre=re.sub(r"\s+", " ", titre)[:200],
                    verdicts=verdicts, trous=trous, chantier=chantier,
                    vice=vice[:400], interdit=interdit[:400],
                    remplace=remplace[:400],
                    ancrage=f"§5 — {titre[:80]}",
                ))
                self.entrees_parsees += 1
            except Exception:
                self.entrees_non_parsees += 1

    # ——— §12 : journal des chantiers — récolte d'identifiants ———

    def _parser_journal(self, section: str) -> None:
        # En-têtes : « - **Ch.130** », « - **Ch.W1 (PARI 5)** »,
        # « - **Ch.138 — TRILOGIE PICARD** » (texte avant le ** fermant).
        morceaux = re.split(r"(?m)^-\s+\*\*Ch\.([A-Za-z0-9]+)\b[^*\n]*\*\*", section)
        # morceaux[0] = avant la première entrée ; puis (num, corps) en alternance.
        for i in range(1, len(morceaux), 2):
            numero, corps = morceaux[i], morceaux[i + 1] if i + 1 < len(morceaux) else ""
            try:
                verdicts = []
                for mot in re.findall(r"[A-ZÀ-ÞÉÈÊ\-]+", corps):
                    n = _NORMALISATION_STATUT.get(mot, mot if mot in STATUTS_TYPES else "")
                    if n and n not in verdicts:
                        verdicts.append(n)
                trous = sorted({int(x) for x in _RE_TROU.findall(corps)})
                for nom_sorry, trou in SORRY_VERS_TROU.items():
                    if nom_sorry in corps and trou not in trous:
                        trous.append(trou)
                trous.sort()
                for m in _RE_HNUM.finditer(corps):
                    h = self.hypotheses.get(m.group(1))
                    if h:
                        for t in h.trous:
                            if t not in trous:
                                trous.append(t)
                trous.sort()
                identifiants = []
                for ident in _RE_BACKTICK.findall(corps):
                    ident = ident.strip()
                    if not ident or len(ident) < 3 or " " in ident:
                        continue  # fragment de prose, pas un symbole
                    if re.search(r"\.(lean|md|json|toml|tex|pdf)$", ident):
                        continue
                    if re.fullmatch(r"[0-9a-f]{32}", ident):
                        continue  # MD5, pas un symbole
                    if ident not in identifiants:
                        identifiants.append(ident)
                premiere = corps.strip().split("\n")[0]
                self.journal[numero] = EntreeJournal(
                    chantier=numero, verdicts=verdicts, trous=trous,
                    identifiants=identifiants,
                    resume=re.sub(r"\s+", " ", premiere)[:220],
                    corps=corps[:6000],
                )
                self.entrees_parsees += 1
            except Exception:
                self.entrees_non_parsees += 1


# ────────────────────────────────────────────────────────
# RÉSOLUTION DU MÉCANISME
# ────────────────────────────────────────────────────────

def resoudre_mecanisme(requete: str, registre: RegistreSondes) -> dict:
    """Résout <mécanisme> → {type, trous, statut, notes}. Ne crashe jamais."""
    q = requete.strip().strip("`")
    notes: List[str] = []
    # 1. sorry du Master
    if q in SORRY_VERS_TROU:
        return {"type": "sorry", "nom": q, "trous": [SORRY_VERS_TROU[q]],
                "statut": "sorry du Master (non fermé)", "notes": notes}
    # 2. numéro de chantier (« ch.139 », « 139 », « chantier W1 »)
    mch = re.fullmatch(r"(?:ch(?:antier)?\.?\s*)?([A-Za-z0-9]+)", q, re.IGNORECASE)
    if mch:
        cle = mch.group(1)
        entree = registre.journal.get(cle) or registre.journal.get(cle.lstrip("0"))
        if entree is not None:
            return {"type": "chantier", "nom": f"Ch.{entree.chantier}",
                    "trous": list(entree.trous),
                    "statut": "/".join(entree.verdicts) or "voir journal",
                    "notes": notes}
    # 3. hypothèse nommée (numéro H ou nom exact)
    h = registre.hypotheses.get(q)
    if h is None:
        mh = _RE_HNUM.search(q)
        if mh:
            h = registre.hypotheses.get(mh.group(1))
    if h is not None:
        if not h.trous:
            notes.append(
                f"« {h.nom} » sans trou rattaché au registre : route limitée "
                "aux mentions du journal."
            )
        return {"type": "hypothese", "nom": h.nom, "trous": list(h.trous),
                "statut": h.statut, "notes": notes}
    # 4. identifiant récolté au journal uniquement
    for entree in registre.journal.values():
        if q in entree.identifiants:
            return {"type": "hypothese", "nom": q,
                    "trous": list(entree.trous),
                    "statut": "NOMMÉ (récolté au journal §12, Ch.%s)" % entree.chantier,
                    "notes": notes}
    notes.append(
        f"« {q} » non résolu au registre : ni sorry, ni hypothèse, ni chantier."
    )
    return {"type": "inconnu", "nom": q, "trous": [],
            "statut": "mécanisme non résolu", "notes": notes}


# ────────────────────────────────────────────────────────
# SONDES A ET B
# ────────────────────────────────────────────────────────

def _est_symbole(ident: str) -> bool:
    """Filtre anti-bruit : garde les identifiants qui ressemblent à des
    symboles Lean (CamelCase / snake_case), pas aux fragments de prose."""
    if len(ident) < 4 or " " in ident:
        return False
    if re.search(r"\.(lean|md|json|toml)$", ident):
        return False
    return bool(re.search(r"[A-Z]", ident) or "_" in ident)


def sonder(requete: str, registre: RegistreSondes,
           exact: bool = False) -> ResultatSonde:
    """Interroge les deux sondes pour un mécanisme. Jamais de score, jamais
    de P(A) : les deux pôles exhibent des chaînes typées."""
    resolution = resoudre_mecanisme(requete, registre)
    trous = resolution["trous"]
    res = ResultatSonde(
        mecanisme=requete,
        type_mecanisme=resolution["type"],
        trous=trous,
        statut_mecanisme=resolution["statut"],
        notes=list(resolution["notes"]),
    )

    if not trous:
        res.notes.append("Aucun trou rattaché : les deux sondes restent vides.")
        res.integrite = _bloc_integrite(registre, exact)
        return res

    # —— SONDE A : route vers l'inconditionnel ——
    vus = set()

    # Pertinence d'un identifiant récolté : objet nommé du domaine
    # (table, obstruction, mot-clé Hypothesis/Data/Conjecture) ou objet
    # récurrent (≥2 entrées du journal) — pas un lemme Mathlib isolé.
    freq: Dict[str, int] = {}
    for entree in registre.journal.values():
        for ident in set(entree.identifiants):
            freq[ident] = freq.get(ident, 0) + 1
    obs_titres = " ".join(ob.titre for ob in registre.obstructions)

    # Verdicts attachés par identifiant (journal) : servent le statut des
    # objets récoltés (ex. (`zeroAdmissibleData`, DÉMONTRÉ) → levée).
    verdicts_attaches: Dict[str, str] = {}
    for entree in registre.journal.values():
        for m in _RE_VERDICT_ATTACHE.finditer(entree.corps):
            ident = m.group(1).strip()
            v = _normaliser_statut(m.group(2))
            if v and ident not in verdicts_attaches:
                verdicts_attaches[ident] = v

    def _pertinent(ident: str) -> bool:
        if ident in TERMES_EXCLUS_ROUTE:
            return False
        if ident in registre.hypotheses:
            return True
        if ident in obs_titres:
            return True
        if re.search(r"(Hypothes|Conjecture|Assumption)", ident):
            return True
        if ident.endswith("Data"):
            return True
        return freq.get(ident, 0) >= 2

    def _ajouter(h: Hypothese) -> None:
        cle = h.nom
        if cle in vus:
            return
        vus.add(cle)
        res.route_a.append(h)

    # (i) hypothèses de la table dont les trous intersectent
    for h in registre.hypotheses.values():
        if any(t in trous for t in h.trous):
            _ajouter(h)
    # (ii) identifiants récoltés au journal sur les mêmes trous
    nom_mecanisme = resolution["nom"].lower()
    for entree in registre.journal.values():
        if not any(t in trous for t in entree.trous):
            continue
        for ident in entree.identifiants:
            if not _est_symbole(ident) or ident in vus or not _pertinent(ident):
                continue
            if ident.lower() == nom_mecanisme:
                continue  # le mécanisme ne se sonde pas lui-même
            connu = registre.hypotheses.get(ident)
            if connu is not None:
                _ajouter(connu)
                continue
            statut_recolte = verdicts_attaches.get(ident, "NOMMÉ (récolté au journal)")
            h = Hypothese(
                nom=ident,
                statut=statut_recolte,
                trous=list(entree.trous),
                contenu=f"Objet nommé récolté au journal §12, Ch.{entree.chantier}.",
                chantiers=[entree.chantier],
                source=f"journal §12 (Ch.{entree.chantier})",
            )
            _ajouter(h)

    # Ordre d'attaque du registre : portes → murs → forteresses → hors-classe.
    res.route_a.sort(key=lambda h: (_RANG_TAG.get(h.tag, 4), h.nom))
    for h in res.route_a:
        if h.statut in STATUTS_LEVES:
            res.levees.append(h)
            continue
        res.distance_fermeture.append(h)
        if zone_sonde(h) == "NON-ATTAQUÉ":
            res.terra_incognita.append(h)

    # —— SONDE B : inconditionnel négatif ——
    for ob in registre.obstructions:
        lie = any(t in trous for t in ob.trous)
        if not lie and resolution["type"] == "sorry":
            lie = resolution["nom"] in (ob.titre + " " + ob.vice + " " + ob.interdit)
        if not lie and resolution["type"] == "chantier":
            lie = f"Ch.{ob.chantier}" == resolution["nom"] or \
                  ob.chantier == resolution["nom"].replace("Ch.", "")
        if lie:
            res.obstructions_b.append(ob)
    # Réfutations nommées au journal : verdict TYPÉ syntaxiquement attaché à
    # l'identifiant (« `X` : RÉFUTÉ ») — jamais de verdict voisin capturé
    # au hasard (leçon : « RÉFUTÉE par `g4_kinetic_shape_differ` » ne réfute
    # pas g4_kinetic_shape_differ, c'est l'instrument qui s'en sert).
    for entree in registre.journal.values():
        if not any(t in trous for t in entree.trous):
            continue
        for m in _RE_VERDICT_ATTACHE.finditer(entree.corps):
            ident = m.group(1).strip()
            verdict = _normaliser_statut(m.group(2))
            if verdict not in ("RÉFUTÉ", "ÉLIMINÉ"):
                continue
            if not _est_symbole(ident):
                continue
            if any(r["identifiant"] == ident and r["chantier"] == entree.chantier
                   for r in res.refutes_journal_b):
                continue
            contexte = entree.corps[max(0, m.start() - 60):m.end() + 120]
            res.refutes_journal_b.append({
                "identifiant": ident,
                "verdicts": [verdict],
                "chantier": entree.chantier,
                "contexte": re.sub(r"\s+", " ", contexte).strip()[:300],
            })

    res.integrite = _bloc_integrite(registre, exact)
    # Doubles verdicts (ch.139 et analogues) : rattachés au niveau nœud,
    # jamais laissés en Sonde B seule avec un nœud NON-ATTAQUÉ.
    _attacher_doubles_verdicts(res)
    return res


def _bloc_integrite(registre: RegistreSondes, exact: bool) -> dict:
    """Bloc d'intégrité : en mode exact, double passe de parsing indépendante
    et concordance des comptes. (Pour les sondes, l'exactitude porte sur
    l'intégrité du parsing — aucune arithmétique flottante n'est en jeu.)"""
    bloc = {
        "registre": os.path.basename(registre.chemin),
        "md5_registre": registre.md5,
        "entrees_parsees": registre.entrees_parsees,
        "entrees_non_parsees": registre.entrees_non_parsees,
        "mode": "exact (double passe)" if exact else "standard",
    }
    if exact:
        # Seconde passe indépendante : re-parse depuis le disque.
        second = RegistreSondes().charger(registre.chemin)
        bloc["passes"] = 2
        bloc["concordantes"] = (
            second.entrees_parsees == registre.entrees_parsees
            and second.entrees_non_parsees == registre.entrees_non_parsees
            and second.md5 == registre.md5
        )
        if not bloc["concordantes"]:
            bloc["alerte"] = (
                "DIVERGENCE entre les deux passes de parsing — "
                "résultat à auditer avant usage."
            )
    return bloc


# ────────────────────────────────────────────────────────
# DOUBLES VERDICTS — verdicts scindés rattachés au niveau nœud
# (chantier ANCRAGE-ENTROPIE, 2026-09-30)
# ────────────────────────────────────────────────────────
# Leçon du chantier 139 : une obstruction peut porter DEUX verdicts
# (ex. §5 : « version inconditionnelle RÉFUTÉE, version conditionnelle
# DÉMONTRÉE »). Les exhiber seulement en Sonde B en laissant le nœud
# NON-ATTAQUÉ dans le DAG, c'est mentir par omission : le nœud porte
# explicitement les deux versions, au lieu d'un seul statut.
#
# Règle : une obstruction est à double verdict si ses verdicts mêlent un
# pôle positif (DÉMONTRÉ / CONDITIONNEL / ÉVIDENCE) et un pôle négatif
# (RÉFUTÉ / ÉLIMINÉ). Les versions sont parsées depuis le titre
# (« version <X> <STATUT> ») ; à défaut, les deux verdicts sont exhibés
# sans étiquette de version (jamais inventée).

_RE_VERSION_STATUT = re.compile(
    r"version\s+([a-zàâäéèêëîïôöùûüç]+)\s+"
    r"([A-ZÀ-ÞÉÈÊ][A-ZÀ-ÞÉÈÊ\-]*)",
    re.IGNORECASE,
)

_STATUTS_POSITIFS = {"DÉMONTRÉ", "CONDITIONNEL", "ÉVIDENCE"}
_STATUTS_NEGATIFS = {"RÉFUTÉ", "ÉLIMINÉ"}


def est_double_verdict(ob: Obstruction) -> bool:
    """Vrai si l'obstruction mêle un verdict positif et un verdict négatif."""
    v = set(ob.verdicts)
    return bool(v & _STATUTS_POSITIFS) and bool(v & _STATUTS_NEGATIFS)


def versions_double_verdict(ob: Obstruction) -> List[dict]:
    """Paires [{version, statut}] parsées depuis le titre (« version X STATUT »).

    Jamais inventées : si le titre ne porte pas la forme « version », rend [].
    """
    versions = []
    for m in _RE_VERSION_STATUT.finditer(ob.titre):
        statut = _normaliser_statut(m.group(2))
        if statut:
            versions.append({
                "version": m.group(1).lower(),
                "statut": statut,
            })
    return versions


def _cles_titre_obstruction(titre: str) -> List[str]:
    """Identifiants concernés par une obstruction : H-numéros + noms backtick."""
    cles = _RE_HNUM.findall(titre)
    cles += [c.strip() for c in _RE_BACKTICK.findall(titre) if c.strip()]
    return cles


def _attacher_doubles_verdicts(res: ResultatSonde) -> None:
    """Rattache les doubles verdicts au niveau nœud (et au mécanisme lui-même).

    Remplit res.doubles_verdicts et le champ double_statut des nœuds
    concernés. Ne touche jamais au registre : lecture seule — les nœuds
    partagés avec le registre sont COPIÉS avant rattachement (pas de
    pollution inter-appels).
    """
    import copy as _copy
    copies: Dict[int, Hypothese] = {}

    def _noeud_propre(h: Hypothese) -> Hypothese:
        if id(h) not in copies:
            copies[id(h)] = _copy.copy(h)
        return copies[id(h)]

    for ob in res.obstructions_b:
        if not est_double_verdict(ob):
            continue
        versions = versions_double_verdict(ob)
        cles = set(_cles_titre_obstruction(ob.titre))
        if not cles:
            continue
        fiche = {
            "obstruction": ob.titre,
            "chantier": ob.chantier,
            "verdicts": list(ob.verdicts),
            "versions": versions,
            "note": (
                "Double verdict : les deux versions sont exhibées "
                "explicitement — jamais un seul statut. "
                + ("Versions parsées depuis le titre du registre. "
                   if versions else
                   "Versions non étiquetées au registre : les deux "
                   "verdicts sont exhibés sans étiquette (jamais inventée). ")
                + ("Vice : " + ob.vice[:160] if ob.vice else "")
            ).strip(),
        }
        rattaches = 0
        # (i) le mécanisme lui-même, s'il est concerné
        mec_bas = res.mecanisme.strip().strip("`").lower()
        if any(c.lower() == mec_bas or mec_bas in c.lower() or c.lower() in mec_bas
               for c in cles if len(c) >= 4):
            entree = dict(fiche)
            entree["noeud"] = res.mecanisme
            entree["portee"] = "mecanisme"
            res.doubles_verdicts.append(entree)
            rattaches += 1
        # (ii) les nœuds de la route A (+ terra incognita, incluse dans route_a)
        for h in res.route_a:
            if h.nom in cles or any(
                    h.nom.lower() == c.lower() for c in cles):
                h2 = _noeud_propre(h)
                h2.double_statut = dict(fiche)
                h2.double_statut["noeud"] = h2.nom
                h2.double_statut["portee"] = "noeud"
                entree = dict(fiche)
                entree["noeud"] = h2.nom
                entree["portee"] = "noeud"
                res.doubles_verdicts.append(entree)
                rattaches += 1
        if rattaches == 0:
            # Exhibé quand même : le double verdict existe mais ne touche
            # aucun nœud de cette route — jamais silencieusement ignoré.
            entree = dict(fiche)
            entree["noeud"] = ""
            entree["portee"] = "hors_route"
            entree["note"] += (" Aucun nœud de la route de « %s » n'est "
                               "concerné." % res.mecanisme)
            res.doubles_verdicts.append(entree)
    # Remap : les listes de nœuds pointent vers les copies propres.
    if copies:
        remap = lambda h: copies.get(id(h), h)
        res.route_a = [remap(h) for h in res.route_a]
        res.distance_fermeture = [remap(h) for h in res.distance_fermeture]
        res.levees = [remap(h) for h in res.levees]
        res.terra_incognita = [remap(h) for h in res.terra_incognita]


# ────────────────────────────────────────────────────────
# RENDUS
# ────────────────────────────────────────────────────────

def rendre_sonde_console(res: ResultatSonde) -> str:
    """Rendu console lisible : les deux pôles, les chaînes typées."""
    L = []
    L.append("╔" + "═" * 62 + "╗")
    L.append("║" + "PHI-COMPLEXITY — SONDES A/B 🔭".center(62) + "║")
    L.append("║" + "tracer l'inconditionnel à partir du conditionnel".center(62) + "║")
    L.append("╚" + "═" * 62 + "╝")
    L.append("")
    L.append(f"Mécanisme : {res.mecanisme}  [{res.type_mecanisme}]")
    if res.trous:
        L.append(f"Trous rattachés : {', '.join('trou ' + str(t) for t in res.trous)}")
    L.append(f"Statut actuel : {res.statut_mecanisme}")
    integ = res.integrite
    L.append(f"Registre : {integ.get('registre', '?')} "
             f"(md5 {integ.get('md5_registre', '?')[:12]}…, "
             f"{integ.get('entrees_parsees', '?')} parsées, "
             f"{integ.get('entrees_non_parsees', '?')} non parsées)")
    for note in res.notes:
        L.append(f"  note : {note}")
    L.append("")
    # — Sonde A —
    L.append("┌─ SONDE A — pôle fermeture : la route vers l'inconditionnel")
    if not res.route_a:
        L.append("│  (route vide)")
    else:
        for h in res.route_a:
            marque = "✓" if h.statut in STATUTS_LEVES else "·"
            cout = f" · {h.cout} l." if h.cout else ""
            ch = f" · ch.{'/'.join(h.chantiers)}" if h.chantiers else ""
            L.append(f"│  {marque} {h.nom}  [{h.statut} · {h.tag}{cout}{ch}]")
            if h.contenu:
                L.append(f"│      {h.contenu[:110]}")
        L.append("│")
        L.append(f"│  Distance de fermeture : {len(res.distance_fermeture)} nœud(s) "
                 f"non fermé(s), {len(res.levees)} levé(s) — DAG fini, "
                 f"ordre d'attaque §4 du registre.")
        L.append("│  ⚠ La sonde montre la ROUTE, pas sa praticabilité : un "
                 "« mur (dur) » n'est pas une case à cocher.")
        if res.terra_incognita:
            L.append("│")
            L.append(f"│  ▓ TERRA INCOGNITA — où l'on n'est pas allé "
                     f"({len(res.terra_incognita)} nœud(s) jamais attaqués) :")
            for h in res.terra_incognita:
                cout = f" · {h.cout} l." if h.cout else ""
                L.append(f"│    ? {h.nom}  [{h.tag}{cout}]")
                if h.contenu:
                    L.append(f"│        {h.contenu[:100]}")
            L.append("│  La carte montre le territoire inexploré — pas qu'il "
                     "contient un passage.")
    L.append("└" + "─" * 61)
    L.append("")
    # — Sonde B —
    L.append("┌─ SONDE B — pôle obstruction : l'inconditionnel négatif")
    if not res.obstructions_b and not res.refutes_journal_b:
        L.append("│  (aucune obstruction rattachée au registre)")
    for ob in res.obstructions_b:
        L.append(f"│  ✗ {ob.titre}")
        L.append(f"│      verdicts : {' / '.join(ob.verdicts)}"
                 + (f" · ch.{ob.chantier}" if ob.chantier else ""))
        if ob.vice:
            L.append(f"│      vice : {ob.vice[:160]}")
        if ob.interdit:
            L.append(f"│      interdit : {ob.interdit[:160]}")
        if ob.remplace:
            L.append(f"│      remplace : {ob.remplace[:160]}")
    for rj in res.refutes_journal_b:
        L.append(f"│  ✗ {rj['identifiant']} — {' / '.join(rj['verdicts'])} "
                 f"(journal Ch.{rj['chantier']})")
        if rj["contexte"]:
            L.append(f"│      {rj['contexte'][:160]}")
    if res.obstructions_b or res.refutes_journal_b:
        L.append("│")
        L.append("│  Ces voies sont mortes définitivement ; l'espace où "
                 "l'inconditionnel")
        L.append("│  positif peut se trouver est resserré d'autant.")
    L.append("└" + "─" * 61)
    L.append("")
    # — Impact natif : dépendants et risque de modification (PHI-NATIF-B) —
    # Rendu seulement si la section est active ({} = --sans-impact).
    if res.impact:
        L.append("┌─ IMPACT — dépendants et risque de modification (natif)")
        imp = res.impact
        noeud = imp.get("noeud")
        avert = imp.get("avertissement")
        if noeud:
            L.append(f"│  Symbole : {noeud}")
            dep = imp.get("dependants", {}) or {}
            tronq = ", tronqué" if dep.get("tronque") else ""
            L.append(f"│  Dépendants : {dep.get('nombre', '?')} "
                     f"(profondeur max {dep.get('profondeur_max', '?')}{tronq})")
            ris = imp.get("risque", {}) or {}
            score = ris.get("score")
            score_txt = f"{score:.1f}/100" if isinstance(score, (int, float)) else "?"
            L.append(f"│  Score de risque : {score_txt} — "
                     f"niveau {ris.get('niveau', '?')}")
            fact = ris.get("facteurs", {}) or {}
            couvert = "couvert" if fact.get("T") else "non couvert"
            L.append(f"│    D={fact.get('D', '?')} dépendants, "
                     f"P={fact.get('P', '?')}, T={couvert} par les tests, "
                     f"C={fact.get('C', '?')} (cyclomatique)")
            candidats = imp.get("autres_candidats") or []
            if candidats:
                suite = "…" if len(candidats) > 5 else ""
                L.append(f"│  Autres candidats : {', '.join(candidats[:5])}{suite}")
            rg = imp.get("graphe", {}) or {}
            L.append(f"│  Graphe : {rg.get('noeuds', '?')} nœuds, "
                     f"{rg.get('aretes', '?')} arêtes "
                     f"(racine : {imp.get('racine', '?')})")
        if avert:
            L.append(f"│  ⚠ {avert}")
        L.append("└" + "─" * 61)
        L.append("")
    # — Doubles verdicts : statuts scindés, jamais un seul statut —
    if res.doubles_verdicts:
        L.append("┌─ DOUBLES VERDICTS — statuts scindés au niveau nœud")
        for dv in res.doubles_verdicts:
            cible = dv.get("noeud") or "(hors route)"
            L.append(f"│  ⑂ {cible}  [{dv.get('portee', '?')}]")
            for v in dv.get("versions", []):
                L.append(f"│      version {v['version']} : {v['statut']}")
            if not dv.get("versions"):
                L.append(f"│      verdicts : {' / '.join(dv.get('verdicts', []))}")
            ch = dv.get("chantier")
            L.append(f"│      ← {dv.get('obstruction', '')[:90]}"
                     + (f" (ch.{ch})" if ch else ""))
        L.append("│  Les deux versions sont exhibées explicitement : "
                 "jamais un seul statut.")
        L.append("└" + "─" * 61)
        L.append("")
    L.append(INTERDICTION_TEXTE)
    L.append("Limites : " + LIMITE_SONDES[:220] + "… (voir Sondes_AB_20260930.md)")
    return "\n".join(L)


def sonder_depuis_disque(requete: str, chemin_registre: str = REGISTRE_DEFAUT,
                         exact: bool = False) -> ResultatSonde:
    """Point d'entrée pratique : charge le registre puis sonde."""
    registre = RegistreSondes().charger(chemin_registre)
    return sonder(requete, registre, exact=exact)
