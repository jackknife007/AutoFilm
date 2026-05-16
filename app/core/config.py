from pathlib import Path
from yaml import safe_load
from typing import Any

from app.version import APP_VERSION


class SettingManager:
    """
    系统配置
    """

    # APP 名称
    APP_NAME: str = "Autofilm"
    # APP 版本
    APP_VERSION: str = APP_VERSION
    # 时区
    TZ: str = "Asia/Shanghai"
    # 开发者模式
    DEBUG: bool = False

    def __init__(self) -> None:
        """
        初始化 SettingManager 对象
        """
        self.__mkdir()
        self.__load_mode()

    def __mkdir(self) -> None:
        """
        创建目录
        """
        config_dir = self.CONFIG_DIR
        if not config_dir.exists():
            config_dir.mkdir(parents=True, exist_ok=True)

        log_dir = self.LOG_DIR
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)

    def __load_mode(self) -> None:
        """
        加载模式
        """
        is_dev = self.__load_config().get("Settings", {}).get("DEV", False)

        self.DEBUG = is_dev

    def __load_config(self) -> dict[str, Any]:
        """
        加载配置文件
        """
        if not self.CONFIG.exists():
            return {}
        with self.CONFIG.open(mode="r", encoding="utf-8") as file:
            return safe_load(file) or {}

    @property
    def BASE_DIR(self) -> Path:
        """
        后端程序基础路径 AutoFilm/app
        """
        return Path(__file__).parents[2]

    @property
    def CONFIG_DIR(self) -> Path:
        """
        配置文件路径
        """
        return self.BASE_DIR / "config"

    @property
    def LOG_DIR(self) -> Path:
        """
        日志文件路径
        """
        return self.BASE_DIR / "logs"

    @property
    def CONFIG(self) -> Path:
        """
        配置文件
        """
        return self.CONFIG_DIR / "config.yaml"

    @property
    def LOG(self) -> Path:
        """
        日志文件
        """
        if self.DEBUG:
            return self.LOG_DIR / "dev.log"
        else:
            return self.LOG_DIR / "AutoFilm.log"

    @property
    def AlistServerList(self) -> list[dict[str, Any]]:
        alist_server_list = self.__load_config().get("Alist2StrmList", [])
        return alist_server_list

    @property
    def Ani2AlistList(self) -> list[dict[str, Any]]:
        ani2alist_list = self.__load_config().get("Ani2AlistList", [])
        return ani2alist_list

    @property
    def LibraryPosterList(self) -> list[dict[str, Any]]:
        library_poster_list = self.__load_config().get("LibraryPosterList", [])
        return library_poster_list

    @property
    def WebUI(self) -> dict[str, Any]:
        web_ui = self.__load_config().get("WebUI", {})
        return web_ui or {}


settings = SettingManager()
