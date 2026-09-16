from __future__ import annotations

import unittest

from rag_json_evaluation.routing import route_query


class RoutingTests(unittest.TestCase):
    def test_multi_route(self) -> None:
        result = route_query("保持已有道路不变，并按规范增加建筑间距", {"turn_id": 2})
        self.assertIn("context", result["routes"])
        self.assertIn("standard", result["routes"])
        self.assertIn("resource", result["routes"])

    def test_resource_fallback_is_auditable(self) -> None:
        result = route_query("创建一个对象", {"turn_id": 1})
        self.assertEqual(result["routes"], ["resource"])
        self.assertEqual(result["reasons"]["resource"], ["construction_resource_fallback"])


if __name__ == "__main__":
    unittest.main()

