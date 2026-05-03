from __future__ import annotations

import os


APP_NAME = os.environ.get("TASK_MEMORY_HUB_APP_NAME", "Task Memory Hub")
APP_SLUG = os.environ.get("TASK_MEMORY_HUB_APP_SLUG", "task-memory-hub")
APP_SHORT_NAME = os.environ.get("TASK_MEMORY_HUB_SHORT_NAME", "TMH")
CLI_PROG = os.environ.get("TASK_MEMORY_HUB_CLI_PROG", "tmh")
TASK_ID_PREFIX = os.environ.get("TASK_MEMORY_HUB_TASK_ID_PREFIX", "tmh")
ENV_PREFIX = "TASK_MEMORY_HUB"
