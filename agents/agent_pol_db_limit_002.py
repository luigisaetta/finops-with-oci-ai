"""
CrewAI agent with MCP

Enforces POL-DB-LIMIT-002 (Autonomous Database Count Limit per Compartment).

Approach:
- Use --month YYYY-MM to define the time window for "Database" spend ranking
- Step 1: via MCP, get TOP_N compartments by 'Database' amount (USD) in the month
- Step 2: via MCP, count Autonomous Databases for each of those top compartments
- Apply soft/hard limits (actuals only, no forecast)
- Save Markdown report + machine-readable FINDINGS JSON
"""

import os
import re
import json
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from crewai import Agent, Task, Crew, LLM
from crewai_tools import MCPServerAdapter

from agents_config import LITELLM_GATEWAY_URL, MCP_OCI_CONSUMPTION_URL

# -------------------- Disable CrewAI telemetry/logging --------------------
os.environ["CREWAI_LOGGING_ENABLED"] = "false"
os.environ["CREWAI_TELEMETRY_ENABLED"] = "false"
os.environ["CREWAI_TRACING_ENABLED"] = "false"

# -------------------- Policy Parameters (POL-DB-LIMIT-002) --------------------
SOFT_LIMIT_COUNT = 2
HARD_LIMIT_COUNT = 4
EXEMPT_TAGS = ["HighAvailability", "Clustered", "DR"]  # exclude from effective count
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


# -------------------- Date helpers --------------------
def month_bounds(year: int, month: int, tz: str = "Europe/Rome"):
    """
    Compute start/end dates for a given month/year in tz.
    """
    z = ZoneInfo(tz)
    start = datetime(year, month, 1, tzinfo=z)
    next_month = datetime(year + (month // 12), ((month % 12) + 1), 1, tzinfo=z)
    end = next_month - timedelta(days=1)
    return {"tz": tz, "start": start, "end": end}


# -------------------- Task description builder --------------------
def build_task_description(month_str: str, bounds: dict) -> str:
    """
    Builds the task description for the agent, including policy parameters and data requirements.
    month_str: "YYYY-MM"
    bounds: output of month_bounds()

    be careful if you want to modify this prompt, it is crucial for correct agent operation.
    """
    start_str = bounds["start"].date().isoformat()
    end_str = bounds["end"].date().isoformat()

    return f"""
You are enforcing **POL-DB-LIMIT-002 (Autonomous Database Count Limit per Compartment)**.

**Time window (actuals only)**
- From: {start_str}
- To:   {end_str}
- Timezone: Europe/Rome

**Policy thresholds**
- SOFT limit: ≤ {SOFT_LIMIT_COUNT} Autonomous Databases (ADB) per compartment
- HARD limit: ≤ {HARD_LIMIT_COUNT} Autonomous Databases (ADB) per compartment
- Exempt tags (excluded from effective count): {EXEMPT_TAGS}

**Approach (minimize tool calls)**
1) Using MCP tools, compute the **TOP {TOP_N_COMPARTMENTS} compartments by 'Database' amount (USD)** within [{start_str}, {end_str}].
   - Use amount (USD) not quantity.
   - Filter/aggregate by service/category equivalent to "Database".
   - Return a list of top compartments by spend (descending).

2) For **each of those top compartments**, use MCP to **list/count Autonomous Databases** (ADB).
   - Include defined and freeform tags so we can detect exemptions.
   - Compute:
        total_count = number of ADBs (all)
        exempted_count = number of ADBs that have ANY of the exempt tags {EXEMPT_TAGS}
        effective_count = total_count - exempted_count

3) Evaluate policy:
   - soft_breach = (effective_count > {SOFT_LIMIT_COUNT})
   - hard_breach = (effective_count > {HARD_LIMIT_COUNT})

**Output requirements**
1) A concise **Markdown report**:
   - Table: TOP compartments by 'Database' spend with columns:
     [compartment, database_spend_usd, total_count, exempted_count, effective_count, soft_breach, hard_breach]
   - Short recommendations for any breached compartments (e.g., consolidate, justify with tags, decommission).

2) A **machine-readable JSON** called FINDINGS at the end of your answer in a fenced code block:
   ```json
   {{
     "policy_id": "POL-DB-LIMIT-002",
     "month": "{month_str}",
     "timezone": "Europe/Rome",
     "limits": {{
       "soft": {SOFT_LIMIT_COUNT},
       "hard": {HARD_LIMIT_COUNT},
       "exempt_tags": {json.dumps(EXEMPT_TAGS)}
     }},
     "top_by_database_spend": {TOP_N_COMPARTMENTS},
     "compartments": [
       {{
         "compartment": "<name-or-ocid>",
         "database_spend_usd": 0.0,
         "total_count": 0,
         "exempted_count": 0,
         "effective_count": 0,
         "soft_breach": false,
         "hard_breach": false
       }}
     ]
   }}
   ```
Only include keys with numeric values as numbers (no strings). Keep monetary values with 2 decimals for spend.
Exclude compartments with missing data.
""".strip()


# -------------------- Output helpers --------------------
def save_markdown_report(
    result_text: str, month: str, output_dir: str = "reports", tz: str = "Europe/Rome"
) -> tuple[str, str]:
    """
    Save the Markdown report to a file.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now(ZoneInfo(tz)).strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(output_dir, f"oci_db_limit_report_{month}_{timestamp}.md")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result_text)

    print(f"\n✅ Report saved successfully to: {md_path}")
    return timestamp, md_path


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
        output_dir, f"oci_db_limit_findings_{month}_{timestamp}.json"
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
            role="ADB Density Compliance Analyst",
            goal="Check compartments against ADB count limits using minimal tool calls.",
            backstory="FinOps-oriented analyst using OCI Consumption and Inventory MCP tools.",
            llm=llm,
            tools=mcp_tools,
            max_iter=50,
            max_retry_limit=5,
            verbose=True,
        )

        task = Task(
            description=build_task_description(args.month, bounds),
            expected_output=(
                "Markdown report + FINDINGS JSON (as specified). "
                "Use amount (USD) only for Database spend; count Autonomous Databases via inventory."
            ),
            agent=agent,
        )

        crew = Crew(agents=[agent], tasks=[task])

        result = crew.kickoff()

        print(result)

    # Save outputs
    output_dir = "reports"
    timestamp, _ = save_markdown_report(str(result), args.month, output_dir)
    _ = save_findings_json_from_result(
        str(result),
        args.month,
        output_dir=output_dir,
        timestamp=timestamp,  # <-- keyword, so tz keeps default "Europe/Rome"
    )


if __name__ == "__main__":
    main()
