from collector.base import AbstractCrawler

CRAWLER_REGISTRY = {
    "bilibili": "collector.bilibili.crawler.BilibiliCrawler",
    "xhs": "collector.xhs.crawler.XhsCrawler",
    "douyin": "collector.douyin.crawler.DouyinCrawler",
}


def create_crawler(platform: str) -> AbstractCrawler:
    path = CRAWLER_REGISTRY.get(platform)
    if not path:
        raise ValueError(f"Unsupported platform: {platform}")
    module_path, class_name = path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()
