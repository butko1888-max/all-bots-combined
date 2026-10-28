"""Combined bot runner — runs 3 Telegram bots as subprocesses + shared health server.

Services:
- glowroom-main  (@glowroomkira_bot)   runs glowroom/run.py
- glowroom-admin (@glowwroom_klientbot) runs glowroom/admin.py
- rodychi-leads  (@leadsrodychi_bot)   runs rodychi_leads/main.py

One Render container → one 750h quota. Health server on $PORT so Render keeps it alive.
"""
import os
import sys
import asyncio
import logging
import subprocess
from aiohttp import web

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("combined")

PORT = int(os.environ.get("PORT", "10000"))
ROOT = os.path.dirname(os.path.abspath(__file__))


def spawn_bot(name: str, module: str, env_overrides: dict) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(env_overrides)
    # Prevent each sub-bot from binding its own health port; set PORT=0 (OS-assigned)
    env["PORT"] = "0"
    cmd = [sys.executable, "-u", "-m", module]
    log.info("Spawning %s: %s", name, " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=ROOT, env=env)
    return p


BOTS = []


async def start_bots():
    """Spawn each bot once at startup."""
    # 1. glowroom main booking bot
    BOTS.append(("glowroom-main", spawn_bot(
        "glowroom-main", "glowroom.run",
        {"BOT_TOKEN": os.environ.get("GLOWROOM_TOKEN", "")},
    )))
    # 2. glowroom admin bot
    BOTS.append(("glowroom-admin", spawn_bot(
        "glowroom-admin", "glowroom.admin",
        {"TOKEN": os.environ.get("GLOWROOM_ADMIN_TOKEN", "")},
    )))
    # 3. rodychi leads webhook bot
    BOTS.append(("rodychi-leads", spawn_bot(
        "rodychi-leads", "rodychi_leads.main",
        {"BOT_TOKEN": os.environ.get("RODYCHI_LEADS_TOKEN", "")},
    )))


async def monitor_bots():
    """Restart dead sub-processes after short backoff."""
    while True:
        await asyncio.sleep(30)
        for i, (name, proc) in enumerate(list(BOTS)):
            if proc.poll() is not None:
                log.error("%s died with code %s — restarting", name, proc.returncode)
                env_overrides = {
                    "glowroom-main":  {"BOT_TOKEN": os.environ.get("GLOWROOM_TOKEN", "")},
                    "glowroom-admin": {"TOKEN":     os.environ.get("GLOWROOM_ADMIN_TOKEN", "")},
                    "rodychi-leads":  {"BOT_TOKEN": os.environ.get("RODYCHI_LEADS_TOKEN", "")},
                }[name]
                module = {
                    "glowroom-main":  "glowroom.run",
                    "glowroom-admin": "glowroom.admin",
                    "rodychi-leads":  "rodychi_leads.main",
                }[name]
                BOTS[i] = (name, spawn_bot(name, module, env_overrides))


# ──────────────────────────────────────────────────────
# Health server
# ──────────────────────────────────────────────────────
async def health(_):
    status = {name: ("alive" if proc.poll() is None else f"dead:{proc.returncode}")
              for name, proc in BOTS}
    return web.json_response({"ok": True, "bots": status})


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health server on :%s", PORT)


async def main():
    await start_health_server()
    await start_bots()
    log.info("All %d bots launched, monitoring…", len(BOTS))
    try:
        await monitor_bots()
    except KeyboardInterrupt:
        pass
    finally:
        for name, proc in BOTS:
            if proc.poll() is None:
                log.info("Terminating %s", name)
                proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())
