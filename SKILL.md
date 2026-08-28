---
name: batch-ai-gen-inspector
description: Inspect folders of AI-generated PNG, JPEG, and WebP assets for delivery problems such as corruption, wrong dimensions, missing text, unsafe text margins, blur, compression artifacts, exposure clipping, unreadable text, possible subject cropping, and duplicates. Use when a user asks to batch-check, preflight, lint, or accept AI-generated images; do not use it to generate, edit, or aesthetically rank images.
---

# Batch AI Gen Inspector

Turn the user's acceptance requirements into a repeatable local inspection and a bilingual HTML report.

## Workflow

1. Confirm the input folder and extract only requirements the user actually stated. Never invent expected dimensions, required copy, prices, URLs, or margins.
2. Read [references/configuration.md](references/configuration.md) when requirements need a config file. Write a temporary JSON config outside the skill folder; the resolved config is preserved in the report.
3. Use the project's `.venv` Python when it exists. If dependencies are missing, explain that local OCR requires `rapidocr` and `onnxruntime`; obtain authorization before installing anything.
4. Run:

```powershell
& '<python>' '<skill>\scripts\inspect_batch.py' --input '<image-folder>' --config '<config.json>'
```

Omit `--config` when the user wants only generic integrity, image-property, and duplicate checks. Use `--output` only when the user requests a particular report location.

5. Open `report.html` for the user and summarize PASS, WARN, and FAIL counts. A process exit code of `1` means inspected assets failed requirements, not that the tool crashed. Exit code `2` means configuration or runtime failure.

Read [references/rules.md](references/rules.md) when interpreting findings or changing severities.

## Boundaries

- Treat source images as read-only. Never rename, move, overwrite, or delete them.
- Keep all analysis local. Do not upload images or OCR text.
- This version does not judge aesthetics, brand style, product/person similarity, or reference drift. Do not present those as checked.
- Blur, compression, exposure, readability, and subject-edge findings are review signals. In particular, subject cropping is inferred from foreground-like regions touching an edge; it is not object recognition.
- Do not scan generated report folders, `.git`, `.venv`, or cache folders.

