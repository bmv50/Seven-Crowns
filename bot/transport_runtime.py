"""Transport selection and signal lifetime, independent of game rules."""
import asyncio
import signal


def telegram_enabled(env):
    # Absence preserves every existing Telegram deployment.
    return env.get('TELEGRAM_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'on')


def create_telegram_bot(env, bot_factory, session_factory):
    """No token validation, client or proxy construction for a disabled transport."""
    if not telegram_enabled(env):
        return None
    token = env.get('BOT_TOKEN', 'PASTE_YOUR_TOKEN_HERE')
    proxy = env.get('PROXY_URL', '').strip()
    return (bot_factory(token=token, session=session_factory(proxy=proxy))
            if proxy else bot_factory(token=token))


async def telegram_username(bot):
    if bot is None:
        return ''
    me = await bot.get_me()
    return me.username or ''


async def wait_for_shutdown():
    """MAX-only has no Telegram polling to own SIGINT/SIGTERM handling."""
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    installed = []
    fallback = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stopped.set)
                installed.append(sig)
            except NotImplementedError:  # Windows development runtime
                previous = signal.getsignal(sig)
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopped.set))
                fallback.append((sig, previous))
        await stopped.wait()
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        for sig, previous in fallback:
            signal.signal(sig, previous)


async def run_transport(bot, dispatcher):
    if bot is None:
        await wait_for_shutdown()
    else:
        await dispatcher.start_polling(bot)
