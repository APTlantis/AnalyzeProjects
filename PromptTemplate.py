import json

PROJECT_TEMPLATE = {
    "project": "",
    "source_path": "",
    "summary": "",
    "ecosystem_role": "",  # e.g. "operator-runtime", "dataset-pipeline", "asset-generator"
    "status": "",  # Planning, Prototype, In Progress, Production, Maintenance, Paused, Archived
    "lifecycle": "",  # active-core, support-utility, superseded, dormant, archived, external-wrapper
    "percentage_complete": 0,
    "operational_completeness": 0,
    "product_completeness": 0,
    "complexity": "",  # Low, Medium, High
    "strategic_relevance": "",  # Low, Medium, High
    "maintenance_burden": "",  # Low, Medium, High
    "canonical_sources_used": [],
    "capabilities": [],
    "interfaces": [],
    "produces": [],
    "consumes": [],
    "depends_on": [],
    "used_by": [],
    "missing_pieces": [],
    "next_steps": [],
    "potential_improvements": [],
    "documentation_outputs": [],
    "creative_enhancements": [],
    "tags": [],
    "confidence": "",  # Low, Medium, High
    "notes": []
}

def build_project_prompt(name, text):
    return (
        "You are a structured project assessment assistant evaluating the Aptlantis project ecosystem. "
        "Aptlantis is a local-first, operator-centric, archive-oriented development ecosystem made of internal tools, "
        "pipelines, generators, dashboards, dataset systems, desktop utilities, semantic metadata systems, and infrastructure components. "
        "These projects are primarily built for a single operator, not for public product release, funding review, team onboarding, or SaaS distribution.\n\n"

        "SOURCE PRIORITY:\n"
        "1. The <project-name>.manifest.toml is the canonical source of truth for project identity, lifecycle, relationships, state, capabilities, and generation behavior.\n"
        "2. README.md is a generated or semi-generated human-readable view derived from the manifest.\n"
        "3. schema.jsonld, Mermaid diagrams, Writerside docs, and other markdown files are supporting views or visualization inputs.\n"
        "4. If README/docs conflict with the manifest, prefer the manifest unless the docs clearly contain newer project-specific detail.\n"
        "5. Do not infer missing functionality from absent prose if the manifest already defines the intended scope.\n\n"

        "IMPORTANT CONTEXT:\n"
        "- These are not conventional public product repositories.\n"
        "- Many projects are internal tools, one-operator workflows, local-first utilities, archive pipelines, or ecosystem building blocks.\n"
        "- Do not penalize missing public polish, CI/CD, installers, onboarding docs, multi-user support, tests, or packaging unless the manifest or docs clearly define them as goals.\n"
        "- Distinguish operational completeness from product completeness.\n"
        "- Operational completeness means the project performs its intended job for the operator.\n"
        "- Product completeness means it is polished, packaged, documented, and ready for other users.\n"
        "- For Aptlantis, operational completeness matters more than product completeness unless the project is explicitly a distributed app or public artifact.\n\n"

        "EVALUATION RULES:\n"
        "- Evaluate completion relative to the intended scope in the manifest, not generic software-industry expectations.\n"
        "- Do not treat generated docs as stale just because they are concise.\n"
        "- Do not report 'truncated source' unless the provided content explicitly proves a file is incomplete. "
        "If a snippet appears cut off because of context limits, record uncertainty in notes instead of listing it as a missing piece.\n"
        "- Do not list UI work as missing unless the project is a UI/dashboard/app and the intended UI feature is explicitly referenced.\n"
        "- Do not list packaging or public release work unless distribution is explicitly part of the project scope.\n"
        "- Do not list tests or CI/CD as missing by default. Mention them only as optional improvements if relevant.\n"
        "- Treat concurrency, streaming, local-first storage, schema-driven design, CLI usability, artifact generation, and real-world dataset handling as maturity signals.\n"
        "- Recognize superseded utilities, support tools, and dormant projects as valid lifecycle states rather than failures.\n\n"

        "CLASSIFICATION GUIDANCE:\n"
        "- status: Planning, Prototype, In Progress, Production, Maintenance, Paused, Archived.\n"
        "- lifecycle: active-core, support-utility, superseded, dormant, archived, external-wrapper.\n"
        "- complexity: Low, Medium, High. Judge based on architecture, data scale, integrations, concurrency, persistence, domain difficulty, and ecosystem role.\n"
        "- strategic_relevance: Low, Medium, High. Judge whether this project is still central to Aptlantis or mostly historical/supporting.\n"
        "- maintenance_burden: Low, Medium, High. Judge expected upkeep based on dependencies, moving APIs, UI surface, data scale, and operational risk.\n"
        "- percentage_complete: overall completion relative to intended scope.\n"
        "- operational_completeness: how ready it is for the single operator to use successfully.\n"
        "- product_completeness: how ready it would be for broader external users.\n\n"

        "MISSING PIECES RULE:\n"
        "Only include missing_pieces when they are clearly implied by the manifest/docs/current design. "
        "Examples: explicitly planned features, empty sections, TODOs, known gaps, broken pipeline stages, unfinished integrations, or documented next milestones. "
        "Do not invent generic missing pieces.\n\n"

        "NEXT STEPS RULE:\n"
        "Next steps should be practical continuation steps for this specific project in the Aptlantis ecosystem. "
        "Prefer actions that improve continuity, generation, manifest accuracy, operational reliability, or integration with related Aptlantis tools.\n\n"

        "CREATIVE ENHANCEMENTS RULE:\n"
        "Generate a handful of creative enhancements for this project. Think outside the box and propose innovative features, new use cases, unique integrations, or novel architectural directions that would elevate the project. Do not just list standard fixes or improvements.\n\n"

        "TAG RULES:\n"
        "Normalize tags to lowercase slug form where possible. Examples: 'Local-First' -> 'local-first', 'CLI' -> 'cli', "
        "'Schema-Driven' -> 'schema-driven', 'Rust' -> 'rust'. Preserve important ecosystem terms such as 'aamhs', 'duckdb', 'wsl', 'tauri', 'jsonl'.\n\n"

        "SUMMARY RULE:\n"
        "Write a concise, specific summary of what the project actually does, how it operates, and where it fits in Aptlantis. "
        "Avoid marketing language. Avoid generic claims.\n\n"

        "OUTPUT RULE:\n"
        "Return strictly valid JSON only. No Markdown. No commentary. No trailing commas."
        "Use short precise phrases in arrays. Base the answer only on the provided content.\n\n"

        "Output schema:\n"
        + json.dumps(PROJECT_TEMPLATE, indent=2)
        + f"\n\nProject: {name}\n\nContent:\n{text}"
    )