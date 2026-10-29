# Claim protocol v0.1

Status: **specified and implemented** in `crates/gnomon-core` (Rust) with
Python bindings. Part of Gnomon.

## 1. The log

The log is the only source of truth. It is a file of events, one per line,
each line the RFC 8785 canonical JSON of the event's envelope followed by
`\n`. Anything not in canonical form is treated as tampering. A final line
without `\n` is a torn write and is reported, never silently dropped.

### Envelope

| Field | Type | Rule |
| --- | --- | --- |
| `seq` | integer | 0, 1, 2 … position in the log |
| `event_id` | string | exactly `<writer>/<writer_seq>` |
| `writer` | identifier | a registered writer |
| `writer_seq` | integer | exactly the writer's previous `writer_seq` + 1 (no reuse, no gaps) |
| `record_time` | timestamp | `YYYY-MM-DDTHH:MM:SSZ`; never earlier than the previous event; a fresh write is refused (`E_FUTURE`) if more than 300 seconds ahead of the gate's clock (replay is not checked against the clock) |
| `prev` | hex | `hash` of the previous event; 64 zeros for the first |
| `kind` | string | one of the event kinds below |
| `body` | object | validated per kind; unknown fields are refused |
| `hash` | hex | SHA-256 of the canonical envelope without `hash` |

Identifiers are 1–32 characters of `a-z`, `0-9` and `-`, starting with a
letter or digit. They cannot contain `/`, so event ids from different
writers can never collide.

### JSON subset

Integers only, within ±(2^53 − 1). Fractions, exponents, `-0`, NaN and
infinities are refused, never rounded. Duplicate keys, lone surrogates and
raw control characters are refused. Nesting is limited to 64 levels.
Confidence is expressed as integer basis points (0–10000).

## 2. Event kinds

| Kind | Who may write it | What it does |
| --- | --- | --- |
| `register` | the owner (and, at genesis, the owner registering itself); or a writer with `spawn`, for a new instance of itself (section 2a) | adds a writer with a party, kind, capabilities, compartments and identity |
| `episode` | `write` | records raw material whole: a transcript, document or tool output, optionally binding its speakers' names to writers |
| `claim` | `write`; in the start-packet frames, only the writers section 13 allows (from version 4) | records a claim; identical content from another writer is corroboration |
| `verdict` | `verify`, never a writer or party that asserted the claim; one verdict per party | supports, refutes or abstains on a claim |
| `supersede` | `write`; both claims in one compartment; neither in an open dispute | links an old claim to a new one: refuted, narrowed, generalised or contested |
| `resolve` | to rule: `verify`, never a party with a stake; to withdraw: `write`, the party that raised it | ends a contest (`old`, `new`, `both`) or an appeal (`reinstated`, `upheld`); either may be `withdrawn` |
| `appeal` | `write`; one open appeal per claim; once per party per claim | asks a neutral party to reinstate a refuted or defeated claim, citing outside support |
| `schema_change` | `govern` | advances the schema version by exactly one |
| `revoke` | the owner; from version 5; never against an owner | ends a writer's access to named compartments, for it and every instance started from it; reads and writes there are refused from then on, and what it wrote stays |
| `lease` | `schedule` (or the owner); from version 6; never to oneself or an owner; one live lease per frame | gives a writer a hold on a frame until `expires` |
| `release` | the lease's holder, whoever granted it, or the owner | ends a lease early |

The `owner` capability implies the others; `schedule` (version 6) grants leases.

## 2a. Identity (M6)

A writer is one acting identity: one person, or one running instance of a
model or tool. Two sessions or agents on the same model are two writers,
because they say different things. Three things are kept apart:

* **The instance** (the writer): who said or wrote each thing.
* **The model** it runs, as its runtime reports it. Instances of one model
  share weights and so share mistakes: their agreement is not independent.
* **The party** that answers for it: who has a stake in a dispute.

**Versions.** These rules are protocol version 2. A memory's genesis
registration may say `"schema_version": 2` (the Python package and command
line always do for new memories); otherwise it is at version 1, and its
log replays under the rules it was written with: models and tools may omit
`model` and `operator`, and a claim's speaker counts as the writer's own if
it matches the writer's id or party in any letter case. An owner moves a
version 1 memory to version 2 with a `schema_change`; earlier events keep
their meaning.

`register` carries the identity:

| Field | For | Meaning |
| --- | --- | --- |
| `name` | anyone, optional | how to address it ("John Doe", "Claude"); no other party's writer may hold the same name |
| `model` | models: required; tools: optional | the model it runs as reported by its runtime (not verified), or a tool's version |
| `operator` | models and tools: required | the registered human it acts for |
| `surface` | models and tools, optional | where it runs: claude-code, claude-app, api… |
| `session` | models and tools, optional | the runtime's id for this session or agent; one writer per party, session and surface (`E_IDENTITY`) |

Models, operators and sessions are printable ASCII without spaces; names
follow the participant-name rules (no look-alike scripts, combining marks,
or hidden characters). A human has none of `model`, `operator`, `surface`
or `session`, and cannot have `spawn`.

**Instances.** A writer with `spawn` may register a new instance of itself
(`E_SPAWN` otherwise): same kind, model, party, operator and name, and its
surface if it has one; an id that starts with the parent's id and a hyphen
(`claude-3` for `claude`); only compartments the parent is in; only
capabilities the parent has, never `owner`, `govern` or `spawn`. So an
instance can never take another identity or more power, and cannot probe
for ids outside its parent's namespace. The MCP server uses this
(`--per-session`) so that every session gets its own writer, however the
host is configured; a session that resumes keeps its writer.

**Names in recordings.** An episode may bind participant names to writers
in its compartment (`"bindings": {"Claude": "claude-3"}`): this is the
recorder's account of who each name is. A recorder may bind only its own
id or registered name to itself: binding "John" to yourself would make
John's words yours. A name without a binding is only a name: "Claude" in a
transcript is not any particular Claude, and "John" is not the writer
`john`. Binding to a writer that does not exist and to one outside the
compartment are refused alike, so neither reveals the other.

**Who a claim's speaker is** is settled when the claim is first recorded.
Quoting an episode: the writer the name is bound to there, or nobody. Not
quoting: a writer id given as the speaker, or the author's own registered
name. Nothing later changes it: a writer registered afterwards under a
matching id, or another writer repeating the claim. This decides the
hearsay rules (section 4): only the writer itself speaks in its own voice;
not another session of the same model, not its party in general, and not
a writer repeating a claim under a shared name.

**Independence.** Each writer has a group: `model:<model>` for models and
`tool:<version>` for versioned tools (both in lower case), its own id
otherwise, and for an instance always its parent's group, so starting
instances never makes agreement look independent. A claim's
`independent_groups` (in state) and the `asserted_group` relation list the
groups that asserted it; `independently_asserted(C)` holds when two groups
did. Two sessions of one model agreeing is one group. Independence is
*as reported*, not verified: the model string is what the runtime says and
an owner could register one model under two names; a person and an agent
acting for them count as two groups. Treat it as a floor on correlation,
never as proof of independence.

**Titles.** `gnomon.title()` names a writer so that it cannot be mistaken
for another: name, model, surface, session, id, and who it acts for, e.g.
`Claude (claude-opus-5-5, claude-code, session 7f3a) [claude-3] for John
Doe [john]`.

Queries see identity through `identity(W, Key, Value)` (keys: name, model,
operator, surface, session), `group(W, G)`, `instance_of(W, Parent)`,
`speaks_as(C, W)` and `bound(Episode, Name, W)`, with views
`asserted_group`, `independently_asserted`, `same_model` and `acts_for`.
`group`, `instance_of` and `speaks_as` cite every event they rest on, and
the independent checker re-derives them from the log. A claim's
`speaks_as` is shown only when the reader can see that writer.

## 3. Claims

A claim's id is `c:` followed by the SHA-256 of its canonical body, so the
id is the content.

| Field | Required | Values |
| --- | --- | --- |
| `statement` | yes | text; no invisible or reordering characters |
| `statement_kind` | yes | describe, promise, permit, command, ask, imagine |
| `origin` | yes | direct, manual, observed, forecast, derived, inferred, guessed |
| `stance` | yes | asserted, decided, suggested, hypothetical, asked |
| `speaker` | yes | who said it (may differ from the writer who recorded it) |
| `truth` | yes | true, false, contested, unknown |
| `confidence` | yes | integer 0–10000 |
| `frame` | yes | the context the claim belongs to |
| `compartment` | yes | identifier; the writer must have access |
| `validity_domain` | no | where the claim holds |
| `valid_from`, `valid_to` | no | timestamps: when the claim is true in the world |
| `source_episode` | no | an episode in the same compartment |
| `quote`, `quote_start` | no, together, only with `source_episode` | the exact passage of the episode the claim came from, and where it starts (in Unicode code points); the gate checks it (`E_QUOTE`) |
| `intended`, `delivered`, `observed` | no | kept separate, never collapsed |
| `about` | no | 1–32 references to earlier claims, each `{claim, relation}` with relation answers, critiques, replies or cites (section 12) |

Rules: a guessed claim cannot carry full confidence; a question (`asked`)
must have truth `unknown`. Because `stance` is part of the content, a
suggestion and a decision with the same words are different claims.

Recording a claim that already exists (identical content) is
**corroboration**: the original author is kept and the corroborating
writer's party joins the claim's parties. A party that has given a verdict
on a claim cannot then corroborate it.

### Two clocks

`valid_from`/`valid_to` say when a claim is true in the world.
`record_time` says when the log learned it. "As of" queries replay the log
up to a record time, so they return exactly what was known then.

## 4. Verification, supersession and disputes

### Verification

* No self-certification: a verdict's writer can't have asserted the claim
  (as author or corroborator) or belong to any party that did. Each party
  gives at most one verdict per claim.
* A party that gave a verdict on a claim cannot then corroborate it.

### Supersession (evidence)

* `refuted` needs a refuting verdict on the old claim as evidence, and the
  refuter's party must have asserted the new claim (consent: nobody's claim
  is conscripted as the opposing side). If the old
  claim was in a ruled dispute, that verdict must come from a party outside
  the dispute. Refutation is open to anyone, including the claim's own party
  (a retraction), because the evidence always comes from someone else.
* `narrowed` needs the new claim to state its `validity_domain`; the old
  claim stays true within its domain. `generalised` needs evidence.
* A supersession and its evidence stay within one compartment.
* Non-contest supersessions are recorded once per (old, new, kind).

### Disputes (argument)

A `contested` supersession opens a **dispute about a pair of claims**.

1. **Two real sides, both consenting.** The challenger's party must have
   asserted the new claim, cannot have asserted the old one, and the two
   claims cannot share a party. Nobody is drawn into a dispute unasked.
2. **Defeated claims don't argue their way back.** A claim that was refuted,
   or lost a ruling, cannot enter a contest on either side. Contests are
   argument, not evidence. A defeated claim returns only by appeal on
   evidence, or as a new claim (see "Three ways back").
3. **A contest is a flag, not a verdict.** Opening one leaves both claims'
   truth unchanged and marks them `disputed`, so contesting many claims at
   once changes nothing but flags. Only a ruling or a refutation changes truth.
4. **Parties are frozen at opening:** the challenger's party and every party
   that asserted either claim. While the dispute is open, neither claim can
   be corroborated or superseded, so no one can join a side or jam it, with
   one exception: an evidence-backed refutation of either claim is allowed
   and ends the dispute, with the refuted claim's side losing. Entering a
   dispute never shields a claim from evidence. The refuting verdict must
   come from outside the open dispute's parties (no party wins a contest with
   its own verdict), and the refuter does not thereby become a judge.
5. **Someone must be able to end it.** A dispute can only open if a
   registered writer exists who has `verify`, can see the compartment and is
   not a party (`E_NO_RESOLVER` otherwise).
6. **Resolution.** A writer with `verify` outside the frozen parties, and
   that has not given a verdict on either claim, rules
   `old`, `new` or `both`. The challenger's party may instead withdraw (needs
   only `write`). A withdrawal is not a ruling, but it is final for the party
   that withdrew: it cannot raise the same pair again (no withdraw-and-reraise
   loops). Another consenting party can.
7. **One argument per party per claim.** A party that challenged a claim in
   a ruled contest, refuted it, gave a refuting verdict on it, or ruled on it
   cannot contest it again, whatever the rewording. From then on it can only
   challenge the claim with evidence (a refutation). Parties that only
   defended against it keep their argument, and every party remains free to
   act on other claims.
9. **A last-resort judge.** If no ordinary neutral judge remains (for
   example because every neutral party has already given a verdict), a
   writer with `govern` whose party is neither an advocate nor an adversary
   may decide. This keeps every contest and appeal decidable unless the
   owner itself has a stake, in which case the challenger or appellant can
   still withdraw.
8. **Resolvers stay neutral.** A party that ruled on a dispute cannot then
   corroborate either claim, and counts as interested: its verdicts cannot
   be the evidence that refutes a claim it ruled on.

### Three ways back for a defeated claim

Different situations call for different routes, so all three exist:

| Route | When it fits | How |
| --- | --- | --- |
| **Appeal on new evidence** | Something changed: a new test, source or check supports the claim | `appeal`, citing a supporting verdict given **after** the defeat by a party with no stake |
| **Appeal on the balance of verdicts** | The claim was outvoted by a single refutation despite wider support | `appeal`, allowed when more parties support the claim than refute it, citing any outside supporting verdict |
| **Restate as a new claim** | The claim was partly wrong and needs rewording or a narrower scope | record a new claim; it starts fresh, and can cite the old one |

Appeal rules:

* Only a claim that is currently refuted or defeated can be appealed, one
  appeal at a time, and each party may appeal a given claim once.
* **Opponents cannot appeal:** its refuters, every party that gave a
  refuting verdict, and the current parties of every claim set against it.
  (This stops sham appeals that block the real one and use up its evidence.)
* **Stake:** the appellant, the claim's parties, everyone interested in its
  earlier disputes and appeals, and its opponents. Evidence must come from
  outside the stake. The decider must be outside the stake, must not have
  given the evidence or any other verdict on the claim, and is checked
  against the claim's opponents as they are when it decides (so allies who
  join the other side later cannot decide).
* A neutral writer with `verify` decides `reinstated` (truth returns to the
  asserted truth) or `upheld` (it stays defeated). The appellant's party may
  withdraw instead, which uses up its one appeal. An appeal can only be filed
  if a neutral decider exists.
* While an appeal is open the claim is `disputed` and cannot be corroborated
  or superseded.
* **After a reinstatement**, refuting the claim again needs a verdict given
  after the reinstatement, from a party outside the appeal's stake and its
  decider. Old refutations cannot simply be replayed.
* **The balance of verdicts never decides truth by itself.** It only earns a
  hearing. Parties are labels assigned by the owner (see trust assumptions),
  so counting them would let whoever registers the most parties win.

### Effective truth

Starts from the asserted truth and folds, in log order, every supersession
and resolution touching the claim; later events win:

| Event | Effect |
| --- | --- |
| refuted (claim is old) | false |
| contest or appeal opened | no change; the claim shows `disputed: true` |
| ruling | winner returns to asserted truth, loser false; `both` returns both |
| appeal reinstated | asserted truth |
| appeal upheld, any withdrawal | no change |
| narrowed, generalised | no change (the old claim holds within its domain) |

The state view also shows, for every claim, `disputed`, `defeated` and its
`balance` (number of parties supporting and refuting).

A claim reinstated on appeal and the claim that originally beat it can both
be true: the appeal decides the appealed claim, not its rival. Each can still
be refuted with fresh outside evidence.

### Trust assumptions

* **Parties are labels assigned by the owner.** Every neutrality rule above
  is only as strong as the owner's honesty: an owner can register a "neutral"
  party and use it. Signed identities and external parties are needed before
  neutrality can be trusted between owners (planned with multi-writer work).
* **A party acts as one.** Any identity of a party, including one registered
  later, can act for it (for example, withdraw its challenge).

### Open design questions (status: research programme)

These are trade-offs between finality and liveness, not bugs:

* **A judge may rule on a claim more than once.** The party that reinstated
  a claim on appeal can later rule against it in a contest brought by a fresh
  challenger. Each kill still needs a challenger who has not argued about the
  claim before.
* **Neutral parties run out.** Every kill and every revival needs a party
  with no stake. With a fixed set of parties, long fights end when the
  neutral parties are used up. This is inherent to any system that relies on
  neutral judges; it is bounded by how many parties exist.
* **Appeal churn.** Different parties can each file and withdraw one appeal
  on a claim, keeping it flagged as disputed. A neutral decider can end any
  open appeal, so a claim is never stuck for everyone.

* **Mass contests.** Now mitigated: contests only set a flag. One party can
  still flag many claims at once; rate limits may follow.
* **Revocation.** Writers cannot yet be removed. When they can, the
  "a neutral resolver exists" check must be re-run, or the owner given an
  override.

### Episodes and turns

An episode may record its **turns**: a list of `{speaker, start, end}` (code
points, end exclusive) saying who said which part of the content. Turns must
be non-empty, in order, not overlapping, within the content, and every
speaker must be one of the episode's `participants`. The content itself is
always kept whole; turns only index it.

Participant names must be told apart at a glance: no line breaks or tabs, no
two names that differ only in letter case (including full case folds such as
"Straße"/"STRASSE"), no combining marks (write "Zoë", not "Zoe" + U+0308), and
no mixing of Latin, Greek and Cyrillic letters in one name ("Аlex" with a
Cyrillic А), and no leading or trailing spaces. The Python binding also
requires NFC. Known limit: the core has no Unicode tables (it has no
dependencies), so rarer look-alikes (kana or Hangul written in decomposed
form, fullwidth or Cherokee letters) pass the core's check. Every quoted claim
must still name the exact turn speaker, so this can confuse a reader, never
the gate.

Turns are the recorder's attribution, made once, when the episode is
recorded; every later claim is held to them. The state view shows, for every
claim that cites a source, who recorded that source (`source_recorded_by`),
so a reader can always see whose attribution a quote rests on.

### Quotes

A claim that carries a `quote` is held to its source episode on three
counts, each refused with `E_QUOTE`:

1. **The words.** The episode's text, starting at `quote_start` code points
   in, must be the quote, character for character.
2. **The speaker.** The quote must lie wholly inside one turn whose speaker
   is the claim's `speaker`. An episode without turns can only be quoted if
   it has exactly one participant, who is then the speaker (a document and
   its author). Text outside every turn (speaker labels, preambles) is
   nobody's words.
3. **The statement.** Origin `direct` means the speaker's own words,
   verbatim: a direct claim must carry a quote, and must state exactly its
   quote. Paraphrase is allowed under another origin (`inferred`, `derived`),
   so a reader always knows when words are not the speaker's own, and a
   paraphrase can never carry confidence above 7000: it is an
   interpretation.

So whatever produced the claim (a rule, a model, a person), it cannot record
words that are not in the source, or put one speaker's words in another's
mouth. A quote may not contain hidden characters even when the episode does
(episodes keep them; claims never carry them). A hidden or missing episode
gives `E_REF` whatever the quote says.

### Hearsay

A claim whose speaker is not its writer (section 2a: not another session of
the same model, not its party, not a name that merely looks like it), and
that does not quote them (origin anything but `direct`), is the writer's
account of what someone else said. However it is labelled, it cannot
carry confidence above 7000 (`E_ATTRIBUTION`). Quoting is the only way to put
someone's words on record with certainty, and the gate checks the quote.

A quote is only as good as its recording. Quoting someone else from an
episode recorded by the writer's own party is still that party's account of
what was said (it chose the words and the turns), so it is capped at 7000 too.
A quote from an episode another party recorded is not.

### Verdict methods

`test`, `cross_model_review`, `external_source`, `scored_prediction`,
`human`, and `computed` (worked out exactly by a native tool: arithmetic,
calendar, and later solvers and proof checkers).

## 5. Compartments

Every episode and claim lives in one compartment. Writers can only write,
verify, supersede, resolve or cite within compartments they were registered
for. Content in a compartment a writer cannot access gets exactly the same
error as content that does not exist (`E_REF`), so error codes never confirm
that hidden content exists.

Reads can be restricted to a set of compartments. A restricted view contains
nothing from other compartments: no claims, episodes, supersessions or
event ids, and writers are listed only with the restricted compartments and
without `last_seq`. Its digest covers only what it shows, and it omits the
log's event count and head hash, which describe the whole log.

Known limit: event ids are `<writer>/<writer_seq>`, so gaps in a writer's
visible sequence reveal that it wrote hidden events (not what they were).

**Revocation (protocol version 5).** Access granted by mistake, or to a
writer that later misbehaves, is withdrawn with `revoke` (`writer`,
`compartments`, `reason`). Only the owner may revoke, never another owner's
access (that could lock the record), and only compartments the writer holds
(`E_REVOKE` otherwise). The writer's instances lose the same compartments.
Visibility follows a writer's current compartments, so its reads stop with
its writes; everything it wrote before stays on record and keeps its
compartment. There is no re-grant yet: give access again by registering a
new writer.

**Leases (protocol version 6).** A run is a short-lived writer with a
grant, and the gate enforces the grant's deadline and who holds the work.
`lease` (`writer`, `frame`, `expires`, `reason`) gives a writer a hold on a
frame; its event id is the lease's fencing token. While a lease is live (not
released, and `expires` later than the event's record time) only its holder,
the holder's instances or the owner write claims and handoffs in that frame,
and the holder names the lease in each (`lease`); naming a released or
expired lease is refused (`E_LEASE`), so a stale process still holding an old
token is fenced off (Kleppmann 2016). A second lease on a frame with a live
one is refused (`E_LEASED`). A fresh write naming a lease is also checked
against the gate's clock, so it cannot be dated back to before expiry; replay
uses record time only and stays deterministic. When a lease ends the frame is
free again: if a scheduler crashes, its leases still expire. The `lease`
field is left out of a claim's id, so the same statement under two leases is
one claim. Budgets are the engine's business, not the gate's.

## 6. Hidden channels

Refused in every structured field: every Unicode format character
(general category Cf, which covers zero-width characters, bidirectional
controls, the BOM, the soft hyphen and the tag block), noncharacters,
private-use characters, control characters other than newline and tab,
and other invisible or smuggling-capable characters (variation selectors,
Hangul fillers, the combining grapheme joiner, the braille blank, the object
replacement character, line and paragraph separators).
Episodes keep raw content exactly as received and are flagged when such
characters are present.

Timestamps must be real calendar times (month lengths and leap years are
checked).

## 7. Integrity checks

* Replaying the log rebuilds state; equal state has an equal digest.
* Any changed byte, reordered line, or line removed from before the end
  breaks verification.
* Lines removed from the end leave a shorter valid chain; only checkpoints
  (`count`, `head`) detect that truncation.
* A missing log is `E_NOT_FOUND`, never an empty valid log.
* Verification with a budget smaller than the log reports *exhausted*, never *ok*.
* Duplicate object keys are refused everywhere, including in values built in
  code rather than parsed.

## 7a. Concurrency and caching

Every operation locks the log file itself (shared for reads, exclusive for
appends) across its whole read-check-write, so concurrent writers are
serialised, including writers reaching the same file through symlinks or
other spellings of its path. There is no separate lock file. An empty file is
treated exactly like a missing one. A verified log is cached in memory with the exact bytes it was
verified from; every operation re-reads the file and compares it byte for
byte before reusing the cache, so any change forces full re-verification.
The cache is bounded (16 logs, 256 MB).

## 8. Ingestion (M2)

The computer does what it can do exactly; a model is the last resort.

1. **Record who said what, once.** Structured messages (chat exports, APIs)
   are recorded with their turns straight from the source, with no parsing.
   A plain-text transcript is split into turns at labelled lines ("John:",
   "**John:**", "[10:31] John:", or the name on its own line), on every
   Unicode line break. Only participants become speakers; any other label
   ("Bob:", "Step 1:") ends the turn before it and starts text with no known
   speaker, so it is never absorbed into someone else's turn. A document has one author and needs no turns. The turns
   are stored in the episode, and extraction always reads them from there.
   Plain text has one unavoidable ambiguity: a quoted "John: …" line inside
   someone else's message looks like John speaking, so structured input is
   preferred whenever it exists.
2. **Split sentences exactly.** Abbreviations, initials and decimals do not
   end sentences; every offset is kept. The same input always gives the same
   output.
3. **Classify by named rules.** Each sentence's stance (decided, suggested,
   hypothetical, asked, asserted), kind (describe, promise, imagine, ask),
   truth and confidence come from visible language cues, and the rule that
   fired is reported. A sentence opening with a condition is hypothetical as
   a whole. A first-person commitment ("I will…", "we're going to…", "I
   promise…") is a promise, and any other statement about the future ("prices
   will double") a forecast: both have truth `unknown`, since neither is true
   or false yet.
4. **Residue, not guesses.** Text between recorded turns is reported too
   (unless it is only a speaker label). A sentence goes back unrecorded, with
   its reason, if it: has no known speaker; holds hidden characters or a speaker
   label; is too short or only makes sense with what came before (opens with
   a pronoun, "all three", "I agree", "…then"); reports someone else's words
   ("Bob claims…"); has a condition after its start ("…only if…", and for
   decisions and promises also "…when…"); reads as
   a question without a question mark; mixes decision and hedge cues; or is
   a decision that is negated or undecided ("we haven't decided whether…")
   or not a decision at all ("let's see", "let us know"). A contrast after a
   decision ("we will use Rust, not Go") is not a negation.
5. **A judge for the residue only.** An optional judge (a model or a person)
   may classify residue. It only chooses the classification of the sentence
   it was asked about; the words, speaker and quote stay the original. Its
   claims are recorded with origin `inferred` and confidence capped at 7000.
6. **Only what the writer may see.** Extraction reads the episode through
   the extracting writer's compartments.
7. **Everything quoted.** Every extracted claim is the speaker's sentence,
   verbatim, with `quote` and `quote_start`, origin `direct`, so the core
   checks it against the episode.

### Native checkers

Checkers run as their own registered writers and parties (kind `tool`) and
give `computed` verdicts through the same gate as anyone else.

* **Arithmetic:** `<expression> = <number>` (or `≠`) with + − × ÷ ^ and
  brackets, evaluated exactly with fractions (so 0.1 + 0.2 = 0.3 holds).
  Expressions with letters are not arithmetic claims, and a hyphen between
  digits with no spaces ("2026-10-14", "10-12") is a date, range or score as
  often as a minus, so it is not checked. Division by zero, huge exponents
  or results, over-long expressions and more than 50 equations in one
  statement are skipped, never guessed. So are notations that read two ways:
  "-2^2" and "2^3^2" (conventions differ). A statement that is wrong as
  written is left alone only if another common reading makes it true: "4!"
  as a factorial, "1.000" as one thousand, or an answer rounded half up or
  half even ("1/8 = 0.13"), or cut off at three or more decimals
  ("2/3 = 0.666"). Wrong under every reading, it is refuted. The scan is linear, so no input can make it
  slow.
* **Calendar:** "<date> is/was a <weekday>" in ISO, "14 October 2026" and
  "October 14, 2026" forms (ASCII letters and digits only), checked against
  the real calendar; a date that does not exist refutes the claim. Dates
  before 1923 are not checked, since their weekday depends on which calendar
  (Julian or Gregorian) was meant.

A checker **supports** or **refutes** only if what it checked is the whole
statement; if the statement says more ("it is not true that…", "…if 2 + 2 =
5"), the surrounding words could reverse it, so it **abstains**, with a note
saying exactly what it computed. Only claims asserted `true` or `false` are
checked, and the result is judged against what the claim asserts: a claim
that "2 + 2 = 5" is false is supported. Questions, suggestions,
hypotheticals and claims of unknown or contested truth are skipped. A checker reads only its own
compartments, and one claim that fails to check is reported and never stops
the rest.

## 9. Queries (M3)

Queries are written in Datalog: rules over facts, with no function symbols,
so every query terminates and its answers do not depend on the order rules
are written in. The engine is part of the core (`datalog.rs`, no
dependencies).

### Language

```text
path(X, Y) :- edge(X, Y).            % a rule
path(X, Z) :- path(X, Y), edge(Y, Z). % recursion
sink(X) :- node(X), not edge(X, _).  % negation
edge("a", "b").                      % a fact
?- path("a", Y), Y != "a".           % the query: exactly one per program
```

* **Terms.** Variables start with an upper-case letter or `_`; `_` alone
  matches anything. Variables whose name starts with `_` (`_`, `_X`) are not
  reported as answer columns, though a named one like `_X` still links its
  occurrences. Constants are `"strings"` (escapes
  `\" \\ \n \t \r \u{hex}`), 64-bit integers, or bare lower-case words
  (`decided` is the string "decided").
* **Literals.** `p(...)`, `not p(...)`, comparisons `= != < <= > >=`, and
  `contains(S, Sub)` (substring, ignoring the case of ASCII letters only, so
  the answer is the same on every platform and Unicode version: "CAFÉ"
  contains "caf" but not "café"). Integers compare as
  numbers and strings by code point; values of different types are never
  equal and never ordered, so `1 = "1"` is false.
* **Left to right.** A variable must be bound by a positive literal to its
  left before a negation, comparison or `contains` uses it; `X = c` may bind
  `X`. Every head variable must be bound. Violations are errors with a
  reason, not silent emptiness.
* **Stratified negation.** A relation may not depend on its own negation
  (`p :- not q. q :- not p.` has no single meaning and is refused).
* **No silent typos.** An unknown relation, a wrong number of arguments, or
  a rule for a base relation or built-in view is an error (`E_QUERY`, with
  the line).
* **Limits.** Programs are capped at 100 KB, 10,000 clauses, 32 literals per
  body and 16 arguments; evaluation at 500,000 facts, 20,000,000 steps (join
  work, index building, `contains` by length, storing facts) and 10,000,000
  values held in facts and indexes; results at 10,000 answers (flagged
  `truncated`) and 64 MB of answers and proofs. Exceeding a limit is
  `E_QUERY_LIMIT`, never a partial answer. `not` applies to one atom, once.
* **Only what is needed.** Base relations the query cannot reach through
  its rules are not loaded; they cannot change an answer.

The query's named variables, in order of first appearance, are the answer's
columns (`variables`). Answers are sorted.

### The memory as facts

`Memory.query(reader, program, as_of=None)` runs a program over the log as
of `as_of`, through the compartments of `reader` (a registered writer;
unknown readers are `E_REF`). Base relations:

| Relation | From |
| --- | --- |
| `claim(C)` | the claim's first event |
| `statement`, `speaker`, `frame`, `compartment`, `stance`, `origin`, `statement_kind`, `asserted_truth`, `source`, `validity_domain`, `valid_from`, `valid_to` `(C, Value)` | the claim's fields (strings), from its first event |
| `confidence(C, N)`, `quote_start(C, N)` | integer fields |
| `recorded(C, Time)`, `author(C, Writer)` | the first event's time and writer |
| `asserted_by(C, Writer)` | every event that asserted it (author and corroborators) |
| `truth(C, T)` | effective truth (section 4), citing the events that decide it: the claim's first event if nothing changed it; the refutation; or the contest and the ruling or refutation that ended it (plus the first event if the claim won); or the appeal, its reinstatement and the first event |
| `disputed(C)` | each open contest or appeal involving it |
| `defeated(C)` | the refutation, or the contest and the event that ended it against the claim |
| `verdict(V, C, Writer, Result, Method)` | the verdict event `V` |
| `supersession(S, Old, New, Kind, Writer)`, `evidence(S_or_A, Item)` | the supersession or appeal event |
| `resolution(D, Outcome, R)` | the dispute or appeal `D` together with the event `R` that ended it (a ruling, a withdrawal, or a refutation of one side) |
| `appeal(A, C, Writer)` | the appeal event |
| `episode(E, Writer, Medium)`, `participant(E, Name)` | the episode event |
| `writer(W, Party, Kind)`, `capability(W, Cap)`, `member(W, Compartment)` | the registration; only writers sharing a compartment with the reader, and only the shared compartments |

Built-in views are plain Datalog over these (`gnomon.VIEWS`):
`believed(C)` (true and not disputed), `rejected(C)` (false),
`decision(C)` (a decided claim that is believed), `supported(C)`,
`challenged(C)`, `asserted_by_party(C, P)`, `refined(Old, New)` (narrowed or
generalised). They may be used but not redefined.

### Proofs

Every answer has a proof: a finite, acyclic tree of `nodes`. A derived node
gives the fact, the id of its rule (the rule's text and origin, `view` or
`line N`, appear once in `rules`), its premises (one per positive literal,
in order) and the atoms it found absent (one per negation; `_` shown as
null). A base node gives the fact and the events it was read from, each as
`event`, `kind` and `hash`.

Absence is judged against the same view: the log as of `as_of`, through the
reader's compartments. The result's `through` names the last event of that
view (`event`, `kind`, `hash`, `time`), so a proof stays checkable as the
log grows: every event that can bear on a visible fact is at or before it.
The result's `as_of` always states the moment it describes: the one asked
for, or else the time of `through`. A proof is a claim about that moment, so
the checker rejects one whose moment includes, after `through`, any event
bearing on what it cites (a past view relabelled as a later one). Sources
never give an event's position, and results carry no event count or head
hash, since either would reveal how many events the reader cannot see.

`gnomon.proofs.check` re-checks proofs without the core: its own parser and
printer, its own canonical JSON and SHA-256, and the log file read directly.
For each derived node it confirms the rule is in the program or views, that
the fact and premises are an instance of it, that every comparison holds and
the absent atoms are exactly its negations; for each base node, that each
event is in an unbroken hash chain with that hash, no later than `as_of`,
and that the events say so: a fact read from one event must be stated by
it. `truth`, `defeated`, `disputed` and `resolution` it re-derives itself,
by folding the log up to `through` with the rules of section 4 (which
contest a ruling or refutation ended, which ruling came last, which disputes
are still open), and the value and the cited events must match. The one
thing it cannot confirm from a proof alone is that an absent fact is absent;
running the query again does.

## 10. The MCP server (M4)

`python -m gnomon.mcp --log memory.jsonl --writer <writer> [--checker <writer>]`
serves the memory to any AI model over MCP (stdio, JSON-RPC 2.0, standard
library only).

* **One identity, fixed by whoever configures it.** The server acts as
  `--writer`; nothing a model sends can change that. It reads only that
  writer's compartments, and every write still passes the core's write gate.
  `run_checkers` (listed only when `--checker` is given) checks only claims
  the writer can see.
* **Tools.** `remember`, `record_conversation` (record, then extract by the
  rules of section 8), `query` (answers, proofs explained, and every proof
  re-checked by `gnomon.proofs` before it is returned), `query_language`,
  `give_verdict`, `supersede`, `run_checkers`, `verify_log` (pass or fail
  only: the log's size would reveal what the writer cannot see). Refusals by
  the gate come back as tool errors with their code, so a model can correct
  itself.
* **Both protocol eras.** Modern (2026-07-28): every request carries
  `io.modelcontextprotocol/protocolVersion` and `clientCapabilities` in
  `_meta`; `server/discover`; results carry `resultType`; other versions get
  `-32022` listing the supported ones. Legacy (2025-11-25 and earlier,
  e.g. today's official SDKs): an `initialize` handshake for the stdio
  process. Earlier revisions are reached only through `initialize`.
* **Robust framing.** One message per line, at most 4 MiB (longer lines are
  skipped as they are read, never held in memory), nesting at most 64
  levels, no NaN or infinities, no lone surrogates, no batches. Every request
  gets exactly one well-formed reply and no input ends the server.

`python -m gnomon init | register | verify | query` sets up and inspects a
memory from the command line, through the same gate.

**Auto mode (Claude Code).** A model that is asked to record its
conversations may forget to. With host hooks, nothing depends on it
(`python -m gnomon hooks` prints the settings, shell-quoted; `gnomon.hook`
runs them):

- **SessionStart:** the session gets its own writer, an instance of the
  template writer keyed by the host's session id (only that template's own
  instances count), so a resumed session keeps its writer. The model is
  told who it is on record and given a token-budgeted resume packet. It
  also gets a token for the `attach` tool, which makes its MCP tool calls
  those of the same writer.
  - **The token:** a random secret kept beside the log, readable only by
    its owner. It is single use, tied to its session (a server that knows
    its own session cannot redeem another's), and expires after a day.
    There is at most one outstanding token per session.
  - **Never recorded:** any outstanding token that appears in recorded text
    is replaced with "[attach token]".
  - So the binding never rests on what a model says about itself.
  - **One compartment:** the session's writer holds only the compartment it is recorded in, so nothing it reads through Gnomon comes from another. A resumed session whose writer holds more is not recorded (the hook says so on stderr).
- **Stop, PreCompact, SessionEnd:** the turns added to the host's
  transcript since the last time are recorded as episodes. The model's
  turns go under its registered name (or its id) bound to its writer, and
  "User" is bound to its operator.
  - **What counts as said:** only words people and the model said. These are
    left out:
    - tool calls and tool output (unless asked for);
    - side conversations;
    - the host's own entries: notes, compaction summaries, slash commands
      and their output, interruptions, API errors.
  - **Losslessness:**
    - Recording is exact: a very long turn becomes several turns whose text
      joins back to exactly the original.
    - A broken surrogate pair becomes U+FFFD rather than losing the turn.
  - **The transcript is read from a byte offset,** never whole.
    - One recorder runs per session at a time; overlapping hooks wait.
    - The offset advances after each recorded episode, so a failure never
      duplicates turns.
    - A part the gate refuses is replaced by a note saying so, so it never
      stops recording.
    - A rewritten or replaced transcript is read again from the start.
    - A half-written last line waits for the next run.
- **Failures:** a hook never breaks the session it serves. It reports
  failures on stderr and exits 0.

## 11. Recall (M5)

`query` answers exactly, with proofs. Recall answers the other question a
memory gets: *where was this said?* It ranks what the reader can see, in
plain words, with no model in the loop, and every hit says exactly where
it came from.

**What is searched.** The turns of every episode (or its paragraphs when it
has no turns) and every claim, in the reader's compartments, as of the
requested moment: the same visibility as queries (section 9), so recall
leaks nothing a query would not. `speaker`, `after` and `before` choose
which hits are returned; ranking still runs over everything visible, so a
reply can be found through the question before it.

**Ranking.** Each step is a standard, documented method with fixed settings,
and each can be switched off (`tuning`) to measure it:

1. *Words.* Unicode lower-casing, runs of letters and digits, a fixed stop
   list, and Porter's 1980 stemmer (words over 48 letters are left alone).
2. *Joins* (on). Adjacent words are also indexed joined, so "road trip"
   meets "roadtrip" and "de-stress" meets "destress".
3. *BM25* with k1 = 1.2 and b = 0.4.
4. *Speakers* (on). A speaker named in the question by their whole name, or
   by their first name with a capital, is who spoke: their turns score
   ×2.5 and the name stops counting as a word to find (people are mostly
   named when addressed), unless the question is nothing but names.
5. *Neighbours* (on). A turn gains the whole score of the turn just before
   it when that turn is a question, otherwise a quarter, and a quarter of
   the turn after it. The gain is listed among its matches as
   `(neighbours)`.
6. *Conversations* (×1.2). A turn gains from its whole conversation
   matching: it scores × (1 + 1.2 · s / s_max), where s is the sum of its
   conversation's turn scores over the square root of its length, and
   s_max the highest such value. Evidence for one question often spans a
   session.
7. *When* (×1.5). If the question asks when (it begins "when", or asks
   "what date", "what time" or "how long"), a turn that holds a date scores
   ×1.5.
8. *Dates in the question.* With `now`, relative dates in the question are
   resolved, and anything said or dated within them scores ×1.5.
9. *Feedback* (off). RM3-style expansion from the first results. It is
   implemented but off by default because it hurt the top of the ranking
   on the development conversations (see below).

Every weight is a `tuning` value in thousandths:

- k1, b
- prop_q and prop_n (the neighbour shares)
- speaker, window, session and when

`bench/locomo.py --grid` measures any of them. The defaults were chosen
there, on conversations 0–4 only.

A hit's `score` is the sum of its `matched` shares times its `boosts`. The
boosts list every multiplier applied, with its reason and size. Ties
go to what was recorded first. The output is identical for identical
inputs.

**Dates, by the calendar.** Every hit lists the relative dates in it,
resolved against the day it was recorded: yesterday, today, tonight,
tomorrow, the day before yesterday, the day after tomorrow, last night,
last/this/next with a weekday, week, weekend, month or year, "N days,
weeks, months or years ago", "N … from now" and "in N …". Conventions:
weeks run Monday to Sunday; "last Friday" is the latest Friday before
today (a week back when today is Friday) and "next Friday" the first after
it; "this Friday" is the Friday of this week; "a week ago" is the whole
week seven days back; "last weekend" is the Saturday and Sunday of last
week; months are calendar months. Dates outside 0000–9999 are not
resolved.

**Provenance.** Each hit gives its words, speaker, time, the episode, turn
number and code-point span, and the event it was recorded in with that
event's hash, so it can be checked against the log. Text holding hidden
characters (section 6) is flagged with `hidden_chars`; the MCP server shows
such characters escaped.

**Measured.** On LoCoMo (Maharana et al., 2024; 10 conversations of up to
35 sessions, 1,986 questions each labelled with the turns holding its
evidence), recall is measured exactly: the share of a question's evidence
turns among the first k hits. Choices were made on conversations 0–4 only;
conversations 5–9 were held out. `bench/locomo.py` reproduces every number.

| Held out (conversations 5–9) | R@1 | R@5 | R@10 | R@25 | R@50 |
|---|---|---|---|---|---|
| BM25 alone, categories 1–4 | 26.4 | 49.2 | 57.2 | 65.5 | 71.3 |
| Gnomon recall (M5), categories 1–4 | 34.3 | 61.4 | 68.8 | 77.0 | 82.9 |
| **Gnomon recall (now), categories 1–4** | **40.6** | **66.3** | **73.0** | **80.1** | **84.6** |
| **Gnomon recall (now), all questions** | **35.8** | **65.4** | **74.2** | **82.3** | **86.5** |
| DRAGON, as reported in the LoCoMo paper, all questions | – | 58.8 | 67.5 | 79.9 | 84.8 |

DRAGON is a trained neural retriever; its numbers are from the LoCoMo paper
over the whole dataset, possibly another release of it, while Gnomon's are
on the held-out half only, so the comparison is indicative, not exact.

- **Over all ten conversations:** Gnomon scores 67.5 / 75.1 / 83.2 / 87.2 at
  5 / 10 / 25 / 50.
- **Time:** the median per question is under 2 ms on one core, with no model
  and no network.

**Speed at scale.** Turns and claims are analysed once (words, stems,
dates), cached by content (the hash of the event that recorded them, or the
claim's id and recording time), and indexed by term. Scoring a question
touches only the entries holding its terms. Gathering what the reader can
see is still a pass over the visible memory, but a cheap one with nothing
re-analysed. The cache is held for the whole of a question, so concurrent
questions never see it change. `bench/scale.py` measures it.

## 12. The record as a forum (M7)

The record is not a store of truths. It is a place where claims are put
forward, questioned, answered, criticised, judged and superseded, all in
the open and all attributed. Verdicts and supersessions (section 4) judge
claims. Section 12 adds the conversation around them.

**References.** A claim may say what it is `about`: earlier claims, each
with a relation.

| Relation | Means | Rule |
| --- | --- | --- |
| `answers` | an answer to a question | the target's stance is `asked`; an answer is not itself a question, and answers one question (`E_REFERENCE`, `E_BODY`) |
| `critiques` | an argument against it | changes no one's truth; only verdicts and supersessions do |
| `replies` | a reply in a discussion | |
| `cites` | builds on it, or points to it | |

The targets must be claims the writer can see, in the claim's own
compartment: a thread never crosses compartments. A missing target and a
hidden one are refused alike (`E_REF`). A claim's id is the hash of its
body, `about` included, so a claim can only refer to claims that existed
before it. Threads are therefore acyclic by construction, like commits.

**Questions** are claims with stance `asked` and truth `unknown`. Anyone
may answer, and answers are claims, judged like any other.

**Views** (with proofs, re-checked by the independent checker):

| View | Holds when |
| --- | --- |
| `question(Q)` | Q is a question |
| `answer(A, Q)` | A answers Q |
| `settled(Q)` | some answer to Q is believed and has a supporting verdict |
| `open_question(Q)` | Q is neither settled nor rejected (a question with a false premise can be refuted like any claim) |
| `critique(K, C)` | K critiques C |
| `unanswered_critique(K, C)` | C's author has not replied to K (a critique of several claims waits on each author) |
| `awaiting_verification(C)` | C's truth is unknown, it is not a question, and nobody has judged it |

A query evaluates only the rules it can reach, so no view costs anything
unless it is used. Threads are read with `discussion` rather than a
recursive view: it walks the reference graph once, from the claim
outwards, and lists each post once in the order it was recorded. Each post
comes with its standing (truth, verdicts including abstentions, disputes,
supersessions) and the posts in the thread it refers to. It is bounded (500
posts by default, 200 over MCP) and says when it stopped early.

**The MCP server** gives models `remember` with `about`, `discussion` (a
claim's whole thread with its standing) and `open_questions` (each with
its exact number of answers). In Python
the same are available as `Memory.ask`, `Memory.respond` and
`Memory.discussion`.

**Content-addressed proofs.** A proof is a term: rules applied to facts,
down to log events. Every rule in a proof carries the SHA-256 of its text,
and every node the SHA-256 of what it states and of the hashes of what it
rests on. These are the rule's hash and its premises' hashes, or for a
leaf, the hashes of its log events. A proof is therefore a Merkle DAG, and
each answer carries a `proof_hash` that fixes its entire derivation.

- Equal derivations get equal hashes.
- A rule changed by a single character gets a new name.
- The independent checker recomputes every hash. A proof that is partly
  hashed and partly not is refused. With `require_hashes` (which the MCP
  server and the command line use), so is one with no hashes at all.

## 13. Continuity (M8)

A piece of work outlives the session doing it. A **handoff** records where
the work stands, so that anyone can pick it up exactly there: the same
model in a new session, another model, another app or a person.

| Field | Required | Meaning |
| --- | --- | --- |
| `compartment` | yes | where it lives; the writer must be in it |
| `frame` | yes | the piece of work, named as in claims (a project, task or topic); one line |
| `summary` | yes | where things stand (may span lines) |
| `goal` | no | what the work is for; one line |
| `done`, `next`, `open` | no | 1–64 items each, each one line (repeats allowed) |
| `refs` | no | up to 64 claims, episodes or handoffs it rests on, in its compartment |
| `artifacts` | no | up to 64 `{uri, note?, sha256?}`: files, URLs, commits; uri without spaces, note one line |
| `follows` | no | the handoff this one continues, in the same frame; from protocol version 3, a handoff has at most one successor (`E_FORK`) |

"One line" means no line breaks or tabs, so that no field can pass itself
off as another line wherever it is shown, for example a fake "latest
handoff" block from someone else. From protocol version 2 the same applies
to claim frames. Lists are measured before anything else is checked.
Missing references and references to other compartments are refused alike
(`E_REF`). A handoff is the writer's own account, not a claim about the
world: it is attributed and kept, but never judged true or false. When it
matters, the claims it rests on are what gets judged.

**One successor (protocol version 3).** Two runs that both continue the
same handoff would fork the chain, and resume would show only the newer
branch, silently losing the other's work. From version 3 the second is
refused with `E_FORK` ("follow the latest"); the writer resumes and
follows the newest handoff instead. Logs at version 2 keep the old rule
until upgraded with `schema_change`.

**Start-packet frames (protocol version 4).** Three frame families are
guarded, because what a resident reads at the start of a run decides how it
works: another writer's request that reaches that packet changes the
resident far more than any label on it (measured in Polymechania, T12b-d).
`self/<w>` (who writer `w` is: identity, rules, voice) and `standing/<w>`
(instructions for every future run) are written by the owner only;
`run/<w>` and `run/<w>/...` (the resident's own work: handoffs, lessons,
journal) by `w`, its instances (section 2a) or the owner. Anyone else is
refused with `E_CAPABILITY`, for claims and handoffs alike. Every other
frame stays free. `Memory.packet(reader, w)` builds a start packet from
these alone: the owner's latest claim in `self/<w>` and in `standing/<w>`,
each the whole current page (earlier ones are history; a page is replaced
by writing a new one), and the resume of `w`'s run frame. Claims there by
anyone but the owner, possible only on a log upgraded from an older
version, are counted and never shown as the page. `render_packet` shows the
two pages as the owner's, and the rest as recorded data. Logs at version 3
keep free frames until upgraded with `schema_change`. Not closed by the
gate: a resident that restates another writer's request in its own handoff
carries it forward under its own name.

**Time from the gate's side.** Record time never runs backwards, so one
event dated far ahead would block every later write. A fresh write dated
more than 300 seconds ahead of the gate's own clock is refused with
`E_FUTURE`. Only fresh writes are checked against the clock: replay stays
deterministic, and a log that already holds such an event still verifies
(it stays blocked for later writes until it is rebuilt).

**Resuming** (`Memory.resume`, MCP `resume`) gives, for a frame (by
default the most recently active one the reader can see):

- the latest handoff and the earlier ones;
- how everything the handoff rests on stands *now*. A decision it relied
  on may have been refuted, disputed or superseded since, and resume says
  so;
- what happened after it: new claims in the frame, decisions that stand,
  open questions, and critiques nobody has answered.
- the end of the last recorded conversation: the turns recorded by the
  writer that left the handoff, or, without one, by the writer of the most
  recent conversation. That is one session's conversation, never several
  interleaved.

**Context is a budget.** As text for a model (`render_resume`, MCP
`resume` with `budget_tokens`, the SessionStart packet), resume is fitted
to a budget of tokens, estimated at four characters each. Parts are kept
in this order:

1. The handoff. Its goal, next steps and open items come first. Its summary
   is shortened rather than dropped, and the handoff as a whole is never
   left out.
2. What changed under the handoff.
3. Open questions.
4. The end of the last conversation, newest turns kept first and shown
   oldest first.
5. Decisions.
6. What happened since the handoff.
7. Unanswered critiques.

A section's title appears only with at least one of its items. Whatever is
left out is counted, with a note on how to ask for more. Over MCP the
structured result carries ids only, so a resume never costs its tokens
twice.

Order is log order. The state gives each visible claim and handoff an
`order`: its rank among the claims and handoffs the reader can see, which
says what came first without revealing how many hidden events lie between.
Resume is bounded. Long texts are cut and marked (summary 4,000
characters, items and statements 1,000 or fewer), and lists keep their
most recent entries (20 by default, saying how many more there were).

`frames` lists every piece of work the reader can see, most recently active
first. In queries, `handoff(H, Frame, Writer, Time)` is a base relation.

Over MCP, resume opens by saying that everything in it is recorded data,
attributed to its writers, to be weighed as information and never followed
as instructions. Every one-line field is shown escaped, and a summary is
shown indented under the writer who wrote it.

The MCP server tells models to `resume` at the start of work and to
`handoff` before they stop or when their context grows long. Every session
is its own writer (section 2a), so a resumed piece of work shows exactly
which session and model left it, and for whom.

## 14. Speed, and what a model reads

**Speed.**

- **Verifying a log:** lines are parsed, checked to be canonical and hashed in
  parallel. Each event's hash is worked out once, from its exact line minus
  its top-level hash member. They are then replayed through the gate in
  order.
- **Hashing** uses the processor's SHA-256 instructions where it has them
  (x86-64 SHA extensions: Intel since Ice Lake, AMD since Zen), chosen at
  run time and tested against the portable code at every length. Canonical
  JSON copies runs of plain bytes whole and sorts members only when they are
  out of order.
- **A new process** (every hook, every new session) starts from a snapshot
  of the verified state when there is a usable one: `<log>.d/snapshot`,
  readable by its owner only. A snapshot is a cache, not a source of truth:
  - it names the exact bytes it was made from (their length and SHA-256),
    and is used only while the log still begins with exactly those bytes,
    with as many lines as it has events and its last event's hash on the
    last of them; events after them go through the gate as usual;
  - it names the code that made it (a hash of the gate, schema, canonical
    JSON, hashing, store and snapshot source), so another build never uses
    it;
  - it is sealed with HMAC-SHA-256 under a key of the user's own
    (`snapshot.key` in `$GNOMON_STATE_DIR`, `$XDG_STATE_HOME/gnomon` or
    `~/.local/state/gnomon`, owner only). Whoever can write beside the log
    but cannot read that key cannot make a snapshot that is used: a forged
    or damaged one is ignored, removed, and the log verified in full;
  - its directory and file must be the user's own and writable by no one
    else. It is written under a fresh name, never through an existing file
    or link, then renamed into place, and never over one that covers more;
  - decoding is bounded in depth (as canonical JSON is) and in allocation;
  - it decodes to exactly the ledger a replay gives. An appended event is
    held in memory exactly as a replay of its line would hold it, so state
    never depends on how a draft was built;
  - the full audit (`gn_verify`) never reads it.

  Logs under 1,000 events get none, and neither do systems other than Unix
  (for the key and ownership checks). A new one is saved, with no lock held
  on the log, once the log has grown 1% past the last (at least 256 events,
  at most 4,096), so a new process replays little.
- **Partial state:** `gn_writers` (Python `Memory.writers`) returns only the
  writers, and `gn_select` (`Memory.select`) the writers plus named claims,
  episodes and handoffs, so callers that need a little of the state never
  render all of it.
- **In a long-lived process** (the MCP server), a verified log is cached:
  - A log that has only grown, from any process, has just its new lines
    checked, through the same gate.
  - Any other change forces a full replay.
- **Queries** load only the relations they can reach, and make event
  references only for the sources a proof shows.
- **Proofs** are most of a result's cost: `proofs` (Python `Memory.query`,
  MCP `query`, or `gn_query_opts`) limits them to the first n answers. The
  others carry a null proof, and `proved` says how many were proved.
  - `gnomon.proofs.check` fails any answer without a proof, unless the
    caller says how many proofs it asked for (`proofs_requested`). Even
    then, exactly that many must be proved, and the unproven answers must
    come after them.
  - The MCP server proves and checks the first 100 by default and reports
    the rest as unproven, never as checked.
- **The independent checker** re-reads only what was appended to a log since
  it last read it. It checks those lines itself and remembers each claim's
  id.
- **Resume** reads only the part of the state it needs, worked out natively:
  - the frame's handoffs and claims, and what they rest on;
  - claims from elsewhere that refer to them;
  - enough of the latest conversation.

  It runs one combined query for open questions, decisions and unanswered
  critiques.

**What a model reads.** MCP text is written for a reader that pays per
token:

- **Claim ids** are shown short (`c:` and 16 hex digits, 64 bits, far too
  many to forge a look-alike). Every tool takes them back, query programs
  included, resolved to the one claim the writer can see that starts that
  way. Missing, hidden and ambiguous ids are refused.
- **Times** are written `2026-10-16 10:15`.
- **Stance and truth** are written as codes (`[D T]` is decided, true) with a
  one-line legend whenever codes appear.
- **A writer** is named only when it is not the speaker.
- **Structured results** for recall and resume carry ids and scores, not the
  text again.
- **What the model was already shown** is referred to, not repeated. Each
  MCP connection notes the recall hits (and neighbouring turns) it showed in
  full. A hit shown within the last 20 results is written as `^` with its
  place. Words of 40 characters or fewer are always shown.
  - When the host compacts the session's context, the `PreCompact` hook
    leaves a mark beside the log (`<log>.d/hooks/<session hash>.compacted`).
    The server sees it and forgets what it showed, since the model may no
    longer have it.
  - `repeat: true` shows everything in full. `--no-dedup` turns this off
    for a server.

`bench/tokens.py` measures the cost per item shown.

## 15. Organisations in the record

Teams of models and tools (pricing who does each part, the forms
alphabet, companies) are specified in Polymechania
(github.com/5kasgard/polymechania, `spec/polymechania-v0.1.md`), which
keeps its books here: a work order is a question in a `firm/<kind>`
frame, a result is a claim that `answers` it, and verdicts on results
from outside the worker's group are its track record. What Gnomon itself
enforces is the structure:

**Organisations in the record.** Three events make an organisation's
structure part of the verified log, with authority checked by the gate:

- `position` {compartment, org, title, reports_to?, duties}: defines a
  title and whom it reports to (none: the owners). One organisation lives
  in one compartment; no position reports, through others, to itself.
- `appoint` {compartment, org, title, holder}: puts a writer of that
  compartment in a position. Many may hold one title; no one holds it twice
  at once.
- `dismiss` {appointment, reason}: ends an appointment.

Owners and governors may do all three. Anyone else acts only on positions
below one they currently hold (directly or through others): a Lead hires
Engineers, an Engineer hires no one, and a dismissed Lead has no authority
left. No one appoints themselves; anyone may resign. Queries see
`position(O, T, ReportsTo)`, `holds(W, O, T)`, `appointed(A, W, O, T)` and
`dismissed(A, E)`, and the views `above(O, T, U)` and `oversees(B, W)`;
the independent checker establishes `position` and `holds` by folding the
log itself, so a holding that has since ended no longer checks.

**Organisations** (positions, appointments, dismissals) belong to their
compartment: the same name in two compartments is two organisations, and
neither can learn of the other. Owners and governors act in an
organisation only if their party founded it; anyone else acts through the
positions they hold. Titles have no leading, trailing, doubled or unusual
spaces.

## 16. Not yet in v0.1

Grants that let someone other than the owner write a resident's standing
instructions (`instruct:<w>`) (they could now be leased); re-granting
revoked access; signatures (writer identity is by registration only), multi-writer merge of
independently written logs, storage in immutable segments with sync to
object storage and client-side encryption, a hosted remote MCP endpoint
with sign-in, a web viewer for people, arithmetic and aggregation in queries, HTTP
transport for the MCP server, a persistent recall index (recall re-reads
the visible memory on every request; at 10M tokens the first query of a
conversation takes seconds). End-to-end question answering is measured in
`bench/qa` and `bench/beam_qa`, with a language model reading the hits.
See the v1 implementation plan.
