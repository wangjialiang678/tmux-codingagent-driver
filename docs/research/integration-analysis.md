# tmux-bridge × tcd Integration Options: In-Depth Analysis

> Date: 2026-03-02
> Perspective: Consumer (demand side) + technical architecture trade-offs

---

## 1. Clarifying Who the "Consumers" Are

The two projects serve different consumers with different needs. Before discussing options, we must map out the requirements clearly.

### 1.1 Consumers of tmux-bridge

| Consumer | Invocation | Core Need |
|----------|-----------|-----------|
| **Claude Code Skill** | CLI (`tmux-bridge start/status/output`) | Minimal interface — one prompt in, one result out |
| **Nanobot Tool** | Python API (`TmuxSession.create()`) | Conform to Nanobot Tool ABC, embedded in Ralph Loop |
| **OpenClaw Plugin** | CLI (TypeScript `child_process`) | JSON stdout, zero-dependency cross-language |

**Demand characteristics**: All three consumer types follow a **single-task model** — each invocation starts one Codex instance, completes, then tears down. They do not care about multi-provider support, job persistence, or multi-turn conversation.

### 1.2 Consumers of tcd

| Consumer | Invocation | Core Need |
|----------|-----------|-----------|
| **Claude Code** (direct bash) | CLI (`tcd start -p codex ...`) | Parallel dispatch, multi-provider selection |
| **Shell scripts** (pipeline/batch) | CLI (script orchestration) | Bulk submission, status polling, result collection, pipeline chaining |
| **Python automation** | SDK (`from tcd import TCD`) | Object-oriented API, `wait()`/`clean()` lifecycle management |
| **Custom Orchestrator** | SDK/CLI | Multi-turn conversation, failure recovery, context_limit detection |

**Demand characteristics**: These consumers require **multi-task orchestration** — managing multiple jobs simultaneously, tracking state across different providers, handling failures and timeouts.

### 1.3 Requirement Intersection and Differences

```
              tmux-bridge needs              tcd needs
            ┌──────────────┐            ┌──────────────┐
            │ Zero deps    │            │ Multi-provider│
            │ Nanobot fit  │            │ Job state     │
            │ Structured   │  ┌──────┐  │  machine      │
            │  output      │  │Common│  │ Multi-turn    │
            │ (CodexOutput)│  │      │  │ wait/clean    │
            │ Semantic     │  │ tmux │  │ SDK (OOP)     │
            │  depth consts│  │driver│  │ Idle detect   │
            │              │  │ core │  │ Claude/Gemini │
            │              │  │      │  │ trust dialog  │
            │              │  │      │  │ bracketed     │
            │              │  │      │  │  paste        │
            └──────────────┘  └──────┘  └──────────────┘
```

**Shared core** (~50% of code): tmux session management, send-keys/load-buffer transport, signal file detection, ANSI cleaning.

---

## 2. Option A in Detail: tcd Depends on tmux-bridge

### Architecture

```
Caller → tcd (CLI/SDK)
           ↓
    ┌─────────────────────────────┐
    │  tcd layer (orchestration + providers)  │
    │  ├── sdk.py                 │
    │  ├── cli.py (click)         │
    │  ├── job.py (state machine)  │
    │  ├── provider.py (registry) │
    │  ├── idle_detector.py       │
    │  ├── marker_detector.py     │
    │  └── providers/{codex,claude,gemini}.py │
    └──────────────┬──────────────┘
                   │  import tmux_bridge
    ┌──────────────▼──────────────┐
    │  tmux-bridge layer (driver primitives) │
    │  ├── session.py             │
    │  ├── transport.py           │
    │  ├── capture.py             │
    │  ├── completion.py          │
    │  └── output.py              │
    └──────────────┬──────────────┘
                   ↓
               tmux + AI CLIs
```

### When Option A Fits

#### Scenario 1: tmux-bridge Has an Independent Life

If Nanobot and OpenClaw are **active, continuously evolving** consumers, tmux-bridge needs to be released, tested, and maintained independently with interface stability.

**Deciding factors**:
- Is Nanobot's Ralph Loop using tmux-bridge in production?
- Has OpenClaw actually integrated the tmux-bridge CLI?
- Are other teams/projects using tmux-bridge?

If the answer is "yes," tmux-bridge must remain independently published — Option A is the natural choice.

#### Scenario 2: You Expect More tmux Driver Layer Consumers in the Future

For example:
- A future **Go orchestrator** needs to call the tmux-bridge CLI
- A future **MCP Server** directly wraps tmux-bridge
- tmux-bridge becomes "the standard library for tmux + AI CLI interaction" and gets reused

In this case, keeping tmux-bridge independent with its zero-dependency property is valuable.

#### Scenario 3: Team Structure Requires Decoupling

If tmux-bridge and tcd are maintained by different people, or you want to enable that split in the future — stabilize the low-level tmux interaction and hand it off while focusing on upper-level orchestration — Option A provides a clean responsibility boundary.

### Pros of Option A

| Pro | Description |
|-----|-------------|
| **Eliminates ~1000 LOC duplication** | tcd's tmux_adapter + signal file detection + ANSI cleaning replaced by importing tmux-bridge |
| **Gains tmux-bridge's fine-grained capabilities** | 4-layer JSON extraction, semantic depth constants, structured CodexOutput, byte-level UTF-8 chunking |
| **Independent evolution** | tmux-bridge can fix bugs and add features (e.g., WezTerm backend) independently; tcd benefits automatically |
| **Preserves zero-dependency** | tmux-bridge stays zero-dependency, giving lightweight consumers (Nanobot, OpenClaw) a clean interface |
| **Separation of concerns** | tmux-bridge: "how to interact with tmux"; tcd: "how to orchestrate multiple AIs" |

### Cons of Option A

| Con | Description | Severity |
|-----|-------------|---------|
| **Interface adaptation cost** | tmux-bridge's API is designed for single tasks (`TmuxSession` + `SessionConfig`); tcd needs to adapt it to a multi-provider model. For example, tmux-bridge hardcodes the Codex update skip in `session.py`, but tcd's providers each have different initialization logic. A wrapper or adapter layer is needed. | Medium |
| **Version coupling risk** | If tmux-bridge changes its API (e.g., `TmuxSession.create()` signature changes), tcd must follow. The two packages may not release in sync. | Medium |
| **Abstraction leakage** | tmux-bridge currently assumes "agent = Codex" (hardcoded update prompt skip, notify-hook format). tcd supports 3 providers, requiring tmux-bridge to generalize these assumptions. This means **reverse-engineering tmux-bridge**. | High |
| **Deployment complexity** | tcd's `uv tool install` needs to resolve tmux-bridge as a dependency. Local development may require editable installs of both packages. | Medium |
| **tmux-bridge lacks bracketed paste** | tcd found that Ink TUI requires `paste-buffer -p`; tmux-bridge currently uses `paste-buffer` without `-p`. Either fix tmux-bridge or work around it in tcd. | Medium |

### Hidden Prerequisite for Option A

**tmux-bridge must be generalized.** Several hardcoded assumptions in tmux-bridge are Codex-specific:

1. `session.py`'s `_skip_update_prompt()` — sends "3" + Enter to skip the Codex update prompt
2. `session.py`'s `_build_agent_command()` — Codex's `-c 'notify=[...]'` format
3. `completion.py`'s `SESSION_COMPLETE_MARKER` — tmux-bridge-specific marker
4. `output.py`'s `CodexOutput` — Codex NDJSON format specific

Without generalizing these, tcd can only partially use tmux-bridge (transport and capture, but bypass session and completion), weakening Option A's benefits.

---

## 3. Option B in Detail: tcd Absorbs tmux-bridge's Best Parts

### Architecture

```
Caller → tcd (CLI/SDK)               tmux-bridge (maintained independently)
           ↓                              ↓
    ┌─────────────────────────┐    ┌──────────────┐
    │  tcd layer (all-in-one)  │    │  (runs as-is) │
    │  ├── sdk.py             │    │              │
    │  ├── cli.py             │    │  Nanobot/    │
    │  ├── job.py             │    │  OpenClaw    │
    │  ├── provider.py        │    │  consumers   │
    │  ├── tmux_adapter.py    │    └──────────────┘
    │  │   (absorbed essentials)  │
    │  ├── output_cleaner.py  │
    │  │   (absorbed 4-layer JSON) │
    │  └── providers/...      │
    └─────────────────────────┘
```

### When Option B Fits

#### Scenario 1: tmux-bridge's External Consumers Are Inactive or Abandoned

If Nanobot's Ralph Loop no longer uses tmux-bridge (switched to tcd SDK or other approach), and OpenClaw hasn't actually integrated it either — then tmux-bridge is a "historical predecessor," not worth maintaining interface compatibility for.

#### Scenario 2: You Want tcd to Iterate Quickly Without Upstream Constraints

tcd has discovered many issues in practice that tmux-bridge never addressed:
- Bracketed paste (Ink TUI)
- Idle detection (AI doesn't follow the marker protocol)
- Trust dialog auto-handling (Claude Code)
- Context limit detection
- Multi-turn `turn_count` tracking

These were all discovered and solved by tcd in the field. If every improvement requires convincing tmux-bridge to accept it, waiting for tmux-bridge to release, then consuming it in tcd — iteration speed suffers severely.

#### Scenario 3: You Are the Sole Maintainer and Can't Afford Two-Package Coordination

Practical consideration: two packages = double the releases, double the changelogs, double the CI maintenance, plus coordination cost when interfaces change. If you're the sole maintainer, this overhead may not be worth it.

#### Scenario 4: tcd's tmux Adapter Is Already Good Enough, Just Missing a Few Tricks

Option B is essentially "cherry-picking" — porting 4–5 essentials from tmux-bridge into tcd's existing code:

| Item to port | Target location | Effort |
|-------------|----------------|--------|
| 4-layer JSON extraction | `tcd/output_cleaner.py` | ~50 LOC |
| Semantic depth constants | `tcd/tmux_adapter.py` | ~15 LOC |
| UTF-8 byte-level chunking | `tcd/tmux_adapter.py` | ~30 LOC |
| Structured CodexOutput | `tcd/providers/codex.py` | ~40 LOC |
| DCS sequence cleaning | `tcd/output_cleaner.py` | ~10 LOC |

Total ~145 LOC to port, one-time effort, no ongoing coordination cost.

### Pros of Option B

| Pro | Description |
|-----|-------------|
| **Zero coordination cost** | tcd evolves fully autonomously; changing the tmux adapter doesn't require considering tmux-bridge compatibility |
| **Single package deployment** | `uv tool install tcd` is enough; no dependency resolution needed |
| **Fast iteration** | Fix issues directly, no upstream PR → review → merge → publish cycle |
| **Small port size** | Only ~145 LOC, half a day's work |
| **tcd is already more complete** | Multi-provider, bracketed paste, idle detection, etc. are all tcd improvements over tmux-bridge; no "downgrade" risk |

### Cons of Option B

| Con | Description | Severity |
|-----|-------------|---------|
| **Overlapping code persists** | tmux-bridge and tcd's tmux interaction layers continue to be maintained separately; bugs need to be fixed in both places | Medium |
| **tmux-bridge improvements don't flow into tcd automatically** | If tmux-bridge adds a WezTerm backend in the future, tcd won't benefit automatically | Low (can be manually synced anytime) |
| **Narrative fragmentation** | Two projects with similar positioning create confusion: users may ask "which one should I use?" | Medium |

---

## 4. Are There Other Options?

### Option C: Merge into One Project (tmux-bridge Absorbed into tcd)

```
tcd/
├── src/tcd/
│   ├── bridge/              ← original tmux-bridge code, renamed as submodule
│   │   ├── session.py
│   │   ├── transport.py
│   │   ├── capture.py
│   │   ├── completion.py
│   │   └── output.py
│   ├── cli.py
│   ├── sdk.py
│   ├── job.py
│   ├── provider.py
│   └── providers/...
```

**Fits when**: tmux-bridge's external consumers can migrate to `from tcd.bridge import TmuxSession`.

**Pros**:
- Completely eliminates overlap; one place to change, one place to test
- Preserves tmux-bridge's fine-grained implementation as tcd's foundation

**Cons**:
- Breaks tmux-bridge's existing consumers (`import tmux_bridge` → `import tcd.bridge`)
- tmux-bridge's zero-dependency promise is broken (tcd depends on click)
- If Nanobot/OpenClaw only need the low-level driver, they're forced to install all of tcd

**Assessment**: Not recommended unless tmux-bridge is confirmed to have no independent consumers.

### Option D: Extract a Shared Core Library

```
tmux-agent-core/     ← new shared base package (zero-dependency)
├── session.py
├── transport.py
├── capture.py
└── output.py

tmux-bridge/         ← depends on tmux-agent-core + zero extra deps
├── cli.py
├── completion.py
└── adapters/

tcd/                 ← depends on tmux-agent-core + click
├── cli.py
├── sdk.py
├── providers/
└── ...
```

**Fits when**: Both projects are active and share common low-level code requiring unified maintenance.

**Assessment**: Over-engineering. Three packages have higher maintenance cost, unless a third consumer appears. **Not recommended.**

---

## 5. Decision Framework: 4 Questions

Answer the following 4 questions to make the choice:

### Q1: Does tmux-bridge have active consumers outside of tcd?

| Answer | Recommendation |
|--------|---------------|
| **Yes** (Nanobot/OpenClaw in use, ongoing) | → Option A |
| **No** (abandoned or never truly integrated) | → Option B or C |
| **Uncertain** (planned but not done yet) | → Option B (move fast now, refactor when needed) |

### Q2: Do you plan to make tmux-bridge a universal tmux-AI interaction standard library?

| Answer | Recommendation |
|--------|---------------|
| **Yes** (want more projects to reuse it) | → Option A (but generalize tmux-bridge first) |
| **No** (it's just tcd's predecessor) | → Option B |

### Q3: How much capacity do you have to coordinate two packages?

| Answer | Recommendation |
|--------|---------------|
| **Sufficient** (or have a team) | → Option A |
| **Limited** (personal project, time is precious) | → Option B |

### Q4: What is the focus for the next 6 months?

| Answer | Recommendation |
|--------|---------------|
| **tcd fast iteration** (add MCP server, context transfer, etc.) | → Option B (reduce dependency management overhead) |
| **Ecosystem building** (get more tools using tmux-bridge) | → Option A |
| **Stable operation** (neither will change much) | → Status quo is fine, defer integration |

---

## 6. My Assessment and Recommendation

Based on information gathered, I make the following factual inferences:

1. **tmux-bridge is tcd's predecessor**: Both were developed on the same day (2026-03-02) and researched the same 4 open-source projects. tcd's PRD explicitly states "CCB and codex-orchestrator are insufficient," then designs a more complete solution. tmux-bridge looks more like a first-iteration prototype.

2. **tmux-bridge's consumers are not mature**: The Nanobot adapter exists but the Nanobot orchestration system itself is still evolving; the OpenClaw interface research docs indicate an exploratory phase with no merged PRs.

3. **tcd is your actual primary tool**: 119 tests vs 36, 9 scenario documents, 3 provider implementations — the investment magnitude difference is clear.

4. **You are the sole maintainer**: No signs of team collaboration visible.

### Conclusion: Recommend Option B

**Rationale**:

- tmux-bridge has no irreplaceable independent consumers
- tcd is already the more complete implementation, just missing a few nice-to-have tricks
- Your energy should go into tcd v0.2 features (MCP server, context transfer), not coordinating interfaces across two packages
- The port is only ~145 LOC — a one-time, extremely low-cost effort

### Specific Action Plan

1. **Do immediately**: Port 5 essentials from tmux-bridge into tcd (~145 LOC, half a day's work)
2. **Document the relationship**: Note in tcd's README and CHANGELOG that it "absorbed XX capabilities from tmux-bridge"
3. **Archive tmux-bridge**: Mark its README as "merged into tcd; recommend using tcd"
4. **Keep an escape hatch**: If Nanobot or OpenClaw genuinely needs a zero-dependency lightweight driver layer in the future, extract `tcd.bridge` as a submodule package

### If Circumstances Change

| Change | Response |
|--------|----------|
| Nanobot genuinely needs tmux-bridge | Extract `tcd.bridge` package from tcd, publish as zero-dependency |
| External contributors want to maintain tmux-bridge | Option A becomes rational; hand off low-level maintenance |
| Need WezTerm/Zellij backend | Add backend abstraction layer in tcd; no need for tmux-bridge |

---

## 7. Summary Table

| Dimension | Option A (dependency) | Option B (port) | Option C (merge) | Option D (shared lib) |
|-----------|:---:|:---:|:---:|:---:|
| Eliminate duplicate code | Full | Partial | Full | Full |
| Independent consumer support | Preserved | Unaffected | Broken | Preserved |
| Maintenance cost | Two-package coordination | Single-package autonomous | Single package | Three-package coordination |
| Iteration speed | Constrained by upstream | Fully autonomous | Fully autonomous | Constrained by shared lib |
| Implementation cost | High (must generalize tmux-bridge) | Low (~145 LOC) | Medium (migrate consumers) | High (new package) |
| Long-term extensibility | Good | Sufficient | Good | Best |
| **Recommended when** | Active independent consumers | **Best current choice** | When consumers can migrate | 3+ consumer parties |
