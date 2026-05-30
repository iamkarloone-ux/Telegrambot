# database.py
import asyncpg
from config import DATABASE_URL
import sys

pool = None

async def init_db():
    global pool
    if not DATABASE_URL:
        print("FATAL ERROR: DATABASE_URL is not found in config.py!")
        sys.exit(1)
        
    pool = await asyncpg.create_pool(
        dsn=DATABASE_URL, 
        ssl="require",
        statement_cache_size=0 # Disables statement caching for PgBouncer compatibility
    )
    
    async with pool.acquire() as conn:
        print("Synchronizing Database Schema...")
        async with conn.transaction():
            # 1. Core Tables
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id TEXT PRIMARY KEY, 
                    gcash_number TEXT, 
                    is_online BOOLEAN DEFAULT FALSE
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mods (
                    id INTEGER PRIMARY KEY, 
                    name TEXT UNIQUE NOT NULL, 
                    description TEXT, 
                    price REAL DEFAULT 0, 
                    image_url TEXT, 
                    default_claims_max INTEGER DEFAULT 3, 
                    src_email TEXT,
                    src_pass TEXT,
                    src_dev_id TEXT,
                    src_carx_id TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY, 
                    mod_id INTEGER NOT NULL, 
                    username TEXT NOT NULL, 
                    password TEXT NOT NULL, 
                    is_available BOOLEAN DEFAULT TRUE, 
                    FOREIGN KEY (mod_id) REFERENCES mods(id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS "references" (
                    ref_number TEXT PRIMARY KEY, 
                    user_id TEXT NOT NULL, 
                    mod_id INTEGER NOT NULL, 
                    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, 
                    claims_used INTEGER DEFAULT 0, 
                    claims_max INTEGER DEFAULT 1, 
                    last_replacement_timestamp TIMESTAMPTZ, 
                    FOREIGN KEY (mod_id) REFERENCES mods(id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS creation_jobs (
                    job_id SERIAL PRIMARY KEY, 
                    user_psid TEXT NOT NULL, 
                    email TEXT NOT NULL, 
                    password TEXT NOT NULL, 
                    mod_id INTEGER NOT NULL, 
                    status VARCHAR(20) DEFAULT 'pending', 
                    result_message TEXT, 
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, 
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    lang TEXT DEFAULT 'en'
                )
            """)
            
            # Reseller License Table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    key TEXT PRIMARY KEY, 
                    expires_at TIMESTAMPTZ NOT NULL, 
                    assigned_to TEXT,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)
            
            # 2. Settings & Users
            await conn.execute("""CREATE TABLE IF NOT EXISTS paused_users (user_id TEXT PRIMARY KEY)""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)""")
            await conn.execute("""
                INSERT INTO app_settings (key, value) VALUES ('maintenance_mode', 'false') 
                ON CONFLICT (key) DO NOTHING
            """)
            await conn.execute("""CREATE TABLE IF NOT EXISTS users (psid TEXT PRIMARY KEY, lang TEXT DEFAULT 'en')""")

        # 3. Graceful Alterations (Database Migrations)
        try:
            await conn.execute('ALTER TABLE mods DROP COLUMN x_coordinate')
            await conn.execute('ALTER TABLE mods DROP COLUMN y_coordinate')
        except asyncpg.exceptions.UndefinedColumnError:
            pass
            
        try:
            await conn.execute('ALTER TABLE mods ADD COLUMN src_email TEXT')
            await conn.execute('ALTER TABLE mods ADD COLUMN src_pass TEXT')
            await conn.execute('ALTER TABLE mods ADD COLUMN src_dev_id TEXT')
            await conn.execute('ALTER TABLE mods ADD COLUMN src_carx_id TEXT')
        except asyncpg.exceptions.DuplicateColumnError:
            pass

        # Automatically alter table to add missing "bound_user_id" column if it doesn't exist
        try:
            await conn.execute('ALTER TABLE licenses ADD COLUMN bound_user_id TEXT')
            print("📡 Added 'bound_user_id' column to existing licenses table.")
        except asyncpg.exceptions.DuplicateColumnError:
            pass

        # Automatically alter table to add missing "tier" column if it doesn't exist
        try:
            await conn.execute("ALTER TABLE licenses ADD COLUMN tier TEXT DEFAULT 'premium'")
            print("📡 Added 'tier' column to existing licenses table.")
        except asyncpg.exceptions.DuplicateColumnError:
            pass

        print('✅ Database synchronized successfully.')

# --- AUTOMATION & JOB TRACKING ---

async def create_account_creation_job(user_psid: str, email: str, password: str, mod_id: int, lang: str = 'en') -> int:
    async with pool.acquire() as conn:
        query = """
            INSERT INTO creation_jobs (user_psid, email, password, mod_id, status, lang) 
            VALUES ($1, $2, $3, $4, 'processing', $5) RETURNING job_id
        """
        return await conn.fetchval(query, user_psid, email, password, mod_id, lang)

async def get_job_by_id(job_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM creation_jobs WHERE job_id = $1', job_id)
        return dict(row) if row else None

async def update_job_status(job_id: int, new_status: str, result_message: str = None):
    async with pool.acquire() as conn:
        query = """
            UPDATE creation_jobs 
            SET status = $1, result_message = $2, updated_at = CURRENT_TIMESTAMP 
            WHERE job_id = $3
        """
        await conn.execute(query, new_status, result_message, job_id)

async def get_creation_jobs():
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT job_id, user_psid, status, result_message FROM creation_jobs ORDER BY created_at DESC LIMIT 15')
        return [dict(row) for row in rows]

# --- ADMIN & APP SETTINGS ---

async def is_admin(user_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM admins WHERE user_id = $1', user_id)
        return dict(row) if row else None

async def get_admin_info():
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM admins LIMIT 1')
        return dict(row) if row else None

async def update_admin_info(user_id: str, gcash_number: str):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO admins (user_id, gcash_number) VALUES ($1, $2) 
            ON CONFLICT (user_id) DO UPDATE SET gcash_number = $2
        """, user_id, gcash_number)

async def set_admin_online_status(is_online: bool):
    async with pool.acquire() as conn:
        await conn.execute('UPDATE admins SET is_online = $1', is_online)

async def get_maintenance_status() -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = 'maintenance_mode'")
        return row['value'] == 'true' if row else False

async def set_maintenance_status(is_maintenance: bool):
    async with pool.acquire() as conn:
        val = 'true' if is_maintenance else 'false'
        await conn.execute("UPDATE app_settings SET value = $1 WHERE key = 'maintenance_mode'", val)

# --- MOD & INVENTORY MANAGEMENT ---

async def get_mods():
    async with pool.acquire() as conn:
        query = """
            SELECT m.id, m.name, m.description, m.price, m.image_url, m.default_claims_max, 
            (SELECT COUNT(*) FROM accounts WHERE mod_id = m.id AND is_available = TRUE) as stock 
            FROM mods m ORDER BY m.id
        """
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]

async def get_mod_by_id(mod_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM mods WHERE id = $1', mod_id)
        return dict(row) if row else None

async def get_mods_by_price(price: float):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM mods WHERE price BETWEEN $1 AND $2', price - 0.01, price + 0.01)
        return [dict(row) for row in rows]

async def add_mod(mod_id: int, name: str, desc: str, price: float, img: str, claims: int):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO mods (id, name, description, price, image_url, default_claims_max) 
            VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT(id) DO NOTHING
        """, mod_id, name, desc, price, img, claims)

async def update_mod_details(mod_id: int, details: dict):
    if not details:
        return
    
    set_clauses = []
    values = []
    for i, (k, v) in enumerate(details.items(), start=1):
        set_clauses.append(f"{k} = ${i}")
        values.append(v)
    
    values.append(mod_id)
    query = f"UPDATE mods SET {', '.join(set_clauses)} WHERE id = ${len(values)}"
    
    async with pool.acquire() as conn:
        await conn.execute(query, *values)

# --- ACCOUNT & REFERENCE MANAGEMENT ---

async def add_bulk_accounts(mod_id: int, accounts: list):
    async with pool.acquire() as conn:
        query = 'INSERT INTO accounts (mod_id, username, password) VALUES ($1, $2, $3)'
        values = [(mod_id, acc['username'], acc['password']) for acc in accounts]
        await conn.executemany(query, values)

async def add_reference(ref: str, user_id: str, mod_id: int) -> int:
    mod = await get_mod_by_id(mod_id)
    if not mod:
        raise Exception(f"Mod {mod_id} not found.")
    
    claims_max = mod.get('default_claims_max') or 1
    
    async with pool.acquire() as conn:
        res = await conn.execute("""
            INSERT INTO "references" (ref_number, user_id, mod_id, claims_max) 
            VALUES ($1, $2, $3, $4) ON CONFLICT (ref_number) DO NOTHING
        """, ref, user_id, mod_id, claims_max)
        
        if res == 'INSERT 0 0':
            raise Exception('Duplicate reference number')
            
    return claims_max

async def add_bulk_references(mod_id: int, ref_numbers: list) -> dict:
    successful_adds = 0
    duplicates = []
    invalids = []
    
    mod = await get_mod_by_id(mod_id)
    if not mod:
        raise Exception(f"Mod {mod_id} not found.")
    
    claims_max = mod.get('default_claims_max') or 1

    async with pool.acquire() as conn:
        for ref in ref_numbers:
            if not isinstance(ref, str) or not ref.isdigit() or len(ref) != 13:
                invalids.append(ref)
                continue
            
            res = await conn.execute("""
                INSERT INTO "references" (ref_number, user_id, mod_id, claims_max) 
                VALUES ($1, 'ADMIN_ADDED', $2, $3) ON CONFLICT (ref_number) DO NOTHING
            """, ref, mod_id, claims_max)
            
            if res == 'INSERT 0 0':
                duplicates.append(ref)
            else:
                successful_adds += 1
                
    return {"successfulAdds": successful_adds, "duplicates": duplicates, "invalids": invalids}

async def get_reference(ref_number: str):
    async with pool.acquire() as conn:
        query = 'SELECT r.*, m.name as mod_name FROM "references" r JOIN mods m ON r.mod_id = m.id WHERE r.ref_number = $1'
        row = await conn.fetchrow(query, ref_number)
        return dict(row) if row else None

async def get_all_references():
    async with pool.acquire() as conn:
        query = 'SELECT r.ref_number, r.user_id, r.claims_used, r.claims_max, m.name as mod_name FROM "references" r JOIN mods m ON r.mod_id = m.id ORDER BY r.timestamp DESC'
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]

async def delete_reference(ref_number: str) -> int:
    async with pool.acquire() as conn:
        res = await conn.execute('DELETE FROM "references" WHERE ref_number = $1', ref_number)
        return int(res.split(' ')[1])

async def get_available_account(mod_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM accounts WHERE mod_id = $1 AND is_available = TRUE LIMIT 1', mod_id)
        return dict(row) if row else None

async def claim_account(account_id: int):
    async with pool.acquire() as conn:
        await conn.execute('UPDATE accounts SET is_available = FALSE WHERE id = $1', account_id)

async def use_claim(ref_number: str):
    async with pool.acquire() as conn:
        await conn.execute('UPDATE "references" SET claims_used = claims_used + 1, last_replacement_timestamp = CURRENT_TIMESTAMP WHERE ref_number = $1', ref_number)

async def update_reference_mod(ref: str, new_mod_id: int):
    async with pool.acquire() as conn:
        await conn.execute('UPDATE "references" SET mod_id = $1 WHERE ref_number = $2', new_mod_id, ref)

async def update_reference_claims(ref: str, used: int, max_claims: int) -> int:
    async with pool.acquire() as conn:
        res = await conn.execute('UPDATE "references" SET claims_used = $1, claims_max = $2 WHERE ref_number = $3', used, max_claims, ref)
        return int(res.split(' ')[1])

async def delete_accounts_by_mod_id(mod_id: int) -> int:
    async with pool.acquire() as conn:
        res = await conn.execute('DELETE FROM accounts WHERE mod_id = $1 AND is_available = TRUE', mod_id)
        return int(res.split(' ')[1])

# --- USER MANAGEMENT ---

async def add_user(psid: str, lang: str = 'en'):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (psid, lang) VALUES ($1, $2) 
            ON CONFLICT (psid) DO UPDATE SET lang = EXCLUDED.lang
        """, psid, lang)

async def get_user(psid: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM users WHERE psid = $1', psid)
        return dict(row) if row else None

async def get_all_user_psids():
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT psid FROM users')
        return [row['psid'] for row in rows]

async def is_user_paused(user_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT user_id FROM paused_users WHERE user_id = $1', user_id)
        return bool(row)

async def pause_user(user_id: str):
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO paused_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING', user_id)

async def resume_user(user_id: str):
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM paused_users WHERE user_id = $1', user_id)

# --- REPORTING ---

async def get_sales_statistics(period: str):
    intervals = {'daily': '1 day', 'weekly': '7 days', 'monthly': '30 days'}
    interval = intervals.get(period)
    if not interval:
        raise Exception('Invalid period.')
    
    query = f"""
        SELECT m.name, COUNT(r.ref_number) as sales_count, SUM(m.price) as total_revenue
        FROM "references" r
        JOIN mods m ON r.mod_id = m.id
        WHERE r.timestamp >= NOW() - INTERVAL '{interval}'
        GROUP BY m.name
        ORDER BY total_revenue DESC;
    """
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]

# --- LICENSE SYSTEM Helper Query Functions ---

async def add_license_key(key: str, days: int, assigned_to: str, tier: str = 'premium'):
    """Adds a reseller license key with a specified tier ('free' or 'premium')."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO licenses (key, expires_at, assigned_to, tier) 
            VALUES ($1, CURRENT_TIMESTAMP + ($2 * INTERVAL '1 day'), $3, $4)
            ON CONFLICT (key) DO UPDATE SET 
                expires_at = CURRENT_TIMESTAMP + ($2 * INTERVAL '1 day'), 
                assigned_to = EXCLUDED.assigned_to, 
                tier = EXCLUDED.tier,
                is_active = TRUE
        """, key, days, assigned_to, tier)

async def get_all_licenses():
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT key, expires_at, assigned_to, is_active, tier,
                   EXTRACT(epoch FROM (expires_at - NOW())) / 86400 AS days_remaining
            FROM licenses
            ORDER BY expires_at DESC
        """)
        return [dict(row) for row in rows]

async def deactivate_license_key(key: str) -> int:
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM licenses WHERE key = $1", key)
        return int(res.split(' ')[1])

async def verify_license_key(key: str, user_id: str) -> dict:
    """Checks key validity and handles automatic one-device/one-user binding."""
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT expires_at, bound_user_id, is_active, tier 
            FROM licenses 
            WHERE key = $1 AND is_active = TRUE AND expires_at > CURRENT_TIMESTAMP
        """, key)
        
        if not row:
            return None # Invalid or expired
            
        bound_id = row["bound_user_id"]
        tier = row["tier"] or "premium"
        
        # 1. If key is unbound, lock it to this user/device ID
        if not bound_id:
            await conn.execute("""
                UPDATE licenses 
                SET bound_user_id = $1 
                WHERE key = $2
            """, user_id, key)
            return {"expires_at": row["expires_at"], "bound": True, "tier": tier}
            
        # 2. If already bound, verify it matches
        if bound_id == user_id:
            return {"expires_at": row["expires_at"], "bound": True, "tier": tier}
            
        # Already bound to another user/device
        return {"expires_at": row["expires_at"], "bound": False, "tier": tier}
