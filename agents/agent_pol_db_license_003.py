"""
CrewAI agent with MCP

Enforces POL-DB-LICENSE-003 (All Autonomous Databases must be BYOL).

Approach:
- Use --month YYYY-MM to define the time window for "Database" spend ranking
- Step 1: via MCP, get TOP_N compartments by 'Database' amount (USD) in the month
- Step 2: via MCP, list ADBs for those compartments and check license_model
- Hard requirement (no soft): license_model == "BRING_YOUR_OWN_LICENSE" unless exempt
- Save Markdown report + machine-readable FINDINGS JSON
"""

import os
import re
import json
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from telemetry_utils import disable_crewai_telemetry

# put before the imports
disable_crewai_telemetry()

from crewai import Agent, Task, Crew, LLM
from crewai_tools import MCPServerAdapter

from agent_utils import month_bounds, save_markdown_report
from agents_config import LITELLM_GATEWAY_URL, MCP_OCI_CONSUMPTION_URL


# -------------------- Policy Parameters (POL-DB-LIMIT-002) --------------------
ALLOWED_LICENSE_MODELS = ["BRING_YOUR_OWN_LICENSE"]  # BYOL only
EXEMPT_TAGS = []
TOP_N_COMPARTMENTS = 10


# -------------------- CLI --------------------
def parse_args():
    """
    Parse command-line arguments.
    """
    p = argparse.ArgumentParser(description="OCI FinOps POL-DB-LIMIT-002 checker")
    p.add_argument(
        "--month",
        required=True,
        help="Month to analyze in format YYYY-MM (e.g., 2025-10)",
    )
    return p.parse_args()


# -------------------- Task description builder --------------------
def build_task_description(month_str: str, bounds: dict) -> str:
    start_str = bounds["start"].date().isoformat()
    end_str = bounds["end"].date().isoformat()

    return f"""
You are enforcing **POL-DB-LICENSE-003 (BYOL license model for Autonomous Databases)**.

**Time window (for TOP list only)**
- From: {start_str}
- To:   {end_str}
- Timezone: Europe/Rome

**Policy (hard requirement)**
- Every ADB must have `license_model` in {ALLOWED_LICENSE_MODELS}.
- Exemptions via tags (any=value): {EXEMPT_TAGS}.
- There is **no soft limit**: any non-BYOL without exemption is **non-compliant**.

**Approach (minimize tool calls)**
1) Using MCP tools, compute the **TOP {TOP_N_COMPARTMENTS} compartments by 'Database' amount (USD)** within [{start_str}, {end_str}].
   - Use amount (USD), not quantity.
   - Filter/aggregate by the service equal to "Database".
   - Return a list of top compartments by spend (descending).

2) For **each of those top compartments**, use MCP to **list Autonomous Databases** (ADB).
   - Include fields: display_name, ocid, compartment, license_model, and tags (defined + freeform).
   - For each ADB:
        exempt = has ANY tag in {EXEMPT_TAGS}
        compliant = (license_model in {ALLOWED_LICENSE_MODELS}) OR exempt
   - Compute per compartment:
        total_adb = number of ADBs (all)
        non_compliant = list of ADBs where compliant == False

3) Evaluate policy (hard only):
   - hard_breach = (len(non_compliant) > 0)

**Output requirements**
1) A concise **Markdown report**:
   - Table for TOP compartments:
     [compartment, database_spend_usd, total_adb, non_compliant_count, hard_breach]
   - Under the table, if a compartment is in breach, list the offending DBs with (display_name, ocid, license_model).

2) A **machine-readable JSON** called FINDINGS at the end of your answer in a fenced code block:
   ```json
   {{
     "policy_id": "POL-DB-LICENSE-003",
     "month": "{month_str}",
     "timezone": "Europe/Rome",
     "limits": {{
       "allowed_license_models": {json.dumps(ALLOWED_LICENSE_MODELS)},
       "exempt_tags": {json.dumps(EXEMPT_TAGS)}
     }},
     "top_by_database_spend": {TOP_N_COMPARTMENTS},
     "compartments": [
       {{
         "compartment": "<name-or-ocid>",
         "database_spend_usd": 0.0,
         "total_adb": 0,
         "non_compliant_count": 0,
         "hard_breach": false,
         "non_compliant": [
           {{
             "display_name": "<db-name>",
             "ocid": "<db-ocid>",
             "license_model": "<value>"
           }}
         ]
       }}
     ]
   }}
   ```
Only include keys with numeric values as numbers (no strings). Keep monetary values with 2 decimals for spend.
Exclude compartments with missing data.
""".strip()


# -------------------- Output helpers --------------------
def save_findings_json_from_result(
    result_text: str,
    month: str,
    output_dir: str = "reports",
    tz: str = "Europe/Rome",
    timestamp: str | None = None,
) -> str | None:
    """
    Extract the last fenced ```json ... ``` (or ``` ... ```) block from result_text
    and save it as a JSON file. Returns the file path or None.
    """
    # match fenced code blocks with optional 'json' language tag
    fence_re = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    blocks = fence_re.findall(result_text)
    if not blocks:
        print("⚠️ Could not find a fenced JSON block (```json ... ```).")
        return None

    payload = blocks[-1].strip()
    # defensive: sometimes models echo backticks inside
    if payload.startswith("```") and payload.endswith("```"):
        payload = payload.strip("`").strip()

    try:
        findings = json.loads(payload)
    except json.JSONDecodeError as e:
        preview = payload[:200].replace("\n", "\\n")
        print(
            f"⚠️ JSON decode failed at pos {e.pos}: {e.msg}. First 200 chars: {preview}"
        )
        return None

    os.makedirs(output_dir, exist_ok=True)
    if timestamp is None:
        timestamp = datetime.now(ZoneInfo(tz)).strftime("%Y%m%d_%H%M%S")

    json_path = os.path.join(
        output_dir, f"oci_db_license_findings_{month}_{timestamp}.json"
    )
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(findings, jf, indent=2)
    print(f"✅ Findings saved to: {json_path}")
    return json_path


# -------------------- Main agent logic --------------------
def main():
    """
    Main function to run the CrewAI agent for POL-DB-LIMIT-002.
    """
    args = parse_args()
    try:
        year, month = map(int, args.month.split("-"))
        bounds = month_bounds(year, month, tz="Europe/Rome")
    except Exception as e:
        raise SystemExit(
            f"❌ Invalid --month value '{args.month}'. Use YYYY-MM. Error: {e}"
        ) from e

    # LLM
    llm = LLM(
        model="grok4-oci",
        base_url=LITELLM_GATEWAY_URL,
        api_key="sk-local-any",
        temperature=0.0,
        max_tokens=6000,
    )

    # MCP server (must expose cost aggregate + ADB inventory tools)
    server_params = {
        "url": MCP_OCI_CONSUMPTION_URL,
        "transport": "streamable-http",
    }

    with MCPServerAdapter(server_params, connect_timeout=120) as mcp_tools:
        # Optional: print tool names to confirm both are available
        # print(f"Available tools: {[tool.name for tool in mcp_tools]}")

        agent = Agent(
            role="ADB License Compliance Analyst",
            goal="Ensure ADBs in the top spender compartments use BYOL, minimizing tool calls.",
            backstory="FinOps-oriented analyst using OCI Consumption and Inventory MCP tools.",
            llm=llm,
            tools=mcp_tools,
            max_iter=100,
            max_retry_limit=5,
            verbose=True,
        )

        task = Task(
            description=build_task_description(args.month, bounds),
            expected_output=(
                "Markdown report + FINDINGS JSON (as specified). "
                "Use amount (USD) only for 'Database' spend; list ADBs and verify license_model."
            ),
            agent=agent,
        )

        crew = Crew(agents=[agent], tasks=[task])

        result = crew.kickoff()

        print(result)

    # Save outputs
    output_dir = "reports"
    timestamp, _ = save_markdown_report(
        "db_license_report", str(result), args.month, output_dir
    )
    _ = save_findings_json_from_result(
        str(result),
        args.month,
        output_dir=output_dir,
        timestamp=timestamp,  # <-- keyword, so tz keeps default "Europe/Rome"
    )


if __name__ == "__main__":
    main()
