from __future__ import annotations

from threading import Thread
import argparse
import os
import webbrowser

from .api import run_server
from .branding import APP_NAME, APP_SHORT_NAME, APP_SLUG
from .config import default_global_db_path
from .notification_adapters import DEFAULT_NOTIFICATION_CHANNEL
from .service import ensure_db, list_tasks
from .worker import pause_worker, resume_worker, run_once as run_worker_once, worker_status


def _load_tray_deps():
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Install tray extras first: pip install -e .[tray]") from exc
    return pystray, Image, ImageDraw


def _icon_image(Image, ImageDraw):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill="#111827")
    draw.ellipse((15, 17, 27, 29), fill="#38bdf8")
    draw.ellipse((37, 17, 49, 29), fill="#f59e0b")
    draw.ellipse((26, 37, 38, 49), fill="#a7f3d0")
    draw.line((27, 23, 37, 23), fill="#f8fafc", width=4)
    draw.line((23, 29, 31, 37), fill="#f8fafc", width=4)
    draw.line((41, 29, 35, 37), fill="#f8fafc", width=4)
    draw.text((21, 50), APP_SHORT_NAME[:3], fill="#f8fafc")
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Run {APP_NAME} as a Windows tray Hub Station")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db")
    parser.add_argument("--station", action="store_true", help="Run as the global hub station")
    parser.add_argument("--global", dest="global_scope", action="store_true", help="Use the global hub DB for tray/API")
    parser.add_argument("--global-db", help="Global hub DB path. Defaults to the user global hub path.")
    parser.add_argument("--worker-channel", default=DEFAULT_NOTIFICATION_CHANNEL)
    parser.add_argument("--worker-id", default="tmh-tray")
    args = parser.parse_args(argv)

    pystray, Image, ImageDraw = _load_tray_deps()
    db_path = args.db
    if args.station:
        args.global_scope = True
    if args.global_scope:
        db_path = args.global_db or str(default_global_db_path())
    ensure_db(db_path)

    def run_api_server() -> None:
        try:
            run_server(host=args.host, port=args.port, db_path=db_path)
        except OSError as exc:
            print(f"{APP_NAME} API server was not started: {exc}")

    api_thread = Thread(target=run_api_server, daemon=True)
    api_thread.start()

    url = f"http://{args.host}:{args.port}"

    def due_count() -> int:
        return len(list_tasks(db_path=db_path, due=True, limit=10000))

    def title() -> str:
        scope = "Hub Station" if args.global_scope else "Workspace"
        return f"{APP_NAME} {scope} - Due {due_count()}"

    def open_ui(_icon, _item):
        webbrowser.open(url)

    def open_due(_icon, _item):
        webbrowser.open(f"{url}/v1/tasks?due=true")

    def open_quick_add(_icon, _item):
        webbrowser.open(f"{url}/quick-add")

    def refresh_status(icon, _item):
        icon.title = title()
        print(icon.title)

    def show_due(_icon, _item):
        due = list_tasks(db_path=db_path, due=True, limit=10)
        print(f"Due tasks: {len(due)}")
        for task in due:
            print(f"- {task['task_id']} {task['title']}")

    def dispatch_once(icon, _item):
        result = run_worker_once(db_path=db_path, channel=args.worker_channel, worker_id=args.worker_id)
        icon.title = title()
        print(result)

    def pause_dispatch(icon, _item):
        result = pause_worker(db_path=db_path, reason="tray")
        icon.title = title()
        print(result)

    def resume_dispatch(icon, _item):
        result = resume_worker(db_path=db_path)
        icon.title = title()
        print(result)

    def print_worker_status(_icon, _item):
        print(worker_status(db_path=db_path))

    def quit_app(icon, _item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem(f"Open {APP_NAME}", open_ui),
        pystray.MenuItem("Quick Add", open_quick_add),
        pystray.MenuItem("Open Due Tasks", open_due),
        pystray.MenuItem("Refresh Due Count", refresh_status),
        pystray.MenuItem("Print Due Tasks", show_due),
        pystray.MenuItem("Dispatch Due Once", dispatch_once),
        pystray.MenuItem("Pause Worker", pause_dispatch),
        pystray.MenuItem("Resume Worker", resume_dispatch),
        pystray.MenuItem("Worker Status", print_worker_status),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon(APP_SLUG, _icon_image(Image, ImageDraw), title(), menu)
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
