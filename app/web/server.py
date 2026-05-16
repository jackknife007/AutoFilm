from __future__ import annotations

import os
import sys
from argparse import ArgumentParser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps, loads
from pathlib import Path
from threading import Event, Thread, Timer
from time import time
from traceback import format_exc
from typing import Any
from urllib.parse import parse_qs, urlparse

from yaml import YAMLError, safe_load

from app.core import logger, settings

STATIC_DIR = Path(__file__).with_name("static")
INDEX_FILE = STATIC_DIR / "index.html"


class WebState:
    def __init__(self, scheduler: Any = None, running_jobs: dict | None = None) -> None:
        self.scheduler = scheduler
        self.running_jobs: dict = running_jobs if running_jobs is not None else {}
        self.started_at = time()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    content_length = int(handler.headers.get("Content-Length", "0"))
    return handler.rfile.read(content_length) if content_length else b""


def _load_config_text() -> str:
    if not settings.CONFIG.exists():
        return ""
    return settings.CONFIG.read_text(encoding="utf-8")


def _parse_config_text(text: str) -> dict[str, Any]:
    config = safe_load(text) if text.strip() else {}
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError("配置文件顶层必须是 YAML 对象")
    return config


def _save_config_text(text: str) -> dict[str, Any]:
    parsed = _parse_config_text(text)
    settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    settings.CONFIG.write_text(text, encoding="utf-8")
    return parsed


def _tail_file(path: Path, lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as file:
        return file.readlines()[-lines:]


def _available_dates(log_dir: Path) -> list[str]:
    """扫描目录下 YYYY-MM-DD.log 文件，返回降序日期字符串列表。"""
    if not log_dir.exists():
        return []
    return sorted(
        [
            p.stem
            for p in log_dir.glob("*.log")
            if len(p.stem) == 10 and p.stem[4] == "-"
        ],
        reverse=True,
    )


def _current_log_path() -> Path:
    """返回 TRFileHandler 当天实际写入的日志文件路径。"""
    return settings.LOG_DIR / f"{date.today().strftime('%Y-%m-%d')}.log"


def _config_summary(config: dict[str, Any]) -> dict[str, int]:
    return {
        "alist2strm": len(config.get("Alist2StrmList") or []),
        "ani2alist": len(config.get("Ani2AlistList") or []),
        "libraryPoster": len(config.get("LibraryPosterList") or []),
    }


_CATEGORY_MAP = {
    "alist2strm": "Alist2Strm",
    "ani2alist": "Ani2Alist",
    "libraryposter": "LibraryPoster",
}

_DOW_MAP = {
    "0": "周日",
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "mon": "周一",
    "tue": "周二",
    "wed": "周三",
    "thu": "周四",
    "fri": "周五",
    "sat": "周六",
    "sun": "周日",
}


def _job_category(job_id: str) -> str:
    prefix = job_id.split(":")[0] if ":" in job_id else job_id
    return _CATEGORY_MAP.get(prefix, prefix)


def _format_trigger(trigger: Any) -> str:
    """将 APScheduler CronTrigger 对象格式化为人类可读字符串。"""
    try:
        if type(trigger).__name__ != "CronTrigger":
            return str(trigger)
        fields = {f.name: str(f) for f in trigger.fields}
        hour = fields.get("hour", "*")
        minute = fields.get("minute", "0")
        day_of_week = fields.get("day_of_week", "*")
        day = fields.get("day", "*")
        month = fields.get("month", "*")

        time_str = (
            "每小时"
            if hour == "*"
            else f"{hour}:{minute.zfill(2) if minute.isdigit() else minute}"
        )

        if day_of_week != "*":
            dow = _DOW_MAP.get(day_of_week.lower(), day_of_week)
            return f"每{dow} {time_str}"
        if month != "*" and day != "*":
            return f"每年 {month}月{day}日 {time_str}"
        if day != "*":
            return f"每月{day}日 {time_str}"
        return f"每天 {time_str}"
    except Exception:
        return str(trigger)


def _scheduler_jobs(scheduler: Any, running_jobs: dict) -> list[dict[str, Any]]:
    if scheduler is None:
        return []
    jobs = []
    for job in scheduler.get_jobs():
        task_log_path = logger.get_task_log_path(job.id)
        task_log_dir = task_log_path.parent
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "category": _job_category(job.id),
                "trigger": _format_trigger(job.trigger),
                "nextRunTime": (
                    job.next_run_time.isoformat() if job.next_run_time else None
                ),
                "paused": job.next_run_time is None,
                "running": job.id in running_jobs,
                "logPath": str(task_log_path),
                "logExists": task_log_dir.exists(),
            }
        )
    _category_order = {"Alist2Strm": 0, "Ani2Alist": 1, "LibraryPoster": 2}
    jobs.sort(key=lambda j: (_category_order.get(j["category"], 99), j["id"]))
    return jobs


def _status_payload(state: WebState) -> dict[str, Any]:
    config_text = _load_config_text()
    try:
        config = _parse_config_text(config_text)
        config_error = None
    except Exception as e:
        config = {}
        config_error = str(e)
    scheduler = state.scheduler
    return {
        "appName": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "debug": settings.DEBUG,
        "uptimeSeconds": int(time() - state.started_at),
        "configPath": str(settings.CONFIG),
        "configExists": settings.CONFIG.exists(),
        "configError": config_error,
        "logPath": str(_current_log_path()),
        "schedulerRunning": bool(scheduler and scheduler.running),
        "tasks": _config_summary(config),
        "jobs": _scheduler_jobs(scheduler, state.running_jobs),
        "recentLog": "".join(_tail_file(_current_log_path(), 120)),
    }


def _build_handler(state: WebState):
    class WebHandler(BaseHTTPRequestHandler):
        server_version = "AutoFilmWeb/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("Web: " + fmt, *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = dumps(payload, ensure_ascii=False, default=_json_default).encode(
                "utf-8"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, message: str, status: int = 400) -> None:
            self._send_json({"ok": False, "error": message}, status)

        def _send_file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self._send_error_json("文件不存在", 404)
                return
            body = path.read_bytes()
            content_type = (
                "text/html; charset=utf-8"
                if path.suffix == ".html"
                else "application/octet-stream"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            try:
                if path in ("/", "/index.html"):
                    self._send_file(INDEX_FILE)
                elif path == "/api/status":
                    self._send_json({"ok": True, "data": _status_payload(state)})
                elif path == "/api/config":
                    text = _load_config_text()
                    self._send_json(
                        {
                            "ok": True,
                            "data": {
                                "path": str(settings.CONFIG),
                                "content": text,
                                "parsed": _parse_config_text(text),
                            },
                        }
                    )
                elif path == "/api/logs/dates":
                    dates = _available_dates(settings.LOG_DIR)
                    self._send_json({"ok": True, "data": {"dates": dates}})
                elif path == "/api/logs":
                    query_params = parse_qs(parsed_url.query)
                    lines = int(query_params.get("lines", ["200"])[0])
                    date_str = query_params.get("date", [None])[0]
                    log_path = (
                        settings.LOG_DIR / f"{date_str}.log"
                        if date_str
                        else _current_log_path()
                    )
                    self._send_json(
                        {
                            "ok": True,
                            "data": {
                                "path": str(log_path),
                                "content": "".join(_tail_file(log_path, lines)),
                            },
                        }
                    )
                elif path == "/api/jobs/logs/dates":
                    job_id = parse_qs(parsed_url.query).get("jobId", [""])[0]
                    if not job_id:
                        self._send_error_json("缺少 jobId", 400)
                        return
                    task_log_dir = logger.get_task_log_path(job_id).parent
                    dates = _available_dates(task_log_dir)
                    self._send_json({"ok": True, "data": {"dates": dates}})
                elif path == "/api/jobs/logs":
                    query = parse_qs(parsed_url.query)
                    job_id = query.get("jobId", [""])[0]
                    lines = int(query.get("lines", ["200"])[0])
                    date_str = query.get("date", [None])[0]
                    if not job_id:
                        self._send_error_json("缺少 jobId", 400)
                        return
                    log_path = logger.get_task_log_path(job_id, date_str)
                    self._send_json(
                        {
                            "ok": True,
                            "data": {
                                "path": str(log_path),
                                "content": "".join(_tail_file(log_path, lines)),
                            },
                        }
                    )
                else:
                    self._send_error_json("接口不存在", 404)
            except Exception as e:
                logger.error(format_exc())
                self._send_error_json(str(e), 500)

        def do_POST(self) -> None:
            self._handle_write_request()

        def do_PUT(self) -> None:
            self._handle_write_request()

        def _handle_write_request(self) -> None:
            parsed_url = urlparse(self.path)
            try:
                body = _read_body(self)
                if parsed_url.path == "/api/config":
                    if self.headers.get("Content-Type", "").startswith(
                        "application/json"
                    ):
                        content = loads(body.decode("utf-8")).get("content", "")
                    else:
                        content = body.decode("utf-8")
                    parsed = _save_config_text(content)
                    self._send_json(
                        {
                            "ok": True,
                            "data": {
                                "path": str(settings.CONFIG),
                                "parsed": parsed,
                                "message": "配置已保存，重启或重新调度后生效",
                            },
                        }
                    )
                elif parsed_url.path == "/api/jobs/run":
                    if state.scheduler is None:
                        self._send_error_json("当前 Web 服务未绑定调度器", 409)
                        return
                    payload = loads(body.decode("utf-8")) if body else {}
                    job_id = payload.get("jobId")
                    job = state.scheduler.get_job(job_id)
                    if job is None:
                        self._send_error_json("任务不存在", 404)
                        return
                    state.scheduler.modify_job(
                        job_id, next_run_time=datetime.now(state.scheduler.timezone)
                    )
                    self._send_json({"ok": True, "data": {"message": "任务已触发"}})
                elif parsed_url.path in ("/api/jobs/pause", "/api/jobs/resume"):
                    if state.scheduler is None:
                        self._send_error_json("当前 Web 服务未绑定调度器", 409)
                        return
                    payload = loads(body.decode("utf-8")) if body else {}
                    job_id = payload.get("jobId")
                    job = state.scheduler.get_job(job_id)
                    if job is None:
                        self._send_error_json("任务不存在", 404)
                        return
                    if parsed_url.path == "/api/jobs/pause":
                        job.pause()
                        self._send_json({"ok": True, "data": {"message": "任务已暂停"}})
                    else:
                        job.resume()
                        self._send_json({"ok": True, "data": {"message": "任务已启动"}})
                elif parsed_url.path == "/api/jobs/stop":
                    if state.scheduler is None:
                        self._send_error_json("当前 Web 服务未绑定调度器", 409)
                        return
                    payload = loads(body.decode("utf-8")) if body else {}
                    job_id = payload.get("jobId")
                    task = state.running_jobs.get(job_id)
                    if task is None:
                        self._send_error_json("任务当前未在运行", 409)
                        return
                    task.cancel()
                    self._send_json({"ok": True, "data": {"message": "停止信号已发送"}})
                elif parsed_url.path == "/api/restart":
                    self._send_json(
                        {"ok": True, "data": {"message": "应用将在 1 秒后重启"}}
                    )

                    def _do_restart():
                        logger.info("收到重启指令，重启应用…")
                        os.execv(sys.executable, [sys.executable] + sys.argv)

                    Timer(1.0, _do_restart).start()
                else:
                    self._send_error_json("接口不存在", 404)
            except (YAMLError, ValueError) as e:
                self._send_error_json(f"配置格式错误：{e}", 400)
            except Exception as e:
                logger.error(format_exc())
                self._send_error_json(str(e), 500)

    return WebHandler


def start_web_server(
    scheduler: Any = None,
    running_jobs: dict | None = None,
    host: str = "0.0.0.0",
    port: int = 7899,
) -> ThreadingHTTPServer:
    state = WebState(scheduler=scheduler, running_jobs=running_jobs)
    server = ThreadingHTTPServer((host, int(port)), _build_handler(state))
    thread = Thread(target=server.serve_forever, daemon=True, name="AutoFilmWeb")
    thread.start()
    _, server_port = server.server_address
    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    logger.info(f"Web 管理端已启动：http://{display_host}:{server_port}")
    return server


def main() -> None:
    parser = ArgumentParser(description="AutoFilm Web 管理端")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7899)
    args = parser.parse_args()
    server = start_web_server(host=args.host, port=args.port)
    try:
        Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
