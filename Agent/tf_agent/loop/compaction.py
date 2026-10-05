"""Keep re-sent transcripts bounded by eliding old tool outputs (Claude calls are stateless, D-31)."""
from __future__ import annotations

import json

from tf_agent.models.types import Message, TextPart

ELIDED = "[elided to save context]"


def transcript_chars(messages: list[Message]) -> int:
    return sum(len(m.text()) + sum(len(json.dumps(c.arguments)) for c in m.tool_calls) for m in messages)


def compact_transcript(messages: list[Message], max_chars: int, keep_last: int = 6) -> list[Message]:
    out = list(messages)  # always a copy: a request must never alias the live, growing transcript
    total = transcript_chars(out)
    if total <= max_chars:
        return out
    limit = max(0, len(out) - keep_last)
    for i in range(limit):
        if total <= max_chars:
            break
        m = out[i]
        if m.role == "tool" and m.text() != ELIDED:
            total -= len(m.text()) - len(ELIDED)
            out[i] = Message(role="tool", parts=[TextPart(ELIDED)], tool_call_id=m.tool_call_id,
                             tool_name=m.tool_name)
    return out
