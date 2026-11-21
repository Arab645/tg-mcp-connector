import re
_ACK = {"+","+1","ok","okay","k","kk","ty","thx","thanks","спс","спасибо","дякую","done","got it"}
_SERVICE = {"MessageActionChatJoinedByLink","MessageActionPinMessage",
            "MessageActionChatAddUser","MessageActionChatDeleteUser"}
_EMOJI = re.compile(
    r"^[\s☀-➿\U0001f300-\U0001faff\U0001f900-\U0001f9ff✀-➿]+$")

def _is_noise(row: dict) -> bool:
    mk = row.get("media_kind") or ""
    if mk in _SERVICE: return True
    if mk.startswith("MessageMediaSticker"): return True
    text = (row.get("text") or "").strip()
    if not text and not mk: return True
    if not text: return False  # has non-sticker media, keep
    low = text.lower()
    if low in _ACK: return True
    if len(text) <= 3 and _EMOJI.match(text): return True
    if _EMOJI.match(text) and len(text) <= 8: return True
    return False

def filter_messages(rows):
    kept = [r for r in rows if not _is_noise(r)]
    return kept, len(rows) - len(kept)
