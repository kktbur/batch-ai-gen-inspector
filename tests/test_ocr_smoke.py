from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import inspect_batch as inspector  # noqa: E402


@unittest.skipUnless(os.environ.get("RUN_OCR_TESTS") == "1", "set RUN_OCR_TESTS=1 to run real OCR")
class RapidOCRSmokeTest(unittest.TestCase):
    def test_reads_generic_english_and_number(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.png"
            image = Image.new("RGB", (1200, 360), "white")
            draw = ImageDraw.Draw(image)
            font = self._font(82)
            draw.text((60, 110), "SAMPLE PRODUCT $29.99", fill="black", font=font)
            image.save(path)
            tokens = inspector.RapidOCREngine().read(path)
            text = inspector.normalize_text(" ".join(token.text for token in tokens))
            self.assertIn("sampleproduct", text)
            self.assertIn("29.99", text)

    @staticmethod
    def _font(size: int):
        for candidate in ("arial.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()


if __name__ == "__main__":
    unittest.main()

