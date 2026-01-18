# -*- coding: utf-8 -*-
"""
配置服务 - 处理config.py文件的解析和数据处理

回测网页版 | 邢不行 | 2025分享会
author: 邢不行
微信: xbx6660
"""


import importlib.util
import inspect
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Optional

from utils.constant import is_debug
from utils.log_kit import get_logger
from utils.path_kit import get_file_path, get_folder_path, get_backtest_file_path
from model.config_model import BacktestConfig, create_config_from_dict, create_strategy_from_dict


# 初始化日志记录器
logger = get_logger()


class MockModule:
    """模拟模块类，用于处理缺失的依赖模块"""

    def __getattr__(self, name):
        if name == 'get_folder_path':
            # 返回一个模拟的路径函数
            return lambda *args: os.path.join(os.getcwd(), 'mock_data')
        return lambda *args, **kwargs: None


class ConfigService:
    """配置服务类"""

    def __init__(self):
        self.mock_modules = ['core.utils.path_kit', 'core', 'core.utils']

    def serialize_complex_value(self, value):
        """
        递归序列化复杂数据结构，确保可以JSON序列化
        """
        try:
            if isinstance(value, (list, tuple)):
                return [self.serialize_complex_value(item) for item in value]
            elif isinstance(value, dict):
                return {k: self.serialize_complex_value(v) for k, v in value.items()}
            elif isinstance(value, range):
                return list(value)
            elif isinstance(value, Path):
                return str(value)
            elif hasattr(value, '__dict__'):
                return str(value)
            else:
                return value
        except Exception:
            return str(value)

    def parse_config_variables(self, config_file_path):
        """
        解析config.py文件中的变量
        使用动态导入的方式，参考用户提供的简洁方法
        """
        logger.info(f"开始解析配置文件: {config_file_path}")

        try:
            # 检查文件是否存在
            if not os.path.exists(config_file_path):
                logger.error(f"配置文件不存在: {config_file_path}")
                return {"error": "配置文件不存在"}

            # 动态导入config模块
            spec = importlib.util.spec_from_file_location("config", config_file_path)
            config_module = importlib.util.module_from_spec(spec)

            # 在sys.modules中注册模拟模块
            original_modules = {}

            for module_name in self.mock_modules:
                if module_name not in sys.modules:
                    original_modules[module_name] = None
                    sys.modules[module_name] = MockModule()

            try:
                # 执行模块
                spec.loader.exec_module(config_module)
            except SystemExit:
                # 捕获exit()调用，但继续解析已有变量
                logger.warning("配置模块包含exit()调用，已忽略")
                pass

            # 使用用户提供的方法提取自定义变量，但过滤掉导入的类、函数等
            config_dict = {}
            for key, value in vars(config_module).items():
                # 跳过私有变量和模块
                if key.startswith("__") or isinstance(value, ModuleType):
                    continue

                # 跳过导入的类和函数
                if (callable(value) or
                        inspect.isclass(value) or
                        inspect.isfunction(value) or
                        inspect.isbuiltin(value) or
                        inspect.ismethod(value)):
                    continue

                # 保留配置变量
                if key == 'strategy_pool':
                    strategy_pool = self.serialize_complex_value(value)
                    for index, stg_dict in enumerate(strategy_pool):
                        if 'name' not in stg_dict:
                            stg_dict['name'] = f"S{index + 1}-自动填充名称"
                        for stg in stg_dict.get('strategy_list', []):
                            if 'is_use_spot' in stg:
                                stg['market'] = 'spot_swap' if stg['is_use_spot'] else 'swap_swap'
                                del stg['is_use_spot']
                    config_dict[key] = strategy_pool
                else:
                    config_dict[key] = self.serialize_complex_value(value)

                # 根据 cpu 核心数，筛选性能模式
                if key == 'job_num':
                    economy = min(int(os.cpu_count() / 3), 63)
                    equal = min(int(os.cpu_count() / 2), 63)
                    if value <= economy:
                        config_dict['performance_mode'] = 'ECONOMY'
                    elif value <= equal:
                        config_dict['performance_mode'] = 'EQUAL'
                    else:
                        config_dict['performance_mode'] = 'PERFORMANCE'

            # 清理模拟模块
            for module_name in self.mock_modules:
                if module_name in original_modules:
                    if original_modules[module_name] is None:
                        sys.modules.pop(module_name, None)
                    else:
                        sys.modules[module_name] = original_modules[module_name]

            # 处理simulator_config解构
            if 'simulator_config' in config_dict and isinstance(config_dict['simulator_config'], dict):
                simulator_config = config_dict['simulator_config']
                # 将simulator_config中的字段提取到顶层
                for key, value in simulator_config.items():
                    config_dict[key] = value
                # 移除simulator_config字典
                del config_dict['simulator_config']

            logger.ok(f"配置解析完成，获取到 {len(config_dict)} 个配置变量")
            return config_dict

        except Exception as e:
            error_msg = f"配置文件解析失败: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return {"error": error_msg}

    def get_config_data(self, config_name: str = 'config'):
        """获取完整的配置数据，只返回业务数据，错误抛出异常。支持指定配置名"""
        logger.info(f"开始获取配置数据: {config_name}")

        config_path = self.get_config_file_path(config_name)
        config_data = self.parse_config_variables(config_path)

        if "error" in config_data:
            logger.error("配置数据获取失败")
            raise RuntimeError(config_data["error"])

        logger.ok("配置数据获取成功")
        return config_data

    @staticmethod
    def get_config_file_path(config_name: str = 'config'):
        """获取配置文件路径，支持指定配置名"""
        if is_debug or config_name != 'config':
            return get_file_path('data', f'{config_name}.py', as_path_type=False)
        return get_backtest_file_path(f'{config_name}.py', as_path_type=False)

    @staticmethod
    def generate_config_file_content(config: BacktestConfig) -> str:
        """生成配置文件的Python代码内容"""
        logger.info(f"生成配置文件内容: {config.name}")

        # 获取配置数据字典
        config_dict = config.to_dict()

        # 生成Python代码
        content_parts = []

        # 文件头注释
        content_parts.append('"""')
        content_parts.append('邢不行｜策略分享会')
        content_parts.append('仓位管理框架')
        content_parts.append('')
        content_parts.append('版权所有 ©️ 邢不行')
        content_parts.append('微信: xbx1717')
        content_parts.append('')
        content_parts.append('本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。')
        content_parts.append('')
        content_parts.append('Author: 邢不行')
        content_parts.append('"""')
        content_parts.append('import os')
        content_parts.append('from pathlib import Path')
        content_parts.append('')
        content_parts.append('from core.utils.path_kit import get_folder_path')
        content_parts.append('')

        # 回测配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 回测配置 **')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# region 回测策略细节配置')
        content_parts.append(f"start_date = '{config_dict['start_date']}'  # 回测开始时间")
        content_parts.append(f"end_date = '{config_dict['end_date']}'  # 回测结束时间")
        content_parts.append('')

        # 数据配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 数据配置 **')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# 数据存储路径，填写绝对路径')
        content_parts.append('# 使用官方准备的预处理数据，专门用于本框架回测使用，大幅提高速度')
        content_parts.append('# 现货和合约1小时预处理数据（pkl格式）：https://www.quantclass.cn/data/coin/coin-binance-spot-swap-preprocess-pkl-1h')
        content_parts.append(f"pre_data_path = r'{config_dict['pre_data_path']}'")

        # 添加 min_kline_num 配置（使用默认值如果不存在）
        min_kline_num = config_dict.get('min_kline_num', 168)
        content_parts.append(f"min_kline_num = {min_kline_num}  # 最少上市多久，不满该K线根数的币剔除，即剔除刚刚上市的新币。168：标识168个小时，即：7*24")

        # 添加 reserved_cache 配置（使用默认值如果不存在）
        reserved_cache = config_dict.get('reserved_cache', ('select',))
        content_parts.append(f"reserved_cache = {repr(reserved_cache)}  # 用于缓存控制：['select']表示只缓存选币结果，不缓存其他数据，['all']表示缓存所有数据。")
        content_parts.append('# 目前支持选项：')
        content_parts.append('# - select: 选币结果pkl')
        content_parts.append('# - strategy: 大杂烩中策略选币pkl')
        content_parts.append('# - ratio: 最终模拟持仓的各个币种资金占比')
        content_parts.append('# - all: 无视上述配置细节，包含 `all` 就代表我全要')
        content_parts.append('# 缓存东西越多，硬盘消耗越大，对于参数比较多硬盘没那么大的童鞋，可以在这边设置')
        content_parts.append('')

        # 策略细节配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 策略细节配置 **')
        content_parts.append('# 案例策略，需要自己探索，不保证可用')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"backtest_name = '{config_dict['backtest_name']}'  # 回测的策略组合的名称。可以自己任意取。一般建议，一个回测组，就是实盘中的一个账户。")
        content_parts.append('')

        # strategy_config (简化版本，如果没有则使用默认结构)
        strategy_config = config_dict.get('strategy_config', {
            'name': 'FixedRatioStrategy',
            'hold_period': '1H',
            'params': {
                'cap_ratios': [1/3, 1/3, 1/3]
            }
        })
        content_parts.append('strategy_config = {')
        content_parts.append(f"    'name': '{strategy_config.get('name', 'FixedRatioStrategy')}',  # *必填。使用什么策略，这里是轮动策略")
        content_parts.append(f"    'hold_period': '{strategy_config.get('hold_period', '1H')}',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......")
        content_parts.append("    'params': {")
        params = strategy_config.get('params', {})
        for key, value in params.items():
            if not value:
                continue
            if key == 'cap_ratios' and isinstance(value, list):
                content_parts.append(f"        '{key}': [")
                for ratio in value:
                    content_parts.append(f"            {ratio},")
                content_parts.append("        ],")
            else:
                content_parts.append(f"        '{key}': {repr(value)},")
        content_parts.append("    },")

        symbol_ratio_limit = strategy_config.get('symbol_ratio_limit', {})
        if symbol_ratio_limit:
            content_parts.append("    'symbol_ratio_limit': {")
            for direction in symbol_ratio_limit.keys():
                # 使用提供的配置或默认配置
                direction_config = symbol_ratio_limit.get(direction, {})
                content_parts.append(f"        '{direction}': {{")
                for key, value in direction_config.items():
                    if isinstance(value, str):
                        content_parts.append(f"            '{key}': '{value}',")
                    else:
                        content_parts.append(f"            '{key}': {value},")
                content_parts.append("        },")
            content_parts.append("    }")
        content_parts.append('}')
        content_parts.append('')

        # 全部策略混合 - strategy_pool
        content_parts.append('# 全部策略混合')
        content_parts.append('strategy_pool = [')

        # 生成 strategy_pool 结构
        strategy_pool = config_dict.get('strategy_pool', [])
        if strategy_pool:
            for i, pool_item in enumerate(strategy_pool):
                content_parts.append(f'    # {i+1}.策略组合')
                content_parts.append('    dict(')
                content_parts.append(f"        name='{pool_item.get('name', f'自动填充名称-{i+1}')}',")
                content_parts.append('        strategy_list=[')

                strategy_list = pool_item.get('strategy_list', [])
                for strategy_dict in strategy_list:
                    content_parts.append('            {')
                    for key, value in strategy_dict.items():
                        if key == 'offset_list' and isinstance(value, list):
                            # 处理range对象
                            if len(value) > 0 and value == list(range(value[0], value[-1] + 1, 1)):
                                content_parts.append(f'                "{key}": list(range({value[0]}, {value[-1] + 1}, 1)),')
                            else:
                                content_parts.append(f'                "{key}": {repr(value)},')
                        elif isinstance(value, str):
                            content_parts.append(f'                "{key}": "{value}",')
                        elif isinstance(value, list) and len(value) > 0:
                            content_parts.append(f'                "{key}": {repr(value)},')
                        elif value is not None:
                            content_parts.append(f'                "{key}": {repr(value)},')
                    content_parts.append('            },')
                content_parts.append('        ],')

                # 添加可选的 re_timing 配置
                re_timing = pool_item.get('re_timing')
                if re_timing:
                    content_parts.append('        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟')
                    content_parts.append(f"        re_timing={repr(re_timing)}  # 可选，配置再择时策略")

                if i < len(strategy_pool) - 1:
                    content_parts.append('    ),')
                else:
                    content_parts.append('    ),')

        content_parts.append(']')
        content_parts.append('')

        # 其他配置
        content_parts.append(f"leverage = {config_dict['leverage']}  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。")
        content_parts.append(f"black_list = {config_dict['black_list']}  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'")
        content_parts.append(f"white_list = {config_dict['white_list']}  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'")

        # rebalance_mode 注释掉
        if config_dict.get('rebalance_mode'):
            content_parts.append(f"rebalance_mode = {repr(config_dict['rebalance_mode'])}")
        else:
            content_parts.append('# rebalance_mode =')
        content_parts.append('')

        # 模拟器细节配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 模拟器细节配置 **')
        content_parts.append('# 也就是如何模拟的细节，帮你按照你策略指令，计算资金曲线')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('simulator_config = dict(')
        content_parts.append('    # 模拟下单回测设置')
        content_parts.append(f"    account_type='{config_dict['account_type']}',  # '统一账户'或者'普通账户'")
        content_parts.append(f"    initial_usdt={config_dict['initial_usdt']:.0f},  # 初始资金")
        content_parts.append(f"    margin_rate={config_dict['margin_rate']},  # 维持保证金率，净值低于这个比例会爆仓")
        content_parts.append(f"    swap_c_rate={config_dict['swap_c_rate']},  # 合约手续费(包含滑点)")
        content_parts.append(f"    spot_c_rate={config_dict['spot_c_rate']},  # 现货手续费(包含滑点)")
        content_parts.append(f"    swap_min_order_limit={config_dict['swap_min_order_limit']},  # 合约最小下单量。最小不能低于5")
        content_parts.append(f"    spot_min_order_limit={config_dict['spot_min_order_limit']},  # 现货最小下单量。最小不能低于10")
        content_parts.append(f"    avg_price_col='{config_dict['avg_price_col']}',  # 用于模拟计算的平均价，预处理数据使用的是1m，'avg_price_1m'表示1分钟的均价, 'avg_price_5m'表示5分钟的均价。")
        content_parts.append('    # 用于对齐 非24约数持仓周期(`strategy_config -> hold_period`)的资金曲线。')
        content_parts.append('    # 以下配置不需要调整：1H,2H,3H,4H,6H,8H,12H')
        content_parts.append('    # 其他持仓周期，可以根据回测与实盘的情况，调整一致')
        content_parts.append("    unified_time='2017-01-01',")
        content_parts.append(')')
        content_parts.append('')

        # 数据配置（第二部分）
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 数据配置 **')
        content_parts.append('# - 配置需要的额外数据')
        content_parts.append('# ' + '=' * 100)
        content_parts.append('data_source_dict = {')
        content_parts.append('    # 数据源的标签,需要与因子文件中的 extra_data_dict 中的 key 保持一致')
        for key, value in config_dict['data_source_dict'].items():
            content_parts.append(f'    "{key}": {repr(value)},')
        content_parts.append('}')
        content_parts.append('')

        # 全局设置及自动化
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 全局设置及自动化 **')
        content_parts.append('# 这些设置是客观事实，基本不会影响到回测的细节，正常不用去改动')
        content_parts.append('# ' + '=' * 100)
        if config_dict['job_num'] is None:
            content_parts.append("job_num = max(os.cpu_count() - 1, 1)  # 回测并行数量")
        else:
            content_parts.append(f"job_num = {config_dict['job_num']}  # 回测并行数量")
        content_parts.append('# job_num = 2  # 回测并行数量')
        content_parts.append('')

        # factor_col_limit 详细介绍
        content_parts.append('# ==== factor_col_limit 介绍 ====')
        content_parts.append(f"factor_col_limit = {config_dict['factor_col_limit']}  # 内存优化选项，一次性计算多少列因子。64是 16GB内存 电脑的典型值")
        content_parts.append('# - 数字越大，计算速度越快，但同时内存占用也会增加。')
        content_parts.append('# - 该数字是在 "因子数量 * 参数数量" 的基础上进行优化的。')
        content_parts.append('#   - 例如，当你遍历 200 个因子，每个因子有 10 个参数，总共生成 2000 列因子。')
        content_parts.append('#   - 如果 `factor_col_limit` 设置为 64，则计算会拆分为 ceil(2000 / 64) = 32 个批次，每次最多处理 64 列因子。')
        content_parts.append('# - 对于16GB内存的电脑，在跑含现货的策略时，64是一个合适的设置。')
        content_parts.append('# - 如果是在16GB内存下跑纯合约策略，则可以考虑将其提升到 128，毕竟数值越高计算速度越快。')
        content_parts.append('# - 以上数据仅供参考，具体值会根据机器配置、策略复杂性、回测周期等有所不同。建议大家根据实际情况，逐步测试自己机器的性能极限，找到适合的最优值。')
        content_parts.append('')

        # 路径处理
        content_parts.append('# 路径处理')
        content_parts.append('raw_data_path = Path(pre_data_path)  # 预处理数据路径')
        content_parts.append("spot_path = raw_data_path / 'spot_dict.pkl'  # 现货数据路径")
        content_parts.append("swap_path = raw_data_path / 'swap_dict.pkl'  # 合约数据路径")
        content_parts.append('')

        # 回测结果数据路径
        content_parts.append('# 回测结果数据路径。用于发帖脚本使用')
        content_parts.append("backtest_path = Path(get_folder_path('data', '仓位管理回测结果'))")
        content_parts.append("backtest_iter_path = Path(get_folder_path('data', '子策略回测结果'))")
        content_parts.append('')

        # 稳定币信息
        content_parts.append('# 稳定币信息，不参与交易的币种')
        stable_coins = ['BKRW', 'USDC', 'USDP', 'TUSD', 'BUSD', 'FDUSD', 'DAI', 'EUR', 'GBP', 'USBP', 'SUSD', 'PAXG', 'AEUR',
                        'EURI']
        content_parts.append('stable_symbol = [' + repr(stable_coins)[1:-1] + ']')
        content_parts.append('')

        # 检查和验证
        content_parts.append("if spot_path.exists() is False or swap_path.exists() is False:")
        content_parts.append("    print('⚠️ 请先准确配置预处理数据的位置（pre_data_path）。建议直接复制绝对路径，并且粘贴给 pre_data_path')")
        content_parts.append("    exit()")

        return '\n'.join(content_parts)

    def save_config_file(self, config: BacktestConfig) -> dict:
        """保存配置到Python文件，只返回业务数据，错误抛出异常"""
        logger.info(f"开始保存配置文件: {config.name}")

        # 验证配置数据
        errors = config.validate()
        if errors:
            logger.error(f"配置验证失败: {errors}")
            raise ValueError(f"配置数据验证失败: {errors}")

        # 生成文件内容
        content = self.generate_config_file_content(config)

        # 确定保存路径
        filename = f"{config.name}.py"
        if config.name == 'config' and not is_debug:
            file_path = get_backtest_file_path('config.py', as_path_type=False)
        else:
            file_path = get_file_path('data', filename, as_path_type=False)

        # 保存文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.ok(f"配置文件保存成功: {file_path}")

        return {
            "config_name": config.name,
            "file_path": file_path,
            "filename": filename
        }

    def get_config_list(self) -> list:
        """获取data目录下所有配置文件列表，只返回业务数据，错误抛出异常"""
        logger.info("获取配置文件列表")

        try:
            data_dir = get_folder_path('data')

            configs = []

            if data_dir.exists():
                for file in data_dir.iterdir():
                    if file.is_file() and file.suffix == ".py" and not file.name.startswith('_'):
                        equity_path = get_backtest_file_path('data', '仓位管理回测结果', file.stem, '资金曲线.csv')
                        configs.append(
                            dict(
                                name=file.stem,
                                last_update_time=datetime.fromtimestamp(file.stat().st_mtime).strftime(
                                    '%Y-%m-%d %H:%M:%S'),
                                backtest_time=(
                                    datetime.fromtimestamp(equity_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                                    if equity_path.exists() else None
                                ),
                            )
                        )
                    if not is_debug:
                        config_path = get_backtest_file_path('config.py')
                        config_data = self.parse_config_variables(config_path)
                        config_equity_path = get_backtest_file_path('data', '仓位管理回测结果',
                                                                    config_data.get('backtest_name', 'config'),
                                                                    '资金曲线.csv')
                        configs.append(
                            dict(
                                name=config_path.stem,
                                last_update_time=datetime.fromtimestamp(config_path.stat().st_mtime).strftime(
                                    "%Y-%m-%d %H:%M:%S"),
                                backtest_time=(
                                    datetime.fromtimestamp(config_equity_path.stat().st_mtime).strftime(
                                        "%Y-%m-%d %H:%M:%S")
                                    if config_equity_path.exists() else None
                                ),
                            )
                        )
            logger.ok(f"找到 {len(configs)} 个配置文件")

            return configs

        except Exception as e:
            logger.error(f"获取配置列表失败: {e}")
            raise RuntimeError(f'获取配置列表失败: {str(e)}')

    @staticmethod
    def create_config_from_request(data: dict) -> BacktestConfig:
        """从请求数据创建配置对象"""
        logger.info("从请求数据创建配置对象")

        try:
            config = create_config_from_dict(data)
            logger.ok("配置对象创建成功")
            return config
        except Exception as e:
            logger.error(f"创建配置对象失败: {str(e)}")
            raise

    @staticmethod
    def process_symbol(symbol_list):
        results = []
        for symbol in symbol_list:
            if symbol.endswith('USDT') and '-' not in symbol:
                symbol = symbol.replace('USDT', '-USDT')
            results.append(symbol)
        return results

    def convert_real_trading_to_backtest_config(self, config_file_path: Path) -> Optional[str]:
        """将实盘配置文件转换为回测配置文件"""
        logger.info(f"开始转换实盘配置文件: {config_file_path}")

        try:
            # 检查文件是否存在
            if not os.path.exists(config_file_path):
                logger.error(f"配置文件不存在: {config_file_path}")
                return None

            # 动态导入config模块
            spec = importlib.util.spec_from_file_location("config", config_file_path)
            config_module = importlib.util.module_from_spec(spec)

            # 在sys.modules中注册模拟模块
            original_modules = {}

            for module_name in self.mock_modules:
                if module_name not in sys.modules:
                    original_modules[module_name] = None
                    sys.modules[module_name] = MockModule()

            try:
                # 执行模块
                spec.loader.exec_module(config_module)
            except SystemExit:
                # 捕获exit()调用，但继续解析已有变量
                logger.warning("配置模块包含exit()调用，已忽略")
                pass
            except Exception as e:
                logger.warning(f"执行配置文件失败: {e}")
                # 即使有错误，也尝试继续解析
                pass

            # 提取实盘配置中的strategy_pool（位于顶层，不是account_config中）
            real_strategy_pool = getattr(config_module, 'strategy_pool', None)
            if not real_strategy_pool:
                logger.warning("未找到strategy_pool")
                return None

            # 检查strategy_pool中的name字段重名
            logger.info(f"开始检查strategy_pool重名，共{len(real_strategy_pool)}个策略组合")
            name_counts = {}
            duplicate_names = []

            for i, strategy_dict in enumerate(real_strategy_pool):
                name = strategy_dict.get('name', f'策略组合{i+1}')
                if name in name_counts:
                    name_counts[name] += 1
                    if name not in duplicate_names:
                        duplicate_names.append(name)
                else:
                    name_counts[name] = 1

            if duplicate_names:
                duplicate_info = []
                for name in duplicate_names:
                    duplicate_info.append(f"'{name}' (出现{name_counts[name]}次)")

                error_msg = f"strategy_pool中存在重名的策略组合: {', '.join(duplicate_info)}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            else:
                logger.info("strategy_pool重名检查通过")

            # 转换为BacktestConfig格式的strategy_pool
            strategy_configs = []
            for strategy_dict in real_strategy_pool:
                # 创建StrategyConfig对象，使用create_strategy_from_dict确保字段过滤
                strategy_config = create_strategy_from_dict(strategy_dict)
                strategy_configs.append(strategy_config)

            # 从config.py获取fuel_data_path
            from config import fuel_data_path
            local_data_path = Path(fuel_data_path)

            # 从原配置中提取其他字段
            real_black_list = getattr(config_module, 'black_list', [])
            real_white_list = getattr(config_module, 'white_list', [])

            # 准备完整的config_data字典，匹配BacktestConfig结构
            config_data = {
                'name': f"{config_file_path.stem}",
                'pre_data_path': str(local_data_path / 'coin-binance-spot-swap-preprocess-pkl-1h'),
                'data_source_dict': {"coin-cap": ('load_coin_cap', str(local_data_path / 'coin-cap'))},
                'min_kline_num': getattr(config_module, 'min_kline_num', 168),
                'reserved_cache': getattr(config_module, 'reserved_cache', ['select']),
                'start_date': getattr(config_module, 'start_date', '2021-01-01 00:00:00'),
                'end_date': getattr(config_module, 'end_date', '2025-01-07'),
                'backtest_name': f"{config_file_path.stem}回测版",
                'strategy_config': getattr(config_module, 'strategy_config', {}),
                'strategy_pool': strategy_configs,  # ✅ CORRECTED: 使用strategy_pool而不是strategy_list
                'leverage': getattr(config_module, 'leverage', 1),
                'black_list': self.process_symbol(real_black_list),
                'white_list': self.process_symbol(real_white_list),
                'simulator_config': {
                    'account_type': '普通账户',
                    'initial_usdt': 10000,
                    'margin_rate': 0.05,
                    'swap_c_rate': 0.0005,
                    'spot_c_rate': 0.001,
                    'swap_min_order_limit': 5,
                    'spot_min_order_limit': 10,
                    'avg_price_col': 'avg_price_1m',
                    'unified_time': '2017-01-01',
                },
                'job_num': max(os.cpu_count() - 1, 1),
                'factor_col_limit': 64,
            }

            # 使用create_config_from_dict来创建BacktestConfig对象
            backtest_config = create_config_from_dict(config_data)

            # 保存配置文件
            result = self.save_config_file(backtest_config)

            # 清理模拟模块
            for module_name in self.mock_modules:
                if module_name in original_modules:
                    if original_modules[module_name] is None:
                        sys.modules.pop(module_name, None)
                    else:
                        sys.modules[module_name] = original_modules[module_name]

            logger.info(f"配置文件转换成功: {result['filename']}")
            return result['filename']

        except Exception as e:
            logger.error(f"转换配置文件失败: {e}")
            logger.error(traceback.format_exc())
            return None

    def convert_backtest_to_real_trading_config(self, config_name: str) -> Optional[str]:
        """将回测配置文件转换为实盘配置文件"""
        logger.info(f"开始转换回测配置为实盘配置: {config_name}")

        try:
            # 获取回测配置数据
            backtest_config_data = self.get_config_data(config_name)

            # 提取需要的字段
            strategy_config = backtest_config_data.get('strategy_config', {})
            strategy_pool = backtest_config_data.get('strategy_pool', [])
            leverage = backtest_config_data.get('leverage', 1)
            black_list = backtest_config_data.get('black_list', [])
            white_list = backtest_config_data.get('white_list', [])

            # 处理strategy_pool中的字段转换 (market → is_use_spot)
            processed_strategy_pool = []
            for pool_item in strategy_pool:
                processed_pool = pool_item.copy()
                processed_strategy_list = []

                for strategy in pool_item.get('strategy_list', []):
                    processed_strategy = strategy.copy()
                    processed_strategy_list.append(processed_strategy)

                processed_pool['strategy_list'] = processed_strategy_list
                processed_strategy_pool.append(processed_pool)

            # 设置实盘配置的默认值
            account_config = {'hour_offset': '25m'}
            strategy_name = backtest_config_data.get('backtest_name', f'{config_name}实盘策略')
            get_kline_num = 1000
            min_kline_num = backtest_config_data.get('min_kline_num', 168)

            # 生成实盘配置文件内容
            content = self.generate_real_trading_config_content(
                account_config=account_config,
                strategy_name=strategy_name,
                get_kline_num=get_kline_num,
                min_kline_num=min_kline_num,
                strategy_config=strategy_config,
                strategy_pool=processed_strategy_pool,
                leverage=leverage,
                black_list=black_list,
                white_list=white_list,
            )

            # 保存实盘配置文件到临时目录
            export_dir = get_folder_path("data", "temp", "export")
            export_dir.mkdir(parents=True, exist_ok=True)

            real_trading_config_filename = f"config_{config_name}_real_trading.py"
            real_trading_config_path = export_dir / real_trading_config_filename

            with open(real_trading_config_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.ok(f"实盘配置文件生成成功: {real_trading_config_path}")
            return str(real_trading_config_path)

        except Exception as e:
            logger.error(f"转换回测配置为实盘配置失败: {e}")
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def generate_real_trading_config_content(account_config, strategy_name, get_kline_num, min_kline_num,
                                           strategy_config, strategy_pool, leverage, black_list,
                                           white_list) -> str:
        """生成实盘配置文件的Python代码内容"""

        # 定义需要将内部列表转换为元组的字段
        tuple_fields = {
            'factor_list', 'long_factor_list', 'short_factor_list',
            'filter_list', 'long_filter_list', 'short_filter_list',
            'filter_list_post', 'long_filter_list_post', 'short_filter_list_post'
        }

        content_parts = []

        # 文件头注释
        content_parts.append('"""')
        content_parts.append('邢不行｜策略分享会')
        content_parts.append('仓位管理实盘框架')
        content_parts.append('')
        content_parts.append('版权所有 ©️ 邢不行')
        content_parts.append('微信: xbx1717')
        content_parts.append('')
        content_parts.append('本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。')
        content_parts.append('')
        content_parts.append('Author: 邢不行')
        content_parts.append('"""')

        # 实盘账户配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 实盘账户配置 **')
        content_parts.append('# ‼️‼️‼️账户配置，需要在accounts下的文件中做配置 ‼️‼️‼️')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"account_config = {repr(account_config)}  # 实盘账户配置")
        content_parts.append('')

        # 策略细节配置
        content_parts.append('# ' + '=' * 100)
        content_parts.append('# ** 策略细节配置 **')
        content_parts.append('# ‼️‼️‼️需要在accounts下的文件中做配置‼️‼️‼️')
        content_parts.append('# ' + '=' * 100)
        content_parts.append(f"strategy_name = '{strategy_name}'  # 当前账户运行策略的名称。可以自己任意取")
        content_parts.append(f"min_kline_num = '{min_kline_num}'  # 最少上市多久，不满该K线根数的币剔除，即剔除刚刚上市的新币。168：标识168个小时，即：7*24")
        content_parts.append(f"get_kline_num = {get_kline_num}  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k")

        # strategy_config
        content_parts.append('strategy_config = {')
        content_parts.append(f"    'name': '{strategy_config.get('name', 'FixedRatioStrategy')}',  # *必填。使用什么策略")
        content_parts.append(f"    'hold_period': '{strategy_config.get('hold_period', '1H')}',  # *必填。聚合后策略持仓周期")
        content_parts.append("    'params': {")

        params = strategy_config.get('params', {})
        for key, value in params.items():
            if isinstance(value, str):
                content_parts.append(f"        '{key}': '{value}',")
            else:
                content_parts.append(f"        '{key}': {repr(value)},")

        content_parts.append("    },")

        # symbol_ratio_limit 配置
        symbol_ratio_limit = strategy_config.get('symbol_ratio_limit', {})
        if symbol_ratio_limit:
            content_parts.append("    'symbol_ratio_limit': {")
            for direction in symbol_ratio_limit.keys():
                direction_config = symbol_ratio_limit.get(direction, {})
                content_parts.append(f"        '{direction}': {{")
                for key, value in direction_config.items():
                    if isinstance(value, str):
                        content_parts.append(f"            '{key}': '{value}',")
                    else:
                        content_parts.append(f"            '{key}': {value},")
                content_parts.append("        },")
            content_parts.append("    },")

        content_parts.append('}  # 策略配置')

        # strategy_pool
        content_parts.append('strategy_pool = [')
        for pool_item in strategy_pool:
            content_parts.append('    dict(')
            content_parts.append(f"        name='{pool_item.get('name', '策略组合')}',")
            content_parts.append('        strategy_list=[')

            for strategy in pool_item.get('strategy_list', []):
                content_parts.append('            {')
                for key, value in strategy.items():
                    if isinstance(value, str):
                        content_parts.append(f'                "{key}": "{value}",')
                    elif isinstance(value, list) and len(value) > 0:
                        # 对于tuple_fields中的字段，需要将内部列表转换为元组
                        if key in tuple_fields:
                            # 转换内部列表为元组
                            converted_value = [tuple(item) if isinstance(item, list) else item for item in value]
                            content_parts.append(f'                "{key}": {repr(converted_value)},')
                        else:
                            content_parts.append(f'                "{key}": {repr(value)},')
                    elif value is not None:
                        content_parts.append(f'                "{key}": {repr(value)},')
                content_parts.append('            },')

            content_parts.append('        ],')

            # 添加可选的 re_timing 配置
            re_timing = pool_item.get('re_timing')
            if re_timing:
                content_parts.append(f"        re_timing={repr(re_timing)},")

            content_parts.append('    ),')

        content_parts.append(']  # 策略池')

        # 其他配置
        content_parts.append(f"leverage = {leverage}  # 杠杆数")
        content_parts.append(f"black_list = {repr(black_list)}  # 拉黑名单")
        content_parts.append(f"white_list = {repr(white_list)}  # 白名单")
        content_parts.append('# rebalance_mode =  # 换仓模式控制')

        return '\n'.join(content_parts)

    def execute_backtest_script(self, python_exec: str, py_file: Path):
        try:
            logger.info(f"开始执行脚本: {py_file}")

            # 构建命令
            cmd = [python_exec, str(py_file)]

            # 创建进程 - 在Windows上不使用universal_newlines=True，手动处理编码
            is_windows = platform.system().lower() == 'windows'
            
            if is_windows:
                # Windows系统：使用二进制模式，手动处理编码
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1
                )

                # 启动输出读取线程
                output_queue = queue.Queue()
                output_thread = threading.Thread(target=self.read_output_windows, args=(process.stdout, output_queue))
                output_thread.daemon = True
                output_thread.start()
            else:
                # 非Windows系统：使用文本模式
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1
                )
                
                # 启动输出读取线程
                output_queue = queue.Queue()
                output_thread = threading.Thread(target=self.read_output, args=(process.stdout, output_queue))
                output_thread.daemon = True
                output_thread.start()

            # 监控输出并记录日志
            while True:
                try:
                    # 检查进程是否还在运行
                    if process.poll() is not None:
                        break

                    # 读取输出
                    try:
                        line = output_queue.get_nowait()
                        print(line)
                    except queue.Empty:
                        pass

                    # 短暂休眠避免CPU占用过高
                    # time.sleep(0.03)

                except Exception as e:
                    logger.error(f"监控回测输出失败: {e}")
                    break

            # 等待进程结束
            return_code = process.wait()

            if return_code == 0:
                logger.ok("回测执行完成")
            else:
                logger.error(f"回测执行失败，返回码: {return_code}")
                raise Exception('回测执行失败')

        except Exception as e:
            logger.error(f"执行脚本失败: {e}")
            logger.error(traceback.format_exc())
            raise e

    @staticmethod
    def read_output(pipe, _queue):
        # 实时读取输出
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    _queue.put(line.strip())
            pipe.close()
        except Exception as e:
            logger.error(f"读取输出失败: {e}")

    @staticmethod
    def read_output_windows(pipe, _queue):
        """Windows系统专用的输出读取方法，处理编码问题"""
        try:
            # 尝试多种编码方式
            encodings = ['utf-8', 'gbk', 'gb2312', 'cp936', 'latin-1']
            
            for line in iter(pipe.readline, b''):
                if line:
                    # 尝试不同的编码
                    decoded_line = None
                    for encoding in encodings:
                        try:
                            decoded_line = line.decode(encoding, errors='replace')
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    if decoded_line:
                        _queue.put(decoded_line.strip())
                    else:
                        # 如果所有编码都失败，使用replace模式
                        decoded_line = line.decode('utf-8', errors='replace')
                        _queue.put(decoded_line.strip())
            
            pipe.close()
        except Exception as e:
            logger.error(f"Windows读取输出失败: {e}")
            logger.error(traceback.format_exc())

# 创建全局配置服务实例
config_service = ConfigService()
