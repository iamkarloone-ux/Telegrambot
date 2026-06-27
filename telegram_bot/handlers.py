# telegram_bot/handlers.py
import asyncio
import uuid
import httpx
import math
import database as db  # Imports your existing root database.py
from aiogram import Dispatcher, Bot, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from telegram_bot.config import CARS_PER_PAGE, TELEGRAM_BOT_TOKEN
from telegram_bot.states import ResellerStates
from telegram_bot.keyboards import get_patch_menu_keyboard, get_skip_keyboard
from telegram_bot.patcher import (
    verify_license_key,
    load_db_data_async,
    get_profile_injector,
    decrypt_payload,
    execute_reseller_patch_task
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- DISAPPEARING CONVERSATION HELPERS ---

async def track_msg(state: FSMContext, message_id: int):
    """Stores a message ID in the current state dataset for cleanup."""
    data = await state.get_data()
    msg_list = data.get("msgs_to_delete", [])
    if message_id not in msg_list:
        msg_list.append(message_id)
    await state.update_data(msgs_to_delete=msg_list)

async def purge_tracked_msgs(chat_id: int, state: FSMContext):
    """Deletes all messages currently tracked in the state."""
    data = await state.get_data()
    msg_list = data.get("msgs_to_delete", [])
    for mid in msg_list:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    await state.update_data(msgs_to_delete=[])

# --- COMMANDS ---

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Greets user, cleans chat, and asks for reseller license key."""
    try:
        await message.delete()
    except Exception:
        pass
        
    await purge_tracked_msgs(message.chat.id, state)
    await state.clear()
    
    sent_msg = await message.answer(
        "🔑 *Reseller Patcher Tool* 🔑\n\n"
        "To access this tool, please enter your active Reseller License Key.\n\n"
        "Don't have a key? Message the developer to purchase access:\n"
        "💬 t.me/ImZhouFann\n\n"
        "👉 Type /start at any time to cancel.",
        parse_mode="Markdown"
    )
    await state.set_state(ResellerStates.awaiting_key)
    await track_msg(state, sent_msg.message_id)

@dp.message(ResellerStates.awaiting_key)
async def process_key(message: Message, state: FSMContext):
    try:
        await message.delete() # Disappear input credentials
    except Exception:
        pass
        
    key = message.text.strip()
    license_info = await verify_license_key(key, str(message.from_user.id))
    
    if not license_info:
        await purge_tracked_msgs(message.chat.id, state)
        sent_msg = await message.answer(
            "❌ *Invalid or Expired License Key.*\n\n"
            "To buy a subscription (10$/Month), contact:\n"
            "💬 m.me/lark.abalunan.1",
            parse_mode="Markdown"
        )
        await track_msg(state, sent_msg.message_id)
        return
        
    if license_info.get("bound") is False:
        await purge_tracked_msgs(message.chat.id, state)
        sent_msg = await message.answer(
            "❌ *License Binding Blocked.*\n\n"
            "This key is already locked/bound to another Telegram user. Keys are restricted to 1 account only.",
            parse_mode="Markdown"
        )
        await track_msg(state, sent_msg.message_id)
        return

    tier_level = license_info.get("tier", "premium")
    tier_display = "⭐ PREMIUM" if tier_level == "premium" else "🆕 FREE"
    
    await state.update_data(license_key=key, license_tier=tier_level)
    await purge_tracked_msgs(message.chat.id, state)
    
    sent_msg = await message.answer(f"✅ License verified successfully! ({tier_display})\n\n📧 Enter target CarX Street account Email:")
    await track_msg(state, sent_msg.message_id)
    await state.set_state(ResellerStates.awaiting_email)

@dp.message(ResellerStates.awaiting_email)
async def process_email(message: Message, state: FSMContext):
    try:
        await message.delete() # Disappear email input
    except Exception:
        pass
        
    email = message.text.strip()
    if "@" not in email or "." not in email:
        sent_err = await message.answer("❌ Invalid email format. Try again:")
        await track_msg(state, sent_err.message_id)
        return
        
    await state.update_data(target_email=email)
    await purge_tracked_msgs(message.chat.id, state)
    
    sent_msg = await message.answer("🔐 Enter target account Password:")
    await track_msg(state, sent_msg.message_id)
    await state.set_state(ResellerStates.awaiting_password)

@dp.message(ResellerStates.awaiting_password)
async def process_password(message: Message, state: FSMContext):
    try:
        await message.delete() # Disappear password input
    except Exception:
        pass
        
    password = message.text.strip()
    await state.update_data(target_pass=password)
    
    data = await state.get_data()
    tier = data.get("license_tier", "premium")
    await purge_tracked_msgs(message.chat.id, state)
    
    sent_msg = await message.answer(
        "⚙️ *Select Patch Action* ⚙️\n\n"
        "Select an option below, or manually type your selection number.",
        reply_markup=get_patch_menu_keyboard(tier=tier),
        parse_mode="Markdown"
    )
    await track_msg(state, sent_msg.message_id)
    await state.set_state(ResellerStates.awaiting_patch_choice)


# --- SPECIFIC SUB-HANDLERS ---

@dp.callback_query(F.data == "premium_locked", ResellerStates.awaiting_patch_choice)
async def process_premium_locked(callback: CallbackQuery, state: FSMContext):
    await callback.answer("🔒 Feature Locked!", show_alert=True)
    
    kb_back = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
    )
    
    await callback.message.edit_text(
        "❌ *Premium Feature Locked.*\n\n"
        "Your current key is registered on our *Free tier* (only Ban-Safe Pack 2 is active).\n"
        "To purchase Premium access, contact the developer:\n"
        "💬 t.me/ImZhouFann",
        reply_markup=kb_back,
        parse_mode="Markdown"
    )

async def send_paginated_catalog(msg_obj: Message, page: int, state: FSMContext):
    await state.update_data(last_catalog_page=page)
    
    car_db, car_maps = await load_db_data_async()
    if not car_db:
        sent_msg = await msg_obj.answer("❌ Failed to download vehicle database. Try again later.")
        await track_msg(state, sent_msg.message_id)
        return

    car_ids = list(car_db.keys())
    total_pages = math.ceil(len(car_ids) / CARS_PER_PAGE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * CARS_PER_PAGE
    end_idx = start_idx + CARS_PER_PAGE
    page_cars = car_ids[start_idx:end_idx]

    out = f"🏎️ *Vehicle Inventory Catalog* (Page {page}/{total_pages})\n"
    out += "Tidy inline injections with zero exposed Car IDs:\n\n"
    
    keyboard_buttons = []
    
    for car_id in page_cars:
        car_data = car_db[car_id]
        mapping = car_maps.get(car_id, {})
        name = mapping.get("name", car_data.get("__desc_id", f"Car {car_id}"))
        
        out += f"• *{name}* (Price: Reseller Free)\n"
        keyboard_buttons.append([InlineKeyboardButton(text=f"⚡ Inject {name}", callback_data=f"inject_car_id_{car_id}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"catalog_page_{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"catalog_page_{page + 1}"))
    
    keyboard_buttons.append(nav_row)
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Return to Action Menu", callback_data="back_to_menu")])

    kb_paginated = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await purge_tracked_msgs(msg_obj.chat.id, state)
    sent_msg = await msg_obj.answer(out, reply_markup=kb_paginated, parse_mode="Markdown")
    await track_msg(state, sent_msg.message_id)

@dp.callback_query(F.data.startswith("catalog_page_"), ResellerStates.awaiting_patch_choice)
async def process_catalog_page_navigation(callback: CallbackQuery, state: FSMContext):
    page_num = int(callback.data.replace("catalog_page_", ""))
    await callback.answer()
    await send_paginated_catalog(callback.message, page=page_num, state=state)

@dp.callback_query(F.data.startswith("inject_car_id_"), ResellerStates.awaiting_patch_choice)
async def process_inline_car_injection(callback: CallbackQuery, state: FSMContext):
    car_id = callback.data.replace("inject_car_id_", "")
    await callback.answer()
    
    car_db, car_maps = await load_db_data_async()
    car_data = car_db[car_id]
    mapping = car_maps.get(car_id, {})
    name = mapping.get("name", car_data.get("__desc_id", f"Car {car_id}"))
    img_url = mapping.get("image_url")
    
    info = (
        f"🏎️ *Model Preview:* {name}\n"
        f"💰 *Price:* Reseller Free\n"
        f"🛡️ *Injection Safety:* Guaranteed\n\n"
        "Would you like to inject this vehicle into your customer's garage?"
    )
    
    kb_confirm = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Confirm Injection", callback_data=f"confirm_inject_{car_id}")],
            [InlineKeyboardButton(text="⬅️ Back to Car List", callback_data="back_to_catalog")]
        ]
    )
    
    await purge_tracked_msgs(callback.message.chat.id, state)
    
    try:
        if img_url and img_url != "N/A":
            sent_msg = await callback.message.answer_photo(photo=img_url, caption=info, reply_markup=kb_confirm, parse_mode="Markdown")
        else:
            sent_msg = await callback.message.answer(info, reply_markup=kb_confirm, parse_mode="Markdown")
    except Exception:
        sent_msg = await callback.message.answer(info, reply_markup=kb_confirm, parse_mode="Markdown")
        
    await track_msg(state, sent_msg.message_id)

@dp.callback_query(F.data == "back_to_catalog", ResellerStates.awaiting_patch_choice)
async def process_back_to_catalog(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    saved_page = data.get("last_catalog_page", 1)
    await send_paginated_catalog(callback.message, page=saved_page, state=state)

@dp.callback_query(F.data.startswith("confirm_inject_"), ResellerStates.awaiting_patch_choice)
async def process_confirmed_car_injection(callback: CallbackQuery, state: FSMContext):
    car_id = callback.data.replace("confirm_inject_", "")
    await callback.answer()
    
    await purge_tracked_msgs(callback.message.chat.id, state)
    sent_msg = await callback.message.answer(f"⏳ Patcher running... Injecting vehicle.")
    await track_msg(state, sent_msg.message_id)
    
    asyncio.create_task(
        execute_reseller_patch_task(
            sent_msg, state, 'inject_car', target_car_id=car_id
        )
    )

# --- CUSTOM RESOURCE SEQUENCER ---

@dp.message(ResellerStates.awaiting_custom_silver)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_silver)
async def process_silver(event, state: FSMContext):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass
        choice = event.text.strip().lower()
    else:
        await event.answer()
        choice = "skip"
        
    silver = 0.0 if choice == "skip" else float(choice.replace(",", ""))
    await state.update_data(silver_val=silver)
    await purge_tracked_msgs(chat_id, state)
    
    msg = "✨ Enter the amount of Gold to add:\n\n👉 Type 'skip' or press below to skip."
    msg_obj = event if isinstance(event, Message) else event.message
    sent_msg = await msg_obj.answer(msg, reply_markup=get_skip_keyboard())
    await track_msg(state, sent_msg.message_id)
    await state.set_state(ResellerStates.awaiting_custom_gold)

@dp.message(ResellerStates.awaiting_custom_gold)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_gold)
async def process_gold(event, state: FSMContext):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass
        choice = event.text.strip().lower()
    else:
        await event.answer()
        choice = "skip"
        
    gold = 0 if choice == "skip" else int(choice.replace(",", ""))
    await state.update_data(gold_val=gold)
    await purge_tracked_msgs(chat_id, state)
    
    msg = "📈 Enter the amount of XP to add:\n\n👉 Type 'skip' or press below to skip."
    msg_obj = event if isinstance(event, Message) else event.message
    sent_msg = await msg_obj.answer(msg, reply_markup=get_skip_keyboard())
    await track_msg(state, sent_msg.message_id)
    await state.set_state(ResellerStates.awaiting_custom_xp)

@dp.message(ResellerStates.awaiting_custom_xp)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_xp)
async def process_xp(event, state: FSMContext):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass
        choice = event.text.strip().lower()
    else:
        await event.answer()
        choice = "skip"
        
    xp = 0 if choice == "skip" else int(choice.replace(",", ""))
    
    data = await state.get_data()
    tier = data.get("license_tier", "premium")
    msg_obj = event if isinstance(event, Message) else event.message
    await purge_tracked_msgs(chat_id, state)

    # Cancel if all skipped
    if data.get('silver_val', 0.0) == 0.0 and data.get('gold_val', 0) == 0 and xp == 0:
        sent_warn = await msg_obj.answer("⚠️ All fields skipped. Patch cancelled.")
        await asyncio.sleep(2)
        try:
            await sent_warn.delete()
        except Exception:
            pass
            
        sent_msg = await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(tier=tier), parse_mode="Markdown")
        await track_msg(state, sent_msg.message_id)
        await state.set_state(ResellerStates.awaiting_patch_choice)
        return

    sent_msg = await msg_obj.answer("⏳ Patcher queueing with custom values... Execution in progress.")
    await track_msg(state, sent_msg.message_id)
    
    asyncio.create_task(
        execute_reseller_patch_task(
            sent_msg, state, 'custom',
            custom_silver=data['silver_val'],
            custom_gold=data['gold_val'],
            custom_xp=xp
        )
    )

# --- NITRO MANAGEMENT SUB-MENU ---

@dp.message(ResellerStates.awaiting_single_nitro_car_id)
@dp.callback_query(F.data.startswith("nitro_"), ResellerStates.awaiting_patch_choice)
async def process_nitro_options(event, state: FSMContext):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass
        choice = event.text.strip().lower()
    else:
        await event.answer()
        choice = event.data
        
    msg_obj = event if isinstance(event, Message) else event.message
    data = await state.get_data()
    tier = data.get("license_tier", "premium")
    
    if choice in ["nitro_all", "all", "1"]:
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer("⏳ Patcher queueing Max Nitro... Execution in progress.")
        await track_msg(state, sent_msg.message_id)
        asyncio.create_task(execute_reseller_patch_task(sent_msg, state, 'nitro_all'))
        
    elif choice in ["nitro_single", "single", "2"]:
        await purge_tracked_msgs(chat_id, state)
        sent_loading = await msg_obj.answer("⏳ Loading garage profile...")
        await track_msg(state, sent_loading.message_id)
        
        try:
            dev_id = uuid.uuid4().hex
            async with httpx.AsyncClient(http2=True) as client:
                client.headers.update({"User-Agent": "UnityPlayer/6000.0.64f1", "X-Project": "STREET"})
                cont, h = await get_profile_injector(client, data['target_email'], data['target_pass'], dev_id)
                profile = decrypt_payload(cont["compressed_data"])
                garage = profile["cars"]["items"] if ("cars" in profile and "items" in profile["cars"]) else profile
                owned_cars = [k for k in garage.keys() if k.isdigit() and isinstance(garage[k], dict)]
                
                await purge_tracked_msgs(chat_id, state)
                
                if not owned_cars:
                    sent_err = await msg_obj.answer("❌ No cars found in this account.")
                    await asyncio.sleep(3)
                    try:
                        await sent_err.delete()
                    except Exception:
                        pass
                    sent_msg = await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(tier=tier), parse_mode="Markdown")
                    await track_msg(state, sent_msg.message_id)
                    await state.set_state(ResellerStates.awaiting_patch_choice)
                    return
                
                out = "🏎️ *Owned Cars List* 🏎️\n\n"
                for c_id in owned_cars:
                    desc_id = garage[c_id].get("__desc_id", "Unknown")
                    out += f"- ID: `{c_id}` : {desc_id}\n"
                out += "\n👉 Please enter the exact Car ID from the list to apply Max Nitro to:\n\n👉 Type /start to cancel."
                
                sent_msg = await msg_obj.answer(out, parse_mode="Markdown")
                await track_msg(state, sent_msg.message_id)
                await state.set_state(ResellerStates.awaiting_single_nitro_car_id)
                
        except Exception as e:
            await purge_tracked_msgs(chat_id, state)
            sent_err = await msg_obj.answer(f"❌ Failed to load garage: {e}")
            await asyncio.sleep(3)
            try:
                await sent_err.delete()
            except Exception:
                pass
            sent_msg = await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(tier=tier), parse_mode="Markdown")
            await track_msg(state, sent_msg.message_id)
            await state.set_state(ResellerStates.awaiting_patch_choice)
    else:
        car_id = choice
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer(f"⏳ Patcher running... Applying Max Nitro to Car ID {car_id}.")
        await track_msg(state, sent_msg.message_id)
        asyncio.create_task(execute_reseller_patch_task(sent_msg, state, 'nitro_single', target_car_id=car_id))

@dp.callback_query(F.data == "back_to_menu", ResellerStates.awaiting_patch_choice)
async def process_back_btn(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    tier = data.get("license_tier", "premium")
    await callback.message.edit_text(
        "⚙️ *Select Patch Action* ⚙️",
        reply_markup=get_patch_menu_keyboard(tier=tier),
        parse_mode="Markdown"
    )

# --- ACTION MENU ROUTER ---

@dp.message(ResellerStates.awaiting_patch_choice)
@dp.callback_query(ResellerStates.awaiting_patch_choice)
async def process_patch_selection(event, state: FSMContext):
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
    if isinstance(event, Message):
        try:
            await event.delete()
        except Exception:
            pass
        choice = event.text.strip()
    else:
        choice = event.data
        
    msg_obj = event if isinstance(event, Message) else event.message
    
    if isinstance(event, CallbackQuery) and (
        choice.startswith("inject_car_id_") or 
        choice.startswith("catalog_page_") or 
        choice.startswith("confirm_inject_") or 
        choice == "back_to_catalog" or 
        choice == "back_to_menu" or
        choice == "premium_locked"
    ):
        return
        
    if not isinstance(event, Message):
        await event.answer()
        
    choice_map = {
        'patch_safe_1': 'safe_1', '1': 'safe_1',
        'patch_safe_2': 'safe_2', '2': 'safe_2',
        'patch_custom': 'custom', '3': 'custom',
        'patch_nitro_menu': 'nitro_menu', '4': 'nitro_menu',
        'patch_maps': 'maps', '5': 'maps',
        'patch_inject_car': 'inject_car', '6': 'inject_car',
        'switch_account': 'switch_account', '7': 'switch_account',
        'cancel_session': 'cancel_session', '8': 'cancel_session'
    }
    
    action = choice_map.get(choice)
    data = await state.get_data()
    tier = data.get("license_tier", "premium")

    if not action:
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer("❌ Invalid selection. Please select from the menu.", reply_markup=get_patch_menu_keyboard(tier=tier))
        await track_msg(state, sent_msg.message_id)
        return

    premium_actions = ['safe_1', 'custom', 'nitro_menu', 'maps', 'inject_car']
    if action in premium_actions and tier == "free":
        kb_back = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_menu")]
            ]
        )
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer(
            "🔒 *Premium Feature Locked.*\n\n"
            "This action is restricted to accounts with a *Premium License Key*.\n"
            "Your current key is registered on the Free tier.\n\n"
            "To buy Premium access, contact support:\n"
            "💬 t.me/ImZhouFann",
            reply_markup=kb_back,
            parse_mode="Markdown"
        )
        await track_msg(state, sent_msg.message_id)
        return

    if action == "cancel_session":
        await purge_tracked_msgs(chat_id, state)
        await state.clear()
        await msg_obj.answer("🚪 Session ended. Type /start to login again.")
        return
        
    elif action == "switch_account":
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer("📧 Enter the target CarX Street account Email:")
        await track_msg(state, sent_msg.message_id)
        await state.set_state(ResellerStates.awaiting_email)
        return

    elif action == "custom":
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer(
            "💰 Enter the amount of Silver to add:\n\n👉 Type 'skip' or press the button below to skip.",
            reply_markup=get_skip_keyboard()
        )
        await track_msg(state, sent_msg.message_id)
        await state.set_state(ResellerStates.awaiting_custom_silver)
        return

    elif action == "nitro_menu":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚡ Max All Cars", callback_data="nitro_all")],
                [InlineKeyboardButton(text="🚗 Select Single Car", callback_data="nitro_single")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_menu")]
            ]
        )
        await purge_tracked_msgs(chat_id, state)
        sent_msg = await msg_obj.answer(
            "Do you want to apply Max Nitro to ALL cars or a single car?\n\n"
            "👉 Type *1* for Max All Cars\n"
            "👉 Type *2* to Select a Single Car", 
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await track_msg(state, sent_msg.message_id)
        return

    elif action == "inject_car":
        await send_paginated_catalog(msg_obj, page=1, state=state)
        return

    await purge_tracked_msgs(chat_id, state)
    sent_msg = await msg_obj.answer("⏳ Patcher queueing... Execution in progress.")
    await track_msg(state, sent_msg.message_id)
    
    asyncio.create_task(
        execute_reseller_patch_task(
            sent_msg, state, action
        )
    )
