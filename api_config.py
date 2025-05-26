# 在 config.py 文件的末尾添加

# ... (您现有的axis, Topic, Eval_Config, PERSONL_SEEDS_LIST 定义) ...

# 新增API配置部分
# 您可以从环境变量加载敏感信息，例如 os.environ.get("MY_API_KEY")
# 为了示例清晰，这里直接写出，但生产环境强烈建议使用环境变量或安全配置管理

# 代理配置 (如果全局使用或特定API需要)
# HTTP_PROXY = "http://your_proxy_user:%your_proxy_pass@your_proxy_server:port"
# HTTPS_PROXY = "http://your_proxy_user:%your_proxy_pass@your_proxy_server:port"

API_PROVIDERS_CONFIG = {
    "default_openrouter_gemini_flash": {
        "provider_type": "openrouter",
        "api_key": "sk-or-YOUR-OPENROUTER-KEY", # 替换为您的OpenRouter密钥或从环境变量读取
        "base_url": "https://openrouter.ai/api/v1",
        "model_id": "google/gemini-flash-1.5", # 之前是 gemini-2.5-flash-preview-05-20，请确认模型名
        "temperature": 0.7, # 可以为每个配置指定默认参数
        "max_tokens": 1024,
        "top_p": 0.8,
    },
    "my_custom_deepseek_v3": {
        "provider_type": "custom_requests",
        "url": "https://74.235.187.172:9443/callcenter/callBack/audio",
        "model_id": "deepseek-v3", # 您的代码中是 deepseek-v3
        "username": "llm-deepseek-hw-data-science",
        "password": "T43RtmIfNcw6ZhzPuQSK7EJ9dGWyVAawx3pf",
        "temperature": 0.75,
        "max_tokens": 4096,
        "top_p": 0.7,
        # 如果需要为此特定API设置代理，可以在这里指定
        # "http_proxy": HTTP_PROXY,
        # "https_proxy": HTTPS_PROXY,
    },
    "my_custom_deepseek_r1": {
            "provider_type": "custom_requests",
            "url": "https://74.235.187.172:9443/callcenter/callBack/audio",
            "model_id": "deepseek-r1", # 您的代码中是 deepseek-v3
            "username": "llm-deepseek-hw-data-science",
            "password": "T43RtmIfNcw6ZhzPuQSK7EJ9dGWyVAawx3pf",
            "temperature": 0.75,
            "max_tokens": 1024,
            "top_p": 0.7,
            # 如果需要为此特定API设置代理，可以在这里指定
            # "http_proxy": HTTP_PROXY,
            # "https_proxy": HTTPS_PROXY,
        },
    "another_openrouter_model_example": {
        "provider_type": "openrouter",
        "api_key": "sk-or-YOUR-OPENROUTER-KEY",
        "model_id": "anthropic/claude-3-haiku", # 示例
        "temperature": 0.6,
    }
    # 在这里添加更多API配置
}

# 默认的Agent API分配
# 您可以在运行时覆盖这些默认值
DEFAULT_AGENT_APIS = {
    "planner_agent": API_PROVIDERS_CONFIG["my_custom_deepseek_v3"],
    "user_agent": API_PROVIDERS_CONFIG[ "my_custom_deepseek_v3"],
    "responder_agent": API_PROVIDERS_CONFIG["my_custom_deepseek_r1"], # 例如，让Responder使用您的自定义API
}

# 确保 PERSONL_SEEDS_LIST 的加载函数仍然存在且有效
# def load_json(json_file): ...
# def get_person_seed_list(): ...
# PERSONL_SEEDS_LIST = get_person_seed_list() # 已存在