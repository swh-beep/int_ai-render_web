import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "static" / "js" / "script.js"


class InternalWebItemizedFlowContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_script_exposes_itemized_furniture_render_contract(self):
        for token in (
            "furniture-items-container",
            "furniture-item-template",
            "add-furniture-item-btn",
        ):
            self.assertIn(token, self.script)

        self.assertIn("items_json", self.script)
        self.assertIn("item_images", self.script)
        self.assertNotIn("selectedMoodboardFile", self.script)
        self.assertNotIn("formData.append('moodboard'", self.script)
        self.assertIn("Number.isInteger(qty)", self.script)
        self.assertIn("Number.isInteger(width)", self.script)
        self.assertIn("Number.isInteger(depth)", self.script)
        self.assertIn("Number.isInteger(height)", self.script)
        self.assertIn("cards.every(isFurnitureCardComplete)", self.script)
        self.assertIn("selectedIndex = -1", self.script)

    def test_detail_payloads_keep_compatibility_fields(self):
        for token in (
            "moodboard_url: currentMoodboardUrl",
            "furniture_data: currentFurnitureData",
        ):
            self.assertIn(token, self.script)

    def test_script_has_shared_home_image_upload_validation(self):
        for token in (
            "const MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024;",
            "const ALLOWED_IMAGE_UPLOAD_EXTENSIONS = new Set([",
            "function validateHomeImageUpload(file, contextLabel)",
            "file.size > MAX_IMAGE_UPLOAD_BYTES",
            "ALLOWED_IMAGE_UPLOAD_EXTENSIONS.has(extension)",
        ):
            self.assertIn(token, self.script)

    def test_script_has_stale_response_protection_for_room_and_style_loading(self):
        for token in (
            "let roomSelectionRequestToken = 0;",
            "let styleSelectionRequestToken = 0;",
            "const requestToken = ++roomSelectionRequestToken;",
            "if (requestToken !== roomSelectionRequestToken) return;",
            "const requestToken = ++styleSelectionRequestToken;",
            "if (requestToken !== styleSelectionRequestToken) return;",
        ):
            self.assertIn(token, self.script)

    def test_script_guards_optional_main_surface_moodboard_container_calls(self):
        self.assertIn(
            "if (moodboardUploadContainer) moodboardUploadContainer.classList.remove('hidden');",
            self.script,
        )
        self.assertIn(
            "if (moodboardUploadContainer) moodboardUploadContainer.classList.add('hidden');",
            self.script,
        )

    def test_script_ignores_stale_furniture_preview_reads(self):
        for token in (
            "card._previewToken = (card._previewToken || 0) + 1;",
            "if (card._previewToken !== previewToken || card._itemFile !== file) {",
        ):
            self.assertIn(token, self.script)

    def test_script_shows_modal_when_item_dimensions_are_missing_on_render(self):
        for token in (
            "function getFurnitureValidationError()",
            "missingDimensions.push(`Item ${index + 1}`);",
            "showCustomAlert(\"Missing Dimensions\"",
            "if (furnitureValidationError) {",
        ):
            self.assertIn(token, self.script)

    def test_script_initializes_five_visible_cards_and_adds_five_per_click(self):
        for token in (
            "const INITIAL_FURNITURE_CARD_COUNT = 5;",
            "const FURNITURE_BATCH_SIZE = 5;",
            "while (getFurnitureCards().length < INITIAL_FURNITURE_CARD_COUNT) {",
            "appendFurnitureItemCards(FURNITURE_BATCH_SIZE);",
            "if (cards.length <= INITIAL_FURNITURE_CARD_COUNT) {",
        ):
            self.assertIn(token, self.script)
