import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static" / "index.html"
CSS_PATH = ROOT / "static" / "css" / "style.css"


class InternalWebStaticContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")

    def test_home_page_exposes_itemized_furniture_render_surface(self):
        self.assertIn('id="furniture-items-section"', self.html)
        self.assertIn('id="furniture-items-container"', self.html)
        self.assertIn('id="furniture-item-template"', self.html)
        self.assertIn('id="add-furniture-item-btn"', self.html)

        for moodboard_id in (
            'id="moodboard-upload-container"',
            'id="moodboard-drop-zone"',
            'id="moodboard-input"',
            'id="moodboard-preview-container"',
            'id="remove-moodboard"',
            'id="open-mb-gen-btn"',
        ):
            self.assertNotIn(moodboard_id, self.html)

    def test_home_page_furniture_category_options_are_exact(self):
        match = re.search(r'<select[^>]*class="[^"]*furniture-category-select[^"]*"[^>]*>(.*?)</select>', self.html, re.S)
        self.assertIsNotNone(match, "furniture category select was not found")

        options = re.findall(r'<option[^>]*value="([^"]+)"[^>]*>', match.group(1))
        self.assertEqual(
            options,
            [
                "메인소파",
                "라운지소파",
                "소파테이블",
                "다이닝테이블",
                "사이드테이블",
                "데스크테이블",
                "다이닝체어",
                "데스크체이",
                "라운지체어",
                "팬던트램프",
                "플로어램프",
                "테이블램프",
                "스툴/푸프",
                "베드",
                "러그",
                "스토리지/캐비닛/쉘",
                "전자제품(TV,스피커 등)",
                "거울",
                "소품",
                "페인팅/포스터",
            ],
        )

    def test_home_page_styles_include_itemized_furniture_contract(self):
        for selector in (
            "#furniture-items-section",
            "#furniture-items-container",
            ".furniture-item-card",
            ".furniture-item-drop-zone",
            ".furniture-item-meta-grid",
            "#add-furniture-item-btn",
            "#furniture-item-template",
        ):
            self.assertIn(selector, self.css)

    def test_home_page_furniture_items_use_five_column_desktop_grid(self):
        self.assertIn(
            "#furniture-items-container {\n    display: grid;\n    grid-template-columns: repeat(5, minmax(0, 1fr));",
            self.css,
        )
        self.assertIn(
            "@media (max-width: 1200px) {\n    #furniture-items-container {\n        grid-template-columns: repeat(3, minmax(0, 1fr));",
            self.css,
        )
        self.assertIn(
            "@media (max-width: 900px) {\n    #furniture-items-container {\n        grid-template-columns: repeat(2, minmax(0, 1fr));",
            self.css,
        )

    def test_home_page_furniture_preview_uses_fixed_box_with_contain(self):
        for token in (
            ".furniture-item-preview {\n    position: relative;\n    overflow: hidden;\n    border: 1px solid #b6b6b6;\n    border-radius: 12px;\n    background: #ffffff;\n    height: 150px;",
            ".furniture-item-preview-image {\n    width: 100%;\n    height: 100%;\n    display: block;\n    object-fit: contain;",
            ".furniture-item-drop-zone {\n    border: 1px dashed #b6b6b6;",
            "height: 150px;",
        ):
            self.assertIn(token, self.css)

    def test_home_page_upload_copy_uses_25mb_limit(self):
        self.assertEqual(self.html.count("up to 25mb"), 4)
        self.assertNotIn("up to 30mb", self.html)

    def test_home_page_item_dimension_inputs_use_positive_minimum(self):
        for marker in (
            'class="dark-input furniture-item-width" min="1" step="1"',
            'class="dark-input furniture-item-depth" min="1" step="1"',
            'class="dark-input furniture-item-height" min="1" step="1"',
        ):
            self.assertIn(marker, self.html)
