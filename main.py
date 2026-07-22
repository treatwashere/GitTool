"""
GitHub Notify Bot v4 — per-server watching lists!
--------------------------------------------------
Every Discord server (a "guild" in bot language) now has its OWN list.
A new server starts empty until its admins use !notifyhere.
The same repo can notify many servers at once!

Admin commands (per server):
  !notifyhere owner/repo   -> send that repo's updates to this channel
  !stopnotify owner/repo   -> stop watching (only for this server)
  !watching                -> list THIS server's watched repos
"""

import os
import asyncpg
import discord
from discord.ext import commands
from aiohttp import web

# ---------- SETTINGS ----------
TOKEN = os.environ["DISCORD_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.environ.get("PORT", 8080))
GITHUB_GREEN = 0x2ECC40

# ---------- THE DATABASE ----------
# watched looks like:  {"owner/repo": {guild_id: channel_id, ...}}
# So one repo can point to MANY servers, each with its own channel!
watched = {}
db_pool = None

async def setup_database():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS watched_repos_v2 (
                repo TEXT NOT NULL,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                PRIMARY KEY (repo, guild_id)
            )
        """)
        rows = await conn.fetch("SELECT repo, guild_id, channel_id FROM watched_repos_v2")
        for row in rows:
            watched.setdefault(row["repo"], {})[row["guild_id"]] = row["channel_id"]
    total = sum(len(g) for g in watched.values())
    print(f"Database connected! Loaded {total} watch entries.")

async def db_save(repo, guild_id, channel_id):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO watched_repos_v2 (repo, guild_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (repo, guild_id) DO UPDATE SET channel_id = $3
        """, repo, guild_id, channel_id)

async def db_delete(repo, guild_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM watched_repos_v2 WHERE repo = $1 AND guild_id = $2",
            repo, guild_id
        )

# ---------- THE DISCORD BOT ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}!")

@bot.command()
@commands.has_permissions(administrator=True)
async def notifyhere(ctx, repo: str):
    if ctx.guild is None:
        await ctx.send("This command only works inside a server!")
        return
    repo = repo.lower().strip()
    if "/" not in repo:
        await ctx.send("Please write it like this: `!notifyhere owner/repo-name`")
        return
    watched.setdefault(repo, {})[ctx.guild.id] = ctx.channel.id
    await db_save(repo, ctx.guild.id, ctx.channel.id)
    await ctx.send(
        f"✅ Got it! Updates for **{repo}** will be posted in this channel "
        f"for **this server**.\n"
        f"Make sure the repo has a webhook set up on GitHub!"
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def stopnotify(ctx, repo: str):
    if ctx.guild is None:
        await ctx.send("This command only works inside a server!")
        return
    repo = repo.lower().strip()
    servers = watched.get(repo, {})
    if servers.pop(ctx.guild.id, None) is not None:
        if not servers:                 # nobody watches this repo anymore
            watched.pop(repo, None)
        await db_delete(repo, ctx.guild.id)
        await ctx.send(f"🛑 Okay, this server will no longer get updates for **{repo}**.")
    else:
        await ctx.send("This server isn't watching that repo!")

@bot.command()
async def watching(ctx):
    if ctx.guild is None:
        await ctx.send("This command only works inside a server!")
        return
    # only show repos THIS server is watching
    lines = [
        f"• **{repo}** → <#{servers[ctx.guild.id]}>"
        for repo, servers in watched.items()
        if ctx.guild.id in servers
    ]
    if not lines:
        await ctx.send("This server isn't watching any repos yet!")
        return
    await ctx.send("This server is watching:\n" + "\n".join(lines))

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

    servers = watched.get(repo_name)
    if not servers:
        return web.Response(text="No server watching this repo", status=200)

    event = request.headers.get("X-GitHub-Event", "something")

    # send to EVERY server that watches this repo
    for guild_id, channel_id in servers.items():
        channel = bot.get_channel(channel_id)
        if channel is None:
            continue    # channel deleted or bot kicked; skip it

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
    await setup_database()
    bot.loop.create_task(start_webserver())

bot.run(TOKEN)
