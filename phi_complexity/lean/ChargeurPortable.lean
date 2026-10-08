/-
ChargeurPortable.lean — SPIKE chantier 2 (H-COMP-1).

Charge un `.phiast` v2 jouet (3 `def` simples) dans l'environnement Lean
sans élaborer de source : parse du binaire → reconstruction des `Expr` →
`Environment.add` (le noyau vérifie chaque déclaration).

Périmètre assumé petit : tags couverts = sort/const/app/lit.nat,
noms str/num, kind def. Tout le reste → erreur explicite.
-/
import Lean

open Lean

namespace ChargeurPortable

structure PState where
  data : ByteArray
  pos : Nat := 0

abbrev P := StateT PState (Except String)

def ensure (n : Nat) : P Unit := do
  let s ← get
  unless s.pos + n ≤ s.data.size do
    throw s!"fin inattendue à {s.pos} (besoin de {n} octets)"

def u8 : P Nat := do
  ensure 1
  let s ← get
  set { s with pos := s.pos + 1 }
  pure s.data[s.pos]!.toNat

def u16 : P Nat := do
  let a ← u8; let b ← u8
  pure (a + 256 * b)

def u32 : P Nat := do
  let a ← u16; let b ← u16
  pure (a + 65536 * b)

def u64 : P Nat := do
  let a ← u32; let b ← u32
  pure (a + b * 4294967296)

def bytesN (n : Nat) : P ByteArray := do
  ensure n
  let s ← get
  set { s with pos := s.pos + n }
  pure (s.data.extract s.pos (s.pos + n))

partial def parseName (strtab : Array String) : P Name := do
  let tag ← u8
  match tag with
  | 0 => pure .anonymous
  | 1 =>
    let p ← parseName strtab
    let sid ← u32
    match strtab[sid]? with
    | some s => pure (p.str s)
    | none => throw s!"sid {sid} hors table ({strtab.size} entrées)"
  | 2 =>
    let p ← parseName strtab
    let n ← u32
    pure (p.num n)
  | _ => throw s!"tag nom inconnu : {tag}"

partial def parseLevel : P Level := do
  let tag ← u8
  match tag with
  | 0 => pure .zero
  | _ => throw s!"tag niveau non couvert par le spike : {tag}"

partial def parseExpr (strtab : Array String) : P Expr := do
  let tag ← u8
  match tag with
  | 3 =>
    let l ← parseLevel
    pure (.sort l)
  | 4 =>
    let n ← parseName strtab
    let k ← u32
    for _ in [:k] do
      let _ ← parseLevel
    pure (.const n [])
  | 5 =>
    let f ← parseExpr strtab
    let a ← parseExpr strtab
    pure (.app f a)
  | 9 =>
    let lt ← u8
    unless lt == 0 do throw s!"lit_tag non nat : {lt}"
    let sid ← u32
    match strtab[sid]? with
    | some s =>
      match s.toNat? with
      | some n => pure (.lit (.natVal n))
      | none => throw s!"littéral nat invalide : {s}"
    | none => throw s!"sid {sid} hors table"
  | _ => throw s!"tag expr non couvert par le spike : {tag}"

structure Declue where
  name : Name
  type : Expr
  value : Expr

def parseDecl (strtab : Array String) : P Declue := do
  let kind ← u8
  unless kind == 0 do throw s!"kind non def : {kind} (spike : defs seulement)"
  let name ← parseName strtab
  let type ← parseExpr strtab
  let hv ← u8
  unless hv == 1 do throw s!"has_value != 1 : {hv}"
  let value ← parseExpr strtab
  pure { name, type, value }

def parseStrtab : P (Array String) := do
  let n ← u32
  let mut tab := #[]
  for _ in [:n] do
    let ln ← u16
    let bs ← bytesN ln
    match String.fromUTF8? bs with
    | some s => tab := tab.push s
    | none => throw "chaîne non UTF-8 dans la table"
  pure tab

def parseFile : P (Array Declue) := do
  let magic ← bytesN 8
  unless magic == "PHIAST01".toUTF8 do throw "magic invalide"
  let ver ← u32
  unless ver == 2 do throw s!"version {ver} != 2"
  let tclen ← u16
  let _ ← bytesN tclen
  let ndecls ← u32
  let _ ← u32 -- flags
  let st ← get
  let bodyStart := st.pos
  let total := st.data.size
  set { st with pos := total - 16 }
  let strtabOff ← u64
  let tmagic ← bytesN 8
  unless tmagic == "PHIAST02".toUTF8 do throw "magic trailer invalide"
  modify fun st => { st with pos := strtabOff }
  let strtab ← parseStrtab
  modify fun st => { st with pos := bodyStart }
  let mut decls := #[]
  for _ in [:ndecls] do
    decls := decls.push (← parseDecl strtab)
  pure decls

def charger : CoreM Unit := do
  let t0 <- IO.monoMsNow
  let bytes : ByteArray <- IO.FS.readBinFile "spike_portable/jouet_portable.phiast"
  let decls : Array Declue <-
    ofExcept (parseFile.run' { data := bytes } : Except String (Array Declue))
  let t1 <- IO.monoMsNow
  IO.println s!"parse : {decls.size} declaration(s) en {t1 - t0} ms"
  for d in decls do
    let dv : DefinitionVal :=
      { name := d.name, levelParams := [], type := d.type, value := d.value,
        hints := .opaque, safety := .safe }
    -- `true` = le noyau verifie la declaration (H-COMP-1 point (c)).
    Lean.addDecl (.defnDecl dv) true
  let t2 <- IO.monoMsNow
  let env <- MonadEnv.getEnv
  for d in decls do
    match env.find? d.name with
    | some ci => IO.println s!"  noyau OK : {d.name} : {ci.type}"
    | none => throwError "constante introuvable apres ajout"
  IO.println s!"addDecl (noyau, 3 decls) : {t2 - t1} ms"
  IO.println "SPIKE H-COMP-1 : CHARGE ET VERIFIE PAR LE NOYAU"

-- Le #eval s'execute dans CoreM : c'est lui qui porte addDecl + getEnv.
#eval charger

end ChargeurPortable
