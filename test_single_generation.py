# test_single_generation.py

import logging
import os
import random

# 从您的项目中导入必要的模块和配置
# 确保 PYTHONPATH 正确设置，或者这些文件在同一目录
from 数据生成 import generate_single_conversation_sample #
from config import (
    Inference_Memory_Topic,
    Inference_Memory_Eval_Config,
    PERSONL_SEEDS_LIST,
    axis as ALL_CHALLENGE_AXES
)
from ours.from_yuxian_chinese_openrouter_new_prompts_with_multimodel.api_config  import (
    API_PROVIDERS_CONFIG, # 导入API配置
    DEFAULT_AGENT_APIS    # 导入默认的Agent API分配
)
# 基本日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 测试用的输出目录
TEST_OUTPUT_DIRECTORY = "generated_test_data"

def run_single_test():
    """
    运行一次数据生成流程以进行测试。
    """
    if not os.path.exists(TEST_OUTPUT_DIRECTORY):
        os.makedirs(TEST_OUTPUT_DIRECTORY)
        logging.info(f"创建测试输出目录: {TEST_OUTPUT_DIRECTORY}")

    # 1. 选择一个测试用的挑战配置
    # 我们这里选择 "Inference Memory" 的第一个子主题作为示例
    # 您可以根据需要更改为其他轴或主题
    try:
        challenge_axis_name = ALL_CHALLENGE_AXES[1] # "Inference Memory"
        topic_category = list(Inference_Memory_Topic.keys())[0] # 例如 "Personal Preference"
        subtopic_desc = Inference_Memory_Topic[topic_category][0] # 该类别下的第一个描述
        challenge_topic_details = {topic_category: subtopic_desc}
        evaluation_config = Inference_Memory_Eval_Config #
    except IndexError:
        logging.error("无法从config.py中获取足够的测试主题/轴信息。请检查Inference_Memory_Topic和ALL_CHALLENGE_AXES是否已正确填充。")
        return

    # 2. 选择一个 Persona
    if not PERSONL_SEEDS_LIST:
        logging.error("PERSONL_SEEDS_LIST 为空。请确保 persona.jsonl 文件存在且已正确加载。") #
        return
    persona_seed = random.choice(PERSONL_SEEDS_LIST) #

    # 3. 定义要用于每个Agent的API配置
    # 您可以从 API_PROVIDERS_CONFIG 中选择或直接定义
    # 例如，让所有agent都使用您的自定义API进行测试
    try:
        planner_api_cfg = API_PROVIDERS_CONFIG["my_custom_deepseek_v3"] #
        user_agent_api_cfg = API_PROVIDERS_CONFIG["my_custom_deepseek_v3"] #
        # 为了全面测试，也可以让Responder使用不同的API
        # responder_api_cfg = API_PROVIDERS_CONFIG["default_openrouter_gemini_flash"]
        responder_api_cfg = API_PROVIDERS_CONFIG["my_custom_deepseek_v3"] #

    except KeyError as e:
        logging.error(f"API配置 '{e}' 未在 config.py 的 API_PROVIDERS_CONFIG 中找到。请检查您的配置。")
        return

    logging.info("--- 开始单条数据生成测试 ---")
    logging.info(f"测试轴: {challenge_axis_name}")
    logging.info(f"测试主题: {challenge_topic_details}")
    logging.info(f"Planner API: {planner_api_cfg.get('provider_type')}/{planner_api_cfg.get('model_id')}")
    logging.info(f"User Agent API: {user_agent_api_cfg.get('provider_type')}/{user_agent_api_cfg.get('model_id')}")
    logging.info(f"Responder API: {responder_api_cfg.get('provider_type')}/{responder_api_cfg.get('model_id')}")

    try:
        generate_single_conversation_sample(
            challenge_topic_details=challenge_topic_details,
            evaluation_config=evaluation_config,
            challenge_axis_name=challenge_axis_name,
            persona_seed=persona_seed,
            output_dir=TEST_OUTPUT_DIRECTORY,
            planner_api_config=planner_api_cfg,
            user_agent_api_config=user_agent_api_cfg,
            responder_api_config=responder_api_cfg
        )
        logging.info("--- 单条数据生成测试完成 ---")
        logging.info(f"请检查目录 '{TEST_OUTPUT_DIRECTORY}' 中的输出文件。")
    except Exception as e:
        logging.error(f"单条数据生成测试过程中发生错误: {e}", exc_info=True)

if __name__ == '__main__':
    # 确保您的config.py中 API_PROVIDERS_CONFIG 和 PERSONL_SEEDS_LIST 已正确配置和加载
    # 特别是 "my_custom_deepseek_v3" 和 "default_openrouter_gemini_flash" (如果用到)
    # 以及相关的API密钥和凭证。

    # 在运行前，请再次确认：
    # 1. `api_clients.py` 文件已创建并包含 `get_llm_response` 和其他API调用函数。
    # 2. `config.py` 已更新，包含 `API_PROVIDERS_CONFIG` 和 `DEFAULT_AGENT_APIS`。
    #    并且您已填入正确的API密钥和自定义API的URL/凭证。
    # 3. `数据生成.py` 中的 `generate_single_conversation_sample` 函数已更新为接受新的API配置参数，
    #    并使用 `get_llm_response` 进行调用。
    # 4. 所有辅助函数（如 `format_planner_prompt` 等）在 `数据生成.py` 中可用。

    run_single_test()