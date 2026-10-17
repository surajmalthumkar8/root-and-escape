"""Runs the real ADK runner and the Root & Escape tools with a scripted fake model, so no network is needed.
This proves the agent wiring: tool calls, evidence logging, output_schema, revision after Challenger rejection."""
import asyncio
import json
from typing import AsyncGenerator

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from rae.agents import GeminiAgents
from rae.data.cases import get_seed
from rae.data.generator import generate
from rae.pipeline import run_investigation

PLAN = [("is_is_not", {}), ("param_excursions", {"press": "P4"}), ("inspection_changes", {"press": "P4"}),
        ("stratify", {"by": "crew", "within": "press"}), ("containment", {"press": "P4"})]

GOOD = {"insufficient_evidence": False, "summary": "Press P4 dryer failure.",
        "occurrence_statement": "Occurrence: the resin dryer on press P4 failed, so wet resin made brittle clips.",
        "occurrence_evidence": ["E-01", "E-02"], "segment_press": "P4", "segment_cavity": "", "segment_shift": "",
        "segment_crew": "", "segment_resin_lot": "",
        "escape_statement": "Escape: inspection sampling on press P4 was cut, so the rise was not detected.",
        "escape_evidence": ["E-03"], "actions": ["Dryer interlock", "Restore sampling"], "next_data": []}
BAD = {**GOOD, "occurrence_statement": "Occurrence: operator error by night crew B on press P4."}


class FakeInvestigator(BaseLlm):
    first_draft: dict = GOOD
    runs: int = 0

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        responses = [p.function_response for c in llm_request.contents for p in (c.parts or []) if p.function_response]
        user_text = " ".join(p.text or "" for c in llm_request.contents for p in (c.parts or []) if c.role == "user")
        revising = "Challenger rejected" in user_text
        n = len([r for r in responses if r.name != "set_model_response"])
        plan = [] if revising else PLAN
        if n < len(plan):
            name, args = plan[n]
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
            return
        if not any(r.name == "set_model_response" for r in responses):
            draft = GOOD if revising else self.first_draft
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="set_model_response", args=draft))]))
            return
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(GOOD))]))


def make_agents(first_draft):
    g = object.__new__(GeminiAgents)
    g.model = "fake-gemini"
    g.adk_model = FakeInvestigator(model="fake-gemini", first_draft=first_draft)
    g.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    async def intake(case, photos):
        return {"d2": {"what": "crack", "where": "line 3", "how_many": "37 of 2,400", "when": "Oct"},
                "box_2d": [508, 459, 625, 508], "photo_observation": "crack at hook root", "defect_type": "crack"}

    async def write(case, intake_, draft, containment, log):
        raise RuntimeError("writer offline")  # exercise the template fallback
    g.intake, g.write = intake, write
    return g


def run(agents):
    plant = generate(42)
    events = []

    async def go():
        async for e in run_investigation(get_seed("118"), plant, "test", agents, {}):
            events.append(e)
    asyncio.run(go())
    return events


def test_adk_agent_with_tools_produces_approved_draft():
    events = run(make_agents(GOOD))
    done = events[-1]["result"]
    assert done["modes"]["investigation"] == "gemini"
    assert done["verdict"] == "APPROVED_FOR_REVIEW"
    assert done["draft"]["occurrence"]["segment"] == {"press": "P4"}
    tools = [e["item"]["tool"] for e in events if e["type"] == "evidence"]
    assert tools[:5] == ["is_is_not", "param_excursions", "inspection_changes", "stratify", "containment"]
    assert done["modes"]["writer"] == "deterministic" and done["notes"]


def test_operator_error_is_rejected_then_revised():
    events = run(make_agents(BAD))
    done = events[-1]["result"]
    rounds = done["challenger"]
    assert rounds[0]["verdict"] == "REJECTED"
    assert any(r["rule"] == "no_people_blame_without_stratified_effect" and not r["passed"] for r in rounds[0]["rules"])
    assert rounds[-1]["verdict"] == "APPROVED_FOR_REVIEW"
    assert "operator error" not in done["draft"]["occurrence"]["statement"]


def test_stored_run_keeps_the_agent_trace():
    done = run(make_agents(BAD))[-1]["result"]
    calls = [t["tool"] for t in done["trace"] if t["stage"] == "investigate"]
    assert calls == ["is_is_not", "param_excursions", "inspection_changes", "stratify", "containment"]
    assert len(done["challenger"]) == 2  # rejected, revised by the agent, approved
