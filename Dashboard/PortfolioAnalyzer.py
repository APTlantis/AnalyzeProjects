#!/usr/bin/env python3
"""
Portfolio Analyzer
------------------
Aggregates per-project JSON summaries (from the Project Summarizer)
into an index, computes statistics, and writes Markdown + JSON output.
"""

import json, pathlib, statistics, shutil, time

RAW = pathlib.Path("Project-Summaries/raw")
COMPILED = pathlib.Path("Project-Summaries")
HISTORY = pathlib.Path("Project-Summaries")

for p in (RAW, COMPILED, HISTORY):
    p.mkdir(parents=True, exist_ok=True)

INDEX_PATH = COMPILED / "index.json"
SUMMARY_PATH = COMPILED / "summary.md"


def load_projects():
    projects = []
    for f in RAW.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                data["_file"] = f.name
                projects.append(data)
        except Exception as e:
            print(f"⚠️ Error reading {f.name}: {e}")
    return projects


def analyze(projects):
    avg = statistics.mean([p.get("percentage_complete", 0) for p in projects]) if projects else 0
    by_status = {}
    tags = {}
    for p in projects:
        st = p.get("status", "Unknown")
        by_status[st] = by_status.get(st, 0) + 1
        for t in p.get("tags", []):
            tags[t] = tags.get(t, 0) + 1
    return avg, by_status, tags


def prioritize(projects):
    # lower completion + higher complexity = higher priority
    def rank(p):
        comp = p.get("percentage_complete", 0)
        miss = len(p.get("missing_pieces", []))
        complexity_weight = {"Trivial": 1, "Simple": 2, "Moderate": 3, "High": 4, "Complex": 5}
        c_score = complexity_weight.get(str(p.get("complexity", "")).capitalize(), 3)
        return (comp, -(miss + c_score))
    return sorted(projects, key=rank)


def write_summary(projects, avg, by_status, tags):
    lines = []
    lines.append("# Portfolio Summary\n")
    lines.append(f"- Total projects: {len(projects)}")
    lines.append(f"- Average completion: {avg:.1f}%")
    lines.append("\n## By Status")
    for k, v in by_status.items():
        lines.append(f"- {k}: {v}")
    lines.append("\n## Top Priorities\n")
    for p in prioritize(projects)[:10]:
        lines.append(f"### {p['project']} ({p.get('percentage_complete', 0)}%)")
        lines.append(f"- Status: {p.get('status')}")
        nxt = p.get("next_steps", [])
        if nxt:
            lines.append(f"- Next: {nxt[0]}")
        lines.append("")
    lines.append("\n## Tags (frequency)\n")
    for t, n in sorted(tags.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"- {t}: {n}")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {SUMMARY_PATH}")


def main():
    projects = load_projects()
    if not projects:
        print("No projects found in Project-Summaries/raw/")
        return
    avg, by_status, tags = analyze(projects)

    data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "average_completion": avg,
        "project_count": len(projects),
        "by_status": by_status,
        "tag_stats": tags,
        "projects": projects,
    }

    # Backup old index
    if INDEX_PATH.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(INDEX_PATH, HISTORY / f"index_{ts}.json")

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Wrote {INDEX_PATH}")

    write_summary(projects, avg, by_status, tags)
    print("Portfolio analysis complete.")


if __name__ == "__main__":
    main()
