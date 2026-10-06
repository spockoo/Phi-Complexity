"""
cli.py — Interface en ligne de commande souveraine.
Suturée selon les recommandations de phi-complexity v0.1.0 (Protocole BMAD).
main() décomposée en 5 fonctions hermétiques — Règle I : Herméticité de la Portée.
Multi-langage depuis 0.2.0 : détection automatique par extension + option --lang.
"""
import sys
import os
import argparse

from . import auditer, rapport_console, rapport_markdown, rapport_json, rapport_sarif
from .core import VERSION
from .langs import est_fichier_supporte


# ────────────────────────────────────────────────────────
# CONSTRUCTION DU PARSEUR (hermétique, sans effets de bord)
# ────────────────────────────────────────────────────────

def _construire_parseur() -> argparse.ArgumentParser:
    """Construit et retourne le parseur d'arguments. Aucun état global."""
    parser = argparse.ArgumentParser(
        prog="phi",
        description="phi-complexity — Audit de code multi-langage par les invariants du nombre d'or (φ)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  phi check mon_script.py
  phi check ./src/ --min-radiance 75
  phi check app.js
  phi check script_sans_extension --lang ruby
  phi report mon_script.py --output rapport.md
  phi check mon_script.py --format json
  phi check mon_script.py --formule "100 - lilith_variance/phi"
  phi check ./src/ --gate "radiance >= 75 and nb_anomalies == 0"
  phi check mon_script.py --format sarif
  phi edit mon_script.py --projet ./src/
  phi index ./src/ --format json | jq .collisions
  phi index ./src/ --rapide   # phase 1 seule : symboles sans métriques
  phi chemins ./src/ --format json   # carte croyante : quel trou attaquer en premier
  phi snapshot ./src/ --out ref.json  # figer l'état structurel (référence)
  phi veille ./src/ --ref ref.json    # détecter les dégradations silencieuses
        """
    )
    parser.add_argument("--version", action="version", version=f"phi-complexity {VERSION}")

    subparsers = parser.add_subparsers(dest="commande")

    check = subparsers.add_parser("check", help="Auditer un fichier ou un dossier")
    check.add_argument("cible", help="Fichier ou dossier à auditer (tout langage supporté)")
    check.add_argument("--min-radiance", type=float, default=0,
                       help="Score minimum (exit code 1 si en-dessous)")
    check.add_argument("--format", choices=["console", "json", "sarif"], default="console",
                       help="Format de sortie (console, json, sarif)")
    check.add_argument("--lang", default=None,
                       help="Force le langage (ex: javascript, java, go...) au lieu de la détection par extension")
    check.add_argument("--formule", default=None,
                       help="Formule arithmétique personnalisée évaluée sur les métriques")
    check.add_argument("--gate", default=None,
                       help="Porte logique de qualité : exit 1 si l'expression est fausse")

    report = subparsers.add_parser("report", help="Générer un rapport Markdown")
    report.add_argument("cible", help="Fichier à analyser")
    report.add_argument("--output", "-o", default=None,
                        help="Fichier de sortie (ex: rapport.md)")
    report.add_argument("--lang", default=None,
                        help="Force le langage (ex: javascript, java, go...) au lieu de la détection par extension")

    bayes = subparsers.add_parser("infer", help="Inférence bayésienne et prédiction d'auto-suture")
    bayes.add_argument("cible", help="Fichier à analyser avec le moteur bayésien")
    bayes.add_argument("--lang", default=None, help="Force le langage")
    bayes.add_argument("--format", choices=["console", "json"], default="console", help="Format de sortie")
    bayes.add_argument("--exact", action="store_true",
                       help="Arithmétique double-double certifiée (EFT, opt-in)")

    edit = subparsers.add_parser("edit", help="Éditer un fichier dans l'éditeur phi (TUI)")
    edit.add_argument("cible", help="Fichier à éditer (tout fichier texte ; créé s'il n'existe pas)")
    edit.add_argument("--projet", default=None,
                      help="Dossier projet pour l'index des symboles (défaut : dossier parent du fichier)")
    edit.add_argument("--lang", default=None,
                      help="Force le langage pour l'audit phi (ex: python)")

    index = subparsers.add_parser("index", help="Carte du projet : symboles, collisions, santé phi")
    index.add_argument("dossier", help="Dossier projet à cartographier")
    index.add_argument("--format", choices=["console", "json"], default="console",
                       help="Format de sortie (console lisible, ou json pour les agents/jq)")
    index.add_argument("--lang", default=None,
                       help="Force le langage d'analyse et d'audit (ex: python)")
    index.add_argument("--exclude", default=None,
                       help="Dossiers supplémentaires à exclure, séparés par des virgules "
                            "(s'ajoutent aux exclusions par défaut : .lake/, .git/, "
                            "__pycache__/, node_modules/, ...)")
    index.add_argument("--no-exclude", action="store_true",
                       help="Désactive les exclusions par défaut : indexe tout, "
                            "y compris .lake/, .git/, __pycache__/")
    index.add_argument("--rapide", action="store_true",
                       help="Phase 1 seule : index des symboles (noms, lignes) "
                            "sans calculer les métriques — quasi-instantané. "
                            "La radiance et l'oudjat sont alors non calculés "
                            "(null dans le JSON, signalés dans la console).")

    chemins = subparsers.add_parser("chemins", help="Carte croyante : symboles classés par postérieur "
                                                    "(quel trou attaquer en premier)")
    chemins.add_argument("dossier", help="Dossier projet à prioriser")
    chemins.add_argument("--format", choices=["console", "json"], default="console",
                         help="Format de sortie (console lisible, ou json pour les agents/jq)")
    chemins.add_argument("--top", type=int, default=10,
                         help="Taille de la liste « attaquer en premier » (défaut : 10)")
    chemins.add_argument("--lang", default=None,
                         help="Force le langage d'analyse et d'audit (ex: python)")
    chemins.add_argument("--exclude", default=None,
                         help="Dossiers supplémentaires à exclure, séparés par des virgules")
    chemins.add_argument("--no-exclude", action="store_true",
                         help="Désactive les exclusions par défaut")
    chemins.add_argument("--exact", action="store_true",
                         help="Arithmétique double-double certifiée (EFT, opt-in)")

    snapshot = subparsers.add_parser("snapshot",
                                     help="Figer l'état structurel d'un dossier "
                                          "(carte croyante + arêtes) dans une "
                                          "enveloppe datée et signée")
    snapshot.add_argument("dossier", help="Dossier projet à figer")
    snapshot.add_argument("--out", "-o", default=None,
                          help="Fichier de sortie JSON (défaut : "
                               "phi_snapshot_AAAAMMJJ_HHMMSS.json dans le "
                               "dossier courant)")
    snapshot.add_argument("--lang", default=None,
                          help="Force le langage d'analyse (ex: python)")
    snapshot.add_argument("--exclude", default=None,
                          help="Dossiers supplémentaires à exclure, séparés par des virgules")
    snapshot.add_argument("--no-exclude", action="store_true",
                          help="Désactive les exclusions par défaut")
    snapshot.add_argument("--force", action="store_true",
                          help="Fige quand même la baseline même si l'instrument "
                               "est dégradé (arrière-plan perdu) — à n'utiliser "
                               "qu'en connaissance de cause ; par défaut la "
                               "baseline est REFUSÉE (durcissement 2026-10-01)")

    veille = subparsers.add_parser("veille",
                                    help="Comparer l'état courant d'un dossier "
                                         "à une référence `phi snapshot` : "
                                         "détecte les dégradations silencieuses")
    veille.add_argument("dossier", help="Dossier projet à surveiller")
    veille.add_argument("--ref", required=True,
                        help="Enveloppe de référence (fichier `phi snapshot`)")
    veille.add_argument("--format", choices=["console", "json"], default="console",
                        help="Format de sortie (console lisible, ou json pour les agents/jq)")
    veille.add_argument("--lang", default=None,
                        help="Force le langage d'analyse (ex: python)")
    veille.add_argument("--exclude", default=None,
                        help="Dossiers supplémentaires à exclure, séparés par des virgules")
    veille.add_argument("--no-exclude", action="store_true",
                        help="Désactive les exclusions par défaut")

    oracle = subparsers.add_parser("oracle",
                                   help="Traces d'oracle : la chaîne de raisonnement "
                                        "exhibée (prior → évidences → postérieur → action)")
    oracle.add_argument("cible", help="Fichier (moteur bayésien) ou dossier "
                                      "(chemins croyants) à interroger")
    oracle.add_argument("--symbole", default=None,
                        help="Ne montre que les traces de ce symbole/hypothèse "
                             "(comparaison exacte)")
    oracle.add_argument("--format", choices=["console", "json"], default="console",
                        help="Format de sortie (console lisible, ou json pour les agents/jq)")
    oracle.add_argument("--exact", action="store_true",
                        help="Arithmétique double-double certifiée (EFT, opt-in) : "
                             "ajoute la borne d'erreur certifiée à chaque trace")
    oracle.add_argument("--top", type=int, default=10,
                        help="Taille de la liste « attaquer en premier » (dossiers)")

    radar = subparsers.add_parser("radar",
                                  help="Témoin : vue du mouvement structurel "
                                       "entre deux états (faits uniquement, "
                                       "jamais de verdict)")
    radar.add_argument("--avant", required=True,
                       help="État avant : dossier ou fichier .lean")
    radar.add_argument("--apres", required=True,
                       help="État après : dossier ou fichier .lean")
    radar.add_argument("--format", choices=["console", "json"],
                       default="console",
                       help="Format de sortie (console lisible, ou json "
                            "pour les agents/jq)")
    radar.add_argument("--inclure-hors-chaine", action="store_true",
                       help="Inclure hors_chaine_clay/ dans la détection de "
                            "doublons (exclu par défaut : déclaré hors de la "
                            "chaîne Clay)")

    sismique = subparsers.add_parser("sismique",
                                     help="Témoin : mémoire des rythmes via "
                                          "l'historique git (faits uniquement, "
                                          "jamais de verdict)")
    sismique.add_argument("--depuis", required=True,
                          help="Borne de départ : date AAAA-MM-JJ ou ISO")
    sismique.add_argument("--depot", default=".",
                          help="Dépôt git à analyser (défaut : répertoire courant)")
    sismique.add_argument("--format", choices=["console", "json"],
                          default="console",
                          help="Format de sortie (console lisible, ou json "
                               "pour les agents/jq)")

    consigner_cmd = subparsers.add_parser("consigner",
                                          help="Consigner une décision humaine "
                                               "de veto sur une observation "
                                               "(saisie manuelle)")
    consigner_cmd.add_argument("--kind", required=True,
                               help="Kind de l'observation (radar)")
    consigner_cmd.add_argument("--fichier", required=True)
    consigner_cmd.add_argument("--ligne", type=int, required=True)
    consigner_cmd.add_argument("--nom", required=True)
    consigner_cmd.add_argument("--veto", required=True,
                               choices=["oui", "non"],
                               help="Veto exercé ou non (décision humaine)")
    consigner_cmd.add_argument("--motif", required=True,
                               help="Motif de la décision (≥10 caractères)")
    consigner_cmd.add_argument("--registre", default=None,
                               help="Fichier registre (défaut : "
                                    "~/.phi-complexity-registre-observations.json)")

    registre_cmd = subparsers.add_parser("registre",
                                         help="Lister les décisions de veto "
                                              "consignées")
    registre_cmd.add_argument("--registre", default=None,
                              help="Fichier registre (défaut : "
                                   "~/.phi-complexity-registre-observations.json)")

    sonde = subparsers.add_parser("sonde",
                                  help="Sondes A/B : tracer l'inconditionnel à "
                                       "partir du conditionnel (pôle fermeture / "
                                       "pôle obstruction)")
    sonde.add_argument("mecanisme",
                       help="Mécanisme à sonder : sorry du Master "
                            "(ex. energy_identity), hypothèse nommée "
                            "(ex. H29, KatoBilinearData) ou chantier (ex. 138)")
    sonde.add_argument("--registre", default=None,
                       help="Chemin du registre vivant des hypothèses "
                            "(défaut : REGISTRE_HYPOTHESES_20260929.md du dépôt lean)")
    sonde.add_argument("--format", choices=["console", "json"], default="console",
                       help="Format de sortie (console lisible, ou json pour les agents/jq)")
    sonde.add_argument("--exact", action="store_true",
                       help="Vérification renforcée (opt-in, cohérent avec PHI_EFT) : "
                            "double passe de parsing indépendante + concordance "
                            "des comptes + MD5 du registre en sortie")
    sonde.add_argument("--dossier", default=None,
                       help="Dossier pour les postérieurs des croyances "
                            "(mode --format json uniquement : attache la "
                            "section `entropie` par nœud ; défaut : "
                            "~/workspace/lean-navier-stokes)")

    piste_sorry = subparsers.add_parser("piste-sorry",
                                        help="Piste d'un sorry : inventaire "
                                             "rigoureux, graphe d'imports et "
                                             "chantiers du registre — le chemin "
                                             "depuis n'importe quelle complexité "
                                             "Lean 4")
    piste_sorry.add_argument("dossier",
                             help="Dossier du projet Lean 4 à explorer")
    piste_sorry.add_argument("--sorry", required=True,
                             help="Nom du sorry à pister (ex. leray_existence)")
    piste_sorry.add_argument("--registre", default=None,
                             help="Chemin du registre vivant des hypothèses "
                                  "(opt-in : sans lui, piste structurelle seule ; "
                                  "défaut : REGISTRE_HYPOTHESES_20260929.md du dépôt lean)")
    piste_sorry.add_argument("--format", choices=["console", "json"], default="console",
                             help="Format de sortie (console lisible, ou json pour les agents/jq)")

    chemins_verifiables = subparsers.add_parser("chemins-verifiables",
                                               help="Chemins vérifiables vers un sorry : "
                                                    "candidats de câblage guidés, chacun "
                                                    "identifié formellement comme une preuve "
                                                    "que Lean tranche (PROUVÉ / RÉFUTÉ / "
                                                    "INDÉCIDÉ)")
    chemins_verifiables.add_argument("dossier",
                                     help="Dossier du projet Lean 4 à explorer")
    chemins_verifiables.add_argument("--sorry", required=True,
                                     help="Nom du sorry visé (ex. leray_existence)")
    chemins_verifiables.add_argument("--registre", default=None,
                                     help="Chemin du registre vivant des hypothèses "
                                          "(opt-in ; défaut : REGISTRE_HYPOTHESES_20260929.md "
                                          "du dépôt lean)")
    chemins_verifiables.add_argument("--max-candidats", type=int, default=12,
                                     help="Borne anti-explosion : au plus N candidats "
                                          "(défaut : 12)")
    chemins_verifiables.add_argument("--verifier", action="store_true",
                                     help="Faire trancher chaque candidat par Lean "
                                          "(lake env lean, borné par --timeout)")
    chemins_verifiables.add_argument("--timeout", type=int, default=600,
                                     help="Timeout Lean par candidat en secondes "
                                          "(défaut : 600)")
    chemins_verifiables.add_argument("--garder", action="store_true",
                                     help="Conserver les fichiers de vérification générés "
                                          "dans le dossier (défaut : supprimés après verdict)")
    chemins_verifiables.add_argument("--format", choices=["console", "json"], default="console",
                                     help="Format de sortie (console lisible, ou json pour les agents/jq)")
    chemins_verifiables.add_argument("--multi", action="store_true",
                                     help="Chemins multi-lemmes : chaînage avant guidé, borné par "
                                          "l'inégalité de budget C(P) ≤ B(S) (fragment D, "
                                          "INDÉCIDÉ a priori sans build Lean si aucun chemin "
                                          "admissible)")
    chemins_verifiables.add_argument("--valider-solidite", type=int, default=0,
                                     metavar="K",
                                     help="Validation de solidité du classifieur : "
                                          "soumet à Lean K directions CANDIDAT_IMPOSSIBLE par "
                                          "candidat (échantillon stratifié par trou, test minimal "
                                          "`example : T := t`). Attendu : RÉFUTÉ partout ; "
                                          "PROUVÉ = violation de solidité → durcissement. "
                                          "Tout CONFIRMÉ est inscrit au registre des "
                                          "IMPOSSIBLE_VALIDÉ (élagage réel). "
                                          "(défaut : 0, désactivé)")
    chemins_verifiables.add_argument("--encercler", type=int, default=0,
                                     metavar="K",
                                     help="Encerclement des directions INCONNU : "
                                          "sonde chaque direction sous au plus K angles "
                                          "Lean (câblage direct, dépliage profond, "
                                          "coercition explicite, sous-termes) sans jamais "
                                          "la fermer au premier contact. Statuts typés du "
                                          "dossier : ENCERCLE "
                                          "(Lean a tranché → registre), RESISTANT "
                                          "(mécanisme sensible → escaladé), "
                                          "ATTEIGNABLE_TROUVE (câblage prouvé → escaladé). "
                                          "Borné par --max-encercles directions par "
                                          "candidat (défaut : 0, désactivé)")
    chemins_verifiables.add_argument("--max-encercles", type=int, default=4,
                                     metavar="M",
                                     help="Borne anti-explosion : au plus M directions "
                                          "INCONNU encerclées par candidat (échantillon "
                                          "stratifié par trou, défaut : 4)")

    ou_aller = subparsers.add_parser("ou-aller",
                                     help="Où aller : liste d'attention ordonnée "
                                          "par coût de booléanisation décroissant "
                                          "(pas un score, pas une P(A) — "
                                          "le veto de Tomy tranche)")
    ou_aller.add_argument("--registre", default=None,
                          help="Chemin du registre vivant des hypothèses "
                               "(défaut : REGISTRE_HYPOTHESES_20260929.md du dépôt lean)")
    ou_aller.add_argument("--dossier", default=None,
                          help="Dossier pour les postérieurs des croyances "
                               "(défaut : ~/workspace/lean-navier-stokes)")
    ou_aller.add_argument("--format", choices=["console", "json"], default="console",
                          help="Format de sortie (console lisible, ou json pour les agents/jq)")

    entropie = subparsers.add_parser("entropie",
                                     help="Lentille entropique sur la Sonde B : "
                                          "le grand livre chiffré du rétrécissement "
                                          "(H et ΔH en bits, bornes EFT)")
    entropie.add_argument("mecanisme",
                          help="Mécanisme à chiffrer : sorry du Master "
                               "(ex. energy_identity), hypothèse nommée "
                               "ou chantier (ex. 138)")
    entropie.add_argument("--registre", default=None,
                          help="Chemin du registre vivant des hypothèses "
                               "(défaut : REGISTRE_HYPOTHESES_20260929.md du dépôt lean)")
    entropie.add_argument("--dossier", default=None,
                          help="Dossier pour les postérieurs des croyances "
                               "(défaut : ~/workspace/lean-navier-stokes)")
    entropie.add_argument("--format", choices=["console", "json"], default="console",
                          help="Format de sortie (console lisible, ou json pour les agents/jq)")

    explorer = subparsers.add_parser("explorer",
                                     help="Explorateur interactif : typographie des symboles "
                                          "et analyse (sondes, oracle, entropie) en une seule "
                                          "surface — HTML autonome, zéro réseau")
    explorer.add_argument("mecanisme",
                          help="Mécanisme à explorer : sorry du Master "
                               "(ex. energy_identity), hypothèse nommée "
                               "(ex. H30) ou chantier (ex. 139)")
    explorer.add_argument("--registre", default=None,
                          help="Chemin du registre vivant des hypothèses "
                               "(défaut : REGISTRE_HYPOTHESES_20260929.md du dépôt lean)")
    explorer.add_argument("--dossier", default=None,
                          help="Dossier pour les postérieurs des croyances et les traces "
                               "d'oracle (défaut : ~/workspace/lean-navier-stokes ; "
                               "ex. ~/workspace/quasicristal pour le programme quasicristaux)")
    explorer.add_argument("--sortie", default=None,
                          help="Fichier HTML de sortie "
                               "(défaut : explorateur_<mecanisme>.html dans le répertoire courant)")
    explorer.add_argument("--sans-oracle", action="store_true",
                          help="Ne pas indexer les traces d'oracle (génération plus rapide)")

    sentinelle_cmd = subparsers.add_parser("sentinelle",
                                           help="Survie des missions au "
                                                "reboot : état structuré, "
                                                "pulsation, orphelines, "
                                                "brief de reprise")
    sentinelle_cmd.add_argument("action",
                                choices=["init", "checkpoint", "pulse",
                                         "check", "reprise", "dormants",
                                         "enchaine"],
                                help="init : créer l'état d'une mission ; "
                                     "checkpoint : noter le statut d'un item "
                                     "(vocabulaire contrôlé) ; pulse : "
                                     "battement de cœur horodaté ; check : "
                                     "montrer les missions présumées "
                                     "orphelines ; reprise : générer le brief "
                                     "de reprise (ne relance rien) ; "
                                     "dormants : détecter les agents/items "
                                     "présumés dormants et générer leur brief "
                                     "de réveil (ne réveille rien) ; "
                                     "enchaine : faire avancer une mission "
                                     "figée vers sa suite naturelle "
                                     "(vérifie qu'elle est vraiment morte, "
                                     "chaîne les items, reprend la pulsation)")
    sentinelle_cmd.add_argument("--terrain", default=".",
                                help="Terrain de la mission (init/checkpoint/"
                                     "pulse/reprise ; défaut : .)")
    sentinelle_cmd.add_argument("--nom", default=None,
                                help="Nom de la mission (init)")
    sentinelle_cmd.add_argument("--item", default=None,
                                help="Identifiant d'item (checkpoint)")
    sentinelle_cmd.add_argument("--statut", default=None,
                                help="Statut typé (checkpoint) : EN-COURS, "
                                     "TERMINE, INTERROMPU, A-REPRENDRE, BLOQUE")
    sentinelle_cmd.add_argument("--note", default="",
                                help="Note libre (checkpoint)")
    sentinelle_cmd.add_argument("--agent", default="",
                                help="Identifiant de l'agent (pulse)")
    sentinelle_cmd.add_argument("--missions", default=".",
                                help="Dossier racine des missions (check ; "
                                     "défaut : .)")
    sentinelle_cmd.add_argument("--seuil", type=int, default=1800,
                                help="Seuil d'orpheline en secondes (check ; "
                                     "défaut : 1800)")
    sentinelle_cmd.set_defaults(commande="sentinelle")

    markov_cmd = subparsers.add_parser("markov",
                                       help="Chaînes de Markov : modéliser la "
                                            "chaîne des goulots d'un pipeline "
                                            "(construire, diagnostic, simuler)")
    markov_cmd.add_argument("action",
                            choices=["construire", "diagnostic", "simuler",
                                     "exemple"],
                            help="construire : bâtir la chaîne depuis un "
                                 "fichier d'observations JSON ; diagnostic : "
                                 "matrice, stationnaire, goulot, absorption ; "
                                 "simuler : sensibilité au débit d'un étage ; "
                                 "exemple : jeu d'observations AST réel")
    markov_cmd.add_argument("--observations", default=None,
                            help="Fichier JSON d'observations "
                                 "(construire ; liste de "
                                 "{etape, duree_s, octets, succes})")
    markov_cmd.add_argument("--ordre", default=None,
                            help="Ordre canonique des étapes, séparé par des "
                                 "virgules (construire ; défaut : inféré)")
    markov_cmd.add_argument("--chaine", default=None,
                            help="Fichier JSON de chaîne "
                                 "(diagnostic, simuler)")
    markov_cmd.add_argument("--sortie", default=None,
                            help="Fichier de sortie pour la chaîne construite "
                                 "(construire)")
    markov_cmd.add_argument("--etape", default=None,
                            help="Étage à accélérer (simuler)")
    markov_cmd.add_argument("--facteur", type=float, default=2.0,
                            help="Multiplicateur de débit (simuler ; "
                                 "défaut : 2.0)")
    markov_cmd.set_defaults(commande="markov")

    write_cmd = subparsers.add_parser("write",
                                      help="phiwrite : générer du code Lean 4 "
                                           "depuis une spec JSON structurée")
    write_cmd.add_argument("--spec", default=None,
                           help="Fichier JSON de spécification "
                                "({theorems: [{name, imports, statement, "
                                "proof, doc}]}) ; 'exemple' pour la spec "
                                "d'exemple intégrée")
    write_cmd.add_argument("--sortie", default=None,
                           help="Fichier .lean à écrire")
    write_cmd.add_argument("--allow-sorry", action="store_true",
                           default=False,
                           help="Autoriser sorry/admit (refusé par défaut, "
                                "doctrine : pas de trou déguisé)")
    write_cmd.set_defaults(commande="write")

    return parser


# ────────────────────────────────────────────────────────
# COLLECTE DES FICHIERS (Suture des boucles LILITH)
# ────────────────────────────────────────────────────────

def _fichiers_depuis_dossier(dossier: str) -> list:
    """Collecte récursivement les fichiers supportés d'un dossier (boucles isolées)."""
    fichiers = []
    for racine, _, noms in os.walk(dossier):
        fichiers.extend(
            os.path.join(racine, nom)
            for nom in noms
            if est_fichier_supporte(nom)
        )
    return sorted(fichiers)


def _collecter_fichiers(cible: str) -> list:
    """Retourne la liste des fichiers sources à auditer depuis un chemin."""
    if os.path.isfile(cible):
        return [cible] if est_fichier_supporte(cible) else []
    if os.path.isdir(cible):
        return _fichiers_depuis_dossier(cible)
    return []


# ────────────────────────────────────────────────────────
# EXÉCUTION DES SOUS-COMMANDES (une fonction par rôle)
# ────────────────────────────────────────────────────────

def _executer_check(args: argparse.Namespace, fichiers: list) -> int:
    """Exécute la sous-commande 'check'. Retourne le code de sortie."""
    # Si la cible est un dossier avec plus d'un fichier et que le format est console, afficher d'abord la matrice
    if os.path.isdir(args.cible) and len(fichiers) > 1 and args.format == "console":
        from . import rapport_matrice_console
        print(rapport_matrice_console(args.cible))
        print()

    exit_code = 0
    for fichier in fichiers:
        exit_code = max(exit_code, _auditer_un_fichier(fichier, args))
    return exit_code


def _auditer_un_fichier(fichier: str, args: argparse.Namespace) -> int:
    """Audite un seul fichier et affiche le résultat. Retourne 0 ou 1."""
    lang = getattr(args, "lang", None)
    try:
        if args.format == "json":
            print(rapport_json(fichier, lang=lang))
        elif args.format == "sarif":
            print(rapport_sarif(fichier, lang=lang))
        else:
            print(rapport_console(fichier, lang=lang))
            print()

        seuil = getattr(args, "min_radiance", 0) or 0
        formule = getattr(args, "formule", None)
        gate = getattr(args, "gate", None)
        if seuil > 0 or formule or gate:
            metriques = auditer(fichier, lang=lang)
            if seuil > 0 and metriques["radiance"] < seuil:
                return 1
            if formule or gate:
                from .formules import construire_env, evaluer_formule, evaluer_gate
                env = construire_env(metriques)
                if formule:
                    try:
                        valeur = evaluer_formule(formule, env)
                    except Exception as e:
                        print(f"  ⚠ Formule invalide : {e}")
                        return 1
                    print(f"  ◈ Formule : {formule} = {valeur}")
                if gate:
                    try:
                        ouverte = evaluer_gate(gate, env)
                    except Exception as e:
                        print(f"  ⚠ Porte invalide : {e}")
                        return 1
                    symbole = "✓" if ouverte else "✗"
                    etat = "OUVERTE" if ouverte else "FERMEE"
                    print(f"  ◈ Porte : {gate} -> {etat} {symbole}")
                    if not ouverte:
                        return 1
    except SyntaxError as e:
        print(f"⚠ Erreur de syntaxe dans {fichier}: {e}")
        return 1
    except (ImportError, ValueError) as e:
        print(f"⚠ {e}")
        return 1
    except Exception as e:
        print(f"⚠ Erreur lors de l'analyse de {fichier}: {e}")
        return 1
    return 0


def _executer_report(args: argparse.Namespace, fichiers: list) -> int:
    """Exécute la sous-commande 'report'. Retourne le code de sortie."""
    lang = getattr(args, "lang", None)
    for fichier in fichiers:
        sortie = _nom_rapport(fichier, args.output)
        try:
            rapport_markdown(fichier, sortie=sortie, lang=lang)
            print(f"✦ Rapport sauvegardé : {sortie}")
        except Exception as e:
            print(f"❌ Erreur : {e}")
            return 1
    return 0


def _nom_rapport(fichier: str, sortie_demandee: str) -> str:
    """Calcule le nom du fichier rapport de sortie."""
    if sortie_demandee:
        return sortie_demandee
    base = os.path.splitext(os.path.basename(fichier))[0]
    return f"RAPPORT_PHI_{base}.md"


def _executer_infer(args: argparse.Namespace, fichiers: list) -> int:
    """Exécute la sous-commande 'infer' (moteur bayésien)."""
    from . import diagnostic_bayesien
    import json
    lang = getattr(args, "lang", None)
    exact = True if getattr(args, "exact", False) else None
    for fichier in fichiers:
        try:
            diag = diagnostic_bayesien(fichier, lang=lang, exact=exact)
            if args.format == "json":
                out = {
                    "fichier": fichier,
                    "hypothese_dominante": diag.hypothese_dominante,
                    "probabilite_dominante": diag.probabilite_dominante,
                    "gain_espere_radiance": diag.gain_espere_radiance,
                    "action_recommandee": diag.action_recommandee,
                    "posteriors": diag.posteriors,
                    "auto_patch_suggestion": diag.auto_patch_suggestion,
                }
                if exact:
                    # Clés EFT : uniquement en mode exact (JSON flottant inchangé).
                    out["mode_arithmetique"] = diag.mode
                    out["certifie"] = {h: c.vers_dict()
                                       for h, c in diag.certifie.items()}
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print("╔════════════════════════════════════════════════════════════════════╗")
                print("║      PHI-COMPLEXITY — INFÉRENCE BAYÉSIENNE & AUTO-SUTURE           ║")
                print("╚════════════════════════════════════════════════════════════════════╝")
                print(f"  📄 Fichier               : {fichier}")
                print(f"  🎯 Hypothèse Dominante   : {diag.hypothese_dominante} (P = {diag.probabilite_dominante * 100:.1f}%)")
                print(f"  📈 Gain Espéré Radiance  : +{diag.gain_espere_radiance:.1f} pts")
                print(f"  🛠 Action Recommandée    : {diag.action_recommandee}")
                print("\n  DISTRIBUTION A POSTERIORI P(H | Evidence) :")
                for h, p in diag.posteriors.items():
                    barre = "█" * int(p * 20) + "░" * (20 - int(p * 20))
                    print(f"    {h:<18} : {barre} {p * 100:>5.1f}%")
                if diag.auto_patch_suggestion:
                    print("\n  🔮 PROPOSITION D'AUTO-SUTURE (SUGGESTION CHIRURGICALE) :")
                    for ligne in diag.auto_patch_suggestion.splitlines():
                        print(f"    {ligne}")
                if diag.mode == "eft" and diag.certifie:
                    print("\n  BORNES D'ERREUR CERTIFIÉES (EFT double-double) :")
                    for h, c in diag.certifie.items():
                        print(f"    {h:<18} : ±{c.borne_erreur:.3e}  "
                              f"({c.nb_operations} ops, |hi|_max={c.magnitude_max:.4f})")
                print("  ────────────────────────────────────────────────────────────────────\n")
        except Exception as e:
            print(f"❌ Erreur lors de l'inférence sur {fichier}: {e}")
            return 1
    return 0


def _executer_edit(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'edit' : ouvre l'éditeur phi (TUI curses)."""
    from .editeur import lancer_editeur
    try:
        lancer_editeur(args.cible, projet=getattr(args, "projet", None),
                       lang=getattr(args, "lang", None))
    except Exception as e:
        print(f"❌ Erreur de l'éditeur : {e}")
        return 1
    return 0

def _executer_index(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'index' : carte du projet (console ou json)."""
    import json
    from . import carte_projet
    from .carte import carte_console
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    from .editeur.indexeur import EXCLUSIONS_DEFAUT
    from .langs import treesitter_disponible
    if getattr(args, "no_exclude", False):
        exclusions = []
    else:
        exclusions = list(EXCLUSIONS_DEFAUT)
        supplement = getattr(args, "exclude", None)
        if supplement:
            exclusions += [e.strip() for e in supplement.split(",") if e.strip()]
    if not treesitter_disponible():
        # stderr : ne jamais polluer le JSON sur stdout (agents, jq).
        print("\u26a0 'tree-sitter-language-pack' non installé : "
              "seuls les langages natifs (python) seront analysés. "
              "Voir la section AVERTISSEMENTS de la carte ; "
              "installation : pip install phi-complexity[multilang].",
              file=sys.stderr)
    try:
        carte = carte_projet(dossier, lang=getattr(args, "lang", None),
                             exclusions=exclusions,
                             complet=not getattr(args, "rapide", False))
    except Exception as e:
        print(f"❌ Erreur lors de la cartographie : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(carte, ensure_ascii=False, indent=2))
    else:
        print(carte_console(carte))
    return 0


def _exclusions_depuis_args(args) -> list:
    """Exclusions effectives depuis les flags --exclude/--no-exclude."""
    from .editeur.indexeur import EXCLUSIONS_DEFAUT
    if getattr(args, "no_exclude", False):
        return []
    exclusions = list(EXCLUSIONS_DEFAUT)
    supplement = getattr(args, "exclude", None)
    if supplement:
        exclusions += [e.strip() for e in supplement.split(",") if e.strip()]
    return exclusions


def _executer_snapshot(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'snapshot' : fige l'état, écrit l'enveloppe."""
    import json
    from datetime import datetime
    from .veille import ecrire_snapshot, _causes_arriere_plan
    from .langs.registry import EXTENSIONS_SUPPORTEES
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    # Durcissement 2026-10-01 : jamais de baseline sur un instrument
    # dégradé (le 2026-09-30 a failli figer 33 symboles au lieu de 3566).
    # La doctrine « pas de baseline sur signal dégradé » est désormais
    # mécanique, pas disciplinaire.
    causes = _causes_arriere_plan(
        dossier, _exclusions_depuis_args(args), set(EXTENSIONS_SUPPORTEES))
    if causes and not getattr(args, "force", False):
        print("❌ Baseline REFUSÉE : l'instrument est dégradé —")
        for c in causes:
            print(f"   🔧 {c}")
        print("   Réparez l'arrière-plan puis relancez "
              "(ou --force en connaissance de cause).")
        return 3
    sortie = getattr(args, "out", None)
    if not sortie:
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        sortie = f"phi_snapshot_{horodatage}.json"
    try:
        enveloppe = ecrire_snapshot(dossier, sortie,
                                    exclusions=_exclusions_depuis_args(args))
    except Exception as e:
        print(f"❌ Erreur lors du snapshot : {e}")
        return 1
    print(f"📸 Snapshot écrit : {sortie}")
    print(f"   dossier : {enveloppe['dossier']}")
    print(f"   date    : {enveloppe['date']}")
    print(f"   symboles: {enveloppe['carte']['nb_symboles']} "
          f"({enveloppe['carte']['nb_fichiers']} fichiers)")
    print(f"   md5     : {enveloppe['md5_carte']}")
    return 0


def _executer_veille(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'veille' : diff contre la référence."""
    import json
    from .veille import charger_reference, comparer, veille_console
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    try:
        ref = charger_reference(args.ref)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    try:
        diff = comparer(ref, dossier,
                        exclusions=_exclusions_depuis_args(args))
    except Exception as e:
        print(f"❌ Erreur lors de la veille : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(diff, ensure_ascii=False, indent=2))
    else:
        print(veille_console(diff))
    # Durcissement 2026-10-01 : un instrument dégradé échoue bruyamment
    # (exit 3, distinct de 2 = dégradation détectée). Jamais de ✅ STABLE
    # sur un arrière-plan perdu.
    if diff.get("instrument_degrade"):
        print("❌ INSTRUMENT DÉGRADÉ — la veille refuse de rendre un verdict "
              "sur un arrière-plan perdu.", file=sys.stderr)
        return 3
    return 0 if diff["verdict"] == "STABLE" else 2


def _executer_chemins(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'chemins' : carte croyante (console ou json)."""
    import json
    from . import chemins_croyants
    from .croyances import chemins_console
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    from .editeur.indexeur import EXCLUSIONS_DEFAUT
    if getattr(args, "no_exclude", False):
        exclusions = []
    else:
        exclusions = list(EXCLUSIONS_DEFAUT)
        supplement = getattr(args, "exclude", None)
        if supplement:
            exclusions += [e.strip() for e in supplement.split(",") if e.strip()]
    try:
        carte = chemins_croyants(dossier, lang=getattr(args, "lang", None),
                                 exclusions=exclusions,
                                 top=getattr(args, "top", 10),
                                 exact=(True if getattr(args, "exact", False)
                                        else None))
    except Exception as e:
        print(f"❌ Erreur lors du calcul des chemins : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(carte, ensure_ascii=False, indent=2))
    else:
        print(chemins_console(carte))
    # Durcissement 2026-10-01 (audit) : `chemins` rendait une carte
    # partielle SANS prévenir quand l'arrière-plan manquait — la même
    # classe de silence que l'incident 3531 → 33 symboles du 2026-09-30
    # (trouvé parce que `index` prévenait mais pas `chemins`).
    # On surface l'avertissement comme `carte` le fait ; pas de
    # changement de code de sortie (commande informative : le contrat
    # exit-3 reste porté par `snapshot` et `veille`).
    # En mode JSON, l'avertissement part sur stderr pour ne pas
    # corrompre le document.
    from .editeur.indexeur import fichiers_non_supportes
    non_supportes = fichiers_non_supportes(
        dossier, exclusions, langage=getattr(args, "lang", None))
    if non_supportes:
        canal = sys.stderr if getattr(args, "format", "console") == "json" \
            else sys.stdout
        print("⚠️  AVERTISSEMENT — fichiers SANS analyseur fonctionnel :",
              file=canal)
        for ns in non_supportes[:10]:
            print(f"     • {ns['fichier']} ({ns['extension']}) : "
                  f"{ns['raison']}", file=canal)
        if len(non_supportes) > 10:
            print(f"     … et {len(non_supportes) - 10} autres", file=canal)
    return 0


def _executer_oracle(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'oracle' : traces de raisonnement exhibées.

    Cible fichier → moteur bayésien ; cible dossier → chemins croyants.
    Les traces existent dans les deux modes arithmétiques ; `--exact`
    ajoute la borne d'erreur certifiée (EFT) à chaque trace.
    """
    import json
    from .oracle import TraceurOracle, rendre_oracle_console
    cible = args.cible
    exact = True if getattr(args, "exact", False) else None
    traceur = TraceurOracle()
    try:
        if os.path.isfile(cible):
            from . import diagnostic_bayesien
            diagnostic_bayesien(cible,
                                lang=getattr(args, "lang", None),
                                exact=exact, traceur=traceur)
        elif os.path.isdir(cible):
            from . import chemins_croyants
            from .editeur.indexeur import EXCLUSIONS_DEFAUT
            chemins_croyants(cible, lang=getattr(args, "lang", None),
                             exclusions=list(EXCLUSIONS_DEFAUT),
                             top=getattr(args, "top", 10),
                             exact=exact, traceur=traceur)
        else:
            print(f"❌ Cible introuvable (ni fichier ni dossier) : {cible}")
            return 1
    except Exception as e:
        print(f"❌ Erreur lors de l'interrogation de l'oracle : {e}")
        return 1
    traces = traceur.entrees
    symbole = getattr(args, "symbole", None)
    if symbole:
        traces = traceur.filtrer(symbole)
        if not traces:
            print(f"⚠️ Aucune trace pour le symbole : {symbole}")
            return 1
    if getattr(args, "format", "console") == "json":
        enveloppe = traceur.vers_json()
        enveloppe["traces"] = [t.vers_dict() for t in traces]
        enveloppe["nb_traces"] = len(traces)
        if symbole:
            enveloppe["filtre_symbole"] = symbole
        print(json.dumps(enveloppe, ensure_ascii=False, indent=2))
    else:
        print(rendre_oracle_console(traces))
    return 0


def _executer_sonde(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'sonde' : les deux pôles A/B d'un mécanisme.

    Ne touche à aucun JSON existant : sous-commande neuve, lecture seule
    du registre. `--exact` / PHI_EFT=1 = vérification renforcée du parsing.
    """
    from .sondes import (
        RegistreSondes, sonder, rendre_sonde_console, REGISTRE_DEFAUT,
    )
    import json
    try:
        from .eft import eft_active
        exact = True if (getattr(args, "exact", False) or eft_active()) else False
    except Exception:
        exact = True if getattr(args, "exact", False) else False
    chemin = getattr(args, "registre", None) or REGISTRE_DEFAUT
    if not os.path.isfile(chemin):
        print(f"❌ Registre introuvable : {chemin}")
        print("   (passez --registre CHEMIN vers le registre vivant des hypothèses)")
        return 1
    try:
        registre = RegistreSondes().charger(chemin)
        resultat = sonder(args.mecanisme, registre, exact=exact)
    except Exception as e:
        print(f"❌ Erreur lors du sondage : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        # Ancrage entropique (import direct d'entropie.py via ancrage.py —
        # jamais de subprocess) : section `entropie` par nœud + mécanisme.
        # Mode console inchangé (rapide, sans lentille).
        try:
            from .ancrage import attacher_entropie
            from .entropie import posterieurs_symboles, DOSSIER_DEFAUT
            dossier = getattr(args, "dossier", None) or DOSSIER_DEFAUT
            if not os.path.isdir(dossier):
                print(f"❌ Dossier introuvable pour les croyances : {dossier}")
                return 1
            attacher_entropie(resultat, registre, posterieurs_symboles(dossier))
        except Exception as e:
            print(f"❌ Erreur lors de l'ancrage entropique : {e}")
            return 1
        print(json.dumps(resultat.vers_dict(), ensure_ascii=False, indent=2))
    else:
        print(rendre_sonde_console(resultat))
    return 0


def _executer_ou_aller(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'ou-aller' : liste d'attention ordonnée.

    Lecture seule : ne touche à aucun JSON existant (registre, croyances,
    veille). Le tri par coût de booléanisation décroissant n'est PAS un
    score et PAS une P(A) : il ordonne où le typage porte le plus de bits.
    L'instrument montre où regarder ; le veto de Tomy tranche.
    """
    from .ancrage import construire_ou_aller, rendre_ou_aller_console
    from .entropie import posterieurs_symboles, DOSSIER_DEFAUT
    from .sondes import RegistreSondes, REGISTRE_DEFAUT
    import json
    chemin = getattr(args, "registre", None) or REGISTRE_DEFAUT
    if not os.path.isfile(chemin):
        print(f"❌ Registre introuvable : {chemin}")
        print("   (passez --registre CHEMIN vers le registre vivant des hypothèses)")
        return 1
    dossier = getattr(args, "dossier", None) or DOSSIER_DEFAUT
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable pour les croyances : {dossier}")
        return 1
    try:
        registre = RegistreSondes().charger(chemin)
        posterieurs = posterieurs_symboles(dossier)
        data = construire_ou_aller(registre, posterieurs)
    except Exception as e:
        print(f"❌ Erreur lors de « ou-aller » : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(rendre_ou_aller_console(data))
    return 0


def _executer_explorer(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'explorer' : HTML autonome interactif.

    Lecture seule : ne touche ni au registre, ni aux croyances, ni à la
    veille. Import direct (explorateur.py), jamais de subprocess.
    """
    from .explorateur import explorer_vers_fichier
    from .entropie import DOSSIER_DEFAUT
    from .sondes import REGISTRE_DEFAUT
    chemin = getattr(args, "registre", None) or REGISTRE_DEFAUT
    if not os.path.isfile(chemin):
        print(f"❌ Registre introuvable : {chemin}")
        print("   (passez --registre CHEMIN vers le registre vivant des hypothèses)")
        return 1
    dossier = getattr(args, "dossier", None) or DOSSIER_DEFAUT
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable pour les croyances : {dossier}")
        return 1
    try:
        from .sondes import RegistreSondes
        registre = RegistreSondes().charger(chemin)
        sortie = explorer_vers_fichier(
            args.mecanisme,
            dossier=dossier,
            sortie=getattr(args, "sortie", None),
            registre=registre,
            avec_oracle=not getattr(args, "sans_oracle", False),
        )
    except ValueError as e:
        # Erreur propre (ex. mécanisme inconnu) : message, pas de traceback.
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ Erreur lors de « explorer » : {e}")
        return 1
    print(f"✅ Explorateur généré : {sortie}")
    print("   (HTML autonome — ouvrez-le dans un navigateur, aucune connexion requise)")
    return 0


def _executer_piste_sorry(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'piste-sorry'.

    Lecture seule. Le registre est opt-in : sans lui (ou chemin invalide),
    la piste structurelle (inventaire + imports) est produite seule —
    jamais d'échec sur une machine sans la chaîne Lean.
    """
    import json
    from .piste_sorry import piste, rendre_console
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    try:
        from .sondes import REGISTRE_DEFAUT
    except Exception:
        REGISTRE_DEFAUT = None
    chemin_registre = getattr(args, "registre", None) or REGISTRE_DEFAUT
    try:
        resultat = piste(args.sorry, dossier, chemin_registre=chemin_registre)
    except Exception as e:
        print(f"❌ Erreur lors du pistage : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(resultat, indent=2, ensure_ascii=False))
    else:
        print(rendre_console(resultat))
    return 0


def _executer_chemins_verifiables(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'chemins-verifiables'.

    Lecture seule sur le dossier (sauf --verifier, qui écrit des fichiers
    temporaires de vérification puis les supprime sauf --garder).
    Codes : 0 = candidats émis ; 2 = sorry introuvable ; 1 = erreur.
    """
    import json
    from .chemins_verifiables import (
        _defs_corps,
        analyser_entete,
        candidats_cablage,
        empreinte_enonce,
        encercler_direction,
        ErreurRegistre,
        extraire_entete,
        fichier_validation_solidite,
        fichier_verification,
        inscrire_impossible_valide,
        interpreter_solidite,
        lire_dossiers_encerclement,
        realiser_chemins,
        rendre_chemins,
        rendre_console,
        verdict_lean,
        _cle_registre,
    )
    from .piste_sorry import decaper_lean
    dossier = args.dossier
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        return 1
    try:
        from .sondes import REGISTRE_DEFAUT
    except Exception:
        REGISTRE_DEFAUT = None
    chemin_registre = getattr(args, "registre", None) or REGISTRE_DEFAUT
    if getattr(args, "multi", False):
        try:
            res = realiser_chemins(
                args.sorry, dossier, chemin_registre=chemin_registre,
                max_candidats=getattr(args, "max_candidats", 12),
                verifier=getattr(args, "verifier", False),
                timeout_s=getattr(args, "timeout", 600),
                garder=getattr(args, "garder", False))
        except Exception as e:
            print(f"❌ Erreur lors de la recherche de chemins : {e}")
            return 1
        if res["statut"] == "INTROUVABLE":
            if getattr(args, "format", "console") == "json":
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(rendre_chemins(res))
            return 2
        if getattr(args, "format", "console") == "json":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(rendre_chemins(res))
        return 0
    try:
        res = candidats_cablage(
            args.sorry, dossier, chemin_registre=chemin_registre,
            max_candidats=getattr(args, "max_candidats", 12))
    except Exception as e:
        print(f"❌ Erreur lors de la recherche de candidats : {e}")
        return 1
    if res["statut"] == "INTROUVABLE":
        if getattr(args, "format", "console") == "json":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(rendre_console(res))
        return 2
    if getattr(args, "verifier", False):
        timeout = getattr(args, "timeout", 600)
        garder = getattr(args, "garder", False)
        groupes, noms, conclusion = analyser_entete(
            res["enonce_sorry"], args.sorry)
        for i, c in enumerate(res["candidats"]):
            contenu = fichier_verification(
                args.sorry, c, res["module_sorry"], groupes, conclusion,
                opens_sorry=res.get("opens_sorry"))
            chemin_v = os.path.join(
                dossier, f"Verification_{args.sorry}_{i}.lean")
            try:
                with open(chemin_v, "w", encoding="utf-8") as fh:
                    fh.write(contenu)
                v = verdict_lean(chemin_v, dossier, timeout_s=timeout)
            finally:
                if not garder:
                    try:
                        os.remove(chemin_v)
                    except OSError:
                        pass
            c["verdict"] = v["verdict"]
            c["diagnostic"] = v["diagnostic"]
            if garder:
                c["fichier_verification"] = chemin_v
    n_solidite = getattr(args, "valider_solidite", 0) or 0
    if n_solidite > 0:
        # Validation de solidité : chaque direction CANDIDAT_IMPOSSIBLE est
        # soumise à Lean via un test minimal. Protocole falsifiable
        # (docs/DISCIPLINE_ELAGAGE_REEL.md) : PROUVÉ = violation.
        # Boucle de feedback : tout CONFIRMÉ (RÉFUTÉ par Lean) est inscrit
        # au registre des IMPOSSIBLE_VALIDÉ → élagage réel aux prochains runs.
        timeout = getattr(args, "timeout", 600)
        garder = getattr(args, "garder", False)
        bilan = {"confirmes": 0, "violations": [], "inconclusifs": [],
                 "inscrits_registre": 0, "erreurs_registre": []}
        # Empreinte de l'énoncé du sorry : les validations Lean sont
        # liées à CET énoncé — si le corpus évolue, elles ne sont plus
        # réutilisées silencieusement.
        empreinte = empreinte_enonce(res["enonce_sorry"])
        for i, c in enumerate(res["candidats"]):
            # échantillon stratifié : tourniquet sur les trous pour couvrir
            # des décisions différentes du classifieur, pas N fois le même.
            par_trou = {}
            for p in c.get("impossibles", []):
                par_trou.setdefault(p["trou"], []).append(p)
            trous = list(par_trou)
            echantillon, k = [], 0
            while len(echantillon) < n_solidite and trous:
                t = trous[k % len(trous)]
                if par_trou[t]:
                    echantillon.append(par_trou[t].pop(0))
                elif all(not par_trou[u] for u in trous):
                    break
                k += 1
            for j, p in enumerate(echantillon):
                # B8/B9/B10/B11 : le test lie l'union des télescopes
                # (candidat + sorry) et importe le module du candidat —
                # le type du trou vit dans le contexte du candidat, le
                # pool de termes inclut les lieurs du sorry.
                contenu = fichier_validation_solidite(
                    args.sorry, res["module_sorry"], c,
                    res.get("contexte_sorry") or {},
                    p["type_trou"], p["terme"],
                    opens_sorry=res.get("opens_sorry"), raison=p["raison"])
                chemin_v = os.path.join(
                    dossier, f"Solidite_{args.sorry}_{i}_{j}.lean")
                try:
                    with open(chemin_v, "w", encoding="utf-8") as fh:
                        fh.write(contenu)
                    v = verdict_lean(chemin_v, dossier, timeout_s=timeout)
                finally:
                    if not garder:
                        try:
                            os.remove(chemin_v)
                        except OSError:
                            pass
                statut = interpreter_solidite(v)
                fiche = {"candidat": c["declaration"], "trou": p["trou"],
                         "terme": p["terme"], "type_trou": p["type_trou"],
                         "raison": p["raison"], "verdict": v["verdict"],
                         "statut": statut}
                if garder:
                    fiche["fichier"] = chemin_v
                if statut == "CONFIRMÉ":
                    bilan["confirmes"] += 1
                    # Élagage réel : Lean a RÉFUTÉ → IMPOSSIBLE_VALIDÉ.
                    # Échec d'inscription VISIBLE (jamais `except: pass`) :
                    # le bilan et stderr le signalent, le run continue.
                    try:
                        inscrire_impossible_valide(
                            args.sorry, c["declaration"], p["trou"],
                            p["type_trou"], p["terme"], p["raison"],
                            chemin_v if garder else
                            f"Solidite_{args.sorry}_{i}_{j}.lean",
                            empreinte)
                        bilan["inscrits_registre"] += 1
                        fiche["registre"] = "IMPOSSIBLE_VALIDÉ"
                    except ErreurRegistre as e:
                        bilan["erreurs_registre"].append(str(e))
                        fiche["registre"] = f"ÉCHEC_INSCRIPTION : {e}"
                        print(f"⚠️  {e}", file=sys.stderr)
                    except Exception as e:  # inattendu : visible, run sauvé
                        bilan["erreurs_registre"].append(f"inattendu : {e!r}")
                        fiche["registre"] = \
                            f"ÉCHEC_INSCRIPTION (inattendu) : {e!r}"
                        print(f"⚠️  inscription impossible (inattendu) : {e!r}",
                              file=sys.stderr)
                elif statut == "VIOLATION":
                    fiche["diagnostic"] = v["diagnostic"]
                    bilan["violations"].append(fiche)
                else:
                    fiche["diagnostic"] = v["diagnostic"]
                    bilan["inconclusifs"].append(fiche)
        res["validation_solidite"] = bilan
    n_encercler = getattr(args, "encercler", 0) or 0
    if n_encercler > 0:
        # Encerclement : chaque direction INCONNU est sondée sous
        # plusieurs angles Lean, jamais fermée au premier contact
        # (docs/ENCERCLEMENT.md). Budgets : K angles max par direction,
        # M directions max par candidat (R4 : pas d'explosion
        # combinatoire non guidée). Seul Lean promeut (ENCERCLE →
        # registre) ; RESISTANT et ATTEIGNABLE_TROUVE sont escaladés.
        timeout = getattr(args, "timeout", 600)
        garder = getattr(args, "garder", False)
        max_enc = getattr(args, "max_encercles", 4) or 0
        defs = _defs_corps(dossier)
        empreinte = empreinte_enonce(res["enonce_sorry"])
        bilan_enc = {"encercles": 0, "resistants": [], "atteignables": [],
                     "sans_angle": 0, "inscrits_registre": 0,
                     "erreurs": []}
        for i, c in enumerate(res["candidats"]):
            # échantillon stratifié : tourniquet sur les trous (même
            # discipline que --valider-solidite). Durcissement
            # (2026-10-01) : on exclut les directions déjà encerclées
            # (dossier existant, quel que soit le statut) pour ne pas
            # re-sonder les RÉSISTANT à chaque vague — le dossier fait
            # foi, le ré-examen passe par l'escalade explicite.
            try:
                dossiers_connus = lire_dossiers_encerclement()
            except ErreurRegistre as e:
                print(f"⚠️  dossiers illisibles ({e}) : pas de "
                      f"déduplication", file=sys.stderr)
                dossiers_connus = {}
            def deja_encerclé(p, _c=c):
                cle = _cle_registre(args.sorry, _c["declaration"],
                                    p["trou"], p["type_trou"], p["terme"],
                                    empreinte)
                return cle in dossiers_connus
            par_trou = {}
            for p in c.get("inconnus", []):
                if deja_encerclé(p):
                    continue
                par_trou.setdefault(p["trou"], []).append(p)
            trous = list(par_trou)
            echantillon, k = [], 0
            while len(echantillon) < max_enc and trous:
                t = trous[k % len(trous)]
                if par_trou[t]:
                    echantillon.append(par_trou[t].pop(0))
                elif all(not par_trou[u] for u in trous):
                    break
                k += 1
            for j, p in enumerate(echantillon):
                try:
                    fiche = encercler_direction(
                        args.sorry, res["module_sorry"], c,
                        res.get("contexte_sorry") or {}, p, defs,
                        empreinte, dossier,
                        opens_sorry=res.get("opens_sorry"),
                        timeout_s=timeout, max_angles=n_encercler,
                        garder=garder, index=(i, j))
                except ErreurRegistre as e:
                    bilan_enc["erreurs"].append(str(e))
                    print(f"⚠️  {e}", file=sys.stderr)
                    continue
                except Exception as e:  # inattendu : visible, run sauvé
                    bilan_enc["erreurs"].append(f"inattendu : {e!r}")
                    print(f"⚠️  encerclement impossible (inattendu) : {e!r}",
                          file=sys.stderr)
                    continue
                if fiche is None:
                    bilan_enc["sans_angle"] += 1
                    continue
                bilan_enc["encercles"] += 1
                if fiche["registre"] == "IMPOSSIBLE_VALIDÉ":
                    bilan_enc["inscrits_registre"] += 1
                elif fiche["registre"] and \
                        fiche["registre"].startswith("ÉCHEC"):
                    bilan_enc["erreurs"].append(fiche["registre"])
                resume = {"candidat": c["declaration"], "trou": p["trou"],
                          "terme": p["terme"], "statut": fiche["statut"],
                          "angles": [(a["angle"], a["statut_angle"])
                                     for a in fiche["angles"]]}
                if fiche["statut"] == "RESISTANT":
                    bilan_enc["resistants"].append(resume)
                elif fiche["statut"] == "ATTEIGNABLE_TROUVE":
                    resume["diagnostic"] = "câblage PROUVÉ par Lean"
                    bilan_enc["atteignables"].append(resume)
        res["encerclement"] = bilan_enc
    if getattr(args, "format", "console") == "json":
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(rendre_console(res))
    return 0


def _executer_entropie(args: argparse.Namespace) -> int:
    """Exécute la sous-commande 'entropie' : H et ΔH en bits sur la Sonde B.

    Lecture seule : ne touche à aucun JSON existant (registre, croyances,
    veille). Les bits mesurent l'attention de l'instrument, jamais P(A).
    """
    from .entropie import (
        DOSSIER_DEFAUT, REGISTRE_DEFAUT, entropie_depuis_sonde,
        rendre_entropie_console,
    )
    import json
    chemin = getattr(args, "registre", None) or REGISTRE_DEFAUT
    if not os.path.isfile(chemin):
        print(f"❌ Registre introuvable : {chemin}")
        print("   (passez --registre CHEMIN vers le registre vivant des hypothèses)")
        return 1
    dossier = getattr(args, "dossier", None) or DOSSIER_DEFAUT
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable pour les croyances : {dossier}")
        return 1
    try:
        resultat = entropie_depuis_sonde(args.mecanisme, dossier=dossier,
                                         chemin_registre=chemin)
    except Exception as e:
        print(f"❌ Erreur lors du chiffrement entropique : {e}")
        return 1
    if getattr(args, "format", "console") == "json":
        print(json.dumps(resultat.vers_dict(), ensure_ascii=False, indent=2))
    else:
        print(rendre_entropie_console(resultat))
    return 0


def _executer_sentinelle(args: argparse.Namespace) -> int:
    """Exécute 'sentinelle' : init / checkpoint / pulse / check / reprise / dormants / enchaine.

    Lecture/écriture sur disque durable uniquement. check MONTRE les
    orphelines, reprise GÉNÈRE le brief, enchaine FAIT AVANCER l'état
    d'une mission vérifiée figée (ordre Tomy 2026-10-05).
    """
    from . import sentinelle as mod_sentinelle
    action = getattr(args, "action", None)
    terrain = getattr(args, "terrain", None) or "."
    if action == "init":
        nom = getattr(args, "nom", None) or os.path.basename(
            os.path.abspath(terrain))
        res = mod_sentinelle.init(terrain, nom)
        print(f"✅ mission « {res['nom']} » initialisée"
              if res["cree"]
              else f"ℹ️ mission « {res['nom']} » déjà initialisée (état préservé)")
        return 0
    if action == "checkpoint":
        item = getattr(args, "item", None)
        statut = getattr(args, "statut", None)
        if not item or not statut:
            print("❌ --item et --statut requis pour checkpoint")
            return 1
        try:
            mod_sentinelle.checkpoint(
                terrain, item, statut, note=getattr(args, "note", "") or "")
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        print(f"✅ [{statut}] {item}")
        return 0
    if action == "pulse":
        mod_sentinelle.pulse(terrain, agent=getattr(args, "agent", "") or "")
        print("💓 pulsation enregistrée")
        return 0
    if action == "check":
        trouvees = mod_sentinelle.orphelines(
            getattr(args, "missions", None) or ".",
            seuil_s=getattr(args, "seuil", None) or 1800)
        print(mod_sentinelle.rendre_orphelines_console(trouvees))
        return 0
    if action == "reprise":
        print(mod_sentinelle.brief_reprise(terrain))
        return 0
    if action == "dormants":
        print(mod_sentinelle.brief_reveil(terrain))
        return 0
    if action == "enchaine":
        print(mod_sentinelle.rendre_enchaine_console(
            mod_sentinelle.enchaine(terrain)))
        return 0
    return 1


def _executer_markov(args: argparse.Namespace) -> int:
    """Exécute 'markov' : construire / diagnostic / simuler / exemple.

    L'instrument modélise, il ne décide pas : il calcule le goulot et
    les gains simulés depuis des observations, sans toucher au pipeline.
    """
    from . import markov as mod_markov
    action = getattr(args, "action", None)
    if action == "exemple":
        import json
        print(json.dumps(mod_markov.exemple_observations_ast(),
                         ensure_ascii=False, indent=2))
        return 0
    if action == "construire":
        import json
        chemin_obs = getattr(args, "observations", None)
        if not chemin_obs:
            print("❌ --observations requis pour construire")
            return 1
        with open(chemin_obs, encoding="utf-8") as fh:
            observations = json.load(fh)
        ordre_txt = getattr(args, "ordre", None)
        ordre = ([e.strip() for e in ordre_txt.split(",") if e.strip()]
                 if ordre_txt else None)
        try:
            chaine = mod_markov.construire(observations, ordre=ordre)
        except ValueError as e:
            print("❌ %s" % e)
            return 1
        sortie = getattr(args, "sortie", None)
        if sortie:
            mod_markov.sauvegarder(chaine, sortie)
            print("✅ chaîne construite (%d états, %d runs) → %s"
                  % (len(chaine["etats"]), chaine["runs_n"], sortie))
        else:
            print(mod_markov.rendre_diagnostic_console(chaine))
        return 0
    if action == "diagnostic":
        chemin = getattr(args, "chaine", None)
        if not chemin:
            print("❌ --chaine requis pour diagnostic")
            return 1
        try:
            chaine = mod_markov.charger(chemin)
        except (OSError, ValueError) as e:
            print("❌ %s" % e)
            return 1
        print(mod_markov.rendre_diagnostic_console(chaine))
        return 0
    if action == "simuler":
        chemin = getattr(args, "chaine", None)
        etape = getattr(args, "etape", None)
        if not chemin or not etape:
            print("❌ --chaine et --etape requis pour simuler")
            return 1
        try:
            chaine = mod_markov.charger(chemin)
        except (OSError, ValueError) as e:
            print("❌ %s" % e)
            return 1
        try:
            scenario = mod_markov.simuler_debit(
                chaine, etape, getattr(args, "facteur", 2.0))
        except ValueError as e:
            print("❌ %s" % e)
            return 1
        print(mod_markov.rendre_simulation_console(scenario))
        return 0
    return 1


def _executer_write(args: argparse.Namespace) -> int:
    """Exécute 'write' : génère un .lean depuis une spec JSON.

    L'instrument met en forme, il ne prouve rien : la validation est
    syntaxique (délimiteurs, champs, pas de sorry sauf flag), la
    vérification sémantique reste le job de Lean (élaboration).
    """
    from . import phiwrite as mod_phiwrite
    chemin_spec = getattr(args, "spec", None)
    sortie = getattr(args, "sortie", None)
    allow_sorry = bool(getattr(args, "allow_sorry", False))
    if not chemin_spec or not sortie:
        print("❌ --spec et --sortie requis "
              "(--spec exemple : spec intégrée)")
        return 1
    try:
        if chemin_spec == "exemple":
            spec = mod_phiwrite.exemple_spec()
        else:
            spec = mod_phiwrite.charger_spec(chemin_spec)
    except (OSError, ValueError) as e:
        print("❌ %s" % e)
        return 1
    try:
        res = mod_phiwrite.ecrire(spec, sortie, allow_sorry=allow_sorry)
    except ValueError as e:
        print("❌ %s" % e)
        return 1
    print("✅ %d théorème(s) → %s (imports : %s)"
          % (res["theoremes"], res["chemin"], ", ".join(res["imports"])))
    return 0


# ────────────────────────────────────────────────────────
# POINT D'ENTRÉE (hermétique — orchestre uniquement)
# ────────────────────────────────────────────────────────

def main():
    """Point d'entrée principal. Délègue à des fonctions spécialisées."""
    parser = _construire_parseur()
    args = parser.parse_args()

    if not args.commande:
        parser.print_help()
        sys.exit(0)

    if args.commande == "edit":
        # L'éditeur accepte tout fichier texte, existant ou à créer :
        # pas de collecte préalable de fichiers supportés.
        sys.exit(_executer_edit(args))

    if args.commande == "index":
        # La carte travaille sur un dossier (pas une liste de fichiers) :
        # court-circuit avant la collecte.
        sys.exit(_executer_index(args))

    if args.commande == "chemins":
        # La carte croyante travaille aussi sur un dossier.
        sys.exit(_executer_chemins(args))

    if args.commande == "snapshot":
        # Le snapshot fige un dossier : court-circuit avant la collecte.
        sys.exit(_executer_snapshot(args))

    if args.commande == "veille":
        # La veille compare un dossier à une référence.
        sys.exit(_executer_veille(args))

    if args.commande == "oracle":
        # L'oracle interroge un fichier OU un dossier : court-circuit
        # avant la collecte de fichiers.
        sys.exit(_executer_oracle(args))

    if args.commande == "radar":
        # Le radar compare deux états : court-circuit avant la collecte.
        sys.exit(_executer_radar(args))

    if args.commande == "sismique":
        # La sismique lit l'historique git : court-circuit avant la collecte.
        sys.exit(_executer_sismique(args))

    if args.commande == "consigner":
        sys.exit(_executer_consigner(args))

    if args.commande == "registre":
        sys.exit(_executer_registre(args))

    if args.commande == "sonde":
        # La sonde lit le registre vivant : court-circuit avant la collecte.
        sys.exit(_executer_sonde(args))

    if args.commande == "piste-sorry":
        # La piste lit le dossier Lean (+ registre opt-in) : court-circuit.
        sys.exit(_executer_piste_sorry(args))

    if args.commande == "chemins-verifiables":
        # Les candidats lisent le dossier Lean (+ registre opt-in) : court-circuit.
        sys.exit(_executer_chemins_verifiables(args))

    if args.commande == "entropie":
        # La lentille entropique lit registre + croyances : court-circuit.
        sys.exit(_executer_entropie(args))

    if args.commande == "ou-aller":
        # La liste d'attention lit registre + croyances : court-circuit.
        sys.exit(_executer_ou_aller(args))

    if args.commande == "explorer":
        # L'explorateur lit registre + croyances et écrit un HTML : court-circuit.
        sys.exit(_executer_explorer(args))

    if args.commande == "sentinelle":
        # La sentinelle travaille sur disque durable, pas sur des fichiers
        # sources : court-circuit avant la collecte de fichiers.
        sys.exit(_executer_sentinelle(args))

    if args.commande == "markov":
        # Les chaînes de Markov travaillent sur des JSON, pas des sources :
        # court-circuit avant la collecte de fichiers.
        sys.exit(_executer_markov(args))

    if args.commande == "write":
        # phiwrite génère depuis une spec JSON, pas depuis des sources :
        # court-circuit avant la collecte de fichiers.
        sys.exit(_executer_write(args))

    fichiers = _collecter_fichiers(args.cible)
    if not fichiers and getattr(args, "lang", None) and os.path.isfile(args.cible):
        # Fichier sans extension reconnue mais langage forcé explicitement
        fichiers = [args.cible]
    if not fichiers:
        print(f"❌ Aucun fichier supporté trouvé dans : {args.cible}")
        sys.exit(1)

    if args.commande == "check":
        sys.exit(_executer_check(args, fichiers))
    elif args.commande == "report":
        sys.exit(_executer_report(args, fichiers))
    elif args.commande == "infer":
        sys.exit(_executer_infer(args, fichiers))


if __name__ == "__main__":
    main()
