"""
CrewAI agent with MCP

Analyzes tenant consumption via MCP server and checks compliance with
POL-COMP-SPEND-001 (Monthly Spend Cap per Compartment).

Changes vs original:
- Added --month YYYY-MM CLI parameter
- Task now enforces POL001 (hard cap $400 at month end; soft cap "MTD + forecast <= $400")
- Saves both Markdown report and a JSON findings file
"""

import os
import json
import re
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from crewai import Agent, Task, Crew, LLM
from crewai_tools import MCPServerAdapter

from agent_utils import month_bounds, save_markdown_report
from agents_config import LITELLM_GATEWAY_URL, MCP_OCI_CONSUMPTION_URL

# Disable telemetry, tracing, and logging
os.environ["CREWAI_LOGGING_ENABLED"] = "false"
os.environ["CREWAI_TELEMETRY_ENABLED"] = "false"
os.environ["CREWAI_TRACING_ENABLED"] = "false"


# -------------------- CLI --------------------
def parse_args():
    """
    Parse command-line arguments.
    """
    p = argparse.ArgumentParser(description="OCI FinOps POL001 checker")
    p.add_argument(
        "--month",
        required=True,
        help="Month to analyze in format YYYY-MM (e.g., 2025-10)",
    )
    return p.parse_args()


# -------------------- Policy Parameters (POL001) --------------------
HARD_CAP_USD = 400
SOFT_CAP_USD = 400
# avoid noisy early-month averages
MIN_DAYS_OBSERVED_FOR_FORECAST = 3

# -------------------- Disable CrewAI telemetry/logging --------------------
os.environ["CREWAI_LOGGING_ENABLED"] = "false"
os.environ["CREWAI_TELEMETRY_ENABLED"] = "false"
os.environ["CREWAI_TRACING_ENABLED"] = "false"


def build_task_description(month_str: str, bounds: dict) -> str:
    """
    Builds the task description for the agent, including policy parameters and data requirements.
    month_str: "YYYY-MM"
    bounds: output of month_bounds()

    be careful if you want to modify this prompt, it is crucial for correct agent operation.
    """
    start_str = bounds["start"].date().isoformat()
    end_str = bounds["end"].date().isoformat()
    today_str = bounds["today"].date().isoformat()

    return f"""
You are enforcing policy POL-COMP-SPEND-001 (Monthly Spend Cap per Compartment) for the month {month_str}
in timezone Europe/Rome.

**Date window**
- Month start: {start_str}
- Month end:   {end_str}
- Today:       {today_str}
- Days observed so far: {bounds['days_observed']}
- Remaining days in month: {bounds['remaining_days']}
- Is month end today? {bounds['is_month_end']}

**Policy thresholds**
- HARD cap at month end: ${HARD_CAP_USD}
- SOFT cap at any time (forecast-to-EOM): ${SOFT_CAP_USD}
- Ignore forecasting if days_observed < {MIN_DAYS_OBSERVED_FOR_FORECAST} or if you're at month end.

**Data requirements (use MCP tools only)**
1) Retrieve **daily amount (USD)** for each **compartment** between {start_str} and {today_str} (inclusive).
   - Use amount (USD), not quantity.
   - If a weekly or monthly endpoint is easier, you must reconstruct **MTD daily** to compute average daily.

**Calculations**
- For each compartment:
  - MTD_USD = sum(daily USD from {start_str} to {today_str})
  - If days_observed >= {MIN_DAYS_OBSERVED_FOR_FORECAST}:
      avg_daily = MTD_USD / days_observed
      forecast_eom = MTD_USD + avg_daily * {bounds['remaining_days']}
    Else:
      avg_daily and forecast_eom = null
- Soft breach: forecast_eom > {SOFT_CAP_USD} (only if forecast_eom is not null)
- Hard breach: only if **today == month end** AND MTD_USD > {HARD_CAP_USD}

**Output requirements**
1) A concise Markdown report:
   - Top compartments by MTD spend (table)
   - Summary of soft/hard breaches (counts)
   - Short recommendations for any breached compartments
2) A **machine-readable JSON** called FINDINGS at the end of your answer in a fenced code block:
   ```json
   {{
     "month": "{month_str}",
     "timezone": "Europe/Rome",
     "today": "{today_str}",
     "hard_cap_usd": {HARD_CAP_USD},
     "soft_cap_usd": {SOFT_CAP_USD},
     "compartments": [
       {{
         "compartment": "<name-or-ocid>",
         "mtd_usd": 0.0,
         "avg_daily": 0.0,
         "forecast_eom": 0.0,
         "soft_breach": false,
         "hard_breach": false
       }}
     ]
   }}
   ```
Only include keys with numeric values as numbers (no strings). Keep monetary values with 2 decimals.
If data is missing for a compartment, exclude it from the JSON.
""".strip()


def save_findings_json_from_result(
    result_text: str,
    month: str,
    output_dir: str = "reports",
    timestamp: str | None = None,
) -> str | None:
    """
    Extract the last fenced ```json ... ``` block from result_text and save it
    as reports/oci_consumption_findings_<month>_<timestamp>.json.
    Returns the JSON file path if saved, else None.
    """
    # Find all fenced ```json ... ``` blocks (greedy across lines)
    pattern = r"```json\s*(.*?)\s*```"
    matches = re.findall(pattern, result_text, flags=re.DOTALL | re.IGNORECASE)
    if not matches:
        print(
            "⚠️ Could not parse FINDINGS JSON from the agent output. Check the formatting."
        )
        return None

    payload = matches[-1].strip()  # take the last fenced json block
    try:
        findings = json.loads(payload)
    except json.JSONDecodeError:
        print(
            "⚠️ The fenced JSON block could not be decoded. Check the JSON formatting."
        )
        return None

    os.makedirs(output_dir, exist_ok=True)
    if timestamp is None:
        timestamp = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(
        output_dir, f"oci_consumption_findings_{month}_{timestamp}.json"
    )
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(findings, jf, indent=2)
    print(f"✅ Findings saved to: {json_path}")
    return json_path


def main():
    """
    Main function to run the CrewAI agent for POL001 compliance checking.
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
        base_url=LITELLM_GATEWAY_URL,  # LiteLLM proxy endpoint
        api_key="sk-local-any",
        temperature=0.0,
        # we need this high otherwise json get truncated
        max_tokens=6000,
    )

    # OCI consumption MCP server
    server_params = {
        "url": MCP_OCI_CONSUMPTION_URL,
        "transport": "streamable-http",
    }

    # Create agent with MCP tools
    with MCPServerAdapter(server_params, connect_timeout=120) as mcp_tools:

        research_agent = Agent(
            role="OCI Consumption Analyst",
            goal="Analyze OCI tenant consumption and check policy compliance.",
            backstory="Expert analyst with access to OCI Consumption MCP server.",
            llm=llm,
            tools=mcp_tools,
            max_iter=50,
            max_retry_limit=5,
            verbose=True,
        )

        # ----- Task updated to include POL001 checks -----
        # We pass explicit dates/thresholds and require a small JSON findings object.
        task_description = build_task_description(args.month, bounds)

        research_task = Task(
            description=task_description,
            expected_output="Markdown report + FINDINGS JSON (as specified). "
            "Use amount (USD) only.",
            agent=research_agent,
        )

        crew = Crew(agents=[research_agent], tasks=[research_task])

        result = crew.kickoff()

        print(result)

    # --- Save the result to a Markdown file + JSON ---
    output_dir = "reports"
    timestamp, _ = save_markdown_report(
        "oci_consumption_report", str(result), args.month, output_dir
    )
    _ = save_findings_json_from_result(str(result), args.month, output_dir, timestamp)


if __name__ == "__main__":
    main()
