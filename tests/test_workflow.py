"""Static checks on the workflow export + unit tests for the offline mock LLM.

    pip install pytest fastapi httpx && pytest -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mock_services"))

from server import _classify  # noqa: E402

WORKFLOW = json.loads((ROOT / "workflows" / "ai-lead-triage.json").read_text(encoding="utf-8"))
NODES = {n["name"]: n for n in WORKFLOW["nodes"]}


def test_node_names_are_unique():
    names = [n["name"] for n in WORKFLOW["nodes"]]
    assert len(names) == len(set(names))


def test_every_connection_points_to_a_real_node():
    for source, outputs in WORKFLOW["connections"].items():
        assert source in NODES, source
        for branch in outputs["main"]:
            for link in branch:
                assert link["node"] in NODES, link["node"]


def test_every_working_node_is_reachable_from_the_trigger():
    reachable, frontier = set(), ["Incoming message"]
    while frontier:
        node = frontier.pop()
        reachable.add(node)
        for branch in WORKFLOW["connections"].get(node, {}).get("main", []):
            frontier += [l["node"] for l in branch if l["node"] not in reachable]
    working = {n["name"] for n in WORKFLOW["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
    assert working == reachable


def test_external_calls_retry_and_llm_calls_use_credentials():
    for node in WORKFLOW["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            assert node.get("retryOnFail") and node.get("maxTries", 0) >= 2, node["name"]
            if node["name"].startswith("AI:"):
                assert "httpHeaderAuth" in node.get("credentials", {}), node["name"]


def test_no_secrets_in_export():
    text = json.dumps(WORKFLOW)
    assert "sk-" not in text and "Bearer " not in text.replace("Bearer <your key>", "")


@pytest.mark.parametrize("sample,expected", [
    (0, "sales_lead"), (1, "sales_lead"), (2, "support"), (3, "billing"), (4, "spam"), (5, "partnership"),
])
def test_mock_classifies_samples(sample, expected):
    msg = json.loads((ROOT / "samples" / "messages.json").read_text(encoding="utf-8"))[sample]
    prompt = f"From: {msg['from_name']} <{msg['from_email']}>\nSubject: {msg['subject']}\n\nMessage:\n{msg['body']}"
    result = _classify(prompt, msg["from_email"])
    assert result["category"] == expected
    if sample == 0:
        assert result["lead_score"] >= 70 and result["budget_eur"] == 18000
