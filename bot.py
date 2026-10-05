"""@LoopyOrderBot — @loopy_uz kanali uchun buyurtma boti (aiogram 3).

Imkoniyatlar:
  * /start -> Bosh menyu (Katalog, Buyurtmalarim, Aloqa)
  * Katalog -> kanal havolasi + egasi tanlagan mahsulotlar menyusi (inline tugmalar)
  * Kanal postlari ostida "Buyurtma berish" tugmasi (deep link) -> mijoz
    botga o'tib, to'g'ridan-to'g'ri rasmiylashtirishga tushadi
  * Buyurtma oqimi: soni -> telefon -> manzil -> tasdiqlash
  * Admin: yangi buyurtma xabari + holat tugmalari, katalog menyusini boshqarish
"""
import asyncio
import html
import logging
import os
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

from db import DB

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("loopy")

# =============================================================================
#  SOZLAMALAR — faqat shu qismni tahrirlang (qo'shtirnoqlarni o'chirmang)
# =============================================================================
BOT_TOKEN = "BU_YERGA_TOKENNI_YOZING"       # @BotFather bergan token
ADMIN_IDS = [123456789]                     # buyurtmalar keladigan Telegram ID(lar): [111, 222]
CHANNEL_USERNAME = "loopy_uz"               # kanal username (@ belgisiz)
CONTACT_TEXT = "Telefon: +998 00 000 00 00\nTelegram: @username"   # "Aloqa" tugmasi matni
DB_PATH = "bot.db"                          # buyurtmalar bazasi fayli
PING_PORT = "8080"                          # UptimeRobot uchun ping porti (hosting PORT bersa, o'sha ishlatiladi)
# =============================================================================

# Agar hostingda Environment Variables (yoki .env) bo'lsa, ular yuqoridagilardan ustun turadi.
BOT_TOKEN = (os.getenv("BOT_TOKEN") or BOT_TOKEN).strip()
if os.getenv("ADMIN_IDS"):
    ADMIN_IDS = re.findall(r"\d+", os.getenv("ADMIN_IDS"))
ADMIN_IDS = {int(x) for x in ADMIN_IDS}
CHANNEL_USERNAME = (os.getenv("CHANNEL_USERNAME") or CHANNEL_USERNAME).strip().lstrip("@")
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME}"
DB_PATH = os.getenv("DB_PATH") or DB_PATH
CONTACT_TEXT = (os.getenv("CONTACT_TEXT") or CONTACT_TEXT).replace("\\n", "\n").strip()
PING_PORT = os.getenv("PORT") or os.getenv("PING_PORT") or PING_PORT

db = DB(DB_PATH)
router = Router()
router.message.filter(F.chat.type == "private")

# --- Matnlar ---------------------------------------------------------------
BTN_CATALOG = "🛍 Katalog"
BTN_ORDERS = "📦 Buyurtmalarim"
BTN_CONTACT = "☎️ Aloqa"
BTN_CANCEL = "❌ Bekor qilish"

STATUSES = {
    "accepted": "✅ Qabul qilindi",
    "delivering": "🚚 Yo'lda",
    "done": "🎉 Yetkazildi",
    "cancelled": "❌ Bekor qilindi",
}
STATUS_NEW = "🆕 Yangi"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def status_label(code: str) -> str:
    return STATUSES.get(code, STATUS_NEW)


# --- Klaviaturalar -----------------------------------------------------------
def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CATALOG)],
            [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_CONTACT)],
        ],
        resize_keyboard=True,
    )


def cancel_row() -> list[KeyboardButton]:
    return [KeyboardButton(text=BTN_CANCEL)]


def qty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=str(n), callback_data=f"qty:{n}") for n in range(1, 6)]]
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="confirm:yes"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="confirm:no"),
        ]]
    )


def status_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Qabul", callback_data=f"st:{order_id}:accepted"),
                InlineKeyboardButton(text="🚚 Yo'lda", callback_data=f"st:{order_id}:delivering"),
            ],
            [
                InlineKeyboardButton(text="🎉 Yetkazildi", callback_data=f"st:{order_id}:done"),
                InlineKeyboardButton(text="❌ Bekor", callback_data=f"st:{order_id}:cancelled"),
            ],
        ]
    )


# --- Holatlar ----------------------------------------------------------------
class OrderForm(StatesGroup):
    qty = State()
    phone = State()
    address = State()
    confirm = State()


# --- Yordamchi funksiyalar ----------------------------------------------------
async def send_main_menu(bot: Bot, chat_id: int, prefix: str = "") -> None:
    text = f"{prefix}Bosh menyu\n\nQuyidagilardan birini tanlang:"
    await bot.send_message(chat_id, text, reply_markup=main_menu_kb())


async def begin_order(bot: Bot, chat_id: int, state: FSMContext, post_id: int) -> None:
    title = db.get_post_title(post_id) or f"Mahsulot (post #{post_id})"
    await state.clear()
    await state.set_state(OrderForm.qty)
    await state.update_data(post_id=post_id, title=title, qty=1)
    await bot.send_message(
        chat_id,
        f"Siz tanlagan mahsulot: <b>{esc(title)}</b>\n\nNechta dona kerak? (tugmani bosing yoki raqam yozing)",
        reply_markup=ReplyKeyboardRemove(),
    )
    await bot.send_message(chat_id, "Sonini tanlang:", reply_markup=qty_kb())


async def ask_phone(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(OrderForm.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamimni yuborish", request_contact=True)], cancel_row()],
        resize_keyboard=True,
    )
    await bot.send_message(
        chat_id,
        "Telefon raqamingizni yuboring: pastdagi tugmani bosing yoki raqamni yozing.",
        reply_markup=kb,
    )


async def ask_address(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(OrderForm.address)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Lokatsiyamni yuborish", request_location=True)], cancel_row()],
        resize_keyboard=True,
    )
    await bot.send_message(
        chat_id,
        "Yetkazib berish manzilini yozing yoki lokatsiyangizni yuboring.",
        reply_markup=kb,
    )


async def show_confirm(bot: Bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(OrderForm.confirm)
    text = (
        "Buyurtmani tekshiring:\n\n"
        f"Mahsulot: <b>{esc(data['title'])}</b>\n"
        f"Soni: <b>{data['qty']}</b>\n"
        f"Telefon: {esc(data['phone'])}\n"
        f"Manzil: {esc(data['address'])}"
    )
    await bot.send_message(chat_id, "Ma'lumotlar tayyor.", reply_markup=ReplyKeyboardRemove())
    await bot.send_message(chat_id, text, reply_markup=confirm_kb())


def order_text(o) -> str:
    username = f" (@{esc(o['username'])})" if o["username"] else ""
    post_link = f"{CHANNEL_URL}/{o['post_id']}" if o["post_id"] else ""
    product = esc(o["product_title"])
    if post_link:
        product = f'<a href="{post_link}">{product}</a>'
    return (
        f"🧾 <b>Buyurtma #{o['id']}</b>\n"
        f"Holat: <b>{status_label(o['status'])}</b>\n\n"
        f"Mahsulot: {product}\n"
        f"Soni: <b>{o['qty']}</b>\n"
        f"Mijoz: {esc(o['full_name'])}{username}\n"
        f"Telefon: {esc(o['phone'])}\n"
        f"Manzil: {esc(o['address'])}\n"
        f"Vaqt: {esc(o['created_at'])}"
    )


async def clear_markup(call: CallbackQuery) -> None:
    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass


# --- /start va deep link --------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    args = (command.args or "").strip()
    match = re.fullmatch(r"order_(\d+)", args)
    if match:
        # Kanal posti ostidagi tugmadan kelgan mijoz: to'g'ridan-to'g'ri rasmiylashtirish
        await begin_order(bot, message.chat.id, state, int(match.group(1)))
        return
    await send_main_menu(bot, message.chat.id)


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cancel(message: Message, state: FSMContext, bot: Bot):
    was_ordering = await state.get_state() is not None
    await state.clear()
    await send_main_menu(bot, message.chat.id, "Buyurtma bekor qilindi.\n\n" if was_ordering else "")


# --- Bosh menyu tugmalari ----------------------------------------------------------
@router.message(F.text == BTN_CATALOG)
async def catalog(message: Message, state: FSMContext):
    await state.clear()
    items = db.menu_list()
    rows = [
        [InlineKeyboardButton(text=item["text"], callback_data=f"order:{item['post_id']}")]
        for item in items
    ]
    rows.append([InlineKeyboardButton(text="📢 Kanalga o'tish", url=CHANNEL_URL)])
    text = f"Barcha mahsulotlarni ko'rish uchun kanalimizga o'ting:\n{CHANNEL_URL}"
    if items:
        text += "\n\nYoki mahsulotni shu yerdan tanlang:"
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.message(F.text == BTN_ORDERS)
async def my_orders(message: Message, state: FSMContext):
    await state.clear()
    rows = db.user_orders(message.from_user.id)
    if not rows:
        await message.answer("Hozircha buyurtmalaringiz yo'q.")
        return
    lines = ["<b>Oxirgi buyurtmalaringiz:</b>\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} • {esc(r['product_title'])} × {r['qty']}\n"
            f"   {status_label(r['status'])} • {esc(r['created_at'])}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == BTN_CONTACT)
async def contact(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(esc(CONTACT_TEXT))


@router.callback_query(F.data.startswith("order:"))
async def pick_from_menu(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await begin_order(bot, call.from_user.id, state, int(call.data.split(":")[1]))


# --- Admin buyruqlari ---------------------------------------------------------------
ADMIN = F.from_user.id.in_(ADMIN_IDS)


@router.message(Command("admin"), ADMIN)
async def admin_help(message: Message):
    await message.answer(
        "<b>Admin buyruqlari</b>\n\n"
        "/menu_add Nomi | post_id — katalog menyusiga mahsulot qo'shish "
        "(post_id o'rniga post havolasini ham yozish mumkin)\n"
        "/menu_list — menyudagi mahsulotlar\n"
        "/menu_del id — menyudan o'chirish\n"
        "/title post_id | Nomi — post nomini o'zgartirish\n\n"
        "Masalan:\n<code>/menu_add Qizil ryukzak | https://t.me/loopy_uz/12</code>"
    )


@router.message(Command("menu_add"), ADMIN)
async def menu_add(message: Message, command: CommandObject):
    m = re.fullmatch(r"(.+?)\s*\|\s*(?:https?://\S*/)?(\d+)", (command.args or "").strip())
    if not m:
        await message.answer("Format: <code>/menu_add Nomi | post_id</code>")
        return
    text, post_id = m.group(1).strip()[:60], int(m.group(2))
    menu_id = db.menu_add(text, post_id)
    db.save_post(post_id, text, overwrite=False)
    await message.answer(f"Qo'shildi: #{menu_id} — {esc(text)} → post {post_id}")


@router.message(Command("menu_list"), ADMIN)
async def menu_list(message: Message):
    items = db.menu_list()
    if not items:
        await message.answer("Menyu bo'sh. /menu_add bilan qo'shing.")
        return
    await message.answer("\n".join(f"{i['id']}. {esc(i['text'])} → post {i['post_id']}" for i in items))


@router.message(Command("menu_del"), ADMIN)
async def menu_del(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Format: <code>/menu_del id</code>")
        return
    ok = db.menu_del(int(arg))
    await message.answer("O'chirildi." if ok else "Bunday id topilmadi.")


@router.message(Command("title"), ADMIN)
async def set_title(message: Message, command: CommandObject):
    m = re.fullmatch(r"(?:https?://\S*/)?(\d+)\s*\|\s*(.+)", (command.args or "").strip())
    if not m:
        await message.answer("Format: <code>/title post_id | Nomi</code>")
        return
    db.save_post(int(m.group(1)), m.group(2).strip()[:60])
    await message.answer("Post nomi yangilandi.")


# --- Buyurtma oqimi ---------------------------------------------------------------------
@router.callback_query(OrderForm.qty, F.data.startswith("qty:"))
async def qty_button(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await clear_markup(call)
    await state.update_data(qty=int(call.data.split(":")[1]))
    await ask_phone(bot, call.from_user.id, state)


@router.message(OrderForm.qty, F.text.regexp(r"^\d{1,3}$"))
async def qty_text(message: Message, state: FSMContext, bot: Bot):
    qty = int(message.text)
    if qty < 1:
        await message.answer("Son 1 dan kam bo'lmasligi kerak.")
        return
    await state.update_data(qty=qty)
    await ask_phone(bot, message.chat.id, state)


@router.message(OrderForm.qty)
async def qty_invalid(message: Message):
    await message.answer("Sonini tugma orqali tanlang yoki raqam yozing (masalan: 2).")


@router.message(OrderForm.phone, F.contact)
async def phone_contact(message: Message, state: FSMContext, bot: Bot):
    phone = message.contact.phone_number
    await state.update_data(phone=phone if phone.startswith("+") else f"+{phone}")
    await ask_address(bot, message.chat.id, state)


@router.message(OrderForm.phone, F.text)
async def phone_text(message: Message, state: FSMContext, bot: Bot):
    digits = re.sub(r"\D", "", message.text)
    if not 9 <= len(digits) <= 15:
        await message.answer("Raqam noto'g'ri ko'rinadi. Masalan: +998 90 123 45 67")
        return
    await state.update_data(phone=f"+{digits}")
    await ask_address(bot, message.chat.id, state)


@router.message(OrderForm.address, F.location)
async def address_location(message: Message, state: FSMContext, bot: Bot):
    loc = message.location
    await state.update_data(address=f"https://maps.google.com/?q={loc.latitude},{loc.longitude}")
    await show_confirm(bot, message.chat.id, state)


@router.message(OrderForm.address, F.text)
async def address_text(message: Message, state: FSMContext, bot: Bot):
    address = message.text.strip()
    if len(address) < 5:
        await message.answer("Manzilni to'liqroq yozing yoki lokatsiya yuboring.")
        return
    await state.update_data(address=address[:300])
    await show_confirm(bot, message.chat.id, state)


@router.callback_query(OrderForm.confirm, F.data == "confirm:no")
async def confirm_no(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await clear_markup(call)
    await state.clear()
    await send_main_menu(bot, call.from_user.id, "Buyurtma bekor qilindi.\n\n")


@router.callback_query(OrderForm.confirm, F.data == "confirm:yes")
async def confirm_yes(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await clear_markup(call)
    data = await state.get_data()
    await state.clear()
    user = call.from_user
    order_id = db.add_order(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone=data["phone"],
        address=data["address"],
        post_id=data["post_id"],
        product_title=data["title"],
        qty=data["qty"],
    )
    await bot.send_message(
        user.id,
        f"✅ Buyurtmangiz qabul qilindi!\nBuyurtma raqami: <b>#{order_id}</b>\n\n"
        "Tez orada operatorimiz siz bilan bog'lanadi. Holatni «Buyurtmalarim» bo'limida ko'rishingiz mumkin.",
        reply_markup=main_menu_kb(),
    )
    order = db.get_order(order_id)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, order_text(order), reply_markup=status_kb(order_id)
            )
        except TelegramAPIError as e:
            log.warning("Adminga (%s) yuborib bo'lmadi: %s", admin_id, e)


@router.callback_query(F.data.startswith(("qty:", "confirm:")))
async def stale_callback(call: CallbackQuery):
    await call.answer("Bu buyurtma eskirgan. Iltimos, qaytadan boshlang.", show_alert=True)


@router.callback_query(F.data.startswith("st:"))
async def change_status(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    _, raw_id, code = call.data.split(":")
    order = db.get_order(int(raw_id))
    if not order or code not in STATUSES:
        await call.answer("Buyurtma topilmadi", show_alert=True)
        return
    db.set_status(order["id"], code)
    order = db.get_order(order["id"])
    await call.answer(STATUSES[code])
    try:
        await call.message.edit_text(
            order_text(order), reply_markup=status_kb(order["id"])
        )
    except TelegramAPIError:
        pass  # matn o'zgarmagan bo'lsa Telegram xato qaytaradi — bu normal
    try:
        await bot.send_message(
            order["user_id"], f"Buyurtma #{order['id']} holati: <b>{STATUSES[code]}</b>"
        )
    except TelegramAPIError as e:
        log.warning("Mijozga xabar yuborib bo'lmadi: %s", e)


# Boshqa har qanday xabar -> Bosh menyu
@router.message(StateFilter(None))
async def fallback(message: Message, bot: Bot):
    await send_main_menu(bot, message.chat.id)


# --- Kanal postlari: "Buyurtma berish" tugmasi ------------------------------------------------
def is_our_channel(post: Message) -> bool:
    return (post.chat.username or "").lower() == CHANNEL_USERNAME.lower()


def post_title(post: Message) -> str:
    raw = (post.caption or post.text or "").strip()
    first = raw.splitlines()[0].strip() if raw else ""
    return first[:60] or f"Post #{post.message_id}"


async def attach_order_button(bot: Bot, post: Message) -> None:
    if post.reply_markup:  # postda allaqachon tugma bor
        return
    me = await bot.me()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🛒 Buyurtma berish",
                url=f"https://t.me/{me.username}?start=order_{post.message_id}",
            )
        ]]
    )
    try:
        await bot.edit_message_reply_markup(
            chat_id=post.chat.id, message_id=post.message_id, reply_markup=kb
        )
    except TelegramAPIError as e:
        log.warning("Postga tugma qo'shib bo'lmadi (post %s): %s", post.message_id, e)


@router.channel_post()
async def on_channel_post(post: Message, bot: Bot):
    if not is_our_channel(post):
        return
    db.save_post(post.message_id, post_title(post))
    await attach_order_button(bot, post)


@router.edited_channel_post()
async def on_channel_edit(post: Message, bot: Bot):
    if not is_our_channel(post):
        return
    db.save_post(post.message_id, post_title(post))
    await attach_order_button(bot, post)


# --- UptimeRobot uchun ping manzili ----------------------------------------------------------------
# PORT (yoki PING_PORT) berilsa, bot oddiy veb-sahifa ochadi. UptimeRobot shu manzilga
# har 5 daqiqada murojaat qilib, bepul hostingda botni "uxlab qolishdan" saqlaydi.
async def start_ping_server(port: int) -> None:
    from aiohttp import web  # aiogram bilan birga o'rnatiladi

    async def ok(_request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("Ping manzili ishlayapti: 0.0.0.0:%s", port)
    except OSError as e:  # port band bo'lsa bot baribir ishlayveradi
        log.warning("Ping serverni %s portda ochib bo'lmadi: %s", port, e)


# --- Ishga tushirish -----------------------------------------------------------------------------
async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("BU_YERGA"):
        raise SystemExit("Token kiritilmagan. bot.py boshidagi SOZLAMALAR qismiga BOT_TOKEN ni yozing.")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS bo'sh: buyurtmalar hech kimga yuborilmaydi va admin buyruqlari ishlamaydi!")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    me = await bot.me()
    log.info("Bot ishga tushdi: @%s | kanal: @%s | adminlar: %s", me.username, CHANNEL_USERNAME, sorted(ADMIN_IDS))
    await bot.delete_webhook(drop_pending_updates=False)
    if PING_PORT:
        await start_ping_server(int(PING_PORT))
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
