"""
音频/视频加载器（基于 OpenAI Whisper）

职责：
- 从音频文件（mp3/wav/m4a/flac/aac 等）中转写文本
- 从视频文件（mp4/avi/mov/mkv 等）中提取音轨并转写文本
- 支持指定语言模型大小（tiny/base/small/medium/large）
- 支持指定设备（CPU/GPU）和语言
- 将转写文本切分为指定大小的块

依赖：
- openai-whisper (pip install openai-whisper)
- torch (pip install torch)
- ffmpeg（系统级依赖，需安装到 PATH）

注意：
- 首次运行时 Whisper 会自动下载模型（约 74MB~2.9GB，取决于 model 大小）
- base 模型约 74MB，适合中文转写，速度和准确率平衡较好
- 如需更高准确率可切换到 small(244MB) 或 medium(769MB)
"""

import os
import subprocess
import tempfile
from typing import Optional, List

from src.config import settings
from src.utils.logger import setup_logger

logger = setup_logger(name="AudioLoader")

# 支持的文件扩展名
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}


class AudioLoader:
    """
    音频/视频转写加载器

    使用 OpenAI Whisper 模型进行语音转文字。
    首次使用时会自动下载模型文件到本地缓存。

    用法：
        loader = AudioLoader()
        text = loader.load("interview.mp4")  # 直接传入视频文件
        chunks = loader.load_and_chunk("lecture.mp3", metadata={"source": "expert"})
    """

    def __init__(
        self,
        model_name: str = None,
        device: str = None,
        language: str = None,
    ):
        """
        初始化音频加载器

        Args:
            model_name: Whisper 模型大小，可选 tiny/base/small/medium/large
                        默认从配置读取 (WHISPER_MODEL)
            device: 计算设备，可选 auto/cpu/cuda
                    auto 自动检测 CUDA，无 GPU 时回退到 CPU
            language: 音频语言代码，如 zh/en/ja
                      设为 None 时 Whisper 自动检测
        """
        self.model_name = model_name or settings.WHISPER_MODEL
        self.device = self._resolve_device(device or settings.WHISPER_DEVICE)
        self.language = self._resolve_language(language or settings.WHISPER_LANGUAGE)
        self._model = None  # 延迟加载，避免导入时下载模型

    # ============================================================
    # 公共接口
    # ============================================================

    def load(self, file_path: str) -> Optional[str]:
        """
        从音频/视频文件中转写文本

        自动判断文件类型：
        - 音频文件：直接转写
        - 视频文件：先用 ffmpeg 提取音轨，再转写

        Args:
            file_path: 音频/视频文件路径

        Returns:
            转写后的完整文本，失败返回 None
        """
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None

        ext = os.path.splitext(file_path)[1].lower()

        # 视频文件：先提取音轨到临时 wav 文件
        if ext in VIDEO_EXTENSIONS:
            logger.info(f"检测到视频文件 ({ext})，正在提取音轨: {file_path}")
            audio_path = self._extract_audio_from_video(file_path)
            if audio_path is None:
                return None
            try:
                text = self._transcribe(audio_path)
                return text
            finally:
                # 转写完成后删除临时音频文件
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                    logger.debug(f"已清理临时音频文件: {audio_path}")

        # 音频文件：直接转写
        elif ext in AUDIO_EXTENSIONS:
            return self._transcribe(file_path)

        else:
            logger.error(f"不支持的文件格式: {ext} (支持: {AUDIO_EXTENSIONS | VIDEO_EXTENSIONS})")
            return None

    def load_and_chunk(
        self,
        file_path: str,
        metadata: Optional[dict] = None,
        chunk_size: int = 500,
    ) -> List[dict]:
        """
        转写音频/视频并将文本切分为块

        Args:
            file_path: 音频/视频文件路径
            metadata: 附加到每个块的元数据
            chunk_size: 每块的目标字符数

        Returns:
            文本块列表，每块格式 {"content": str, "metadata": dict}
        """
        text = self.load(file_path)
        if not text:
            return []

        return self._chunk_text(text, metadata or {}, chunk_size)

    # ============================================================
    # 内部实现
    # ============================================================

    def _transcribe(self, audio_path: str) -> Optional[str]:
        """
        使用 Whisper 模型转写音频文件

        Args:
            audio_path: 音频文件路径（wav/mp3 等）

        Returns:
            转写文本
        """
        try:
            model = self._get_model()
        except ImportError:
            logger.error(
                "whisper 未安装，请运行: pip install openai-whisper\n"
                "同时需要安装 ffmpeg 并添加到系统 PATH"
            )
            return None
        except Exception as e:
            logger.error(f"Whisper 模型加载失败: {e}")
            return None

        try:
            logger.info(f"开始转写: {audio_path} (model={self.model_name}, language={self.language})")

            # Whisper 转写参数
            transcribe_kwargs = {
                "fp16": self.device != "cpu",  # CPU 模式下禁用 fp16
            }
            if self.language:
                transcribe_kwargs["language"] = self.language

            result = model.transcribe(audio_path, **transcribe_kwargs)
            text = result.get("text", "").strip()

            # 日志记录转写信息
            segments = result.get("segments", [])
            duration = result.get("segments", [{}])[-1].get("end", 0) if segments else 0
            logger.info(
                f"转写完成: {len(text)} 字符, "
                f"{len(segments)} 段, "
                f"时长 {duration:.1f}s"
            )

            return text

        except Exception as e:
            logger.error(f"转写失败 {audio_path}: {e}")
            return None

    def _get_model(self):
        """
        延迟加载 Whisper 模型（首次调用时加载，后续复用）

        尝试导入 openai-whisper 包。
        如果环境中存在同名的 Graphite whisper.py 文件（数据库模块），
        会产生冲突，此时提示用户卸载错误包。

        Returns:
            whisper.WhisperAPI 模型实例
        """
        if self._model is None:
            try:
                import whisper

                # 验证是 OpenAI Whisper，而非 Graphite 数据库的 whisper
                if not hasattr(whisper, "load_model"):
                    raise ImportError(
                        "导入的 whisper 模块不是 OpenAI Whisper（可能是 Graphite 数据库的 whisper.py）。\n"
                        "请卸载错误包: pip uninstall whisper\n"
                        "然后重新安装: pip install openai-whisper"
                    )
            except ImportError as e:
                logger.error(
                    f"openai-whisper 未正确安装: {e}\n"
                    "请运行以下命令修复:\n"
                    "  1. pip uninstall whisper  (卸载 Graphite 的同名包)\n"
                    "  2. pip install openai-whisper  (安装 OpenAI Whisper)\n"
                    "  3. 确保系统 PATH 中有 ffmpeg"
                )
                raise

            logger.info(f"加载 Whisper 模型: {self.model_name} (device={self.device})")
            self._model = whisper.load_model(self.model_name, device=self.device)

        return self._model

    def _extract_audio_from_video(self, video_path: str) -> Optional[str]:
        """
        从视频文件中提取音轨为 WAV 文件

        使用 ffmpeg 将视频的音频流转换为 16kHz 单声道 WAV，
        这是 Whisper 推荐的音频格式。

        ffmpeg 来源优先级：
        1. 系统 PATH 中的 ffmpeg
        2. imageio-ffmpeg 包自带的 ffmpeg 二进制文件

        Args:
            video_path: 视频文件路径

        Returns:
            临时 WAV 文件路径，失败返回 None
        """
        # 获取 ffmpeg 可执行文件路径
        ffmpeg_bin = self._get_ffmpeg_path()
        if not ffmpeg_bin:
            logger.error(
                "ffmpeg 未找到。请选择以下方式之一安装:\n"
                "  方式1: conda install -c conda-forge ffmpeg\n"
                "  方式2: pip install imageio-ffmpeg (自动下载 ffmpeg 二进制)\n"
                "  方式3: 从 https://ffmpeg.org/download.html 下载并添加到 PATH"
            )
            return None

        # 创建临时文件（用纯 ASCII 文件名避免中文路径问题）
        temp_dir = tempfile.gettempdir()
        import hashlib
        name_hash = hashlib.md5(video_path.encode("utf-8")).hexdigest()[:8]
        temp_wav = os.path.join(temp_dir, f"whisper_extract_{name_hash}_{os.getpid()}.wav")

        # ffmpeg 命令：
        # -i          输入文件
        # -vn         禁用视频（只提取音频）
        # -acodec     音频编码器（PCM 16-bit）
        # -ar 16000   采样率 16kHz（Whisper 推荐）
        # -ac 1       单声道
        cmd = [
            ffmpeg_bin,
            "-i", video_path,
            "-vn",                    # 不要视频流
            "-acodec", "pcm_s16le",   # 16-bit PCM
            "-ar", "16000",           # 16kHz 采样率
            "-ac", "1",               # 单声道
            "-y",                     # 覆盖输出文件
            temp_wav,
        ]

        try:
            # 运行 ffmpeg，捕获输出（用 bytes 避免中文路径 GBK 编码错误）
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=600,  # 10 分钟超时
            )

            if result.returncode != 0:
                stderr_msg = result.stderr.decode("utf-8", errors="replace")[:500]
                logger.error(f"ffmpeg 提取音轨失败 (返回码 {result.returncode})")
                logger.error(f"ffmpeg stderr: {stderr_msg}")
                return None

            if not os.path.exists(temp_wav):
                logger.error(f"ffmpeg 未生成输出文件: {temp_wav}")
                return None

            logger.info(f"音轨提取成功: {temp_wav}")
            return temp_wav

        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg 提取音轨超时（视频可能过长）: {video_path}")
            return None
        except Exception as e:
            logger.error(f"ffmpeg 提取音轨异常: {e}")
            return None

    def _get_ffmpeg_path(self) -> Optional[str]:
        """
        获取 ffmpeg 可执行文件路径

        查找优先级：
        1. imageio-ffmpeg 包提供的完整版 ffmpeg（默认支持 wav muxer）
        2. 系统 PATH 中的 ffmpeg（验证支持 wav 输出后再使用）

        背景：PATH 中可能存在精简编译的 ffmpeg（如某些 IDE 内置版本仅
        启用 mp4 muxer，不支持 wav 输出），会导致音频提取失败。

        Returns:
            ffmpeg 可执行文件路径，未找到返回 None
        """
        import shutil

        # 1. 优先使用 imageio-ffmpeg（自带完整版二进制，支持 wav muxer）
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
            if ffmpeg_bin and os.path.exists(ffmpeg_bin):
                logger.info(f"使用 imageio-ffmpeg 提供的 ffmpeg: {ffmpeg_bin}")
                # Whisper 内部通过 shutil.which("ffmpeg") 调用 ffmpeg，
                # 但 imageio-ffmpeg 的二进制名为 ffmpeg-win-x86_64-vX.X.exe，
                # Whisper 找不到。创建一个名为 ffmpeg.exe 的副本到临时目录，
                # 并将该目录加入 PATH，让 Whisper 能找到。
                self._ensure_ffmpeg_in_path(ffmpeg_bin)
                return ffmpeg_bin
        except ImportError:
            pass

        # 2. 回退到系统 PATH 中的 ffmpeg（需验证支持 wav 输出）
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            if self._ffmpeg_supports_wav(ffmpeg_bin):
                logger.info(f"使用系统 PATH 中的 ffmpeg: {ffmpeg_bin}")
                return ffmpeg_bin
            logger.warning(
                f"系统 PATH 中的 ffmpeg ({ffmpeg_bin}) 不支持 wav muxer 输出，"
                "可能是精简编译版本。建议安装 imageio-ffmpeg: pip install imageio-ffmpeg"
            )

        return None

    @staticmethod
    def _ffmpeg_supports_wav(ffmpeg_bin: str) -> bool:
        """
        检测 ffmpeg 是否支持 wav 输出（pcm_s16le 编码 + wav muxer）

        某些精简编译的 ffmpeg（如 IDE 内置版本）仅启用 mp4 muxer，
        无法输出 wav 文件，会导致 Whisper 音轨提取失败。

        Args:
            ffmpeg_bin: ffmpeg 可执行文件路径

        Returns:
            True 表示支持 wav 输出
        """
        try:
            result = subprocess.run(
                [ffmpeg_bin, "-muxers", "-hide_banner"],
                capture_output=True,
                timeout=10,
            )
            output = (result.stdout or b"").decode("utf-8", errors="replace")
            # muxers 列表中形如 "  E wav               WAV / WAVE (Waveform Audio)"
            return "wav" in output.lower()
        except Exception as e:
            logger.warning(f"检测 ffmpeg wav 支持失败: {e}，假设不支持")
            return False

    @staticmethod
    def _ensure_ffmpeg_in_path(ffmpeg_bin: str) -> None:
        """
        确保 Whisper 内部能通过 PATH 找到支持 wav 的 ffmpeg

        Whisper 的 load_audio() 调用 subprocess.run(["ffmpeg", ...])，
        依赖 PATH 中存在名为 ffmpeg（Windows 上是 ffmpeg.exe）的可执行文件。
        imageio-ffmpeg 提供的二进制文件名不是标准 ffmpeg.exe，需要创建副本。

        特殊处理：若 PATH 中已存在 ffmpeg 但不支持 wav muxer（精简编译版本），
        需要将 imageio-ffmpeg 的 ffmpeg.exe 副本目录放到 PATH 最前面，
        覆盖 PATH 中的精简版，否则 Whisper 调用时会再次失败。

        Args:
            ffmpeg_bin: imageio-ffmpeg 提供的 ffmpeg 完整路径
        """
        import shutil

        temp_dir = tempfile.gettempdir()
        alias_path = os.path.join(temp_dir, "ffmpeg.exe")

        # 检查 PATH 中的 ffmpeg 是否支持 wav
        path_ffmpeg = shutil.which("ffmpeg")
        path_ffmpeg_ok = path_ffmpeg and AudioLoader._ffmpeg_supports_wav(path_ffmpeg)

        # PATH 中已存在支持 wav 的 ffmpeg，无需处理
        if path_ffmpeg_ok:
            return

        # PATH 中的 ffmpeg 不可用（精简版或缺失），需要用 imageio-ffmpeg 副本覆盖
        try:
            # 如果副本不存在或源文件更新了，重新复制
            need_copy = True
            if os.path.exists(alias_path):
                src_mtime = os.path.getmtime(ffmpeg_bin)
                alias_mtime = os.path.getmtime(alias_path)
                if src_mtime <= alias_mtime:
                    need_copy = False

            if need_copy:
                shutil.copy(ffmpeg_bin, alias_path)
                logger.info(f"已创建 ffmpeg.exe 别名（供 Whisper 使用）: {alias_path}")

            # 把临时目录加入 PATH 最前面，覆盖 PATH 中的精简版 ffmpeg
            # 注意：必须放在最前面，否则 PATH 中先找到的精简版会优先被调用
            os.environ["PATH"] = temp_dir + os.pathsep + os.environ.get("PATH", "")
            if path_ffmpeg:
                logger.warning(
                    f"PATH 中的 ffmpeg ({path_ffmpeg}) 不支持 wav，"
                    f"已用 imageio-ffmpeg 副本覆盖: {alias_path}"
                )
        except Exception as e:
            logger.warning(f"创建 ffmpeg.exe 别名失败（Whisper 可能无法转写）: {e}")

    def _chunk_text(self, text: str, metadata: dict, chunk_size: int) -> List[dict]:
        """
        将文本切分为指定大小的块

        优先在句子/段落边界处切分，避免截断句子。

        Args:
            text: 待切分文本
            metadata: 附加到每个块的元数据
            chunk_size: 每块目标字符数

        Returns:
            文本块列表
        """
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)

            # 尝试在句号/换行处切分，避免截断句子
            if end < text_len:
                # 在 [start+chunk_size*0.7, end] 范围内寻找最近的句子结束符
                search_start = start + int(chunk_size * 0.7)
                for i in range(end, search_start, -1):
                    if text[i - 1] in "。！？\n；":
                        end = i
                        break

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunk_meta = {**metadata}
                chunk_meta["char_start"] = start
                chunk_meta["char_end"] = end
                chunks.append({
                    "content": chunk_text,
                    "metadata": chunk_meta,
                })

            start = end

        logger.info(f"音频转写文本切分完成: {len(chunks)} 个块 (chunk_size={chunk_size})")
        return chunks

    def _resolve_device(self, device: str) -> str:
        """
        解析计算设备

        auto → 自动检测 CUDA，有 GPU 用 cuda，否则用 cpu
        """
        if device == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                logger.warning("torch 未安装，无法检测 GPU，使用 CPU 模式")
                return "cpu"
        return device

    def _resolve_language(self, language: str) -> Optional[str]:
        """
        解析语言设置

        空字符串或 "auto" → None（让 Whisper 自动检测）
        """
        if not language or language.lower() == "auto":
            return None
        return language