import os
import random
import sqlite3
import shutil
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 基本設定
# ============================================================

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = 977609950706679859
DATABASE_PATH = os.getenv("DATABASE_PATH", "point_shop.db")
BACKUP_DATABASE_PATH = os.getenv(
    "BACKUP_DATABASE_PATH",
    "/data/backups/point_shop_backup.db",
)

JST = timezone(timedelta(hours=9))

FLOAT_REWARD = int(os.getenv("VC_REWARD", "102"))
FLOAT_COOLDOWN_HOURS = int(os.getenv("VC_REWARD_COOLDOWN_HOURS", "24"))

DEFAULT_VC_INTERVAL_MINUTES = int(os.getenv("VC_INTERVAL_MINUTES", "10"))
DEFAULT_VC_POINTS = int(os.getenv("VC_POINTS_PER_INTERVAL", "10"))

PRIVATE_ROOM_CATEGORY_ID = int(os.getenv("PRIVATE_ROOM_CATEGORY_ID", "0") or 0)
PRIVATE_ROOM_DEFAULT_LIMIT = 0  # 0 = 無制限
SECRET_ROLE_ID = 1530733623412654230

DAILY_BONUS_MIN = int(os.getenv("DAILY_BONUS_MIN", "80"))
DAILY_BONUS_MAX = int(os.getenv("DAILY_BONUS_MAX", "150"))
DEFAULT_GACHA_COST = int(os.getenv("POINT_GACHA_COST", "100"))

GAME_ROLES = {
    "VALORANT": int(os.getenv("ROLE_VALORANT_ID", "0") or 0),
    "Apex Legends": int(os.getenv("ROLE_APEX_ID", "0") or 0),
    "Minecraft": int(os.getenv("ROLE_MINECRAFT_ID", "0") or 0),
    "原神": int(os.getenv("ROLE_GENSHIN_ID", "0") or 0),
    "モンスターハンター": int(os.getenv("ROLE_MONHUN_ID", "0") or 0),
}

ITEMS = {
    "topic": {
        "name": "話題ガチャ券",
        "emoji": "🎲",
        "default_price": 300,
        "description": "ランダムな会話のお題を表示します。",
    },
    "private_room": {
        "name": "専用ルーム券",
        "emoji": "🔐",
        "default_price": 1000,
        "description": "自分専用のVCを作成します。",
    },
    "game_role": {
        "name": "ゲームロール券",
        "emoji": "🎮",
        "default_price": 500,
        "description": "ゲームロールを1つ選んで取得できます。",
    },
    "secret": {
        "name": "シークレット券",
        "emoji": "🕯️",
        "default_price": 1500,
        "description": "シークレットロールを取得します。",
    },
    "date": {
        "name": "デート券",
        "emoji": "💗",
        "default_price": 800,
        "description": "指定した相手へデートのお誘いを送れます。",
    },
}

TOPICS = [
    "最近いちばん笑った出来事は？",
    "今いちばん行ってみたい場所は？",
    "理想の休日の過ごし方は？",
    "一週間休みがあったら何をする？",
    "最近ハマっている食べ物は？",
    "おすすめしたい映画・アニメ・ドラマは？",
    "朝型と夜型、どちら？",
    "宝くじで1億円当たったら最初に何をする？",
    "好きな季節とその理由は？",
    "一度は挑戦してみたいことは？",
    "今までで一番印象に残っているゲームは？",
    "タイムマシンなら過去と未来、どちらへ行く？",
    "無人島へ1つだけ持っていくなら？",
    "子どもの頃に好きだった遊びは？",
    "最近買ってよかったものは？",
]

# ============================================================
# データベース
# ============================================================


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.setup()

    def setup(self):
        cur = self.conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                total_xp INTEGER NOT NULL DEFAULT 0,
                last_float_at TEXT,
                last_daily_date TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_key)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS point_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                PRIMARY KEY (guild_id, setting_key)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS private_rooms (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS timed_roles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS vc_reward_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)

        self.conn.commit()

    async def ensure_user(self, user_id: int):
        async with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,),
            )
            self.conn.commit()

    async def get_user(self, user_id: int):
        await self.ensure_user(user_id)
        async with self.lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    async def get_points(self, user_id: int) -> int:
        row = await self.get_user(user_id)
        return int(row["points"])

    async def get_total_xp(self, user_id: int) -> int:
        row = await self.get_user(user_id)
        return int(row["total_xp"])

    async def add_points(self, user_id: int, amount: int, reason: str, add_xp: bool = True):
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()

        async with self.lock:
            xp_amount = max(0, amount) if add_xp else 0
            self.conn.execute(
                "UPDATE users SET points = points + ?, total_xp = total_xp + ? WHERE user_id = ?",
                (amount, xp_amount, user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, reason, now),
            )
            self.conn.commit()

            return self.conn.execute(
                "SELECT points, total_xp FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    async def take_points(self, user_id: int, amount: int, reason: str):
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()

        async with self.lock:
            row = self.conn.execute(
                "SELECT points FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current = int(row["points"])

            if current < amount:
                return False, current

            remaining = current - amount
            self.conn.execute(
                "UPDATE users SET points = ? WHERE user_id = ?",
                (remaining, user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, -amount, reason, now),
            )
            self.conn.commit()
            return True, remaining

    async def claim_float(self, user_id: int):
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc)
        cooldown = timedelta(hours=FLOAT_COOLDOWN_HOURS)

        async with self.lock:
            row = self.conn.execute(
                "SELECT points, total_xp, last_float_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            last_at = (
                datetime.fromisoformat(row["last_float_at"])
                if row["last_float_at"]
                else None
            )

            if last_at and now - last_at < cooldown:
                return False, int(row["points"]), int(row["total_xp"]), last_at + cooldown

            new_points = int(row["points"]) + FLOAT_REWARD
            new_xp = int(row["total_xp"]) + FLOAT_REWARD

            self.conn.execute(
                "UPDATE users SET points = ?, total_xp = ?, last_float_at = ? WHERE user_id = ?",
                (new_points, new_xp, now.isoformat(), user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, FLOAT_REWARD, "浮上ボーナス", now.isoformat()),
            )
            self.conn.commit()

            return True, new_points, new_xp, None

    async def claim_daily(self, user_id: int, amount: int):
        await self.ensure_user(user_id)
        today = datetime.now(JST).date().isoformat()
        now = datetime.now(timezone.utc).isoformat()

        async with self.lock:
            row = self.conn.execute(
                "SELECT points, total_xp, last_daily_date FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            if row["last_daily_date"] == today:
                return False, int(row["points"]), int(row["total_xp"])

            new_points = int(row["points"]) + amount
            new_xp = int(row["total_xp"]) + amount

            self.conn.execute(
                "UPDATE users SET points = ?, total_xp = ?, last_daily_date = ? WHERE user_id = ?",
                (new_points, new_xp, today, user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, "ログインボーナス", now),
            )
            self.conn.commit()

            return True, new_points, new_xp

    async def add_item(self, user_id: int, item_key: str, quantity: int = 1):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO inventory (user_id, item_key, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_key)
                DO UPDATE SET quantity = quantity + excluded.quantity
            """, (user_id, item_key, quantity))
            self.conn.commit()

    async def get_inventory(self, user_id: int):
        async with self.lock:
            rows = self.conn.execute(
                "SELECT item_key, quantity FROM inventory WHERE user_id = ? AND quantity > 0",
                (user_id,),
            ).fetchall()
            return {row["item_key"]: int(row["quantity"]) for row in rows}

    async def get_item_quantity(self, user_id: int, item_key: str) -> int:
        async with self.lock:
            row = self.conn.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND item_key = ?",
                (user_id, item_key),
            ).fetchone()
            return int(row["quantity"]) if row else 0

    async def consume_item(self, user_id: int, item_key: str) -> bool:
        async with self.lock:
            row = self.conn.execute(
                "SELECT quantity FROM inventory WHERE user_id = ? AND item_key = ?",
                (user_id, item_key),
            ).fetchone()

            if not row or int(row["quantity"]) <= 0:
                return False

            self.conn.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_key = ?",
                (user_id, item_key),
            )
            self.conn.commit()
            return True

    async def get_logs(self, user_id: int, limit: int = 10):
        async with self.lock:
            return self.conn.execute(
                """SELECT amount, reason, created_at
                   FROM point_logs
                   WHERE user_id = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()

    async def leaderboard(self, limit: int = 10):
        async with self.lock:
            return self.conn.execute(
                "SELECT user_id, points FROM users ORDER BY points DESC LIMIT ?",
                (limit,),
            ).fetchall()

    async def get_setting(self, guild_id: int, key: str, default: int) -> int:
        async with self.lock:
            row = self.conn.execute(
                "SELECT setting_value FROM settings WHERE guild_id = ? AND setting_key = ?",
                (guild_id, key),
            ).fetchone()

            if not row:
                return default

            try:
                return int(row["setting_value"])
            except (TypeError, ValueError):
                return default

    async def set_setting(self, guild_id: int, key: str, value: int):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO settings (guild_id, setting_key, setting_value)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
            """, (guild_id, key, str(value)))
            self.conn.commit()

    async def save_private_room(self, channel_id: int, guild_id: int, owner_id: int):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO private_rooms (channel_id, guild_id, owner_id)
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id)
                DO UPDATE SET owner_id = excluded.owner_id
            """, (channel_id, guild_id, owner_id))
            self.conn.commit()

    async def delete_private_room(self, channel_id: int):
        async with self.lock:
            self.conn.execute(
                "DELETE FROM private_rooms WHERE channel_id = ?",
                (channel_id,),
            )
            self.conn.commit()

    async def get_private_rooms(self, guild_id: int):
        async with self.lock:
            return self.conn.execute(
                "SELECT channel_id, owner_id FROM private_rooms WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()

    async def add_vc_reward_channel(self, guild_id: int, channel_id: int):
        async with self.lock:
            self.conn.execute(
                """INSERT OR IGNORE INTO vc_reward_channels (guild_id, channel_id)
                   VALUES (?, ?)""",
                (guild_id, channel_id),
            )
            self.conn.commit()

    async def remove_vc_reward_channel(self, guild_id: int, channel_id: int):
        async with self.lock:
            self.conn.execute(
                """DELETE FROM vc_reward_channels
                   WHERE guild_id = ? AND channel_id = ?""",
                (guild_id, channel_id),
            )
            self.conn.commit()

    async def get_vc_reward_channels(self, guild_id: int):
        async with self.lock:
            rows = self.conn.execute(
                """SELECT channel_id
                   FROM vc_reward_channels
                   WHERE guild_id = ?
                   ORDER BY channel_id""",
                (guild_id,),
            ).fetchall()
            return [int(row["channel_id"]) for row in rows]


    async def create_backup(self, backup_path: str):
        """通常コマンドを止めずにSQLiteを安全にバックアップします。"""
        backup_file = Path(backup_path)
        backup_file.parent.mkdir(parents=True, exist_ok=True)

        # commitだけ短時間ロック
        async with self.lock:
            self.conn.commit()

        def _backup():
            source_conn = sqlite3.connect(
                self.path,
                timeout=10,
                check_same_thread=False,
            )
            destination_conn = sqlite3.connect(
                str(backup_file),
                timeout=10,
                check_same_thread=False,
            )
            try:
                source_conn.execute("PRAGMA busy_timeout = 10000")
                destination_conn.execute("PRAGMA busy_timeout = 10000")
                source_conn.backup(
                    destination_conn,
                    pages=128,
                    sleep=0.05,
                )
                destination_conn.commit()
            finally:
                destination_conn.close()
                source_conn.close()

        await asyncio.wait_for(
            asyncio.to_thread(_backup),
            timeout=30,
        )

    async def restore_backup(self, backup_path: str):
        """バックアップDBを現在DBへ復元します。"""
        backup_file = Path(backup_path)
        if not backup_file.exists():
            raise FileNotFoundError(str(backup_file))

        async with self.lock:
            self.conn.commit()
            self.conn.close()

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        shutil.copy2,
                        str(backup_file),
                        self.path,
                    ),
                    timeout=30,
                )
            finally:
                self.conn = sqlite3.connect(
                    self.path,
                    check_same_thread=False,
                    timeout=10,
                )
                self.conn.row_factory = sqlite3.Row
                self.setup()

    async def save_timed_role(
        self,
        guild_id: int,
        user_id: int,
        role_id: int,
        expires_at: datetime,
    ):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO timed_roles (guild_id, user_id, role_id, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, role_id)
                DO UPDATE SET expires_at = excluded.expires_at
            """, (guild_id, user_id, role_id, expires_at.isoformat()))
            self.conn.commit()

    async def get_expired_timed_roles(self, now: datetime):
        async with self.lock:
            return self.conn.execute(
                """SELECT guild_id, user_id, role_id, expires_at
                   FROM timed_roles
                   WHERE expires_at <= ?""",
                (now.isoformat(),),
            ).fetchall()

    async def delete_timed_role(
        self,
        guild_id: int,
        user_id: int,
        role_id: int,
    ):
        async with self.lock:
            self.conn.execute(
                """DELETE FROM timed_roles
                   WHERE guild_id = ? AND user_id = ? AND role_id = ?""",
                (guild_id, user_id, role_id),
            )
            self.conn.commit()


db = Database(DATABASE_PATH)

# ============================================================
# 共通関数
# ============================================================


def level_from_xp(xp: int):
    level = max(1, xp // 500 + 1)
    current = xp % 500
    return level, current, 500


def title_from_level(level: int):
    if level >= 50:
        return "👑 月夜の伝説"
    if level >= 30:
        return "💎 至高の常連"
    if level >= 20:
        return "🌟 輝く人気者"
    if level >= 10:
        return "🌙 夜の住人"
    if level >= 5:
        return "✨ 期待の新人"
    return "🌱 はじめの一歩"


def discord_relative_time(dt: datetime):
    return f"<t:{int(dt.timestamp())}:R>"


async def get_item_price(guild_id: int, item_key: str):
    return await db.get_setting(
        guild_id,
        f"shop_price_{item_key}",
        int(ITEMS[item_key]["default_price"]),
    )


async def get_manager_role_id(guild_id: int):
    return await db.get_setting(guild_id, "manager_role_id", 0)


async def is_bot_manager(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return False

    if interaction.user.guild_permissions.administrator:
        return True

    role_id = await get_manager_role_id(interaction.guild_id or GUILD_ID)
    return bool(role_id and any(role.id == role_id for role in interaction.user.roles))


def manager_only():
    async def predicate(interaction: discord.Interaction):
        return await is_bot_manager(interaction)
    return app_commands.check(predicate)


async def ensure_deferred(interaction: discord.Interaction):
    """まだ応答していない場合だけ処理中メッセージを返します。"""
    if not interaction.response.is_done():
        await interaction.response.send_message(
            "⏳ 処理しています…",
            ephemeral=True,
        )


async def admin_guard(interaction: discord.Interaction) -> bool:
    """ACK後に管理者権限を確認します。"""
    if not isinstance(interaction.user, discord.Member):
        await interaction.followup.send(
            "このコマンドはサーバー内で使用してください。",
            ephemeral=True,
        )
        return False

    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send(
            "このコマンドはDiscord管理者のみ使用できます。",
            ephemeral=True,
        )
        return False

    return True


async def send_log(guild: Optional[discord.Guild], title: str, description: str):
    if guild is None:
        return

    channel_id = await db.get_setting(guild.id, "log_channel_id", 0)
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        await channel.send(
            embed=discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )
        )
    except discord.Forbidden:
        pass


async def notify_level_up(
    member: discord.Member,
    old_xp: int,
    new_xp: int,
    channel: Optional[discord.abc.Messageable],
):
    """レベルアップ通知は無効化されています。"""
    return

async def award_points(
    member: discord.Member,
    amount: int,
    reason: str,
    channel: Optional[discord.abc.Messageable] = None,
):
    old_xp = await db.get_total_xp(member.id)
    result = await db.add_points(member.id, amount, reason, add_xp=True)
    new_xp = int(result["total_xp"])
    await notify_level_up(member, old_xp, new_xp, channel)
    return int(result["points"])


# ============================================================
# 専用ルーム管理
# ============================================================

private_rooms: dict[int, int] = {}
vc_minutes: dict[tuple[int, int], int] = {}


def get_owned_room(guild: discord.Guild, owner_id: int):
    for channel_id, stored_owner_id in list(private_rooms.items()):
        if stored_owner_id != owner_id:
            continue

        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.VoiceChannel):
            return channel

        private_rooms.pop(channel_id, None)

    return None


async def create_private_room(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user

    if guild is None or not isinstance(member, discord.Member):
        return False, "サーバー内で使用してください。"

    existing = get_owned_room(guild, member.id)
    if existing:
        return False, f"すでに専用ルームがあります：{existing.mention}"

    category = guild.get_channel(PRIVATE_ROOM_CATEGORY_ID) if PRIVATE_ROOM_CATEGORY_ID else None
    if not isinstance(category, discord.CategoryChannel):
        category = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        ),
        member: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=True,
            move_members=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=True,
            move_members=True,
        ),
    }

    try:
        channel = await guild.create_voice_channel(
            name=f"🔐｜{member.display_name}の専用ルーム",
            category=category,
            overwrites=overwrites,
            user_limit=PRIVATE_ROOM_DEFAULT_LIMIT,
            reason="専用ルーム券を使用",
        )
    except discord.Forbidden:
        return False, "Botにチャンネル管理権限がありません。"

    private_rooms[channel.id] = member.id
    await db.save_private_room(channel.id, guild.id, member.id)

    if member.voice and member.voice.channel:
        try:
            await member.move_to(channel)
        except discord.Forbidden:
            pass

    await send_log(
        guild,
        "🔐 専用ルーム作成",
        f"所有者：{member.mention}\n部屋：{channel.mention}",
    )

    return True, f"専用ルームを作成しました：{channel.mention}"


# ============================================================
# View
# ============================================================


class FloatView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="浮上する",
        emoji="🌙",
        style=discord.ButtonStyle.primary,
        custom_id="point_shop:float",
    )
    async def float_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        old_xp = await db.get_total_xp(interaction.user.id)
        success, points, new_xp, next_time = await db.claim_float(interaction.user.id)

        if not success:
            await interaction.response.send_message(
                f"今日はすでに受け取り済みです。\n"
                f"次に受け取れるまで：{discord_relative_time(next_time)}\n"
                f"現在：**{points:,} pt**",
                ephemeral=True,
            )
            return

        if isinstance(interaction.user, discord.Member):
            await notify_level_up(
                interaction.user,
                old_xp,
                new_xp,
                interaction.channel,
            )

        embed = discord.Embed(
            title="🌙 浮上確認",
            description=(
                f"{interaction.user.mention} が浮上しました！\n"
                f"**{FLOAT_REWARD}ポイント**を獲得しました。"
            ),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)


class GameRoleSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        options = [
            discord.SelectOption(label=name, value=str(role_id), emoji="🎮")
            for name, role_id in GAME_ROLES.items()
            if role_id
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="ゲームロールが未設定です",
                    value="0",
                )
            ]

        super().__init__(
            placeholder="取得するゲームロールを選択",
            options=options[:25],
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "券を使った本人だけ操作できます。",
                ephemeral=True,
            )
            return

        role_id = int(self.values[0])
        if role_id == 0:
            await interaction.response.send_message(
                "ゲームロールIDが設定されていません。",
                ephemeral=True,
            )
            return

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return

        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                "対象ロールが見つかりません。",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="ゲームロール券")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Botのロールを対象ロールより上にしてください。",
                ephemeral=True,
            )
            return

        consumed = await db.consume_item(interaction.user.id, "game_role")
        if not consumed:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                "ゲームロール券を所持していません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🎮 **{role.name}** を付与しました。",
            ephemeral=True,
        )
        self.view.stop()


class GameRoleView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.add_item(GameRoleSelect(owner_id))


# ============================================================
# Bot
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True


class EarlyAckCommandTree(app_commands.CommandTree):
    EARLY_ACK_COMMANDS = {
        "dbバックアップ",
        "db復元",
        "ポイント追加",
        "ポイント減少",
    }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            command_name = (
                interaction.data.get("name")
                if isinstance(interaction.data, dict)
                else None
            )

            if (
                command_name in self.EARLY_ACK_COMMANDS
                and not interaction.response.is_done()
            ):
                status_text = {
                    "dbバックアップ": "💾 バックアップ処理を開始しました…",
                    "db復元": "♻️ バックアップデータを復元しています…",
                    "ポイント追加": "➕ ポイントを追加しています…",
                    "ポイント減少": "➖ ポイントを減らしています…",
                }.get(command_name, "⏳ 処理しています…")

                await interaction.response.send_message(
                    status_text,
                    ephemeral=True,
                )
                print(
                    f"[EARLY ACK] {command_name} acknowledged",
                    flush=True,
                )
        except discord.HTTPException as error:
            print(
                f"[EARLY ACK ERROR] {type(error).__name__}: {error}",
                flush=True,
            )
            return False

        return True


class PointShopBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(FloatView())

        guild = discord.Object(id=GUILD_ID)

        # 現在のコードにあるコマンドを指定サーバーへコピーします。
        self.tree.clear_commands(guild=guild)
        self.tree.copy_global_to(guild=guild)
        synced_guild = await self.tree.sync(guild=guild)

        # 過去に登録されたグローバルコマンドを削除します。
        # Discord上で同じコマンドが二重表示されるのを防ぎます。
        self.tree.clear_commands(guild=None)
        synced_global = await self.tree.sync()

        print(f"[SYNC] Guild ID: {GUILD_ID}", flush=True)
        print(f"[SYNC] Guild command count: {len(synced_guild)}", flush=True)
        print(
            "[SYNC] Guild commands: "
            + ", ".join(command.name for command in synced_guild),
            flush=True,
        )
        print(
            f"[SYNC] Global command count after cleanup: {len(synced_global)}",
            flush=True,
        )


bot = PointShopBot(
    command_prefix="!",
    intents=intents,
    tree_cls=EarlyAckCommandTree,
)


@bot.event
async def on_ready():
    if not vc_reward_loop.is_running():
        vc_reward_loop.start()

    if not timed_role_cleanup_loop.is_running():
        timed_role_cleanup_loop.start()

    guild = bot.get_guild(GUILD_ID)
    if guild:
        rows = await db.get_private_rooms(guild.id)
        for row in rows:
            channel = guild.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.VoiceChannel):
                private_rooms[channel.id] = int(row["owner_id"])
            else:
                await db.delete_private_room(int(row["channel_id"]))

    print(f"[READY] Logged in as {bot.user}", flush=True)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    if after.channel is not None and before.channel != after.channel:
        old_xp = await db.get_total_xp(member.id)
        success, points, new_xp, _ = await db.claim_float(member.id)

        if success:
            await notify_level_up(member, old_xp, new_xp, after.channel)
            try:
                await member.send(
                    f"🎉 VC浮上ボーナスとして **{FLOAT_REWARD}ポイント**獲得しました。"
                )
            except discord.Forbidden:
                pass

    if before.channel is not None and after.channel is None:
        vc_minutes.pop((member.guild.id, member.id), None)

    if before.channel and before.channel.id in private_rooms:
        if len(before.channel.members) == 0:
            room_id = before.channel.id
            private_rooms.pop(room_id, None)
            await db.delete_private_room(room_id)

            try:
                await before.channel.delete(reason="専用ルームが空になったため削除")
            except (discord.NotFound, discord.Forbidden):
                pass


@tasks.loop(minutes=1)
async def vc_reward_loop():
    active: set[tuple[int, int]] = set()

    for guild in bot.guilds:
        interval = max(
            1,
            await db.get_setting(
                guild.id,
                "vc_interval_minutes",
                DEFAULT_VC_INTERVAL_MINUTES,
            ),
        )
        reward = max(
            0,
            await db.get_setting(
                guild.id,
                "vc_points",
                DEFAULT_VC_POINTS,
            ),
        )

        for channel in guild.voice_channels:
            human_members = [
                member for member in channel.members
                if not member.bot
            ]

            # サーバー内のすべてのVCがポイント対象です。
            # シャベレア等で後から自動作成されたVCも自動で対象になります。
            # Bot以外が2人以上いる時だけポイントを加算します。
            if len(human_members) < 2:
                for member in human_members:
                    vc_minutes.pop((guild.id, member.id), None)
                continue

            for member in human_members:
                key = (guild.id, member.id)
                active.add(key)
                vc_minutes[key] = vc_minutes.get(key, 0) + 1

                while vc_minutes[key] >= interval:
                    vc_minutes[key] -= interval
                    if reward:
                        await award_points(
                            member,
                            reward,
                            (
                                f"VC滞在ボーナス"
                                f"（{channel.name} / {interval}分・2人以上）"
                            ),
                            channel,
                        )

    for key in list(vc_minutes):
        if key not in active:
            vc_minutes.pop(key, None)


@vc_reward_loop.before_loop
async def before_vc_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def timed_role_cleanup_loop():
    """期限切れのシークレットロールを自動で剥奪します。"""
    now = datetime.now(timezone.utc)
    rows = await db.get_expired_timed_roles(now)

    for row in rows:
        guild_id = int(row["guild_id"])
        user_id = int(row["user_id"])
        role_id = int(row["role_id"])

        guild = bot.get_guild(guild_id)
        if guild is None:
            continue

        role = guild.get_role(role_id)
        member = guild.get_member(user_id)

        if role is not None and member is not None:
            try:
                await member.remove_roles(
                    role,
                    reason="シークレット券の有効期限24時間が終了",
                )
                await send_log(
                    guild,
                    "🕯️ シークレットロール自動剥奪",
                    f"対象：{member.mention}\nロール：{role.mention}",
                )
            except discord.Forbidden:
                # 権限不足の場合は次回も再試行
                continue

        await db.delete_timed_role(guild_id, user_id, role_id)


@timed_role_cleanup_loop.before_loop
async def before_timed_role_cleanup_loop():
    await bot.wait_until_ready()


# ============================================================
# ポイント系コマンド
# ============================================================


@bot.tree.command(name="浮上パネル", description="浮上ボタンを設置します")
@manager_only()
async def float_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌙 浮上ポイント",
        description=(
            f"下のボタンを押すと、**{FLOAT_COOLDOWN_HOURS}時間に1回 "
            f"{FLOAT_REWARD}ポイント**獲得できます。"
        ),
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed, view=FloatView())


@bot.tree.command(name="浮上", description="浮上してポイントを受け取ります")
async def float_command(interaction: discord.Interaction):
    old_xp = await db.get_total_xp(interaction.user.id)
    success, points, new_xp, next_time = await db.claim_float(interaction.user.id)

    if not success:
        await interaction.response.send_message(
            f"今日は受け取り済みです。\n次回：{discord_relative_time(next_time)}",
            ephemeral=True,
        )
        return

    if isinstance(interaction.user, discord.Member):
        await notify_level_up(interaction.user, old_xp, new_xp, interaction.channel)

    await interaction.response.send_message(
        f"🌙 {interaction.user.mention} が浮上しました！\n"
        f"**{FLOAT_REWARD}ポイント**獲得しました！"
    )


@bot.tree.command(name="ポイント", description="ポイント残高を確認します")
@app_commands.describe(member="確認するメンバー。省略すると自分")
async def points_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    target = member or interaction.user
    points = await db.get_points(target.id)

    await interaction.response.send_message(
        embed=discord.Embed(
            title="💰 ポイント残高",
            description=f"{target.mention}：**{points:,} pt**",
            color=discord.Color.gold(),
        ),
        ephemeral=True,
    )


@bot.tree.command(name="ログインボーナス", description="1日1回ボーナスを受け取ります")
async def daily_bonus(interaction: discord.Interaction):
    amount = random.randint(DAILY_BONUS_MIN, DAILY_BONUS_MAX)
    old_xp = await db.get_total_xp(interaction.user.id)
    success, points, new_xp = await db.claim_daily(interaction.user.id, amount)

    if not success:
        await interaction.response.send_message(
            "今日はすでに受け取り済みです。",
            ephemeral=True,
        )
        return

    if isinstance(interaction.user, discord.Member):
        await notify_level_up(interaction.user, old_xp, new_xp, interaction.channel)

    await interaction.response.send_message(
        f"🎁 **{amount:,}ポイント**獲得しました！"
    )


@bot.tree.command(name="ポイント履歴", description="最近のポイント履歴を確認します")
@app_commands.describe(member="確認するメンバー。省略すると自分")
async def point_history(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    target = member or interaction.user
    rows = await db.get_logs(target.id)

    if not rows:
        await interaction.response.send_message(
            "履歴はまだありません。",
            ephemeral=True,
        )
        return

    lines = []
    for row in rows:
        dt = datetime.fromisoformat(row["created_at"]).astimezone(JST)
        amount = int(row["amount"])
        sign = "+" if amount >= 0 else ""
        lines.append(
            f"`{dt.strftime('%m/%d %H:%M')}` **{sign}{amount:,}** — {row['reason']}"
        )

    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"📜 {target.display_name}のポイント履歴",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        ),
        ephemeral=True,
    )


@bot.tree.command(name="ランキング", description="ポイントランキングを表示します")
async def ranking(interaction: discord.Interaction):
    rows = await db.leaderboard()
    lines = []
    medals = ["🥇", "🥈", "🥉"]

    for index, row in enumerate(rows, start=1):
        member = interaction.guild.get_member(int(row["user_id"])) if interaction.guild else None
        name = member.display_name if member else f"ID:{row['user_id']}"
        mark = medals[index - 1] if index <= 3 else f"`{index}.`"
        lines.append(f"{mark} **{name}** — {int(row['points']):,} pt")

    await interaction.response.send_message(
        embed=discord.Embed(
            title="🏆 ポイントランキング",
            description="\n".join(lines) if lines else "まだデータがありません。",
            color=discord.Color.gold(),
        )
    )


@bot.tree.command(name="レベル", description="レベルと称号を確認します")
@app_commands.describe(member="確認するメンバー。省略すると自分")
async def level_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    target = member or interaction.user
    xp = await db.get_total_xp(target.id)
    level, current, needed = level_from_xp(xp)

    embed = discord.Embed(
        title=f"📈 {target.display_name}のレベル",
        color=discord.Color.green(),
    )
    embed.add_field(name="レベル", value=f"**Lv.{level}**")
    embed.add_field(name="称号", value=title_from_level(level))
    embed.add_field(name="経験値", value=f"{current:,} / {needed:,} XP", inline=False)
    embed.add_field(name="累計経験値", value=f"{xp:,} XP", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="経験値", description="経験値を確認します")
async def experience(interaction: discord.Interaction):
    xp = await db.get_total_xp(interaction.user.id)
    level, current, needed = level_from_xp(xp)

    await interaction.response.send_message(
        f"✨ **{xp:,} XP**\nLv.{level}の進行：**{current:,}/{needed:,}**",
        ephemeral=True,
    )


@bot.tree.command(name="称号", description="称号一覧を確認します")
async def titles(interaction: discord.Interaction):
    xp = await db.get_total_xp(interaction.user.id)
    level, _, _ = level_from_xp(xp)

    embed = discord.Embed(
        title="🏅 称号",
        description=f"現在：**{title_from_level(level)}**",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="一覧",
        value=(
            "Lv.1 🌱 はじめの一歩\n"
            "Lv.5 ✨ 期待の新人\n"
            "Lv.10 🌙 夜の住人\n"
            "Lv.20 🌟 輝く人気者\n"
            "Lv.30 💎 至高の常連\n"
            "Lv.50 👑 月夜の伝説"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# ショップ
# ============================================================


def item_choices():
    return [
        app_commands.Choice(
            name=f"{data['emoji']} {data['name']}",
            value=key,
        )
        for key, data in ITEMS.items()
    ]


class ShopBuySelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        options = []
        for key, data in ITEMS.items():
            options.append(
                discord.SelectOption(
                    label=data["name"],
                    value=key,
                    emoji=data["emoji"],
                    description=data["description"][:100],
                )
            )

        super().__init__(
            placeholder="購入する商品を選んでください",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id="point_shop:buy_select",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このショップパネルは開いた本人だけ操作できます。",
                ephemeral=True,
            )
            return

        key = self.values[0]
        data = ITEMS[key]
        guild_id = interaction.guild_id or GUILD_ID

        await interaction.response.send_message(
            f"🛍️ **{data['name']}** の購入処理をしています…",
            ephemeral=True,
        )

        try:
            price = await asyncio.wait_for(
                get_item_price(guild_id, key),
                timeout=3,
            )

            success, remaining = await asyncio.wait_for(
                db.take_points(
                    interaction.user.id,
                    price,
                    f"{data['name']}を購入",
                ),
                timeout=3,
            )

            if not success:
                await interaction.edit_original_response(
                    content=(
                        f"❌ ポイント不足です。\n"
                        f"必要：**{price:,} pt**\n"
                        f"所持：**{remaining:,} pt**"
                    )
                )
                return

            await asyncio.wait_for(
                db.add_item(interaction.user.id, key),
                timeout=3,
            )

            await interaction.edit_original_response(
                content=(
                    f"✅ {data['emoji']} **{data['name']}** を購入しました。\n"
                    f"価格：**{price:,} pt**\n"
                    f"残り：**{remaining:,} pt**"
                )
            )

        except asyncio.TimeoutError:
            await interaction.edit_original_response(
                content=(
                    "❌ 購入処理がタイムアウトしました。\n"
                    "少し待ってからもう一度お試しください。"
                )
            )
        except Exception as error:
            print(
                f"[SHOP BUY ERROR] {type(error).__name__}: {error}",
                flush=True,
            )
            try:
                await interaction.edit_original_response(
                    content=f"❌ 購入エラー：`{type(error).__name__}: {error}`"
                )
            except discord.HTTPException:
                pass


class ShopUseSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        options = []
        for key, data in ITEMS.items():
            options.append(
                discord.SelectOption(
                    label=data["name"],
                    value=key,
                    emoji=data["emoji"],
                    description=data["description"][:100],
                )
            )

        super().__init__(
            placeholder="使用する券を選んでください",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id="point_shop:use_select",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このショップパネルは開いた本人だけ操作できます。",
                ephemeral=True,
            )
            return

        key = self.values[0]
        data = ITEMS[key]

        quantity = await db.get_item_quantity(interaction.user.id, key)
        if quantity <= 0:
            await interaction.response.send_message(
                f"{data['name']}を所持していません。",
                ephemeral=True,
            )
            return

        if key == "topic":
            await db.consume_item(interaction.user.id, key)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎲 話題ガチャ",
                    description=f"## {random.choice(TOPICS)}",
                    color=discord.Color.random(),
                )
            )
            return

        if key == "private_room":
            await interaction.response.defer(ephemeral=True)
            success, message = await create_private_room(interaction)
            if not success:
                await interaction.followup.send(message, ephemeral=True)
                return

            await db.consume_item(interaction.user.id, key)
            await interaction.followup.send(message, ephemeral=True)
            return

        if key == "game_role":
            await interaction.response.send_message(
                "付与するゲームロールを選択してください。",
                view=GameRoleView(interaction.user.id),
                ephemeral=True,
            )
            return

        if key == "secret":
            if interaction.guild is None or not isinstance(
                interaction.user,
                discord.Member,
            ):
                await interaction.response.send_message(
                    "サーバー内で使用してください。",
                    ephemeral=True,
                )
                return

            role = interaction.guild.get_role(SECRET_ROLE_ID)
            if role is None:
                await interaction.response.send_message(
                    "シークレットロールが見つかりません。",
                    ephemeral=True,
                )
                return

            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            try:
                await interaction.user.add_roles(
                    role,
                    reason="シークレット券を使用",
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Botのロールをシークレットロールより上にしてください。",
                    ephemeral=True,
                )
                return

            await db.consume_item(interaction.user.id, key)
            await db.save_timed_role(
                interaction.guild.id,
                interaction.user.id,
                role.id,
                expires_at,
            )

            await interaction.response.send_message(
                f"🕯️ {role.mention} を付与しました。\n"
                f"期限まで：<t:{int(expires_at.timestamp())}:R>",
                ephemeral=True,
            )
            return

        if key == "date":
            await interaction.response.send_message(
                "デート券は `/デート券を使う` から相手を選んで使用してください。",
                ephemeral=True,
            )
            return


class ShopView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(ShopBuySelect(owner_id))
        self.add_item(ShopUseSelect(owner_id))

    @discord.ui.button(
        label="所持券を見る",
        emoji="🎫",
        style=discord.ButtonStyle.secondary,
        custom_id="point_shop:inventory_button",
    )
    async def inventory_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このショップパネルは開いた本人だけ操作できます。",
                ephemeral=True,
            )
            return

        inventory_data = await db.get_inventory(interaction.user.id)
        lines = [
            f"{data['emoji']} **{data['name']}**："
            f"{inventory_data.get(key, 0)}枚"
            for key, data in ITEMS.items()
        ]

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎫 所持券一覧",
                description="\n".join(lines),
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )


@bot.tree.command(
    name="ショップ",
    description="商品購入・券一覧・券の使用を行います",
)
async def shop(interaction: discord.Interaction):
    # まず即応答して、Discordの3秒制限を回避
    await interaction.response.defer(ephemeral=True, thinking=True)

    guild_id = interaction.guild_id or GUILD_ID

    try:
        prices = {}

        # 商品価格だけ取得
        for key, data in ITEMS.items():
            try:
                prices[key] = await asyncio.wait_for(
                    get_item_price(guild_id, key),
                    timeout=2,
                )
            except asyncio.TimeoutError:
                prices[key] = int(data["default_price"])

        embed = discord.Embed(
            title="🛍️ ポイントショップ",
            description=(
                "下のメニューから購入する商品を選んでください。\n"
                "券を使う場合は2つ目のメニューを選択してください。\n"
                "所持券はボタンから確認できます。"
            ),
            color=discord.Color.purple(),
        )

        for key, data in ITEMS.items():
            embed.add_field(
                name=(
                    f"{data['emoji']} {data['name']} "
                    f"— **{prices[key]:,} pt**"
                ),
                value=data["description"],
                inline=False,
            )

        await interaction.followup.send(
            embed=embed,
            view=ShopView(interaction.user.id),
            ephemeral=True,
        )

    except Exception as error:
        print(
            f"[SHOP OPEN ERROR] {type(error).__name__}: {error}",
            flush=True,
        )
        try:
            await interaction.followup.send(
                f"❌ ショップ表示エラー：`{type(error).__name__}: {error}`",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


@bot.tree.command(name="デート券を使う", description="相手へデートのお誘いを送ります")
@app_commands.describe(member="誘う相手", message="相手へ送るメッセージ")
async def use_date_ticket(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str = "よかったら一緒にお話ししませんか？",
):
    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message(
            "自分自身やBotには使用できません。",
            ephemeral=True,
        )
        return

    if not await db.consume_item(interaction.user.id, "date"):
        await interaction.response.send_message(
            "デート券を所持していません。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="💗 デートのお誘い",
        description=(
            f"{member.mention}\n"
            f"{interaction.user.mention} さんからお誘いが届きました！\n\n"
            f"**メッセージ**\n{message}"
        ),
        color=discord.Color.magenta(),
    )

    await interaction.response.send_message(
        content=member.mention,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True),
    )

    try:
        await member.send(
            f"{interaction.user.display_name}さんからデート券のお誘いが届きました。\n"
            f"メッセージ：{message}"
        )
    except discord.Forbidden:
        pass


@bot.tree.command(name="ポイントガチャ", description="ポイントを使ってガチャを回します")
async def point_gacha(interaction: discord.Interaction):
    guild_id = interaction.guild_id or GUILD_ID
    cost = await db.get_setting(guild_id, "gacha_cost", DEFAULT_GACHA_COST)

    success, remaining = await db.take_points(
        interaction.user.id,
        cost,
        "ポイントガチャ代",
    )

    if not success:
        await interaction.response.send_message(
            f"ガチャには**{cost:,}ポイント**必要です。",
            ephemeral=True,
        )
        return

    roll = random.randint(1, 100)

    if roll == 1:
        reward, result = cost * 20, "🌈 超大当たり"
    elif roll <= 5:
        reward, result = cost * 5, "💎 大当たり"
    elif roll <= 20:
        reward, result = cost * 2, "✨ 当たり"
    elif roll <= 55:
        reward, result = cost, "🙂 普通"
    else:
        reward, result = 0, "💨 はずれ"

    current = remaining

    if reward and isinstance(interaction.user, discord.Member):
        current = await award_points(
            interaction.user,
            reward,
            f"ポイントガチャ {result}",
            interaction.channel,
        )

    await interaction.response.send_message(
        embed=discord.Embed(
            title="🎰 ポイントガチャ",
            description=(
                f"結果：**{result}**\n"
                f"消費：{cost:,}pt\n"
                f"獲得：{reward:,}pt\n"
                f"現在：{current:,}pt"
            ),
            color=discord.Color.purple(),
        )
    )


# ============================================================
# 専用ルームコマンド
# ============================================================


@bot.tree.command(name="専用ルーム招待", description="専用ルームへメンバーを招待します")
async def room_invite(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message(
            "あなたの専用ルームがありません。",
            ephemeral=True,
        )
        return

    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message(
            "そのメンバーは招待できません。",
            ephemeral=True,
        )
        return

    try:
        await room.set_permissions(
            member,
            view_channel=True,
            connect=True,
            speak=True,
            reason="専用ルーム招待",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Botにチャンネル権限管理の権限がありません。",
            ephemeral=True,
        )
        return

    moved = False
    if member.voice and member.voice.channel:
        try:
            await member.move_to(room)
            moved = True
        except discord.Forbidden:
            pass

    embed = discord.Embed(
        title="🔐 専用ルームへの招待",
        description=(
            f"{member.mention}\n"
            f"{interaction.user.mention} さんから招待されました。\n"
            f"ルーム：{room.mention}\n"
            + ("現在のVCから移動しました。" if moved else "ルームを押して参加できます。")
        ),
        color=discord.Color.green(),
    )

    await interaction.response.send_message(
        content=member.mention,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True),
    )

    try:
        await member.send(
            f"{interaction.user.display_name}さんから専用ルームへ招待されました。\n"
            f"サーバー：{interaction.guild.name}\n"
            f"部屋：{room.name}"
        )
    except discord.Forbidden:
        pass


@bot.tree.command(name="専用ルーム招待解除", description="招待したメンバーの権限を解除します")
async def room_uninvite(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    try:
        await room.set_permissions(member, overwrite=None)

        if member.voice and member.voice.channel == room:
            await member.move_to(None)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Botに必要な権限がありません。",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🚪 {member.mention} の招待を解除しました。",
        ephemeral=True,
    )


@bot.tree.command(name="専用ルーム人数", description="専用ルームの人数上限を変更します")
async def room_limit(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 0, 99],
):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    await room.edit(user_limit=limit)
    text = "無制限" if limit == 0 else f"{limit}人"

    await interaction.response.send_message(
        f"👥 人数上限を**{text}**に変更しました。",
        ephemeral=True,
    )


@bot.tree.command(name="専用ルーム名前", description="専用ルーム名を変更します")
async def room_name(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    clean = name.strip()[:90]
    await room.edit(name=clean)

    await interaction.response.send_message(
        f"✏️ 部屋名を**{clean}**に変更しました。",
        ephemeral=True,
    )


@bot.tree.command(name="専用ルームロック", description="専用ルームを非公開にします")
async def room_lock(interaction: discord.Interaction):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    overwrite = room.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = False
    overwrite.connect = False
    await room.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("🔒 ロックしました。", ephemeral=True)


@bot.tree.command(name="専用ルームアンロック", description="専用ルームを公開します")
async def room_unlock(interaction: discord.Interaction):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    overwrite = room.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = True
    overwrite.connect = True
    await room.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("🔓 公開しました。", ephemeral=True)


@bot.tree.command(name="専用ルーム削除", description="自分の専用ルームを削除します")
async def room_delete(interaction: discord.Interaction):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    room_id = room.id
    room_name = room.name

    await interaction.response.send_message(
        f"🗑️ **{room_name}** を削除します。",
        ephemeral=True,
    )

    private_rooms.pop(room_id, None)
    await db.delete_private_room(room_id)
    await room.delete(reason="所有者が専用ルームを削除")


@bot.tree.command(name="専用ルーム所有者変更", description="専用ルームの所有者を変更します")
async def room_transfer(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if interaction.guild is None:
        return

    room = get_owned_room(interaction.guild, interaction.user.id)
    if room is None:
        await interaction.response.send_message("専用ルームがありません。", ephemeral=True)
        return

    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message(
            "そのメンバーには変更できません。",
            ephemeral=True,
        )
        return

    if get_owned_room(interaction.guild, member.id):
        await interaction.response.send_message(
            "そのメンバーはすでに専用ルームを所有しています。",
            ephemeral=True,
        )
        return

    await room.set_permissions(
        interaction.user,
        view_channel=True,
        connect=True,
        speak=True,
        manage_channels=False,
        move_members=False,
    )
    await room.set_permissions(
        member,
        view_channel=True,
        connect=True,
        speak=True,
        manage_channels=True,
        move_members=True,
    )

    private_rooms[room.id] = member.id
    await db.save_private_room(room.id, interaction.guild.id, member.id)
    await room.edit(name=f"🔐｜{member.display_name}の専用ルーム")

    await interaction.response.send_message(
        f"👑 所有者を{member.mention}へ変更しました。"
    )


@bot.tree.command(name="専用ルーム管理", description="専用ルーム管理コマンド一覧を表示します")
async def room_management(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=discord.Embed(
            title="🔐 専用ルーム管理",
            description=(
                "`/専用ルーム招待`\n"
                "`/専用ルーム招待解除`\n"
                "`/専用ルーム人数`\n"
                "`/専用ルーム名前`\n"
                "`/専用ルームロック`\n"
                "`/専用ルームアンロック`\n"
                "`/専用ルーム削除`\n"
                "`/専用ルーム所有者変更`"
            ),
            color=discord.Color.purple(),
        ),
        ephemeral=True,
    )


# ============================================================
# 管理コマンド
# ============================================================


@bot.tree.command(name="vcポイント設定", description="VCポイントの設定を変更します")
@manager_only()
async def vc_setting(
    interaction: discord.Interaction,
    interval_minutes: app_commands.Range[int, 1, 1440],
    points: app_commands.Range[int, 0, 100000],
):
    if interaction.guild is None:
        return

    await db.set_setting(interaction.guild.id, "vc_interval_minutes", interval_minutes)
    await db.set_setting(interaction.guild.id, "vc_points", points)

    await interaction.response.send_message(
        f"✅ VC滞在ポイントを**{interval_minutes}分ごとに{points:,}pt**へ変更しました。",
        ephemeral=True,
    )


@bot.tree.command(name="vcポイント設定確認", description="VCポイント設定を確認します")
async def vc_setting_view(interaction: discord.Interaction):
    guild_id = interaction.guild_id or GUILD_ID

    interval = await db.get_setting(
        guild_id,
        "vc_interval_minutes",
        DEFAULT_VC_INTERVAL_MINUTES,
    )
    points = await db.get_setting(
        guild_id,
        "vc_points",
        DEFAULT_VC_POINTS,
    )

    hourly = 60 / interval * points

    await interaction.response.send_message(
        f"🎤 **{interval}分ごとに{points:,}pt**\n"
        f"1時間の目安：**約{hourly:,.1f}pt**\n"
        "対象VC：**サーバー内のすべてのVC**\n"
        "※ シャベレア等で自動作成されたVCも対象\n"
        "※ Bot以外が2人以上いる時だけ加算",
        ephemeral=True,
    )


@bot.tree.command(name="ショップ価格設定", description="商品の価格を変更します")
@manager_only()
@app_commands.choices(item=item_choices())
async def shop_price_setting(
    interaction: discord.Interaction,
    item: app_commands.Choice[str],
    price: app_commands.Range[int, 0, 10000000],
):
    if interaction.guild is None:
        return

    await db.set_setting(
        interaction.guild.id,
        f"shop_price_{item.value}",
        price,
    )

    await interaction.response.send_message(
        f"✅ **{ITEMS[item.value]['name']}**を{price:,}ptに変更しました。",
        ephemeral=True,
    )


@bot.tree.command(name="ガチャ設定", description="ポイントガチャ料金を変更します")
@manager_only()
async def gacha_setting(
    interaction: discord.Interaction,
    cost: app_commands.Range[int, 0, 1000000],
):
    if interaction.guild is None:
        return

    await db.set_setting(interaction.guild.id, "gacha_cost", cost)

    await interaction.response.send_message(
        f"✅ ガチャ料金を**{cost:,}pt**に変更しました。",
        ephemeral=True,
    )


@bot.tree.command(name="ログチャンネル設定", description="Botログを送信するチャンネルを設定します")
@manager_only()
async def log_channel_setting(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    if interaction.guild is None:
        return

    await db.set_setting(interaction.guild.id, "log_channel_id", channel.id)

    await interaction.response.send_message(
        f"✅ ログチャンネルを{channel.mention}に設定しました。",
        ephemeral=True,
    )


@bot.tree.command(name="権限設定", description="Bot管理コマンドを使えるロールを設定します")
@app_commands.checks.has_permissions(administrator=True)
async def permission_setting(
    interaction: discord.Interaction,
    role: discord.Role,
):
    if interaction.guild is None:
        return

    await db.set_setting(interaction.guild.id, "manager_role_id", role.id)

    await interaction.response.send_message(
        f"✅ {role.mention}をBot管理ロールに設定しました。",
        ephemeral=True,
    )


@bot.tree.command(name="ポイント追加", description="【管理者限定】メンバーへポイントを追加します")
@app_commands.default_permissions(administrator=True)
async def admin_add_points(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: app_commands.Range[int, 1, 1000000],
    reason: str = "管理者による付与",
):
    await ensure_deferred(interaction)
    if not await admin_guard(interaction):
        return

    total = await award_points(member, amount, reason, interaction.channel)

    await interaction.edit_original_response(
        content=f"✅ {member.mention}へ**{amount:,}pt**追加しました。\n現在：{total:,}pt"
    )


@bot.tree.command(name="ポイント減少", description="【管理者限定】メンバーのポイントを減らします")
@app_commands.default_permissions(administrator=True)
async def admin_remove_points(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: app_commands.Range[int, 1, 1000000],
    reason: str = "管理者による減少",
):
    await ensure_deferred(interaction)
    if not await admin_guard(interaction):
        return

    success, remaining = await db.take_points(member.id, amount, reason)

    if not success:
        await interaction.followup.send(
            f"ポイント不足です。現在：{remaining:,}pt",
            ephemeral=True,
        )
        return

    await interaction.edit_original_response(
        content=f"✅ {member.mention}から**{amount:,}pt**減らしました。\n現在：{remaining:,}pt"
    )


@bot.tree.command(name="設定確認", description="現在のBot設定を確認します")
@manager_only()
async def settings_view(interaction: discord.Interaction):
    if interaction.guild is None:
        return

    guild = interaction.guild
    interval = await db.get_setting(
        guild.id,
        "vc_interval_minutes",
        DEFAULT_VC_INTERVAL_MINUTES,
    )
    vc_points = await db.get_setting(guild.id, "vc_points", DEFAULT_VC_POINTS)
    gacha = await db.get_setting(guild.id, "gacha_cost", DEFAULT_GACHA_COST)
    log_id = await db.get_setting(guild.id, "log_channel_id", 0)
    manager_id = await db.get_setting(guild.id, "manager_role_id", 0)

    log_channel = guild.get_channel(log_id) if log_id else None
    manager_role = guild.get_role(manager_id) if manager_id else None

    embed = discord.Embed(
        title="⚙️ 現在の設定",
        color=discord.Color.dark_purple(),
    )
    embed.add_field(
        name="ポイント",
        value=(
            f"浮上：{FLOAT_REWARD}pt / {FLOAT_COOLDOWN_HOURS}時間\n"
            f"VC：{interval}分ごとに{vc_points}pt\n"
            f"ガチャ：{gacha}pt"
        ),
        inline=False,
    )
    embed.add_field(
        name="管理",
        value=(
            f"ログ：{log_channel.mention if log_channel else '未設定'}\n"
            f"管理ロール：{manager_role.mention if manager_role else '管理者のみ'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="専用ルーム",
        value="初期人数：無制限",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)



@bot.tree.command(
    name="dbバックアップ",
    description="【管理者限定】SQLiteデータを永続バックアップへ保存します",
)
@app_commands.default_permissions(administrator=True)
async def database_backup(interaction: discord.Interaction):
    await ensure_deferred(interaction)
    if not await admin_guard(interaction):
        return

    try:
        backup_file = Path(BACKUP_DATABASE_PATH)
        backup_file.parent.mkdir(parents=True, exist_ok=True)

        print("[DB BACKUP] START", flush=True)
        print(f"[DB BACKUP] DB={DATABASE_PATH}", flush=True)
        print(f"[DB BACKUP] DEST={BACKUP_DATABASE_PATH}", flush=True)
        print(
            f"[DB BACKUP] writable={os.access(backup_file.parent, os.W_OK)}",
            flush=True,
        )

        await db.create_backup(BACKUP_DATABASE_PATH)

        size = backup_file.stat().st_size
        updated_at = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

        print("[DB BACKUP] DONE", flush=True)

        await interaction.followup.send(
            "✅ **SQLiteバックアップが完了しました。**\n"
            f"保存先：`{BACKUP_DATABASE_PATH}`\n"
            f"サイズ：**{size / 1024:.1f} KB**\n"
            f"保存日時：**{updated_at}**",
            ephemeral=True,
        )

    except asyncio.TimeoutError:
        print("[DB BACKUP ERROR] TimeoutError", flush=True)
        try:
            await interaction.followup.send(
                "❌ バックアップが30秒以内に完了しなかったため中止しました。",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

    except Exception as error:
        print(f"[DB BACKUP ERROR] {type(error).__name__}: {error}", flush=True)
        try:
            await interaction.followup.send(
                f"❌ **バックアップに失敗しました。**\n"
                f"`{type(error).__name__}: {error}`",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


@bot.tree.command(
    name="db復元",
    description="【管理者限定】保存済みバックアップを動作中SQLiteへ反映します",
)
@app_commands.default_permissions(administrator=True)
async def database_restore(interaction: discord.Interaction):
    await ensure_deferred(interaction)
    if not await admin_guard(interaction):
        return

    backup_file = Path(BACKUP_DATABASE_PATH)

    if not backup_file.exists():
        await interaction.followup.send(
            "❌ **バックアップデータがありません。**\n"
            f"確認先：`{BACKUP_DATABASE_PATH}`\n"
            "先に `/dbバックアップ` を実行してください。",
            ephemeral=True,
        )
        return

    emergency_path = f"{DATABASE_PATH}.before_restore"

    try:
        print("[DB RESTORE] 1/3 emergency backup", flush=True)
        await db.create_backup(emergency_path)

        print("[DB RESTORE] 2/3 restore", flush=True)
        await db.restore_backup(BACKUP_DATABASE_PATH)

        print("[DB RESTORE] 3/3 done", flush=True)

        await interaction.followup.send(
            "✅ **バックアップデータを復元しました。**\n"
            "ポイント・券・設定などがバックアップ時点へ戻りました。",
            ephemeral=True,
        )

    except asyncio.TimeoutError:
        try:
            await interaction.followup.send(
                "❌ 復元処理が30秒以内に完了しなかったため中止しました。",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

    except Exception as error:
        print(f"[DB RESTORE ERROR] {type(error).__name__}: {error}", flush=True)
        try:
            await interaction.followup.send(
                f"❌ 復元失敗：`{type(error).__name__}: {error}`",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


@bot.tree.command(
    name="dbバックアップ確認",
    description="【管理者限定】保存済みバックアップの状態を確認します",
)
@app_commands.checks.has_permissions(administrator=True)
async def database_backup_status(interaction: discord.Interaction):
    backup_file = Path(BACKUP_DATABASE_PATH)

    if not backup_file.exists():
        await interaction.response.send_message(
            (
                "バックアップはまだありません。\n"
                f"保存予定先：`{BACKUP_DATABASE_PATH}`"
            ),
            ephemeral=True,
        )
        return

    stat = backup_file.stat()
    modified = datetime.fromtimestamp(
        stat.st_mtime,
        tz=timezone.utc,
    ).astimezone(JST)

    await interaction.response.send_message(
        (
            "💾 **バックアップ状態**\n"
            f"保存先：`{BACKUP_DATABASE_PATH}`\n"
            f"サイズ：**{stat.st_size / 1024:.1f} KB**\n"
            f"最終更新：**{modified.strftime('%Y/%m/%d %H:%M:%S')}**"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="管理パネル", description="管理コマンド一覧を表示します")
@manager_only()
async def admin_panel(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=discord.Embed(
            title="⚙️ Bot管理パネル",
            description=(
                "`/vcポイント設定`\n"
                "`/ショップ価格設定`\n"
                "`/ガチャ設定`\n"
                "`/ログチャンネル設定`\n"
                "`/権限設定`\n"
                "`/ポイント追加`\n"
                "`/ポイント減少`\n"
                "`/dbバックアップ`\n"
                "`/db復元`\n"
                "`/dbバックアップ確認`\n"
                "`/浮上パネル`\n"
                "`/設定確認`"
            ),
            color=discord.Color.dark_purple(),
        ),
        ephemeral=True,
    )


@bot.tree.error
async def command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    print(f"[COMMAND ERROR] {repr(error)}", flush=True)

    original = getattr(error, "original", None)

    if isinstance(original, discord.HTTPException):
        if getattr(original, "code", None) in (10062, 40060):
            print(
                "[COMMAND ERROR] Interaction expired/already acknowledged; "
                "no second response will be sent.",
                flush=True,
            )
            return

    message = (
        "このコマンドを使用する権限がありません。"
        if isinstance(error, app_commands.CheckFailure)
        else f"エラーが発生しました：`{type(error).__name__}`"
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException as response_error:
        print(
            f"[COMMAND ERROR RESPONSE FAILED] {response_error}",
            flush=True,
        )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKENが設定されていません。RenderのEnvironmentを確認してください。"
    )

bot.run(TOKEN)
