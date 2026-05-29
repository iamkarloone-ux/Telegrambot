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

# Initialize Bot and Dispatcher locally inside handlers to allow clean imports in main.py
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Greets user and asks for reseller license key."""
    await state.clear()
    await message.answer(
        "🔑 *Reseller Patcher Tool* 🔑\n\n"
        "To access this tool, please enter your active License Key.\n\n"
        "Don't have a key? Message the developer to purchase access:\n"
        "💬 m.me/lark.abalunan.1\n\n"
        "👉 Type /start at any time to cancel.",
        parse_mode="Markdown"
    )
    await state.set_state(ResellerStates.awaiting_key)

@dp.message(ResellerStates.awaiting_key)
async def process_key(message: Message, state: FSMContext):
    key = message.text.strip()
    # Pass Telegram user ID to verify and handle license key binding
    license_info = await verify_license_key(key, str(message.from_user.id))
    
    if not license_info:
        await message.answer(
            "❌ *Invalid or Expired License Key.*\n\n"
            "To buy a subscription, contact:\n"
            "💬 m.me/lark.abalunan.1",
            parse_mode="Markdown"
        )
        return
        
    if license_info.get("bound") is False:
        await message.answer(
            "❌ *License Binding Blocked.*\n\n"
            "This key is already locked/bound to another Telegram user. Keys are restricted to 1 account only.",
            parse_mode="Markdown"
        )
        return

    await state.update_data(license_key=key)
    await message.answer("✅ License verified successfully!\n\n📧 Enter target CarX Street account Email:")
    await state.set_state(ResellerStates.awaiting_email)

@dp.message(ResellerStates.awaiting_email)
async def process_email(message: Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email or "." not in email:
        await message.answer("❌ Invalid email format. Try again:")
        return
        
    await state.update_data(target_email=email)
    await message.answer("🔐 Enter target account Password:")
    await state.set_state(ResellerStates.awaiting_password)

@dp.message(ResellerStates.awaiting_password)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    await state.update_data(target_pass=password)
    
    await message.answer(
        "⚙️ *Select Patch Action* ⚙️\n\n"
        "Select an option below, or manually type your selection number.",
        reply_markup=get_patch_menu_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(ResellerStates.awaiting_patch_choice)


# --- 🛑 SPECIFIC SUB-HANDLERS (DEFINED FIRST FOR ROUTING PRIORITY) 🛑 ---

# --- PAGINATED INJECT CATALOG ROUTING ---

async def send_paginated_catalog(msg_obj: Message, page: int, state: FSMContext):
    """Downloads vehicle database and outputs a clean, paginated, safe inline-button list."""
    await state.update_data(last_catalog_page=page)
    
    car_db, car_maps = await load_db_data_async()
    if not car_db:
        await msg_obj.answer("❌ Failed to download vehicle database. Try again later.")
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
        # Falls back cleanly to internal __desc_id string if missing in car_images.json
        name = mapping.get("name", car_data.get("__desc_id", f"Car {car_id}"))
        
        out += f"• *{name}* (Price: Reseller Free)\n"
        keyboard_buttons.append([InlineKeyboardButton(text=f"⚡ Inject {name}", callback_data=f"inject_car_id_{car_id}")])

    # Add Navigation row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"catalog_page_{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"catalog_page_{page + 1}"))
    
    keyboard_buttons.append(nav_row)
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Return to Action Menu", callback_data="back_to_menu")])

    kb_paginated = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await msg_obj.answer(out, reply_markup=kb_paginated, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("catalog_page_"), ResellerStates.awaiting_patch_choice)
async def process_catalog_page_navigation(callback: CallbackQuery, state: FSMContext):
    """Processes previous/next page swaps inside FSM state."""
    page_num = int(callback.data.replace("catalog_page_", ""))
    await callback.answer()
    
    # Edits existing list to swap pages instantly without cluttering the chat history
    await callback.message.delete()
    await send_paginated_catalog(callback.message, page=page_num, state=state)

# --- INLINE PREVIEW CAR INJECTION HANDLERS (ON-DEMAND PREVIEWS) ---

@dp.callback_query(F.data.startswith("inject_car_id_"), ResellerStates.awaiting_patch_choice)
async def process_inline_car_injection(callback: CallbackQuery, state: FSMContext):
    """Renders a single on-demand visual confirmation card, bypassing flood limits."""
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
    
    await callback.message.delete()
    try:
        if img_url and img_url != "N/A":
            await callback.message.answer_photo(photo=img_url, caption=info, reply_markup=kb_confirm, parse_mode="Markdown")
        else:
            await callback.message.answer(info, reply_markup=kb_confirm, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(info, reply_markup=kb_confirm, parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_catalog", ResellerStates.awaiting_patch_choice)
async def process_back_to_catalog(callback: CallbackQuery, state: FSMContext):
    """Reads saved memory index and swaps cleanly back to the exact catalog page."""
    await callback.answer()
    data = await state.get_data()
    saved_page = data.get("last_catalog_page", 1)
    
    await send_paginated_catalog(callback.message, page=saved_page, state=state)
    try:
        await callback.message.delete()
    except Exception:
        pass

@dp.callback_query(F.data.startswith("confirm_inject_"), ResellerStates.awaiting_patch_choice)
async def process_confirmed_car_injection(callback: CallbackQuery, state: FSMContext):
    """Executes the confirmed injection from the card."""
    car_id = callback.data.replace("confirm_inject_", "")
    await callback.answer()
    
    await callback.message.answer(f"⏳ Patcher running... Injecting vehicle.")
    asyncio.create_task(
        execute_reseller_patch_task(
            callback.message, state, 'inject_car', target_car_id=car_id
        )
    )

# --- CUSTOM RESOURCE SEQUENCER ---

@dp.message(ResellerStates.awaiting_custom_silver)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_silver)
async def process_silver(event, state: FSMContext):
    choice = event.text.strip().lower() if isinstance(event, Message) else "skip"
    silver = 0.0 if choice == "skip" else float(choice.replace(",", ""))
    
    await state.update_data(silver_val=silver)
    msg = "✨ Enter the amount of Gold to add:\n\n👉 Type 'skip' or press below to skip."
    
    if isinstance(event, Message):
        await event.answer(msg, reply_markup=get_skip_keyboard())
    else:
        await event.answer()
        await event.message.edit_text(msg, reply_markup=get_skip_keyboard())
        
    await state.set_state(ResellerStates.awaiting_custom_gold)

@dp.message(ResellerStates.awaiting_custom_gold)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_gold)
async def process_gold(event, state: FSMContext):
    choice = event.text.strip().lower() if isinstance(event, Message) else "skip"
    gold = 0 if choice == "skip" else int(choice.replace(",", ""))
    
    await state.update_data(gold_val=gold)
    msg = "📈 Enter the amount of XP to add:\n\n👉 Type 'skip' or press below to skip."
    
    if isinstance(event, Message):
        await event.answer(msg, reply_markup=get_skip_keyboard())
    else:
        await event.answer()
        await event.message.edit_text(msg, reply_markup=get_skip_keyboard())
        
    await state.set_state(ResellerStates.awaiting_custom_xp)

@dp.message(ResellerStates.awaiting_custom_xp)
@dp.callback_query(F.data == "skip_step", ResellerStates.awaiting_custom_xp)
async def process_xp(event, state: FSMContext):
    choice = event.text.strip().lower() if isinstance(event, Message) else "skip"
    xp = 0 if choice == "skip" else int(choice.replace(",", ""))
    
    data = await state.get_data()
    msg_obj = event if isinstance(event, Message) else event.message
    if not isinstance(event, Message):
        await event.answer()

    # Cancel if all skipped
    if data.get('silver_val', 0.0) == 0.0 and data.get('gold_val', 0) == 0 and xp == 0:
        await msg_obj.answer("⚠️ All fields skipped. Patch cancelled.")
        await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(), parse_mode="Markdown")
        await state.set_state(ResellerStates.awaiting_patch_choice)
        return

    await msg_obj.answer("⏳ Patcher queueing with custom values... Execution in progress.")
    asyncio.create_task(
        execute_reseller_patch_task(
            msg_obj, state, 'custom',
            custom_silver=data['silver_val'],
            custom_gold=data['gold_val'],
            custom_xp=xp
        )
    )

# --- NITRO MANAGEMENT SUB-MENU & OPTIONS ---

@dp.message(ResellerStates.awaiting_single_nitro_car_id)
@dp.callback_query(F.data.startswith("nitro_"), ResellerStates.awaiting_patch_choice)
async def process_nitro_options(event, state: FSMContext):
    choice = event.text.strip().lower() if isinstance(event, Message) else event.data
    msg_obj = event if isinstance(event, Message) else event.message
    
    if not isinstance(event, Message):
        await event.answer()
    
    if choice in ["nitro_all", "all", "1"]:
        await msg_obj.answer("⏳ Patcher queueing Max Nitro... Execution in progress.")
        asyncio.create_task(execute_reseller_patch_task(msg_obj, state, 'nitro_all'))
        
    elif choice in ["nitro_single", "single", "2"]:
        await msg_obj.answer("⏳ Loading garage profile...")
        data = await state.get_data()
        
        try:
            dev_id = uuid.uuid4().hex
            async with httpx.AsyncClient(http2=True) as client:
                client.headers.update({"User-Agent": "UnityPlayer/6000.0.64f1", "X-Project": "STREET"})
                cont, h = await get_profile_injector(client, data['target_email'], data['target_pass'], dev_id)
                profile = decrypt_payload(cont["compressed_data"])
                garage = profile["cars"]["items"] if ("cars" in profile and "items" in profile["cars"]) else profile
                owned_cars = [k for k in garage.keys() if k.isdigit() and isinstance(garage[k], dict)]
                
                if not owned_cars:
                    await msg_obj.answer("❌ No cars found in this account.")
                    await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(), parse_mode="Markdown")
                    await state.set_state(ResellerStates.awaiting_patch_choice)
                    return
                
                out = "🏎️ *Owned Cars List* 🏎️\n\n"
                for c_id in owned_cars:
                    desc_id = garage[c_id].get("__desc_id", "Unknown")
                    out += f"- ID: `{c_id}` : {desc_id}\n"
                out += "\n👉 Please enter the exact Car ID from the list to apply Max Nitro to:\n\n👉 Type /start to cancel."
                await msg_obj.answer(out, parse_mode="Markdown")
                await state.set_state(ResellerStates.awaiting_single_nitro_car_id)
                
        except Exception as e:
            await msg_obj.answer(f"❌ Failed to load garage: {e}")
            await msg_obj.answer("⚙️ *Select Patch Action* ⚙️", reply_markup=get_patch_menu_keyboard(), parse_mode="Markdown")
            await state.set_state(ResellerStates.awaiting_patch_choice)
    else:
        # Received single Car ID text
        car_id = choice
        await msg_obj.answer(f"⏳ Patcher running... Applying Max Nitro to Car ID {car_id}.")
        asyncio.create_task(execute_reseller_patch_task(msg_obj, state, 'nitro_single', target_car_id=car_id))

@dp.callback_query(F.data == "back_to_menu", ResellerStates.awaiting_patch_choice)
async def process_back_btn(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "⚙️ *Select Patch Action* ⚙️",
        reply_markup=get_patch_menu_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(ResellerStates.awaiting_patch_choice)

# --- 🚨 CATCH-ALL ACTION MENU ROUTER (DEFINED LAST FOR ROUTING PRIORITY) 🚨 ---

@dp.message(ResellerStates.awaiting_patch_choice)
@dp.callback_query(ResellerStates.awaiting_patch_choice)
async def process_patch_selection(event, state: FSMContext):
    choice = event.text.strip() if isinstance(event, Message) else event.data
    msg_obj = event if isinstance(event, Message) else event.message
    
    # Bypass callback routing collisions (forces pagination/inline-injects to bypass the menu)
    if isinstance(event, CallbackQuery) and (
        choice.startswith("inject_car_id_") or 
        choice.startswith("catalog_page_") or 
        choice.startswith("confirm_inject_") or 
        choice == "back_to_catalog" or 
        choice == "back_to_menu"
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
    if not action:
        await msg_obj.answer("❌ Invalid selection. Please select from the menu.", reply_markup=get_patch_menu_keyboard())
        return

    data = await state.get_data()

    if action == "cancel_session":
        await state.clear()
        await msg_obj.answer("🚪 Session ended. Type /start to login again.")
        return
        
    elif action == "switch_account":
        await msg_obj.answer("📧 Enter the target CarX Street account Email:")
        await state.set_state(ResellerStates.awaiting_email)
        return

    elif action == "custom":
        await msg_obj.answer(
            "💰 Enter the amount of Silver to add:\n\n👉 Type 'skip' or press the button below to skip.",
            reply_markup=get_skip_keyboard()
        )
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
        await msg_obj.answer(
            "Do you want to apply Max Nitro to ALL cars or a single car?\n\n"
            "👉 Type *1* for Max All Cars\n"
            "👉 Type *2* to Select a Single Car", 
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    elif action == "inject_car":
        # Load vehicles and display the interactive Paginated Menu (Page 1)
        await send_paginated_catalog(msg_obj, page=1, state=state)
        return

    # Trigger instant operations (safe_1, safe_2, maps)
    await msg_obj.answer("⏳ Patcher queueing... Execution in progress.")
    asyncio.create_task(
        execute_reseller_patch_task(
            msg_obj, state, action
        )
    )
