import asyncio
from asyncio import get_event_loop
from sys import path
from os.path import dirname
from traceback import format_exc

path.append(dirname(dirname(__file__)))

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore

from app.core import settings, logger
from app.extensions import LOGO
from app.modules import Alist2Strm, Ani2Alist, LibraryPoster
from app.web.server import start_web_server

_running_jobs: dict[str, asyncio.Task] = {}


async def run_task_with_log(task_id: str, task_name: str, task_factory) -> None:
    """
    运行任务并写入独立任务日志。
    task_factory 是一个不接受参数、返回协程的工厂函数，
    在此处才实例化模块对象，避免启动时立即发起 HTTP 连接。
    """
    current = asyncio.current_task()
    if current is not None:
        _running_jobs[task_id] = current
    with logger.task_context(task_id, task_name):
        logger.info(f"任务 {task_name} 开始运行")
        try:
            await task_factory()
        except asyncio.CancelledError:
            logger.warning(f"任务 {task_name} 已被停止")
        except Exception:
            logger.error(format_exc())
            raise
        finally:
            _running_jobs.pop(task_id, None)
            logger.info(f"任务 {task_name} 运行结束")


def print_logo() -> None:
    """
    打印 Logo
    """

    print(LOGO)
    print(f" {settings.APP_NAME} {settings.APP_VERSION} ".center(65, "="))
    print("")


if __name__ == "__main__":
    print_logo()

    logger.info(f"AutoFilm {settings.APP_VERSION} 启动中...")
    logger.debug(f"是否开启 DEBUG 模式: {settings.DEBUG}")

    scheduler = AsyncIOScheduler()

    if settings.AlistServerList:
        logger.info("检测到 Alist2Strm 模块配置，正在添加至后台任务")
        for server in settings.AlistServerList:
            cron = server.get("cron")
            if cron:
                task_id = f"alist2strm:{server['id']}"
                task_name = str(server["id"])
                scheduler.add_job(
                    run_task_with_log,
                    args=(task_id, task_name, lambda s=server: Alist2Strm(**s).run()),
                    trigger=CronTrigger.from_crontab(cron),
                    id=task_id,
                    name=task_name,
                    replace_existing=True,
                )
                logger.info(f"{server['id']} 已被添加至后台任务")
            else:
                logger.warning(f"{server['id']} 未设置 cron")
    else:
        logger.warning("未检测到 Alist2Strm 模块配置")

    if settings.Ani2AlistList:
        logger.info("检测到 Ani2Alist 模块配置，正在添加至后台任务")
        for server in settings.Ani2AlistList:
            cron = server.get("cron")
            if cron:
                task_id = f"ani2alist:{server['id']}"
                task_name = str(server["id"])
                scheduler.add_job(
                    run_task_with_log,
                    args=(task_id, task_name, lambda s=server: Ani2Alist(**s).run()),
                    trigger=CronTrigger.from_crontab(cron),
                    id=task_id,
                    name=task_name,
                    replace_existing=True,
                )
                logger.info(f"{server['id']} 已被添加至后台任务")
            else:
                logger.warning(f"{server['id']} 未设置 cron")
    else:
        logger.warning("未检测到 Ani2Alist 模块配置")

    if settings.LibraryPosterList:
        logger.info("检测到 LibraryPoster 模块配置，正在添加至后台任务")
        for poster in settings.LibraryPosterList:
            cron = poster.get("cron")
            if cron:
                task_id = f"libraryposter:{poster['id']}"
                task_name = str(poster["id"])
                scheduler.add_job(
                    run_task_with_log,
                    args=(
                        task_id,
                        task_name,
                        lambda s=poster: LibraryPoster(**s).run(),
                    ),
                    trigger=CronTrigger.from_crontab(cron),
                    id=task_id,
                    name=task_name,
                    replace_existing=True,
                )
                logger.info(f"{poster['id']} 已被添加至后台任务")
            else:
                logger.warning(f"{poster['id']} 未设置 cron")
    else:
        logger.warning("未检测到 LibraryPoster 模块配置")

    scheduler.start()
    web_server = None
    web_config = settings.WebUI
    if web_config.get("enabled", False):
        web_server = start_web_server(
            scheduler=scheduler,
            running_jobs=_running_jobs,
            host=web_config.get("host", "0.0.0.0"),
            port=int(web_config.get("port", 7899)),
        )
    logger.info("AutoFilm 启动完成")

    try:
        get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        if web_server:
            web_server.shutdown()
        logger.info("AutoFilm 程序退出！")
