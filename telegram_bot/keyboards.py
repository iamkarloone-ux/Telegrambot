# telegram_bot/keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_patch_menu_keyboard():
    """Generates inline keyboard for main action menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1️⃣ Ban-Safe Pack 1 (10M/6K)", callback_data="patch_safe_1")],
            [InlineKeyboardButton(text="2️⃣ Ban-Safe Pack 2 (6M/1K)", callback_data="patch_safe_2")],
            [InlineKeyboardButton(text="3️⃣ Custom Resources", callback_data="patch_custom")],
            [InlineKeyboardButton(text="4️⃣ Max Nitro", callback_data="patch_nitro_menu")],
            [InlineKeyboardButton(text="5️⃣ Map Unlock Only", callback_data="patch_maps")],
            [InlineKeyboardButton(text="6️⃣ Inject Custom Car", callback_data="patch_inject_car")],
            [InlineKeyboardButton(text="👥 Use Different Account", callback_data="switch_account")],
            [InlineKeyboardButton(text="🚪 Cancel Session", callback_data="cancel_session")]
        ]
    )

def get_skip_keyboard():
    """Generates inline skip button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Skip ➡️", callback_data="skip_step")]]
    )