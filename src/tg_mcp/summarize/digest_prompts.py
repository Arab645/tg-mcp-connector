DIGEST_SYSTEM_PROMPT = """You are a terse daily brief writer.
You receive per-chat JSON summaries from one day across multiple chats.
Write a single Markdown daily digest the user can read in under a minute.

Output STRICT Markdown with these sections (omit a section if nothing to say):

## What happened today
2-3 paragraphs: the day's main threads across all chats, with chat names in italics where relevant.

## Key decisions
- short bullet (chat: *Chat Name*)

## Action items
- [owner] what to do (chat: *Chat Name*)

## Open questions / risks
- short bullet (chat: *Chat Name*)

## Links
- url — one-line context

Rules: deduplicate across chats, prioritize what's actionable, keep it short.
Write in the dominant language of the inputs (ru/uk/en)."""

def build_digest_user_prompt(date: str, per_chat: list[dict]) -> str:
    """per_chat items: {"chat_title": str, "chat_id": int, "summary": dict, "message_count": int}"""
    import json as _json
    lines = [f"Date (UTC): {date}", "", "Per-chat summaries:"]
    for item in per_chat:
        lines.append(f"\n### {item['chat_title']}  (msgs={item['message_count']})")
        lines.append("```json")
        lines.append(_json.dumps(item["summary"], ensure_ascii=False, indent=2))
        lines.append("```")
    return "\n".join(lines)
