# telegram_bot/states.py
from aiogram.fsm.state import State, StatesGroup

class ResellerStates(StatesGroup):
    awaiting_key = State()
    awaiting_email = State()
    awaiting_password = State()
    awaiting_patch_choice = State()
    awaiting_custom_silver = State()
    awaiting_custom_gold = State()
    awaiting_custom_xp = State()
    awaiting_single_nitro_car_id = State()