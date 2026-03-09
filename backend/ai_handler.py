import httpx
import json
import os

def _log(msg):
    try:
        from logger import app_logger
        app_logger.info("AI 请求", msg)
    except Exception:
        pass

class AIHandler:
    def __init__(self, api_key, base_url, model, system_prompt, proxy=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.system_prompt = system_prompt
        self.proxy = proxy  # 代理 dict 或 None，用于 AI API 请求

    def _proxy_url(self):
        """构造代理 URL，若应跳过（ignore_local）或未配置则返回 None"""
        if not self.proxy:
            return None
        from proxy_utils import build_proxy_url, should_skip_proxy
        if should_skip_proxy(self.proxy, self.base_url):
            return None
        return build_proxy_url(self.proxy) or None

    async def test_connection(self):
        """测试 AI 端点连接性"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1
        }
        proxy_url = self._proxy_url()
        try:
            async with httpx.AsyncClient(timeout=10.0, proxy=proxy_url) as client:
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
        
        proxy_url = self._proxy_url()
        _log(f"base_url={self.base_url!r}, proxy={'是' if proxy_url else '否'}")
        try:
            async with httpx.AsyncClient(timeout=60.0, proxy=proxy_url) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=data) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        try:
                            err_obj = json.loads(body.decode())
                            err_msg = err_obj.get("error", {}).get("message") or err_obj.get("errors", {}).get("message") or str(err_obj.get("error", body.decode()[:200]))
                        except Exception:
                            err_msg = body.decode(errors="replace")[:300]
                        hint = ""
                        if response.status_code == 401:
                            hint = " 建议到 模型设置 中编辑该端点，重新填写有效的 API Key/Token。"
                        yield f"\n[AI Error: API 返回 {response.status_code}: {err_msg}]{hint}\n"
                        return
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            if line.strip() == "data: [DONE]":
                                break
                            try:
                                json_data = json.loads(line[6:])
                                choices = json_data.get('choices')
                                if choices is None:
                                    choices = []
                                item = choices[0] if len(choices) > 0 else {}
                                delta = item.get('delta') or {}
                                content = delta.get('content', '') or ''
                                if content:
                                    yield content
                            except Exception as e:
                                _log(f"JSON 解析失败: {e}, 行: {line[:100]}")
        except Exception as e:
            error_msg = str(e)
            if "All connection attempts failed" in error_msg or "connection" in error_msg.lower():
                error_msg += " (请检查 AI 端点、网络或代理：若未勾选「AI 对话」绑定，AI 应直连)"
            yield f"\n[AI Error: {error_msg}]\n"
