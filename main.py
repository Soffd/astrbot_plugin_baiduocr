from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.api.all import *
import tempfile
import os
import asyncio
import time
import re
import sys
import json
import base64
import aiohttp

@register("百度OCR文字识别", "Yuki Soffd", "提供OCR文字识别功能，通过/提取文字指令触发", "1.1")
class OCRPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.temp_dir = os.path.join(tempfile.gettempdir(), "astrbot_ocr")
        os.makedirs(self.temp_dir, exist_ok=True)
        # 存储access token
        self.access_token = None
        # 存储token过期时间
        self.token_expire_time = 0
        
        # 从配置中读取API参数
        self.api_key = config.get("api_key", "")
        self.secret_key = config.get("secret_key", "")
        self.token_url = config.get("token_url", "https://aip.baidubce.com/oauth/2.0/token")
        self.ocr_url = config.get("ocr_url", "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic")
        
        # 检查必要配置
        if not self.api_key or not self.secret_key:
            logger.warning("百度OCR API密钥未配置，请检查配置文件！")

    async def initialize(self):
        """插件初始化"""
        logger.info("百度OCR文字识别")
        if self.api_key and self.secret_key:
            await self.get_access_token()
    
    async def get_access_token(self):
        """获取百度API的access token"""
        if not self.api_key or not self.secret_key:
            return None
            
        if self.access_token and time.time() < self.token_expire_time:
            return self.access_token
            
        params = {
            'grant_type': 'client_credentials',
            'client_id': self.api_key,
            'client_secret': self.secret_key
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.token_url, data=params) as response:
                    result = await response.json()
                    
                    if 'access_token' in result:
                        self.access_token = result['access_token']
                        # 设置token过期时间（提前5分钟刷新）
                        self.token_expire_time = time.time() + result['expires_in'] - 300
                        return self.access_token
                    else:
                        error_msg = result.get('error_description', '未知错误')
                        logger.error(f"获取百度API token失败: {error_msg}")
                        return None
        except Exception as e:
            logger.error(f"获取百度API token异常: {str(e)}")
            return None

    
    async def download_image(self, event: AstrMessageEvent, file_id: str) -> tuple:
        """
        下载图片到临时目录
        返回元组: (插件临时文件路径, astrbot原始图片路径)
        """
        original_path = None
        
        try:
            image_obj = next(
                (msg for msg in event.get_messages() 
                 if isinstance(msg, Image) and msg.file == file_id),
                None
            )
        
            if not image_obj:
                return ("", None)
        
            # 尝试直接获取文件路径
            file_path = await image_obj.convert_to_file_path()
            if file_path and os.path.exists(file_path):
                # 记录原始文件路径
                original_path = file_path
                with open(file_path, "rb") as f:
                    data = f.read()
            else:
                # 通过API获取图片
                client = event.bot
                result = await client.api.call_action("get_image", file_id=file_id)
                file_path = result.get("file")
                if not file_path:
                    return ("", None)
                # 记录原始文件路径
                original_path = file_path
                with open(file_path, "rb") as f:
                    data = f.read()
        
            # 创建插件的临时文件
            temp_path = os.path.join(self.temp_dir, f"ocr_{int(time.time())}.jpg")
            with open(temp_path, "wb") as f:
                f.write(data)
            
            return (temp_path, original_path)
        
        except Exception as e:
            logger.error(f"图片下载失败: {str(e)}", exc_info=True)
            return ("", None)
        
    async def _perform_ocr(self, image_path: str) -> str:
        """使用百度OCR API执行文字识别"""
        try:
            token = await self.get_access_token()
            if not token:
                return "OCR服务认证失败"
            
            # 读取图片并转换为base64
            with open(image_path, 'rb') as f:
                image_data = f.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
            
            # 设置请求参数
            params = {
                "image": base64_data,
                "language_type": "CHN_ENG",   # 中英文混合
                "detect_direction": "true",   # 检测朝向
                "paragraph": "true",          # 输出段落信息
                "probability": "false"        # 不返回识别结果中每一行的置信度
            }
            
            # 构造请求URL
            url = f"{self.ocr_url}?access_token={token}"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=params) as response:
                    result = await response.json()
                    
                    if 'error_code' in result:
                        error_msg = result.get('error_msg', '未知错误')
                        logger.error(f"百度OCR API错误: {error_msg} (错误码: {result['error_code']})")
                        return f"OCR识别失败: {error_msg}"
                    
                    text_list = [words['words'] for words in result.get('words_result', [])]
                    text = '\n'.join(text_list)                   
                    text = re.sub(r'\n\s*\n', '\n', text) 
                    return text.strip()
        
        except Exception as e:
            logger.error(f"OCR识别失败: {str(e)}", exc_info=True)
            return ""
    
    async def cleanup_files(self, paths: list):
        """异步清理临时文件"""
        await asyncio.sleep(3)
        for path in paths:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.info(f"已成功删除文件: {path}")
                except Exception as e:
                    logger.warning(f"清理临时文件失败 {path}: {str(e)}")
    
    @filter.command("提取文字")
    async def ocr_command(self, event: AstrMessageEvent):
        """OCR识别：/提取文字 [图片]"""
        messages = event.get_messages()
        images = [msg for msg in messages if isinstance(msg, Image)]
        
        if not images:
            yield event.plain_result("请发送一张包含文字的图片")
            return
        
        try:
            # 调用OCR功能，这里需要传递文件ID
            file_id = images[0].file
            temp_path, original_path = await self.download_image(event, file_id)
            if not temp_path:
                yield event.plain_result("图片下载失败")
                return
                
            text = await self._perform_ocr(temp_path)
            files_to_delete = []
            if temp_path and os.path.exists(temp_path):
                files_to_delete.append(temp_path)
            
            if original_path and os.path.exists(original_path):
                files_to_delete.append(original_path)
            
            if files_to_delete:
                asyncio.create_task(self.cleanup_files(files_to_delete))
            
            if not text.strip():
                yield event.plain_result("未识别到文字，请检查图片清晰度")
            else:
                yield event.plain_result(f"图片中的文字内容：\n{text}")
            
        except Exception as e:
            logger.error(f"OCR命令处理失败: {str(e)}", exc_info=True)
            yield event.plain_result(f"OCR处理失败: {str(e)}")
    
    async def terminate(self):
        """插件销毁时清理资源"""
        logger.info("OCR插件已卸载")
