import asyncio
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Chat, FSInputFile, Message, PhotoSize

from bot import panel

CHAT = 1


def photo_message(message_id: int, unique: str) -> Message:
    return Message(
        message_id=message_id, date=datetime.now(), chat=Chat(id=CHAT, type="private"),
        photo=[PhotoSize(file_id=f"id-{unique}", file_unique_id=unique, width=1280, height=720)],
    )


def text_message(message_id: int) -> Message:
    return Message(message_id=message_id, date=datetime.now(), chat=Chat(id=CHAT, type="private"), text="x")


class FakeBot:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.next_id = 100
        self.not_modified = False

    def _photo(self, media):
        self.next_id += 1
        name = media.path.stem if isinstance(media, FSInputFile) else media.removeprefix("id-")
        return photo_message(self.next_id, name)

    async def send_photo(self, chat_id, photo, **kwargs):
        self.calls.append(("send_photo", photo))
        return self._photo(photo)

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", None))
        self.next_id += 1
        return text_message(self.next_id)

    async def edit_message_caption(self, **kwargs):
        if self.not_modified:
            raise TelegramBadRequest(EditMessageText(text=""), "Bad Request: message is not modified")
        self.calls.append(("edit_caption", kwargs["message_id"]))
        return True

    async def edit_message_media(self, media, **kwargs):
        self.calls.append(("edit_media", media.media))
        sent = self._photo(media.media)
        return sent.model_copy(update={"message_id": kwargs["message_id"]})

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_text", kwargs["message_id"]))
        return True

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", message_id))


def reset():
    panel._file_ids.clear()
    panel._unique_to_banner.clear()
    panel._panels.clear()


def test_panel_transitions():
    reset()
    bot = FakeBot()

    async def scenario():
        await panel.show(bot, CHAT, "menu", banner="menu", new=True)
        assert bot.calls[-1][0] == "send_photo" and isinstance(bot.calls[-1][1], FSInputFile)
        panel_id = panel._panels[CHAT][0]

        await panel.show(bot, CHAT, "steam list", banner="topup")
        assert bot.calls[-1][0] == "edit_media"

        await panel.show(bot, CHAT, "steam page 2", banner="topup")
        assert bot.calls[-1] == ("edit_caption", panel_id)

        bot.not_modified = True
        await panel.show(bot, CHAT, "steam page 2", banner="topup")
        assert bot.calls[-1] == ("edit_caption", panel_id)
        bot.not_modified = False

        await panel.show(bot, CHAT, "x" * 1500, banner="docs")
        assert [c[0] for c in bot.calls[-2:]] == ["send_message", "delete"]
        assert bot.calls[-1] == ("delete", panel_id)
        text_id = panel._panels[CHAT][0]

        await panel.show(bot, CHAT, "short text")
        assert bot.calls[-1] == ("edit_text", text_id)

        await panel.show(bot, CHAT, "menu again", banner="menu")
        assert bot.calls[-2] == ("send_photo", "id-menu")  # cached file_id, no re-upload
        assert bot.calls[-1] == ("delete", text_id)

    asyncio.run(scenario())


def test_clicked_message_is_edited_even_when_unknown():
    """After a restart the panel registry is empty; the clicked photo is still edited, not duplicated."""
    reset()
    bot = FakeBot()
    asyncio.run(panel.show(bot, CHAT, "cart", banner="cart", message=photo_message(7, "old-upload")))
    assert bot.calls == [("edit_media", bot.calls[0][1])]
    assert panel._panels[CHAT][0] == 7


def test_caption_length_counts_visible_text():
    assert panel._visible_length("<b>Цена</b> &amp; 🎮") == len("Цена & ") + 2
