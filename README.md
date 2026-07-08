# AnalyzeProjects

AnalyzeProjects evaluates a controlled portfolio of local projects with OpenAI and produces per-project JSON plus a compiled Markdown summary. `ProjectIndex.md` is the sole inclusion authority: the analyzer does not discover extra projects from drive roots.

## Current evaluation inventory

The checked-in index contains exactly 30 projects:

| Group | Count | Evaluation basis |
| --- | ---: | --- |
| DRS | 12 | Desktop Application Release Standard |
| CTS | 6 | Command Tool Standard |
| WDS | 4 | Website Development Standard |
| Standards | 8 | Internal clarity, completeness, consistency, and implementability |

`QB-Winget` is evaluated as DRS. `Llama` is intentionally not in the evaluation index. The eight standards are read from `D:\.library\aptlantis_core`, not the historical `.city_hall` tree.

## How evaluation works

1. Parse and validate `ProjectIndex.md`.
2. Require 30 unique, existing project directories with the exact 12/6/4/8 group distribution.
3. Select `<folder>.manifest.toml` when present. A single alternative direct manifest is accepted; a manifest is optional.
4. Read each DRS, CTS, and WDS governing standard once.
5. Sample high-value files within the configured file and character budgets.
6. Send the project evidence and its group-specific governance context to `gpt-5-mini-2025-08-07`.
7. Validate and normalize the JSON response, write individual results, then compile the summary.

Standards projects use a separate rubric. Application release requirements are not automatically imposed on governance documents.

## Sampling and truncation safety

Every transferred file block is labeled either `complete file` or `sampled excerpt`, including the exact excerpt size when shortened. An excerpt ending is a context-transfer boundary, not evidence of a damaged repository file. The prompt prohibits treating that boundary as a defect, and post-processing removes transfer-related truncation claims from actionable findings.

Each result records:

- `project_group`
- `governing_standard`
- `repository_url`
- `manifest_path` when available
- `sampling.sampled_files`, sampled characters, and configured limits

The original assessment fields remain compatible with existing dashboard consumers.

## Configuration and credentials

Copy `config.example.toml` to the ignored local `config.toml` when bootstrapping a checkout. Set the OpenAI key in the process environment; credentials are not read from configuration files:

```powershell
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "Machine")
```

The analyzer fails before scanning when the key is unavailable. If a credential was previously stored in `config.toml`, rotate it through the OpenAI dashboard.

## Run

From `D:\CTS\AnalyzeProjects`:

```powershell
python Summarizer.py
```

The configured output remains under the existing dashboard summary location. Recent result files may be reused when `skip_already_processed` is enabled.

## Verification

The test suite uses temporary directories and mocked HTTP requests; it does not make paid API calls:

```powershell
python -m unittest discover -s tests -v
```

To inspect the index without invoking the model:

```powershell
python -c "import Summarizer; c=Summarizer.load_config('config.toml'); print([(t.project_group, t.project_name) for t in Summarizer.discover_projects(c)])"
```

Malformed entries, duplicate paths, missing directories, missing standards, and incorrect group totals are fatal rather than silently skipped.
