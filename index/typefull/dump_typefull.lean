/-
Dump Lean Finder's `typeFull` for every declaration of the given modules.

Lean Finder's corpus (jixia `Analyzer/Process/Symbol.lean`) stores `PrettyPrinter.ppExpr` of each
constant's *type*, rendered at the default width of 120 columns.  doc-gen4's `signature` header is a
different rendering (see FORMAT-CHECK.md), so the most faithful way to build the passage text is to
run the pretty printer ourselves over the same environment the extraction used.

    LEAN_PATH=<project lib dirs> lean --run dump_typefull.lean [--only-roots] Mathlib.Topology.Compactness.Compact > typefull.jsonl

Arguments: module names to import, or `@file` for a file of names (one per line).  Every non-internal constant of the imported closure is
emitted, or with `--only-roots` first only those defined in the named modules.  One JSON object per
line: {"name","module","typeFull"}.  finder_common.adapt_decl uses a row's `typeFull` verbatim, so the
output can be merged into decls.jsonl by name.
-/
import Lean
open Lean Meta

def isInternalName (n : Name) : Bool :=
  n.isInternal || n.isInternalDetail

unsafe def main (args : List String) : IO UInt32 := do
  let (onlyRoots, rawMods) := match args with
    | "--only-roots" :: rest => (true, rest)
    | rest => (false, rest)
  -- `@file` expands to the module names listed in `file`, one per line.
  let mut mods : List String := []
  for a in rawMods do
    if a.startsWith "@" then
      let txt ← IO.FS.readFile (a.drop 1).toString
      mods := mods ++ (txt.splitOn "\n").filter (· ≠ "")
    else
      mods := mods ++ [a]
  enableInitializersExecution
  initSearchPath (← findSysroot)
  let imports := mods.toArray.map fun m => ({ module := m.toName } : Import)
  let env ← importModules imports {} (trustLevel := 1024) (loadExts := true)
  let roots : Std.HashSet Name := Std.HashSet.ofList (mods.map String.toName)
  let ctx : Core.Context := { fileName := "<dump>", fileMap := default, maxHeartbeats := 0 }
  let stdout ← IO.getStdout
  let mut count := 0
  for (n, ci) in env.constants.map₁.toList do
    if isInternalName n then continue
    let some idx := env.getModuleIdxFor? n | continue
    let modName := env.header.moduleNames[idx.toNat]!
    if onlyRoots && !roots.contains modName then continue
    let st : Core.State := { env }
    let act : CoreM Format := (ppExpr ci.type).run'
    let r ← (act.toIO ctx st).toBaseIO
    match r with
    | .ok (fmt, _) =>
      stdout.putStrLn <| Json.compress <| Json.mkObj [("name", toJson n.toString),
        ("module", toJson modName.toString), ("typeFull", toJson (fmt.pretty 120))]
      count := count + 1
    | .error _ => pure ()
  IO.eprintln s!"dumped {count} constants"
  return 0
