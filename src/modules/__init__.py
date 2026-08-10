"""
V4 预留模块占位包

此包为 V4 版本的业务模块预留目录，当前版本不包含实现。
V4 将包含以下子模块：

- social/   : 社交功能（关注/粉丝/动态/评论）
- expert/   : 专家服务（认证/付费咨询/专属内容）
- content/  : 内容管理（UGC/审核/标签体系）

预留策略：
1. 当前版本仅创建占位目录与 __init__.py
2. 数据库表已预留相关字段（user_profiles.role/visibility 等）
3. Redis Key 规范已定义（见 config/redis_key_spec.py）
4. API 路由预留 v2 占位接口（src/api/routes/v2.py）

启用 V4 模块时：
1. 在此目录下创建对应子模块
2. 在 src/api/routes/ 添加新路由文件
3. 在 src/api/main.py 注册新路由
4. 实施对应数据库迁移
"""
