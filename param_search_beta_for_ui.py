"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import copy
import itertools
import json
import re
import time
import warnings
from typing import List

import pandas as pd

from config import raw_data_path
from core.backtest import step6_simulate_performance
from core.model.backtest_config import MultiEquityBacktestConfig
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_file_path
from core.version import version_prompt

# ====================================================================================================
# ** 脚本运行前配置 **
# 主要是解决各种各样奇怪的问题们
# ====================================================================================================
warnings.filterwarnings('ignore')  # 过滤一下warnings，不要吓到老实人

# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)


def dict_itertools(dict_):
    filter_dict = {k: v for k, v in dict_.items() if isinstance(v, list) and len(v) > 0}
    keys = list(filter_dict.keys())
    values = list(filter_dict.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def __parse_path_expression(path_expr):
    """解析路径表达式，如 'factor_list[0][2][0]'

    Args:
        path_expr: 路径表达式字符串

    Returns:
        tuple: (base_key, indices)
        - base_key: 基础键名，如 'factor_list'
        - indices: 索引列表，如 [0, 2, 0]
    """
    # 使用正则表达式匹配基础键名和所有索引
    match = re.match(r"^([^[]+)((?:\[\d+\])+)$", path_expr)
    if not match:
        return path_expr, []

    base_key = match.group(1)
    indices_str = match.group(2)

    # 提取所有数字索引
    indices = [int(idx) for idx in re.findall(r"\[(\d+)\]", indices_str)]

    return base_key, indices


def __set_nested_value(obj, base_key, indices, value):
    """根据路径设置嵌套数据结构中的值

    Args:
        obj: 目标对象（字典）
        base_key: 基础键名
        indices: 索引列表
        value: 要设置的值
    """
    if base_key not in obj['params']:
        return

    current = obj['params'][base_key]

    # 导航到最后一层的父级
    for idx in indices[:-1]:
        if isinstance(current, list) and 0 <= idx < len(current):
            current = current[idx]
        else:
            return

    # 设置最后一层的值
    final_idx = indices[-1]
    if 0 <= final_idx < len(current):
        if isinstance(current, (list, tuple)):
            current = list(current)
            current[final_idx] = value
            current = tuple(current)
        else:
            current[final_idx] = value

    obj['params'][base_key][indices[:-1][0]] = current


def convert_range_params(data):
    """转换range格式的参数为列表

    Args:
        data: 配置数据（通常是字典）

    Returns:
        转换后的数据
    """
    if isinstance(data, dict):
        # 检查是否是range格式 {"start": x, "end": y, "step": z}
        if all(key in data for key in ["start", "end", "step"]):
            start = data["start"]
            end = data["end"]
            step = data["step"]
            return list(range(start, end, step))
        else:
            # 递归处理字典中的每个值
            return {k: convert_range_params(v) for k, v in data.items()}
    elif isinstance(data, list):
        # 递归处理列表中的每个元素
        return [convert_range_params(item) for item in data]
    else:
        # 其他类型直接返回
        return data


def convert_lists_to_tuples(data, target_fields=None):
    """将指定字段中的列表里的列表转换为元组

    Args:
        data: 字典数据
        target_fields: 需要处理的字段集合，默认为None时处理所有字段

    Returns:
        处理后的数据
    """
    if not isinstance(data, dict):
        return data

    # 默认的元组字段
    if target_fields is None:
        target_fields = {
            "factor_list",
            "long_factor_list",
            "short_factor_list",
            "filter_list",
            "long_filter_list",
            "short_filter_list",
            "filter_list_post",
            "long_filter_list_post",
            "short_filter_list_post",
        }

    # 深拷贝以避免修改原数据
    result = copy.deepcopy(data)

    for field in target_fields:
        if field in result and isinstance(result[field], list):
            result[field] = [
                tuple(item) if isinstance(item, list) else item
                for item in result[field]
            ]

    return result


def find_best_params(strategies: List[dict], strategy_info):
    # ====================================================================================================
    # ** 1. 初始化 **
    # 根据 config.py 中的配置，初始化回测
    # ====================================================================================================
    # 需要携带所有回测组的因子列表
    default_strategy = copy.deepcopy(strategy_info.get('strategy_config'))
    factor_list = set()
    for strategy in strategies:
        factor_list = factor_list | set(strategy.get('params', {}).get('factor_list', []))
        default_strategy['hold_period'] = strategy['hold_period']
        default_strategy['name'] = strategy['name']
    default_strategy['params']['factor_list'] = factor_list

    # 用聚合的数据进行me conf初始化
    me_conf = MultiEquityBacktestConfig(
        name=backtest_name,
        strategy_config=default_strategy,
        strategies=strategy_info['strategy_pool'],
        leverage=strategy_info['leverage'],
    )

    # ====================================================================================================
    # ** 2. 子策略回测 **
    # 运行子策略回测，计算每一个子策略的资金曲线
    # 💡小技巧：如果你仓位管理的子策略不变化，调试的时候可以注释这个步骤，可以加快调试的速度
    # ====================================================================================================
    me_conf.backtest_strategies()

    # ====================================================================================================
    # ** 3. 整理子策略的资金曲线 **
    # 获取所有子策略的资金曲线信息，并且针对仓位管理策略做周期转换，并计算因子
    # ====================================================================================================
    me_conf.process_equities()

    # ====================================================================================================
    # ** 4. 初始化遍历的配置列表 **
    # ====================================================================================================
    divider('初始化遍历', sep='-')
    logger.warning(f'子策略结果仍在 {me_conf.factory.result_folder} 中(节约存储)')
    me_conf_list: List[MultiEquityBacktestConfig] = []
    for index, strategy in enumerate(strategies):
        new_me_conf = MultiEquityBacktestConfig.duplicate_conf(me_conf, f'{backtest_name}_参数{index + 1}', strategy)
        me_conf_list.append(new_me_conf)

    # ====================================================================================================
    # ** 5. 逐个进行仓位管理回测 **
    # ====================================================================================================
    divider('逐个回测', sep='-')
    pivot_dict_spot = pd.read_pickle(raw_data_path / 'market_pivot_spot.pkl')
    pivot_dict_swap = pd.read_pickle(raw_data_path / 'market_pivot_swap.pkl')

    report_list = []
    for i, me_conf_i in enumerate(me_conf_list):
        # ====================================================================================================
        # ** 5-1. 计算仓位比例 **
        # 仓位管理策略接入，计算每一个时间周期中，子策略应该持仓的资金比例
        # ====================================================================================================
        seq = f'({i + 1} / {len(me_conf_list)})'
        logger.debug(f'🗄️ {seq} {me_conf_i}')
        s_time = time.time()
        pos_ratio = me_conf_i.calc_ratios()

        # ====================================================================================================
        # ** 5-2. 聚合选币结果 **
        # 根据子策略的资金比例，重新聚合成一个选币结果，及对应周期内币种的资金分配
        # ====================================================================================================
        df_spot_ratio, df_swap_ratio = me_conf_i.agg_pos_ratio(pos_ratio)
        logger.ok(f'完成仓位管理模块的计算，已花费时间{time.time() - s_time:.3f}秒')

        # ====================================================================================================
        # ** 5-3. 模拟交易 **
        # 根据生成好的选币结果+资金配比，重新模拟交易，得到回测报告
        # ====================================================================================================
        conf_all = me_conf_i.factory.generate_all_factor_config()
        conf_all.name = me_conf_i.factory.backtest_name
        # 用于参数遍历场景
        conf_all.is_param_search = True

        # 让我们荡起双桨🎵～
        report = step6_simulate_performance(
            conf_all,
            df_spot_ratio, df_swap_ratio, pivot_dict_spot, pivot_dict_swap,
            if_show_plot=False,  # 是否显示图表
            description=str(me_conf),  # 图表描述替换为仓位管理策略
        )

        report_list.append(report)

        logger.ok(f'{seq} {me_conf_i}')

    # ====================================================================================================
    # 6. 展示最优参数
    # - 根据回测结果筛选最优参数组合，并保存到 Excel 文件
    # ====================================================================================================
    divider('展示最优参数', sep='-')
    s_time = time.time()
    if len(report_list) > 65535:
        logger.warning(f'回测参数列表超过 65535，会占用大量内存，请手动合并结果')
        return None

    all_params_map = pd.concat(report_list, ignore_index=True)
    report_cols = all_params_map.columns
    all_me_conf_name_list = [me_conf_i.factory.backtest_name for me_conf_i in me_conf_list]
    all_me_conf_str_list = [str(me_conf_i) for me_conf_i in me_conf_list]
    all_params_map['仓位管理策略'] = all_me_conf_str_list
    all_params_map = all_params_map.assign(
        策略名=all_me_conf_name_list,
        仓位管理参数=all_me_conf_str_list
    )

    # 合并参数细节
    # 按累积净值排序并保存结果
    all_params_map.sort_values(by='累积净值', ascending=False, inplace=True)
    all_params_map = all_params_map[['策略名', '仓位管理策略', *report_cols]].drop(columns=['param'])
    all_params_map.to_excel(get_file_path('data', backtest_name, '最优参数.xlsx'), index=False)
    print(all_params_map)
    logger.ok(f'完成展示最优参数，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - s_time):.3f}秒')


if __name__ == '__main__':
    version_prompt()
    print()
    divider('[仓位管理框架遍历脚本_beta]', with_timestamp=False)
    logger.debug(f'# 本脚本为 BETA 版本，目前存在一个已知问题：')
    logger.debug(f'# 本脚本聚合遍历的参数进行计算，在处理轮动因子时，会清理空值数据，这里会按照遍历的最大参数进行处理')
    logger.debug(f'# 因此，本脚本遍历之后的结果会出现与直接跑回测存在部分误差')
    divider('[仓位管理框架遍历脚本_beta]', with_timestamp=False)

    # ====================================================================================================
    # backtest_name和strategy_pool都默认使用config.py中的同名变量
    # ====================================================================================================
    with open(get_file_path("config.json"), "r", encoding="utf-8") as f:
        batch = json.load(f)
    # 转换range格式的参数为列表
    batch = convert_range_params(batch)

    backtest_name = batch.get("search_name", "遍历")

    strategy_config_list = []
    for param_dict in dict_itertools(batch):
        strategy_config = copy.deepcopy(batch.get('strategy_info').get('strategy_config'))
        # 更新可遍历的参数
        for param_key, param_value in param_dict.items():
            if not param_value:  # 跳过空值
                continue

            # 检查是否是路径表达式
            base_key, indices = __parse_path_expression(param_key)

            if indices:  # 如果是路径表达式（包含索引）
                __set_nested_value(strategy_config, base_key, indices, param_value)
                logger.info(f"更新路径表达式 {param_key}: {param_value}")
            else:  # 传统的直接键值对
                # 处理传统的参数更新逻辑
                strategy_config[param_key] = param_value

        # 统一转换 params 中的列表元素为元组
        strategy_config['params'] = convert_lists_to_tuples(strategy_config['params'])

        strategy_config_list.append(strategy_config)

    find_best_params(strategy_config_list, copy.deepcopy(batch.get('strategy_info')))
