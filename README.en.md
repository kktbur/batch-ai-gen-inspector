# Batch AI Gen Inspector

[简体中文](README.md) | [English](README.en.md)

Preflight batches of AI-generated images and review only the assets that actually need attention.

This project does not generate images or assign subjective aesthetic scores. It checks corrupt files, dimensions, aspect ratio, file size, blank or transparent output, OCR text and numbers, text-safe margins, duplicates, blur, block compression, exposure clipping, text readability, and possible subject cropping.

The added visual-quality checks are WARN-level review signals by default:

- Blur: a sharpness score detects likely defocus or excessive softening.
- Compression damage: 8×8 boundary discontinuities indicate block artifacts.
- Exposure: large near-black or near-white regions indicate clipped detail.
- Text readability: OCR box height and local luminance contrast are checked.
- Subject cropping: a foreground-like region touching an edge is flagged. This is a heuristic, not person or product recognition.

## Outputs

Each run creates:

- `report.html`: filterable bilingual report.
- `report.json`: machine-readable results for agents and automation.
- `resolved-config.json`: the exact acceptance rules used for the run.
- `contact-sheet.jpg`: whole-batch status overview.
- `annotated/`: previews with safe-zone and OCR boxes.

Source images are read-only. They are never renamed, overwritten, moved, or deleted. OCR runs locally; images and extracted text are not uploaded.

## Quick Start

Install as an Agent Skill:

```bash
npx skills add kktbur/batch-ai-gen-inspector
```

You can also clone the repository and run the CLI directly. Python 3.10–3.12 is recommended.

```bash
python -m venv .venv
# Windows
.venv/Scripts/python -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

The project uses the maintained `rapidocr` package with CPU `onnxruntime`. OCR works offline after dependencies are installed.

## Use as an Agent Skill

After installing the skill, ask:

> Use Batch AI Gen Inspector on this folder. Images must be 1080×1440, contain “SAMPLE PRODUCT” and “$29.99”, and keep text at least 60px from every edge.

The Agent translates those requirements into temporary JSON, runs the inspector, and preserves `resolved-config.json` for auditing and reruns.

## Run directly

```bash
python scripts/inspect_batch.py --input <image-folder> --config <config.json>
```

Without `--config`, only generic default rules are used. Reports default to `batch-ai-gen-inspector-report/<timestamp>/` inside the input folder.

Exit codes: `0` means no failed assets, `1` means inspection completed with at least one FAIL, and `2` means a configuration or runtime error.

See [configuration](references/configuration.md) and [inspection rules](references/rules.md) for details.

## Tests

```bash
python -m unittest discover -s tests -v
```

The real OCR smoke test is skipped unless `RUN_OCR_TESTS=1` is set.

## License

[MIT](LICENSE)

