# Batch AI Gen Inspector

[简体中文](README.md) | [English](README.en.md)

批量检查 AI 生成图片的交付问题，让你只人工查看真正需要复核的图片。

它不负责生成图片，也不主观判断“好不好看”。当前版本检查文件损坏、尺寸、比例、文件大小、空白/透明异常、OCR 文字与数字、安全边距、重复图片，并进一步检查模糊度、块状压缩、曝光裁切、文字可读性和可能的主体裁切。

新增图面质量检查默认输出 `WARN`，提醒人工复核，而不是把统计信号冒充确定错误：

- 模糊度：使用清晰度分数发现可能失焦或过度柔化的图片。
- 压缩损伤：检查典型的 8×8 块状断层。
- 曝光：检查大面积死黑和过曝高光。
- 文字可读性：检查 OCR 文字框的实际高度和局部明暗对比。
- 主体裁切：检查显著前景区域是否接触画面边缘；这是启发式风险提示，不是人物或商品识别。

## 输出什么

一次运行会生成：

- `report.html`：可筛选的中英双语报告。
- `report.json`：供 Agent 和自动化读取的结果。
- `resolved-config.json`：本次实际采用的验收标准。
- `contact-sheet.jpg`：整批状态总览。
- `annotated/`：带安全区和 OCR 框的预览图。

原始图片只读，不会被改名、覆盖、移动或删除。OCR 在本机运行，图片和识别文字不会上传。

## Quick Start

安装为 Agent Skill：

```powershell
npx skills add kktbur/batch-ai-gen-inspector
```

也可以克隆仓库后直接运行。需要 Python 3.10–3.12，推荐使用项目独立虚拟环境。

Windows PowerShell 7：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
```

macOS / Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

项目使用当前维护的 `rapidocr` 和 CPU 版 `onnxruntime`。依赖安装完成后，OCR 可离线运行。

## 作为 Agent Skill 使用

安装为 Skill 后可以说：

> 使用 Batch AI Gen Inspector 检查这个文件夹。图片应为 1080×1440，必须包含“示例商品”和“¥29.99”，文字距离四边至少 60px。

Agent 会把自然语言要求转换成临时 JSON 配置，运行检查，并保留 `resolved-config.json` 便于复查和复跑。

## 直接运行

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\inspect_batch.py' --input '<图片目录>' --config '<配置文件.json>'
```

不提供 `--config` 时只使用通用默认规则。报告默认写入输入目录下的 `batch-ai-gen-inspector-report/<时间戳>/`。

退出码：

- `0`：检查完成，没有 FAIL。
- `1`：检查完成，至少一张图片 FAIL。
- `2`：配置或运行错误。

完整配置见 [references/configuration.md](references/configuration.md)，规则说明见 [references/rules.md](references/rules.md)。

## 测试

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

真实 OCR 冒烟测试默认跳过。需要运行时设置 `RUN_OCR_TESTS=1`。

## License

[MIT](LICENSE)

