"""Combined bot runner — runs 3 Telegram bots as subprocesses + shared health server.

Services:
- glowroom-main  (@glowroomkira_bot)   runs glowroom/run.py (python-telegram-bot)
- glowroom-admin (@glowwroom_klientbot) runs glowroom/admin.py (python-telegram-bot)
- rodychi-leads  (@leadsrodychi_bot)   runs rodychi_leads/main.py (aiogram)

One Render container → one 750h quota.
Health server on $PORT keeps Render container alive.
Each sub-bot runs from its own subdirectory so its relative imports work.
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

BOT_SPECS = {
    "glowroom-main": {
        "cwd":    os.path.join(ROOT, "glowroom"),
        "script": "run.py",
        "env":    {"BOT_TOKEN": os.environ.get("GLOWROOM_TOKEN", "")},
    },
    "glowroom-admin": {
        "cwd":    os.path.join(ROOT, "glowroom"),
        "script": "admin.py",
        "env":    {"TOKEN": os.environ.get("GLOWROOM_ADMIN_TOKEN", "")},
    },
    "rodychi-leads": {
        "cwd":    os.path.join(ROOT, "rodychi_leads"),
        "script": "main.py",
        "env":    {"BOT_TOKEN": os.environ.get("RODYCHI_LEADS_TOKEN", "")},
    },
    "rdch-bonus": {
        "cwd":    os.path.join(ROOT, "rdch_bonus"),
        "script": "main.py",
        "env":    {"BOT_TOKEN": os.environ.get("RDCH_BONUS_TOKEN", "")},
    },
}

BOTS = []  # [(name, popen)]


def spawn_bot(name: str) -> subprocess.Popen:
    spec = BOT_SPECS[name]
    env = dict(os.environ)
    env.update(spec["env"])
    # Prevent each sub-bot from binding our main health port
    env["PORT"] = "0"
    cmd = [sys.executable, "-u", spec["script"]]
    log.info("[%s] Spawning: %s (cwd=%s)", name, " ".join(cmd), spec["cwd"])
    return subprocess.Popen(cmd, cwd=spec["cwd"], env=env)


async def start_bots():
    for name in BOT_SPECS:
        BOTS.append([name, spawn_bot(name)])


async def monitor_bots():
    """Restart dead sub-processes."""
    while True:
        await asyncio.sleep(30)
        for i, (name, proc) in enumerate(BOTS):
            if proc.poll() is not None:
                log.error("[%s] died with code %s — restarting in 5s", name, proc.returncode)
                await asyncio.sleep(5)
                BOTS[i][1] = spawn_bot(name)


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
