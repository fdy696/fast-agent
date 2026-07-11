"""任务队列 —— SAQ + Redis."""
import saq
from core.config import settings

queue = saq.Queue.from_url(settings.REDIS_URL)
