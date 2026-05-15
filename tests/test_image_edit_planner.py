import unittest

from application.media.image_edit_generation_stage import (
    _extract_step_targets,
    compose_step_instructions,
)
from infrastructure.ai.gemini_prompts import build_image_edit_step_prompt


class ImageEditPlannerTests(unittest.TestCase):
    def test_compose_step_instructions_keeps_full_user_request(self):
        text = "Remove the coffee table and make the lamp smaller, while keeping the rest unchanged."
        step_targets = _extract_step_targets("remove", text)
        composed = compose_step_instructions("remove", text, step_targets)
        self.assertIn("FULL USER REQUEST", composed)
        self.assertIn(text, composed)
        self.assertIn("CURRENT STEP: remove", composed)
        self.assertIn("PRIMARY TARGETS FOR THIS STEP", composed)
        self.assertIn("Remove the coffee table", composed)

    def test_extract_step_targets_keeps_relevant_clause_and_guardrail(self):
        text = "Remove the coffee table and make the orange lamp smaller while keeping the rest unchanged."
        step_targets = _extract_step_targets("resize", text)
        self.assertIn("make the orange lamp smaller", step_targets)
        self.assertIn("keeping the rest unchanged", step_targets)
        self.assertNotIn("Remove the coffee table", step_targets)

    def test_build_image_edit_step_prompt_no_longer_discards_other_intents(self):
        prompt = build_image_edit_step_prompt(
            role="Editor",
            task="Edit the room",
            step_focus="Remove objects",
            step_instructions="FULL USER REQUEST:\nRemove the coffee table and make the lamp smaller.",
            critical_rule="Keep framing.",
            strict_mask_rules="",
        )
        self.assertIn("do NOT contradict", prompt)
        self.assertNotIn("Ignore unrelated requests", prompt)
        self.assertIn("OBJECT IDENTITY LOCK", prompt)
        self.assertIn("UNCHANGED OBJECT LOCK", prompt)


if __name__ == "__main__":
    unittest.main()
