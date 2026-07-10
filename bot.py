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

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "point_shop.db")

VC_REWARD = int(os.getenv("VC_REWARD", "102"))
VC_REWARD_COOLDOWN_HOURS = int(os.getenv("VC_REWARD_COOLDOWN_HOURS", "24"))
DEFAULT_VC_INTERVAL_MINUTES = int(os.getenv("VC_INTERVAL_MINUTES", "10"))
DEFAULT_VC_POINTS_PER_INTERVAL = int(os.getenv("VC_POINTS_PER_INTERVAL", "10"))

PRIVATE_ROOM_CATEGORY_ID = int(os.getenv("PRIVATE_ROOM_CATEGORY_ID", "0") or 0)
SECRET_ROLE_ID = int(os.getenv("SECRET_ROLE_ID", "0") or 0)

TOPIC_TICKET_PRICE = int(os.getenv("TOPIC_TICKET_PRICE", "300"))
PRIVATE_ROOM_TICKET_PRICE = int(os.getenv("PRIVATE_ROOM_TICKET_PRICE", "1000"))
GAME_ROLE_TICKET_PRICE = int(os.getenv("GAME_ROLE_TICKET_PRICE", "500"))
SECRET_TICKET_PRICE = int(os.getenv("SECRET_TICKET_PRICE", "1500"))

# ゲームロールを追加・変更する場合は、表示名とロールIDを書き換えてください。
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
        "price": TOPIC_TICKET_PRICE,
        "description": "ランダムな会話のお題を1つ表示します。",
    },
    "private_room": {
        "name": "専用ルーム券",
        "emoji": "🔐",
        "price": PRIVATE_ROOM_TICKET_PRICE,
        "description": "自分専用のボイスチャンネルを作成します。",
    },
    "game_role": {
        "name": "ゲームロール券",
        "emoji": "🎮",
        "price": GAME_ROLE_TICKET_PRICE,
        "description": "ゲームロールを1つ選んで取得できます。",
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
    "一日だけ別の職業を体験できるなら何を選ぶ？",
    "最近ハマっている食べ物や飲み物は？",
    "無人島に1つだけ持っていくなら？",
    "理想の休日の過ごし方は？",
    "今までで一番印象に残っているゲームは？",
    "タイムマシンがあったら過去と未来、どちらへ行く？",
    "得意料理、または作れるようになりたい料理は？",
    "最近買ってよかったものは？",
    "一週間休みが取れたら何をする？",
    "自分を動物に例えるなら？",
    "何歳に戻ってみたい？その理由は？",
    "みんなにおすすめしたい映画・アニメ・ドラマは？",
    "朝型と夜型、どちら？",
    "宝くじで1億円当たったら最初に何をする？",
    "今までで一番恥ずかしかった思い出は？",
    "好きな季節と、その理由は？",
    "一度は挑戦してみたいことは？",
]

JST = timezone(timedelta(hours=9))


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self._setup()

    def _setup(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                last_vc_reward TEXT
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
                "INSERT OR IGNORE INTO users (user_id, points) VALUES (?, 0)",
                (user_id,),
            )
            self.conn.commit()

    async def get_points(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        async with self.lock:
            row = self.conn.execute(
                "SELECT points FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["points"])

    async def add_points(self, user_id: int, amount: int, reason: str) -> int:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with self.lock:
            self.conn.execute(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (amount, user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, reason, now),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT points FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["points"])

    async def take_points(self, user_id: int, amount: int, reason: str) -> tuple[bool, int]:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with self.lock:
            row = self.conn.execute(
                "SELECT points FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current = int(row["points"])
            if current < amount:
                return False, current
            self.conn.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ?",
                (amount, user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, -amount, reason, now),
            )
            self.conn.commit()
            return True, current - amount

    async def reward_vc_if_available(self, user_id: int) -> tuple[bool, int, Optional[datetime]]:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc)
        cooldown = timedelta(hours=VC_REWARD_COOLDOWN_HOURS)

        async with self.lock:
            row = self.conn.execute(
                "SELECT points, last_vc_reward FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            last_text = row["last_vc_reward"]
            last_reward = datetime.fromisoformat(last_text) if last_text else None

            if last_reward and now - last_reward < cooldown:
                next_time = last_reward + cooldown
                return False, int(row["points"]), next_time

            new_points = int(row["points"]) + VC_REWARD
            self.conn.execute(
                "UPDATE users SET points = ?, last_vc_reward = ? WHERE user_id = ?",
                (new_points, now.isoformat(), user_id),
            )
            self.conn.execute(
                "INSERT INTO point_logs (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, VC_REWARD, "VC浮上ボーナス", now.isoformat()),
            )
            self.conn.commit()
            return True, new_points, None

    async def add_item(self, user_id: int, item_key: str, quantity: int = 1):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO inventory (user_id, item_key, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_key)
                DO UPDATE SET quantity = quantity + excluded.quantity
            """, (user_id, item_key, quantity))
            self.conn.commit()

    async def get_inventory(self, user_id: int) -> dict[str, int]:
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

    async def get_setting(self, guild_id: int, key: str, default: int) -> int:
        async with self.lock:
            row = self.conn.execute(
                "SELECT setting_value FROM bot_settings WHERE guild_id = ? AND setting_key = ?",
                (guild_id, key),
            ).fetchone()
            if row is None:
                return default
            try:
                return int(row["setting_value"])
            except (TypeError, ValueError):
                return default

    async def set_setting(self, guild_id: int, key: str, value: int):
        async with self.lock:
            self.conn.execute("""
                INSERT INTO bot_settings (guild_id, setting_key, setting_value)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
            """, (guild_id, key, str(value)))
            self.conn.commit()

    async def leaderboard(self, limit: int = 10):
        async with self.lock:
            return self.conn.execute(
                "SELECT user_id, points FROM users ORDER BY points DESC LIMIT ?",
                (limit,),
            ).fetchall()


intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database(DATABASE_PATH)

# 作成した専用ルームを保存（チャンネルID: 所有者ID）
private_rooms: dict[int, int] = {}

# VCで連続滞在している分数を保存
# キー: (サーバーID, ユーザーID)
vc_presence_minutes: dict[tuple[int, int], int] = {}


def item_choices():
    return [
        app_commands.Choice(
            name=f'{data["emoji"]} {data["name"]}（{data["price"]}pt）',
            value=key,
        )
        for key, data in ITEMS.items()
    ]


def format_discord_time(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


class RiseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="浮上する",
            emoji="🌙",
            style=discord.ButtonStyle.primary,
            custom_id="point_shop_bot:rise_button",
        )

    async def callback(self, interaction: discord.Interaction):
        rewarded, points, next_time = await db.reward_vc_if_available(interaction.user.id)

        if rewarded:
            embed = discord.Embed(
                title="🌙 浮上確認",
                description=(
                    f"{interaction.user.mention} が浮上しました！\n"
                    f"**{VC_REWARD}ポイント**を獲得しました。"
                ),
                color=discord.Color.purple(),
            )
            embed.add_field(name="現在のポイント", value=f"**{points:,} pt**")
            embed.set_footer(text=f"次の浮上ポイントは{VC_REWARD_COOLDOWN_HOURS}時間後に受け取れます。")
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(
            title="⏳ 今日は受け取り済みです",
            description=(
                f"浮上ポイントは**{VC_REWARD_COOLDOWN_HOURS}時間に1回**受け取れます。\n"
                f"次に受け取れるまで：{format_discord_time(next_time)}"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="現在のポイント", value=f"**{points:,} pt**")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RisePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RiseButton())


async def send_vc_reward_notice(member: discord.Member, points: int):
    embed = discord.Embed(
        title="🎉 VC浮上ボーナス",
        description=f"どこかのVCに参加したため、**{VC_REWARD}ポイント**獲得しました！",
        color=discord.Color.purple(),
    )
    embed.add_field(name="現在のポイント", value=f"**{points:,} pt**")
    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass


@bot.event
async def on_ready():
    bot.add_view(RisePanelView())

    if not vc_reward_loop.is_running():
        vc_reward_loop.start()

    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Guild commands synced: {GUILD_ID}")
    else:
        await bot.tree.sync()
        print("Global commands synced.")

    print(f"Logged in as {bot.user} ({bot.user.id if bot.user else 'unknown'})")


@tasks.loop(minutes=1)
async def vc_reward_loop():
    """VC滞在時間を1分単位で数え、設定間隔ごとにポイントを付与します。"""
    active_keys: set[tuple[int, int]] = set()

    for guild in bot.guilds:
        interval = await db.get_setting(
            guild.id,
            "vc_interval_minutes",
            DEFAULT_VC_INTERVAL_MINUTES,
        )
        points = await db.get_setting(
            guild.id,
            "vc_points_per_interval",
            DEFAULT_VC_POINTS_PER_INTERVAL,
        )

        interval = max(1, interval)
        points = max(0, points)

        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot:
                    continue

                key = (guild.id, member.id)
                active_keys.add(key)
                vc_presence_minutes[key] = vc_presence_minutes.get(key, 0) + 1

                while vc_presence_minutes[key] >= interval:
                    vc_presence_minutes[key] -= interval
                    if points > 0:
                        await db.add_points(
                            member.id,
                            points,
                            f"VC滞在ボーナス（{interval}分）",
                        )

    # VCから退出したユーザーの連続滞在時間をリセット
    for key in list(vc_presence_minutes):
        if key not in active_keys:
            vc_presence_minutes.pop(key, None)


@vc_reward_loop.before_loop
async def before_vc_reward_loop():
    await bot.wait_until_ready()


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot:
        return

    # VC未参加 → VC参加、または別VCへ移動した場合
    if after.channel is not None and before.channel != after.channel:
        rewarded, points, _ = await db.reward_vc_if_available(member.id)
        if rewarded:
            await send_vc_reward_notice(member, points)

    # VCから完全に退出した場合は連続滞在時間をリセット
    if before.channel is not None and after.channel is None:
        vc_presence_minutes.pop((member.guild.id, member.id), None)

    # 専用ルームが空になった場合は自動削除
    if before.channel and before.channel.id in private_rooms:
        if len(before.channel.members) == 0:
            channel_id = before.channel.id
            private_rooms.pop(channel_id, None)
            try:
                await before.channel.delete(reason="専用ルームが空になったため自動削除")
            except (discord.NotFound, discord.Forbidden):
                pass


@bot.tree.command(name="浮上パネル", description="浮上ボタンのパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def rise_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌙 浮上ポイント",
        description=(
            f"下の**「浮上する」**ボタンを押すと、"
            f"**{VC_REWARD_COOLDOWN_HOURS}時間に1回 {VC_REWARD}ポイント**獲得できます。\n\n"
            "今日も浮上したことをみんなに知らせよう！"
        ),
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed, view=RisePanelView())


@bot.tree.command(name="浮上", description="浮上して1日1回ポイントを受け取ります")
async def rise(interaction: discord.Interaction):
    rewarded, points, next_time = await db.reward_vc_if_available(interaction.user.id)

    if rewarded:
        embed = discord.Embed(
            title="🌙 浮上確認",
            description=(
                f"{interaction.user.mention} が浮上しました！\n"
                f"**{VC_REWARD}ポイント**を獲得しました。"
            ),
            color=discord.Color.purple(),
        )
        embed.add_field(name="現在のポイント", value=f"**{points:,} pt**")
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.send_message(
        f"今日はすでに浮上ポイントを受け取っています。\n"
        f"次に受け取れるまで：{format_discord_time(next_time)}\n"
        f"現在：**{points:,} pt**",
        ephemeral=True,
    )


@bot.tree.command(name="vcポイント設定", description="【管理者】VC滞在ポイントの間隔と付与量を設定します")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    interval_minutes="何分ごとに付与するか（1～1440分）",
    points="1回に付与するポイント（0～100000）",
)
async def vc_point_setting(
    interaction: discord.Interaction,
    interval_minutes: app_commands.Range[int, 1, 1440],
    points: app_commands.Range[int, 0, 100000],
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "サーバー内で使用してください。",
            ephemeral=True,
        )
        return

    await db.set_setting(interaction.guild.id, "vc_interval_minutes", interval_minutes)
    await db.set_setting(interaction.guild.id, "vc_points_per_interval", points)

    embed = discord.Embed(
        title="✅ VCポイント設定を変更しました",
        description=(
            f"VCにいるメンバーへ、**{interval_minutes}分ごとに {points:,}ポイント**付与します。\n"
            "どのVCでも対象で、1人だけでも加算されます。"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="設定はBotを再起動しても保存されます。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="vcポイント設定確認", description="現在のVC滞在ポイント設定を確認します")
async def vc_point_setting_view(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "サーバー内で使用してください。",
            ephemeral=True,
        )
        return

    interval = await db.get_setting(
        interaction.guild.id,
        "vc_interval_minutes",
        DEFAULT_VC_INTERVAL_MINUTES,
    )
    points = await db.get_setting(
        interaction.guild.id,
        "vc_points_per_interval",
        DEFAULT_VC_POINTS_PER_INTERVAL,
    )

    hourly = (60 / interval) * points
    embed = discord.Embed(
        title="🎤 VCポイント設定",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="付与間隔", value=f"**{interval}分ごと**", inline=True)
    embed.add_field(name="付与ポイント", value=f"**{points:,} pt**", inline=True)
    embed.add_field(
        name="1時間の目安",
        value=f"**約{hourly:,.1f} pt**",
        inline=False,
    )
    embed.set_footer(text="VCから退出すると途中の滞在時間はリセットされます。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ポイント", description="自分または指定メンバーのポイントを確認します")
@app_commands.describe(member="確認したいメンバー。省略すると自分")
async def points(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    value = await db.get_points(target.id)

    embed = discord.Embed(
        title="💰 ポイント残高",
        color=discord.Color.gold(),
    )
    embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
    embed.description = f"現在の所持ポイントは **{value:,} pt** です。"
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ランキング", description="ポイントランキングを表示します")
async def ranking(interaction: discord.Interaction):
    rows = await db.leaderboard(10)
    if not rows:
        await interaction.response.send_message(
            "まだランキングデータがありません。",
            ephemeral=True,
        )
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for index, row in enumerate(rows, start=1):
        user = interaction.guild.get_member(int(row["user_id"])) if interaction.guild else None
        name = user.display_name if user else f"ユーザーID: {row['user_id']}"
        mark = medals[index - 1] if index <= 3 else f"`{index}.`"
        lines.append(f"{mark} **{name}** — {int(row['points']):,} pt")

    embed = discord.Embed(
        title="🏆 ポイントランキング",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ショップ", description="ショップの商品一覧を表示します")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛍️ ポイントショップ",
        description="`/購入` で商品を購入できます。",
        color=discord.Color.purple(),
    )
    for data in ITEMS.values():
        embed.add_field(
            name=f'{data["emoji"]} {data["name"]} — {data["price"]:,} pt',
            value=data["description"],
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="購入", description="ショップの商品を購入します")
@app_commands.choices(item=item_choices())
@app_commands.describe(item="購入する商品")
async def buy(interaction: discord.Interaction, item: app_commands.Choice[str]):
    item_key = item.value
    data = ITEMS[item_key]

    success, remaining = await db.take_points(
        interaction.user.id,
        data["price"],
        f'{data["name"]}を購入',
    )
    if not success:
        await interaction.response.send_message(
            f'ポイントが足りません。\n'
            f'必要：**{data["price"]:,} pt**\n'
            f'所持：**{remaining:,} pt**',
            ephemeral=True,
        )
        return

    await db.add_item(interaction.user.id, item_key, 1)
    quantity = await db.get_item_quantity(interaction.user.id, item_key)

    embed = discord.Embed(
        title="✅ 購入完了",
        description=f'{data["emoji"]} **{data["name"]}** を購入しました。',
        color=discord.Color.green(),
    )
    embed.add_field(name="残りポイント", value=f"{remaining:,} pt")
    embed.add_field(name="現在の所持数", value=f"{quantity}枚")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="券一覧", description="自分が持っている利用券を確認します")
async def inventory(interaction: discord.Interaction):
    inventory_data = await db.get_inventory(interaction.user.id)

    embed = discord.Embed(
        title="🎫 所持している券",
        color=discord.Color.blurple(),
    )
    lines = []
    for key, data in ITEMS.items():
        quantity = inventory_data.get(key, 0)
        lines.append(f'{data["emoji"]} **{data["name"]}**：{quantity}枚')

    embed.description = "\n".join(lines)
    embed.set_footer(text="券は /券を使う から使用できます。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class GameRoleSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        options = []
        for name, role_id in GAME_ROLES.items():
            if role_id:
                options.append(discord.SelectOption(label=name, value=str(role_id), emoji="🎮"))

        if not options:
            options = [
                discord.SelectOption(
                    label="ゲームロールが未設定です",
                    value="0",
                    description=".envにロールIDを設定してください",
                )
            ]

        super().__init__(
            placeholder="取得するゲームロールを選択してください",
            min_values=1,
            max_values=1,
            options=options[:25],
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このメニューは券を使用した本人だけ操作できます。",
                ephemeral=True,
            )
            return

        role_id = int(self.values[0])
        if role_id == 0:
            await interaction.response.send_message(
                "ゲームロールが設定されていません。管理者に連絡してください。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        role = guild.get_role(role_id)
        member = guild.get_member(interaction.user.id)
        if role is None or member is None:
            await interaction.response.send_message(
                "ロールが見つかりません。設定を確認してください。",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="ゲームロール券を使用")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Botのロールが対象ロールより下にあるため付与できません。",
                ephemeral=True,
            )
            return

        consumed = await db.consume_item(interaction.user.id, "game_role")
        if not consumed:
            await member.remove_roles(role, reason="券を所持していなかったため取り消し")
            await interaction.response.send_message(
                "ゲームロール券を所持していません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🎮 **{role.name}** ロールを付与しました！",
            ephemeral=True,
        )
        self.view.stop()


class GameRoleView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.add_item(GameRoleSelect(owner_id))


async def create_private_room(interaction: discord.Interaction) -> tuple[bool, str]:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return False, "サーバー内で使用してください。"

    existing = next(
        (channel_id for channel_id, owner_id in private_rooms.items() if owner_id == interaction.user.id),
        None,
    )
    if existing:
        channel = guild.get_channel(existing)
        if channel:
            return False, f"すでに専用ルームがあります：{channel.mention}"
        private_rooms.pop(existing, None)

    category = guild.get_channel(PRIVATE_ROOM_CATEGORY_ID) if PRIVATE_ROOM_CATEGORY_ID else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        interaction.user: discord.PermissionOverwrite(
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
            name=f"🔐｜{interaction.user.display_name}の専用ルーム",
            category=category,
            overwrites=overwrites,
            reason="専用ルーム券を使用",
        )
    except discord.Forbidden:
        return False, "Botにチャンネル管理権限がありません。"

    private_rooms[channel.id] = interaction.user.id

    # 利用者がすでにVCにいる場合は作成した部屋へ移動
    if interaction.user.voice and interaction.user.voice.channel:
        try:
            await interaction.user.move_to(channel)
        except discord.Forbidden:
            pass

    return True, f"専用ルームを作成しました：{channel.mention}\n空になると自動で削除されます。"


@bot.tree.command(name="券を使う", description="所持している利用券を使用します")
@app_commands.choices(item=[
    app_commands.Choice(name="🎲 話題ガチャ券", value="topic"),
    app_commands.Choice(name="🔐 専用ルーム券", value="private_room"),
    app_commands.Choice(name="🎮 ゲームロール券", value="game_role"),
    app_commands.Choice(name="🕯️ シークレット券", value="secret"),
])
@app_commands.describe(item="使用する券")
async def use_ticket(interaction: discord.Interaction, item: app_commands.Choice[str]):
    item_key = item.value
    quantity = await db.get_item_quantity(interaction.user.id, item_key)
    if quantity <= 0:
        await interaction.response.send_message(
            f'{ITEMS[item_key]["name"]}を所持していません。',
            ephemeral=True,
        )
        return

    if item_key == "topic":
        consumed = await db.consume_item(interaction.user.id, item_key)
        if not consumed:
            await interaction.response.send_message("券を所持していません。", ephemeral=True)
            return

        topic = random.choice(TOPICS)
        embed = discord.Embed(
            title="🎲 話題ガチャ",
            description=f"## {topic}",
            color=discord.Color.random(),
        )
        embed.set_footer(text="みんなで話してみよう！")
        await interaction.response.send_message(embed=embed)
        return

    if item_key == "private_room":
        await interaction.response.defer(ephemeral=True)
        created, message = await create_private_room(interaction)
        if not created:
            await interaction.followup.send(message, ephemeral=True)
            return

        consumed = await db.consume_item(interaction.user.id, item_key)
        if not consumed:
            await interaction.followup.send(
                "券の消費に失敗しました。管理者に連絡してください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(message, ephemeral=True)
        return

    if item_key == "game_role":
        view = GameRoleView(interaction.user.id)
        await interaction.response.send_message(
            "取得したいゲームロールを選んでください。\n"
            "ロール付与に成功した時点で券を1枚消費します。",
            view=view,
            ephemeral=True,
        )
        return

    if item_key == "secret":
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        role = guild.get_role(SECRET_ROLE_ID) if SECRET_ROLE_ID else None
        if role is None:
            await interaction.response.send_message(
                "シークレットロールが設定されていません。管理者に連絡してください。",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="シークレット券を使用")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Botのロールがシークレットロールより下にあるため付与できません。",
                ephemeral=True,
            )
            return

        consumed = await db.consume_item(interaction.user.id, item_key)
        if not consumed:
            await interaction.user.remove_roles(role, reason="券を所持していなかったため取り消し")
            await interaction.response.send_message("券を所持していません。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"🕯️ シークレット特典として **{role.name}** ロールを付与しました。",
            ephemeral=True,
        )


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and member.guild_permissions.administrator
    return app_commands.check(predicate)


@bot.tree.command(name="ポイント追加", description="【管理者】メンバーにポイントを追加します")
@admin_only()
@app_commands.describe(member="対象メンバー", amount="追加するポイント", reason="理由")
async def add_points_admin(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: app_commands.Range[int, 1, 1000000],
    reason: str = "管理者による付与",
):
    total = await db.add_points(member.id, amount, reason)
    await interaction.response.send_message(
        f"✅ {member.mention} に **{amount:,} pt** 追加しました。\n"
        f"現在：**{total:,} pt**",
        ephemeral=True,
    )


@bot.tree.command(name="ポイント減少", description="【管理者】メンバーのポイントを減らします")
@admin_only()
@app_commands.describe(member="対象メンバー", amount="減らすポイント", reason="理由")
async def remove_points_admin(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: app_commands.Range[int, 1, 1000000],
    reason: str = "管理者による減少",
):
    success, remaining = await db.take_points(member.id, amount, reason)
    if not success:
        await interaction.response.send_message(
            f"❌ {member.display_name} のポイントが不足しています。\n"
            f"現在：**{remaining:,} pt**",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"✅ {member.mention} から **{amount:,} pt** 減らしました。\n"
        f"現在：**{remaining:,} pt**",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = "このコマンドは管理者だけ使用できます。"
    else:
        message = f"エラーが発生しました：`{type(error).__name__}`"
        print(f"Command error: {repr(error)}")

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if not TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKENが設定されていません。.envファイルを確認してください。"
    )

bot.run(TOKEN)
