# Architecture

## System overview

```mermaid
flowchart LR
    U[Warehouse user] --> P[PlannerAgent\nReAct orchestrator]
    P --> T[text2sql\n5 intents]
    P --> S[SopIndex\nTF-IDF over 4 SOPs]
    P --> C[Carrier signal\nfake / live]
    P --> SL[Slotting optimizer\nABC-velocity moves]
    T --> G[SELECT-only gateway\nSEC-SELECT-01 / SEC-WRITE-02 / SEC-VIEW-03\n+ SQLite authorizer]
    G --> V[(4 read-only views\nSQLite telemetry)]
    SL --> V
    P --> RP[Escalation tiers T1-T4\n+ confirmation gates]
    P --> A[Hash-chained audit log]
    F[feedback.py\nPII-redacted] --> A
```

## Data flow for one question

```mermaid
sequenceDiagram
    participant U as User
    participant P as PlannerAgent
    participant T as text2sql
    participant G as Gateway
    participant S as SOP RAG
    participant C as Carrier
    participant A as Audit log
    U->>P: question
    P->>A: record question_received
    P->>T: detect_intents (fan-out)
    T->>G: SELECT over allowlisted views
    G-->>T: row dicts (capped)
    P->>A: record tool.telemetry
    P->>S: cited SOP excerpts (or explicit not-found)
    P->>C: delay signal (or explicit gap)
    P->>P: assess tier, maybe mint gate token
    P->>A: record answer_synthesized
    P-->>U: answer + citations + tier + gate token
```

## Slotting optimizer loop

```mermaid
flowchart TD
    A[ABC velocity classes\nfrom pick history] --> B[Ideal zone rank per SKU]
    C[Current slot zones\nfrom telemetry] --> D[Rank mismatch?]
    B --> D
    D -->|mis-slotted| E[Compute saving:\npicks/day x delta-distance x 2]
    E --> F[Rank moves by m/day saved]
    F --> G{Labor over 8h?}
    G -->|yes| H[Confirmation gate]
    G -->|no| I[Execute on night shift]
```

## Trust boundaries

- The planner never touches base tables: only the four views, through the
  gateway. A prompt-injection attempt that smuggles `DROP` or a second
  statement dies at SEC-SELECT-01 / SEC-WRITE-02 with a cited rule ID.
- The carrier feed is untrusted input: the fake backend is deterministic,
  the live backend degrades to an explicit gap, never a hallucinated ETA.
- Feedback free text is PII-redacted before it enters the append-only
  audit chain (emails and 10+ digit numbers become placeholders).
- Reslot plans above 8 labor-hours and every wave release need a human
  confirmation token before anything executes.
