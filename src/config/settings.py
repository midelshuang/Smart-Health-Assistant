import os
import dotenv

base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# print(base_dir)

# 规定文件路径（营养成分表）
Nutrition_file_path = os.path.join(base_dir, 'raw_data', 'china-food-composition-data', 'json_data')
# print(Nutrition_file_path)
# 指定GI文件路径
GI_file_path = os.path.join(base_dir, 'raw_data','china-food-composition-data', 'json_gi_of_foods','glycemic_index_of_foods.json')
# print(GI_file_path)

# 数据库连接字符串
dotenv.load_dotenv()
DB_CONNECTION_STRING = os.getenv("DATABASE_URL")

# 规定文件路径（原始数据）
Authoritative_file_path = os.path.join(base_dir, 'raw_data', 'raw')
# print(Authoritative_file_path)

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")  # 默认 HS256

# 验证配置是否加载
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY 未在环境变量中设置！")

ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# 登录限流策略
LOGIN_MAX_FAIL_BY_ACCOUNT = int(os.getenv("LOGIN_MAX_FAIL_BY_ACCOUNT", "5"))
LOGIN_MAX_FAIL_BY_IP = int(os.getenv("LOGIN_MAX_FAIL_BY_IP", "20"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))

# ===== SMTP 邮件配置（用于邮箱注册验证码） =====
# 若未配置 SMTP，验证码将仅写入日志（开发模式）
SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "true").lower() == "true"
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # 邮箱授权码，非登录密码
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "HealthAI 平台")

# 验证码策略
EMAIL_CODE_LENGTH = 6                 # 验证码位数
EMAIL_CODE_TTL_SECONDS = 5 * 60       # 验证码有效期 5 分钟
EMAIL_CODE_RESEND_INTERVAL = 60       # 同一邮箱重发间隔（秒）
EMAIL_CODE_MAX_SEND_PER_DAY = 10      # 同一邮箱每日最大发送次数

# ============================================================
# RAG / AI 配置
# ============================================================

# LLM 配置
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))

# Embedding 配置
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))

# ChromaDB 配置
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "authoritative_sources")

# RAG 管道配置
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "10"))
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.3"))
RAG_MAX_REWRITE = int(os.getenv("RAG_MAX_REWRITE", "2"))
RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "500"))
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))
# 追问触发阈值：向量检索最高相似度低于此值时，由 LLM 生成追问而非强行猜测
RAG_CLARIFICATION_THRESHOLD = float(os.getenv("RAG_CLARIFICATION_THRESHOLD", "0.75"))

# Whisper 语音转写配置
# model: tiny/base/small/medium/large —— 越大越准确但越慢
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
# device: auto/cpu/gpu —— auto 自动检测 CUDA
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
# language: zh/en/ja... —— 指定语言可提高准确率，None 为自动检测
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "zh")

# ============================================================
# LangSmith 配置（RAG 全流程监控追踪）
# ============================================================
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "healthai-rag")
LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

# 将 LangSmith 配置写入 os.environ，确保 LangGraph 能自动读取
if LANGSMITH_TRACING and LANGSMITH_API_KEY:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT
    os.environ["LANGSMITH_ENDPOINT"] = LANGSMITH_ENDPOINT

# ============================================================
# Redis 缓存配置（V1.2/V1.3 性能优化）
# ============================================================
# Redis 不可用时自动降级为无缓存模式，不影响主流程
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "2.0"))
REDIS_SOCKET_CONNECT_TIMEOUT = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "2.0"))
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "10"))

# 将 Redis 配置写入 os.environ，供 utils.redis_client 读取
os.environ.setdefault("REDIS_HOST", REDIS_HOST)
os.environ.setdefault("REDIS_PORT", str(REDIS_PORT))
os.environ.setdefault("REDIS_PASSWORD", REDIS_PASSWORD)
os.environ.setdefault("REDIS_DB", str(REDIS_DB))
