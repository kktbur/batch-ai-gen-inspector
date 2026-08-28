# Inspection Rules

## FAIL: deterministic requirement violations

- `CORRUPT_IMAGE`: the file cannot be decoded.
- `UNSUPPORTED_FORMAT`: a known image file is outside PNG, JPEG, and WebP.
- `WIDTH_MISMATCH`, `HEIGHT_MISMATCH`, `ASPECT_RATIO_MISMATCH`: explicit geometry requirements are violated.
- `FILE_TOO_SMALL`, `FILE_TOO_LARGE`: explicit byte limits are violated.
- `REQUIRED_TEXT_MISSING`, `FORBIDDEN_TEXT_FOUND`, `CRITICAL_NUMBER_MISSING`: OCR output violates explicit copy requirements.
- `TEXT_OUTSIDE_SAFE_ZONE`: a detected OCR text box crosses the configured text-safe area.
- `SAFE_ZONE_INVALID`: configured margins consume the entire image.

## WARN: human review signals

- `NEARLY_BLANK`, `TOO_DARK`, `TOO_BRIGHT`, `MOSTLY_TRANSPARENT`: image statistics are unusual.
- `EDGE_ACTIVITY`: high-contrast pixels touch an edge. This is a heuristic and does not prove clipping.
- `OCR_LOW_CONFIDENCE`: at least one OCR token is below the configured confidence threshold.
- `EXACT_DUPLICATE`, `NEAR_DUPLICATE`: two batch assets repeat or are visually very similar.
- `BLURRY_IMAGE`: Laplacian sharpness is below the configured review threshold.
- `COMPRESSION_BLOCKING`: discontinuities along 8-pixel boundaries suggest block compression damage.
- `SHADOW_CLIPPING`, `HIGHLIGHT_CLIPPING`: a large image fraction is pinned near black or white.
- `TEXT_TOO_SMALL`, `TEXT_LOW_CONTRAST`: an OCR text box may be unreadable at delivery size.
- `POSSIBLE_SUBJECT_CLIPPING`: a large foreground-like region touches one or more image edges. This is a background-distance heuristic, not object recognition.

An asset is `FAIL` when it has any FAIL finding, otherwise `WARN` when it has any WARN finding, otherwise `PASS`. Process exit code `1` represents completed inspection with at least one failed asset. Exit code `2` is reserved for configuration or runtime errors.

