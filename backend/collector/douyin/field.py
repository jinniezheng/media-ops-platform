from enum import Enum


class SearchSortType(Enum):
    GENERAL = "0"       # 综合排序
    LATEST = "1"        # 最新发布
    MOST_LIKED = "2"    # 最多点赞


class SearchVideoType(Enum):
    ALL = "1"           # 视频
