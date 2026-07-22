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
import hashlib
import logging
import os
import pathlib
import re
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional

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
GOVERNING_STANDARDS: Dict[str, str] = {}

EXPECTED_GROUP_COUNTS = {
    "DRS": 14,
    "CTS": 9,
    "WDS": 2,
    "STANDARDS": 8,
}


@dataclass(frozen=True)
class ProjectTarget:
    """A project explicitly listed in ProjectIndex.md."""
    project_name: str
    project_path: pathlib.Path
    project_group: str
    manifest_path: Optional[pathlib.Path] = None
    repository_url: str = ""
    governing_standard_path: Optional[pathlib.Path] = None


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
    identity_path = manifest_path or project_path
    digest = hashlib.sha1(str(identity_path.resolve()).encode("utf-8")).hexdigest()[:12]
    manifest_stem = manifest_path.name if manifest_path else project_path.name
    raw = f"{project_name}__{manifest_stem}__{digest}"
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


def select_canonical_manifest(project_path: pathlib.Path) -> Optional[pathlib.Path]:
    """Prefer <folder>.manifest.toml, otherwise use one unambiguous direct manifest."""
    preferred = project_path / f"{project_path.name}.manifest.toml"
    if preferred.is_file():
        return preferred.resolve()

    candidates = sorted(
        (path for path in project_path.glob("*manifest.toml") if not is_manifest_noise(path)),
        key=lambda path: path.name.lower(),
    )
    if len(candidates) == 1:
        return candidates[0].resolve()
    if len(candidates) > 1:
        logger.warning(
            f"⚠️  {project_path} has multiple manifests and no folder-named manifest; continuing without a canonical manifest"
        )
    return None


def parse_project_index(index_path: pathlib.Path, standards: Dict[str, str]) -> List[ProjectTarget]:
    """Parse and strictly validate the authoritative Markdown project index."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Project index not found: {index_path}")

    heading_re = re.compile(r"^#{1,6}\s+(?:\d+\.\s*)?(DRS|CTS|WDS|STANDARDS)\s*$", re.IGNORECASE)
    entry_re = re.compile(r"^-\s+(.+?)(?:\s+-\s+`([^`]+)`)?\s*$")
    current_group = None
    targets: List[ProjectTarget] = []
    seen_paths = set()
    errors = []

    for line_number, raw_line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        heading = heading_re.match(line)
        if heading:
            current_group = heading.group(1).upper()
            continue
        if not line.startswith("-"):
            continue
        if not current_group:
            errors.append(f"line {line_number}: project entry appears before a recognized group heading")
            continue
        entry = entry_re.match(line)
        if not entry:
            errors.append(f"line {line_number}: malformed project entry")
            continue

        raw_path, repository_url = entry.groups()
        project_path = pathlib.Path(raw_path.strip()).resolve()
        normalized = normalize_path(project_path)
        if normalized in seen_paths:
            errors.append(f"line {line_number}: duplicate project path {project_path}")
            continue
        seen_paths.add(normalized)
        if not project_path.is_dir():
            errors.append(f"line {line_number}: project directory does not exist: {project_path}")
            continue

        standard_path = None
        if current_group != "STANDARDS":
            configured_standard = standards.get(current_group)
            if not configured_standard:
                errors.append(f"line {line_number}: no governing standard configured for {current_group}")
                continue
            standard_path = pathlib.Path(configured_standard).resolve()
            if not standard_path.is_file():
                errors.append(f"line {line_number}: governing standard does not exist: {standard_path}")
                continue

        manifest_path = select_canonical_manifest(project_path)
        project_name = manifest_project_name(manifest_path) if manifest_path else project_path.name
        targets.append(ProjectTarget(
            project_name=project_name,
            project_path=project_path,
            project_group=current_group,
            manifest_path=manifest_path,
            repository_url=repository_url or "",
            governing_standard_path=standard_path,
        ))

    actual_counts = {group: 0 for group in EXPECTED_GROUP_COUNTS}
    for target in targets:
        actual_counts[target.project_group] += 1

    expected_total = sum(EXPECTED_GROUP_COUNTS.values())

    if actual_counts != EXPECTED_GROUP_COUNTS:
        errors.append(f"expected group counts {EXPECTED_GROUP_COUNTS}, found {actual_counts}")
    if len(targets) != expected_total:
        errors.append(f"expected {expected_total} unique projects, found {len(targets)}")
    if errors:
        raise ValueError("Invalid ProjectIndex.md:\n- " + "\n- ".join(errors))
    return targets


def discover_projects(config: Dict) -> List[ProjectTarget]:
    """Load exactly the projects named by ProjectIndex.md."""
    index_path = pathlib.Path(config["project"]["index_path"]).resolve()
    return parse_project_index(index_path, config.get("standards", {}))


def load_governing_standards(config: Dict) -> Dict[str, str]:
    """Read each configured standard once per run."""
    loaded = {}
    for group, path_text in config.get("standards", {}).items():
        path = pathlib.Path(path_text).resolve()
        loaded[group.upper()] = path.read_text(encoding="utf-8")
    return loaded


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
                excerpt = text[:max_total_chars]
                state = "complete file" if len(excerpt) == len(text) else f"sampled excerpt: first {len(excerpt):,} of {len(text):,} characters"
                snippet = f"## {rel_path.as_posix()} (canonical project manifest; {state})\n{excerpt}"
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
                        excerpt = text[:max_chars]
                        state = "complete file" if len(excerpt) == len(text) else f"sampled excerpt: first {len(excerpt):,} of {len(text):,} characters"
                        snippet = f"## {rel_path.as_posix()} ({state})\n{excerpt}"
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


def normalize_transfer_artifact_claims(response: Dict) -> Dict:
    """Move unsupported source-truncation claims out of actionable findings."""
    claim_re = re.compile(
        r"\b(truncat(?:ed|ion)?|cut[ -]?off|context (?:limit|window)|transfer artifact)\b",
        re.IGNORECASE,
    )
    removed = []
    for field in ("missing_pieces", "next_steps", "potential_improvements"):
        values = response.get(field, [])
        if not isinstance(values, list):
            continue
        kept = []
        for value in values:
            if isinstance(value, str) and claim_re.search(value):
                removed.append(value)
            else:
                kept.append(value)
        response[field] = kept
    if removed:
        notes = response.get("notes")
        if not isinstance(notes, list):
            notes = []
        note = "Source-truncation claims were suppressed because sampled or transferred excerpts are not evidence of repository file damage."
        if note not in notes:
            notes.append(note)
        response["notes"] = notes
    return response


def get_model_api_key(config: Dict) -> str | None:
    """Resolve OpenAI credentials exclusively from the process environment."""
    return os.getenv("OPENAI_API_KEY")


def validate_runtime_config(config: Dict) -> None:
    """Fail before scanning when required model credentials are unavailable."""
    if config["model"].get("prefer", "auto").lower() == "openai" and not get_model_api_key(config):
        raise RuntimeError("OPENAI_API_KEY is not set in the process environment.")


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
            project_group="",
            manifest_path=select_canonical_manifest(project_path),
        )

    name = target.project_name
    project_path = target.project_path
    manifest_path = target.manifest_path
    out_dir = pathlib.Path(config["project"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    output_stem = make_output_stem(name, project_path, manifest_path)
    json_path = out_dir / f"{output_stem}.json"

    # Skip if already processed recently
    if skip_existing and json_path.exists() and not should_reprocess(json_path):
        logger.info(f"⏭️  Skipping {name} (already processed)")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
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
    prompt = build_project_prompt(
        name,
        text,
        target.project_group,
        GOVERNING_STANDARDS.get(target.project_group, ""),
        str(target.governing_standard_path) if target.governing_standard_path else "",
    )

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
        result["manifest_path"] = str(manifest_path.resolve()) if manifest_path else ""
        result["project_group"] = target.project_group
        result["governing_standard"] = str(target.governing_standard_path) if target.governing_standard_path else ""
        result["repository_url"] = target.repository_url
        result["sampling"] = {
            "sampled_files": file_count,
            "sampled_characters": total_chars,
            "max_files": config["processing"].get("max_files_per_project", 60),
            "max_characters": config["processing"].get("max_total_chars", 90000),
            "content_is_sampled": True,
        }
        normalize_transfer_artifact_claims(result)

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
    global CONFIG, GOVERNING_STANDARDS
    config = load_config()
    CONFIG = config
    validate_runtime_config(config)
    GOVERNING_STANDARDS = load_governing_standards(config)

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
