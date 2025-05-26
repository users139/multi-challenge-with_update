
# 数据生成.py

import copy
import json
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# 修改导入，使用新的api_clients模块
from api_clients import get_llm_response

from ours.from_yuxian_chinese_openrouter_new_prompts_with_multimodel.config import (
    axis as ALL_CHALLENGE_AXES,
    Instruction_Retention_Topic, Inference_Memory_Topic,
    Reliable_Versioned_Editing_Topic, Self_Coherence_Topic,
    Instruction_Retention_Eval_Config, Inference_Memory_Eval_Config,
    Reliable_Versioned_Editing_Eval_Config, Self_Coherence_Eval_Config,
    PERSONL_SEEDS_LIST,
)
from ours.from_yuxian_chinese_openrouter_new_prompts_with_multimodel.api_config  import (
    API_PROVIDERS_CONFIG, # 导入API配置
    DEFAULT_AGENT_APIS    # 导入默认的Agent API分配
)
from planer import prompt as PLANNER_BASE_PROMPT
from user import prompt as USER_BASE_PROMPT

# Setup logging for better traceability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')

MAX_CONVERSATION_HISTORY_TURNS_FOR_PLANNER = 10
OUTPUT_DIRECTORY = "generated_data_all_topics"
# 可以放在 config.py 或 数据生成.py 顶部
RESPONDER_SYSTEM_PROMPT = """You are a helpful, knowledgeable, and creative assistant. Your goal is to provide comprehensive and relevant answers to the user's questions to the best of your ability, drawing upon your general knowledge. You should always attempt to answer the user's question directly and avoid refusing to answer if the question is reasonable and does not violate safety guidelines. If a question involves practical advice, try to offer general suggestions or frameworks if specific, real-time data is unavailable. Remember to maintain context from the ongoing conversation.
"""
# --- (此处粘贴之前重构代码中的辅助函数) ---
# format_planner_prompt
# format_user_agent_prompt
# parse_user_agent_response
# build_conversation_history_text
# build_planner_update_messages
# build_user_agent_update_messages
# generate_single_conversation_sample (确保此函数中的输出文件名参数被正确使用或修改为接受目录)

# (为了简洁，我将假设这些辅助函数已在此处定义，与您之前提供的重构版本一致)
# START - 辅助函数定义 (从之前的重构版本复制)
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


def parse_user_agent_response(response_text):
    """Parses the User Agent's structured response to extract message, stop flag, and stop type."""
    import re
    user_message = None
    stop_flag = False
    stop_type = "UNDEFINED"  # Default if not stopping or not specified

    # Regex to find User Message (尝试更灵活地匹配可选的Markdown)
    # 优先匹配没有额外Markdown的严格格式
    msg_match_strict = re.search(r'^User Message:\s*(.*?)$', response_text, re.MULTILINE | re.DOTALL)
    if msg_match_strict:
        # Extract text between "User Message:" and the next label ("STOP:")
        potential_message_block = response_text.split("User Message:")[1]
        user_message = potential_message_block.split("STOP:")[0].strip()
    else:  # 如果严格匹配失败，尝试更宽松的、可能包含Markdown的匹配
        # (您可以保留或调整之前的宽松正则表达式，或者根据模型输出进一步优化)
        # 例如:
        msg_match_flexible = re.search(r'(?:User Message:|### \*\*User Message\*\*:\s*)"?([^"\n]*?)"?\s*(\n|$)',
                                       response_text, re.DOTALL | re.IGNORECASE)
        if not msg_match_flexible:
            msg_match_flexible = re.search(
                r'(?:User Message:|### \*\*User Message\*\*:\s*)(.*?)(\n•\s*(?:STOP:|### \*\*STOP\*\*:)|$)',
                response_text, re.DOTALL | re.IGNORECASE)

        if msg_match_flexible:
            user_message = msg_match_flexible.group(1).strip()
        else:
            logging.warning(f"Could not extract User Message from: {response_text}")
            user_message = f"Error: Could not parse user message from original: {response_text}"

    # Regex to find STOP flag
    stop_match_strict = re.search(r'^STOP:\s*(True|False)$', response_text, re.MULTILINE | re.IGNORECASE)
    if stop_match_strict:
        stop_flag_str = stop_match_strict.group(1).strip().lower()
        stop_flag = stop_flag_str == 'true'
    else:
        stop_match_flexible = re.search(r'(?:STOP:|### \*\*STOP\*\*:\s*)\*?\*?\s*(True|False)', response_text,
                                        re.IGNORECASE)
        if stop_match_flexible:
            stop_flag_str = stop_match_flexible.group(1).strip().lower()
            stop_flag = stop_flag_str == 'true'
        else:
            logging.warning(f"Could not extract STOP flag from: {response_text}, defaulting to False.")
            # 即使STOP解析失败，也尝试解析STOP_TYPE，因为它可能独立存在或在STOP之前

    # Regex to find STOP_TYPE (只在stop_flag为True时才有意义，但可以先解析出来)
    stop_type_match_strict = re.search(r'^STOP_TYPE:\s*(BROKEN|PASSED|UNDEFINED)$', response_text,
                                       re.MULTILINE | re.IGNORECASE)
    if stop_type_match_strict:
        stop_type = stop_type_match_strict.group(1).strip().upper()
    else:
        stop_type_match_flexible = re.search(
            r'(?:STOP_TYPE:|### \*\*STOP_TYPE\*\*:\s*)\*?\*?\s*(BROKEN|PASSED|UNDEFINED)', response_text, re.IGNORECASE)
        if stop_type_match_flexible:
            stop_type = stop_type_match_flexible.group(1).strip().upper()
        else:
            if stop_flag:  # 如果确实是停止了，但没有解析到STOP_TYPE
                logging.warning(
                    f"Could not extract STOP_TYPE from: {response_text} while STOP was True. Defaulting STOP_TYPE to BROKEN for safety.")
                stop_type = "BROKEN"  # 或者 "UNDEFINED"，取决于您希望如何处理
            # else STOP_TYPE 保持 "UNDEFINED"

    # 确保如果 stop_flag 为 False，stop_type 为 UNDEFINED
    if not stop_flag:
        stop_type = "UNDEFINED"
    elif stop_flag and stop_type == "UNDEFINED":  # 如果STOP了但类型还是UNDEFINED，则默认为BROKEN
        logging.warning(f"STOP was True but STOP_TYPE was UNDEFINED. Defaulting to BROKEN.")
        stop_type = "BROKEN"

    return user_message, stop_flag, stop_type  # 返回三个值

def build_conversation_history_text(direct_dialogue_turns):
    """Builds a text representation of the conversation history for the planner."""
    history_text = "\nHere is the conversation history up to the last assistant response:\n"
    start_index = max(0, len(direct_dialogue_turns) - MAX_CONVERSATION_HISTORY_TURNS_FOR_PLANNER * 2)
    for i in range(start_index, len(direct_dialogue_turns)):
        turn = direct_dialogue_turns[i]
        history_text += f"{turn['role'].capitalize()}: {turn['content']}\n"
    return history_text.strip()

def build_planner_update_messages(initial_planner_prompt_content, previous_planner_blueprint,
                                  current_direct_dialogue_turns):
    """Constructs the message list for updating the planner's blueprint."""
    messages = [{"role": "user", "content": initial_planner_prompt_content}]
    if previous_planner_blueprint:
        messages.append({"role": "assistant", "content": previous_planner_blueprint})
    messages.append({"role": "user", "content": build_conversation_history_text(current_direct_dialogue_turns)})
    return messages

def build_user_agent_update_messages(initial_user_agent_prompt_content, previous_user_agent_full_response,
                                     updated_planner_blueprint):
    """Constructs the message list for the user agent to generate the next turn."""
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
        planner_api_config=DEFAULT_AGENT_APIS["planner_agent"],
        user_agent_api_config=DEFAULT_AGENT_APIS["user_agent"],
        responder_api_config=DEFAULT_AGENT_APIS["responder_agent"]
):
    """
    Generates a single multi-turn conversation sample.
    Outputs to files named based on axis and status within the output_dir.
    Accepts API configurations for each agent.
    Distinguishes between 'broken', 'passed_by_agent', and 'max_turns_reached'.
    """
    sample_log_id = f"{challenge_axis_name.replace(' ', '_')}_{list(challenge_topic_details.keys())[0].replace(' ', '_')}_{random.randint(1000, 9999)}"
    logging.info(
        f"[{sample_log_id}] Starting generation with "
        f"Planner: {planner_api_config.get('provider_type')}/{planner_api_config.get('model_id')}, "
        f"User: {user_agent_api_config.get('provider_type')}/{user_agent_api_config.get('model_id')}, "
        f"Responder: {responder_api_config.get('provider_type')}/{responder_api_config.get('model_id')}"
    )

    full_generation_log = []
    max_dialogue_turns = planner_api_config.get("max_dialogue_turns", random.randint(6, 11))  # 可通过API配置覆盖默认轮数

    current_planner_blueprint = None
    initial_planner_prompt_content = format_planner_prompt(
        PLANNER_BASE_PROMPT, challenge_axis_name, challenge_topic_details,
        evaluation_config, max_dialogue_turns, persona_seed
    )
    initial_user_agent_prompt_content = format_user_agent_prompt(
        USER_BASE_PROMPT, challenge_axis_name, "No blueprint available yet.",
        challenge_topic_details, max_dialogue_turns
    )
    direct_dialogue_turns = []
    user_agent_last_full_response = None

    full_generation_log.append({"type": "initial_setup", "sample_id": sample_log_id, "details": {
        "challenge_topic": challenge_topic_details,
        "max_dialogue_turns_target": max_dialogue_turns,
        "persona": persona_seed,
        "challenge_axis": challenge_axis_name,
        "planner_config": {"provider": planner_api_config.get("provider_type"),
                           "model": planner_api_config.get("model_id")},
        "user_agent_config": {"provider": user_agent_api_config.get("provider_type"),
                              "model": user_agent_api_config.get("model_id")},
        "responder_config": {"provider": responder_api_config.get("provider_type"),
                             "model": responder_api_config.get("model_id")}
    }})

    # 定义输出文件名
    axis_filename_prefix = challenge_axis_name.replace(' ', '_')
    output_file_successful_break = os.path.join(output_dir, f"{axis_filename_prefix}_broken.jsonl")
    output_file_passed_by_agent = os.path.join(output_dir, f"{axis_filename_prefix}_passed_by_agent.jsonl")
    output_file_max_turns_reached = os.path.join(output_dir,
                                                 f"{axis_filename_prefix}_max_turns_reached.jsonl")  # 更明确的文件名
    output_file_errored = os.path.join(output_dir, f"{axis_filename_prefix}_errored.jsonl")

    try:
        for turn_number in range(max_dialogue_turns):
            logging.info(f"[{sample_log_id}] Turn {turn_number + 1}/{max_dialogue_turns}")

            # 1. Planner Agent
            if turn_number == 0:
                planner_messages = [{"role": "user", "content": initial_planner_prompt_content}]
            else:
                planner_messages = build_planner_update_messages(
                    initial_planner_prompt_content,
                    current_planner_blueprint,
                    direct_dialogue_turns
                )
            full_generation_log.append(
                {"type": "planner_agent_prompt", "turn": turn_number + 1, "content": planner_messages})

            current_planner_blueprint, planner_cot = get_llm_response(
                messages=planner_messages,
                api_config=planner_api_config
            )

            if not current_planner_blueprint:
                raise ValueError("Planner agent returned empty blueprint.")
            full_generation_log.append(
                {"type": "planner_agent_result", "turn": turn_number + 1, "blueprint": current_planner_blueprint,
                 "thought_process": planner_cot})

            # 2. User Agent
            if turn_number == 0:
                user_agent_messages = build_user_agent_update_messages(initial_user_agent_prompt_content, None,
                                                                       current_planner_blueprint)
            else:
                user_agent_messages = build_user_agent_update_messages(initial_user_agent_prompt_content,
                                                                       user_agent_last_full_response,
                                                                       current_planner_blueprint)

            full_generation_log.append(
                {"type": "user_agent_prompt", "turn": turn_number + 1, "content": user_agent_messages})

            user_agent_response_text, user_agent_cot = get_llm_response(
                messages=user_agent_messages,
                api_config=user_agent_api_config
            )

            if not user_agent_response_text:
                raise ValueError("User agent returned empty response.")
            user_agent_last_full_response = user_agent_response_text

            user_query, stop_requested_by_user_agent, stop_type_from_agent = parse_user_agent_response(
                user_agent_response_text)

            full_generation_log.append({
                "type": "user_agent_result",
                "turn": turn_number + 1,
                "full_response": user_agent_response_text,
                "parsed_query": user_query,
                "stop_flag_parsed": stop_requested_by_user_agent,
                "stop_type_parsed": stop_type_from_agent,
                "thought_process": user_agent_cot
            })

            if stop_requested_by_user_agent:
                logging.info(
                    f"[{sample_log_id}] User Agent signaled STOP. Type: {stop_type_from_agent}. Saving to appropriate file.")
                direct_dialogue_turns.append({
                    "role": "user_agent_stop_signal",
                    "content": user_query if user_query else "User agent stopped with no message.",
                    "stop_type": stop_type_from_agent
                })
                final_status_message = f"stopped_by_agent_as_{stop_type_from_agent.lower()}"
                full_generation_log.append({
                    "type": "final_state",
                    "status": final_status_message,
                    "dialogue": direct_dialogue_turns
                })

                if stop_type_from_agent == "PASSED":
                    file_to_save = output_file_passed_by_agent
                elif stop_type_from_agent == "BROKEN":
                    file_to_save = output_file_successful_break
                else:
                    logging.warning(
                        f"[{sample_log_id}] Unknown or UNDEFINED stop_type '{stop_type_from_agent}' from agent with STOP:True. Defaulting to broken file.")
                    file_to_save = output_file_successful_break
                    # 更新状态以反映这种不确定性
                    full_generation_log[-1][
                        "status"] = f"stopped_by_agent_as_undefined_or_unknown (classified as broken)"

                with open(file_to_save, "a", encoding="utf-8") as f:
                    f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")
                return

            if not user_query or "Error: Could not parse user message" in user_query:
                logging.warning(
                    f"[{sample_log_id}] User agent produced an unparsable/empty query. Ending this sample as errored.")
                raise ValueError(f"User agent produced unparsable/empty query: {user_agent_response_text}")

            direct_dialogue_turns.append({"role": "user", "content": user_query})

            # 3. Responder Agent
            responder_messages = []  # 初始化为空列表
            if RESPONDER_SYSTEM_PROMPT:  # 检查系统提示词是否存在且非空
                responder_messages.append({"role": "system", "content": RESPONDER_SYSTEM_PROMPT})

            # 追加当前的直接对话历史
            responder_messages.extend(copy.deepcopy(direct_dialogue_turns))  # 使用 extend 而不是直接赋值

            full_generation_log.append(
                {"type": "responder_agent_prompt", "turn": turn_number + 1, "content": responder_messages})

            assistant_response, responder_cot = get_llm_response(
                messages=responder_messages,
                api_config=responder_api_config
            )

            if not assistant_response:
                logging.warning(f"[{sample_log_id}] Responder agent returned empty response.")
                assistant_response = "[Responder Agent returned empty or failed]"
            direct_dialogue_turns.append({"role": "assistant", "content": assistant_response})
            full_generation_log.append(
                {"type": "responder_agent_result", "turn": turn_number + 1, "response": assistant_response,
                 "thought_process": responder_cot})

            # 检查 Planner 是否在蓝图中指示停止
            # if "STOP THE CONVERSATION" in current_planner_blueprint.upper():
            #     logging.info(
            #         f"[{sample_log_id}] Planner's blueprint indicated STOP after assistant's response. User agent will process this in the next turn.")
                # User Agent会在下一轮根据这个蓝图来决定是否以及如何停止

        # 如果循环正常完成 (达到 max_turns)
        logging.info(f"[{sample_log_id}] Max turns reached. Saving to max_turns_reached file.")
        full_generation_log.append(
            {"type": "final_state", "status": "max_turns_reached", "dialogue": direct_dialogue_turns})
        with open(output_file_max_turns_reached, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")

    except Exception as e:
        logging.error(f"[{sample_log_id}] Error during generation: {e}", exc_info=True)
        full_generation_log.append({"type": "error", "message": str(e), "dialogue_so_far": direct_dialogue_turns})
        with open(output_file_errored, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_generation_log, ensure_ascii=False, indent=2) + "\n")

if __name__ == '__main__':
    # 创建输出目录
    if not os.path.exists(OUTPUT_DIRECTORY):
        os.makedirs(OUTPUT_DIRECTORY)
    logging.info(f"Ensuring output directory exists: {OUTPUT_DIRECTORY}")

    # 定义所有挑战的配置映射
    challenge_configs = {
        ALL_CHALLENGE_AXES[0]: (Instruction_Retention_Topic, Instruction_Retention_Eval_Config),
        ALL_CHALLENGE_AXES[1]: (Inference_Memory_Topic, Inference_Memory_Eval_Config),
        ALL_CHALLENGE_AXES[2]: (Reliable_Versioned_Editing_Topic, Reliable_Versioned_Editing_Eval_Config),
        ALL_CHALLENGE_AXES[3]: (Self_Coherence_Topic, Self_Coherence_Eval_Config),
    }

    tasks_to_submit = []
    # 为每个挑战轴的每个主题的每个子主题描述创建一个任务
    # 可以调整 num_samples_per_subtopic 来控制每个最细粒度主题生成的样本数
    num_samples_per_subtopic = 1 # 每个子主题生成1个样本用于演示，可以调大

    if not PERSONL_SEEDS_LIST:
        logging.error("PERSONL_SEEDS_LIST is empty. Cannot generate data without personas.")
        exit()

    persona_iterator = 0 # 用于循环使用 persona

    for axis_name, (topic_map, eval_config) in challenge_configs.items():
        logging.info(f"Preparing tasks for Axis: {axis_name}")
        for topic_category, subtopic_descriptions in topic_map.items():
            for subtopic_desc in subtopic_descriptions:
                current_topic_detail = {topic_category: subtopic_desc}
                for _ in range(num_samples_per_subtopic):
                    # 循环使用 Persona 种子
                    current_persona = PERSONL_SEEDS_LIST[persona_iterator % len(PERSONL_SEEDS_LIST)]
                    persona_iterator += 1

                    tasks_to_submit.append({
                        "challenge_topic_details": current_topic_detail,
                        "evaluation_config": eval_config,
                        "challenge_axis_name": axis_name,
                        "persona_seed": current_persona,
                        "output_dir": OUTPUT_DIRECTORY
                    })
    random.shuffle(tasks_to_submit) # 打乱任务顺序，避免集中请求相似主题

    logging.info(f"Total tasks to submit: {len(tasks_to_submit)}")

    # 调整线程池大小，请根据您的API限制和机器性能调整
    # 大量任务并发可能导致API限流
    max_concurrent_workers = 5 # 示例值
    completed_tasks = 0
    failed_tasks = 0

    with ThreadPoolExecutor(max_workers=max_concurrent_workers) as executor:
        futures = {executor.submit(
                        generate_single_conversation_sample,
                        **task_args
                   ): task_args for task_args in tasks_to_submit}

        for future in as_completed(futures):
            task_info = futures[future]
            try:
                future.result()  # Wait for task to complete, will raise exception if task did
                logging.info(f"Task completed for topic: {task_info['challenge_topic_details']} on axis: {task_info['challenge_axis_name']}")
                completed_tasks +=1
            except Exception as e:
                logging.error(f"Task failed for topic: {task_info['challenge_topic_details']} on axis: {task_info['challenge_axis_name']}. Error: {e}", exc_info=False) # exc_info=False to avoid huge traceback spam for many errors
                failed_tasks +=1
            logging.info(f"Progress: {completed_tasks+failed_tasks}/{len(tasks_to_submit)} tasks processed. (Completed: {completed_tasks}, Failed: {failed_tasks})")


    logging.info("----------------------------------------------------")
    logging.info("All data generation tasks process attempt finished.")
    logging.info(f"Total tasks submitted: {len(tasks_to_submit)}")
    logging.info(f"Successfully completed tasks (may include 'passed' or 'broken' saves): {completed_tasks}")
    logging.info(f"Tasks that raised an exception during processing: {failed_tasks}")
    logging.info(f"Generated data saved in directory: {OUTPUT_DIRECTORY}")
    logging.info("Please check the .jsonl files in the output directory.")
    logging.info("----------------------------------------------------")