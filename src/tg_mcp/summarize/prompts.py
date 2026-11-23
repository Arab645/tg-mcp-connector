SYSTEM_PROMPT = """You are a terse work-chat summarizer.
Given one day of messages from a single chat, output STRICT JSON with keys:
topics (list of {title, summary}), decisions (list of strings),
action_items (list of {text, owner}), open_questions (list of strings),
links (list of {url, context}), participants (list of names).
Ignore chit-chat, stickers, +1s, thanks, and small talk.
Deduplicate repeated points. Keep each topic summary to 1-2 sentences.
If the day has nothing substantive, return all empty lists with topics=[].
Language rules (strict):
- topics[].title: ENGLISH only (short noun phrase, Title Case).
- Everything else (topics[].summary, decisions, action_items[].text,
  open_questions, links[].context) must be in UKRAINIAN, regardless of
  the source message language. Translate if needed.
- participants[] and action_items[].owner: keep names exactly as they
  appear in the messages (do not translate or transliterate)."""

def build_user_prompt(chat_title: str, date: str, messages: list[dict]) -> str:
    lines = [f"Chat: {chat_title}", f"Date (UTC): {date}", "Messages:"]
    for m in messages:
        t = (m.get("date_utc") or "")[11:16]
        who = m.get("sender_name") or "?"
        txt = (m.get("text") or "").replace("\n"," ").strip()
        if not txt: continue
        lines.append(f"[{t}] {who}: {txt}")
    return "\n".join(lines)
