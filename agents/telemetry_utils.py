"""
telemetry_utils.py
------------------

Helper to disable all CrewAI telemetry, tracing, and event listeners.
Must be called **before importing CrewAI** modules.
"""

import os


def disable_crewai_telemetry(verbose: bool = False):
    """
    Disable all CrewAI and OTEL telemetry/tracing.

    Call this as the very first thing in your script:
        from utils.telemetry_utils import disable_crewai_telemetry
        disable_crewai_telemetry()
        from crewai import Agent, Task, Crew

    Parameters
    ----------
    verbose : bool
        If True, prints confirmation that telemetry is disabled.
    """
    env_vars = {
        # CrewAI core flags
        "CREWAI_LOGGING_ENABLED": "0",
        "CREWAI_TELEMETRY_ENABLED": "0",
        "CREWAI_TRACING_ENABLED": "0",
        "CREWAI_DISABLE_EVENTS": "1",
        "CREWAI_EVENTS_ENABLED": "0",
        # OpenTelemetry disable flags
        "OTEL_SDK_DISABLED": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
    }

    for key, value in env_vars.items():
        os.environ[key] = value

    if verbose:
        print("🚫 CrewAI telemetry, tracing, and event listeners disabled.")


def disable_crewai_event_listeners():
    """
    Defensive runtime cleanup — safe to call *after* importing CrewAI.
    Removes any tracing/event listeners if they were already loaded.
    """
    try:
        from crewai.events import event_bus

        if hasattr(event_bus, "disable_all_listeners"):
            event_bus.disable_all_listeners()
        elif hasattr(event_bus, "listeners"):
            event_bus.listeners.clear()
    except Exception:
        pass

    # Monkey-patch trace batch manager if still active
    try:
        from crewai.events.listeners import tracing

        if hasattr(tracing, "trace_batch_manager"):
            tracing.trace_batch_manager._send_batch = lambda *a, **k: None
    except Exception:
        pass
