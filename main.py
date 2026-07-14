"""
GitHub Notify Bot v2 — now with fancy embeds!
----------------------------------------------
Posts pretty embed cards (like the official GitHub bot) whenever
a watched repository gets updated.

Admin commands:
  !notifyhere owner/repo   -> send that repo's updates to this channel
  !stopnotify owner/repo   -> stop watching a repo
  !watching                -> list watched repos (anyone can use)
"""

import os
import json
import discord
from discord.ext import commands
from aiohttp import web

# ---------- SETTINGS ----------
TOKEN = os.environ["DISCORD_TOKEN"]
PORT = int(os.environ.get("PORT", 8080))
SAVE_FILE = "watched_repos.json"
GITHUB_GREEN = 0x2ECC40   # the green stripe colour

# ---------- MEMORY ----------
def load_repos():
    try:
        with open(SAVE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_repos(repos):
    with open(SAVE_FILE, "w") as f:
        json.dump(repos, f, indent=2)

watched = load_repos()

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
    save_repos(watched)
    await ctx.send(
        f"✅ Got it! Updates for **{repo}** will be posted in this channel.\n"
        f"Now add a webhook in that repo's GitHub settings."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def stopnotify(ctx, repo: str):
    repo = repo.lower().strip()
    if watched.pop(repo, None) is not None:
        save_repos(watched)
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
    """Make an embed card that looks like the official GitHub bot."""
    repo = data.get("repository", {})
    repo_name = repo.get("full_name", "unknown/repo")
    repo_url = repo.get("html_url", "https://github.com")

    # branch name comes as "refs/heads/main" -> we just want "main"
    branch = data.get("ref", "refs/heads/?").split("/")[-1]

    commits = data.get("commits", [])
    count = len(commits)
    plural = "s" if count != 1 else ""

    # the person who pushed (name + profile picture)
    sender = data.get("sender", {})
    pusher_name = data.get("pusher", {}).get("name", "someone")
    avatar = sender.get("avatar_url", "")

    embed = discord.Embed(
        title=f"[{repo_name}:{branch}] {count} new commit{plural}",
        url=data.get("compare", repo_url),   # clicking the title shows the changes
        color=GITHUB_GREEN,
    )
    embed.set_author(name=pusher_name, icon_url=avatar)

    # one line per commit: short-hash  message - author  (max 10 lines)
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
        if data.get("commits"):                      # normal push with commits
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
    bot.loop.create_task(start_webserver())

bot.run(TOKEN)
