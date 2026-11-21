from __future__ import annotations
import json
from typing import Protocol, runtime_checkable
from .prompts import SYSTEM_PROMPT, build_user_prompt

@runtime_checkable
class ClaudePort(Protocol):
    async def summarize(self, chat_title: str, date: str,
                        messages: list[dict]) -> tuple[dict, int, int]: ...

class FakeClaude:
    def __init__(self, response: dict): self._r = response
    async def summarize(self, chat_title, date, messages):
        payload = build_user_prompt(chat_title, date, messages)
        return self._r, len(payload), len(json.dumps(self._r))

class AnthropicClaude:
    def __init__(self, api_key: str, model: str):
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def summarize(self, chat_title, date, messages):
        prompt = build_user_prompt(chat_title, date, messages)
        resp = await self._client.messages.create(
            model=self._model, max_tokens=2000, system=SYSTEM_PROMPT,
            messages=[{"role":"user","content": prompt + "\n\nReturn ONLY the JSON object."}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{"); end = text.rfind("}")
            empty = {"topics":[], "decisions":[], "action_items":[],
                     "open_questions":[], "links":[], "participants":[]}
            try:
                data = json.loads(text[start:end+1]) if start >= 0 and end > start else empty
            except json.JSONDecodeError:
                data = empty
        u = resp.usage
        return data, int(u.input_tokens), int(u.output_tokens)
