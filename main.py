"""
GitHub Notify Bot
-----------------
A Discord bot that posts a message whenever a GitHub repository gets updated.

How it works:
1. An admin types:  !notifyhere owner/repo-name   in a Discord channel
2. The bot remembers "send updates for that repo to this channel"
3. In the GitHub repo settings, you add a webhook pointing to this bot
4. When someone pushes code, GitHub pings the bot and the bot posts in Discord
"""

import os
import json
import discord
from discord.ext import commands
from aiohttp import web

# ---------- SETTINGS ----------
TOKEN = os.environ["DISCORD_TOKEN"]        # your bot token (kept secret!)
PORT = int(os.environ.get("PORT", 8080))   # Railway gives us this automatically
SAVE_FILE = "watched_repos.json"           # where we remember repo -> channel

# ---------- MEMORY (which repo goes to which channel) ----------
def load_repos():
    try:
        with open(SAVE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_repos(repos):
    with open(SAVE_FILE, "w") as f:
        json.dump(repos, f, indent=2)

watched = load_repos()   # looks like: {"owner/repo": channel_id}

# ---------- THE DISCORD BOT ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}! Watching {len(watched)} repos.")

@bot.command()
@commands.has_permissions(administrator=True)   # admins only!
async def notifyhere(ctx, repo: str):
    """Admin command: !notifyhere owner/repo"""
    repo = repo.lower().strip()
    if "/" not in repo:
        await ctx.send("Please write it like this: `!notifyhere owner/repo-name`")
        return
    watched[repo] = ctx.channel.id
    save_repos(watched)
    await ctx.send(
        f"✅ Got it! Updates for **{repo}** will be posted in this channel.\n"
        f"Now add a webhook in that repo's GitHub settings (ask the bot owner for the URL)."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def stopnotify(ctx, repo: str):
    """Admin command: !stopnotify owner/repo"""
    repo = repo.lower().strip()
    if watched.pop(repo, None) is not None:
        save_repos(watched)
        await ctx.send(f"🛑 Okay, no more updates for **{repo}**.")
    else:
        await ctx.send("I wasn't watching that repo!")

@bot.command()
async def watching(ctx):
    """Anyone can ask: !watching"""
    if not watched:
        await ctx.send("I'm not watching any repos yet!")
        return
    lines = [f"• **{repo}** → <#{channel_id}>" for repo, channel_id in watched.items()]
    await ctx.send("I'm watching:\n" + "\n".join(lines))

# ---------- THE WEBHOOK SERVER (GitHub knocks on this door) ----------
async def github_webhook(request):
    data = await request.json()
    repo_info = data.get("repository", {})
    repo_name = repo_info.get("full_name", "").lower()

    channel_id = watched.get(repo_name)
    if channel_id is None:
        return web.Response(text="Not watching this repo", status=200)

    channel = bot.get_channel(channel_id)
    if channel is None:
        return web.Response(text="Channel not found", status=200)

    event = request.headers.get("X-GitHub-Event", "something")

    if event == "push":
        pusher = data.get("pusher", {}).get("name", "someone")
        commits = data.get("commits", [])
        msg = f"📦 **{repo_name}** was updated by **{pusher}**!"
        if commits:
            latest = commits[-1].get("message", "").split("\n")[0][:100]
            msg += f"\n💬 Latest change: *{latest}*"
        await channel.send(msg)
    elif event == "ping":
        await channel.send(f"🔔 Webhook connected for **{repo_name}**! I'm listening.")
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
    bot.loop.create_task(start_webserver())

bot.run(TOKEN)
