from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from app.schemas import TaskConfig, TopicConfig


def load_task_files(paths: Iterable[Path]) -> list[TaskConfig]:
    tasks: list[TaskConfig] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tasks.append(TaskConfig.model_validate(data))
    return tasks


def load_topic_files(paths: Iterable[Path]) -> list[TopicConfig]:
    topics: list[TopicConfig] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        topics.append(TopicConfig.model_validate(data))
    return topics
