#!/usr/bin/env python3
"""
Portfolio Dashboard
-------------------
Simple Flask dashboard to browse all project summaries visually.
"""

from flask import Flask, render_template_string
import json, pathlib

app = Flask(__name__)
INDEX_PATH = pathlib.Path(__file__).resolve().parent / "Project-Summaries/index.json"

@app.route("/")
def index():
    if not INDEX_PATH.exists():
        return "<h3>No index.json found. Run PortfolioAnalyzer.py first.</h3>"
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    projects = sorted(data["projects"], key=lambda p: p.get("percentage_complete", 0))
    avg = round(data["average_completion"], 1)
    project_count = len(projects)

    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Project Dashboard</title>
      <style>
        body { background:#0a0a0a; color:#ccc; font-family:Consolas, monospace; margin:40px; }
        h1 { color:#6cf; }
        .project { border-bottom:1px solid #333; padding:8px 0; }
        .bar { height:6px; background:#6cf; margin-top:3px; }
        .next { color:#999; font-size:0.9em; }
        a { color:#9cf; text-decoration:none; }
        a:hover { text-decoration:underline; }
      </style>
    </head>
    <body>
      <h1>Project Dashboard</h1>
      <p>Average completion: {{avg}}% &nbsp;|&nbsp; Total projects: {{project_count}}</p>
      {% for p in projects %}
        <div class="project">
          <b>{{p['project']}}</b> — {{p.get('status','?')}} ({{p.get('percentage_complete',0)}}%)
          <span class="group">[{{p.get('project_group','Unclassified')}}]</span>
          <div class="bar" style="width:{{p.get('percentage_complete',0)}}%;"></div>
          <div class="next">Next: {{p.get('next_steps',[None])[0] or 'n/a'}}</div>
          {% if p.get('governing_standard') %}<div class="standard">Standard: {{p.get('governing_standard')}}</div>{% endif %}
          <div class="tags">{{", ".join(p.get('tags', [])[:8])}}</div>
        </div>
      {% endfor %}
    </body>
    </html>
    """
    return render_template_string(html, projects=projects, avg=avg, project_count=project_count)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
