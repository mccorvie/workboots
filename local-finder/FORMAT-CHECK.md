# FORMAT-CHECK: how Lean Finder renders `typeFull`, and how our extraction can match it

Source: Lean Finder's public Mathlib corpus `mathlib4_28_0.jsonl` (224,722 rows, all `path` under
`Mathlib/`), downloaded to `~/.local/share/tauceti-finder/upstream-corpus/`. Scripts used live in
`scripts/`; scratch outputs are not kept. Checked 2026-10-08. The shared extraction
(`~/.local/share/tauceti-index/b3-11bc13335fb9/decls.jsonl`) did not exist yet; instead I compared against
`partial.jsonl`, a 67,092-row doc-gen4 extraction of core Lean (Init/Std) that S0 left in the scratchpad, and
against my own `ppExpr` dump (below).

## What gets embedded

`vendor/lean_finder/build_index.py:format_entry` renders each row as the string
`{'formal_name': '<name>', 'kind': '<kind>', 'type': '<typeFull>', 'docstring': '<docString>'}` (values via `str()`, no escaping).
Neither `path`, `informal_name` nor `informal_description` is embedded, so only `name`, `kind`, `typeFull` and `docString`
must be reproduced by the extraction. A missing docstring is JSON `null` upstream (164,717 of 224,722 rows;
3 rows have `""`) and is rendered as the literal text `None`; `adapt_decl` maps empty/missing to `null`
so we land on the same text. Entry formatting round-trips byte for byte: all 200 rows mapped upstream ->
our `decls.jsonl` schema -> `adapt_decl` -> `format_entry` equal the upstream `format_entry` (0 mismatches),
and the predecessor's check (see VALIDATION.md) shows our CPU vectors have cosine 0.99991 to the shipped index.

## `typeFull` is `ppExpr` of the constant's type, not a signature

Statistics over all 224,722 upstream rows:

| property | share |
|---|---|
| contains a line break | 64.3% |
| starts with `∀` (Prop-valued: theorems) | 78.2% |
| starts with `{` (data: `{α : Type u} → ...`) | 14.4% |
| contains named instance binder `[inst : C]` | 71.7% |
| contains an anonymous instance binder `[C ...]` only | 2.9% |
| mentions `Type u...`/`Sort`: universe-polymorphic | 83.0% / 1.8% |
| strict-implicit `⦃x⦄` | 0.6% |
| contains `Mathlib.` qualified names | 0.75% |
| contains `✝` (inaccessible names) | 0.09% |
| longest line | 120 columns (max over the first 20k rows) |
| median / max length | 161 / 7,616 characters |

Kinds are only `theorem` 179,271, `definition` 44,912, `inductive` 304, `opaque` 235. Absent from the corpus:
structures and classes (`Group`, `Real`, `Submonoid`), constructors, projections, recursors, Lean core and Std
(every `path` is under `Mathlib/`, so core Lean and Std names are never indexed upstream).
Instances are `definition`. Constructors of Prop inductives appear as `theorem` (`List.Sublist.cons_cons`).

Rules, read off the samples in the appendix:

- **Binders.** A `∀` telescope for Props, a dependent-arrow telescope for everything else:
  `∀ {X : Type u} [inst : TopologicalSpace X] {s : Set X}, IsCompact s → ...` and
  `{α : Type u} → [PseudoMetricSpace α] → α → ℝ → Set α`. Explicit binders whose name is not used later become
  plain arrows (`IsCompact s → Continuous f → ...`); used ones stay `(x : T)`. Adjacent binders of the same kind and
  type are merged (`{l₁ l₂ : List α}`, `(a b : Erased α)`), also when the source wrote them separately.
- **Implicit and instance binders.** `{x}` implicit, `⦃x⦄` strict implicit, `[inst : C]` or `[C]`: an instance binder
  shows its name (`inst`, `inst_1`, ...) exactly when a later part of the *elaborated* type depends on it
  (e.g. `Continuous f` takes both topologies as hidden arguments), otherwise it is anonymous (`[IsOrderedAddMonoid E]`).
  This cannot be recovered from the source-level signature text.
- **Universes.** Auto-named `Type u_1`, `Sort u_4`, `Type (max u v)`, `Type (u + 1)`, `Sort (max 1 u)`; no `.{u}` instantiations
  are printed in types.
- **Namespaces.** Names are printed fully qualified as `ppExpr` prints them with no `open` in effect:
  `MeasureTheory.Measure α`, `Topology.IsInducing f`, `Filter.Tendsto`. Dot notation is used for the first explicit
  argument of a namespaced function (`l₁.Perm l₂`, `s.Finite`, `(f '' s)`). Notation comes from the unexpanders in the
  environment: `∈`, `⁻¹'`, `⋃ i ∈ s, f i`, `∃ t, ...`, `∫ (x : α), f x ∂μ`, `ℝ`, `ℕ`.
  A few rows show `Nat` instead of `ℕ` where the generating environment lacked the notation (see drift below).
- **Line breaks.** Lean's `Format` at width 120: the first line holds the binder telescope up to 120 columns; a wrapped
  continuation is indented 2 spaces, and each nested `→` after a break adds 2 more (2, 4, 6, ...); the indent histogram of
  continuation lines is 2: 235k, 4: 45k, 6: 33k, 8: 23k. There is no trailing newline or whitespace.
- **Extra elaborated forms.** `autoParam`/`optParam` wrappers appear verbatim; `↑x`, `⇑f` coercions appear;
  `fun x => ...` binders appear with `fun {β} x => x.run` style after delaboration.

## Why the doc-gen4 `signature` cannot match, and what to do

The shared extraction's `signature` is doc-gen4's header `<kind words> <name> <binders> : <type>`. Against the true
`ppExpr` type of the same constants from the same environment (55,277 core-Lean declarations: `partial.jsonl` joined to my dump):

| rebuild | exact match | by kind |
|---|---|---|
| `adapt_decl` from `signature` (forall mode) | **8,799 / 55,277 = 15.9%** | theorem 5,465/40,732; def 1,340/8,779; instance 967/3,739; ctor 482/1,044 |

Typical failures (see examples further down): doc-gen4 names are relative to the declaration's namespace and `open`
scope (`Lex r s (inl a₁)` vs `Sum.Lex r s (Sum.inl a₁)`), `Nat` vs `ℕ`, source binder names kept (`x✝`, `ss1 ss2 : Raw`), lambda binder
annotations kept (`fun {β : Type u} (x : OptionT m β) => x` vs `fun {β} x => x.run`), not re-wrapped to width 120, instance binders
named in 19% of mismatches, constructors that are Props not turned into `∀`. These are not reparable by text surgery.

**Recommended and implemented fix: dump `typeFull` with the real pretty printer.**
`scripts/dump_typefull.lean` imports the same modules as the extraction and prints `(ppExpr c.type).pretty 120` for each
non-internal constant (`lean --run`, needs `enableInitializersExecution` and `loadExts := true` so Mathlib's unexpanders are
loaded). `scripts/merge_typefull.py decls.jsonl typefull.jsonl` adds a `typeFull` field by name, which `adapt_decl` then
uses verbatim. Test run in the b3 scratch environment (Lean v4.35.0-rc3, `LEAN_PATH` built from
`.lake/packages/*/.lake/build/lib/lean`):

| run | result |
|---|---|
| closure of `Mathlib.Topology.Compactness.Compact` | 144,255 constants in 3 min 51 s, single thread, under host load ~150 |
| names shared with upstream `mathlib4_28_0` | 23,670; **20,197 identical (85.3%)** |
| `Mathlib.Topology.Compactness.Compact` itself | 158 of 165 shared names identical |
| the other 3,473 shared names | all content differences; none whitespace-only |

The residual 14.7% is Mathlib drift between upstream's snapshot (their corpus is the `mathlib4_28_0` snapshot, not b3's Mathlib) and b3's Mathlib: renamed class hierarchies (`ConditionallyCompleteLattice` vs `ConditionallyCompletePartialOrderInf`), reordered
binders (`{a : α} [inst : SemilatticeSup α]`), `Nat` vs `ℕ`, `∀ j ∈ s` vs `∀ (j : ι), j ∈ s`. Line wrapping and instance naming match exactly in
every row we compared at the same statement, so for a given Mathlib the dump reproduces Lean Finder's text.

A full Mathlib+TauCeti dump should take about 10 minutes on one core; run it inside the pin's scratch checkout (same toolchain and
`LEAN_PATH` as `extract.py`) for modules `Mathlib` and `TauCeti` (plus whatever roots the extraction imports).
Without it, `build_finder.py` still works from `signature` (`TAUCETI_FINDER_SIGNATURE_MODE=forall`, with fixes made in this pass:
dot-notation uses such as `xs.reverse` now count as a use of binder `xs`, and adjacent same-type binders are merged) and prints a warning with the count of
rows lacking `typeFull`. The manifest records `type_full_source`.

### Doc-gen4 header vs `ppExpr` examples (core Lean, same environment)

```
SIG: constructor Sum.Lex.inl {α : Type u_1} {β : Type u_2} {r : α → α → Prop} {s : β → β → Prop} {a₁ a₂ : α} (h : r a₁ a₂) : Lex r s (inl a₁) (inl a₂)
PP : ∀ {α : Type u_1} {β : Type u_2} {r : α → α → Prop} {s : β → β → Prop} {a₁ a₂ : α},
  r a₁ a₂ → Sum.Lex r s (Sum.inl a₁) (Sum.inl a₂)

SIG: constructor Lean.ParserDescr.trailingNode (kind : SyntaxNodeKind) (prec lhsPrec : Nat) (p : ParserDescr) : ParserDescr
PP : Lean.SyntaxNodeKind → ℕ → ℕ → Lean.ParserDescr → Lean.ParserDescr

SIG: theorem Std.Total.total {α : Sort u} {r : α → α → Prop} [self : Total r] (a b : α) : r a b ∨ r b a
PP : ∀ {α : Sort u} {r : α → α → Prop} [self : Std.Total r] (a b : α), r a b ∨ r b a

SIG: opaque Substring.Raw.Internal.beq (ss1 ss2 : Raw) : Bool
PP : Substring.Raw → Substring.Raw → Bool

SIG: theorem OptionT.run_liftWith {m : Type u → Type u_1} {α : Type u} [Monad m] [LawfulMonad m] (f : ...) : (liftWith f).run = some <$> f fun {β : Type u} (x : OptionT m β) => x.run
PP : ∀ {m : Type u → Type u_1} {α : Type u} [inst : Monad m] [LawfulMonad m]
  (f : ({β : Type u} → OptionT m β → m (stM m (OptionT m) β)) → m α), (liftWith f).run = some <$> f fun {β} x => x.run
```

## Other policy differences (kinds)

`adapt_decl` maps doc-gen4 kinds onto upstream's vocabulary (`def`/`instance`/`abbrev` -> `definition`, `structure`/`class`/`inductive` -> `inductive`,
`lemma` -> `theorem`). Constructors are kept as `constructor` and recursors etc. pass through, and **upstream has none of structures, classes, constructors,
projections, or Lean core**, so including them is a deliberate widening (the query side is trained on theorems and defs; structures and ctors
may rank oddly). The model sees the `kind` string, so this is the one place we knowingly leave the training distribution. Decision for the operator: filter
to upstream's kinds, or keep the wider set (default: keep).

## Appendix: 54 upstream declarations (typeFull verbatim, as embedded)

Chosen to span: named well-known lemmas, theorems without binders, `autoParam`/`optParam`, inaccessible names, inductives, opaque,
`Type (max ..)`, very long types, multi-line docstrings, anonymous instance binders.

```
54
#### IsCompact.image  [theorem]  Mathlib/Topology/Compactness/Compact  (named)
typeFull:
∀ {X : Type u} {Y : Type v} [inst : TopologicalSpace X] [inst_1 : TopologicalSpace Y] {s : Set X} {f : X → Y},
  IsCompact s → Continuous f → IsCompact (f '' s)
docString: null

#### MeasureTheory.integral_nonneg  [theorem]  Mathlib/MeasureTheory/Integral/Bochner/Basic  (named)
typeFull:
∀ {α : Type u_1} {E : Type u_2} [inst : NormedAddCommGroup E] [inst_1 : NormedSpace ℝ E] {m : MeasurableSpace α}
  {μ : MeasureTheory.Measure α} [inst_2 : PartialOrder E] [IsOrderedAddMonoid E] [IsOrderedModule ℝ E]
  [ClosedIciTopology E] {f : α → E}, 0 ≤ f → 0 ≤ ∫ (x : α), f x ∂μ
docString: null

#### MeasureTheory.setIntegral_nonneg  [theorem]  Mathlib/MeasureTheory/Integral/Bochner/Set  (named)
typeFull:
∀ {X : Type u_1} {mX : MeasurableSpace X} {μ : MeasureTheory.Measure X} {f : X → ℝ} {s : Set X},
  MeasurableSet s → (∀ x ∈ s, 0 ≤ f x) → 0 ≤ ∫ (x : X) in s, f x ∂μ
docString: null

#### MeasureTheory.lintegral_mono  [theorem]  Mathlib/MeasureTheory/Integral/Lebesgue/Basic  (named)
typeFull:
∀ {α : Type u_1} {m : MeasurableSpace α} {μ : MeasureTheory.Measure α} ⦃f g : α → ENNReal⦄,
  f ≤ g → ∫⁻ (a : α), f a ∂μ ≤ ∫⁻ (a : α), g a ∂μ
docString: null

#### Continuous.comp  [theorem]  Mathlib/Topology/Continuous  (named)
typeFull:
∀ {X : Type u_1} {Y : Type u_2} {Z : Type u_3} [inst : TopologicalSpace X] [inst_1 : TopologicalSpace Y]
  [inst_2 : TopologicalSpace Z] {f : X → Y} {g : Y → Z}, Continuous g → Continuous f → Continuous (g ∘ f)
docString: null

#### Metric.ball  [definition]  Mathlib/Topology/MetricSpace/Pseudo/Defs  (named)
typeFull:
{α : Type u} → [PseudoMetricSpace α] → α → ℝ → Set α
docString: "`ball x ε` is the set of all points `y` with `dist y x < ε` "

#### Set.image  [definition]  Mathlib/Data/Set/Defs  (named)
typeFull:
{α : Type u} → {β : Type v} → (α → β) → Set α → Set β
docString: "The image of `s : Set α` by `f : α → β`, written `f '' s`, is the set of `b : β` such that\n`f a = b` for some `a ∈ s`. "

#### Monotone  [definition]  Mathlib/Order/Monotone/Defs  (named)
typeFull:
{α : Type u} → {β : Type v} → [Preorder α] → [Preorder β] → (α → β) → Prop
docString: "A function `f` is monotone if `a ≤ b` implies `f a ≤ f b`. "

#### Submodule.span  [definition]  Mathlib/LinearAlgebra/Span/Defs  (named)
typeFull:
(R : Type u_1) →
  {M : Type u_4} → [inst : Semiring R] → [inst_1 : AddCommMonoid M] → [inst_2 : Module R M] → Set M → Submodule R M
docString: "The span of a set `s ⊆ M` is the smallest submodule of M that contains `s`. "

#### deriv  [definition]  Mathlib/Analysis/Calculus/Deriv/Basic  (named)
typeFull:
{𝕜 : Type u} →
  [inst : NontriviallyNormedField 𝕜] →
    {F : Type v} → [inst_1 : AddCommGroup F] → [Module 𝕜 F] → [TopologicalSpace F] → (𝕜 → F) → 𝕜 → F
docString: "Derivative of `f` at the point `x`, if it exists.  Zero otherwise.\n\nIf the derivative exists (i.e., `∃ f', HasDerivAt f f' x`), then\n`f x' = f x + (x' - x) • deriv f x + o(x' - x)` where `x'` converges to `x`.\n"

#### HasDerivAt  [definition]  Mathlib/Analysis/Calculus/Deriv/Basic  (named)
typeFull:
{𝕜 : Type u} →
  [inst : NontriviallyNormedField 𝕜] →
    {F : Type v} →
      [inst_1 : AddCommGroup F] →
        [inst_2 : Module 𝕜 F] → [inst_3 : TopologicalSpace F] → [ContinuousSMul 𝕜 F] → (𝕜 → F) → F → 𝕜 → Prop
docString: "`f` has the derivative `f'` at the point `x`.\n\nThat is, `f x' = f x + (x' - x) • f' + o(x' - x)` where `x'` converges to `x`.\n"

#### Polynomial.degree  [definition]  Mathlib/Algebra/Polynomial/Degree/Definitions  (named)
typeFull:
{R : Type u} → [inst : Semiring R] → Polynomial R → WithBot ℕ
docString: "`degree p` is the degree of the polynomial `p`, i.e. the largest `X`-exponent in `p`.\n`degree p = some n` when `p ≠ 0` and `n` is the highest power of `X` that appears in `p`, otherwise\n`degree 0 = ⊥`. "

#### Matrix.det  [definition]  Mathlib/LinearAlgebra/Matrix/Determinant/Basic  (named)
typeFull:
{n : Type u_2} → [DecidableEq n] → [Fintype n] → {R : Type v} → [CommRing R] → Matrix n n R → R
docString: "The determinant of a matrix given by the Leibniz formula. "

#### Real.exp_pos  [theorem]  Mathlib/Analysis/Complex/Exponential  (named)
typeFull:
∀ (x : ℝ), 0 < Real.exp x
docString: null

#### Equiv.Perm  [definition]  Mathlib/Logic/Equiv/Defs  (named)
typeFull:
Sort u_1 → Sort (max 1 u_1)
docString: "`Perm α` is the type of bijections from `α` to itself. "

#### Nat.factorial  [definition]  Mathlib/Data/Nat/Factorial/Basic  (named)
typeFull:
ℕ → ℕ
docString: "`Nat.factorial n` is the factorial of `n`. "

#### Set.Finite  [definition]  Mathlib/Data/Finite/Defs  (named)
typeFull:
{α : Type u} → Set α → Prop
docString: "A set is finite if the corresponding `Subtype` is finite,\ni.e., if there exists a natural `n : ℕ` and an equivalence `s ≃ Fin n`. "

#### Real.pi  [definition]  Mathlib/Analysis/SpecialFunctions/Trigonometric/Basic  (named)
typeFull:
ℝ
docString: "The number π = 3.14159265... Defined here using choice as twice a zero of cos in [1,2],\nfrom which one can derive all its properties. For explicit bounds on π,\nsee `Mathlib/Analysis/Real/Pi/Bounds.lean`.\n\nDenoted `π`, once the `Real` namespace is opened. "

#### Filter.Tendsto  [definition]  Mathlib/Order/Filter/Defs  (named)
typeFull:
{α : Type u_1} → {β : Type u_2} → (α → β) → Filter α → Filter β → Prop
docString: "`Filter.Tendsto` is the generic \"limit of a function\" predicate.\n  `Tendsto f l₁ l₂` asserts that for every `l₂` neighborhood `a`,\n  the `f`-preimage of `a` is an `l₁` neighborhood. "

#### Ideal.span  [definition]  Mathlib/RingTheory/Ideal/Span  (named)
typeFull:
{α : Type u} → [inst : Semiring α] → Set α → Ideal α
docString: "The ideal generated by a subset of a ring "

#### IsCompact.image_of_continuousOn  [theorem]  Mathlib/Topology/Compactness/Compact  (named)
typeFull:
∀ {X : Type u} {Y : Type v} [inst : TopologicalSpace X] [inst_1 : TopologicalSpace Y] {s : Set X} {f : X → Y},
  IsCompact s → ContinuousOn f s → IsCompact (f '' s)
docString: null

#### Finset.card_le_card  [theorem]  Mathlib/Data/Finset/Card  (named)
typeFull:
∀ {α : Type u_1} {s t : Finset α}, s ⊆ t → s.card ≤ t.card
docString: null

#### intervalIntegral.integral_nonneg  [theorem]  Mathlib/MeasureTheory/Integral/IntervalIntegral/Basic  (named)
typeFull:
∀ {f : ℝ → ℝ} {a b : ℝ} {μ : MeasureTheory.Measure ℝ},
  a ≤ b → (∀ u ∈ Set.Icc a b, 0 ≤ f u) → 0 ≤ ∫ (u : ℝ) in a..b, f u ∂μ
docString: null

#### Real.sqrt  [definition]  Mathlib/Data/Real/Sqrt  (named)
typeFull:
ℝ → ℝ
docString: "The square root of a real number. This returns 0 for negative inputs.\n\nThis has notation `√x`. Note that `√x⁻¹` is parsed as `√(x⁻¹)`. "

#### Nat.Prime  [definition]  Mathlib/Data/Nat/Prime/Defs  (named)
typeFull:
ℕ → Prop
docString: "`Nat.Prime p` means that `p` is a prime number, that is, a natural number\n  at least 2 whose only divisors are `p` and `1`.\n  The theorem `Nat.prime_def` witnesses this description of a prime number. "

#### MeasureTheory.Measure.instFunLike  [definition]  Mathlib/MeasureTheory/Measure/MeasureSpaceDef  (named)
typeFull:
{α : Type u_1} → [inst : MeasurableSpace α] → FunLike (MeasureTheory.Measure α) (Set α) ENNReal
docString: null

#### instAbelianCondensedMod  [definition]  Mathlib/Condensed/Module  (named)
typeFull:
(R : Type (u + 1)) → [inst : Ring R] → CategoryTheory.Abelian (CondensedMod R)
docString: null

#### FunLike  [definition]  Mathlib/Data/FunLike/Basic  (named)
typeFull:
Sort u_1 → Sort u_2 → Sort u_3 → Sort (max (max (max 1 u_1) u_2) u_3)
docString: "The class `FunLike F α β` (`Fun`ction-`Like`) expresses that terms of type `F`\nhave an injective coercion to functions from `α` to `β`.\n`FunLike` is the non-dependent version of `DFunLike`.\nThis typeclass is used in the definition of the homomorphism typeclasses,\nsuch as `ZeroHomClass`, `MulHom

#### Finset.prod  [definition]  Mathlib/Algebra/BigOperators/Group/Finset/Defs  (named)
typeFull:
{ι : Type u_1} → {M : Type u_3} → [CommMonoid M] → Finset ι → (ι → M) → M
docString: "`∏ x ∈ s, f x` is the product of `f x` as `x` ranges over the elements of the finite set `s`.\n\nWhen the index type is a `Fintype`, the notation `∏ x, f x`, is a shorthand for\n`∏ x ∈ Finset.univ, f x`. "

#### Set.mem_Icc  [theorem]  Mathlib/Order/Interval/Set/Defs  (named)
typeFull:
∀ {α : Type u_1} [inst : Preorder α] {a b x : α}, x ∈ Set.Icc a b ↔ a ≤ x ∧ x ≤ b
docString: null

#### Nat.Prime.two_le  [theorem]  Mathlib/Data/Nat/Prime/Defs  (named)
typeFull:
∀ {p : ℕ}, Nat.Prime p → 2 ≤ p
docString: null

#### Set.Icc  [definition]  Mathlib/Order/Interval/Set/Defs  (named)
typeFull:
{α : Type u_1} → [Preorder α] → α → α → Set α
docString: "`Icc a b` is the left-closed right-closed interval $[a, b]$. "

#### List.sublists_append  [theorem]  Mathlib/Data/List/Sublists  (named)
typeFull:
∀ {α : Type u} (l₁ l₂ : List α),
  (l₁ ++ l₂).sublists = do
    let x ← l₂.sublists
    List.map (fun x_1 => x_1 ++ x) l₁.sublists
docString: null

#### AddMonoidWithOne.ext  [theorem]  Mathlib/Algebra/Ring/Ext  (named)
typeFull:
∀ {R : Type u} ⦃inst₁ inst₂ : AddMonoidWithOne R⦄, HAdd.hAdd = HAdd.hAdd → One.one = One.one → inst₁ = inst₂
docString: null

#### Multiset.prod_hom_rel  [theorem]  Mathlib/Algebra/BigOperators/Group/Multiset/Defs  (named)
typeFull:
∀ {ι : Type u_2} {M : Type u_3} {N : Type u_4} [inst : CommMonoid M] [inst_1 : CommMonoid N] (s : Multiset ι)
  {r : M → N → Prop} {f : ι → M} {g : ι → N},
  r 1 1 → (∀ ⦃a : ι⦄ ⦃b : M⦄ ⦃c : N⦄, r b c → r (f a * b) (g a * c)) → r (Multiset.map f s).prod (Multiset.map g s).prod
docString: null

#### Complex.exp  [definition]  Mathlib/Analysis/Complex/Exponential  (named)
typeFull:
ℂ → ℂ
docString: "The complex exponential function, defined via its Taylor series "

#### Ordinal.wellFoundedLT  [theorem]  Mathlib/SetTheory/Ordinal/Basic  (theorem-no-binders)
typeFull:
WellFoundedLT Ordinal.{u_1}
docString: null

#### dense_liouville  [theorem]  Mathlib/NumberTheory/Transcendental/Liouville/Residual  (theorem-no-binders)
typeFull:
Dense {x | Liouville x}
docString: "The set of Liouville numbers in dense. "

#### CategoryTheory.Join.mkNatTrans_app_right  [theorem]  Mathlib/CategoryTheory/Join/Basic  (autoParam)
typeFull:
∀ {C : Type u₁} [inst : CategoryTheory.Category.{v₁, u₁} C] {D : Type u₂} [inst_1 : CategoryTheory.Category.{v₂, u₂} D]
  {E : Type u₃} [inst_2 : CategoryTheory.Category.{v₃, u₃} E]
  {F F' : CategoryTheory.Functor (CategoryTheory.Join C D) E}
  (αₗ : (CategoryTheory.Join.inclLeft C D).comp F ⟶ (CategoryTheory.Join.inclLeft C D).comp F')
  (αᵣ : (CategoryTheory.Join.inclRight C D).comp F ⟶ (CategoryTheory.Join.inclRight C D).comp F')
  (h :
    autoParam
      (CategoryTheory.CategoryStruct.comp
          (CategoryTheory.Functor.whiskerRight (CategoryTheory.Join.edgeTransform C D) F)
          ((CategoryTheory.Prod.snd C D).whiskerLeft αᵣ) =
        CategoryTheory.CategoryStruct.comp ((CategoryTheory.Prod.fst C D).whiskerLeft αₗ)
          (CategoryTheory.Functor.whiskerRight (CategoryTheory.Join.edgeTransform C D) F'))
      _auto_108✝)
  (d : D), (CategoryTheory.Join.mkNatTrans αₗ αᵣ h).app (CategoryTheory.Join.right d) = αᵣ.app d
docString: null

#### Algebra.PreSubmersivePresentation.jacobiMatrix_naive  [theorem]  Mathlib/RingTheory/Extension/Presentation/Submersive  (optParam)
typeFull:
∀ {R : Type u} {ι : Type w} {σ : Type t} [inst : CommRing R] {v : ι → MvPolynomial σ R} (a : ι → σ)
  (ha : Function.Injective a)
  (s : optParam (MvPolynomial σ R ⧸ Ideal.span (Set.range v) → MvPolynomial σ R) (Function.surjInv ⋯))
  (hs :
    autoParam
      (∀ (x : MvPolynomial σ R ⧸ Ideal.span (Set.range v)), (Ideal.Quotient.mk (Ideal.span (Set.range v))) (s x) = x)
      _auto_103✝)
  [inst_1 : Fintype ι] [inst_2 : DecidableEq ι] (i j : ι),
  (Algebra.PreSubmersivePresentation.naive a ha s hs).jacobiMatrix i j = (MvPolynomial.pderiv (a i)) (v j)
docString: null

#### StieltjesFunction.length_diff_botSet  [theorem]  Mathlib/MeasureTheory/Measure/Stieltjes  (hygienic✝)
typeFull:
∀ {R : Type u_1} [inst : LinearOrder R] [inst_1 : TopologicalSpace R] (f : StieltjesFunction R) {s : Set R},
  f.length (s \ botSet✝) = f.length s
docString: null

#### Simps.addProjections  [opaque]  Mathlib/Tactic/Simps/Basic  (hygienic✝)
typeFull:
Lean.Syntax →
  List Lean.Name →
    NameStruct✝ →
      Lean.Expr →
        Lean.Expr →
          Lean.Expr →
            Array Lean.Expr →
              Bool → Simps.Config → List (String × Lean.Syntax) → List Nat → Lean.MetaM (Array Lean.Name)
docString: "Derive lemmas specifying the projections of the declaration.\n`nm`: name of the lemma\nIf `todo` is non-empty, it will generate exactly the names in `todo`.\n`toApply` is non-empty after a custom projection that is a composition of multiple projections\nwas just used. In that case we need to apply 

#### SNum  [inductive]  Mathlib/Data/Num/Bitwise  (inductive)
typeFull:
Type
docString: "Alternative representation of integers using a sign bit at the end.\n  The convention on sign here is to have the argument to `msb` denote\n  the sign of the MSB itself, with all higher bits set to the negation\n  of this sign. The result is interpreted in two's complement.\n\n     13  = ..0001101(

#### CategoryTheory.Limits.WidePullbackShape.Hom  [inductive]  Mathlib/CategoryTheory/Limits/Shapes/WidePullbacks  (inductive)
typeFull:
{J : Type w} → CategoryTheory.Limits.WidePullbackShape J → CategoryTheory.Limits.WidePullbackShape J → Type w
docString: "The type of arrows for the shape indexing a wide pullback. "

#### Mathlib.Tactic.Says.says.verify  [opaque]  Mathlib/Tactic/Says  (opaque)
typeFull:
Lean.Option Bool
docString: "If this option is `true`, verify for `X says Y` that `X says` outputs `Y`. "

#### CategoryTheory.ReflQuiv  [definition]  Mathlib/CategoryTheory/Category/ReflQuiv  (Type(max))
typeFull:
Type (max (u + 1) u (v + 1))
docString: "Category of refl quivers. "

#### Quiver.Path.BoundedPaths  [definition]  Mathlib/Combinatorics/Quiver/Path  (Sort(max))
typeFull:
{V : Type u_1} → [Quiver V] → V → V → ℕ → Sort (max 1 (u_1 + 1) u_2)
docString: "A bounded path is a path with a uniform bound on its length. "

#### IsLocalization.mapₐ_injective_of_injective  [theorem]  Mathlib/RingTheory/Localization/Algebra  (long>600)
typeFull:
∀ {R : Type u_5} [inst : CommSemiring R] (M : Submonoid R) {A : Type u_6} [inst_1 : CommSemiring A]
  [inst_2 : Algebra R A] {B : Type u_7} [inst_3 : CommSemiring B] [inst_4 : Algebra R B] (Rₚ : Type u_8)
  [inst_5 : CommSemiring Rₚ] [inst_6 : Algebra R Rₚ] [inst_7 : IsLocalization M Rₚ] (Aₚ : Type u_9)
  [inst_8 : CommSemiring Aₚ] [inst_9 : Algebra R Aₚ] [inst_10 : Algebra A Aₚ] [inst_11 : IsScalarTower R A Aₚ]
  [inst_12 : IsLocalization (Algebra.algebraMapSubmonoid A M) Aₚ] (Bₚ : Type u_10) [inst_13 : CommSemiring Bₚ]
  [inst_14 : Algebra R Bₚ] [inst_15 : Algebra B Bₚ] [inst_16 : IsScalarTower R B Bₚ]
  [inst_17 : IsLocalization (Algebra.algebraMapSubmonoid B M) Bₚ] [inst_18 : Algebra Rₚ Aₚ] [inst_19 : Algebra Rₚ Bₚ]
  [inst_20 : IsScalarTower R Rₚ Aₚ] [inst_21 : IsScalarTower R Rₚ Bₚ] (f : A →ₐ[R] B),
  Function.Injective ⇑f → Function.Injective ⇑(IsLocalization.mapₐ M Rₚ Aₚ Bₚ f)
docString: null

#### measurableSet_tendsto  [theorem]  Mathlib/MeasureTheory/MeasurableSpace/MeasurablyGenerated  (docstring-multiline)
typeFull:
∀ {β : Type u_2} {γ : Type u_3} {δ : Type u_4} {x : MeasurableSpace β} [inst : MeasurableSpace γ] [Countable δ]
  {l : Filter δ} [l.IsCountablyGenerated] (l' : Filter γ) [l'.IsCountablyGenerated] [hl' : l'.IsMeasurablyGenerated]
  {f : δ → β → γ}, (∀ (i : δ), Measurable (f i)) → MeasurableSet {x | Filter.Tendsto (fun n => f n x) l l'}
docString: "The set of points for which a sequence of measurable functions converges to a given value\nis measurable. "

#### Algebra.normalizedTrace_minpoly  [theorem]  Mathlib/FieldTheory/NormalizedTrace  (docstring-multiline)
typeFull:
∀ (F : Type u_1) {K : Type u_2} [inst : Field F] [inst_1 : Field K] [inst_2 : Algebra F K] [inst_3 : CharZero F]
  [inst_4 : Algebra.IsIntegral F K] (a : K),
  (Algebra.normalizedTrace F K) a = (↑(minpoly F a).natDegree)⁻¹ • -(minpoly F a).nextCoeff
docString: "Normalized trace defined purely in terms of the degree and the next coefficient of the minimal\npolynomial. Could be an alternative definition but it is harder to work with linearity. "

#### LibraryNote.category_theory_universes  [definition]  Mathlib/CategoryTheory/Category/Basic  (docstring-with-quotes)
typeFull:
Batteries.Util.LibraryNote
docString: "The typeclass `Category C` describes morphisms associated to objects of type `C : Type u`.\n\nThe universe levels of the objects and morphisms are independent, and will often need to be\nspecified explicitly, as `Category.{v} C`.\n\nTypically any concrete example will either be a `SmallCategory`, w

#### AlgebraicGeometry.Scheme.Pullback.Triplet.tensorInr  [definition]  Mathlib/AlgebraicGeometry/PullbackCarrier  (match/let)
typeFull:
{X Y S : AlgebraicGeometry.Scheme} →
  {f : X ⟶ S} → {g : Y ⟶ S} → (T : AlgebraicGeometry.Scheme.Pullback.Triplet f g) → Y.residueField T.y ⟶ T.tensor
docString: "Given `x : X` and `y : Y` such that `f x = s = g y`, this is the\ncanonical map `κ(y) ⟶ κ(x) ⊗[κ(s)] κ(y)`. "

#### Unitization.algebraMap_eq_inl  [theorem]  Mathlib/Algebra/Algebra/Unitization  (coe⇑)
typeFull:
∀ (R : Type u_2) (A : Type u_3) [inst : CommSemiring R] [inst_1 : NonUnitalSemiring A] [inst_2 : Module R A]
  [inst_3 : IsScalarTower R A A] [inst_4 : SMulCommClass R A A], ⇑(algebraMap R (Unitization R A)) = Unitization.inl
docString: null

#### Filter.EventuallyEq.of_eventually_mem_of_forall_separating_preimage  [theorem]  Mathlib/Order/Filter/CountableSeparatingOn  (anonymous-inst-binder)
typeFull:
∀ {α : Type u_1} {β : Type u_2} {l : Filter α} [CountableInterFilter l] {f g : α → β} (p : Set β → Prop) {s : Set β}
  [HasCountableSeparatingOn β p s],
  (∀ᶠ (x : α) in l, f x ∈ s) → (∀ᶠ (x : α) in l, g x ∈ s) → (∀ (U : Set β), p U → f ⁻¹' U =ᶠ[l] g ⁻¹' U) → f =ᶠ[l] g
docString: null

```
