import asyncio
import sys
from aiogram import Bot
from aiogram.types import ChatPermissions
import config

FULL = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)

async def main(user_ids):
    bot = Bot(token=config.BOT_TOKEN)
    try:
        for uid in user_ids:
            try:
                await bot.restrict_chat_member(config.CHAT_ID, uid, permissions=FULL)
                print(f"OK razmuted: {uid}")
            except Exception as e:
                print(f"FAIL {uid}: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    ids = [int(x) for x in sys.argv[1:]]
    if not ids:
        print("Usage: python unmute.py <user_id> [user_id ...]")
        sys.exit(1)
    asyncio.run(main(ids))
