# Project Summarizer Agent

AI-powered tool to automatically assess and summarize multiple coding projects using Ollama-hosted models.

## What's New

### Fixed Issues
- **Schema alignment** - Template now matches markdown output (added status, complexity, tags fields)
- **Config loading** - Now properly loads from `config.toml` instead of hardcoded values
- **Field type fix** - Changed `percentage_complete` from array to integer (0-100)

### Enhancements
1. **TOML Configuration** - Externalized all settings to `config.toml`
2. **Directory Exclusions** - Automatically skips `.git`, `node_modules`, `venv`, etc.
3. **Retry Logic** - Exponential backoff (1s, 2s, 4s) for failed API calls
4. **Incremental Processing** - Skips projects processed within 7 days
5. **Context Warnings** - Alerts when project files exceed safe token limits
6. **Better Logging** - Structured logging with timestamps and levels
7. **Progress Tracking** - Shows "Processing 5/23 projects..."
8. **JSON Validation** - Verifies model responses match expected schema
9. **Summary Statistics** - Average completion, status/complexity distributions
10. **Concurrent Processing** - Processes 3 projects in parallel (configurable)

## Installation

```bash
# For Python 3.11+
pip install requests

# For Python <3.11, also install:
pip install tomli
```

## Security

**Important:** If you're using an API key for cloud-hosted models:

1. **Never commit** `config.toml` to version control if it contains your API key
2. Add `config.toml` to your `.gitignore`
3. Consider using environment variables instead:
   - Set `OLLAMA_API_KEY` environment variable
   - Remove `api_key` from config.toml
   - The code will check environment variables as a fallback

## Configuration

Edit `config.toml`:

```toml
[project]
root = "D:/Projects"              # Legacy single-root option
roots = ["D:/Projects"]           # Preferred: evaluate each top-level child directory under these roots
expand_children_of = ["D:/Projects/utilities"]  # Treat each child folder here as its own project
standalone_projects = [           # Explicit project roots outside the main roots
  "C:/Users/You/Desktop/Tool & Data Tracking",
  "C:/Users/You/Desktop/Aptlantis-Studio",
]
out_dir = "D:/Projects/project_summaries"

[model]
name = "minimax-m2:cloud"         # Ollama model name
ollama_host = "http://127.0.0.1:11434"
ollama_api_key = "your-api-key-here"  # Optional: for cloud-hosted models

[processing]
max_chars_per_file = 2500         # Truncate long files
max_total_chars = 90000           # Hard cap for total prompt context per project
max_files_per_project = 60        # Max number of files sampled per project
delay = 0.3                       # Delay between API calls (seconds)
max_retries = 3                   # Retry failed requests
skip_already_processed = true     # Skip recently processed projects
max_workers = 3                   # Concurrent processing threads

[filtering]
allowed_exts = [".py", ".md", ...]  # File types to analyze
excluded_dirs = [".git", "node_modules", ...]  # Directories to skip
```

## Usage

```bash
python Summarizer.py
```

Output:
- Individual JSON files: `project_summaries/{project_name}.json`
- Combined markdown: `project_summaries/summary.md`

## Schema

Each project is assessed with:

```json
{
  "project": "MyProject",
  "source_path": "D:/Projects/MyProject",
  "summary": "Brief description...",
  "status": "In Progress",
  "percentage_complete": 75,
  "missing_pieces": ["Unit tests", "Documentation"],
  "next_steps": ["Add error handling", "Deploy to prod"],
  "potential_improvements": ["Add caching", "Optimize queries"],
  "complexity": "Medium",
  "tags": ["python", "web", "automation"]
}
```

## Features

- **Smart Filtering** - Skips binary files, build artifacts, dependencies
- **Budgeted Context** - Caps total chars and sampled files so very large repos do not blow up the model request
- **Flexible Discovery** - Supports multiple roots, explicit standalone projects, and expanded child-project folders
- **Incremental Updates** - Only reprocesses changed/new projects
- **Fault Tolerant** - Continues processing even if individual projects fail
- **Rich Output** - Markdown summary sorted by completion percentage
- **Statistics Dashboard** - Overview of all projects at a glance

## Performance

- Processes 20 small projects in ~2-3 minutes (with 3 workers)
- Uses ~100-500 tokens per project depending on size
- Automatic caching reduces re-analysis of unchanged projects
