"""Combined bot runner — runs 4 Telegram bots as subprocesses + shared health/proxy server.

Services:
- glowroom-main  (@glowroomkira_bot)   runs glowroom/run.py (python-telegram-bot)
- glowroom-admin (@glowwroom_klientbot) runs glowroom/admin.py (python-telegram-bot)
- rodychi-leads  (@leadsrodychi_bot)   runs rodychi_leads/main.py (aiogram) — exposes /quiz on internal port 10001
- rdch-bonus     (@rdchbonus_bot)      runs rdch_bonus/main.py

One Render container → one 750h quota.
Public server on $PORT:
  GET  /         → health JSON
  GET  /health   → health JSON
  POST /quiz     → proxies to leads bot (http://127.0.0.1:10001/quiz)
"""
import os
import sys
import asyncio
import logging
import subprocess
import aiohttp
from aiohttp import web

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("combined")

PORT = int(os.environ.get("PORT", "10000"))
ROOT = os.path.dirname(os.path.abspath(__file__))

LEADS_INTERNAL_PORT = 10001

BOT_SPECS = {
    "glowroom-main": {
        "cwd":    os.path.join(ROOT, "glowroom"),
        "script": "run.py",
        "env":    {"BOT_TOKEN": os.environ.get("GLOWROOM_TOKEN", "")},
        "port":   "0",
    },
    "glowroom-admin": {
        "cwd":    os.path.join(ROOT, "glowroom"),
        "script": "admin.py",
        "env":    {"TOKEN": os.environ.get("GLOWROOM_ADMIN_TOKEN", "")},
        "port":   "0",
    },
    "rodychi-leads": {
        "cwd":    os.path.join(ROOT, "rodychi_leads"),
        "script": "main.py",
        "env":    {"BOT_TOKEN": os.environ.get("RODYCHI_LEADS_TOKEN", "")},
        "port":   str(LEADS_INTERNAL_PORT),
    },
    "rdch-bonus": {
        "cwd":    os.path.join(ROOT, "rdch_bonus"),
        "script": "main.py",
        "env":    {"BOT_TOKEN": os.environ.get("RDCH_BONUS_TOKEN", "")},
        "port":   "0",
    },
}

BOTS = []  # [[name, popen], ...]


def spawn_bot(name: str) -> subprocess.Popen:
    spec = BOT_SPECS[name]
    env = dict(os.environ)
    env.update(spec["env"])
    env["PORT"] = spec["port"]
    cmd = [sys.executable, "-u", spec["script"]]
    log.info("[%s] Spawning: %s (cwd=%s, PORT=%s)", name, " ".join(cmd), spec["cwd"], spec["port"])
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


async def quiz_proxy(request: web.Request):
    """Forward POST/OPTIONS /quiz to rodychi-leads bot on internal port."""
    if request.method == "OPTIONS":
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    target = f"http://127.0.0.1:{LEADS_INTERNAL_PORT}/quiz"
    try:
        body = await request.read()
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(target, data=body, headers={"Content-Type": "application/json"}) as r:
                text = await r.text()
                return web.Response(
                    status=r.status,
                    text=text,
                    content_type="application/json",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
    except Exception as e:
        log.exception("[quiz-proxy] error: %s", e)
        return web.json_response(
            {"ok": False, "err": f"proxy_error: {e}"},
            status=502,
            headers={"Access-Control-Allow-Origin": "*"},
        )


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/quiz", quiz_proxy)
    app.router.add_options("/quiz", quiz_proxy)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Public server on :%s", PORT)


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
