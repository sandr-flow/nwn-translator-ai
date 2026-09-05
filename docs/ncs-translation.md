# NCS translation and validation

The unit of selection is a string constant occurrence, identified by script and
CONSTS index, not a distinct text or an entire script. Identical text can be
spoken in one instruction and used as a variable name in another.

Selection quality has ordered priorities: prevent unwanted changes to technical
strings that can break gameplay, retain needed player text, then avoid harmless
extra translation such as diagnostics. Do not combine these into a pooled
accuracy score: removing harmless extras does not justify missing dialogue.
The corpus records change risk separately from the translate/preserve label.
Unassessed risk is explicit; reaching the model boundary is not a final change
or evidence that a script actually breaks in the game.

## Production path

1. Parse bytecode and group supported linear string concatenations into one
   translation unit with ordered `<VARn>` runtime placeholders.
2. Trace the value and its copies through local assignments, branches, known
   script calls and deferred actions to particular engine arguments. Any observed
   internal use or string comparison excludes the constant, even if another copy
   is displayed. Unknown instructions and unmodeled global state prevent proof.
   Exploration is bounded to 2048 states and 16 nested script calls.
3. Apply the shared technical-text veto. Other plausible strings become
   candidates. A nearby display call and sentence shape are hints, not proof.
4. Review every candidate through the provider's NCS gate. Only a JSON boolean
   `true` in the expected verdict object approves translation. Missing verdicts,
   malformed responses and provider failures leave the original unchanged.
5. Translate approved occurrences, then inject by item ID and original byte
   offset. A text match alone never authorizes production injection. The patcher
   checks original text, rejects duplicate offsets, adjusts jumps, reparses and
   checks jump boundaries before writing. Concat replacements must preserve
   runtime placeholders exactly once and in order.

The matching `.nss` supplies a bounded source excerpt to the gate. It cannot
prove a compiled occurrence's consumer: source can be stale, and the same literal
can occur more than once. Source text never promotes a deterministic candidate.
Other scripts' matching literals are not consulted.
There is no module-wide source classifier or source cache.

`TranslationConfig.skip_ncs_llm_gate=True` bypasses semantic model review only
for bytecode-proven display arguments. It rejects unresolved candidates and
still applies technical-text vetoes. This is not equivalent to production model
review: a display argument can contain a developer diagnostic.

The trace is deliberately incomplete. Values stored in engine variables, unknown
calls, global aliases and paths beyond the exploration budget may remain unresolved.
An observed display use can retain a candidate without proving exclusive display
use; such a candidate still requires model review. Source excerpts can help model
review, but neither excerpts nor model approval guarantee runtime visibility.
This pipeline does not execute scripts or prove all uses of shared values.

## Tests and development

Run the normal unit suite and the reviewed corpus selection suite:

```sh
pytest
python scripts/evaluate_ncs_corpus.py
pytest -m realdata tests/realdata/test_ncs_selection.py
```

Unit tests cover argument positions, nested calls, comparisons, aliases,
source mismatches, strict gate parsing and selective injection. End-to-end NCS
tests use identical text in internal and display slots, including model rejection,
and assert that unapproved bytes do not change.

The checked-in corpus at `tests/fixtures/ncs_selection/` labels every constant
in each included script, with source evidence and fixed
input hashes. It runs in the normal suite without external archives or an API.
The evaluator observes production decisions at each rejection stage and writes
per-occurrence and aggregate counts to `docs/local/ncs-corpus-report.json`.
Unknown labels remain separate. Candidates reaching the model boundary are
not counted as model approvals; live semantic review is a separate measurement.

Corpus tests run both with and without matching NSS sources. Reviewed positive
and negative examples are declared in `tests/realdata/test_ncs_selection.py`.
They pass through the real extractor, manager and injector with a deterministic
provider, and check every original constant after patching. These labels test
routing and preservation; they do not measure a live model's accuracy. A skipped
test because the corpus is missing is not evidence of coverage.

For a live model review, `scripts/run_ncs_translation_compare.py --mode batch`
uses the same extractor and manager and records source items, item-ID results
and gate diagnostics without injecting into the input archive. Do not use
`--skip-ncs-llm-gate` to assess production selection. Review both accepted and
rejected occurrences against source/bytecode; also inspect original constants
that extraction omitted to find missed dialogue. Keep one-off outputs under
`docs/local/`. Batch-versus-single agreement and reparsing alone do not establish
that the correct text was selected. New selection rules need reviewed positive
and negative examples, independent of the model's own verdicts.

## Sampling and coverage

There are three separately reported cohorts: `targeted` real scripts chosen for
specific semantic cases, `controls`, and `hash_stratified` real scripts selected
without consulting either labels or extractor output.

For the hash cohort, each pinned module contributes four scripts with 1-14
constants, four with 15-49, and two with 50-200. Within each stratum, select in
ascending `(binary SHA-256, resource name)` order. Process modules by filename
and strata from small to large. Skip binary hashes already in the targeted
corpus or selected earlier, across all modules. Matching packed NSS is required.
Every constant in a selected script is annotated, including linked-library
constants; no occurrence is sampled away. The manifest records the sampling
frame counts, quotas and exclusions. Reconstructing selection requires the
pinned archives; evaluating the included fixtures does not.

The sample excludes source-less scripts and scripts over 200 constants. It does
not claim coverage of those populations. Running its bytecode-only mode measures
the effect of withholding source for these same scripts, not accuracy on the
separate population of scripts distributed without source.

Exact binary deduplication does not remove shared library code or near-identical
scripts. Reports therefore separate modules and cohorts and give distinct text
counts per label alongside occurrence counts. Distinct texts are descriptive
coverage counts, not independent observations or a replacement for occurrence
labels. Do not interpret the pooled pass rate as production-wide accuracy.

## Annotation policy

`tests/fixtures/ncs_selection/annotations.json` is the reference, independently assigned by Codex after
reviewing source, bytecode consumers and included library references. Labels
were not generated from extractor decisions. There has been no independent
human second review. The targeted and control cohorts complement the hash
sample; none of them establishes production-wide accuracy on its own.

The unit is `(case, const_index, offset)`, not distinct text. Each occurrence has
its original text, label, role, rationale and evidence. Source line numbers are
one-based. NSS files retain their original cp1252 bytes. Engine source excerpts
include original byte ranges and hashes; the game installation is not needed.

- `translate`: linguistic player-facing content, including names, barks,
  ordinary gameplay feedback and message fragments. A linguistic plural suffix
  such as `s` belongs here even when it is appended conditionally.
- `preserve`: identifiers, lookup keys, comparison operands, resource names,
  configuration, explicit diagnostic output, empty defaults and nonlinguistic
  separators such as a standalone period. These bytes should stay unchanged.
- `unknown`: the available evidence cannot establish the downstream use, such
  as a stored value with an unobserved reader. Exclude these from correct/error
  counts and report them separately.

Labels describe the role if the code path executes; they do not prove whole
program reachability. A key remains a key even when it reads like a sentence.
Likewise, a display API alone does not make TEST_MODE diagnostics translatable.
Identical text can therefore have different labels in the same script.

### Preservation risk

Schema version 2 requires a separate `change_risk` and `risk_rationale` for each
`preserve` occurrence. `potentially_breaking` means the evidence establishes an
identifier, lookup/comparison operand, protocol delimiter or control value.
It is a risk classification, not an observed in-game failure. `harmless` requires
evidence of display/diagnostic-only use; ordinary player-visible feedback stays
`translate`, even when it resembles a debug message. Nonlinguistic display
separators can also be harmless, but delimiters parsed by code are technical.

`unassessed` means preservation is required but the consequence of changing the
occurrence has not been established. Empty defaults and working values are not
automatically harmless. Keep these apart from assessed risks, and also apart
from `unknown` selection labels. Uncertainty is not a fourth, lower priority.

## Reading experiment results

The evaluator runs the production extractor and translation manager twice:
with bytecode alone, and with the matching packed NSS. The optional extractor
trace observes the existing decisions; it does not implement another selector.
Stages are `units` (empty literals and grouping), `consumer` (argument role),
`text_filter` (technical/shape veto), `candidate` (context/sentence evidence),
and `pre_gate` (manager hard veto before the model).

For every stage, the JSON report gives newly correct and incorrect rejections,
unknown rejections, remaining labels and cumulative missed translations. Each
occurrence records its first rejection and reason. A later stage is scored only
on occurrences that actually reached it. The evaluator asserts that no reached
occurrence is silently lost or counted twice.

The `breakdowns.module` and `breakdowns.cohort` sections contain the same stage
metrics for each group, plus its script count, label counts and distinct texts
per label. Their occurrence-based metrics add up to the overall report.

Candidates at the model boundary are not approved translations or observed
in-game failures. Compare potentially breaking candidates first, missed player
text second, and harmless extras third. Keep unassessed risk and unknown labels
separate. Regression tests require no dangerous or unassessed candidates and no
missed player text in either source mode; only explicitly reviewed harmless IDs
may remain. Do not widen these allowances to make a change pass.

`groups` records reviewed linear concatenations and runtime variable positions.
Conditional appends are not assumed to form a single linear message. Counts
always expand translation units back to their original constant occurrences.

## Adding an experiment or regression

1. Record the baseline with `python scripts/evaluate_ncs_corpus.py --out docs/local/ncs-before.json`. Keep subsequent results under a different name.
2. Investigate each error using the constant's offset, source consumers and
   bytecode. Add a positive case and a technical-use counterexample, including
   repeated text or shared-value uses when applicable. Never generate expected
   labels from extractor or model decisions.
3. For corpus additions, record archive provenance, encoding, input hashes and
   the complete constant inventory in `manifest.json`. Annotate every constant
   with evidence, rationale and risk as specified above. Keep hand-assembled
   controls separate from compiler-produced real scripts. Changing existing gold
   requires re-reviewing its evidence; changing bytes requires new hashes and a
   complete inventory. Source files retain their original cp1252 bytes.
4. Run focused regression tests, `pytest tests/test_ncs_corpus.py`, and the
   relevant realdata selection tests. Compare per-stage counts, occurrence IDs,
   modules and cohorts in both modes. Check that better counts do not merely move
   an error to a later stage. For injection changes, check every original
   constant after patching; reparsing alone is insufficient.
5. Run `pytest`, `mypy src` and `black --check src tests` before integration.
   A skipped realdata suite is not evidence of coverage. Record model/config and
   actual verdicts separately if evaluating the live gate. Keep reports and
   one-off measurements in ignored `docs/local/`, not in this document.

The corpus excludes source-less distributions and very large scripts; prioritize
these populations when extending coverage. Shared engine storage and globals,
unknown wrappers, and diagnostic-only output also need independent evidence.
Successful selection on this corpus does not establish production-wide accuracy.
