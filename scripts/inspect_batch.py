#!/usr/bin/env python3
"""Deterministic, local preflight checks for batches of AI-generated images."""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required. Install the project requirements first.") from exc

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("NumPy and OpenCV are required. Install the project requirements first.") from exc


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".svg", ".tif", ".tiff"}
EXCLUDED_DIR_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache"}
REPORT_DIR_PREFIX = "batch-ai-gen-inspector-report"
SEVERITY_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "recursive": False,
    "formats": ["png", "jpg", "jpeg", "webp"],
    "ocr": {"enabled": True, "low_confidence": 0.55},
    "expectations": {
        "width": None,
        "height": None,
        "aspect_ratio": None,
        "aspect_tolerance": 0.01,
        "min_file_bytes": None,
        "max_file_bytes": None,
        "required_text": [],
        "forbidden_text": [],
        "critical_numbers": [],
        "safe_zone": {"top": 0, "right": 0, "bottom": 0, "left": 0},
    },
    "thresholds": {
        "blank_stddev_max": 2.0,
        "dark_mean_max": 8.0,
        "bright_mean_min": 247.0,
        "min_visible_fraction": 0.02,
        "edge_activity_warn": 0.35,
        "near_duplicate_distance": 6,
        "blur_laplacian_warn": 45.0,
        "compression_blockiness_warn": 2.2,
        "compression_boundary_min": 6.0,
        "shadow_clip_fraction_warn": 0.2,
        "highlight_clip_fraction_warn": 0.2,
        "text_min_height_px": 16,
        "text_min_height_ratio": 0.012,
        "text_min_contrast_warn": 30.0,
        "subject_crop_min_area_ratio": 0.04,
        "subject_background_distance": 28.0,
    },
    "severity_overrides": {},
}


class InspectorError(Exception):
    """User-facing configuration or runtime error."""


class OCREngine(Protocol):
    def read(self, image_path: Path) -> list["OCRToken"]: ...


@dataclass
class OCRToken:
    text: str
    confidence: float
    box: list[list[float]]


@dataclass
class Finding:
    code: str
    severity: str
    zh: str
    en: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetResult:
    path: str
    filename: str
    status: str = "PASS"
    width: int | None = None
    height: int | None = None
    file_bytes: int | None = None
    format: str | None = None
    mean_brightness: float | None = None
    brightness_stddev: float | None = None
    visible_fraction: float | None = None
    sharpness_score: float | None = None
    blockiness_score: float | None = None
    shadow_clip_fraction: float | None = None
    highlight_clip_fraction: float | None = None
    subject_crop_sides: list[str] = field(default_factory=list)
    sha256: str | None = None
    perceptual_hash: str | None = None
    ocr_text: str = ""
    ocr_tokens: list[OCRToken] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    annotated_path: str | None = None

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if SEVERITY_RANK[finding.severity] > SEVERITY_RANK[self.status]:
            self.status = finding.severity


class RapidOCREngine:
    """Adapter around the maintained RapidOCR Python API."""

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise InspectorError(
                "OCR is enabled but RapidOCR is unavailable. Install requirements.txt "
                "inside the project virtual environment, or set ocr.enabled to false."
            ) from exc
        try:
            self._engine = RapidOCR()
        except Exception as exc:
            raise InspectorError(f"RapidOCR could not initialize: {exc}") from exc

    def read(self, image_path: Path) -> list[OCRToken]:
        try:
            return parse_rapidocr_result(self._engine(str(image_path)))
        except InspectorError:
            raise
        except Exception as exc:
            raise InspectorError(f"OCR failed for {image_path.name}: {exc}") from exc


def parse_rapidocr_result(raw: Any) -> list[OCRToken]:
    """Accept current RapidOCR output objects and the legacy tuple shape."""
    if raw is None:
        return []
    boxes = getattr(raw, "boxes", None)
    texts = getattr(raw, "txts", None)
    if texts is None:
        texts = getattr(raw, "texts", None)
    scores = getattr(raw, "scores", None)
    if boxes is not None and texts is not None:
        if scores is None:
            scores = [1.0] * len(texts)
        return [
            OCRToken(str(text), float(score), _box_to_lists(box))
            for box, text, score in zip(boxes, texts, scores)
        ]
    if isinstance(raw, dict):
        boxes = raw.get("boxes")
        if boxes is None:
            boxes = raw.get("dt_polys", [])
        texts = raw.get("txts")
        if texts is None:
            texts = raw.get("texts")
        if texts is None:
            texts = raw.get("rec_texts", [])
        scores = raw.get("scores")
        if scores is None:
            scores = raw.get("rec_scores")
        if scores is None:
            scores = [1.0] * len(texts)
        return [
            OCRToken(str(text), float(score), _box_to_lists(box))
            for box, text, score in zip(boxes, texts, scores)
        ]
    if isinstance(raw, tuple):
        raw = raw[0]
    tokens: list[OCRToken] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            box, text_score = item[0], item[1]
            if isinstance(text_score, (list, tuple)) and text_score:
                score = text_score[1] if len(text_score) > 1 else 1.0
                tokens.append(OCRToken(str(text_score[0]), float(score), _box_to_lists(box)))
    return tokens


def _box_to_lists(box: Any) -> list[list[float]]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    return [[float(point[0]), float(point[1])] for point in box]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None) -> dict[str, Any]:
    supplied: dict[str, Any] = {}
    if path:
        try:
            supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InspectorError(f"Cannot read config {path}: {exc}") from exc
        if not isinstance(supplied, dict):
            raise InspectorError("The config root must be a JSON object.")
    config = deep_merge(DEFAULT_CONFIG, supplied)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise InspectorError("Only config version 1 is supported.")
    expectations = config["expectations"]
    for name in ("width", "height", "min_file_bytes", "max_file_bytes"):
        value = expectations.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            raise InspectorError(f"expectations.{name} must be a positive integer or null.")
    ratio = expectations.get("aspect_ratio")
    if ratio is not None and (not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or ratio <= 0):
        raise InspectorError("expectations.aspect_ratio must be a positive number or null.")
    if expectations["min_file_bytes"] and expectations["max_file_bytes"]:
        if expectations["min_file_bytes"] > expectations["max_file_bytes"]:
            raise InspectorError("min_file_bytes cannot exceed max_file_bytes.")
    for name in ("required_text", "forbidden_text", "critical_numbers"):
        value = expectations.get(name)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise InspectorError(f"expectations.{name} must be a list of non-empty strings.")
    safe_zone = expectations.get("safe_zone")
    if not isinstance(safe_zone, dict):
        raise InspectorError("expectations.safe_zone must be an object.")
    for side in ("top", "right", "bottom", "left"):
        value = safe_zone.get(side)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InspectorError(f"safe_zone.{side} must be a non-negative integer.")
    thresholds = config["thresholds"]
    if not 0 <= thresholds["min_visible_fraction"] <= 1:
        raise InspectorError("min_visible_fraction must be between 0 and 1.")
    if not 0 <= config["ocr"]["low_confidence"] <= 1:
        raise InspectorError("ocr.low_confidence must be between 0 and 1.")
    if not isinstance(thresholds["near_duplicate_distance"], int) or not 0 <= thresholds["near_duplicate_distance"] <= 64:
        raise InspectorError("near_duplicate_distance must be an integer from 0 to 64.")
    non_negative_thresholds = (
        "blur_laplacian_warn",
        "compression_blockiness_warn",
        "compression_boundary_min",
        "text_min_height_px",
        "text_min_height_ratio",
        "text_min_contrast_warn",
        "subject_background_distance",
    )
    for name in non_negative_thresholds:
        value = thresholds[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise InspectorError(f"thresholds.{name} must be a non-negative number.")
    for name in ("shadow_clip_fraction_warn", "highlight_clip_fraction_warn", "subject_crop_min_area_ratio"):
        value = thresholds[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise InspectorError(f"thresholds.{name} must be between 0 and 1.")
    allowed_formats = {str(item).lower().lstrip(".") for item in config.get("formats", [])}
    if not allowed_formats or not allowed_formats <= {"png", "jpg", "jpeg", "webp"}:
        raise InspectorError("formats may contain only png, jpg, jpeg, and webp.")
    overrides = config.get("severity_overrides", {})
    if not isinstance(overrides, dict) or any(str(value).upper() not in {"WARN", "FAIL", "IGNORE"} for value in overrides.values()):
        raise InspectorError("severity_overrides values must be WARN, FAIL, or IGNORE.")


def discover_assets(input_dir: Path, config: dict[str, Any]) -> list[Path]:
    if not input_dir.is_dir():
        raise InspectorError(f"Input folder does not exist: {input_dir}")
    allowed = {f".{item.lower().lstrip('.')}" for item in config["formats"]}
    candidates: Iterable[Path] = input_dir.rglob("*") if config["recursive"] else input_dir.iterdir()
    assets = []
    for path in candidates:
        if not path.is_file():
            continue
        relative_parts = path.relative_to(input_dir).parts[:-1]
        if any(part in EXCLUDED_DIR_NAMES or part.startswith(REPORT_DIR_PREFIX) for part in relative_parts):
            continue
        suffix = path.suffix.lower()
        if suffix in allowed or suffix in UNSUPPORTED_IMAGE_EXTENSIONS:
            assets.append(path)
    return sorted(assets, key=lambda item: str(item.relative_to(input_dir)).casefold())


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("￥", "¥").replace("﹩", "$")
    return re.sub(r"\s+", "", normalized)


def normalize_number(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace(",", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?%?", value)
    return match.group(0) if match else value


def make_finding(config: dict[str, Any], code: str, severity: str, zh: str, en: str, **details: Any) -> Finding | None:
    override = str(config.get("severity_overrides", {}).get(code, severity)).upper()
    if override == "IGNORE":
        return None
    return Finding(code=code, severity=override, zh=zh, en=en, details=details)


def add_finding(result: AssetResult, config: dict[str, Any], code: str, severity: str, zh: str, en: str, **details: Any) -> None:
    finding = make_finding(config, code, severity, zh, en, **details)
    if finding:
        result.add(finding)


def perceptual_hash(image: Image.Image) -> int:
    gray = ImageOps.grayscale(image).resize((9, 8), Image.Resampling.LANCZOS)
    pixels = _pixels(gray)
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return value


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _alpha_visible_fraction(image: Image.Image) -> float:
    if "A" not in image.getbands():
        return 1.0
    histogram = image.getchannel("A").histogram()
    return sum(histogram[1:]) / max(1, image.width * image.height)


def _edge_activity(image: Image.Image) -> float:
    gray = ImageOps.grayscale(image)
    gray.thumbnail((512, 512))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    band = max(1, min(edges.width, edges.height) // 50)
    boxes = [
        (0, 0, edges.width, band),
        (0, edges.height - band, edges.width, edges.height),
        (0, 0, band, edges.height),
        (edges.width - band, 0, edges.width, edges.height),
    ]
    active = total = 0
    for box in boxes:
        pixels = _pixels(edges.crop(box))
        active += sum(pixel > 48 for pixel in pixels)
        total += len(pixels)
    return active / max(1, total)


def _pixels(image: Image.Image) -> list[int]:
    getter = getattr(image, "get_flattened_data", None)
    return list(getter() if getter else image.getdata())


def _gray_array(image: Image.Image) -> np.ndarray:
    return np.asarray(ImageOps.grayscale(image), dtype=np.uint8)


def _compression_blockiness(gray: np.ndarray) -> tuple[float, float]:
    """Return 8px-boundary/interior difference ratio and boundary mean."""
    if gray.shape[0] < 16 or gray.shape[1] < 16:
        return 0.0, 0.0
    values = gray.astype(np.float32)
    vertical = np.abs(np.diff(values, axis=1))
    horizontal = np.abs(np.diff(values, axis=0))
    vertical_mask = np.zeros(vertical.shape[1], dtype=bool)
    horizontal_mask = np.zeros(horizontal.shape[0], dtype=bool)
    vertical_mask[7::8] = True
    horizontal_mask[7::8] = True
    boundaries = np.concatenate((vertical[:, vertical_mask].ravel(), horizontal[horizontal_mask, :].ravel()))
    interiors = np.concatenate((vertical[:, ~vertical_mask].ravel(), horizontal[~horizontal_mask, :].ravel()))
    boundary_mean = float(boundaries.mean()) if boundaries.size else 0.0
    interior_mean = float(interiors.mean()) if interiors.size else 0.0
    return boundary_mean / max(interior_mean, 1.0), boundary_mean


def _subject_crop_sides(image: Image.Image, thresholds: dict[str, Any]) -> list[str]:
    """Find large foreground-like regions touching an edge; this is only a heuristic."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = rgb.shape[:2]
    scale = min(1.0, 640.0 / max(height, width))
    if scale < 1:
        rgb = cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    height, width = lab.shape[:2]
    patch = max(2, min(height, width) // 20)
    corners = np.concatenate(
        (
            lab[:patch, :patch].reshape(-1, 3),
            lab[:patch, -patch:].reshape(-1, 3),
            lab[-patch:, :patch].reshape(-1, 3),
            lab[-patch:, -patch:].reshape(-1, 3),
        )
    )
    background = np.median(corners, axis=0)
    distance = np.linalg.norm(lab - background, axis=2)
    mask = (distance >= thresholds["subject_background_distance"]).astype(np.uint8) * 255
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sides: set[str] = set()
    total_area = height * width
    margin = max(1, round(min(height, width) * 0.01))
    for contour in contours:
        area_ratio = cv2.contourArea(contour) / max(1, total_area)
        if area_ratio < thresholds["subject_crop_min_area_ratio"] or area_ratio > 0.92:
            continue
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if x <= margin:
            sides.add("left")
        if y <= margin:
            sides.add("top")
        if x + box_width >= width - margin:
            sides.add("right")
        if y + box_height >= height - margin:
            sides.add("bottom")
    order = ("top", "right", "bottom", "left")
    return [side for side in order if side in sides]


def _apply_quality_checks(result: AssetResult, image: Image.Image, config: dict[str, Any]) -> None:
    thresholds = config["thresholds"]
    gray = _gray_array(image)
    result.sharpness_score = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)
    blockiness, boundary_mean = _compression_blockiness(gray)
    result.blockiness_score = round(blockiness, 3)
    result.shadow_clip_fraction = round(float(np.mean(gray <= 5)), 4)
    result.highlight_clip_fraction = round(float(np.mean(gray >= 250)), 4)

    if result.brightness_stddev is not None and result.brightness_stddev > thresholds["blank_stddev_max"]:
        if result.sharpness_score < thresholds["blur_laplacian_warn"]:
            add_finding(result, config, "BLURRY_IMAGE", "WARN", "图片可能模糊或失焦", "Image may be blurred or out of focus", score=result.sharpness_score, threshold=thresholds["blur_laplacian_warn"])
        if blockiness >= thresholds["compression_blockiness_warn"] and boundary_mean >= thresholds["compression_boundary_min"]:
            add_finding(result, config, "COMPRESSION_BLOCKING", "WARN", "检测到明显的块状压缩痕迹", "Visible block-compression artifacts detected", score=round(blockiness, 3), boundary_mean=round(boundary_mean, 2))
    if result.shadow_clip_fraction >= thresholds["shadow_clip_fraction_warn"]:
        add_finding(result, config, "SHADOW_CLIPPING", "WARN", "较大区域压在纯黑附近，暗部细节可能丢失", "A large area is clipped near black; shadow detail may be lost", fraction=result.shadow_clip_fraction)
    if result.highlight_clip_fraction >= thresholds["highlight_clip_fraction_warn"]:
        add_finding(result, config, "HIGHLIGHT_CLIPPING", "WARN", "较大区域压在纯白附近，高光细节可能丢失", "A large area is clipped near white; highlight detail may be lost", fraction=result.highlight_clip_fraction)
    result.subject_crop_sides = _subject_crop_sides(image, thresholds)
    if result.subject_crop_sides:
        add_finding(result, config, "POSSIBLE_SUBJECT_CLIPPING", "WARN", "显著前景区域接触图片边缘，主体可能被裁切", "A prominent foreground region touches the image edge; the subject may be clipped", sides=result.subject_crop_sides)


def inspect_asset(path: Path, input_dir: Path, config: dict[str, Any], ocr_engine: OCREngine | None) -> AssetResult:
    relative = path.relative_to(input_dir).as_posix()
    result = AssetResult(path=relative, filename=path.name, file_bytes=path.stat().st_size)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        add_finding(result, config, "UNSUPPORTED_FORMAT", "FAIL", "不支持的图片格式", "Unsupported image format", extension=path.suffix)
        return result
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as opened:
            result.format = opened.format
            image = ImageOps.exif_transpose(opened).copy()
    except Exception as exc:
        add_finding(result, config, "CORRUPT_IMAGE", "FAIL", "图片损坏或无法解码", "Image is corrupt or cannot be decoded", error=str(exc))
        return result

    result.width, result.height = image.size
    result.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    result.perceptual_hash = f"{perceptual_hash(image):016x}"
    stats = ImageStat.Stat(ImageOps.grayscale(image))
    result.mean_brightness = round(float(stats.mean[0]), 2)
    result.brightness_stddev = round(float(stats.stddev[0]), 2)
    result.visible_fraction = round(_alpha_visible_fraction(image), 4)
    expected, thresholds = config["expectations"], config["thresholds"]

    if expected["width"] is not None and result.width != expected["width"]:
        add_finding(result, config, "WIDTH_MISMATCH", "FAIL", "图片宽度不符合要求", "Image width does not match", expected=expected["width"], actual=result.width)
    if expected["height"] is not None and result.height != expected["height"]:
        add_finding(result, config, "HEIGHT_MISMATCH", "FAIL", "图片高度不符合要求", "Image height does not match", expected=expected["height"], actual=result.height)
    if expected["aspect_ratio"] is not None:
        actual_ratio = result.width / result.height
        if abs(actual_ratio - expected["aspect_ratio"]) > expected["aspect_tolerance"]:
            add_finding(result, config, "ASPECT_RATIO_MISMATCH", "FAIL", "图片宽高比不符合要求", "Image aspect ratio does not match", expected=expected["aspect_ratio"], actual=round(actual_ratio, 4))
    if expected["min_file_bytes"] is not None and result.file_bytes < expected["min_file_bytes"]:
        add_finding(result, config, "FILE_TOO_SMALL", "FAIL", "文件小于最低要求", "File is smaller than required", expected=expected["min_file_bytes"], actual=result.file_bytes)
    if expected["max_file_bytes"] is not None and result.file_bytes > expected["max_file_bytes"]:
        add_finding(result, config, "FILE_TOO_LARGE", "FAIL", "文件超过大小限制", "File exceeds the size limit", expected=expected["max_file_bytes"], actual=result.file_bytes)
    if result.brightness_stddev <= thresholds["blank_stddev_max"]:
        add_finding(result, config, "NEARLY_BLANK", "WARN", "图片几乎没有视觉变化", "Image appears nearly blank", stddev=result.brightness_stddev)
    if result.mean_brightness <= thresholds["dark_mean_max"]:
        add_finding(result, config, "TOO_DARK", "WARN", "图片整体异常偏暗", "Image appears unusually dark", mean=result.mean_brightness)
    if result.mean_brightness >= thresholds["bright_mean_min"]:
        add_finding(result, config, "TOO_BRIGHT", "WARN", "图片整体异常偏亮", "Image appears unusually bright", mean=result.mean_brightness)
    if result.visible_fraction < thresholds["min_visible_fraction"]:
        add_finding(result, config, "MOSTLY_TRANSPARENT", "WARN", "图片绝大部分透明", "Image is mostly transparent", visible_fraction=result.visible_fraction)
    edge_activity = round(_edge_activity(image), 4)
    if edge_activity >= thresholds["edge_activity_warn"]:
        add_finding(result, config, "EDGE_ACTIVITY", "WARN", "边缘存在较多高对比内容，可能需要检查裁切", "High-contrast content touches the edge; inspect for clipping", edge_activity=edge_activity)
    _apply_quality_checks(result, image, config)
    if ocr_engine is not None:
        result.ocr_tokens = ocr_engine.read(path)
        result.ocr_text = " ".join(token.text for token in result.ocr_tokens)
        check_ocr_rules(result, config, image)
    return result


def check_ocr_rules(result: AssetResult, config: dict[str, Any], image: Image.Image | None = None) -> None:
    expected = config["expectations"]
    normalized = normalize_text(result.ocr_text)
    for required in expected["required_text"]:
        if normalize_text(required) not in normalized:
            add_finding(result, config, "REQUIRED_TEXT_MISSING", "FAIL", "缺少必需文字", "Required text is missing", expected=required)
    for forbidden in expected["forbidden_text"]:
        if normalize_text(forbidden) in normalized:
            add_finding(result, config, "FORBIDDEN_TEXT_FOUND", "FAIL", "发现禁止文字", "Forbidden text was found", text=forbidden)
    observed_numbers = {
        normalize_number(value)
        for value in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?%?", unicodedata.normalize("NFKC", result.ocr_text))
    }
    for number in expected["critical_numbers"]:
        if normalize_number(number) not in observed_numbers:
            add_finding(result, config, "CRITICAL_NUMBER_MISSING", "FAIL", "关键数字缺失或发生变化", "Critical number is missing or changed", expected=number, observed=sorted(observed_numbers))
    low_tokens = [token for token in result.ocr_tokens if token.confidence < config["ocr"]["low_confidence"]]
    if low_tokens:
        add_finding(result, config, "OCR_LOW_CONFIDENCE", "WARN", "部分文字识别置信度较低", "Some OCR text has low confidence", tokens=[{"text": token.text, "confidence": round(token.confidence, 3)} for token in low_tokens])
    if image is not None:
        _check_text_readability(result, config, image)
    safe = expected["safe_zone"]
    if any(safe.values()) and result.width and result.height:
        if safe["left"] + safe["right"] >= result.width or safe["top"] + safe["bottom"] >= result.height:
            add_finding(result, config, "SAFE_ZONE_INVALID", "FAIL", "安全边距占满或超过图片", "Safe margins consume the entire image")
            return
        violating = []
        for token in result.ocr_tokens:
            xs = [point[0] for point in token.box]
            ys = [point[1] for point in token.box]
            if min(xs) < safe["left"] or max(xs) > result.width - safe["right"] or min(ys) < safe["top"] or max(ys) > result.height - safe["bottom"]:
                violating.append(token.text)
        if violating:
            add_finding(result, config, "TEXT_OUTSIDE_SAFE_ZONE", "FAIL", "文字进入安全边距", "Text enters the safe margin", tokens=violating)


def _check_text_readability(result: AssetResult, config: dict[str, Any], image: Image.Image) -> None:
    thresholds = config["thresholds"]
    gray = _gray_array(image)
    min_height = max(thresholds["text_min_height_px"], image.height * thresholds["text_min_height_ratio"])
    small_tokens, low_contrast_tokens = [], []
    for token in result.ocr_tokens:
        xs, ys = [point[0] for point in token.box], [point[1] for point in token.box]
        left, right = max(0, int(math.floor(min(xs)))), min(image.width, int(math.ceil(max(xs))))
        top, bottom = max(0, int(math.floor(min(ys)))), min(image.height, int(math.ceil(max(ys))))
        if bottom - top < min_height:
            small_tokens.append(token.text)
        if right > left and bottom > top:
            crop = gray[top:bottom, left:right]
            contrast = float(np.percentile(crop, 95) - np.percentile(crop, 5))
            if contrast < thresholds["text_min_contrast_warn"]:
                low_contrast_tokens.append({"text": token.text, "contrast": round(contrast, 2)})
    if small_tokens:
        add_finding(result, config, "TEXT_TOO_SMALL", "WARN", "部分文字可能过小，实际发布时难以阅读", "Some text may be too small to read at delivery size", tokens=small_tokens, min_height=round(min_height, 2))
    if low_contrast_tokens:
        add_finding(result, config, "TEXT_LOW_CONTRAST", "WARN", "部分文字与背景对比度较低", "Some text has low contrast against its background", tokens=low_contrast_tokens)


def add_duplicate_findings(results: list[AssetResult], config: dict[str, Any]) -> None:
    complete = [result for result in results if result.sha256 and result.perceptual_hash]
    for index, left in enumerate(complete):
        for right in complete[index + 1 :]:
            if left.sha256 == right.sha256:
                pairs = ((left, right), (right, left))
                code, zh, en, details = "EXACT_DUPLICATE", "与另一张图片完全重复", "Exact duplicate of another image", {}
            else:
                distance = hamming_distance(left.perceptual_hash or "0", right.perceptual_hash or "0")
                if distance > config["thresholds"]["near_duplicate_distance"]:
                    continue
                pairs = ((left, right), (right, left))
                code, zh, en, details = "NEAR_DUPLICATE", "与另一张图片高度相似", "Near-duplicate of another image", {"distance": distance}
            for current, other in pairs:
                add_finding(current, config, code, "WARN", zh, en, other=other.path, **details)


def create_annotated_images(results: list[AssetResult], input_dir: Path, output_dir: Path, config: dict[str, Any]) -> None:
    annotated_dir = output_dir / "annotated"
    annotated_dir.mkdir(parents=True, exist_ok=True)
    colors = {"PASS": "#22c55e", "WARN": "#f59e0b", "FAIL": "#ef4444"}
    safe = config["expectations"]["safe_zone"]
    for index, result in enumerate(results, start=1):
        source = input_dir / Path(result.path)
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except Exception:
            image = Image.new("RGB", (960, 540), "#1f2937")
        draw = ImageDraw.Draw(image)
        thickness = max(2, min(image.size) // 300)
        draw.rectangle((1, 1, image.width - 2, image.height - 2), outline=colors[result.status], width=thickness)
        crop_width = max(thickness * 2, 5)
        if "top" in result.subject_crop_sides:
            draw.line((0, 1, image.width, 1), fill="#fb7185", width=crop_width)
        if "right" in result.subject_crop_sides:
            draw.line((image.width - 2, 0, image.width - 2, image.height), fill="#fb7185", width=crop_width)
        if "bottom" in result.subject_crop_sides:
            draw.line((0, image.height - 2, image.width, image.height - 2), fill="#fb7185", width=crop_width)
        if "left" in result.subject_crop_sides:
            draw.line((1, 0, 1, image.height), fill="#fb7185", width=crop_width)
        if any(safe.values()) and result.width and result.height and safe["left"] + safe["right"] < image.width and safe["top"] + safe["bottom"] < image.height:
            draw.rectangle((safe["left"], safe["top"], image.width - safe["right"] - 1, image.height - safe["bottom"] - 1), outline="#38bdf8", width=thickness)
        readability_tokens = {
            str(token)
            for finding in result.findings
            if finding.code in {"TEXT_TOO_SMALL", "TEXT_LOW_CONTRAST"}
            for token in (
                [item.get("text", "") for item in finding.details.get("tokens", [])]
                if finding.code == "TEXT_LOW_CONTRAST"
                else finding.details.get("tokens", [])
            )
        }
        for token in result.ocr_tokens:
            xs, ys = [point[0] for point in token.box], [point[1] for point in token.box]
            token_color = "#f59e0b" if token.text in readability_tokens else "#a78bfa"
            draw.rectangle((min(xs), min(ys), max(xs), max(ys)), outline=token_color, width=thickness)
        image.thumbnail((1600, 1600))
        target = annotated_dir / f"{index:04d}.jpg"
        image.save(target, "JPEG", quality=88)
        result.annotated_path = target.relative_to(output_dir).as_posix()


def create_contact_sheet(results: list[AssetResult], output_dir: Path) -> None:
    if not results:
        image = Image.new("RGB", (800, 240), "white")
        ImageDraw.Draw(image).text((30, 30), "No image assets found", fill="black")
        image.save(output_dir / "contact-sheet.jpg", quality=90)
        return
    columns, cell_width, cell_height = 4, 300, 260
    rows = math.ceil(len(results) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#111827")
    draw = ImageDraw.Draw(sheet)
    colors = {"PASS": "#22c55e", "WARN": "#f59e0b", "FAIL": "#ef4444"}
    for index, result in enumerate(results):
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        try:
            with Image.open(output_dir / (result.annotated_path or "")) as opened:
                thumb = opened.convert("RGB")
                thumb.thumbnail((cell_width - 20, cell_height - 60))
            sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 10))
        except Exception:
            pass
        draw.rectangle((x + 5, y + 5, x + cell_width - 5, y + cell_height - 5), outline=colors[result.status], width=4)
        draw.text((x + 10, y + cell_height - 38), f"{index + 1:03d} {result.status}  {result.filename[:24]}", fill="white")
    sheet.save(output_dir / "contact-sheet.jpg", quality=90)


def serialize_asset(result: AssetResult) -> dict[str, Any]:
    return asdict(result)


def report_payload(results: list[AssetResult], input_dir: Path, output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    counts = {status: sum(result.status == status for result in results) for status in ("PASS", "WARN", "FAIL")}
    return {
        "schema_version": 1,
        "tool": "Batch AI Gen Inspector",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_dir.resolve()),
        "output": str(output_dir.resolve()),
        "summary": {"total": len(results), **counts},
        "config": config,
        "assets": [serialize_asset(result) for result in results],
    }


def write_html_report(payload: dict[str, Any], output_dir: Path) -> None:
    summary = payload["summary"]
    cards = []
    for asset in payload["assets"]:
        findings = "".join(
            f'<li class="{html.escape(item["severity"].lower())}"><code>{html.escape(item["code"])}</code> '
            f'{html.escape(item["zh"])}<span>{html.escape(item["en"])}</span></li>'
            for item in asset["findings"]
        ) or '<li class="pass">未发现问题<span>No issues found</span></li>'
        ocr_text = html.escape(asset.get("ocr_text") or "—")
        image_src = html.escape(asset.get("annotated_path") or "")
        quality = (
            f'<div class="metrics"><span>清晰度 / Sharpness <b>{asset.get("sharpness_score") if asset.get("sharpness_score") is not None else "—"}</b></span>'
            f'<span>块状压缩 / Blockiness <b>{asset.get("blockiness_score") if asset.get("blockiness_score") is not None else "—"}</b></span>'
            f'<span>暗部裁切 / Shadows <b>{_percent(asset.get("shadow_clip_fraction"))}</b></span>'
            f'<span>高光裁切 / Highlights <b>{_percent(asset.get("highlight_clip_fraction"))}</b></span></div>'
        )
        cards.append(
            f'<article class="asset" data-status="{html.escape(asset["status"])}"><img src="{image_src}" alt="{html.escape(asset["filename"])}">'
            f'<div class="body"><div class="row"><h2>{html.escape(asset["filename"])}</h2><b class="badge {asset["status"].lower()}">{asset["status"]}</b></div>'
            f'<p class="meta">{asset.get("width") or "—"} × {asset.get("height") or "—"} · {asset.get("file_bytes") or 0} bytes</p>'
            f'<ul>{findings}</ul><details><summary>图面指标 / Quality metrics</summary>{quality}</details>'
            f'<details><summary>OCR 文字 / OCR text</summary><pre>{ocr_text}</pre></details></div></article>'
        )
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Batch AI Gen Inspector Report</title><style>
:root{{--bg:#0b1020;--panel:#151c30;--text:#e8edf7;--muted:#9aa7bd;--pass:#22c55e;--warn:#f59e0b;--fail:#ef4444}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}header,main{{max-width:1280px;margin:auto;padding:24px}}h1{{margin:.2rem 0}}.subtitle,.meta,li span{{color:var(--muted)}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px;margin:22px 0}}.stat,.asset{{background:var(--panel);border:1px solid #28324c;border-radius:14px}}.stat{{padding:18px}}.stat strong{{display:block;font-size:28px}}
.filters{{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}}button{{border:1px solid #3b4968;background:#151c30;color:var(--text);padding:8px 13px;border-radius:999px;cursor:pointer}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}}
.asset{{overflow:hidden}}.asset img{{width:100%;height:230px;object-fit:contain;background:#080c16}}.body{{padding:15px}}.row{{display:flex;align-items:start;gap:12px;justify-content:space-between}}h2{{font-size:16px;overflow-wrap:anywhere;margin:0}}
.badge{{padding:4px 8px;border-radius:7px;color:#08110a}}.pass{{color:var(--pass)}}.warn{{color:var(--warn)}}.fail{{color:var(--fail)}}.badge.pass{{background:var(--pass);color:#07120b}}.badge.warn{{background:var(--warn);color:#1e1400}}.badge.fail{{background:var(--fail);color:white}}
ul{{padding-left:20px}}li span{{display:block}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#080c16;padding:10px;border-radius:8px}}.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}}.metrics span{{background:#080c16;border-radius:7px;padding:8px;color:var(--muted)}}.metrics b{{display:block;color:var(--text)}}@media(max-width:650px){{.summary{{grid-template-columns:repeat(2,1fr)}}header,main{{padding:16px}}}}
</style></head><body><header><h1>Batch AI Gen Inspector</h1><div class="subtitle">批量 AI 图片验收报告 / Batch AI image inspection report</div>
<div class="summary"><div class="stat"><span>总计 / Total</span><strong>{summary["total"]}</strong></div><div class="stat pass"><span>通过 / Pass</span><strong>{summary["PASS"]}</strong></div><div class="stat warn"><span>警告 / Warn</span><strong>{summary["WARN"]}</strong></div><div class="stat fail"><span>失败 / Fail</span><strong>{summary["FAIL"]}</strong></div></div></header>
<main><p class="subtitle">图面质量 WARN 是可解释的人工复核信号，不代表已经完成主观审美判断。 / Visual-quality WARN findings are explainable review signals, not subjective aesthetic judgments.</p><div class="filters"><button onclick="filterAssets('ALL')">全部 / All</button><button onclick="filterAssets('PASS')">PASS</button><button onclick="filterAssets('WARN')">WARN</button><button onclick="filterAssets('FAIL')">FAIL</button></div><section class="grid">{''.join(cards)}</section></main>
<script>function filterAssets(s){{document.querySelectorAll('.asset').forEach(e=>e.hidden=s!=='ALL'&&e.dataset.status!==s)}}</script></body></html>"""
    (output_dir / "report.html").write_text(document, encoding="utf-8")


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def choose_output_dir(input_dir: Path, requested: Path | None) -> Path:
    if requested:
        return requested
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = input_dir / REPORT_DIR_PREFIX
    candidate = base / stamp
    suffix = 1
    while candidate.exists():
        candidate = base / f"{stamp}-{suffix}"
        suffix += 1
    return candidate


def run_inspection(input_dir: Path, config: dict[str, Any], output_dir: Path, ocr_engine: OCREngine | None = None) -> dict[str, Any]:
    assets = discover_assets(input_dir, config)
    if config["ocr"]["enabled"] and ocr_engine is None:
        ocr_engine = RapidOCREngine()
    results = [inspect_asset(path, input_dir, config, ocr_engine) for path in assets]
    add_duplicate_findings(results, config)
    output_dir.mkdir(parents=True, exist_ok=False)
    create_annotated_images(results, input_dir, output_dir, config)
    create_contact_sheet(results, output_dir)
    payload = report_payload(results, input_dir, output_dir, config)
    (output_dir / "resolved-config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html_report(payload, output_dir)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a batch of AI-generated images and create a bilingual QA report.")
    parser.add_argument("--input", required=True, type=Path, help="Folder containing image assets")
    parser.add_argument("--config", type=Path, help="Optional version 1 JSON configuration")
    parser.add_argument("--output", type=Path, help="Optional report directory; it must not already exist")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        output_dir = choose_output_dir(args.input, args.output)
        payload = run_inspection(args.input, config, output_dir)
    except (InspectorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = payload["summary"]
    print(f"Report: {output_dir / 'report.html'}")
    print(f"Total {summary['total']} | PASS {summary['PASS']} | WARN {summary['WARN']} | FAIL {summary['FAIL']}")
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

