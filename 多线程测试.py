# multithread_stability_test.py

import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# 从您的项目中导入必要的模块和配置
# 确保 PYTHONPATH 正确设置，或者这些文件在同一目录
from 数据生成 import generate_single_conversation_sample  #
from config import ( #
    Instruction_Retention_Topic, Inference_Memory_Topic,
    Reliable_Versioned_Editing_Topic, Self_Coherence_Topic,
    Instruction_Retention_Eval_Config, Inference_Memory_Eval_Config,
    Reliable_Versioned_Editing_Eval_Config, Self_Coherence_Eval_Config,
    PERSONL_SEEDS_LIST,
)
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')

# 测试用的输出目录
STABILITY_TEST_OUTPUT_DIRECTORY = "stability_test_output"
NUM_TOPICS_PER_AXIS = 5  # 每个axis随机抽取5个topic
MAX_CONCURRENT_WORKERS = 5  # 并发线程数，根据您的API限制和机器性能调整


def get_all_subtopics_for_axis(axis_name, topic_map):
    """辅助函数：获取一个axis下的所有子主题描述及其所属大类"""
    all_subtopics = []
    for category, subtopic_descriptions in topic_map.items():
        for subtopic_desc in subtopic_descriptions:
            all_subtopics.append({
                "category": category,
                "description": subtopic_desc,
                "full_detail": {category: subtopic_desc}  # generate_single_conversation_sample期望的格式
            })
    return all_subtopics


def run_stability_test():
    """
    运行多线程稳定性测试。
    """
    start_time = time.time()

    if not os.path.exists(STABILITY_TEST_OUTPUT_DIRECTORY):
        os.makedirs(STABILITY_TEST_OUTPUT_DIRECTORY)
        logging.info(f"创建测试输出目录: {STABILITY_TEST_OUTPUT_DIRECTORY}")

    if not PERSONL_SEEDS_LIST:
        logging.error("PERSONL_SEEDS_LIST 为空。请确保 persona.jsonl 文件存在且已正确加载。")  #
        return

    # 将axis名称映射到其Topic字典和Eval配置
    challenge_axis_configs = {
        ALL_CHALLENGE_AXES[0]: (Instruction_Retention_Topic, Instruction_Retention_Eval_Config),  #
        ALL_CHALLENGE_AXES[1]: (Inference_Memory_Topic, Inference_Memory_Eval_Config),  #
        ALL_CHALLENGE_AXES[2]: (Reliable_Versioned_Editing_Topic, Reliable_Versioned_Editing_Eval_Config),  #
        ALL_CHALLENGE_AXES[3]: (Self_Coherence_Topic, Self_Coherence_Eval_Config),  #
    }

    tasks_to_submit = []
    persona_iterator = 0

    for axis_name in ALL_CHALLENGE_AXES:  #
        if axis_name not in challenge_axis_configs:
            logging.warning(f"Axis '{axis_name}' 在 challenge_axis_configs 中没有对应的配置，将跳过。")
            continue

        topic_map, eval_config = challenge_axis_configs[axis_name]
        all_subtopics_for_this_axis = get_all_subtopics_for_axis(axis_name, topic_map)

        if not all_subtopics_for_this_axis:
            logging.warning(f"Axis '{axis_name}' 没有找到任何子主题，将跳过。")
            continue

        # 随机抽取N个子主题
        num_to_sample = min(NUM_TOPICS_PER_AXIS, len(all_subtopics_for_this_axis))
        sampled_subtopics = random.sample(all_subtopics_for_this_axis, num_to_sample)

        logging.info(f"为 Axis: '{axis_name}' 随机选择了 {len(sampled_subtopics)} 个子主题进行测试。")

        for subtopic_info in sampled_subtopics:
            current_persona = PERSONL_SEEDS_LIST[persona_iterator % len(PERSONL_SEEDS_LIST)]  #
            persona_iterator += 1

            # 您可以在这里为不同的任务或Agent类型指定不同的API配置
            # 为了稳定性测试，可以都用同一个你想测试的API，或混合使用
            planner_api_cfg = DEFAULT_AGENT_APIS["planner_agent"]  #
            user_agent_api_cfg = DEFAULT_AGENT_APIS["user_agent"]  #
            responder_api_cfg = DEFAULT_AGENT_APIS["responder_agent"]  #

            # 示例：如果想特别测试您的自定义API
            # try:
            #     custom_api_config = API_PROVIDERS_CONFIG["my_custom_deepseek_v3"]
            #     planner_api_cfg = custom_api_config
            #     user_agent_api_cfg = custom_api_config
            #     responder_api_cfg = custom_api_config
            # except KeyError:
            #    logging.error("API配置 'my_custom_deepseek_v3' 未在 config.py 中找到。将使用默认配置。")

            tasks_to_submit.append({
                "challenge_topic_details": subtopic_info["full_detail"],
                "evaluation_config": eval_config,
                "challenge_axis_name": axis_name,
                "persona_seed": current_persona,
                "output_dir": STABILITY_TEST_OUTPUT_DIRECTORY,
                "planner_api_config": planner_api_cfg,
                "user_agent_api_config": user_agent_api_cfg,
                "responder_api_config": responder_api_cfg
            })

    if not tasks_to_submit:
        logging.info("没有生成任何测试任务。请检查您的主题配置。")
        return

    random.shuffle(tasks_to_submit)  # 打乱任务顺序
    logging.info(f"总共准备提交 {len(tasks_to_submit)} 个测试任务。")

    completed_count = 0
    failed_count = 0  # 指的是在线程执行中抛出未捕获异常的任务

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        # 提交任务
        future_to_task_info = {
            executor.submit(generate_single_conversation_sample, **task_args): task_args
            for task_args in tasks_to_submit
        }

        for future in as_completed(future_to_task_info):
            task_info = future_to_task_info[future]
            try:
                future.result()  # 获取任务结果，如果任务中发生异常，这里会重新抛出
                logging.info(
                    f"任务完成: Axis='{task_info['challenge_axis_name']}', Topic='{list(task_info['challenge_topic_details'].values())[0][:50]}...'")
                completed_count += 1
            except Exception as e:
                logging.error(
                    f"任务执行失败: Axis='{task_info['challenge_axis_name']}', Topic='{list(task_info['challenge_topic_details'].values())[0][:50]}...'. 错误: {e}",
                    exc_info=False)  # exc_info=True 会打印完整堆栈
                failed_count += 1

            processed_count = completed_count + failed_count
            logging.info(
                f"进度: {processed_count}/{len(tasks_to_submit)} (成功完成代码执行: {completed_count}, 执行中抛出异常: {failed_count})")

    end_time = time.time()
    logging.info("----------------------------------------------------")
    logging.info("稳定性测试执行完毕。")
    logging.info(f"总耗时: {end_time - start_time:.2f} 秒")
    logging.info(f"总任务数: {len(tasks_to_submit)}")
    logging.info(f"代码执行成功完成的任务数: {completed_count} (请检查输出目录中的具体文件分类)")
    logging.info(f"执行中抛出未捕获异常的任务数: {failed_count}")
    logging.info(f"生成的测试数据保存在目录: {STABILITY_TEST_OUTPUT_DIRECTORY}")
    logging.info("----------------------------------------------------")


if __name__ == '__main__':
    # 在运行前，请确保:
    # 1. `api_clients.py` 和 `数据生成.py` 中的函数已按之前的讨论更新。
    # 2. `config.py` 包含所有必要的API配置和主题定义。
    # 3. `user.py` 和 `planer.py` 中的prompts也已更新。
    # 4. `persona.jsonl` 文件存在且包含有效的personas。

    run_stability_test()