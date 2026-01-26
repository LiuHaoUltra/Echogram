# 导出所有模型，确保 Base.metadata 能收集到它们
from models.base import Base
from models.config import Config
from models.whitelist import Whitelist
from models.history import History
