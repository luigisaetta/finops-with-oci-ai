---
id: POL-DB-LICENSE-003
version: 1.0.0
title: Enforce BYOL License Model for Autonomous Databases
status: proposed               # proposed | active | deprecated
owners: ["finops@company.example"]
scope:
  resource: "compartment"      # evaluated per compartment
  selector: { include: ["*"], exclude: [] }   # list of compartment names
timezone: "Europe/Rome"
parameters:
  allowed_license_models: ["BRING_YOUR_OWN_LICENSE"]   # BYOL only
  exempt_tags: [] # any=value → exempt
checks:
  - id: ADB_LICENSE_BYOL_ONLY
    severity: high
    description: "All Autonomous Databases must use the BYOL license model."
    evaluate:
      inputs: { resource_type: "AutonomousDatabase", include_fields: ["display_name","ocid","license_model","tags","compartment"] }
      logic: |
        non_compliant = [
          db for db in ADB_list
          if (db.license_model not in params.allowed_license_models)
             and not any_tag(params.exempt_tags, db.tags)
        ]
        breach = (len(non_compliant) > 0)
    evidence: ["non_compliant[].display_name","non_compliant[].ocid","non_compliant[].license_model"]
    remediation:
      - "Convert the database to BYOL if supported, or recreate with license_model=BRING_YOUR_OWN_LICENSE."
      - "If temporarily justified (e.g., migration), tag the DB with 'MigrationInProgress=true' and set an expiry."
      - "Coordinate with procurement to ensure entitlement coverage for BYOL assets."

exemptions:
  tags_any: []   # temporary or approved exceptions
  approval_process: "Ticket in FINOPS-QUEUE with business justification and expiry date"
outputs:
  finding_key: "COMP:{compartment_ocid}:POL-DB-LICENSE-003"
  metrics:
    - "non_compliant_count"
    - "non_compliant[]"
references:
  finops_principles: "Governance → Accountability → Optimization"
  related_policies: ["POL-DB-LIMIT-002","POL-COMP-SPEND-001"]
---

# Enforce BYOL License Model for Autonomous Databases

## Intent
Ensure all Autonomous Databases (ADB) use **Bring Your Own License (BYOL)** to align with enterprise licensing strategy and reduce recurring cloud license costs.

## Policy Summary
- **Hard requirement:** Every ADB must have `license_model = BRING_YOUR_OWN_LICENSE`.
- No soft threshold: any non-BYOL ADB without an approved exemption is **non-compliant**.

## Evaluation Logic
- For each compartment, list all ADBs and read their `license_model`.
- Exclude databases carrying any exemption tags (e.g., `LicenseExempt=true`, `MigrationInProgress=true`).
- If **any** remaining ADB is not `BRING_YOUR_OWN_LICENSE`, raise a **High** severity breach and list offenders.

## Notes & Edge Cases
- Periodically review exemptions and remove them once the workload is converted to BYOL.
- If conversion-in-place is unsupported, export and recreate the DB with the correct `license_model`.

## Remediation Examples
- Convert the license model to **BYOL** (if supported) or **recreate** the ADB with `license_model=BRING_YOUR_OWN_LICENSE`.
- Align with **procurement** to validate license entitlements and maintain compliance.
