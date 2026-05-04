from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "manual.html"

ROOT_MARKDOWN = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "DESIGN.md",
    ROOT / "task-memory-hub-설계명세.md",
]

SOURCE_FILES = [
    ROOT / "pyproject.toml",
    ROOT / "task_memory_hub" / "service.py",
    ROOT / "task_memory_hub" / "cli.py",
    ROOT / "task_memory_hub" / "api.py",
    ROOT / "task_memory_hub" / "mcp_server.py",
    ROOT / "task_memory_hub" / "runner.py",
    ROOT / "task_memory_hub" / "governance.py",
    ROOT / "task_memory_hub" / "orchestrator.py",
    ROOT / "task_memory_hub" / "registry.py",
    ROOT / "task_memory_hub" / "store.py",
    ROOT / "task_memory_hub" / "worker.py",
    ROOT / "task_memory_hub" / "notification_adapters.py",
    ROOT / "task_memory_hub" / "static" / "app.js",
    ROOT / "task_memory_hub" / "static" / "task-detail.js",
    ROOT / "task_memory_hub" / "static" / "app.css",
    ROOT / "scripts" / "ci-smoke.ps1",
    ROOT / "scripts" / "test-cline-mcp-pilot.ps1",
    ROOT / "scripts" / "tmh-deepagents-live-smoke.py",
    ROOT / "scripts" / "build-docs-manual.py",
]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_-]+", "-", value.strip()).strip("-").lower()
    return normalized or "section"


def inline_markdown(value: str) -> str:
    text = escape(value)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        text,
    )
    return text


def markdown_to_html(markdown: str, prefix: str) -> tuple[str, list[tuple[int, str, str]]]:
    html: list[str] = []
    headings: list[tuple[int, str, str]] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_list = False
    table_lines: list[str] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    def flush_table() -> None:
        if table_lines:
            html.append('<pre class="table-block"><code>' + escape("\n".join(table_lines)) + "</code></pre>")
            table_lines.clear()

    def close_code() -> None:
        nonlocal in_code, code_lang, code_lines
        if in_code:
            lang_class = f" language-{escape(code_lang)}" if code_lang else ""
            html.append(f'<pre class="code-block"><code class="{lang_class}">' + escape("\n".join(code_lines)) + "</code></pre>")
            in_code = False
            code_lang = ""
            code_lines = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("```"):
            if in_code:
                close_code()
            else:
                close_list()
                flush_table()
                in_code = True
                code_lang = line.strip("`").strip()
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            close_list()
            flush_table()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            anchor = f"{prefix}-{slug(title)}"
            headings.append((level, title, anchor))
            html.append(f'<h{level} id="{anchor}">{inline_markdown(title)}</h{level}>')
            continue

        if line.startswith("|"):
            close_list()
            table_lines.append(line)
            continue
        flush_table()

        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet:
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{inline_markdown(bullet.group(1))}</li>")
            continue

        numbered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if numbered:
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{inline_markdown(numbered.group(1))}</li>")
            continue

        if not line.strip():
            close_list()
            html.append("")
            continue

        close_list()
        html.append(f"<p>{inline_markdown(line)}</p>")

    close_code()
    close_list()
    flush_table()
    return "\n".join(html), headings


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def first_paragraph(markdown: str) -> str:
    for block in re.split(r"\n\s*\n", markdown):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            continue
        return re.sub(r"\s+", " ", stripped)[:240]
    return ""


def render_doc_section(path: Path, group: str) -> tuple[str, dict[str, object]]:
    content = read_text(path)
    section_id = slug(rel(path))
    rendered, headings = markdown_to_html(content, section_id)
    title = headings[0][1] if headings else rel(path)
    summary = first_paragraph(content)
    section = f"""
    <section class="doc-section" id="{section_id}">
      <div class="section-kicker">{escape(group)} / {escape(rel(path))}</div>
      <h2>{escape(title)}</h2>
      <p class="summary">{inline_markdown(summary) if summary else "No summary paragraph detected."}</p>
      <div class="markdown-body">
        {rendered}
      </div>
    </section>
    """
    return section, {"id": section_id, "title": title, "path": rel(path), "headings": headings}


def render_source_section(path: Path) -> tuple[str, dict[str, object]]:
    content = read_text(path)
    section_id = slug(rel(path))
    lines = content.count("\n") + 1
    section = f"""
    <section class="doc-section source-section" id="{section_id}">
      <div class="section-kicker">source / {escape(rel(path))}</div>
      <h2>{escape(rel(path))}</h2>
      <p class="summary">Source snapshot included for implementation traceability. Lines: {lines}.</p>
      <details>
        <summary>Open source snapshot</summary>
        <pre class="code-block"><code>{escape(content)}</code></pre>
      </details>
    </section>
    """
    return section, {"id": section_id, "title": rel(path), "path": rel(path), "headings": []}


def render_toc(entries: list[dict[str, object]]) -> str:
    items: list[str] = []
    for entry in entries:
        items.append(f'<li><a href="#{entry["id"]}">{escape(str(entry["title"]))}</a><span>{escape(str(entry["path"]))}</span>')
        headings = [item for item in entry["headings"] if item[0] in {2, 3}]
        if headings:
            items.append("<ul>")
            for _level, title, anchor in headings[:12]:
                items.append(f'<li><a href="#{anchor}">{escape(title)}</a></li>')
            items.append("</ul>")
        items.append("</li>")
    return "\n".join(items)


def main() -> int:
    markdown_docs = [path for path in ROOT_MARKDOWN if path.exists()]
    markdown_docs.extend(
        path
        for path in sorted((ROOT / "docs").glob("*.md"))
        if path.name not in {"manual.html"} and path.exists()
    )
    sections: list[str] = []
    entries: list[dict[str, object]] = []
    for path in markdown_docs:
        section, entry = render_doc_section(path, "manual" if path.parent == ROOT else "docs")
        sections.append(section)
        entries.append(entry)
    for path in SOURCE_FILES:
        if not path.exists():
            continue
        section, entry = render_source_section(path)
        sections.append(section)
        entries.append(entry)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Task Memory Hub Integrated Manual</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --ink: #142033;
      --muted: #5f6b7a;
      --line: #d8e0ea;
      --accent: #087f76;
      --code: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.55;
    }}
    header {{
      padding: 28px 32px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    header h1 {{ margin: 0 0 8px; font-size: 28px; }}
    header p {{ margin: 0; color: var(--muted); }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
      gap: 20px;
      padding: 20px;
    }}
    nav {{
      position: sticky;
      top: 20px;
      align-self: start;
      max-height: calc(100vh - 40px);
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    nav h2 {{ margin-top: 0; font-size: 16px; }}
    nav ul {{ list-style: none; padding-left: 0; margin: 0; }}
    nav ul ul {{ padding-left: 14px; margin: 4px 0 8px; border-left: 2px solid var(--line); }}
    nav li {{ margin: 6px 0; }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 650; }}
    nav span {{ display: block; color: var(--muted); font-size: 12px; }}
    main {{ min-width: 0; }}
    .doc-section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin-bottom: 18px;
    }}
    .section-kicker {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    h2 {{ margin: 6px 0 8px; font-size: 22px; }}
    h3 {{ margin-top: 22px; }}
    .summary {{ color: var(--muted); border-left: 3px solid var(--accent); padding-left: 10px; }}
    code {{ background: #eef3f8; border-radius: 4px; padding: 1px 4px; }}
    .code-block, .table-block {{
      overflow: auto;
      background: var(--code);
      color: #e5e7eb;
      border-radius: 6px;
      padding: 12px;
      line-height: 1.45;
      font-size: 12px;
    }}
    details summary {{ cursor: pointer; color: var(--accent); font-weight: 700; }}
    a {{ color: var(--accent); }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; padding: 12px; }}
      nav {{ position: static; max-height: none; }}
      header {{ padding: 20px 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Task Memory Hub Integrated Manual</h1>
    <p>Generated at {generated_at}. This file combines root docs, docs/*.md, and selected implementation snapshots into one navigable HTML manual.</p>
  </header>
  <div class="layout">
    <nav>
      <h2>목차</h2>
      <ul>
        {render_toc(entries)}
      </ul>
    </nav>
    <main>
      {''.join(sections)}
    </main>
  </div>
</body>
</html>
"""
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
