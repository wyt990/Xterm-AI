import httpx
import json
import os

class AIHandler:
    def __init__(self, api_key, base_url, model, system_prompt):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.system_prompt = system_prompt

    async def test_connection(self):
        """测试 AI 端点连接性"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        # 发送一个极其简短的请求来验证
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.base_url}/chat/completions"
                response = await client.post(url, headers=headers, json=data)
                if response.status_code == 200:
                    return True, "连接成功"
                else:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get('error', {}).get('message', response.text)
                    except:
                        err_msg = response.text
                    return False, f"API 错误: {response.status_code} - {err_msg}"
        except Exception as e:
            error_msg = str(e)
            if "All connection attempts failed" in error_msg:
                error_msg += " (请检查 Base URL 是否正确，或者服务器是否能正常访问该网络地址)"
            return False, f"连接异常: {error_msg}"

    async def get_response_stream(self, messages):
        # 始终包含系统提示词
        full_messages = [{"role": "system", "content": self.system_prompt}] + messages
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model,
            "messages": full_messages,
            "stream": True
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=data) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            if line.strip() == "data: [DONE]":
                                break
                            try:
                                json_data = json.loads(line[6:])
                                content = json_data['choices'][0]['delta'].get('content', '')
                                if content:
                                    yield content
                            except Exception as e:
                                print(f"JSON Parse Error: {e}, Line: {line}")
        except Exception as e:
            error_msg = str(e)
            if "All connection attempts failed" in error_msg:
                error_msg += " (连接 AI 端点失败，请检查网络或代理设置)"
            yield f"\n[AI Error: {error_msg}]\n"
