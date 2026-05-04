from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import metadata
from typing import Any


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def last_message_text(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    message = messages[-1]
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live TMH Deepagents API smoke test")
    parser.add_argument(
        "--model",
        default=os.environ.get("TMH_DEEPAGENTS_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
    )
    parser.add_argument("--prompt", default="TMH를 한 문장으로 설명하고, source of truth가 무엇인지 답해줘.")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print(json.dumps({"ok": False, "error": "OPENAI_API_KEY is not set"}, ensure_ascii=False))
        return 2

    try:
        from deepagents import create_deep_agent
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"missing optional deepagents/langchain packages: {exc}",
                    "packages": {
                        "deepagents": package_version("deepagents"),
                        "langchain": package_version("langchain"),
                        "langchain-openai": package_version("langchain-openai"),
                        "langgraph": package_version("langgraph"),
                    },
                },
                ensure_ascii=False,
            )
        )
        return 2

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    model = ChatOpenAI(
        model=args.model,
        base_url=base_url,
        temperature=0,
        timeout=args.timeout,
        max_retries=1,
    )

    def tmh_capability_summary() -> str:
        """Return a short local summary of TMH surfaces for the smoke agent."""
        return (
            "TMH surfaces: CLI tmh, loopback REST/Web UI tmh-web, STDIO MCP tmh-mcp, "
            "worker/outbox, registry-aware global hub, review gate, and dry-run delivery control. "
            "Runtime source of truth: the durable task database and task event log."
        )

    agent = create_deep_agent(
        model=model,
        tools=[tmh_capability_summary],
        system_prompt=(
            "You are a tiny live smoke-test agent for Task Memory Hub. "
            "Use the tool once if it helps. Answer concisely. "
            "For runtime state, the source of truth is the task database and task event log. "
            "Do not reveal secrets."
        ),
    )
    result = agent.invoke({"messages": [{"role": "user", "content": args.prompt}]})
    answer = last_message_text(result)
    print(
        json.dumps(
            {
                "ok": True,
                "model": args.model,
                "base_url_present": bool(base_url),
                "packages": {
                    "deepagents": package_version("deepagents"),
                    "langchain": package_version("langchain"),
                    "langchain-openai": package_version("langchain-openai"),
                    "langgraph": package_version("langgraph"),
                },
                "answer_preview": answer[:500],
                "message_count": len(result.get("messages") or []),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
