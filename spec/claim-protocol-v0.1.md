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
| `record_time` | timestamp | `YYYY-MM-DDTHH:MM:SSZ`; never earlier than the previous event |
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
| `register` | the owner (and, at genesis, the owner registering itself) | adds a writer with a party, kind, capabilities and compartments |
| `episode` | `write` | records raw material whole: a transcript, document or tool output |
| `claim` | `write` | records a claim; identical content from another writer is corroboration |
| `verdict` | `verify`, never a writer or party that asserted the claim; one verdict per party | supports, refutes or abstains on a claim |
| `supersede` | `write`; both claims in one compartment; neither in an open dispute | links an old claim to a new one: refuted, narrowed, generalised or contested |
| `resolve` | to rule: `verify`, never a party with a stake; to withdraw: `write`, the party that raised it | ends a contest (`old`, `new`, `both`) or an appeal (`reinstated`, `upheld`); either may be `withdrawn` |
| `appeal` | `write`; one open appeal per claim; once per party per claim | asks a neutral party to reinstate a refuted or defeated claim, citing outside support |
| `schema_change` | `govern` | advances the schema version by exactly one |

The `owner` capability implies the others.

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
| `intended`, `delivered`, `observed` | no | kept separate, never collapsed |

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

## 8. Not yet in v0.1

Signatures (writer identity is by registration only), multi-writer merge of
independently written logs, views beyond the derived state, the query
language (Datalog), and the MCP server. See the v1 implementation plan.
