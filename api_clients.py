# api_clients.py

import httpx
import requests
from requests.auth import HTTPBasicAuth
import json
import os
import time
import logging
from openai import OpenAI, AuthenticationError  # 用于OpenRouter
from sqlalchemy import false

# urllib3警告只在您的自定义API部分需要，如果其他API不需要可以考虑条件导入或在函数内处理
try:
    from urllib3.exceptions import InsecureRequestWarning

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    pass

# 设置日志
logger = logging.getLogger(__name__)


# 统一的返回格式: (response_text: str, cot_content: str | None)

def call_openrouter_api(messages, api_config, max_retries=3):
    """
    通过OpenRouter调用OpenAI兼容的API。
    api_config 应包含:
        - api_key: 你的OpenRouter API密钥
        - base_url: OpenRouter的API基础URL (例如 "https://openrouter.ai/api/v1")
        - model_id: 要使用的模型ID (例如 "google/gemini-2.5-flash-preview-05-20")
        - temperature: (可选, 默认0.95)
        - max_tokens: (可选, 默认1024)
        - top_p: (可选, 默认0.7)
    """
    api_key = api_config.get("api_key", os.environ.get("OPENROUTER_API_KEY"))
    if not api_key:
        logger.error("OpenRouter API key not provided in config or environment variable OPENROUTER_API_KEY.")
        raise ValueError("OpenRouter API key missing.")

    client = OpenAI(
        api_key=api_key,
        base_url=api_config.get("base_url", "https://openrouter.ai/api/v1"),
        http_client=httpx.Client(verify=False)  # 允许配置SSL验证
    )

    model_id = api_config.get("model_id")
    if not model_id:
        logger.error("Model ID not provided in OpenRouter API config.")
        raise ValueError("OpenRouter Model ID missing.")

    retry = 0
    while retry < max_retries:
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=api_config.get("temperature", 0.95),
                max_tokens=api_config.get("max_tokens", 1024),
                top_p=api_config.get("top_p", 0.7),
            )
            if completion.choices and len(completion.choices) > 0:
                message_object = completion.choices[0].message
                res = message_object.content.strip() if message_object.content else ""
                # OpenRouter/OpenAI API通常不直接在标准响应中分离CoT，除非模型本身输出特定格式
                # 这里我们假设CoT不直接从OpenRouter API的标准响应中提取，与原r1.py行为一致
                cot = None
                if len(res) >= 3:
                    return res, cot
                else:
                    logger.warning(
                        f"OpenRouter response too short (length {len(res)}): '{res}'. Retrying ({retry + 1}/{max_retries})...")
            else:
                logger.warning(f"OpenRouter API response missing 'choices'. Retrying ({retry + 1}/{max_retries})...")
        except AuthenticationError as auth_err:
            logger.error(f"OpenRouter Authentication Error: {auth_err}")
            raise  # 认证错误通常不可重试
        except Exception as e:
            logger.error(f"Error calling OpenRouter API (attempt {retry + 1}/{max_retries}): {e}")

        retry += 1
        if retry < max_retries:
            time.sleep(1 + retry)  # 增加等待时间

    logger.error(f"Failed to get valid response from OpenRouter after {max_retries} retries. Last message: {messages}")
    raise Exception("Exceeded max retries for OpenRouter API.")


def call_custom_requests_api(messages, api_config, max_retries=3):
    """
    调用您的自定义requests API。
    api_config 应包含:
        - url: API的URL
        - model_id: 要使用的模型名称 (例如 "deepseek-v3")
        - username: (可选) API认证用户名
        - password: (可选) API认证密码
        - temperature: (可选, 默认0.95)
        - max_tokens: (可选, 默认4096)
        - top_p: (可选, 默认0.7)
        - http_proxy: (可选) HTTP代理
        - https_proxy: (可选) HTTPS代理
    """
    url = api_config.get("url")
    model_id = api_config.get("model_id")
    if not url or not model_id:
        logger.error("URL or Model ID not provided in custom API config.")
        raise ValueError("Custom API URL or Model ID missing.")

    username = api_config.get("username")
    password = api_config.get("password")
    auth = HTTPBasicAuth(username, password) if username and password else None

    proxies = {}
    if "http_proxy" in api_config:
        proxies["http"] = api_config["http_proxy"]
    if "https_proxy" in api_config:
        proxies["https"] = api_config["https_proxy"]

    # 如果您想通过环境变量设置代理，可以在这里处理
    # os.environ["http_proxy"] = api_config.get("http_proxy", "")
    # os.environ["https_proxy"] = api_config.get("https_proxy", "")

    retry = 0
    last_response_data = None  # 用于记录最后一次的响应数据，以防出错时打印

    while retry < max_retries:
        try:
            payload = {
                "model": model_id,
                "messages": messages,
                "temperature": api_config.get("temperature", 0.95),
                "max_tokens": api_config.get("max_tokens", 4096),
                "top_p": api_config.get("top_p", 0.7),
            }
            response = requests.post(url, auth=auth, json=payload, verify=False, proxies=proxies if proxies else None)
            response.raise_for_status()  # 如果HTTP状态码是4xx或5xx，则抛出异常

            text = response.content.decode('utf-8')
            response_data = json.loads(text)
            last_response_data = response_data  # 更新最后一次的响应数据

            cot = response_data.get('choices', [{}])[0].get('message', {}).get('reasoning_content')
            res = response_data.get('choices', [{}])[0].get('message', {}).get('content', "").strip()

            if len(res) >= 3:  # 或者您需要的其他验证逻辑
                return res, cot
            else:
                logger.warning(
                    f"Custom API response too short (length {len(res)}): '{res}'. Retrying ({retry + 1}/{max_retries})...")

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred with custom API: {http_err} (attempt {retry + 1}/{max_retries})")
            logger.error(f"Response content: {response.text if response else 'No response'}")
        except Exception as e:
            logger.error(f"Error calling custom API (attempt {retry + 1}/{max_retries}): {e}")
            if last_response_data:
                logger.error(f"Last successful response data (or partial data before error): {last_response_data}")

        retry += 1
        if retry < max_retries:
            time.sleep(1 + retry)

    logger.error(
        f"Failed to get valid response from custom API after {max_retries} retries. Last message: {messages}, Last Response Data: {last_response_data}")
    raise Exception("Exceeded max retries for custom API.")


# API调用分发器
API_DISPATCHER = {
    "openrouter": call_openrouter_api,
    "custom_requests": call_custom_requests_api,
    # 在这里可以添加更多API类型和对应的处理函数
}


def get_llm_response(messages, api_config, max_retries=3):
    """
    根据api_config中的provider_type调用相应的LLM API。
    api_config 必须包含:
        - provider_type: str, 例如 "openrouter", "custom_requests"
        - ... (其他特定于提供商的配置)
    """
    provider_type = api_config.get("provider_type")
    if not provider_type:
        raise ValueError("API configuration must include 'provider_type'.")

    if provider_type not in API_DISPATCHER:
        raise ValueError(f"Unsupported provider_type: {provider_type}. Available: {list(API_DISPATCHER.keys())}")

    api_call_function = API_DISPATCHER[provider_type]

    logger.debug(f"Dispatching to {provider_type} with model {api_config.get('model_id')}")
    return api_call_function(messages, api_config, max_retries)


# 在 api_clients.py 文件顶部或合适位置导入
import difflib
import logging  # logging 应该已经存在

# ... (文件中的其他代码) ...

# 预定义的拒答模板
REFUSAL_TEMPLATES_ZH = [
    "作为一个人工智能语言模型，我还没有学习如何回答这个问题，您可以向我提问一些其它问题，我会尽力帮您结局的。",
    # 您可以根据需要添加更多中文拒答模板的变体
]
REFUSAL_TEMPLATES_EN = [
    "I'm sorry! As an AI language model, I don't know how to answer this question yet. You can ask me any questions about other topics, and I will try to deliver high quality and reliable information.",
    "I'm sorry, but as an AI language model, I cannot directly answer that.",
    "I am unable to provide an answer to this question.",
    # 您可以根据需要添加更多英文拒答模板的变体
]

# 合并所有模板
ALL_REFUSAL_TEMPLATES = REFUSAL_TEMPLATES_ZH + REFUSAL_TEMPLATES_EN

# 相似度阈值，当相似度高于此阈值时，判定为拒答
# 这个值可能需要根据实际测试进行调整，0.7 到 0.8 之间通常是一个不错的起点
REFUSAL_SIMILARITY_THRESHOLD = 0.75


def is_similar_enough(text1, text2, threshold):
    """使用 difflib 计算两个文本的相似度，并判断是否超过阈值。"""
    if not text1 or not text2:  # 处理空字符串的情况
        return False
    # 移除一些常见的非核心词汇和标点，使比较更关注核心内容
    # 这是一个简化的处理，更复杂的可以考虑词干提取、停用词移除等
    normalized_text1 = ''.join(filter(str.isalnum, text1.lower()))
    normalized_text2 = ''.join(filter(str.isalnum, text2.lower()))
    if not normalized_text1 or not normalized_text2:  # 归一化后可能为空
        return False

    seq_matcher = difflib.SequenceMatcher(None, normalized_text1, normalized_text2)
    return seq_matcher.ratio() >= threshold


def check_for_refusal(response_text):
    """
    检测给定的响应文本是否属于拒答。
    """
    if not response_text or not response_text.strip():
        # 空响应或仅包含空白的响应，不直接视为拒答，但可能需要上层逻辑判断
        # 如果空响应也视为一种形式的“无法回答”，可以在这里返回 True
        return False

    # 移除文本两端的空白，并转换为小写以进行不区分大小写的比较
    normalized_response = response_text.strip().lower()

    for template in ALL_REFUSAL_TEMPLATES:
        normalized_template = template.strip().lower()
        # 直接包含检测 (更严格，但也可能漏掉细微变体)
        # if normalized_template in normalized_response:
        #     logging.warning(f"Refusal detected by direct inclusion: Response '{response_text}' contains '{template}'")
        #     return True

        # 基于相似度的检测
        if is_similar_enough(normalized_response, normalized_template, REFUSAL_SIMILARITY_THRESHOLD):
            logging.warning(
                f"Refusal detected by similarity: Response '{response_text}' is similar to '{template}' (Similarity > {REFUSAL_SIMILARITY_THRESHOLD})")
            return True

    # 也可以加入一些关键词检测作为补充
    # refusal_keywords = ["cannot answer", "unable to provide", "don't know how to answer"]
    # for keyword in refusal_keywords:
    #     if keyword in normalized_response:
    #         logging.warning(f"Refusal detected by keyword: Response '{response_text}' contains '{keyword}'")
    #         return True

    return False


# 修改 get_llm_response 函数以集成拒答检测
# (假设 get_llm_response 在 api_clients.py 中)

# 原来的 get_llm_response 函数签名
# def get_llm_response(messages, api_config, max_retries=3):
#     ...
#     api_call_function = API_DISPATCHER[provider_type]
#     logger.debug(f"Dispatching to {provider_type} with model {api_config.get('model_id')}")
#     # 原本直接返回: return api_call_function(messages, api_config, max_retries)
#     response_text, cot_content = api_call_function(messages, api_config, max_retries) # 假设返回 (text, cot)

#     if check_for_refusal(response_text):
#         # 可以抛出一个特定的异常，或者返回一个特殊值
#         raise AgentRefusalError(f"Agent response was detected as a refusal: {response_text}")
#         # 或者 return "AGENT_REFUSAL_DETECTED", cot_content

#     return response_text, cot_content

# 为了使修改更清晰，我们可以将拒答检测逻辑放在调用 get_llm_response 之后
# 而不是修改 get_llm_response 内部。这样调用方可以决定如何处理。
# 但如果希望 get_llm_response 直接抛出异常，上述修改也是可以的。
# 这里我们先提供一个独立的检测函数，然后在 `数据生成.py` 中调用它。

# 自定义异常（可选，但推荐，方便捕获）
class AgentRefusalError(Exception):
    """当Agent响应被检测为拒答时抛出此异常。"""
    pass
