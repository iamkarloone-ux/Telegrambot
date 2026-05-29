# telegram_bot/patcher.py
import time
import uuid
import httpx
import json
import base64
import gzip
import orjson
import database as db  # Imports your existing root database.py
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from telegram_bot.config import BASE_AUTH, BASE_SYNC, CAR_LIST_URL, CAR_IMAGES_URL
from telegram_bot.keyboards import get_patch_menu_keyboard
from telegram_bot.states import ResellerStates

# --- SYSTEM UTILITIES ---

async def verify_license_key(key: str, user_id: str) -> dict:
    """Delegates key verification and binding to the core database module."""
    return await db.verify_license_key(key, user_id)

def find_compressed_data(d):
    if isinstance(d, dict):
        if "compressed_data" in d: return d
        for v in d.values():
            res = find_compressed_data(v)
            if res: return res
    elif isinstance(d, list):
        for item in d:
            res = find_compressed_data(item)
            if res: return res
    return None

def decrypt_payload(compressed_str):
    return orjson.loads(gzip.decompress(base64.b64decode(compressed_str[4:])[1:]))

def encrypt_payload_strict_local(profile_dict):
    json_bytes = orjson.dumps(profile_dict)
    return "l84l" + base64.b64encode(b"\x00" + gzip.compress(json_bytes)).decode("utf-8")

async def get_profile_injector(client, email, pwd, dev, carx="", is_target=False):
    payload = {"project": "STREET", "username": email, "password": pwd, "deviceId": dev, "deviceUniqueId": dev}
    r = await client.post(f"{BASE_AUTH}/login", json=payload)
    
    if r.status_code != 200 and is_target:
        reg_r = await client.post(f"{BASE_AUTH}/register", json=payload)
        if reg_r.status_code != 200:
            raise Exception(f"CarX Registration Failed: {reg_r.text}")
        await client.post(f"{BASE_AUTH}/verify", json={"code": "g4a369"})
        r = await client.post(f"{BASE_AUTH}/login", json=payload)
        
    if r.status_code != 200:
        raise Exception(f"CarX Login Failed ({r.status_code}): {r.text}")
    
    data = r.json()
    token = data.get("d", {}).get("token") or data.get("token")
    if not token:
        raise Exception(f"CarX auth failed: no token returned. {r.text}")
    
    if not carx:
        carx = str(data.get("d", {}).get("userId") or data.get("userId") or "")
        
    h = {"Authorization": f"Bearer {token}", "x-token": token, "X-CarX-Id": carx, "X-Device-Id": dev}
    await client.post(f"{BASE_AUTH}/verify", json={"code": "g4a369"}, headers=h)
    
    r_profiles = await client.get(f"{BASE_SYNC}/profiles", headers=h)
    if r_profiles.status_code != 200:
        raise Exception(f"Failed to fetch profiles from CarX: {r_profiles.text}")
        
    env = r_profiles.json()
    cont = find_compressed_data(env)
    if not cont:
        return {"compressed_data": encrypt_payload_strict_local({"resources":{"soft":{"amount":0}}})}, h
    return cont, h

async def load_db_data_async() -> tuple:
    try:
        async with httpx.AsyncClient() as client:
            response_list = await client.get(CAR_LIST_URL)
            if response_list.status_code != 200:
                return {}, {}
            content = response_list.text.strip()
            if not content.startswith("{"): content = "{" + content
            if not content.endswith("}"): content = content + "}"
            raw_car_data = json.loads(content)

            car_maps = {}
            response_maps = await client.get(CAR_IMAGES_URL)
            if response_maps.status_code == 200:
                try:
                    car_maps = response_maps.json()
                except Exception:
                    pass

            car_registry = {}
            def scan(d):
                if isinstance(d, dict):
                    for k, v in d.items():
                        if k.isdigit() and isinstance(v, dict) and ("tuning" in v or "body_part_set" in v):
                            car_registry[k] = v
                        else: scan(v)
                elif isinstance(d, list):
                    for item in d: scan(item)
            
            scan(raw_car_data)
            return car_registry, car_maps
    except Exception as e:
        print(f"Error loading car db: {e}")
        return {}, {}

# --- BACKGROUND PATCH EXECUTION ENGINE ---

async def execute_reseller_patch_task(
    event_message: Message, state: FSMContext, action: str,
    custom_silver: float = 0, custom_gold: int = 0, custom_xp: int = 0, target_car_id: str = ""
):
    try:
        data = await state.get_data()
        email = data['target_email']
        password = data['target_pass']
        dev_id = uuid.uuid4().hex
        
        async with httpx.AsyncClient(http2=True, timeout=60.0) as client:
            client.headers.update({"User-Agent": "UnityPlayer/6000.0.64f1", "X-Project": "STREET"})
            
            cont, h = await get_profile_injector(client, email, password, dev_id)
            profile = decrypt_payload(cont["compressed_data"])
            garage = profile["cars"]["items"] if ("cars" in profile and "items" in profile["cars"]) else profile
            
            summary_actions = []
            res = profile.get("resources", {})
            if "experience" not in res or not isinstance(res["experience"], dict):
                res["experience"] = {"amount": 0}
            current_xp = res["experience"].get("amount", 0)
            
            if action == 'safe_1':
                res.setdefault("soft", {"amount": 0.0})["amount"] += 10000000.0
                res.setdefault("hard", {"amount": 0})["amount"] += 6000
                profile["resources"] = res
                summary_actions.append("💰 Applied Ban-Safe Pack 1 (+10M Silver, +6k Gold)")

            elif action == 'safe_2':
                res.setdefault("soft", {"amount": 0.0})["amount"] += 6000000.0
                res.setdefault("hard", {"amount": 0})["amount"] += 1000
                profile["resources"] = res
                summary_actions.append("💰 Applied Ban-Safe Pack 2 (+6M Silver, +1k Gold)")

            elif action == 'custom':
                if custom_silver:
                    res.setdefault("soft", {"amount": 0.0})["amount"] += float(custom_silver)
                if custom_gold:
                    res.setdefault("hard", {"amount": 0})["amount"] += int(custom_gold)
                if custom_xp:
                    res["experience"]["amount"] = current_xp + int(custom_xp)
                profile["resources"] = res
                summary_actions.append(f"💰 Custom Resources added: +{custom_silver:,.0f} Silver, +{custom_gold:,} Gold, +{custom_xp:,} XP")

            elif action in ['nitro', 'nitro_all']:
                owned_cars = [k for k in garage.keys() if k.isdigit() and isinstance(garage[k], dict)]
                if owned_cars:
                    current_timestamp = int(time.time())
                    for c_id in owned_cars:
                        c_res = garage[c_id].setdefault("consumed_resources", {})
                        nitro = c_res.setdefault("nitro", {})
                        nitro["ts"] = current_timestamp
                        nitro["max_amount"] = 20000000
                        nitro["amount"] = 20000000
                    summary_actions.append(f"⚡ Maxed Nitro on {len(owned_cars)} car(s)")

            elif action == 'nitro_single':
                if target_car_id in garage:
                    current_timestamp = int(time.time())
                    c_res = garage[target_car_id].setdefault("consumed_resources", {})
                    nitro = c_res.setdefault("nitro", {})
                    nitro["ts"] = current_timestamp
                    nitro["max_amount"] = 20000000
                    nitro["amount"] = 20000000
                    car_name = garage[target_car_id].get("__desc_id", f"Car {target_car_id}")
                    summary_actions.append(f"⚡ Maxed Nitro on specific Car: {car_name}")

            elif action == 'maps':
                world_parts = profile.setdefault("game_world_parts", {})
                quests = profile.setdefault("quests", {})
                
                target_regions = ["industrial", "midtown", "suburb", "port", "mountain", "sunset"]
                for r in target_regions:
                    world_parts.setdefault(r, {})["unlocked"] = True
                    
                map_quests = [
                    "move_to_industrial_intro_quest", "move_to_midtown_intro_quest",
                    "move_to_suburb_intro_quest", "move_to_mountain_intro_quest", "move_to_port_intro_quest"
                ]
                for mq in map_quests:
                    quest_node = quests.setdefault(mq, {})
                    quest_node["completed"] = True
                    quest_node["rewarded"] = True
                summary_actions.append("🗺️ Unlocked all map regions and bypassed quests")

            elif action == 'inject_car':
                car_db, _ = await load_db_data_async()
                existing_keys = sorted([int(k) for k in garage.keys() if k.isdigit()])
                last_id = existing_keys[-1] if existing_keys else 1000
                
                pushed_id = str(last_id + 1)
                garage[pushed_id] = garage.pop(str(last_id))
                garage[str(last_id)] = car_db[target_car_id]
                
                car_name = car_db[target_car_id].get("__desc_id", f"Car {target_car_id}")
                # Clean, customer-facing delivery text (removed raw database IDs entirely)
                summary_actions.append(f"🚗 Injected untouched {car_name} into your garage")

            # Encryption and Upload
            current_time = int(time.time())
            profile["lastSyncTime"] = current_time
            cont["compressed_data"] = encrypt_payload_strict_local(profile)
            
            r_up = await client.post(f"{BASE_SYNC}/profiles", json=cont, headers=h)
            if r_up.status_code != 200:
                raise Exception(f"Upload rejected by CarX: {r_up.text}")
                
            success_msg = (
                "🎉 *PATCHING COMPLETED SUCCESSFULLY!* 🎉\n\n"
                f"📧 Account: `{email}`\n"
                "Applied modifications:\n" + "\n".join([f"- {act}" for act in summary_actions]) + "\n\n"
                "Please restart your game completely to view changes! Enjoy! 🔥"
            )
            await event_message.answer(success_msg, parse_mode="Markdown")
            
            # Loopback: Send action menu again automatically
            await event_message.answer(
                "⚙️ *Select Patch Action* ⚙️",
                reply_markup=get_patch_menu_keyboard(),
                parse_mode="Markdown"
            )
            await state.set_state(ResellerStates.awaiting_patch_choice)
            
    except Exception as e:
        await event_message.answer(f"😔 *Patcher Task Failed.*\n\nError details: {e}", parse_mode="Markdown")
        await event_message.answer(
            "⚙️ *Select Patch Action* ⚙️",
            reply_markup=get_patch_menu_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(ResellerStates.awaiting_patch_choice)
