from __future__ import annotations

import argparse
import hashlib
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic TMH Deepagents backend smoke stub")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.exit_code:
        print("TMH deepagents_cli backend contract smoke forced failure.", file=sys.stderr)
        return args.exit_code
    prompt = args.prompt or ""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    print(
        json.dumps(
            {
                "backend": "deepagents_cli_smoke",
                "succeeded": True,
                "prompt_sha256_12": digest,
                "summary": "TMH deepagents_cli backend contract smoke completed.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
