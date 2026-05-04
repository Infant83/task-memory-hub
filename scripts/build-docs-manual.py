from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "manual.html"

KOREAN_TITLES = {
    "README.md": "제품 개요와 빠른 시작",
    "AGENTS.md": "에이전트 작업 규칙",
    "DESIGN.md": "웹 UI 설계 기준",
    "task-memory-hub-설계명세.md": "원 설계명세",
    "docs/agentic-workspace-control-plane.md": "에이전트 작업공간 제어면",
    "docs/ci-necessity-review.md": "CI 필요성 검토",
    "docs/cline-mcp-onprem-pilot-checklist.md": "Cline MCP 온프렘 파일럿",
    "docs/deepagents-backend-pilot.md": "Deepagents 백엔드 파일럿",
    "docs/external-adapter-plan.md": "외부 연동 어댑터 계획",
    "docs/harness-runner-governance-development-spec.md": "하니스 러너 거버넌스 개발 명세",
    "docs/postgres-slow-track.md": "PostgreSQL 전환 준비",
    "docs/public-progress.md": "공개 진행 요약",
    "docs/review-gate-flow.md": "Review Gate와 전달 드라이런",
    "docs/task-execution-contract-standard.md": "작업 실행 계약 표준",
    "docs/task-title-standard.md": "작업 제목 작성 표준",
    "docs/verification-manual.md": "검증 명령 매뉴얼",
    "docs/web-ui-screen-guide.md": "Web UI 화면 설명서",
    "docs/windows-install-standard.md": "Windows 설치 표준",
}

KOREAN_SUMMARIES = {
    "README.md": "TMH의 현재 기능, 설치, 빠른 시작, 구조, 로드맵을 설명하는 제품 진입 문서다.",
    "AGENTS.md": "이 저장소에서 Codex/Cline류 에이전트가 따라야 하는 source of truth, 구현 우선순위, 검증 기준, 금지사항을 정의한다.",
    "DESIGN.md": "작업자가 매일 사용하는 제어면으로서 Web UI가 가져야 할 정보 밀도, 가시성, 통제성을 정리한다.",
    "task-memory-hub-설계명세.md": "Windows 로컬 우선 task/alarm/agent hub의 원래 설계 의도와 핵심 요구사항을 담고 있다.",
    "docs/external-adapter-plan.md": "OpenProject, Outlook 이메일, Teams, Webhook 연동을 어떤 순서와 안전장치로 붙일지 정리한다.",
}

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


def display_title(path: Path, headings: list[tuple[int, str, str]]) -> str:
    return KOREAN_TITLES.get(rel(path), headings[0][1] if headings else rel(path))


def display_summary(path: Path, markdown: str) -> str:
    return KOREAN_SUMMARIES.get(rel(path), first_paragraph(markdown))


def render_doc_section(path: Path, group: str) -> tuple[str, dict[str, object]]:
    content = read_text(path)
    section_id = slug(rel(path))
    rendered, headings = markdown_to_html(content, section_id)
    title = display_title(path, headings)
    summary = display_summary(path, content)
    original_title = headings[0][1] if headings else rel(path)
    section = f"""
    <section class="doc-section" id="{section_id}">
      <div class="section-kicker">{escape(group)} / {escape(rel(path))}</div>
      <h2>{escape(title)}</h2>
      <p class="summary">{inline_markdown(summary) if summary else "요약 문단이 없습니다."}</p>
      <p class="source-note">원문 제목: {escape(original_title)}. 아래 내용은 추적성을 위해 원문 구조를 보존합니다.</p>
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
      <div class="section-kicker">구현 파일 / {escape(rel(path))}</div>
      <h2>{escape(rel(path))}</h2>
      <p class="summary">구현 추적을 위해 선택한 파일 내용을 접어둘 수 있는 스냅샷으로 포함했다. 줄 수: {lines}.</p>
      <details>
        <summary>구현 파일 내용 열기</summary>
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


def render_overview(generated_at: str) -> str:
    return f"""
    <section class="doc-section overview" id="manual-overview">
      <div class="section-kicker">운영 요약</div>
      <h2>TMH 통합 매뉴얼 사용법</h2>
      <p class="summary">이 HTML은 TMH의 운영 문서, 설계 문서, 검증 명령, 주요 구현 파일을 한 화면에서 탐색하기 위한 통합 매뉴얼이다. 생성 시각: {escape(generated_at)}.</p>
      <div class="callout-grid">
        <div>
          <h3>현재 제품 단계</h3>
          <p>P0-P5 로컬 우선 파일럿 단계다. Task DB와 task event log가 런타임 source of truth이고, CLI/API/MCP/Web UI는 같은 DB를 조작하는 표면이다.</p>
        </div>
        <div>
          <h3>외부 연동 원칙</h3>
          <p>OpenProject와 Outlook 이메일은 review gate와 delivery dry-run을 통과한 뒤 pilot한다. Teams는 보류한다. Webhook은 raw URL 저장 없이 endpoint reference와 auth profile reference 기반으로 설계한다.</p>
        </div>
        <div>
          <h3>검증 기준</h3>
          <p>변경 후에는 compile, JS syntax, CI smoke, MCP smoke, P5 delivery dry-run, 필요한 경우 live Deepagents smoke를 수행한다. 구체 명령은 검증 명령 매뉴얼 섹션을 따른다.</p>
        </div>
      </div>
    </section>
    """


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
  <link rel="icon" href="data:,">
  <title>Task Memory Hub 통합 매뉴얼</title>
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
    .source-note {{ color: var(--muted); font-size: 13px; }}
    .callout-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .callout-grid > div {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #f9fbfd;
    }}
    .callout-grid h3 {{ margin: 0 0 8px; font-size: 16px; }}
    code {{ background: #eef3f8; border-radius: 4px; padding: 1px 4px; }}
    .code-block, .table-block {{
      overflow: auto;
      background: var(--code);
      color: #f8fafc;
      border-radius: 6px;
      padding: 12px;
      line-height: 1.45;
      font-size: 13px;
      white-space: pre;
      border: 1px solid #1f2937;
    }}
    .code-block code,
    .table-block code {{
      background: transparent;
      color: inherit;
      padding: 0;
      border-radius: 0;
      font: inherit;
      white-space: pre;
    }}
    details summary {{ cursor: pointer; color: var(--accent); font-weight: 700; }}
    a {{ color: var(--accent); }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; padding: 12px; }}
      .callout-grid {{ grid-template-columns: 1fr; }}
      nav {{ position: static; max-height: none; }}
      header {{ padding: 20px 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Task Memory Hub 통합 매뉴얼</h1>
    <p>생성 시각: {generated_at}. 루트 문서, docs/*.md, 주요 구현 파일을 한글 중심 탐색 구조로 묶은 운영 매뉴얼입니다.</p>
  </header>
  <div class="layout">
    <nav>
      <h2>목차</h2>
      <ul>
        <li><a href="#manual-overview">TMH 통합 매뉴얼 사용법</a><span>운영 요약</span></li>
        {render_toc(entries)}
      </ul>
    </nav>
    <main>
      {render_overview(generated_at)}
      {''.join(sections)}
    </main>
  </div>
</body>
</html>
"""
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    OUTPUT.write_text(html, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
