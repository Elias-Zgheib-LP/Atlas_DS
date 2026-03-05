# CG-IDF v2 — Application Report
**Incentive Design Forensic Engine | v2.0.0**

---

## 1. What Is CG-IDF v2?

CG-IDF v2 (Consumer-Grade Incentive Design Forensic engine) is an AI-powered audit tool that analyses a mobile or web app's user interface for **incentive design patterns** — mechanisms that drive engagement, monetisation, retention, or social behaviour. It also detects **dark patterns**: manipulative design tactics that exploit psychological biases.

The analyst uploads screenshots of key app surfaces, and the engine automatically produces a structured, scored report mapping every detected pattern to the evidence that supports it.

---

## 2. Application Structure

The Streamlit front-end is a thin shell over a five-stage backend pipeline. The two main screens are **Evidence Collection** and **Audit Results**.

### 2.1 Evidence Collection Screen

![Evidence Collection](screenshots/evidence_collection.png)

| Element | Purpose |
|---|---|
| **Sidebar — LLM Provider** | Choose OpenAI or Anthropic as the analysis engine |
| **Sidebar — Model Override** | Optionally pin a specific model (e.g. `gpt-4o`, `claude-sonnet-4-6`) |
| **Add New Evidence panel** | Upload one or more screenshots; each is auto-assigned an ID (`ev_001`, `ev_002`, …) |
| **Captured Items table** | Confirms uploaded files and their IDs before the audit starts |
| **Start Incentive Audit button** | Triggers the full five-stage pipeline |
| **Audit Complete badge** | Replaces the button once the run finishes |

**Design notes:**
- Evidence IDs are deterministic (`ev_001` … `ev_N`), keeping the pipeline reproducible.
- The sidebar reminder about API keys (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) surfaces the only hard deployment dependency.

---

### 2.2 Audit Results Screen

#### 2.2.1 Score Summary Bar

![Audit Results](screenshots/audit_results.png)

| Metric | Value in Example Run |
|---|---|
| Overall Score | **0.9** |
| Flags Detected | **5** |
| Summary text | Highest incentive layer, flag breakdown |
| Download JSON Report | Full structured output as a file |

#### 2.2.2 Incentive Profile Radar Chart

The radar chart plots rollup scores for all five analysis layers simultaneously, giving an at-a-glance "shape" of the app's incentive footprint. A layer that fills the outer ring (score ≈ 1.0) is saturated with detectable patterns; a collapsed axis (score ≈ 0) means evidence was absent or inconclusive.

#### 2.2.3 Layer Scores Bar Chart

Horizontal bars give the numeric rollup per layer:

| Layer | Score (Example Run) |
|---|---|
| Engagement Incentives | 0.93 |
| Monetisation Incentives | 1.00 |
| Retention Incentives | 1.00 |
| Social & Sharing Incentives | 1.00 |
| Dark Pattern Detection | 0.65 |

#### 2.2.4 Audit Flags Panel

![Audit Flags & Breakdown](screenshots/audit_flags.png)

Collapsible flag groups show every issue raised during the pipeline run:

| Flag | Count | Meaning |
|---|---|---|
| `MISSING_SURFACE` | 1 | A required surface (home feed / checkout / onboarding) was not provided |
| `MISSING_ANSWER` | 2 | The LLM could not answer a question from the available evidence |
| `LOW_CONFIDENCE` | 2 | The LLM answered but with confidence below the 0.6 threshold |

#### 2.2.5 Detailed Breakdown Panel

A layer selector lets the analyst drill into individual questions. Each question shows:
- **Status icon** — green check (answered & verified), red question mark (missing/low-confidence)
- **Question ID and text** — e.g. `ENG_01: What UI patterns drive the user to spend more time in the app?`
- **Expandable detail** — LLM answer, confidence score, supporting evidence references, any verification notes from Provider B

---

## 3. Backend Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Streamlit UI                              │
│  Evidence upload  →  AuditState init  →  Results rendering      │
└───────────────────────────┬──────────────────────────────────────┘
                            │  compiled_graph.invoke(state)
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LangGraph Pipeline                                  │
│                                                                             │
│  ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐  │
│  │  Provider A     │────▶│  Rules Engine    │────▶│  Provider B         │  │
│  │  (LLM, Step 2)  │     │  (Python, Step 3)│     │  (LLM, Step 4)      │  │
│  │                 │     │                  │  ┌─▶│  [only if queue > 0]│  │
│  │  Extracts facts │     │  Validates       │  │  │                     │  │
│  │  Answers 20 Qs  │     │  Flags issues    │  │  │  Verifies flagged   │  │
│  │  across 5 layers│     │  Builds review   │──┘  │  answers text-only  │  │
│  │  (multimodal)   │     │  queue           │     │                     │  │
│  └─────────────────┘     └──────────────────┘     └──────────┬──────────┘  │
│                                    │                          │             │
│                                    │ [queue empty]            │             │
│                                    ▼                          ▼             │
│                          ┌──────────────────────────────────────────────┐  │
│                          │  Merge + Scoring  (Python, Step 5)           │  │
│                          │                                              │  │
│                          │  Apply verifications · Adjust confidence     │  │
│                          │  Compute layer rollups · Overall score       │  │
│                          │  Assemble FinalReport                        │  │
│                          └──────────────────────┬───────────────────────┘  │
└─────────────────────────────────────────────────┼───────────────────────────┘
                                                  ▼
                                        FinalReport (JSON)
```

### 3.1 File Map

```
cg-idf-v2/
├── app.py                  # Streamlit UI
├── graph.py                # LangGraph DAG definition + conditional routing
├── schema.py               # Pydantic models (AuditState, FinalReport, Layer, …)
├── llm.py                  # Unified OpenAI / Anthropic client
├── main.py                 # CLI entry point
└── nodes/
    ├── provider_a.py       # Step 2 — multimodal LLM analysis
    ├── rules_engine.py     # Step 3 — deterministic validation
    ├── provider_b.py       # Step 4 — text-only verification
    └── merge_scoring.py    # Step 5 — confidence adjustment + scoring
```

### 3.2 Node-by-Node Summary

#### Provider A — Primary LLM Analysis
- Receives all evidence items and their base64-encoded screenshots
- Answers **20 questions** spread across five layers (4 per layer)
- Marks every answer as `supported` (has evidence refs), `inferred` (no direct ref), or `unknown`
- Automatically demotes a "supported" claim to "inferred" if no `evidence_refs` were cited

#### Rules Engine — Deterministic Gating
- Checks that required surfaces (`home_feed`, `checkout`, `onboarding`) are present
- Flags `UNSUPPORTED_CLAIM`, `MISSING_ANSWER`, `LOW_CONFIDENCE` (< 0.6), `MISSING_SURFACE`
- Populates a `review_queue` — only items in this queue proceed to Provider B

#### Provider B — Verification LLM
- Text-only (no images), lower cost, fast
- For each queued item: returns `confirm`, `downgrade`, `contradiction`, `insufficient_evidence`, or `missing_evidence`
- Provides a revised confidence and rationale

#### Merge + Scoring — Final Assembly
- `downgrade` → confidence × 0.6 (floor: 0.1)
- `contradiction` → confidence forced to 0.1, contradiction flag raised
- `insufficient_evidence` / `missing_evidence` → confidence × 0.6
- Layer rollup = mean confidence of **all** questions (unanswered = 0.0, so gaps penalise the score)
- Overall score = mean of all layer rollups
- Produces `FinalReport` with audit_id, timestamp, full layer data, flags, and summary

### 3.3 Data Model Flow

```
Evidence[] ──▶ AuditState ──▶ screen_facts[]
                          ──▶ layers{} ──▶ questions[] ──▶ rollup_score
                          ──▶ review_queue[]
                          ──▶ verifications[]
                          ──▶ pipeline_flags[]
                          ──▶ final_report ──▶ JSON output
```

---

## 4. Output Analysis

### 4.1 What the Example Run Tells Us

The sample run audited a single screenshot (`ev_001`) of what appears to be a consumer app. Despite limited evidence, the engine produced scores across all five layers:

| Observation | Interpretation |
|---|---|
| Monetisation, Retention, Social all at **1.00** | Provider A found strong, high-confidence signals in the single screenshot |
| Dark Pattern Detection at **0.65** | Partial evidence — some patterns were inferred rather than directly supported |
| 1× `MISSING_SURFACE` | The screenshot didn't cover one of the three required surfaces |
| 2× `MISSING_ANSWER` | Two questions had no answerable evidence |
| 2× `LOW_CONFIDENCE` | Two answers were below the 0.6 threshold |
| Overall score **0.9 / 0.92** | High overall — reflects strong detected presence of incentive mechanisms |

> **Note on score interpretation:** A high score does NOT mean "good design." It means the app is *dense with detectable incentive patterns*. A score of 1.0 on Dark Pattern Detection would be a red flag, not a positive.

---

## 5. What Can Be Improved in the Output

### 5.1 Score Interpretation Guidance
**Current state:** The scores (0.93, 1.00, 0.65, etc.) are displayed without any contextual meaning.
**Improvement:** Add a legend or threshold bands:
- 🟢 0.0 – 0.3: Low pattern density
- 🟡 0.3 – 0.6: Moderate
- 🔴 0.6 – 1.0: High density (warrants further review)

This is especially critical for **Dark Pattern Detection**, where a high score should surface a warning rather than appearing the same as a high Engagement score.

### 5.2 Flag Presentation — Show the Answer, Not Just the Code
**Current state:** Flags are listed as `FlagCode.MISSING_ANSWER (2)` — raw enum names.
**Improvement:** Strip the `FlagCode.` prefix and show human-readable labels with plain-English descriptions:
- `Missing Answer (2)` — "The LLM could not find evidence to answer these questions."
- `Low Confidence (2)` — "These answers may be unreliable; consider adding more screenshots."

### 5.3 Per-Question Evidence Traceability
**Current state:** The Detailed Breakdown shows questions and answers but doesn't make evidence links visually prominent.
**Improvement:** Inline thumbnails of the referenced screenshots next to each question answer, so a human reviewer can immediately verify the claim visually.

### 5.4 Contradictions Section Is Hidden
**Current state:** Contradictions are listed as strings inside the JSON but have no dedicated UI panel.
**Improvement:** Add a "Contradictions Detected" section in the results page, shown only when `contradictions[]` is non-empty, with the question, Provider A's original answer, and Provider B's counter-finding side by side.

### 5.5 Single-Evidence Limitation Warning
**Current state:** The run scored three layers at 1.00 from a single screenshot — which is statistically fragile.
**Improvement:** Show a banner when `len(evidence) < 3`: *"Audit based on limited evidence. Scores may not reflect the full product."* Also consider weighting rollup scores by evidence count.

### 5.6 Download Format Options
**Current state:** Only JSON download is available.
**Improvement:** Offer a PDF export (formatted report with radar chart embedded) and a CSV export of the question-level data for spreadsheet analysis.

### 5.7 Radar Chart Axis Direction for Dark Patterns
**Current state:** All five axes point outward, making a "bigger shape" look uniformly better.
**Improvement:** Invert the Dark Pattern Detection axis (or use a separate colour) so that the radar chart shape intuitively represents a *good* product profile — large engagement/retention, small dark-pattern footprint.

### 5.8 Audit History / Comparison
**Current state:** Each run is standalone with no persistence.
**Improvement:** Store run history in a local SQLite or file-based store, and allow overlaying two radar charts to compare app versions or competitors.

---

## 6. Summary

CG-IDF v2 is a well-structured, two-LLM audit pipeline with a clean Streamlit front-end. Its main strengths are the deterministic rules layer (which prevents LLM hallucinations from silently polluting scores), the conditional Provider B verification step (efficient and targeted), and the structured JSON output (fully machine-readable). The areas most worth improving are around **output communication** — making scores interpretable, flags readable, and evidence traceable — rather than the underlying pipeline logic, which is sound.

---

*Report generated from CG-IDF v2 source code and UI screenshots. Pipeline Run ID visible in example output: `0168fc35-52f1-4257-bb93-1f11b6769772`.*
