"""LangGraph 检查点保存器运行时。

SoloChef 使用 MySQL 作为业务数据库，但 LangGraph 暂无官方 MySQL 检查点保存器。
当前采用 ``InMemorySaver`` 作为检查点后端，支持工作流断点恢复与失败重试；
检查点仅存活于当前进程，重启后丢失。

生产环境如需跨进程持久化，可引入 ``langgraph-checkpoint-redis`` 替换此处实现——
项目已依赖 ``redis`` 客户端，切换成本仅限本文件。
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import Settings, get_settings


class CheckpointRuntime:
    """检查点保存器运行时，负责按配置创建并缓存 saver 实例。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._saver: BaseCheckpointSaver[str] | None = None

    async def get(self) -> BaseCheckpointSaver[str]:
        """返回进程内缓存的检查点保存器。

        使用 ``InMemorySaver``：无需外部依赖，不绑定数据库方言，
        支持 LangGraph 断点恢复。首次调用时惰性创建，后续复用同一实例。
        """
        if self._saver is None:
            self._saver = InMemorySaver()
        return self._saver

    async def close(self) -> None:
        """释放检查点后端资源。

        ``InMemorySaver`` 不持有外部连接，此处仅清理引用。
        保留接口便于未来切换到 Redis 等持久化后端时统一生命周期管理。
        """
        self._saver = None


checkpoint_runtime = CheckpointRuntime(get_settings())
