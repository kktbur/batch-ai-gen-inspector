from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import inspect_batch as inspector  # noqa: E402


class FakeOCR:
    def read(self, image_path: Path) -> list[inspector.OCRToken]:
        if "tiny-low-contrast" in image_path.name:
            return [inspector.OCRToken("SAMPLE", 0.99, [[30, 30], [90, 30], [90, 38], [30, 38]])]
        if "low" in image_path.name:
            return [inspector.OCRToken("SAMPLE PRODUCT $29.99", 0.2, [[10, 10], [110, 10], [110, 35], [10, 35]])]
        if "edge" in image_path.name:
            return [inspector.OCRToken("SAMPLE PRODUCT $29.99", 0.99, [[0, 1], [110, 1], [110, 25], [0, 25]])]
        return [inspector.OCRToken("SAMPLE PRODUCT $29.99", 0.99, [[10, 10], [110, 10], [110, 35], [10, 35]])]


def patterned_image(path: Path, size: tuple[int, int] = (120, 160), offset: int = 0) -> None:
    image = Image.new("RGB", size, "#e5e7eb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15 + offset, 45, 80 + offset, 120), fill="#2563eb")
    draw.ellipse((45, 70, 105, 130), fill="#f97316")
    image.save(path)


def config(**overrides):
    base = inspector.deep_merge(inspector.DEFAULT_CONFIG, {"ocr": {"enabled": True}})
    return inspector.deep_merge(base, overrides)


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid(self):
        inspector.validate_config(inspector.deep_merge(inspector.DEFAULT_CONFIG, {}))

    def test_conflicting_file_limits_are_rejected(self):
        value = config(expectations={"min_file_bytes": 200, "max_file_bytes": 100})
        with self.assertRaises(inspector.InspectorError):
            inspector.validate_config(value)

    def test_text_and_number_normalization(self):
        self.assertEqual(inspector.normalize_text(" ￥ ２９．９９ "), "¥29.99")
        self.assertEqual(inspector.normalize_number("$29.99"), "29.99")

    def test_current_rapidocr_object_shape(self):
        class Output:
            boxes = [[[1, 2], [3, 2], [3, 4], [1, 4]]]
            txts = ["SAMPLE PRODUCT"]
            scores = [0.97]

        tokens = inspector.parse_rapidocr_result(Output())
        self.assertEqual(tokens[0].text, "SAMPLE PRODUCT")
        self.assertAlmostEqual(tokens[0].confidence, 0.97)


class InspectionTests(unittest.TestCase):
    def test_mixed_batch_generates_complete_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir, output_dir = root / "input", root / "report"
            input_dir.mkdir()
            patterned_image(input_dir / "good.png")
            patterned_image(input_dir / "wrong-size.png", (100, 100))
            patterned_image(input_dir / "duplicate.png")
            current = config(
                expectations={
                    "width": 120,
                    "height": 160,
                    "required_text": ["SAMPLE PRODUCT", "$29.99"],
                    "critical_numbers": ["29.99"],
                    "safe_zone": {"top": 5, "right": 5, "bottom": 5, "left": 5},
                },
                thresholds={"edge_activity_warn": 1.0},
            )
            payload = inspector.run_inspection(input_dir, current, output_dir, FakeOCR())
            self.assertEqual(payload["summary"]["total"], 3)
            self.assertEqual(payload["summary"]["FAIL"], 1)
            self.assertEqual(payload["summary"]["WARN"], 2)
            for name in ("report.html", "report.json", "resolved-config.json", "contact-sheet.jpg"):
                self.assertTrue((output_dir / name).is_file(), name)
            self.assertEqual(len(list((output_dir / "annotated").glob("*.jpg"))), 3)

    def test_low_confidence_and_safe_zone_are_classified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patterned_image(root / "low.png")
            patterned_image(root / "edge.png", offset=2)
            current = config(
                expectations={"safe_zone": {"top": 5, "right": 5, "bottom": 5, "left": 5}},
                thresholds={"edge_activity_warn": 1.0},
            )
            low = inspector.inspect_asset(root / "low.png", root, current, FakeOCR())
            edge = inspector.inspect_asset(root / "edge.png", root, current, FakeOCR())
            self.assertIn("OCR_LOW_CONFIDENCE", {item.code for item in low.findings})
            self.assertEqual(low.status, "WARN")
            self.assertIn("TEXT_OUTSIDE_SAFE_ZONE", {item.code for item in edge.findings})
            self.assertEqual(edge.status, "FAIL")

    def test_corrupt_and_unsupported_images_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "broken.png").write_bytes(b"not an image")
            (root / "animation.gif").write_bytes(b"GIF89a")
            current = config(ocr={"enabled": False})
            broken = inspector.inspect_asset(root / "broken.png", root, current, None)
            unsupported = inspector.inspect_asset(root / "animation.gif", root, current, None)
            self.assertEqual(broken.status, "FAIL")
            self.assertEqual(unsupported.status, "FAIL")

    def test_recursive_scan_excludes_generated_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "nested"
            report = root / inspector.REPORT_DIR_PREFIX / "previous"
            nested.mkdir()
            report.mkdir(parents=True)
            patterned_image(nested / "asset.png")
            patterned_image(report / "preview.png")
            current = config(recursive=True, ocr={"enabled": False})
            found = inspector.discover_assets(root, current)
            self.assertEqual([path.name for path in found], ["asset.png"])

    def test_html_escapes_untrusted_names_and_ocr(self):
        payload = {
            "summary": {"total": 1, "PASS": 1, "WARN": 0, "FAIL": 0},
            "assets": [{
                "filename": "<script>alert(1)</script>.png",
                "status": "PASS",
                "width": 120,
                "height": 160,
                "file_bytes": 10,
                "findings": [],
                "ocr_text": "<img src=x onerror=alert(1)>",
                "annotated_path": "annotated/0001.jpg",
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            inspector.write_html_report(payload, output)
            document = (output / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>.png", document)
            self.assertNotIn("<img src=x onerror=alert(1)>", document)
            self.assertIn("&lt;script&gt;", document)
            self.assertIn("Quality metrics", document)

    def test_config_is_preserved_in_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"version": 1, "ocr": {"enabled": False}}), encoding="utf-8")
            loaded = inspector.load_config(path)
            self.assertFalse(loaded["ocr"]["enabled"])
            self.assertEqual(loaded["formats"], ["png", "jpg", "jpeg", "webp"])

    def test_blur_detection_distinguishes_softened_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sharp = Image.new("RGB", (256, 256), "white")
            draw = ImageDraw.Draw(sharp)
            for y in range(0, 256, 16):
                for x in range(0, 256, 16):
                    if (x // 16 + y // 16) % 2:
                        draw.rectangle((x, y, x + 15, y + 15), fill="black")
            sharp.save(root / "sharp.png")
            sharp.filter(ImageFilter.GaussianBlur(5)).save(root / "blurred.png")
            current = config(
                ocr={"enabled": False},
                thresholds={"blur_laplacian_warn": 100.0, "edge_activity_warn": 1.0},
            )
            sharp_result = inspector.inspect_asset(root / "sharp.png", root, current, None)
            blurred_result = inspector.inspect_asset(root / "blurred.png", root, current, None)
            self.assertNotIn("BLURRY_IMAGE", {item.code for item in sharp_result.findings})
            self.assertIn("BLURRY_IMAGE", {item.code for item in blurred_result.findings})

    def test_blockiness_and_exposure_clipping_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            blocky = Image.new("L", (256, 256))
            draw = ImageDraw.Draw(blocky)
            for y in range(0, 256, 8):
                for x in range(0, 256, 8):
                    value = 40 if (x // 8 + y // 8) % 2 else 210
                    draw.rectangle((x, y, x + 7, y + 7), fill=value)
            blocky.convert("RGB").save(root / "blocky.png")
            exposure = Image.new("L", (300, 100), 128)
            draw = ImageDraw.Draw(exposure)
            draw.rectangle((0, 0, 89, 99), fill=0)
            draw.rectangle((210, 0, 299, 99), fill=255)
            exposure.convert("RGB").save(root / "exposure.png")
            current = config(
                ocr={"enabled": False},
                thresholds={
                    "compression_blockiness_warn": 2.0,
                    "shadow_clip_fraction_warn": 0.2,
                    "highlight_clip_fraction_warn": 0.2,
                    "blur_laplacian_warn": 0.0,
                    "edge_activity_warn": 1.0,
                },
            )
            block_result = inspector.inspect_asset(root / "blocky.png", root, current, None)
            exposure_result = inspector.inspect_asset(root / "exposure.png", root, current, None)
            self.assertIn("COMPRESSION_BLOCKING", {item.code for item in block_result.findings})
            self.assertIn("SHADOW_CLIPPING", {item.code for item in exposure_result.findings})
            self.assertIn("HIGHLIGHT_CLIPPING", {item.code for item in exposure_result.findings})

    def test_text_readability_and_subject_crop_risk_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            low_contrast = Image.new("RGB", (160, 160), (128, 128, 128))
            ImageDraw.Draw(low_contrast).rectangle((30, 30, 90, 38), fill=(136, 136, 136))
            low_contrast.save(root / "tiny-low-contrast.png")
            cropped = Image.new("RGB", (240, 240), "white")
            ImageDraw.Draw(cropped).rectangle((0, 45, 145, 205), fill="#1d4ed8")
            cropped.save(root / "subject-crop.png")
            current = config(
                thresholds={
                    "text_min_height_px": 16,
                    "text_min_contrast_warn": 30.0,
                    "subject_crop_min_area_ratio": 0.04,
                    "blur_laplacian_warn": 0.0,
                    "edge_activity_warn": 1.0,
                },
            )
            text_result = inspector.inspect_asset(root / "tiny-low-contrast.png", root, current, FakeOCR())
            crop_result = inspector.inspect_asset(root / "subject-crop.png", root, current, FakeOCR())
            text_codes = {item.code for item in text_result.findings}
            self.assertIn("TEXT_TOO_SMALL", text_codes)
            self.assertIn("TEXT_LOW_CONTRAST", text_codes)
            self.assertIn("POSSIBLE_SUBJECT_CLIPPING", {item.code for item in crop_result.findings})
            self.assertIn("left", crop_result.subject_crop_sides)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir = root / "input"
            input_dir.mkdir()
            patterned_image(input_dir / "asset.png")
            pass_config = root / "pass.json"
            pass_config.write_text(json.dumps({
                "version": 1,
                "ocr": {"enabled": False},
                "thresholds": {"edge_activity_warn": 1.0},
            }), encoding="utf-8")
            self.assertEqual(inspector.main([
                "--input", str(input_dir),
                "--config", str(pass_config),
                "--output", str(root / "pass-report"),
            ]), 0)

            fail_config = root / "fail.json"
            fail_config.write_text(json.dumps({
                "version": 1,
                "ocr": {"enabled": False},
                "expectations": {"width": 999},
                "thresholds": {"edge_activity_warn": 1.0},
            }), encoding="utf-8")
            self.assertEqual(inspector.main([
                "--input", str(input_dir),
                "--config", str(fail_config),
                "--output", str(root / "fail-report"),
            ]), 1)

            self.assertEqual(inspector.main([
                "--input", str(root / "missing"),
                "--config", str(pass_config),
                "--output", str(root / "error-report"),
            ]), 2)


if __name__ == "__main__":
    unittest.main()

