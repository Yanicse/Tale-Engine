"""Offline documentary check of the kit; no engine and no LLM call."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


PKG_PARENT = Path(__file__).resolve().parents[2]
KIT = PKG_PARENT / "novel_engine" / "assets" / "docs" / "engine"
import sys
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))
from novel_engine.validation import validate, check_schema


def read(relative: str):
    return json.loads((KIT / relative).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def replay_fixture(state: dict, result: dict) -> dict:
    """Replay in memory only the example mutations, with no persistence."""
    require(result["state_version_before"] == state["state_version"], "Inconsistent initial version")
    require(result["campaign_id"] == state["campaign_id"], "Inconsistent campaign")
    output = deepcopy(state)
    if result["status"] != "recorded":
        require(not result["applied_events"], "A result without recording applies events")
        require(result["state_version_after"] == state["state_version"], "A refusal changes the version")
        require(result["world_minute_after"] == state["world_minute"], "A refusal advances the clock")
        return output
    require(result["state_version_after"] == state["state_version"] + 1, "Inconsistent version increment")
    require(result["world_minute_after"] >= state["world_minute"], "The clock goes backwards")
    for event in result["applied_events"]:
        require(event["event_id"] not in output["applied_event_ids"], "Event already applied")
        require(state["world_minute"] <= event["world_minute"] <= result["world_minute_after"], "Inconsistent deadline")
        for condition in event["conditions"]:
            group, key = condition["path"].strip("/").split("/")
            require(key in output[group] and output[group][key] == condition["equals"], "Condition not met")
        for change in event["changes"]:
            group, key = change["path"].strip("/").split("/")
            if change["operation"] == "set":
                output[group][key] = deepcopy(change["value"])
            elif change["operation"] == "increment":
                require(type(change["value"]) is int and type(output[group].get(key)) is int, "Non-numeric increment")
                output[group][key] += change["value"]
            else:
                require(key in output[group], "Deletion of a missing value")
                del output[group][key]
        output["applied_event_ids"].append(event["event_id"])
    output["state_version"] = result["state_version_after"]
    output["world_minute"] = result["world_minute_after"]
    return output


def must_fail(callable_check) -> None:
    try:
        callable_check()
    except ValueError:
        return
    raise ValueError("A deliberately invalid example was accepted")


def main() -> None:
    schemas = {p.name.split(".")[0]: json.loads(p.read_text(encoding="utf-8"))
               for p in (KIT / "contracts").glob("*.schema.json")}
    require(set(schemas) == {"context", "state", "proposal", "task", "result"}, "Incomplete contracts")
    for schema in schemas.values():
        check_schema(schema)
    examples = {}
    for p in sorted((KIT / "examples").glob("*.json")):
        kind = p.name.split("_")[0]
        value = json.loads(p.read_text(encoding="utf-8"))
        validate(schemas[kind], value, p.name)
        examples[p.name] = value
        if kind == "context":
            for info in value["information"]:
                require(value["recipient"] in info["audience"], f"Context leak in {p.name}")
        if kind == "proposal":
            require(not value["blocking_questions"] or not value["events"], "Blocking question with changes")
            event_ids = [event["event_id"] for event in value["events"]]
            require(len(event_ids) == len(set(event_ids)), "Duplicate events in a proposal")
        if kind == "task":
            context = read(value["context_file"])
            require(context["campaign_id"] == value["campaign_id"], "Context from another campaign")
            require(context["state_version"] == value["expected_state_version"], "Stale context at task creation")
    state0, state1, state2 = (examples[f"state_{i}.json"] for i in range(3))
    require(replay_fixture(state0, examples["result_player.json"]) == state1, "Inconsistent start and state 1")
    require(replay_fixture(state1, examples["result_background_obsolete.json"]) == state1, "Inconsistent obsolete refusal")
    require(replay_fixture(state1, examples["result_background_1.json"]) == state2, "Inconsistent adapted plan and state 2")
    for stem, result_name in (("player", "result_player.json"), ("background_1", "result_background_1.json")):
        proposal, result = examples[f"proposal_{stem}.json"], examples[result_name]
        require(proposal["events"] == result["applied_events"], "The recorded events differ from the proposal")
        require(proposal["request_id"] == result["request_id"], "Inconsistent request")
        require(proposal["narration"] == result["narration"], "Inconsistent narration")
    stale = examples["proposal_background_0.json"]
    require(stale["expected_state_version"] != state1["state_version"], "The obsolete case does not test an old version")
    adapted = examples["proposal_background_1.json"]
    require("player" not in adapted["events"][0]["audience"] and not adapted["narration"], "The secret plan is revealed")
    invalid = deepcopy(examples["proposal_player.json"])
    invalid["expected_state_version"] = True
    must_fail(lambda: validate(schemas["proposal"], invalid))
    invalid = deepcopy(examples["proposal_player.json"])
    del invalid["request_id"]
    must_fail(lambda: validate(schemas["proposal"], invalid))
    observation = examples["result_player.json"]["applied_events"][1]
    require("player" not in observation["audience"], "The secret observation is transmitted to the player")
    invalid_result = deepcopy(examples["result_background_obsolete.json"])
    invalid_result["applied_events"] = stale["events"]
    must_fail(lambda: replay_fixture(state1, invalid_result))
    invalid_schema = {"type": "string", "unsupported": True}
    must_fail(lambda: check_schema(invalid_schema))
    scenarios = read("scenarios.json")["scenarios"]
    require(len({s["id"] for s in scenarios}) == len(scenarios), "Duplicate scenario identifiers")
    for scenario in scenarios:
        for relative in scenario["example_files"]:
            require((KIT / relative).is_file(), f"Example missing: {relative}")
    for p in KIT.rglob("*"):
        if p.is_file():
            require(chr(0x2014) not in p.read_text(encoding="utf-8"), f"Forbidden punctuation: {p.name}")
    print(json.dumps({"status": "ok", "contracts": len(schemas), "examples": len(examples),
                      "specified_scenarios": len(scenarios), "engine_executed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
