#!/usr/bin/env python3
"""
Project Summarizer Agent
------------------------------------
Scans a directory of projects, reads their contents,
and uses an Ollama-hosted model (like MiniMax M2 or Phi)
to summarize status, missing parts, and next steps.

Enhanced with:
- TOML config loading
- Retry logic with exponential backoff
- Incremental processing
- Concurrent execution
- Progress tracking
- Better error handling
"""

import json
import logging
import os
import pathlib
import re
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List

from ModelClient import ask_model_json
from PromptTemplate import build_project_prompt, PROJECT_TEMPLATE

# --- Config Loading ---
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # fallback for older Python
    except ImportError:
        print("⚠️  Please install 'tomli' for Python <3.11: pip install tomli")
        exit(1)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)
CONFIG: Dict = {}


@dataclass(frozen=True)
class ProjectTarget:
    """A project identified by its canonical manifest and scanned from its folder."""
    project_name: str
    project_path: pathlib.Path
    manifest_path: pathlib.Path


def load_config(config_path: str = "config.toml") -> Dict:
    """Load configuration from a TOML file."""
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        logger.info(f"✅ Loaded config from {config_path}")
        return config
    except FileNotFoundError:
        logger.error(f"❌ Config file not found: {config_path}")
        exit(1)
    except Exception as e:
        logger.error(f"❌ Error loading config: {e}")
        exit(1)


def should_skip_directory(dir_name: str, excluded_dirs: List[str]) -> bool:
    """Check if the directory should be skipped."""
    return dir_name in excluded_dirs or dir_name.startswith('.')


def make_output_stem(project_name: str, project_path: pathlib.Path, manifest_path: pathlib.Path | None = None) -> str:
    """Build a stable, filesystem-safe output filename stem."""
    manifest_part = f"__{manifest_path.name}" if manifest_path else ""
    raw = f"{project_name}__{project_path}{manifest_part}"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return stem or "project"


def normalize_path(path: pathlib.Path) -> str:
    """Normalize a path string for reliable case-insensitive comparisons on Windows."""
    return str(path.resolve()).replace("\\", "/").lower()


def is_manifest_noise(manifest_path: pathlib.Path) -> bool:
    """Return true for directory or generated data manifests that are not project targets."""
    name = manifest_path.name.lower()
    normalized = normalize_path(manifest_path)

    if name == "directory.manifest.toml":
        return True

    if name == "corpus_manifest.toml":
        return True

    if "/src/features/distributions/data/projects/" in normalized:
        return True

    return False


def manifest_project_name(manifest_path: pathlib.Path) -> str:
    """Derive the display/project name from [project].title, then id, then filename."""
    try:
        with open(manifest_path, "rb") as f:
            manifest = tomllib.load(f)
        project = manifest.get("project", {})
        for key in ("title", "id"):
            value = project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as e:
        logger.debug(f"Could not read project name from {manifest_path}: {e}")

    stem = manifest_path.name
    for suffix in (".manifest.toml", "-manifest.toml", "_manifest.toml", ".toml"):
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return manifest_path.stem


def discover_projects(config: Dict) -> List[ProjectTarget]:
    """Discover project targets by finding canonical project manifests under configured roots."""
    project_cfg = config["project"]
    filtering_cfg = config["filtering"]

    root_paths = [project_cfg["root"]] if project_cfg.get("root") else []
    root_paths.extend(project_cfg.get("roots", []))

    standalone_paths = [pathlib.Path(p).resolve() for p in project_cfg.get("standalone_projects", [])]
    excluded_dirs = filtering_cfg["excluded_dirs"]

    discovered: List[ProjectTarget] = []
    seen_manifests = set()

    def _add_manifest(path: pathlib.Path):
        manifest_path = path.resolve()
        if manifest_path in seen_manifests:
            return
        if not manifest_path.exists() or not manifest_path.is_file():
            logger.warning(f"⚠️  Skipping missing manifest path: {manifest_path}")
            return
        if is_manifest_noise(manifest_path):
            return
        project_path = manifest_path.parent
        if should_skip_directory(project_path.name, excluded_dirs):
            return

        seen_manifests.add(manifest_path)
        discovered.append(
            ProjectTarget(
                project_name=manifest_project_name(manifest_path),
                project_path=project_path,
                manifest_path=manifest_path,
            )
        )

    def _walk_manifests(root: pathlib.Path):
        for current_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not should_skip_directory(d, excluded_dirs)]
            for file_name in files:
                if file_name.lower().endswith("manifest.toml"):
                    _add_manifest(pathlib.Path(current_root) / file_name)

    for root_str in root_paths:
        root = pathlib.Path(root_str).resolve()
        if not root.exists():
            logger.warning(f"⚠️  Root does not exist: {root}")
            continue
        if not root.is_dir():
            logger.warning(f"⚠️  Root is not a directory: {root}")
            continue

        _walk_manifests(root)

    for standalone in standalone_paths:
        if standalone.is_file() and standalone.name.lower().endswith("manifest.toml"):
            _add_manifest(standalone)
        elif standalone.is_dir():
            direct_manifests = sorted(
                standalone.glob("*manifest.toml"),
                key=lambda p: p.name.lower(),
            )
            if direct_manifests:
                for manifest in direct_manifests:
                    _add_manifest(manifest)
            else:
                logger.warning(f"⚠️  Standalone project has no manifest: {standalone}")
        else:
            logger.warning(f"⚠️  Skipping missing standalone path: {standalone}")

    return sorted(discovered, key=lambda target: normalize_path(target.manifest_path))


def collect_snippets(project_path, max_chars, allowed_exts, excluded_dirs, manifest_path=None):
    """
    Collect representative text from each code or documentation file
    inside a project folder (with a size limit per file).
    Skips common build/dependency directories.
    """
    max_total_chars = CONFIG["processing"].get("max_total_chars", 90000)
    max_files_per_project = CONFIG["processing"].get("max_files_per_project", 60)
    project_path = pathlib.Path(project_path).resolve()
    manifest_path = pathlib.Path(manifest_path).resolve() if manifest_path else None
    candidates = []
    snippets = []
    total_chars = 0
    sampled_files = 0

    if manifest_path:
        try:
            text = manifest_path.read_text(encoding="utf-8", errors="ignore")
            if text.strip():
                rel_path = manifest_path.relative_to(project_path)
                snippet = f"## {rel_path.as_posix()} (canonical project manifest)\n{text[:max_total_chars]}"
                snippets.append(snippet)
                total_chars += len(snippet)
                sampled_files += 1
        except Exception as e:
            logger.warning(f"⚠️  Could not read canonical manifest {manifest_path}: {e}")

    for root, dirs, files in os.walk(project_path):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if not should_skip_directory(d, excluded_dirs)]

        for f in files:
            if f.lower().endswith(tuple(allowed_exts)):
                p = pathlib.Path(root) / f
                resolved = p.resolve()
                if is_manifest_noise(resolved):
                    continue
                if manifest_path and resolved == manifest_path:
                    continue
                if manifest_path and f.lower().endswith("manifest.toml"):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    if text.strip():
                        rel_path = p.relative_to(project_path)
                        snippet = f"## {rel_path.as_posix()}\n{text[:max_chars]}"
                        candidates.append((score_file(rel_path), len(snippet), snippet))
                except Exception as e:
                    logger.debug(f"Could not read {f}: {e}")
                    pass

    candidates.sort(key=lambda item: (-item[0], item[1]))

    for _, snippet_len, snippet in candidates:
        if sampled_files >= max_files_per_project:
            break
        if snippets and (total_chars + snippet_len) > max_total_chars:
            break
        snippets.append(snippet)
        total_chars += snippet_len
        sampled_files += 1

    total_candidate_files = len(candidates)

    if total_candidate_files > max_files_per_project or total_chars >= max_total_chars:
        logger.info(
            f"📦 Sampled {sampled_files}/{total_candidate_files} files for {pathlib.Path(project_path).name} "
            f"({total_chars:,}/{max_total_chars:,} chars budget)"
        )

    return "\n\n".join(snippets), sampled_files, total_chars


def score_file(rel_path: pathlib.Path) -> int:
    """Assign a simple relevance score so key project files are sampled first."""
    rel_str = rel_path.as_posix().lower()
    name = rel_path.name.lower()

    score = 0

    if rel_path.parent == pathlib.Path("."):
        score += 40

    if name in {
        "readme.md", "readme.txt", "project-convo.md", "manifest.json", "project-manifest.toml",
        "package.json", "pyproject.toml", "cargo.toml", "go.mod", "requirements.txt",
        "composer.json", "build.gradle", "pom.xml", "nuget.config", "global.json",
        "dockerfile", "docker-compose.yml", "docker-compose.yaml", "makefile"
    }:
        score += 120

    if name.endswith((".md", ".txt", ".todo")):
        score += 35

    if name.endswith((".json", ".toml", ".yaml", ".yml", ".xml")):
        score += 25

    if any(part in rel_str for part in ("/src/", "/app/", "/lib/", "/docs/", "/scripts/")):
        score += 20

    if any(part in rel_str for part in ("/test/", "/tests/", "/spec/", "/fixtures/", "/samples/", "/vendor/", "/generated/")):
        score -= 30

    if name.endswith((".csv",)):
        score -= 40

    return score


def validate_response(response: Dict) -> bool:
    """Validate that model response matches the expected schema."""
    required_fields = set(PROJECT_TEMPLATE.keys())
    response_fields = set(response.keys())

    if not required_fields.issubset(response_fields):
        missing = required_fields - response_fields
        logger.warning(f"⚠️  Response missing fields: {missing}")
        return False
    return True


def get_model_api_key(config: Dict) -> str | None:
    """Resolve API key from config first, then environment variables."""
    model_cfg = config["model"]
    return (
        model_cfg.get("api_key")
        or model_cfg.get("openai_api_key")
        or model_cfg.get("ollama_api_key")
        or model_cfg.get("public_key")
        or os.getenv("OLLAMA_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def retry_with_backoff(func, max_retries: int = 3, *args, **kwargs):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt
            logger.warning(f"⚠️  Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)


def should_reprocess(json_path: pathlib.Path, max_age_days: int = 7) -> bool:
    """Check if a project should be reprocessed based on file age."""
    if not json_path.exists():
        return True

    file_age = datetime.now() - datetime.fromtimestamp(json_path.stat().st_mtime)
    return file_age > timedelta(days=max_age_days)


def process_project(project, config, skip_existing: bool = True):
    """
    Collect snippets, ask model with retry logic, return a structured summary.
    """
    if isinstance(project, ProjectTarget):
        target = project
    else:
        project_path = pathlib.Path(project).resolve()
        target = ProjectTarget(
            project_name=project_path.name,
            project_path=project_path,
            manifest_path=project_path / f"{project_path.name}.manifest.toml",
        )

    name = target.project_name
    project_path = target.project_path
    manifest_path = target.manifest_path
    out_dir = pathlib.Path(config["project"]["out_dir"])
    output_stem = make_output_stem(name, project_path, manifest_path)
    json_path = out_dir / f"{output_stem}.json"

    # Skip if already processed recently
    if skip_existing and json_path.exists() and not should_reprocess(json_path):
        logger.info(f"⏭️  Skipping {name} (already processed)")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing["project"] = name
            existing["source_path"] = str(project_path.resolve())
            existing["manifest_path"] = str(manifest_path.resolve())
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            return existing
        except Exception:
            logger.warning(f"⚠️  Could not load existing {name}, reprocessing...")

    # Collect project files
    text, file_count, total_chars = collect_snippets(
        project_path,
        config["processing"]["max_chars_per_file"],
        config["filtering"]["allowed_exts"],
        config["filtering"]["excluded_dirs"],
        manifest_path
    )

    if not text.strip():
        logger.info(f"⚪ Skipping {name} (no relevant files)")
        return None

    logger.info(f"🧠 Analyzing {name} ({file_count} files, {total_chars:,} chars)...")

    # Build prompt and query model with retry
    prompt = build_project_prompt(name, text)

    try:
        result = retry_with_backoff(
            ask_model_json,
            config["processing"]["max_retries"],
            config["model"]["name"],
            prompt,
            config["model"]["api_host"],
            get_model_api_key(config),
            config["model"].get("prefer", "auto")
        )

        result["project"] = name
        result["source_path"] = str(project_path.resolve())
        result["manifest_path"] = str(manifest_path.resolve())

        # Validate response
        if not validate_response(result):
            logger.warning(f"⚠️  {name}: Response missing some fields, using defaults")
            # Fill in missing fields with defaults
            for key, default in PROJECT_TEMPLATE.items():
                if key not in result:
                    result[key] = default

        # Save individual JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        return result

    except Exception as e:
        logger.error(f"❌ Error on {name}: {e}")
        return None


def write_markdown_summary(results, output_path):
    """
    Write a human-readable summary of all projects with statistics.
    """
    with open(output_path, "w", encoding="utf-8") as out:
        # Header and statistics
        out.write("# Project Summaries\n\n")
        out.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        out.write(f"**Total Projects:** {len(results)}\n\n")

        # Calculate statistics
        statuses = {}
        complexities = {}
        avg_completion = 0

        for r in results:
            status = r.get('status', 'Unknown')
            statuses[status] = statuses.get(status, 0) + 1

            complexity = r.get('complexity', 'Unknown')
            complexities[complexity] = complexities.get(complexity, 0) + 1

            pct = r.get('percentage_complete', 0)
            if isinstance(pct, (int, float)):
                avg_completion += pct

        avg_completion = avg_completion / len(results) if results else 0

        # Write statistics
        out.write("## Statistics\n\n")
        out.write(f"- **Average Completion:** {avg_completion:.1f}%\n")
        out.write(f"- **Status Distribution:**\n")
        for status, count in sorted(statuses.items()):
            out.write(f"  - {status}: {count}\n")
        out.write(f"- **Complexity Distribution:**\n")
        for complexity, count in sorted(complexities.items()):
            out.write(f"  - {complexity}: {count}\n")
        out.write("\n---\n\n")

        # Individual project summaries
        out.write("## Projects\n\n")
        for r in sorted(results, key=lambda x: x.get('percentage_complete', 0), reverse=True):
            out.write(f"### {r.get('project')}\n\n")
            if r.get("source_path"):
                out.write(f"**Path:** `{r.get('source_path')}`\n\n")
            out.write(f"**Status:** {r.get('status')} | ")
            out.write(f"**Completion:** {r.get('percentage_complete')}% | ")
            out.write(f"**Complexity:** {r.get('complexity')}\n\n")

            if r.get('tags'):
                out.write(f"**Tags:** {', '.join(r.get('tags', []))}\n\n")

            out.write(f"**Summary:** {r.get('summary')}\n\n")

            if r.get("missing_pieces"):
                out.write("**Missing Pieces:**\n")
                for mp in r["missing_pieces"]:
                    out.write(f"- {mp}\n")
                out.write("\n")

            if r.get("next_steps"):
                out.write("**Next Steps:**\n")
                for ns in r["next_steps"]:
                    out.write(f"- {ns}\n")
                out.write("\n")

            if r.get("potential_improvements"):
                out.write("**Potential Improvements:**\n")
                for pi in r["potential_improvements"]:
                    out.write(f"- {pi}\n")
                out.write("\n")

            out.write("---\n\n")


def main():
    # Load configuration
    global CONFIG
    config = load_config()
    CONFIG = config

    out_dir = pathlib.Path(config["project"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Discover all project targets
    project_targets = discover_projects(config)
    total_projects = len(project_targets)

    logger.info(f"🔍 Found {total_projects} project manifests to evaluate")

    # Log model preference/host for clarity
    model_pref = config["model"].get("prefer", "auto")
    model_host = config["model"].get("api_host")
    logger.info(f"🤖 Model: {config['model']['name']} | Host: {model_host} | Prefer: {model_pref}")

    results = []
    max_workers = config["processing"].get("max_workers", 3)
    skip_existing = config["processing"].get("skip_already_processed", True)

    # Process projects concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_project = {
            executor.submit(process_project, target, config, skip_existing): target
            for target in project_targets
        }

        # Process results as they complete
        completed = 0
        for future in as_completed(future_to_project):
            completed += 1
            target = future_to_project[future]

            try:
                result = future.result()
                if result:
                    results.append(result)
                    logger.info(f"✅ [{completed}/{total_projects}] Completed {target.project_name}")
                else:
                    logger.info(f"⚪ [{completed}/{total_projects}] Skipped {target.project_name}")
            except Exception as e:
                logger.error(f"❌ [{completed}/{total_projects}] Failed {target.project_name}: {e}")

            # Rate limiting
            time.sleep(config["processing"]["delay"])

    # Write summary
    if results:
        summary_path = pathlib.Path(
            config["project"].get("summary_path", str(out_dir / "summary.md"))
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown_summary(results, summary_path)
        logger.info(f"\n📘 Wrote {len(results)} project summaries → {summary_path}")
    else:
        logger.warning("⚠️  No summaries generated.")


if __name__ == "__main__":
    main()
