"""
GitHub Notify Bot v3 — now with a real database!
-------------------------------------------------
The watching list is saved in a Postgres database (Neon),
so it survives restarts, redeploys, and naps. 🧠

Admin commands:
  !notifyhere owner/repo   -> send that repo's updates to this channel
  !stopnotify owner/repo   -> stop watching a repo
  !watching                -> list watched repos (anyone can use)
"""

import os
import asyncpg
import discord
from discord.ext import commands
from aiohttp import web

# ---------- SETTINGS ----------
TOKEN = os.environ["DISCORD_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]   # the Neon connection string
PORT = int(os.environ.get("PORT", 8080))
GITHUB_GREEN = 0x2ECC40

# ---------- THE DATABASE ----------
# 'watched' is our quick copy in memory. The database is the real save file.
watched = {}          # {"owner/repo": channel_id}
db_pool = None        # our connection to the database

async def setup_database():
    """Connect to the database, create our table, and load the saved list."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        # Create the table if it's our first time (like making a new save file)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS watched_repos (
                repo TEXT PRIMARY KEY,
                channel_id BIGINT NOT NULL
            )
        """)
        # Load everything that was saved before
        rows = await conn.fetch("SELECT repo, channel_id FROM watched_repos")
        for row in rows:
            watched[row["repo"]] = row["channel_id"]
    print(f"Database connected! Loaded {len(watched)} repos.")

async def db_save_repo(repo, channel_id):
    """Save or update one repo in the database."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO watched_repos (repo, channel_id)
            VALUES ($1, $2)
            ON CONFLICT (repo) DO UPDATE SET channel_id = $2
        """, repo, channel_id)

async def db_delete_repo(repo):
    """Remove one repo from the database."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM watched_repos WHERE repo = $1", repo)

# ---------- THE DISCORD BOT ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}! Watching {len(watched)} repos.")

@bot.command()
@commands.has_permissions(administrator=True)
async def notifyhere(ctx, repo: str):
    repo = repo.lower().strip()
    if "/" not in repo:
        await ctx.send("Please write it like this: `!notifyhere owner/repo-name`")
        return
    watched[repo] = ctx.channel.id
    await db_save_repo(repo, ctx.channel.id)      # saved forever now! 💾
    await ctx.send(
        f"✅ Got it! Updates for **{repo}** will be posted in this channel.\n"
        f"Now add a webhook in that repo's GitHub settings."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def stopnotify(ctx, repo: str):
    repo = repo.lower().strip()
    if watched.pop(repo, None) is not None:
        await db_delete_repo(repo)
        await ctx.send(f"🛑 Okay, no more updates for **{repo}**.")
    else:
        await ctx.send("I wasn't watching that repo!")

@bot.command()
async def watching(ctx):
    if not watched:
        await ctx.send("I'm not watching any repos yet!")
        return
    lines = [f"• **{repo}** → <#{channel_id}>" for repo, channel_id in watched.items()]
    await ctx.send("I'm watching:\n" + "\n".join(lines))

# ---------- BUILDING THE FANCY EMBED ----------
def build_push_embed(data):
    repo = data.get("repository", {})
    repo_name = repo.get("full_name", "unknown/repo")
    repo_url = repo.get("html_url", "https://github.com")
    branch = data.get("ref", "refs/heads/?").split("/")[-1]

    commits = data.get("commits", [])
    count = len(commits)
    plural = "s" if count != 1 else ""

    sender = data.get("sender", {})
    pusher_name = data.get("pusher", {}).get("name", "someone")
    avatar = sender.get("avatar_url", "")

    embed = discord.Embed(
        title=f"[{repo_name}:{branch}] {count} new commit{plural}",
        url=data.get("compare", repo_url),
        color=GITHUB_GREEN,
    )
    embed.set_author(name=pusher_name, icon_url=avatar)

    lines = []
    for c in commits[:10]:
        short_hash = c.get("id", "0000000")[:7]
        commit_url = c.get("url", repo_url)
        message = c.get("message", "").split("\n")[0]
        if len(message) > 60:
            message = message[:57] + "..."
        author = c.get("author", {}).get("username") or c.get("author", {}).get("name", "?")
        lines.append(f"[`{short_hash}`]({commit_url}) {message} - {author}")

    if count > 10:
        lines.append(f"...and {count - 10} more!")

    embed.description = "\n".join(lines) if lines else "*No commit details*"
    return embed

# ---------- THE WEBHOOK SERVER ----------
async def github_webhook(request):
    data = await request.json()
    repo_name = data.get("repository", {}).get("full_name", "").lower()

    channel_id = watched.get(repo_name)
    if channel_id is None:
        return web.Response(text="Not watching this repo", status=200)

    channel = bot.get_channel(channel_id)
    if channel is None:
        return web.Response(text="Channel not found", status=200)

    event = request.headers.get("X-GitHub-Event", "something")

    if event == "push":
        if data.get("commits"):
            await channel.send(embed=build_push_embed(data))
    elif event == "ping":
        embed = discord.Embed(
            description=f"🔔 Webhook connected for **{repo_name}**! I'm listening.",
            color=GITHUB_GREEN,
        )
        await channel.send(embed=embed)
    else:
        await channel.send(f"ℹ️ **{repo_name}** had a `{event}` event.")

    return web.Response(text="OK", status=200)

async def start_webserver():
    app = web.Application()
    app.router.add_post("/github", github_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Webhook server listening on port {PORT}")

# ---------- START EVERYTHING ----------
@bot.event
async def setup_hook():
    await setup_database()                      # connect + load saves first
    bot.loop.create_task(start_webserver())     # then open the webhook door

bot.run(TOKEN)
