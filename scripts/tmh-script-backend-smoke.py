from __future__ import annotations

import json
import os


def main() -> int:
    task_id = os.environ.get("TASK_MEMORY_HUB_TASK_ID", "")
    command_ref = os.environ.get("TASK_MEMORY_HUB_COMMAND_REF", "")
    task_json = os.environ.get("TASK_MEMORY_HUB_TASK_JSON", "{}")
    task = json.loads(task_json)
    print(
        json.dumps(
            {
                "backend": "script_ref_smoke",
                "command_ref": command_ref,
                "task_id": task_id,
                "title": task.get("title", ""),
                "succeeded": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
