# main.py
from web_api import app  # ✅ 只导入，不定义

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)