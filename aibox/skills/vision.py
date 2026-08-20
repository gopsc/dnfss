#!/usr/bin/env python3
"""视觉理解工具 - 理解图片内容。"""

import os
import sys
import argparse
import base64
import json
import requests

# 豆包API配置
DOUBAO_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"


def get_api_key():
    """获取豆包API密钥"""
    key_path = os.path.expanduser("~/.cert/doubao.key")
    try:
        with open(key_path, 'r') as f:
            return f.read().strip()
    except Exception as e:
        return None


def detect_image_mime(image_path):
    """S12（审计 2026-08-20）：按魔数识别真实图片类型——
    拒绝非图片文件（防任意文件读取外泄），并返回正确 MIME（此前硬编码 image/jpeg）"""
    try:
        with open(image_path, 'rb') as f:
            head = f.read(16)
    except Exception:
        return None
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'BM'):
        return 'image/bmp'
    return None


def encode_image_to_base64(image_path):
    """将图片编码为base64"""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def analyze_image(image_path, prompt, stream=False):
    """调用豆包API分析图片"""
    api_key = get_api_key()
    if not api_key:
        return "❌ 错误：无法读取API密钥，请确保 ~/.cert/doubao.key 文件存在"
    
    if not os.path.exists(image_path):
        return f"❌ 错误：图片文件不存在 - {image_path}"

    mime = detect_image_mime(image_path)
    if not mime:
        return f"❌ 错误：文件不是有效图片（仅支持 JPEG/PNG/GIF/WebP/BMP） - {image_path}"

    try:
        base64_image = encode_image_to_base64(image_path)
    except Exception as e:
        return f"❌ 错误：无法读取图片文件 - {str(e)}"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{base64_image}"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }
    ]
    
    payload = {
        "model": DOUBAO_MODEL,
        "messages": messages,
        "stream": stream,
        "max_tokens": 4096,
        "temperature": 0.7
    }
    
    try:
        response = requests.post(
            DOUBAO_API_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=60
        )
        
        if response.status_code != 200:
            return f"❌ API请求失败 (HTTP {response.status_code}): {response.text}"
        
        if stream:
            return response
        else:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "无返回内容")
            return content
            
    except Exception as e:
        return f"❌ API调用错误: {str(e)}"


def main():
    parser = argparse.ArgumentParser(
        description="视觉理解工具 - 理解图片内容。",
        epilog="""
使用示例:
  # 描述图片内容
  vision --image-path photo.jpg --prompt "请详细描述这张图片的内容"

  # 询问图片中的问题
  vision --image-path screenshot.png --prompt "这张截图里有什么？"

  # 识别图片中的文字
  vision --image-path document.jpg --prompt "请识别并输出图片中的所有文字"

  # 不流式输出（一次性返回结果）
  vision --image-path photo.jpg --prompt "这是什么？" --no-stream

  # 查看帮助
  vision --help

需要安装:
  pip install requests

API密钥配置:
  将豆包API密钥写入 ~/.cert/doubao.key 文件
        """
    )
    
    parser.add_argument("--image-path", "-i", required=True,
        help="图片文件路径（支持绝对路径或相对路径）")
    
    parser.add_argument("--prompt", "-p", required=True,
        help="提示词，告诉AI要看什么、理解什么")
    
    parser.add_argument("--no-stream", action="store_true",
        help="禁用流式输出（默认启用流式输出）")
    
    args = parser.parse_args()
    
    # 展开用户目录
    image_path = os.path.expanduser(args.image_path)
    prompt = args.prompt
    stream = not args.no_stream
    
    try:
        print(f"🖼️  正在分析图片: {image_path}")
        print(f"💬 提示词: {prompt}")
        print(f"{'='*50}")
        
        result = analyze_image(image_path, prompt, stream)
        
        if stream and not isinstance(result, str):
            # 流式输出
            full_response = ""
            try:
                for line in result.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            
                            if "content" in delta and delta["content"]:
                                content = delta["content"]
                                print(content, end="", flush=True)
                                full_response += content
                                
                        except json.JSONDecodeError:
                            continue
                
                print()  # 最后换行
                
                # 统计信息
                print(f"\n{'='*50}")
                print(f"✅ 分析完成，共 {len(full_response)} 字符")
                
            except Exception as e:
                print(f"\n❌ 流式输出错误: {str(e)}")
                sys.exit(1)
        else:
            # 非流式输出
            if result.startswith("❌"):
                print(result)
                sys.exit(1)
            else:
                print(result)
                print(f"\n{'='*50}")
                print(f"✅ 分析完成，共 {len(result)} 字符")
        
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"❌ 执行错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()