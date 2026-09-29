import importlib.util
import unittest

import config
from monitor import Monitor
from tests.helpers import FakeNotifier, FakeSource, make_listing, patch_config, temp_storage

HAS_PTB = importlib.util.find_spec("telegram") is not None
CHAT_ID = 42

if HAS_PTB:
    from telegram.error import BadRequest, NetworkError

    import telegram_ui as ui
    from notifier import Notifier, format_listing


class FakeChat:
    def __init__(self, chat_id=CHAT_ID):
        self.id = chat_id
        self.sent = []

    async def send_message(self, text, **kwargs):
        self.sent.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits = []

    async def answer(self):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeMessage:
    def __init__(self, text):
        self.text = text


class FakeUpdate:
    def __init__(self, chat, data=None, text=None):
        self.effective_chat = chat
        self.callback_query = FakeQuery(data) if data else None
        self.effective_message = FakeMessage(text)


class FakeApp:
    def __init__(self, bot_data):
        self.bot_data = bot_data
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


class FakeContext:
    def __init__(self, app, user_data, args=None):
        self.application = app
        self.user_data = user_data
        self.args = args or []


class FakeBot:
    def __init__(self, fail_photo=None):
        self.calls = []
        self.fail_photo = fail_photo

    async def send_media_group(self, chat_id, media):
        if self.fail_photo:
            raise self.fail_photo
        self.calls.append(("album", len(media)))

    async def send_photo(self, chat_id, photo, **kwargs):
        if self.fail_photo:
            raise self.fail_photo
        self.calls.append(("photo",))

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("text",))


@unittest.skipUnless(HAS_PTB, "python-telegram-bot non installato")
class NotifierTest(unittest.IsolatedAsyncioTestCase):
    def test_message_format(self):
        item = make_listing("1", "RTX <4090> & co", source="EBAY_DE", origin="EBAY_IT", price=1234.5)
        text = format_listing(item, "RTX 4090")
        self.assertIn("Nuovo annuncio trovato", text)
        self.assertIn("RTX &lt;4090&gt; &amp; co", text)
        self.assertIn("1.234,50 EUR", text)
        self.assertIn("eBay Germania (pubblicato su eBay Italia)", text)

    def test_price_drop_format(self):
        text = format_listing(make_listing("1", price=1350), "RTX 4090", old_price=1500)
        self.assertIn("Prezzo ribassato", text)
        self.assertIn("prima 1.500,00 EUR", text)

    async def test_album_photo_or_text(self):
        bot = FakeBot()
        notifier = Notifier(bot, CHAT_ID)
        item = make_listing("1", images=["a", "b", "c", "d", "e", "f"])
        self.assertTrue(await notifier.send_listing(item, "x"))
        item.images = ["a"]
        await notifier.send_listing(item, "x")
        item.images = []
        await notifier.send_listing(item, "x")
        self.assertEqual(bot.calls, [("album", config.MAX_PHOTOS), ("photo",), ("text",)])

    async def test_rejected_photo_falls_back_to_text(self):
        bot = FakeBot(fail_photo=BadRequest("wrong file"))
        self.assertTrue(await Notifier(bot, CHAT_ID).send_listing(make_listing("1", images=["a"]), "x"))
        self.assertEqual(bot.calls, [("text",)])

    async def test_network_error_reports_failure(self):
        bot = FakeBot(fail_photo=NetworkError("down"))
        self.assertFalse(await Notifier(bot, CHAT_ID).send_listing(make_listing("1", images=["a"]), "x"))


@unittest.skipUnless(HAS_PTB, "python-telegram-bot non installato")
class TelegramUiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch_config(TELEGRAM_CHAT_ID=CHAT_ID)
        self.patcher.start()
        self.store = temp_storage()
        self.monitor = Monitor(self.store, FakeNotifier(), {m: FakeSource(m) for m in config.EBAY_MARKETS})
        self.app = FakeApp({"store": self.store, "monitor": self.monitor})
        self.user_data = {}
        self.chat = FakeChat()

    async def asyncTearDown(self):
        self.patcher.stop()

    def ctx(self, args=None):
        return FakeContext(self.app, self.user_data, args)

    async def press(self, data):
        update = FakeUpdate(self.chat, data=data)
        await ui.on_button(update, self.ctx())
        return update.callback_query

    async def test_menu(self):
        await ui.cmd_menu(FakeUpdate(self.chat), self.ctx())
        self.assertIn("Monitor annunci", self.chat.sent[-1][0])

    async def test_add_keywords_with_button_and_text(self):
        await self.press("ka")
        self.assertEqual(self.user_data["awaiting"], "kw")
        await ui.on_text(FakeUpdate(self.chat, text="RTX 4090, Game Boy\nThinkPad X1"), self.ctx())
        self.assertEqual([k.term for k in self.store.keywords()], ["RTX 4090", "Game Boy", "ThinkPad X1"])
        self.assertTrue(self.monitor._wake.is_set())

    async def test_add_command_recognises_duplicates(self):
        await ui.cmd_aggiungi(FakeUpdate(self.chat), self.ctx(["RTX", "4090"]))
        await ui.cmd_aggiungi(FakeUpdate(self.chat), self.ctx(["rtx4090"]))
        self.assertIn("Già presenti", self.chat.sent[-1][0])

    async def test_remove_with_button(self):
        self.store.add_keyword("RTX 4090")
        query = await self.press("k")
        button = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0]
        await self.press(button.callback_data)
        self.assertEqual(self.store.keywords(), [])

    async def test_excludes_commands(self):
        await ui.cmd_escludi(FakeUpdate(self.chat), self.ctx(["cover,", "custodia"]))
        self.assertEqual(len(self.store.excludes()), 2)
        await ui.cmd_includi(FakeUpdate(self.chat), self.ctx(["cover"]))
        self.assertEqual([t for _, t in self.store.excludes()], ["custodia"])

    async def test_timer(self):
        query = await self.press("ts:60")
        self.assertEqual(self.store.interval, 60)
        self.assertIn("Timer effettivo", query.edits[-1][0])
        await self.press("tc")
        await ui.on_text(FakeUpdate(self.chat, text="5"), self.ctx())
        self.assertEqual(self.store.interval, config.MIN_INTERVAL_SECONDS)
        await self.press("tc")
        await ui.on_text(FakeUpdate(self.chat, text="abc"), self.ctx())
        self.assertIn("numero", self.chat.sent[-2][0])

    async def test_markets_and_pause(self):
        await self.press("mt:EBAY_CH")
        self.assertNotIn("EBAY_CH", self.store.enabled_markets)
        await self.press("p")
        self.assertTrue(self.store.paused)
        await self.press("p")
        self.assertFalse(self.store.paused)

    async def test_status_view(self):
        self.store.toggle_market("EBAY_CH")
        query = await self.press("s")
        self.assertIn("eBay Svizzera: disattivato", query.edits[-1][0])

    async def test_other_chats_are_ignored(self):
        stranger = FakeChat(chat_id=7)
        update = FakeUpdate(stranger, data="p")
        await ui.on_button(update, self.ctx())
        self.assertFalse(self.store.paused)
        self.assertEqual(update.callback_query.edits, [])

    async def test_dashboard_link_local_and_public(self):
        class FakeDashboard:
            def __init__(self, public):
                self.is_public = public
                self.url = "https://m.example.com" if public else "http://127.0.0.1:8765"

            def create_login_link(self):
                return "https://m.example.com/login?t=abc"

        self.app.bot_data["dashboard"] = FakeDashboard(False)
        await ui.cmd_dashboard(FakeUpdate(self.chat), self.ctx())
        self.assertIn("127.0.0.1:8765", self.chat.sent[-1][0])
        self.app.bot_data["dashboard"] = FakeDashboard(True)
        await self.press("db")
        text, kwargs = self.chat.sent[-1]
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].url, "https://m.example.com/login?t=abc")
        self.assertNotIn("login?t", text)

    async def test_register_handlers(self):
        app = FakeApp({})
        ui.register(app)
        self.assertEqual(len(app.handlers), 17)


if __name__ == "__main__":
    unittest.main()
