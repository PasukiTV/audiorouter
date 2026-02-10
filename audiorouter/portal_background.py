from __future__ import annotations

def ensure_background() -> bool:
    try:
        import asyncio, os, threading
        from dbus_next.aio import MessageBus
        from dbus_next import Variant
    except Exception:
        return False

    async def _request():
        bus = await MessageBus().connect()
        proxy = await bus.introspect(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop"
        )
        obj = bus.get_proxy_object(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            proxy
        )
        bg = obj.get_interface("org.freedesktop.portal.Background")

        await bg.call_request_background(
            "",
            {
                "handle_token": Variant("s", os.urandom(8).hex()),
                "autostart": Variant("b", True),
                "commandline": Variant("as", ["audiorouter", "--daemon"]),
                "reason": Variant("s", "Apply audio routing rules in background"),
            },
        )

    def runner():
        try:
            asyncio.run(_request())
        except Exception:
            pass

    threading.Thread(target=runner, daemon=True).start()
    return True
