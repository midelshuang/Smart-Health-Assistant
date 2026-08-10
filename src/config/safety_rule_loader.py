"""
安全规则加载器（SafetyRuleLoader）

从 config/safety_rules.yaml 加载合规规则，支持热更新：
- 每次访问规则时检测文件 mtime
- 文件变更后自动重新加载，无需重启服务
- 单例模式，全进程共享同一份缓存

使用方式：
    from src.config.safety_rule_loader import get_safety_rules
    rules = get_safety_rules()  # 返回 SafetyRulesConfig 对象
    for rule in rules.regex_rules:
        if rule.pattern.search(query):
            ...

热更新机制：
- 通过 os.stat().st_mtime 检测文件变更
- 文件未变更时直接返回缓存，避免重复 IO
- 加载异常时保留上一次的有效配置，避免规则加载失败导致服务不可用
"""

import os
import re
import json
import threading
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Pattern
from pathlib import Path

import yaml

from src.utils.logger import setup_logger

logger = setup_logger(name="SafetyRuleLoader")


# ============================================================
# 数据类定义
# ============================================================

@dataclass
class RegexRule:
    """单条正则规则"""
    name: str
    pattern: Pattern  # 编译后的正则对象
    reason: str
    enabled: bool = True
    raw_pattern: str = ""  # 原始字符串，用于调试


@dataclass
class CategoryResponse:
    """分类回复"""
    reply: str
    suggestion: str


@dataclass
class SafetyRulesConfig:
    """安全规则配置（不可变）"""
    version: str = ""
    description: str = ""
    regex_rules: List[RegexRule] = field(default_factory=list)
    llm_prompt: str = ""
    category_responses: Dict[str, CategoryResponse] = field(default_factory=dict)
    global_enabled: bool = True
    loaded_at: float = 0.0
    source_path: str = ""

    def get_response(self, category: str) -> CategoryResponse:
        """获取指定类别的回复，不存在时返回 other"""
        return self.category_responses.get(
            category,
            self.category_responses.get(
                "other",
                CategoryResponse(reply="", suggestion=""),
            ),
        )


# ============================================================
# 安全规则加载器（单例 + 热更新）
# ============================================================

class SafetyRuleLoader:
    """
    安全规则加载器（单例）

    特性：
    1. 懒加载：第一次访问时加载 YAML
    2. 热更新：每次 get_rules() 检测 mtime，变化则重新加载
    3. 线程安全：加载过程加锁，避免并发重复加载
    4. 容错：加载失败时保留上一次有效配置，避免规则丢失导致服务降级
    """

    _instance: Optional["SafetyRuleLoader"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        # 默认配置文件路径：项目根目录/config/safety_rules.yaml
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = str(project_root / "config" / "safety_rules.yaml")
        self._config_path = config_path

        self._config: Optional[SafetyRulesConfig] = None
        self._last_mtime: float = 0.0
        self._last_check_time: float = 0.0
        # mtime 检查间隔（秒），避免每次访问都做 stat 系统调用
        self._check_interval: float = float(
            os.getenv("SAFETY_RULE_CHECK_INTERVAL", "5")
        )

        logger.info(f"SafetyRuleLoader 初始化: config_path={self._config_path}")

    def get_rules(self) -> SafetyRulesConfig:
        """
        获取当前安全规则配置（自动热更新）

        Returns:
            SafetyRulesConfig 对象
        """
        now = time.time()
        # 限频检查 mtime，避免高频访问下的 stat 开销
        if (
            self._config is not None
            and now - self._last_check_time < self._check_interval
        ):
            return self._config

        self._last_check_time = now

        try:
            current_mtime = os.stat(self._config_path).st_mtime
        except OSError as e:
            logger.error(f"安全规则文件访问失败: {e}")
            # 文件访问失败，返回上一次缓存（如果有）
            if self._config is not None:
                return self._config
            # 没有缓存，返回空配置（fail-open：放行所有查询）
            logger.warning("无缓存且文件不可用，使用空规则（fail-open）")
            return SafetyRulesConfig(loaded_at=now)

        # mtime 未变化，直接返回缓存
        if self._config is not None and current_mtime == self._last_mtime:
            return self._config

        # mtime 变化，重新加载
        logger.info(
            f"检测到安全规则文件变更 (mtime {self._last_mtime} → {current_mtime})，重新加载"
        )
        with self._lock:
            # 双重检查，避免并发场景下重复加载
            if (
                self._config is not None
                and current_mtime == self._last_mtime
            ):
                return self._config
            self._load_from_file(current_mtime)

        return self._config

    def _load_from_file(self, mtime: float) -> None:
        """从 YAML 文件加载规则"""
        start = time.time()
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise ValueError("YAML 根结构必须是字典")

            config = self._parse_config(data, mtime)
            self._config = config
            self._last_mtime = mtime
            elapsed = (time.time() - start) * 1000
            logger.info(
                f"安全规则加载完成: version={config.version}, "
                f"regex_rules={len(config.regex_rules)}, "
                f"categories={len(config.category_responses)}, "
                f"耗时={elapsed:.1f}ms"
            )
        except Exception as e:
            logger.error(f"安全规则加载失败: {e}")
            # 保留上一次的有效配置，避免规则丢失
            if self._config is None:
                # 第一次加载就失败，创建空配置 fail-open
                logger.warning("首次加载失败，使用空规则（fail-open）")
                self._config = SafetyRulesConfig(loaded_at=time.time())
            else:
                logger.warning("加载失败，保留上一次缓存配置")

    def _parse_config(self, data: Dict, mtime: float) -> SafetyRulesConfig:
        """解析 YAML 数据为 SafetyRulesConfig"""
        # 解析正则规则
        regex_rules: List[RegexRule] = []
        for rule_data in data.get("regex_rules", []) or []:
            try:
                flags = 0
                for flag_name in (rule_data.get("flags") or []):
                    if flag_name == "ignorecase":
                        flags |= re.IGNORECASE
                    elif flag_name == "multiline":
                        flags |= re.MULTILINE
                    elif flag_name == "dotall":
                        flags |= re.DOTALL
                compiled = re.compile(rule_data["pattern"], flags)
                regex_rules.append(
                    RegexRule(
                        name=rule_data["name"],
                        pattern=compiled,
                        reason=rule_data.get("reason", ""),
                        enabled=rule_data.get("enabled", True),
                        raw_pattern=rule_data["pattern"],
                    )
                )
            except (KeyError, re.error) as e:
                logger.error(
                    f"正则规则解析失败，跳过: name={rule_data.get('name')}, error={e}"
                )

        # 解析分类回复
        category_responses: Dict[str, CategoryResponse] = {}
        for cat, resp in (data.get("category_responses") or {}).items():
            category_responses[cat] = CategoryResponse(
                reply=resp.get("reply", ""),
                suggestion=resp.get("suggestion", ""),
            )

        return SafetyRulesConfig(
            version=str(data.get("version", "")),
            description=str(data.get("description", "")),
            regex_rules=regex_rules,
            llm_prompt=str(data.get("llm_prompt", "")),
            category_responses=category_responses,
            global_enabled=bool(data.get("global_enabled", True)),
            loaded_at=time.time(),
            source_path=self._config_path,
        )

    def force_reload(self) -> SafetyRulesConfig:
        """
        强制重新加载（用于管理接口主动触发）

        Returns:
            重新加载后的配置
        """
        logger.info("强制重新加载安全规则")
        try:
            mtime = os.stat(self._config_path).st_mtime
        except OSError as e:
            logger.error(f"强制重载失败: {e}")
            return self._config or SafetyRulesConfig()

        with self._lock:
            self._load_from_file(mtime)
        return self._config

    def export_json(self) -> str:
        """
        导出当前规则为 JSON（用于管理接口查看）

        Returns:
            JSON 字符串
        """
        if self._config is None:
            return "{}"
        return json.dumps(
            {
                "version": self._config.version,
                "description": self._config.description,
                "regex_rules": [
                    {
                        "name": r.name,
                        "pattern": r.raw_pattern,
                        "reason": r.reason,
                        "enabled": r.enabled,
                    }
                    for r in self._config.regex_rules
                ],
                "category_responses": {
                    cat: {"reply": r.reply, "suggestion": r.suggestion}
                    for cat, r in self._config.category_responses.items()
                },
                "global_enabled": self._config.global_enabled,
                "loaded_at": self._config.loaded_at,
                "source_path": self._config.source_path,
            },
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# 模块级便捷函数
# ============================================================

def get_safety_rules() -> SafetyRulesConfig:
    """获取当前安全规则配置（自动热更新）"""
    return SafetyRuleLoader().get_rules()


def reload_safety_rules() -> SafetyRulesConfig:
    """强制重新加载安全规则（用于管理接口）"""
    return SafetyRuleLoader().force_reload()
