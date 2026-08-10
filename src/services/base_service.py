# service/base_service.py
from functools import wraps
from fastapi import HTTPException, status

def require_ownership(model_class, user_id_field="user_id"):
    """
    权限校验装饰器：确保操作对象属于当前用户
    用法：@require_ownership(DailyDietLog, "user_id")
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            db = kwargs.get('db')
            current_user_id = kwargs.get('current_user_id')
            obj_id = kwargs.get('obj_id')

            if not db or not current_user_id or not obj_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="缺少必要参数"
                )

            obj = db.query(model_class).filter(
                model_class.id == obj_id,
                getattr(model_class, user_id_field) == current_user_id
            ).first()

            if not obj:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权访问此资源"
                )

            # 将校验后的对象注入 kwargs，供业务函数使用
            kwargs['verified_obj'] = obj
            return func(*args, **kwargs)
        return wrapper
    return decorator