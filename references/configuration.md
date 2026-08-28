# Configuration

The inspector accepts an optional UTF-8 JSON file. The Agent should translate only requirements the user actually supplied and leave every other expectation unset.

```json
{
  "version": 1,
  "recursive": false,
  "formats": ["png", "jpg", "jpeg", "webp"],
  "ocr": {
    "enabled": true,
    "low_confidence": 0.55
  },
  "expectations": {
    "width": 1080,
    "height": 1440,
    "aspect_ratio": 0.75,
    "aspect_tolerance": 0.01,
    "min_file_bytes": null,
    "max_file_bytes": 5242880,
    "required_text": ["示例商品", "¥29.99"],
    "forbidden_text": ["SAMPLE ONLY"],
    "critical_numbers": ["29.99"],
    "safe_zone": {
      "top": 60,
      "right": 60,
      "bottom": 60,
      "left": 60
    }
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
    "subject_background_distance": 28.0
  },
  "severity_overrides": {
    "EDGE_ACTIVITY": "IGNORE"
  }
}
```

## Rules

- `width` and `height` are exact pixel requirements. Leave either `null` when it was not specified.
- `aspect_ratio` is width divided by height. Do not add it when exact dimensions are already sufficient unless the user stated both requirements.
- File sizes are bytes.
- `required_text` and `forbidden_text` use normalized substring matching.
- `critical_numbers` compares normalized numeric tokens separately from surrounding OCR text.
- Safe-zone values are pixels measured inward from each edge. In v0.1 they apply to detected text boxes, not people, products, or logos.
- A perceptual-hash distance of `0` is extremely similar; larger values are less similar. The default warning threshold is `6`.
- Lower `blur_laplacian_warn` values make blur detection less sensitive. Smooth illustrations can legitimately have low sharpness, so this remains WARN by default.
- Blockiness compares pixel discontinuities on 8-pixel boundaries with ordinary neighboring pixels. Both the ratio and minimum boundary difference must be exceeded.
- Exposure fractions measure pixels near black (`0–5`) and white (`250–255`).
- Text size uses the larger of `text_min_height_px` and image height multiplied by `text_min_height_ratio`. Text contrast uses the grayscale 5th–95th percentile range inside each OCR box.
- Subject-crop detection estimates the corner background color, segments significantly different regions, and warns when a sufficiently large region touches an edge. Full-bleed scenes and complex backgrounds can produce false positives.
- Severity overrides accept `WARN`, `FAIL`, or `IGNORE`. Use them only when the user explicitly changes the acceptance policy.
- Set `ocr.enabled` to `false` only for tests or when the user intentionally wants non-text checks.

