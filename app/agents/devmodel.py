"""Scripted stand-in for Gemini, for offline tests and UI development only.

Enabled in the web app only when CT_DEV_SCRIPTED=1. It replays the stored
extraction of bundled sample documents and calls the analysis tools in a
realistic order. It is never used in production.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

GOLD = Path(__file__).resolve().parent.parent.parent / "samples" / "golden"
DELAY = float(os.getenv("CT_DEV_DELAY", "0"))

SCRIPT = {
    "scope_quality_agent": [("get_activity_data", {}), ("check_cbam_scope", {"cn_codes": ["731815", "731816", "7213"]}),
                            ("run_data_quality_checks", {}),
                            ("record_finding", {"severity": "high", "title": "Wire rod on default values",
                                                "explanation": "No supplier data.", "recommended_action": "Ask the mill."})],
    "calculation_agent": [("calculate_embedded_emissions", {}), ("compare_with_eu_defaults", {})],
    "advisor_agent": [("compare_with_eu_defaults", {}), ("simulate_scenario", {"precursor_see": 0.9}),
                      ("simulate_scenario", {"fuel_switch_to": "natural_gas"})],
}


class ScriptedLlm(BaseLlm):
    model: str = "scripted"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        if DELAY:
            await asyncio.sleep(DELAY)
        # Intake: find the document bytes in the request and replay its stored extraction.
        for c in llm_request.contents:
            for p in c.parts or []:
                if getattr(p, "inline_data", None) and p.inline_data.data:
                    sha = hashlib.sha256(p.inline_data.data).hexdigest()
                    text = (GOLD / f"{sha}.json").read_text()
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))
                    return
        tools = set((llm_request.tools_dict or {}).keys())
        agent = next((a for a, steps in SCRIPT.items() if steps[0][0] in tools and {s[0] for s in steps} <= tools), None)
        if agent is None:  # supplier / regulation agents
            sysi = str(llm_request.config.system_instruction or "")
            if "Supply-Chain" in sysi:
                txt = json.dumps({"subject": "CBAM data request", "body": "Dear team, please share SEE data. Regards"})
            else:
                txt = "Only direct emissions count for steel [K3]."
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=txt)]))
            return
        steps = SCRIPT[agent]
        idx = sum(1 for c in llm_request.contents for p in (c.parts or []) if getattr(p, "function_response", None))
        if idx < len(steps):
            name, args = steps[idx]
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=_summary(agent))]))




def _summary(agent: str) -> str:
    return {
        "scope_quality_agent": "Data is good enough to calculate. Bolts and nuts (7318 15/16) are CBAM goods; only direct emissions count. The biggest gap: the wire rod is on the EU default value because the mill has not shared its emissions data.",
        "calculation_agent": "SEE is about 5.36 tCO2e per tonne. Roughly 87% comes from the purchased wire rod (EU default 4.32 tCO2e/t) and 13% from LPG burned in the heat-treatment furnace. Diesel for the DG set was excluded as an electricity emission. This is below the EU default for bolts from India (6.29 with the 2026 mark-up).",
        "advisor_agent": "### What this means for you\nYour own data already beats the EU default. On this quarter's EU shipments the gross cost is about EUR 64.5k with your data vs EUR 73.9k on defaults.\n### Your biggest levers\n- Get the mill's verified wire rod data (largest lever).\n- A scrap-based EAF supplier would cut SEE sharply.\n- Switching furnace LPG to natural gas helps a little.\n- Solar power does not change the EU CBAM figure for steel.\n### Do this next\n- Send the data request to Shivalik Wire & Steel today.",
    }.get(agent, f"{agent} done.")
