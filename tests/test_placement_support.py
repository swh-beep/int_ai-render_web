import unittest

from application.render.placement_support import build_placement_prompt_block, parse_placement_constraints


class PlacementSupportTests(unittest.TestCase):
    def test_parse_placement_constraints_extracts_anchor_rules(self):
        parsed = parse_placement_constraints(
            "Place the main bed on the right side against the back wall and keep an open walkway on the left."
        )
        self.assertEqual(parsed["horizontal_anchor"], "right")
        self.assertEqual(parsed["depth_anchor"], "back_wall")
        self.assertTrue(parsed["clearance"])

    def test_build_placement_prompt_block_contains_normalized_rules(self):
        prompt_block = build_placement_prompt_block(
            "Keep the sofa on the left side near the window with balanced spacing."
        )
        self.assertIn("HORIZONTAL ANCHOR: LEFT", prompt_block)
        self.assertIn("WINDOW RELATION: keep the requested item near the window side.", prompt_block)
        self.assertIn("HARD RULE", prompt_block)


if __name__ == "__main__":
    unittest.main()
