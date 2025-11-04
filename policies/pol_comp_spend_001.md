---
id: POL_COMP_SPEND_001
version: 1.0.0
title: Monthly Spend Cap per Compartment
# proposed | active | deprecated
status: proposed            
owners: ["finops@company.example"]
scope:
  resource: "compartment"
  # list of compartment names
  selector: { include: ["*"], exclude: [] }   
timezone: "Europe/Rome"
parameters:
  hard_cap_usd: 400
  soft_cap_usd: 400                            # projection-to-EOM must stay <= this
  min_days_observed: 3                         # avoid noisy first days
  warning_threshold_pct_of_final: 0.80         # optional early warning
checks:
  - id: HARD_CAP_MTD
    severity: high
    description: "MTD actual spend must be <= hard_cap_usd at month end."
    evaluate:
      mcp: "mcp://oci/cost.aggregate"
      inputs: { window: "MTD", group_by: ["compartment"] }
      logic: |
        if today == month_end:
            breach = (MTD_USD > params.hard_cap_usd)
        else:
            breach = false
    evidence: ["MTD_USD","params.hard_cap_usd","today","month_end"]
    remediation:
      - "Reduce spend or raise approved budget before month close."

  - id: SOFT_CAP_FORECAST
    severity: high
    description: "At any time, MTD + projected remainder must be <= soft_cap_usd."
    evaluate:
      mcp: "mcp://oci/cost.aggregate"
      inputs: { window: "MTD_DAILY", group_by: ["compartment"] }
      logic: |
        if days_observed >= params.min_days_observed:
            avg_daily = sum(MTD_DAILY_USD) / days_observed
            forecast_eom = sum(MTD_DAILY_USD) + avg_daily * remaining_days_in_month
            breach = (forecast_eom > params.soft_cap_usd)
        else:
            breach = false
    evidence: ["MTD_DAILY_USD","avg_daily","forecast_eom","params.soft_cap_usd","days_observed"]
    remediation:
      - "Throttle non-critical jobs"
      - "Defer experiments to next month"
      - "Request budget adjustment with justification"

exemptions:
  tags_any: ["BudgetExempt=true"]              # optional override
  approval_process: "Ticket in FINOPS-QUEUE"
outputs:
  finding_key: "COMP:{compartment_ocid}:POL-COMP-SPEND-001"
  metrics:
    - "MTD_USD"
    - "forecast_eom"
    - "avg_daily"
references:
  finops_principles: "Inform → Optimize → Operate"
  related_policies: []
---

# Monthly Spend Cap per Compartment

## Intent 
Keep compartment-level cloud spend aligned to approved limits while allowing early warning and course correction.

## Hard Cap 
By the last calendar day of the month, the compartment’s **MTD actual** must be **≤ $400**.

## Soft Cap (Forecast)
On any day, the **projected end-of-month** spend (MTD actual + average daily × remaining days) must be **≤ $400**.

## Notes & Edge Cases
- Ignore the first few days (`min_days_observed`) to avoid unstable averages.
- Use business calendar if applicable; otherwise calendar month.
- Temporary breach can be exempted via `BudgetExempt=true` after approval.

## Remediation Examples
- Pause non-critical workloads; postpone experiments.
- Rightsize or schedule resources; enforce lifecycle rules for storage.
- If justified, initiate a budget increase request.
