import os
import random
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


# =========================================================
# 環境変数
# =========================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "point_shop.db")

# 1日1回の浮上ポイント
DAILY_REWARD = int(os.getenv("VC_REWARD", "102"))
DAILY_REWARD_COOLDOWN_HOURS = int(
    os.getenv("VC_REWARD_COOLDOWN_HOURS", "24")
)

# VC滞在ポイントの初期設定
DEFAULT_VC_INTERVAL_MINUTES = int(
    os.getenv("VC_INTERVAL_MINUTES", "10")
)
DEFAULT_VC_POINTS = int(
    os.getenv("VC_POINTS_PER_INTERVAL", "10")
)

# 専用ルームを作成するカテゴリー
PRIVATE_ROOM_CATEGORY_ID = int(
    os.getenv("PRIVATE_ROOM_CATEGORY_ID", "0") or 0
)

# シークレット券で付与するロール
SECRET_ROLE_ID = int(
    os.getenv("SECRET_ROLE_ID", "0") or 0
)

# ショップ価格
TOPIC_TICKET_PRICE = int(
    os.getenv("TOPIC_TICKET_PRICE", "300")
)
PRIVATE_ROOM_TICKET_PRICE = int(
    os.getenv("PRIVATE_ROOM_TICKET_PRICE", "1000")
)
GAME_ROLE_TICKET_PRICE = int(
    os.getenv("GAME_ROLE_TICKET_PRICE", "500")
)
SECRET_TICKET_PRICE = int(
    os.getenv("SECRET_TICKET_PRICE", "1500")
)

# ゲームロール
GAME_ROLES = {
    "VALORANT": int(os.getenv("ROLE_VALORANT_ID", "0") or 0),
    "Apex Legends": int(os.getenv("ROLE_APEX_ID", "0") or 0),
    "Minecraft": int(os.getenv("ROLE_MINECRAFT_ID", "0") or 0),
    "原神": int(os.getenv("ROLE_GENSHIN_ID", "0") or 0),
    "モンスターハンター": int(
        os.getenv("ROLE_MONHUN_ID", "0") or 0
    ),
}


# =========================================================
# ショップ商品
# =========================================================

ITEMS = {
    "topic": {
        "name": "話題ガチャ券",
        "emoji": "🎲",
        "price": TOPIC_TICKET_PRICE,
        "description": "ランダムな会話のお題を表示します。",
    },
    "private_room": {
        "name": "専用ルーム券",
        "emoji": "🔐",
        "price": PRIVATE_ROOM_TICKET_PRICE,
        "description": "自分専用のVCを作成します。",
    },
    "game_role": {
        "name": "ゲームロール券",
        "emoji": "🎮",
        "price": GAME_ROLE_TICKET_PRICE,
        "description": "好きなゲームロールを1つ取得できます。",
    },
    "secret": {
        "name": "シークレット券",
        "emoji": "🕯️",
        "price": SECRET_TICKET_PRICE,
        "description": "特別なシークレットロールを取得します。",
    },
}


TOPICS = [
    "最近いちばん笑った出来事は？",
    "今いちばん行ってみたい場所は？",
    "子どもの頃に好きだった遊びは？",
    "一日だけ別の職業を体験するなら何を選ぶ？",
    "最近ハマっている食べ物や飲み物は？",
    "無人島に1つだけ持っていくなら？",
    "理想の休日の過ごし方は？",
    "一番印象に残っているゲームは？",
    "過去と未来、行けるならどちらへ行く？",
    "得意料理、または作れるようになりたい料理は？",
    "最近買ってよかったものは？",
    "一週間休みが取れたら何をする？",
    "自分を動物に例えるなら？",
    "何歳に戻ってみたい？",
    "おすすめしたい映画・アニメ・ドラマは？",
    "朝型と夜型、どちら？",
    "宝くじで1億円当たったら最初に何をする？",
    "好きな季節と、その理由は？",
    "一度は挑戦してみたいことは？",
    "恋人や友達と一緒に行きたい場所は？",
]


# =========================================================
# データベース
# =========================================================

class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(
            path,
            check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.setup()

    def setup(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                last_daily_reward TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS point_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                guild_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                PRIMARY KEY (guild_id, setting_key)
            )
        """)

        self.conn.commit()

    async def ensure_user(self, user_id: int):
        async with self.lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO users
                (user_id, points)
                VALUES (?, 0)
                """,
                (user_id,)
            )
            self.conn.commit()

    async def get_points(self, user_id: int) -> int:
        await self.ensure_user(user_id)

        async with self.lock:
            row = self.conn.execute(
                """
                SELECT points
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            return int(row["points"])

    async def add_points(
        self,
        user_id: int,
        amount: int,
        reason: str
    ) -> int:
        await self.ensure_user(user_id)

        now = datetime.now(timezone.utc).isoformat()

        async with self.lock:
            self.conn.execute(
                """
                UPDATE users
                SET points = points + ?
                WHERE user_id = ?
                """,
                (amount, user_id)
            )

            self.conn.execute(
                """
                INSERT INTO point_logs
                (user_id, amount, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, amount, reason, now)
            )

            self.conn.commit()

            row = self.conn.execute(
                """
                SELECT points
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            return int(row["points"])

    async def remove_points(
        self,
        user_id: int,
        amount: int,
        reason: str
    ) -> tuple[bool, int]:
        await self.ensure_user(user_id)

        now = datetime.now(timezone.utc).isoformat()

        async with self.lock:
            row = self.conn.execute(
                """
                SELECT points
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            current = int(row["points"])

            if current < amount:
                return False, current

            remaining = current - amount

            self.conn.execute(
                """
                UPDATE users
                SET points = ?
                WHERE user_id = ?
                """,
                (remaining, user_id)
            )

            self.conn.execute(
                """
                INSERT INTO point_logs
                (user_id, amount, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, -amount, reason, now)
            )

            self.conn.commit()

            return True, remaining

    async def daily_reward(
        self,
        user_id: int,
        reason: str
    ) -> tuple[bool, int, Optional[datetime]]:
        await self.ensure_user(user_id)

        now = datetime.now(timezone.utc)
        cooldown = timedelta(
            hours=DAILY_REWARD_COOLDOWN_HOURS
        )

        async with self.lock:
            row = self.conn.execute(
                """
                SELECT points, last_daily_reward
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            last_text = row["last_daily_reward"]

            if last_text:
                last_reward = datetime.fromisoformat(last_text)

                if now - last_reward < cooldown:
                    next_time = last_reward + cooldown

                    return (
                        False,
                        int(row["points"]),
                        next_time
                   
