---
id: POL-DB-LIMIT-002
version: 1.0.0
title: Autonomous Database Count Limit per Compartment
status: proposed               # proposed | active | deprecated
owners: ["finops@company.example"]
scope:
  resource: "compartment"
  selector: { include: ["*"], exclude: [] }   # list of compartment names
timezone: "Europe/Rome"
parameters:
  soft_limit_count: 2
  hard_limit_count: 4
  exempt_tags: ["HighAvailability", "Clustered", "DR"]
checks:
  - id: DB_COUNT_SOFT
    severity: medium
    description: "Warn when number of Autonomous Databases in a compartment exceeds the soft limit."
    evaluate:
      inputs: { group_by: ["compartment"], resource_type: "AutonomousDatabase" }
      logic: |
        effective_count = count(AutonomousDatabases where not any_tag(params.exempt_tags))
        breach = (effective_count > params.soft_limit_count)
    evidence: ["effective_count", "params.soft_limit_count"]
    remediation:
      - "Evaluate consolidation opportunities for development/test databases."
      - "Consider converting seldom-used instances to shared or smaller shapes."
      - "Tag justified databases with an exemption tag (e.g., HighAvailability=true)."

  - id: DB_COUNT_HARD
    severity: high
    description: "Hard limit of 4 Autonomous Databases per compartment (excluding exempted ones)."
    evaluate:
      inputs: { group_by: ["compartment"], resource_type: "AutonomousDatabase" }
      logic: |
        effective_count = count(AutonomousDatabases where not any_tag(params.exempt_tags))
        breach = (effective_count > params.hard_limit_count)
    evidence: ["effective_count", "params.hard_limit_count", "params.exempt_tags"]
    remediation:
      - "Consolidate or decommission redundant databases."
      - "Request approval for exception via FinOps governance board."
      - "Use tagging to document HA/DR configurations."

exemptions:
  tags_any: ["DBExempt=true", "HighAvailability=true", "Clustered=true", "DR=true"]
  approval_process: "Ticket in FINOPS-QUEUE"
outputs:
  finding_key: "COMP:{compartment_ocid}:POL-DB-LIMIT-002"
  metrics:
    - "effective_count"
references:
  finops_principles: "Visibility → Optimization → Governance"
  related_policies: ["POL-COMP-SPEND-001"]
---

# Autonomous Database Count Limit per Compartment

## Intent
Control the proliferation of Autonomous Databases to prevent unnecessary cost and administrative overhead.  
Promote consolidation of lightly used databases and ensure that every active instance has a clear business justification.

## Policy Summary
Each **compartment** should maintain a controlled number of **Autonomous Databases (ADB)**:

- **Soft limit:** 2 ADBs per compartment — exceeding this triggers a **warning** and a review.
- **Hard limit:** 4 ADBs per compartment — exceeding this requires formal **exception approval**.

Instances tagged with `HighAvailability=true`, `Clustered=true`, or `DR=true` are excluded from the count.

## Evaluation Logic
- Count all ADB instances within each compartment.
- Ignore those containing any exemption tags listed above.
- Compare the resulting `effective_count` against both **soft** and **hard** thresholds.

## Notes & Edge Cases
- Development and test compartments are expected to remain under the soft limit.
- Production environments may justify additional ADBs for HA or DR purposes, but must be properly tagged.
- Temporary overages must be resolved or approved within the FinOps governance process.

## Remediation Examples
- Consolidate multiple low-usage databases into shared or larger multi-tenant instances.
- Decommission stale environments or snapshots.
- Use tagging to justify necessary duplicates (HA, DR).
- Request approval for sustained over-limit usage via the FinOps ticket queue.

