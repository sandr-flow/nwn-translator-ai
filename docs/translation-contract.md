# Translation identity and terminology

Every result is addressed by `(resource filename, item_id)`. Archive resources
are flat; an item ID is local to its resource. Identical source strings remain
independent fields, including different dialogue nodes and NPCs with the same
Tag. Failure status uses the same address.

Extractors attach the selected GFF field's record offset to each item. The shared
GFF injector applies addressed answers to those records; NCS uses its specialized
bytecode patcher. Rebuild re-extracts the current files before applying editor
changes, because resizing strings invalidates old offsets. Descriptions with
equal text remain separate occurrences.

## Request reuse

The ordinary translation manager may reuse an answer only when text, context,
provider hint, content profile and relevant terminology match. It explicitly
distributes that answer to the participating addresses. It does not fold case
or punctuation, strip numbers, match name prefixes or seed answers from glossary
dictionary forms. Contextual dialogue nodes are independent; retrying a partial
group response carries the already accepted node addresses explicitly.

NPC name context contains the actual `FirstName`, `LastName` and gender when
available. Fields belonging to the same NPC are requested together. Translated
words are never split to manufacture translations of source name parts.

## Terminology

The glossary stores canonical translations and explicit source aliases. Candidate
curation can retain in-world abbreviations and recurring terms while excluding
engine identifiers. Alias targets must resolve to retained candidates; missing
targets and cycles are reported and excluded. Source spellings are preserved.

Matching uses complete source forms and explicit alias relations. A shared word
does not establish an alias. All matched forms survive prompt rendering, even
when several forms have the same translation. Project terms from
`race_dictionary.py` take precedence over a conflicting generated entry.

Requests are split when their terminology exceeds the batch budget, rather than
discarding required entries. A single item or linked name pair retains its full
terminology even if it alone exceeds that budget. An unchanged proper name or
abbreviation is a valid model answer. Inflection and deliberate wordplay remain
contextual translation decisions.

## Script context and diagnostics

Only gate-approved NCS strings contribute neighboring speech context. The
translator also includes matching NSS excerpts when available. Constant order
is explicitly not treated as proven execution order. Context does not authorize
additional bytecode patches, and retries retain context and terminology.

Approved NCS messages up to 1000 sanitized characters are batched across resources,
including multiline messages. Each item retains its own context and address;
batch order does not imply a shared conversation. Batches are bounded by item
count, text plus context size, and required terminology. Longer messages and
failed batch items use individual requests.

Logical model requests and responses carry a shared request ID in the local
trace, with participant addresses, context, terminology and output. Reuse events
identify their representative occurrence. A terminology snapshot records curation
decisions and alias relations. For web tasks, events are stored in
`workspace/web/<task>/translation_trace.jsonl`; editor rows stay in SQLite. Library
JSONL logs can contain both translation rows and diagnostic events, distinguished
by the `event` field. Provider credentials are not part of these records.

Stage translation artifacts use version 2 with addressed items. Text-only
translation artifacts must be regenerated; they cannot reliably identify fields.
Glossary version 2 stores entries and aliases and can also read legacy entry-only
glossaries.

Structural success means the response was accepted and its tokens validated. It
does not certify meaning, grammar or verse quality. See the tests under
`tests/realdata/` for archive checks and `docs/local/` for local audit measurements.
