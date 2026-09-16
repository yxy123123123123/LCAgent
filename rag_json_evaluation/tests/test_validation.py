from __future__ import annotations

import copy
import json
import unittest

from rag_json_evaluation.config import PACKAGE_ROOT, load_config
from rag_json_evaluation.io_utils import load_json
from rag_json_evaluation.validation import validate_scene_plan


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("G0")
        self.plan = load_json(PACKAGE_ROOT / "data" / "scene_plan.example.json")
        self.task = {
            "task_id": "resource_demo_001",
            "conversation_id": None,
            "turn_id": 1,
            "user_prompt": "创建一条海边道路，经过(0,0,0)和(10000,0,0)两个点。",
        }
        self.retrieval = {"results": [{
            "text": "/Game/ThirdParty/Road_Creator_Pro/Blueprints/BP_Road_Creator.BP_Road_Creator"
        }]}

    def validate(self, plan):
        return validate_scene_plan(
            plan,
            self.config["paths"]["schema_path"],
            self.config["paths"]["plugin_contract_path"],
            self.task,
            self.retrieval,
        )

    def test_valid_example(self) -> None:
        result = self.validate(self.plan)
        self.assertTrue(result["valid"], result["errors"])

    def test_uppercase_coordinates_are_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        point = plan["objects"][0]["commands"][1]["params"]["positions"][0]
        point["X"] = point.pop("x")
        result = self.validate(plan)
        self.assertFalse(result["schema_valid"])

    def test_hallucinated_path_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["objects"][0]["commands"][0]["params"]["actor_path"] = "/Game/Unknown/FakeActor"
        result = self.validate(plan)
        self.assertIn("UNSUPPORTED_RESOURCE_PATH", {item["code"] for item in result["errors"]})

    def test_execute_must_be_last(self) -> None:
        plan = copy.deepcopy(self.plan)
        commands = plan["objects"][0]["commands"]
        commands[1], commands[2] = commands[2], commands[1]
        result = self.validate(plan)
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("EXECUTE_ORDER", codes)

    def test_searchable_non_plugin_asset_cannot_be_used_as_actor_path(self) -> None:
        plan = copy.deepcopy(self.plan)
        path = "/Game/EnvModels/Textures/T_Road.T_Road"
        plan["objects"][0]["commands"][0]["params"]["actor_path"] = path
        record = {
            "knowledge_type": "resource",
            "ue_object_path_candidate": path,
            "candidate_for_plugin_path": False,
            "index_admission": True,
            "verification_status": "filesystem_verified_ue_unverified",
        }
        self.retrieval = {"results": [{
            "text": "[knowledge_type=resource; source_id=test]\n" + json.dumps(record),
        }]}
        result = self.validate(plan)
        self.assertIn("RESOURCE_NOT_PLUGIN_PATH_CANDIDATE", {item["code"] for item in result["errors"]})
        self.assertIn("UE_RESOURCE_PATH_UNVERIFIED", {item["code"] for item in result["warnings"]})


if __name__ == "__main__":
    unittest.main()
