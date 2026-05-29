# telegram_bot/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Environment Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "YOUR_POSTGRES_DATABASE_URL")

# CarX Production API Gateways
BASE_AUTH = "https://carx-id-prod.carx-online.com/api/auth"
BASE_SYNC = "https://street-prod.carx-online.com/str/v1/client"

# Supabase Catalogs
CAR_LIST_URL = "https://rznrrywtfiyehwkfntfj.supabase.co/storage/v1/object/public/profiles/carlist.json"
CAR_IMAGES_URL = "https://rznrrywtfiyehwkfntfj.supabase.co/storage/v1/object/public/profiles/car_images.json"

# UI Settings
CARS_PER_PAGE = 8