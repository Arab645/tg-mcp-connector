from __future__ import annotations
import json
from typing import Protocol, runtime_checkable
from .prompts import SYSTEM_PROMPT, build_user_prompt

@runtime_checkable
class ClaudePort(Protocol):
    async def summarize(self, chat_title: str, date: str,
                        messages: list[dict]) -> tuple[dict, int, int]: ...
    async def generate_digest(self, date: str, user_prompt: str) -> tuple[str, int, int]: ...

class FakeClaude:
    def __init__(self, response: dict, digest_response: str = ""):
        self._r = response
        self._digest = digest_response
    async def generate_digest(self, date, user_prompt):
        return self._digest, len(user_prompt), len(self._digest)
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
            try:
                if start < 0 or end <= start:
                    raise json.JSONDecodeError("no object", text, 0)
                data = json.loads(text[start:end+1])
            except json.JSONDecodeError:
                raise ValueError("Claude returned unparseable JSON: " + text[:200]) from None
        u = resp.usage
        return data, int(u.input_tokens), int(u.output_tokens)

    async def generate_digest(self, date, user_prompt):
        from .digest_prompts import DIGEST_SYSTEM_PROMPT
        resp = await self._client.messages.create(
            model=self._model, max_tokens=3000, system=DIGEST_SYSTEM_PROMPT,
            messages=[{"role":"user","content": user_prompt}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        u = resp.usage
        return text, int(u.input_tokens), int(u.output_tokens)
