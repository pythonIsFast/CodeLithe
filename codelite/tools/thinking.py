# Copyright 2026 Code Lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tools to control the current run's reasoning effort."""

from functools import partial
from typing import Any

from .base import Tool, ToolError, object_schema
from .context import ToolContext


def _control(action: str, _args: dict[str, Any], context: ToolContext) -> str:
    if context.thinking_control is None:
        raise ToolError("Thinking controls require an active agent run.")
    return context.thinking_control(action)


LOW_THINKING = Tool(
    name="low_thinking",
    description="Use a lower supported thinking effort for the next model request and subsequent "
    "requests in this run. Use it when carrying out an already decided sequence of edits.",
    parameters=object_schema({}),
    run=partial(_control, "low"),
)

NORMAL_THINKING = Tool(
    name="normal_thinking",
    description="Restore the run's normal thinking effort for the next model request. Use it "
    "before planning, reasoning about a failure, or reviewing the changes.",
    parameters=object_schema({}),
    run=partial(_control, "normal"),
)

GET_THINKING_MODE = Tool(
    name="get_thinking-mode",
    description="Read the thinking mode, next effort, and normal effort without changing anything.",
    parameters=object_schema({}),
    run=partial(_control, "get"),
)
