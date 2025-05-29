# 数据生成.py

import copy
import json
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
import os
import re
import time  # 新增导入

# 修改导入，使用新的api_clients模块
from api_clients import get_llm_response, check_for_refusal, \
    AgentRefusalError  # 假设 AgentRefusalError 定义在 api_clients.py

from ours.from_yuxian_chinese_openrouter_new_prompts_with_multimodel.config import (
    axis as ALL_CHALLENGE_AXES,
    Instruction_Retention_Topic, Inference_Memory_Topic,
    Reliable_Versioned_Editing_Topic, Self_Coherence_Topic,
    Instruction_Retention_Eval_Config, Inference_Memory_Eval_Config,
    Reliable_Versioned_Editing_Eval_Config, Self_Coherence_Eval_Config,
    PERSONL_SEEDS_LIST,
)
from ours.from_yuxian_chinese_openrouter_new_prompts_with_multimodel.api_config import (
    API_PROVIDERS_CONFIG,  # 导入API配置
    DEFAULT_AGENT_APIS  # 导入默认的Agent API分配
)
from planer import prompt as PLANNER_BASE_PROMPT
from user import prompt as USER_BASE_PROMPT

# Setup logging for better traceability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')

MAX_CONVERSATION_HISTORY_TURNS_FOR_PLANNER = 20
OUTPUT_DIRECTORY = "generated_data_all_topics"
# 可以放在 config.py 或 数据生成.py 顶部
RESPONDER_SYSTEM_PROMPT = """You are a helpful assistant. 
"""


# --- 辅助函数定义 ---
def format_planner_prompt(base_prompt_template, challenge_axis, topic_details,
                          evaluation_config, max_dialogue_turns, persona_seed):
    """Formats the planner agent's prompt."""
    prompt = base_prompt_template.replace("{axis}", challenge_axis)
    prompt = prompt.replace("{topic}", json.dumps(topic_details, indent=4, ensure_ascii=False))
    prompt = prompt.replace("{num_turns}", str(max_dialogue_turns))
    prompt = prompt.replace("{Definition}", evaluation_config["category_definition"])
    prompt = prompt.replace("{Pass Criteria}", evaluation_config["pass_criteria"])
    prompt = prompt.replace("{Fail Criteria}", evaluation_config["failure_criteria"])
    prompt = prompt.replace("{Example}", evaluation_config["example"])
    prompt = prompt.replace("{Persona_seed}", persona_seed)
    return prompt


def format_user_agent_prompt(base_prompt_template, challenge_axis, current_blueprint,
                             topic_details, max_dialogue_turns):
    """Formats the user agent's prompt."""
    prompt = base_prompt_template.replace("{axis}", challenge_axis)
    prompt = prompt.replace("{topic}", json.dumps(topic_details, indent=4, ensure_ascii=False))
    prompt = prompt.replace("{num_turns}", str(max_dialogue_turns))
    prompt = prompt.replace("{blueprint}", current_blueprint)
    return prompt


import re
import logging


# 之前的 normalize_text_for_parsing 函数可以保留，以防万一LLM在标签内部或内容中仍然产生一些奇怪字符，
# 但对于标签本身的查找，现在依赖性降低了。
# def normalize_text_for_parsing(text): ... (可以保留我们之前讨论的较全面的版本)

def extract_content_between_tags(text, tag_name):
    """Helper function to extract content between <TAG_NAME> and </TAG_NAME>."""
    try:
        # Regex to find <TAG_NAME>(possibly with attributes)Content</TAG_NAME>
        # Making it case-insensitive for tag names for robustness
        # Ensuring it captures multi-line content properly with re.DOTALL
        # (?i) for case-insensitivity of tags
        # Corrected regex to handle potential attributes in start tag if any, though not expected here
        pattern = rf"<{tag_name}>([\s\S]*?)</{tag_name}>"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception as e:
        logging.error(f"Error extracting content for tag '{tag_name}': {e}")
    return None


def parse_user_agent_response(response_text):
    original_for_log = response_text
    # Optional: normalize if you still suspect issues within the content of tags
    # normalized_text = normalize_text_for_parsing(response_text)
    # For this method, we'll parse the raw response_text as LLM should adhere to XML-like tags.

    user_message_str = None
    stop_flag = False
    stop_type_str = "UNDEFINED"

    if not response_text or not response_text.strip():
        logging.warning(f"Received empty or whitespace-only response_text for parsing.")
        return "Error: Received empty response text", False, "UNDEFINED"

    # Check for overall agent response wrapper (optional but good for validation)
    if not (response_text.strip().startswith("<AGENT_RESPONSE>") and response_text.strip().endswith(
            "</AGENT_RESPONSE>")):
        logging.warning(
            f"Response does not seem to be wrapped in <AGENT_RESPONSE> tags. Full response: {original_for_log}")
        # Depending on strictness, you might return error here or try to parse anyway.
        # For now, we'll try to parse individual tags even if the wrapper is malformed or missing.

    user_message_content = extract_content_between_tags(response_text, "USER_MESSAGE")
    stop_flag_content = extract_content_between_tags(response_text, "STOP_FLAG")
    stop_type_content = extract_content_between_tags(response_text, "STOP_TYPE")

    if user_message_content is not None:
        user_message_str = user_message_content  # Already stripped by helper
        # No need to strip quotes here, as the User Agent should not add extra quotes around its message
        # if the prompt is clear about putting the message directly inside the tags.
    else:
        logging.warning(f"Could not extract <USER_MESSAGE> content. Full response: {original_for_log}")
        user_message_str = f"Error: Could not parse <USER_MESSAGE> from response: {original_for_log}"

    if stop_flag_content is not None:
        if stop_flag_content.lower() == 'true':
            stop_flag = True
        elif stop_flag_content.lower() == 'false':
            stop_flag = False
        else:
            logging.warning(
                f"Invalid content for <STOP_FLAG>: '{stop_flag_content}'. Defaulting to False. Full response: {original_for_log}")
            stop_flag = False  # Default to False if invalid value
    else:
        logging.warning(
            f"Could not extract <STOP_FLAG> content. Defaulting to False. Full response: {original_for_log}")
        stop_flag = False  # Default if tag is missing

    if stop_type_content is not None:
        stop_type_str = stop_type_content.upper()
        if stop_type_str not in ["BROKEN", "UNDEFINED"]:  # 移除了 "PASSED"
            logging.warning(f"Unexpected value for <STOP_TYPE>: '{stop_type_str}'. Defaulting based on stop_flag.")
            stop_type_str = "BROKEN" if stop_flag else "UNDEFINED"
    else:  # STOP_TYPE tag content not found
        if stop_flag:
            logging.warning(
                f"Could not extract <STOP_TYPE> content while STOP_FLAG was True. Defaulting to BROKEN. Full response: {original_for_log}")
            stop_type_str = "BROKEN"
        else:
            stop_type_str = "UNDEFINED"  # Default if tag is missing and not stopping

    # Final consistency checks
    if not stop_flag and stop_type_str != "UNDEFINED":
        stop_type_str = "UNDEFINED"
    elif stop_flag and stop_type_str != "BROKEN":  # 如果是停止，但类型不是BROKEN (比如是UNDEFINED或意外的PASSED)
        logging.warning(f"STOP_FLAG is True, but STOP_TYPE is '{stop_type_str}' (not BROKEN). Forcing to BROKEN.")
        stop_type_str = "BROKEN"

    if user_message_str is not None and user_message_str.strip() == "**" and not user_message_str.startswith("Error:"):
        logging.warning(f"Parsed user message is only '**', which is unusual. Original: {original_for_log}")

    return user_message_str, stop_flag, stop_type_str

def build_conversation_history_text(direct_dialogue_turns):
    history_text = "\nHere is the conversation history up to the last assistant response:\n"
    start_index = max(0, len(direct_dialogue_turns) - MAX_CONVERSATION_HISTORY_TURNS_FOR_PLANNER * 2)
    for i in range(start_index, len(direct_dialogue_turns)):
        turn = direct_dialogue_turns[i]
        history_text += f"{turn['role'].capitalize()}: {turn['content']}\n"
    return history_text.strip()


def build_planner_update_messages(initial_planner_prompt_content, previous_planner_blueprint,
                                  current_direct_dialogue_turns):
    messages = [{"role": "user", "content": initial_planner_prompt_content}]
    if previous_planner_blueprint:
        messages.append({"role": "assistant", "content": previous_planner_blueprint})
    messages.append({"role": "user", "content": build_conversation_history_text(current_direct_dialogue_turns)})
    return messages


def build_user_agent_update_messages(initial_user_agent_prompt_content, previous_user_agent_full_response,
                                     updated_planner_blueprint):
    messages = [{"role": "user", "content": initial_user_agent_prompt_content}]
    if previous_user_agent_full_response:
        messages.append({"role": "assistant", "content": previous_user_agent_full_response})
    blueprint_update_text = f"\nHere is the new or updated blueprint from the Planner:\n{updated_planner_blueprint}"
    messages.append({"role": "user", "content": blueprint_update_text})
    return messages


def generate_single_conversation_sample(
        challenge_topic_details,
        evaluation_config,
        challenge_axis_name,
        persona_seed,
        output_dir,
        # 修改点：新增 initial_planner_api_config 参数
        # initial_planner_api_config= DEFAULT_AGENT_APIS["initial_planer_agent"],  # 用于第一轮的Planner API配置
        initial_planner_api_config= None,  # 用于第一轮的Planner API配置
        planner_api_config=DEFAULT_AGENT_APIS["planner_agent"],  # 用于后续更新的Planner API配置
        user_agent_api_config=DEFAULT_AGENT_APIS["user_agent"],
        responder_api_config=DEFAULT_AGENT_APIS["responder_agent"]
):
    sample_log_id = f"{challenge_axis_name.replace(' ', '_')}_{list(challenge_topic_details.keys())[0].replace(' ', '_')}_{random.randint(1000, 9999)}"

    # 修改点：更新日志记录以反映可能不同的Planner模型
    initial_planner_to_log = initial_planner_api_config if initial_planner_api_config else planner_api_config
    log_message = (
        f"[{sample_log_id}] Starting generation. "
        f"Initial Planner: {initial_planner_to_log.get('provider_type')}/{initial_planner_to_log.get('model_id')}"
    )
    if initial_planner_api_config and initial_planner_api_config != planner_api_config:
        log_message += (
            f", Update Planner: {planner_api_config.get('provider_type')}/{planner_api_config.get('model_id')}"
        )
    else:
        log_message += (  # 如果没有提供特定的 initial_planner_api_config，或者它与 planner_api_config 相同
            f", Planner (all turns): {planner_api_config.get('provider_type')}/{planner_api_config.get('model_id')}"
        )
    log_message += (
        f", User: {user_agent_api_config.get('provider_type')}/{user_agent_api_config.get('model_id')}, "
        f"Responder: {responder_api_config.get('provider_type')}/{responder_api_config.get('model_id')}"
    )
    logging.info(log_message)

    full_generation_log = []
    # 从 planner_api_config 获取 max_dialogue_turns (或者从 initial_planner_api_config 如果它定义了不同的值且你希望以此为准)
    # 这里我们假设 max_dialogue_turns 由常规的 planner_api_config 控制，或者你可以选择从 initial_planner_api_config 获取
    # 如果 initial_planner_api_config 专门用于第一轮，它可能不包含 max_dialogue_turns
    # 通常 max_dialogue_turns 是对话的整体目标长度，由负责迭代的planner_api_config控制更合理
    max_dialogue_turns = planner_api_config.get("max_dialogue_turns", random.randint(3, 6))  # 比如你的代码是3-6

    current_planner_blueprint = None
    fine_grained_max_turns = max_dialogue_turns * 2  # 根据我们之前的讨论，这里是细粒度轮次

    initial_planner_prompt_content = format_planner_prompt(
        PLANNER_BASE_PROMPT, challenge_axis_name, challenge_topic_details,
        evaluation_config, fine_grained_max_turns, persona_seed
    )
    initial_user_agent_prompt_content = format_user_agent_prompt(  # 假设这个函数也已更新为XML格式
        USER_BASE_PROMPT, challenge_axis_name, "No blueprint available yet.",
        challenge_topic_details, max_dialogue_turns  # User Agent 可能仍按粗粒度轮次理解其目标
    )
    direct_dialogue_turns = []
    user_agent_last_full_response = None

    # 修改点：更新 initial_setup 日志中的 planner_config 部分
    planner_configs_log = {
        "initial_planner": {
            "provider": initial_planner_to_log.get('provider_type'),
            "model": initial_planner_to_log.get('model_id')
        }
    }
    if initial_planner_api_config and initial_planner_api_config != planner_api_config:
        planner_configs_log["update_planner"] = {
            "provider": planner_api_config.get('provider_type'),
            "model": planner_api_config.get('model_id')
        }
    else:  # 如果只有一个planner配置，简化日志
        planner_configs_log = {"provider": planner_api_config.get('provider_type'),
                               "model": planner_api_config.get('model_id')}

    full_generation_log.append({"type": "initial_setup", "sample_id": sample_log_id, "details": {
        "challenge_topic": challenge_topic_details,
        "max_dialogue_turns_target": max_dialogue_turns,  # 这是User-Agent交互轮次
        "fine_grained_max_turns_for_planner": fine_grained_max_turns,  # Planner感知的总细粒度轮次
        "persona": persona_seed,
        "challenge_axis": challenge_axis_name,
        "planner_configs": planner_configs_log,  # 使用新的日志结构
        "user_agent_config": {"provider": user_agent_api_config.get("provider_type"),
                              "model": user_agent_api_config.get("model_id")},
        "responder_config": {"provider": responder_api_config.get("provider_type"),
                             "model": responder_api_config.get("model_id")}
    }})

    axis_filename_prefix = challenge_axis_name.replace(' ', '_')
    output_file_successful_break = os.path.join(output_dir, f"{axis_filename_prefix}_broken.jsonl")
    output_file_passed_by_agent = os.path.join(output_dir, f"{axis_filename_prefix}_passed_by_agent.jsonl")
    output_file_max_turns_reached = os.path.join(output_dir, f"{axis_filename_prefix}_max_turns_reached.jsonl")
    output_file_errored = os.path.join(output_dir, f"{axis_filename_prefix}_errored.jsonl")

    try:
        for turn_number in range(max_dialogue_turns):  # 这个循环仍然是 User-Agent 交互轮次
            logging.info(f"[{sample_log_id}] User-Agent Interaction Turn {turn_number + 1}/{max_dialogue_turns}")

            # 1. Planner Agent
            planner_config_for_this_call = planner_api_config  # 默认使用更新模型
            if turn_number == 0:
                planner_messages = [{"role": "user", "content": initial_planner_prompt_content}]
                # 修改点：第一轮使用 initial_planner_api_config (如果提供了)
                if initial_planner_api_config:
                    planner_config_for_this_call = initial_planner_api_config
                    logging.info(
                        f"[{sample_log_id}] Planner: Using initial planner for first blueprint: {planner_config_for_this_call.get('provider_type')}/{planner_config_for_this_call.get('model_id')}")
                else:
                    logging.info(
                        f"[{sample_log_id}] Planner: Using default planner for first blueprint: {planner_config_for_this_call.get('provider_type')}/{planner_config_for_this_call.get('model_id')}")

            else:  # 后续轮次，使用常规的 planner_api_config (更新模型)
                # 确保 build_planner_update_messages 传递了正确的轮次信息给Planner
                # completed_fine_grained_turns = turn_number * 2 # 已完成的细粒度轮次对
                # planner_status_update = f"User Agent has completed {turn_number} actions (fine-grained turns up to {completed_fine_grained_turns}). You are now planning for User Agent's action at fine-grained Turn {completed_fine_grained_turns + 1}."
                # 这个状态更新的注入，需要修改 build_planner_update_messages 函数
                planner_messages = build_planner_update_messages(
                    initial_planner_prompt_content, current_planner_blueprint, direct_dialogue_turns
                    # 如果需要传递轮次信息给planner prompt，这里可能需要调整 build_planner_update_messages
                )
                logging.info(
                    f"[{sample_log_id}] Planner: Using update planner for subsequent blueprint: {planner_config_for_this_call.get('provider_type')}/{planner_config_for_this_call.get('model_id')}")

            full_generation_log.append(
                {"type": "planner_agent_prompt",
                 "turn": turn_number + 1,  # User-Agent 交互轮次
                 "planner_model_used": f"{planner_config_for_this_call.get('provider_type')}/{planner_config_for_this_call.get('model_id')}",
                 "content": planner_messages
                 })

            current_planner_blueprint_raw, planner_cot = get_llm_response(
                messages=planner_messages, api_config=planner_config_for_this_call  # 使用选择的配置
            )
            if check_for_refusal(current_planner_blueprint_raw):
                logging.warning(f"[{sample_log_id}] Planner Agent response detected as refusal. Aborting sample.")
                raise AgentRefusalError(
                    f"Planner agent refused: {current_planner_blueprint_raw[:200]}")
            current_planner_blueprint = current_planner_blueprint_raw

            if not current_planner_blueprint:
                raise ValueError("Planner agent returned empty blueprint.")
            full_generation_log.append(
                {"type": "planner_agent_result", "turn": turn_number + 1, "blueprint": current_planner_blueprint,
                 "thought_process": planner_cot})

            # 2. User Agent
            # User Agent 的调用逻辑保持不变，它接收 Planner 的蓝图
            # format_user_agent_prompt 和 parse_user_agent_response 应已更新为XML格式
            # user_agent_turn_for_prompt = turn_number + 1 # User Agent的行动轮次
            user_agent_messages = []
            if turn_number == 0:  # User Agent的第一次行动
                user_agent_messages = build_user_agent_update_messages(
                    initial_user_agent_prompt_content,  # 这里的 initial_user_agent_prompt_content 本身不含动态轮次
                    None,
                    current_planner_blueprint
                    # 如果要让User Agent知道当前是其第几次行动，可以在 build_user_agent_update_messages 中注入
                    # 例如，在传递蓝图时，附加 "This is for your Action Turn X."
                )
            else:
                user_agent_messages = build_user_agent_update_messages(
                    initial_user_agent_prompt_content,
                    user_agent_last_full_response,
                    current_planner_blueprint
                )

            full_generation_log.append(
                {"type": "user_agent_prompt", "turn": turn_number + 1, "content": user_agent_messages})

            user_agent_response_text_raw, user_agent_cot = get_llm_response(
                messages=user_agent_messages, api_config=user_agent_api_config
            )
            if check_for_refusal(user_agent_response_text_raw):
                logging.warning(f"[{sample_log_id}] User Agent response detected as refusal. Aborting sample.")
                raise AgentRefusalError(f"User agent refused: {user_agent_response_text_raw[:200]}")
            user_agent_response_text = user_agent_response_text_raw

            if not user_agent_response_text:
                raise ValueError("User agent returned empty response.")
            user_agent_last_full_response = user_agent_response_text

            # 假设 parse_user_agent_response 已更新为处理XML格式
            user_query, stop_requested_by_user_agent, stop_type_from_agent = parse_user_agent_response(
                user_agent_response_text
            )
            full_generation_log.append({
                "type": "user_agent_result", "turn": turn_number + 1, "full_response": user_agent_response_text,
                "parsed_query": user_query, "stop_flag_parsed": stop_requested_by_user_agent,
                "stop_type_parsed": stop_type_from_agent, "thought_process": user_agent_cot
            })

            if stop_requested_by_user_agent:
                logging.info(
                    f"[{sample_log_id}] User Agent signaled STOP. Type: {stop_type_from_agent}. Saving to appropriate file.")
                direct_dialogue_turns.append({
                    "role": "user_agent_stop_signal",  # 或者直接是 user_query if it's the stop message
                    "content": user_query if user_query and not user_query.startswith(
                        "Error:") else "User agent stopped with no message or parsing error.",
                    "stop_type": stop_type_from_agent
                })
                final_status_message = f"stopped_by_agent_as_{stop_type_from_agent.lower()}"
                full_generation_log.append({
                    "type": "final_state",
                    "status": final_status_message,
                    "dialogue": direct_dialogue_turns
                })
                # ... (文件保存逻辑) ...
                file_to_save = output_file_errored  # Default, will be overwritten
                if stop_type_from_agent == "BROKEN":
                    file_to_save = output_file_successful_break
                else:  # Should ideally not happen if User Agent only stops on BROKEN or its own turn limit (which is handled by main loop)
                    # However, if parse_user_agent_response defaults an unknown stop_type to BROKEN when stop_flag is True, this is okay.
                    logging.warning(
                        f"[{sample_log_id}] User Agent signaled STOP but type was not BROKEN ('{stop_type_from_agent}'). Defaulting to broken file.")
                    file_to_save = output_file_successful_break  # Or an error file
                    full_generation_log[-1][
                        "status"] = f"stopped_by_agent_as_{stop_type_from_agent}_or_error (classified as broken)"

                with open(file_to_save, "a", encoding="utf-8") as f:
                    f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")
                return "SAVED"

            if not user_query or user_query.startswith("Error: Could not parse user message"):
                logging.warning(
                    f"[{sample_log_id}] User agent produced an unparsable query: '{user_query}'. Original response: {user_agent_response_text}")
                raise ValueError(
                    f"User agent produced unparsable/empty query from (non-refusal) response: {user_agent_response_text}")

            direct_dialogue_turns.append({"role": "user", "content": user_query})

            # 3. Responder Agent (逻辑保持不变)
            # ... (responder_messages, get_llm_response, check_for_refusal, etc.) ...
            responder_messages = []
            if RESPONDER_SYSTEM_PROMPT:  # RESPONDER_SYSTEM_PROMPT 应该在文件顶部定义
                responder_messages.append({"role": "system", "content": RESPONDER_SYSTEM_PROMPT})
            responder_messages.extend(copy.deepcopy(direct_dialogue_turns))
            full_generation_log.append(
                {"type": "responder_agent_prompt", "turn": turn_number + 1, "content": responder_messages})

            assistant_response_raw, responder_cot = get_llm_response(
                messages=responder_messages, api_config=responder_api_config
            )
            if check_for_refusal(assistant_response_raw):
                logging.warning(f"[{sample_log_id}] Responder Agent response detected as refusal. Aborting sample.")
                raise AgentRefusalError(f"Responder agent refused: {assistant_response_raw[:200]}")
            assistant_response = assistant_response_raw

            if not assistant_response:
                logging.warning(f"[{sample_log_id}] Responder agent returned empty response.")
                assistant_response = "[Responder Agent returned empty or failed]"

            direct_dialogue_turns.append({"role": "assistant", "content": assistant_response})
            full_generation_log.append(
                {"type": "responder_agent_result", "turn": turn_number + 1, "response": assistant_response,
                 "thought_process": responder_cot})

        # Max turns reached (User-Agent interaction turns)
        logging.info(
            f"[{sample_log_id}] Max User-Agent interaction turns ({max_dialogue_turns}) reached. Saving to max_turns_reached file.")
        full_generation_log.append(
            {"type": "final_state", "status": "max_turns_reached", "dialogue": direct_dialogue_turns})
        with open(output_file_max_turns_reached, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")
        return "SAVED"

    except AgentRefusalError as ar_err:  # 已重命名变量
        logging.warning(  # 修改为 warning，因为这是一种预期的退出路径
            f"[{sample_log_id}] Sample generation aborted due to agent refusal: {ar_err}. This sample will not be saved in main categories.")
        # Optionally, save refusals to a separate file for analysis
        # with open(os.path.join(output_dir, f"{axis_filename_prefix}_refused.jsonl"), "a", encoding="utf-8") as f:
        #     full_generation_log.append({"type": "final_state", "status": "refused", "reason": str(ar_err), "dialogue_so_far": direct_dialogue_turns})
        #     f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")
        return "REFUSED"

    except Exception as e:
        logging.error(f"[{sample_log_id}] Error during generation (non-refusal): {e}", exc_info=True)
        full_generation_log.append({"type": "error", "message": str(e), "dialogue_so_far": direct_dialogue_turns})
        with open(output_file_errored, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")
        return "ERRORED_NON_REFUSAL"


# Helper function to create new random task arguments
def create_new_random_task_args(challenge_configs_map, persona_list, output_dir_path):
    if not challenge_configs_map or not persona_list:
        logging.error("Cannot create new random task: challenge_configs_map or persona_list is empty.")
        return None

    random_axis_name = random.choice(list(challenge_configs_map.keys()))
    topic_map, eval_config = challenge_configs_map[random_axis_name]

    if not topic_map:
        logging.warning(f"No topics found for randomly selected axis: {random_axis_name}")
        return None

    random_topic_category = random.choice(list(topic_map.keys()))
    subtopic_descriptions = topic_map[random_topic_category]

    if not subtopic_descriptions:
        logging.warning(f"No subtopic descriptions for category {random_topic_category} in axis {random_axis_name}")
        return None

    random_subtopic_desc = random.choice(subtopic_descriptions)
    current_topic_detail = {random_topic_category: random_subtopic_desc}
    current_persona = random.choice(persona_list)

    return {
        "challenge_topic_details": current_topic_detail,
        "evaluation_config": eval_config,
        "challenge_axis_name": random_axis_name,
        "persona_seed": current_persona,
        "output_dir": output_dir_path  # Use the global OUTPUT_DIRECTORY or pass as arg
    }


if __name__ == '__main__':
    if not os.path.exists(OUTPUT_DIRECTORY):
        os.makedirs(OUTPUT_DIRECTORY)
    logging.info(f"Ensuring output directory exists: {OUTPUT_DIRECTORY}")

    challenge_configs = {
        ALL_CHALLENGE_AXES[0]: (Instruction_Retention_Topic, Instruction_Retention_Eval_Config),
        ALL_CHALLENGE_AXES[1]: (Inference_Memory_Topic, Inference_Memory_Eval_Config),
        ALL_CHALLENGE_AXES[2]: (Reliable_Versioned_Editing_Topic, Reliable_Versioned_Editing_Eval_Config),
        ALL_CHALLENGE_AXES[3]: (Self_Coherence_Topic, Self_Coherence_Eval_Config),
    }

    initial_tasks_to_create_count = 0
    num_samples_per_subtopic = 2  # Target samples per subtopic
    for axis_name, (topic_map, eval_config) in challenge_configs.items():
        for topic_category, subtopic_descriptions in topic_map.items():
            for _ in subtopic_descriptions:  # Iterate for each subtopic
                initial_tasks_to_create_count += num_samples_per_subtopic

    logging.info(f"Targeting approximately {initial_tasks_to_create_count} successful samples initially.")

    if not PERSONL_SEEDS_LIST:
        logging.error("PERSONL_SEEDS_LIST is empty. Cannot generate data without personas.")
        exit()

    max_concurrent_workers = 30
    successfully_saved_samples = 0
    refusal_aborted_samples = 0
    other_error_samples = 0
    max_attempts_multiplier = 2  # Allow up to N times the target attempts to account for refusals/errors
    total_attempts_limit = initial_tasks_to_create_count * max_attempts_multiplier
    current_attempts = 0

    with ThreadPoolExecutor(max_workers=max_concurrent_workers) as executor:
        futures = []


        # Initial task seeding logic
        def get_initial_task_list(num_tasks_target, configs, personas, out_dir, samples_per_subtopic):
            initial_tasks = []
            p_iterator = 0
            for axis, (topics, ev_conf) in configs.items():
                for cat, sub_descs in topics.items():
                    for sub_desc in sub_descs:
                        for _ in range(samples_per_subtopic):
                            pers = personas[p_iterator % len(personas)]
                            p_iterator += 1
                            initial_tasks.append({
                                "challenge_topic_details": {cat: sub_desc},
                                "evaluation_config": ev_conf,
                                "challenge_axis_name": axis,
                                "persona_seed": pers,
                                "output_dir": out_dir
                            })
            random.shuffle(initial_tasks)
            return initial_tasks


        task_queue = get_initial_task_list(initial_tasks_to_create_count, challenge_configs, PERSONL_SEEDS_LIST,
                                           OUTPUT_DIRECTORY, num_samples_per_subtopic)
        logging.info(f"Prepared an initial queue of {len(task_queue)} task configurations.")

        # Seed initial futures
        for _ in range(min(len(task_queue), max_concurrent_workers)):
            if not task_queue: break
            task_args = task_queue.pop(0)
            futures.append(executor.submit(generate_single_conversation_sample, **task_args))
            current_attempts += 1

        while successfully_saved_samples < initial_tasks_to_create_count and current_attempts < total_attempts_limit:
            if not futures:  # All submitted tasks are done, but target not met or attempts not exhausted
                if task_queue:  # Still pre-defined tasks left
                    logging.info("Futures list empty, adding more tasks from queue.")
                    task_args = task_queue.pop(0)
                    futures.append(executor.submit(generate_single_conversation_sample, **task_args))
                    current_attempts += 1
                else:  # Pre-defined tasks exhausted, generate new random ones
                    logging.info("Futures list and task queue empty, generating a new random task.")
                    new_task_args = create_new_random_task_args(challenge_configs, PERSONL_SEEDS_LIST, OUTPUT_DIRECTORY)
                    if new_task_args:
                        futures.append(executor.submit(generate_single_conversation_sample, **new_task_args))
                        current_attempts += 1
                    else:
                        logging.warning(
                            "Could not create new random task, and target not met. Ending generation early.")
                        break
                if not futures:  # If still no futures after trying to add, break
                    break

            try:
                # Use a timeout to prevent as_completed from blocking indefinitely if a future hangs
                # and to allow periodically checking the main loop conditions.
                for future in as_completed(futures, timeout=180.0):  # timeout in seconds
                    futures.remove(future)  # Remove completed future

                    try:
                        result_status = future.result()  # This can raise exceptions from the thread

                        if result_status == "SAVED":
                            successfully_saved_samples += 1
                            logging.info(
                                f"Successfully saved a sample. Total saved: {successfully_saved_samples}/{initial_tasks_to_create_count}")
                        elif result_status == "REFUSED":
                            refusal_aborted_samples += 1
                            logging.warning(
                                f"Sample generation aborted due to refusal. Total refusals: {refusal_aborted_samples}")
                        elif result_status == "ERRORED_NON_REFUSAL":
                            other_error_samples += 1
                            logging.error(
                                f"Sample generation failed with a non-refusal error. Total other errors: {other_error_samples}")
                        else:  # Should not happen if generate_single_conversation_sample always returns one of these
                            logging.error(
                                f"Unknown result status from generate_single_conversation_sample: {result_status}")
                            other_error_samples += 1

                    except AgentRefusalError as ar_err:  # Should be handled by result_status now
                        refusal_aborted_samples += 1
                        logging.warning(
                            f"Task aborted due to AgentRefusalError (caught in main): {ar_err}. Total refusals: {refusal_aborted_samples}")
                    except Exception as e:  # Other exceptions from the task
                        other_error_samples += 1
                        logging.error(f"Task failed with an unexpected exception (caught in main): {e}", exc_info=False)

                    # Replenish futures if needed and if conditions allow
                    if successfully_saved_samples < initial_tasks_to_create_count and current_attempts < total_attempts_limit:
                        if task_queue:
                            next_task_args = task_queue.pop(0)
                            logging.info("Submitting next task from queue.")
                            futures.append(executor.submit(generate_single_conversation_sample, **next_task_args))
                            current_attempts += 1
                        else:
                            logging.info("Task queue exhausted, submitting a new random task for replacement.")
                            new_task_args = create_new_random_task_args(challenge_configs, PERSONL_SEEDS_LIST,
                                                                        OUTPUT_DIRECTORY)
                            if new_task_args:
                                futures.append(executor.submit(generate_single_conversation_sample, **new_task_args))
                                current_attempts += 1
                            # else:
                            # logging.info("Could not create new random task for replacement.")
                            # No new task if create_new_random_task_args returns None

            except FuturesTimeoutError:
                logging.warning(f"Timeout waiting for futures to complete. Active futures: {len(futures)}. Will retry.")
                # Continue loop, as_completed will pick up where it left off with remaining futures.
                # Or, you might want to check for hung tasks here.

            logging.info(
                f"Progress: Saved Samples: {successfully_saved_samples}/{initial_tasks_to_create_count} | Refusals: {refusal_aborted_samples} | Other Errors: {other_error_samples} | Attempts: {current_attempts}/{total_attempts_limit}")
            if not futures and (
                    successfully_saved_samples >= initial_tasks_to_create_count or current_attempts >= total_attempts_limit):
                break  # Exit outer while if no more futures and conditions met

    logging.info("----------------------------------------------------")
    logging.info("All data generation tasks process attempt finished.")
    logging.info(f"Targeted successful samples: {initial_tasks_to_create_count}")
    logging.info(f"Actually saved samples (broken/passed/max_turns): {successfully_saved_samples}")
    logging.info(f"Attempts aborted due to agent refusal: {refusal_aborted_samples}")
    logging.info(f"Attempts that resulted in other errors: {other_error_samples}")
    logging.info(f"Total generation attempts made: {current_attempts}")
    logging.info(f"Generated data saved in directory: {OUTPUT_DIRECTORY}")
    logging.info("Please check the .jsonl files in the output directory.")
    logging.info("----------------------------------------------------")
