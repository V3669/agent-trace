from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from agenttrace.models import WasteReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html_report(report: WasteReport, output_path: Path) -> None:
    env = Environment(
        loader=PackageLoader("agenttrace.report", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(report=report)
    output_path.write_text(html, encoding="utf-8")
