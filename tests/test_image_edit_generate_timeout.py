import io
import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from application.media.image_edit_generation_stage import process_image_edit_logic


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class ImageEditGenerateTimeoutTests(unittest.TestCase):
    def test_process_image_edit_uses_150_second_generate_timeout(self):
        calls = []
        output_png = _png_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = os.path.join(temp_dir, "target.png")
            Image.new("RGB", (16, 16), "white").save(target_path)

            old_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                os.makedirs("outputs", exist_ok=True)

                def call_model(model_name, content, options, safety_settings, *, log_tag):
                    calls.append({"options": options, "log_tag": log_tag})
                    return SimpleNamespace(
                        candidates=[object()],
                        parts=[
                            SimpleNamespace(
                                inline_data=SimpleNamespace(data=output_png),
                            )
                        ],
                    )

                result = process_image_edit_logic(
                    [target_path],
                    "Remove the chair.",
                    "edit",
                    "unit",
                    0,
                    build_image_edit_step_prompt=lambda **kwargs: "prompt",
                    pad_image_to_target_canvas=lambda image, width, height: image,
                    call_gemini_with_failover=call_model,
                    model_name="test-model",
                    match_aspect_to_target=lambda out_path, target: out_path,
                )
            finally:
                os.chdir(old_cwd)

        self.assertIsNotNone(result)
        self.assertEqual("Edit.Generate", calls[0]["log_tag"])
        self.assertEqual(150, calls[0]["options"]["timeout"])


if __name__ == "__main__":
    unittest.main()
