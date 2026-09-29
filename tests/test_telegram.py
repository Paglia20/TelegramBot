import importlib.util
import unittest

import config
from monitor import Monitor
from tests.helpers import FakeNotifier, FakeSource, make_listing, patch_config, temp_storage

HAS_PTB = importlib.util.find_spec("telegram") is not None
CHAT_ID = 42

if HAS_PTB:
    from telegram.error import BadRequest, Forbidden, NetworkError

    import telegram_ui as ui
    from notifier import Notifier, format_listing


class FakeChat:
    def __init__(self, chat_id=CHAT_ID, chat_type="private"):
        self.id = chat_id
        self.type = chat_type
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


class FakeUser:
    def __init__(self, name="Luca Rossi"):
        self.full_name = name
        self.username = "luca"


class FakeUpdate:
    def __init__(self, chat, data=None, text=None, user=None):
        self.effective_chat = chat
        self.effective_user = user or FakeUser()
        self.callback_query = FakeQuery(data) if data else None
        self.effective_message = FakeMessage(text)


class FakeApp:
    def __init__(self, bot_data):
        self.bot_data = bot_data
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


class FakeTelegramBot:
    username = "MonitorAnnunciBot"

    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


class FakeContext:
    def __init__(self, app, user_data, args=None, bot=None):
        self.application = app
        self.user_data = user_data
        self.args = args or []
        self.bot = bot or FakeTelegramBot()


class FakeBot:
    def __init__(self, fail_photo=None, blocked=()):
        self.calls = []
        self.fail_photo = fail_photo
        self.blocked = set(blocked)

    def _check(self, chat_id):
        if chat_id in self.blocked:
            raise Forbidden("bot was blocked by the user")

    async def send_media_group(self, chat_id, media):
        self._check(chat_id)
        if self.fail_photo:
            raise self.fail_photo
        self.calls.append(("album", len(media)))

    async def send_photo(self, chat_id, photo, **kwargs):
        self._check(chat_id)
        if self.fail_photo:
            raise self.fail_photo
        self.calls.append(("photo", chat_id))

    async def send_message(self, chat_id, text, **kwargs):
        self._check(chat_id)
        self.calls.append(("text", chat_id))


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
        notifier = Notifier(bot, lambda: [CHAT_ID])
        item = make_listing("1", images=["a", "b", "c", "d", "e", "f"])
        self.assertTrue(await notifier.send_listing(item, "x"))
        item.images = ["a"]
        await notifier.send_listing(item, "x")
        item.images = []
        await notifier.send_listing(item, "x")
        self.assertEqual(bot.calls, [("album", config.MAX_PHOTOS), ("photo", CHAT_ID), ("text", CHAT_ID)])

    async def test_rejected_photo_falls_back_to_text(self):
        bot = FakeBot(fail_photo=BadRequest("wrong file"))
        self.assertTrue(await Notifier(bot, lambda: [CHAT_ID]).send_listing(make_listing("1", images=["a"]), "x"))
        self.assertEqual(bot.calls, [("text", CHAT_ID)])

    async def test_network_error_reports_failure(self):
        bot = FakeBot(fail_photo=NetworkError("down"))
        self.assertFalse(await Notifier(bot, lambda: [CHAT_ID]).send_listing(make_listing("1", images=["a"]), "x"))

    async def test_every_recipient_gets_the_listing(self):
        bot = FakeBot()
        notifier = Notifier(bot, lambda: [CHAT_ID, 99])
        self.assertTrue(await notifier.send_listing(make_listing("1", images=["a"]), "x"))
        self.assertEqual(bot.calls, [("photo", CHAT_ID), ("photo", 99)])
        await notifier.alert("prova")
        self.assertEqual(bot.calls[-2:], [("text", CHAT_ID), ("text", 99)])

    async def test_blocked_recipient_does_not_stop_the_others(self):
        bot = FakeBot(blocked={99})
        self.assertTrue(await Notifier(bot, lambda: [99, CHAT_ID]).send_listing(make_listing("1"), "x"))
        self.assertEqual(bot.calls, [("text", CHAT_ID)])
        self.assertFalse(await Notifier(bot, lambda: [99]).send_listing(make_listing("1"), "x"))


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
        app = FakeApp({"store": self.store})
        ui.register(app)
        self.assertEqual(len(app.handlers), 19)

    async def invite_code(self):
        bot = FakeTelegramBot()
        await ui.cmd_invita(FakeUpdate(self.chat), self.ctx_with_bot(bot))
        text = self.chat.sent[-1][0]
        self.assertIn("https://t.me/MonitorAnnunciBot?start=inv_", text)
        return text.split("?start=")[1].split()[0]

    def ctx_with_bot(self, bot, args=None):
        return FakeContext(self.app, {}, args, bot)

    async def test_invite_link_connects_a_friend(self):
        code = await self.invite_code()
        friend = FakeChat(chat_id=99)
        bot = FakeTelegramBot()
        await ui.cmd_start(FakeUpdate(friend, user=FakeUser("Luca Rossi")), self.ctx_with_bot(bot, [code]))
        self.assertTrue(ui.is_authorized(self.store, 99))
        self.assertEqual(ui.recipients(self.store), [CHAT_ID, 99])
        self.assertIn("Sei collegato", friend.sent[0][0])
        self.assertIn("Monitor annunci", friend.sent[1][0])
        self.assertEqual(bot.messages, [(CHAT_ID, "Luca Rossi si è collegato con il tuo link d'invito.")])

    async def test_friend_can_manage_everything(self):
        code = await self.invite_code()
        friend = FakeChat(chat_id=99)
        await ui.cmd_start(FakeUpdate(friend), self.ctx_with_bot(FakeTelegramBot(), [code]))
        await ui.on_button(FakeUpdate(friend, data="p"), FakeContext(self.app, {}))
        self.assertTrue(self.store.paused)

    async def test_invite_link_works_only_once(self):
        code = await self.invite_code()
        await ui.cmd_start(FakeUpdate(FakeChat(chat_id=99)), self.ctx_with_bot(FakeTelegramBot(), [code]))
        stranger = FakeChat(chat_id=100)
        await ui.cmd_start(FakeUpdate(stranger), self.ctx_with_bot(FakeTelegramBot(), [code]))
        self.assertFalse(ui.is_authorized(self.store, 100))
        self.assertIn("scaduto", stranger.sent[0][0])

    async def test_expired_invite_is_rejected(self):
        code = await self.invite_code()
        self.store.db.execute("UPDATE invites SET expires_at=0")
        stranger = FakeChat(chat_id=100)
        await ui.cmd_start(FakeUpdate(stranger), self.ctx_with_bot(FakeTelegramBot(), [code]))
        self.assertFalse(ui.is_authorized(self.store, 100))

    async def test_start_without_invite_is_ignored(self):
        stranger = FakeChat(chat_id=100)
        await ui.cmd_start(FakeUpdate(stranger), self.ctx_with_bot(FakeTelegramBot(), []))
        await ui.cmd_start(FakeUpdate(stranger), self.ctx_with_bot(FakeTelegramBot(), ["inv_inventato"]))
        self.assertFalse(ui.is_authorized(self.store, 100))
        self.assertEqual(len(stranger.sent), 1)
        await ui.on_button(FakeUpdate(stranger, data="p"), FakeContext(self.app, {}))
        self.assertFalse(self.store.paused)

    async def test_invite_ignored_in_groups(self):
        code = await self.invite_code()
        group = FakeChat(chat_id=-100123, chat_type="supergroup")
        await ui.cmd_start(FakeUpdate(group), self.ctx_with_bot(FakeTelegramBot(), [code]))
        self.assertFalse(ui.is_authorized(self.store, -100123))

    async def test_owner_removes_a_user(self):
        self.store.add_user(99, "Luca", CHAT_ID)
        await ui.cmd_utenti(FakeUpdate(self.chat), self.ctx())
        self.assertIn("Luca", self.chat.sent[-1][0])
        bot = FakeTelegramBot()
        update = FakeUpdate(self.chat, data="ud:99")
        await ui.on_button(update, self.ctx_with_bot(bot))
        self.assertFalse(ui.is_authorized(self.store, 99))
        self.assertEqual(bot.messages[0][0], 99)
        self.assertIn("Solo tu", update.callback_query.edits[-1][0])

    async def test_friend_cannot_remove_users(self):
        self.store.add_user(99, "Luca", CHAT_ID)
        self.store.add_user(98, "Marco", CHAT_ID)
        await ui.on_button(FakeUpdate(FakeChat(chat_id=99), data="ud:98"), FakeContext(self.app, {}))
        self.assertTrue(ui.is_authorized(self.store, 98))

    async def test_authorized_filter(self):
        self.store.add_user(99, "Luca", CHAT_ID)
        f = ui.AuthorizedChat(self.store)

        class Msg:
            def __init__(self, chat_id):
                self.chat = FakeChat(chat_id)

        self.assertTrue(f.filter(Msg(CHAT_ID)))
        self.assertTrue(f.filter(Msg(99)))
        self.assertFalse(f.filter(Msg(100)))


if __name__ == "__main__":
    unittest.main()
