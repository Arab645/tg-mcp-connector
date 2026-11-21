# src/tg_mcp/login.py
import asyncio, os
from .config import load_settings
from .telegram.client import TelethonTelegram

async def _amain():
    s = load_settings()
    async with TelethonTelegram(s.tg_api_id, s.tg_api_hash, s.tg_session_path):
        pass
    try:
        os.chmod(s.tg_session_path.with_suffix(".session"), 0o600)
    except FileNotFoundError:
        pass
    print(f"Session saved to {s.tg_session_path}")

def main(): asyncio.run(_amain())

if __name__ == "__main__":
    main()
