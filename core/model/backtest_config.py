"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import shutil
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import List, Dict, Optional, Set

import pandas as pd

from config import backtest_path, backtest_iter_path, backtest_name
from core.factor import calc_factor_vals
from core.model.account_type import AccountType
from core.model.rebalance_mode import RebalanceMode
from core.model.strategy_config import StrategyConfig, PosStrategyConfig
from core.model.timing_signal import TimingSignal
from core.utils.factor_hub import FactorHub
from core.utils.log_kit import logger
from core.utils.path_kit import get_folder_path, get_file_path
from core.utils.strategy_hub import StrategyHub


class BacktestConfig:
    data_file_fingerprint: str = ''  # 记录数据文件的指纹

    def __init__(self, name: str, **conf):
        self.name: str = name  # 账户名称，建议用英文，不要带有特殊符号
        self.original_name: str = name  # 策略原始名称，不加 S 前缀

        self.start_date: str = conf.get("start_date", '2021-01-01')  # 回测开始时间
        self.end_date: str = conf.get("end_date", '2024-03-30')  # 回测结束时间

        # 账户回测交易模拟配置
        self.account_type: AccountType = AccountType.translate(conf.get("account_type", '普通账户'))  # 账户类型
        self.rebalance_mode: RebalanceMode = RebalanceMode.init(conf.get('rebalance_mode', None))
        self.leverage: int | float = conf.get("leverage", 1)  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。

        self.avg_price_col: str = conf.get("avg_price_col", 'avg_price_1m')  # 平均成交价格
        self.initial_usdt: int | float = conf.get("initial_usdt", 10000)  # 初始现金
        self.margin_rate = conf.get('margin_rate', 0.05)  # 维持保证金率，净值低于这个比例会爆仓

        self.swap_c_rate: float = conf.get("swap_c_rate", 6e-4)  # 合约买卖手续费
        self.spot_c_rate: float = conf.get("spot_c_rate", 2e-3)  # 现货买卖手续费

        self.swap_min_order_limit: int | float = conf.get("swap_min_order_limit", 5)  # 合约最小下单量
        self.spot_min_order_limit: int | float = conf.get("spot_min_order_limit", 10)  # 现货最小下单量

        # 策略配置
        self.black_list: List[str] = conf.get('black_list',
                                              [])  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
        self.white_list: List[str] = conf.get('white_list',
                                              [])  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
        self.min_kline_num: int = conf.get('min_kline_num', 168)  # 最少上市多久，不满该K线根数的币剔除，即剔除刚刚上市的新币。168：标识168个小时，即：7*24

        self.unified_time: str = conf.get('unified_time', '2017-01-01')  # 计算 offset，对齐资金曲线的统一时间

        # 再择时配置
        self.timing: Optional[TimingSignal] = None

        # 参数遍历场景需要
        self.is_param_search = False

        self.select_scope_set: Set[str] = set()
        self.order_first_set: Set[str] = set()
        self.is_day_period: bool = False  # 是否是日盘，否则是小时盘
        self.is_hour_period: bool = False  # 是否是小时盘，否则是日盘
        self.factor_params_dict: Dict[str, set] = {}
        self.factor_col_name_list: List[str] = []
        self.max_hold_period: str = '1H'  # 最大的持仓周期，默认值设置为最小
        self.hold_period_list: List[str] = []  # 持仓周期列表
        self.max_offset_len: int = 0

        # 策略列表，包含每个策略的详细配置
        self.strategy_list: List[StrategyConfig] = []
        self.strategy_name_list: List[str] = []
        self.strategy_list_raw: List[dict] = []

        # 策略评价
        self.report: Optional[pd.DataFrame] = None
        self.reserved_cache: set = conf.get('reserved_cache', set())  # 缓存控制

        # 遍历标记
        self.iter_round: int | str = 0  # 遍历的INDEX，0表示非遍历场景，从1、2、3、4、...开始表示是第几个循环，当然也可以赋值为具体名称

    def __repr__(self):
        return f"""{'+' * 56}
# {self.name} 配置信息如下：
+ 回测时间: {self.start_date} ~ {self.end_date}
+ 手续费: 合约{self.swap_c_rate * 100:.2f}%，现货{self.spot_c_rate * 100:.2f}%
+ 杠杆: {self.leverage:.2f}
+ 最小K线数量: {self.min_kline_num}
+ 维持保证金率: {self.margin_rate * 100:.2f}%
+ 拉黑名单: {self.black_list}，只交易名单: {self.white_list}
+ Rebalance 模式: {self.rebalance_mode}
+ 再择时: {self.timing}
{''.join([str(item) for item in self.strategy_list])}
{'+' * 56}
"""

    @property
    def section_params_dict(self):
        section_params_dict = {}
        for factor_name, param_list in self.factor_params_dict.items():
            if FactorHub.get_by_name(factor_name).is_cross:
                section_params_dict[factor_name] = param_list
        return section_params_dict

    @property
    def section_depend_factor_col_name_list(self):
        depend_factor_param_list = []
        for factor_name, param_list in self.factor_params_dict.items():
            factor = FactorHub.get_by_name(factor_name)
            if factor.is_cross:
                for param in param_list:
                    depend_factor_param_list.extend(factor.get_factor_list(param))
        return list(set(f'{f}_{n}' for f, n in set(depend_factor_param_list)))

    @property
    def has_section_factor(self):
        return any(FactorHub.get_by_name(factor_name).is_cross for factor_name in self.factor_params_dict.keys())

    @property
    def hold_period_type(self):
        return 'D' if self.is_day_period else 'H'

    def info(self):
        # 输出一下配置信息
        logger.debug(self)

    def get_fullname(self, as_folder_name=False):
        fullname_list = [self.name]
        for stg in self.strategy_list:
            fullname_list.append(f"{stg.get_fullname(as_folder_name)}")

        if self.timing:
            fullname_list.append(f'再择时:{self.timing}')

        fullname = ' '.join(fullname_list)
        return f'{self.name}' if as_folder_name else fullname

    def load_strategy_config(self, strategy_list: list | tuple, re_timing_config=None):
        self.strategy_list_raw = strategy_list
        # 所有策略中的权重
        all_cap_weight = sum(item["cap_weight"] for item in strategy_list)

        for index, stg_dict in enumerate(strategy_list):
            # 更新策略权重
            name = stg_dict['strategy']

            strategy = StrategyConfig.init(index, file=StrategyHub.get_by_name(name), **stg_dict)

            offset_list = list(filter(lambda x: x < strategy.period_num, strategy.offset_list))
            if len(offset_list) != len(strategy.offset_list):
                logger.warning(
                    f'策略{strategy.name}的offset_list设置有问题，自动裁剪。原始值：{strategy.offset_list},裁剪后：{offset_list}')
            strategy.offset_list = offset_list
            strategy.cap_weight = strategy.cap_weight / all_cap_weight

            if strategy.is_day_period:
                self.is_day_period = True
            else:
                self.is_hour_period = True

            # 缓存持仓周期的事情
            if strategy.hold_period not in self.hold_period_list:
                self.hold_period_list.append(strategy.hold_period)
                # 更新最大的持仓周期
                if pd.to_timedelta(self.max_hold_period) < pd.to_timedelta(strategy.hold_period):
                    self.max_hold_period = strategy.hold_period

            self.select_scope_set.add(strategy.select_scope)
            self.order_first_set.add(strategy.order_first)
            if not {'spot', 'mix'}.isdisjoint(self.select_scope_set) and self.leverage >= 2:
                logger.error(f'现货策略不支持杠杆大于等于2的情况，请重新配置')
                exit(1)

            if strategy.long_select_coin_num == 0 and (strategy.short_select_coin_num == 0 or
                                                       strategy.short_select_coin_num == 'long_nums'):
                logger.warning('策略中的选币数量都为0，忽略此策略配置')
                continue

            self.strategy_list.append(strategy)
            self.strategy_name_list.append(strategy.name)
            self.factor_col_name_list += strategy.factor_columns

            # 针对当前策略的因子信息，整理之后的列名信息，并且缓存到全局
            for factor_config in strategy.all_factors:
                # 添加到并行计算的缓存中
                if factor_config.name not in self.factor_params_dict:
                    self.factor_params_dict[factor_config.name] = set()
                self.factor_params_dict[factor_config.name].add(factor_config.param)

            if len(strategy.offset_list) > self.max_offset_len:
                self.max_offset_len = len(strategy.offset_list)

        self.factor_col_name_list = list(set(self.factor_col_name_list))

        if all((self.is_hour_period, self.is_day_period)):
            logger.critical(f'策略中同时存在小时线和日线的策略融合，请检查配置')
            exit()

        if re_timing_config:
            self.timing = TimingSignal(**re_timing_config)

    @classmethod
    def init_from_config(cls, load_strategy_list: bool = True) -> "BacktestConfig":
        import config

        backtest_config = cls(
            config.backtest_name,
            rebalance_mode=getattr(config, 'rebalance_mode', None),  # rebalance类型
            start_date=config.start_date,  # 回测开始时间
            end_date=config.end_date,  # 回测结束时间
            # ** 交易配置 **
            leverage=config.leverage,  # 杠杆
            black_list=[item.replace('-', '') for item in config.black_list],  # 拉黑名单
            white_list=[item.replace('-', '') for item in config.white_list],  # 只交易名单
            # ** 数据参数 **
            min_kline_num=config.min_kline_num,  # 最小K线数量，k线数量少于这个数字的部分不会计入计算
            reserved_cache=set(config.reserved_cache),  # 预留缓存文件类型，可以控制磁盘占用
            # ** 交易配置 **
            account_type=config.simulator_config['account_type'],  # 账户类型
            initial_usdt=config.simulator_config['initial_usdt'],  # 初始usdt
            margin_rate=config.simulator_config['margin_rate'],  # 维持保证金率
            swap_c_rate=config.simulator_config['swap_c_rate'],  # 合约买入手续费
            spot_c_rate=config.simulator_config['spot_c_rate'],  # 现货买卖手续费
            spot_min_order_limit=config.simulator_config['spot_min_order_limit'],  # 现货最小下单量
            swap_min_order_limit=config.simulator_config['swap_min_order_limit'],  # 合约最小下单量
            avg_price_col=config.simulator_config['avg_price_col'],  # 平均价格列名
            unified_time=config.simulator_config['unified_time'],  # 对齐 offset 和资金曲线的统一时间
        )

        # ** 策略配置 **
        # 初始化策略，默认都是需要初始化的
        if load_strategy_list and hasattr(config, 'strategy_list'):
            re_timing_config = getattr(config, 're_timing', None)  # 从config中读取选币再择时的策略配置
            backtest_config.load_strategy_config(config.strategy_list, re_timing_config)

        return backtest_config

    def set_report(self, report: pd.DataFrame):
        report['param'] = self.get_fullname()
        self.report = report

    def get_result_folder(self) -> Path:
        if self.iter_round == 0:
            parent_path = backtest_path / "".join(
                self.name.split("_参数")[:-1]) if self.is_param_search else backtest_path
            return get_folder_path(parent_path, self.get_fullname(as_folder_name=True), as_path_type=True)
        else:
            config_name = f'策略组_{self.iter_round}' if isinstance(self.iter_round, int) else self.iter_round
            if self.name.startswith(f'S{self.iter_round}'):
                config_name = self.name
            return get_folder_path(backtest_iter_path, backtest_name, config_name, as_path_type=True)

    def get_strategy_config_sheet(self, with_factors=True) -> dict:
        factor_dict = {}
        for stg in self.strategy_list:
            for attr_in in ['hold_period', 'market', 'offset_list', 'cap_weight']:
                if attr_in not in factor_dict:
                    factor_dict[attr_in] = []
                factor_dict[attr_in].append(getattr(stg, attr_in))

            for factor_config in stg.all_factors:
                _name = f'#FACTOR-{factor_config.name}'
                _val = factor_config.param
                if _name not in factor_dict:
                    factor_dict[_name] = []
                factor_dict[_name].append(_val)
        ret = {
            '策略': self.name,
            'fullname': self.get_fullname(),
        }
        if with_factors:
            ret.update(**{
                k: "_".join(map(str, v)) for k, v in factor_dict.items()
            })

        if self.timing:
            ret['再择时'] = str(self.timing)
        return ret

    def save(self):
        pd.to_pickle(self, self.get_result_folder() / 'config.pkl')

    def delete_cache(self):
        shutil.rmtree(self.get_result_folder())

    def is_reserved(self, item: str) -> bool:
        if 'all' in self.reserved_cache:
            return True
        return item in self.reserved_cache

    def get_final_equity_path(self):
        has_timing_signal = isinstance(self.timing, TimingSignal)
        if has_timing_signal:
            filename = '资金曲线_再择时.csv'
        else:
            filename = '资金曲线.csv'
        final_equity_path = self.get_result_folder() / filename
        return final_equity_path


class BacktestConfigFactory:
    """
    遍历参数的时候，动态生成配置
    """

    def __init__(self, **conf):
        # ====================================================================================================
        # ** 参数遍历配置 **
        # 可以指定因子遍历的参数范围
        # ====================================================================================================
        self.backtest_name = conf.get("backtest_name", backtest_name)

        if not self.backtest_name:
            self.backtest_name = f'默认策略-{datetime.now().strftime("%Y%m%dT%H%M%S")}'

        # 缓存全局配置
        self.select_scope_set: Set[str] = set()
        self.black_list = conf.get("black_list", set())

        # 存储生成好的config list和strategy list
        self.config_list: List[BacktestConfig] = []
        logger.debug(f'ℹ️ 名称：{self.backtest_name}')
        logger.debug(f'🗺️ 子策略结果：{self.result_folder}')

    @property
    def result_folder(self) -> Path:
        return get_folder_path(backtest_iter_path, self.backtest_name, as_path_type=True)

    def update_meta_by_config(self, config: BacktestConfig):
        """
        # 缓存是否使用现货等状态
        :param config: 生成的配置信息
        :return: None
        """
        self.select_scope_set = self.select_scope_set | config.select_scope_set
        self.black_list = self.black_list | set(config.black_list)

    def generate_all_factor_config(self):
        backtest_config = BacktestConfig.init_from_config(load_strategy_list=False)
        strategy_list = []
        for conf in self.config_list:
            strategy_list.extend(conf.strategy_list_raw)
        backtest_config.load_strategy_config(strategy_list)
        return backtest_config

    def get_name_params_sheet(self) -> pd.DataFrame:
        rows = []
        for config in self.config_list:
            rows.append(config.get_strategy_config_sheet())

        sheet = pd.DataFrame(rows)
        sheet.to_excel(self.config_list[-1].get_result_folder().parent / '策略回测参数总表.xlsx', index=False)
        return sheet

    def generate_configs_by_strategies(self, strategies, re_timing_strategies=None) -> List[BacktestConfig]:
        config_list = []
        iter_round = 0

        if not re_timing_strategies:
            re_timing_strategies = [None]

        for strategy_list, re_timing_config in product(strategies, re_timing_strategies):
            iter_round += 1
            backtest_config = BacktestConfig.init_from_config(load_strategy_list=False)
            if self.backtest_name:
                backtest_config.name = self.backtest_name
            backtest_config.load_strategy_config(strategy_list, re_timing_config)
            backtest_config.iter_round = iter_round

            self.update_meta_by_config(backtest_config)

            config_list.append(backtest_config)

        self.config_list = config_list

        return config_list

    def generate_configs_by_strategies_with_timing(self, strategies: List[dict]) -> List[BacktestConfig]:
        config_list = []
        iter_round = 0

        for strategy in strategies:
            iter_round += 1
            backtest_config = BacktestConfig.init_from_config(load_strategy_list=False)
            if 'name' in strategy:
                backtest_config.name = f"S{iter_round}-{strategy['name']}"
                backtest_config.original_name = strategy['name']
            else:
                backtest_config.name = f"S{iter_round}-{self.backtest_name}"
            # 再择时的功能是可选的，并不是所有的选币策略都要配套
            backtest_config.load_strategy_config(strategy['strategy_list'], strategy.get('re_timing', None))
            # 增加指定配置
            backtest_config.iter_round = iter_round

            self.update_meta_by_config(backtest_config)

            config_list.append(backtest_config)

        self.config_list = config_list

        return config_list


class MultiEquityBacktestConfig:
    import config as default_config  # 获取config
    pos_ratio_precision = 9  # 仓位比例的精度

    def __init__(
            self,
            name: str = default_config.backtest_name,
            strategy_config: dict = default_config.strategy_config,
            strategies: List[dict] = default_config.strategy_pool,
            leverage: float = default_config.leverage,
    ):
        # 初始化仓位管理策略
        self.strategy: PosStrategyConfig = PosStrategyConfig(**strategy_config)
        self.strategy.load()  # 加载对应的策略实现

        # 初始化策略工厂
        self.factory: BacktestConfigFactory = BacktestConfigFactory(backtest_name=name)
        self.factory.generate_configs_by_strategies_with_timing(strategies)
        self.strategy.strategy_cfg_list = self.factory.config_list

        # 因为后续我们需要ratio选币，所以要配置一下
        for conf in self.factory.config_list:
            conf.reserved_cache.add('ratio')

        # 运行过程中的中间变量们
        self.equity_dfs = []
        self.ratio_dfs = []
        self.start_time = None
        self.end_time = None
        self.leverage = leverage

        logger.debug(f'📅 回测周期: {self.default_config.start_date} -> {self.default_config.end_date}\n')

    @classmethod
    def duplicate_conf(cls, me_conf: 'MultiEquityBacktestConfig', name: str, strategy_config: dict):
        # 初始化仓位管理策略
        new_me_conf = cls(name=name, strategy_config=strategy_config, strategies=[])

        new_me_conf.strategy.strategy_cfg_list = me_conf.strategy.strategy_cfg_list
        new_me_conf.factory.backtest_name = name
        new_me_conf.factory.config_list = me_conf.factory.config_list

        new_me_conf.equity_dfs = me_conf.equity_dfs
        new_me_conf.ratio_dfs = me_conf.ratio_dfs
        new_me_conf.start_time = me_conf.start_time
        new_me_conf.end_time = me_conf.end_time

        return new_me_conf

    def transfer_equity_period(self, equity_df: pd.DataFrame, unified_time: str = '2017-01-01') -> pd.DataFrame:
        """
        把资金曲线的周期转换为策略的周期
        :param equity_df: 资金曲线
        :param unified_time: 对齐 offset 和资金曲线的统一时间
        :return: 合成了`open`, `high`, `low`, `close`的策略的资金曲线
        """
        # 填入统一日期数据，对齐回测与实盘
        equity_df.loc[equity_df.shape[0], 'candle_begin_time'] = pd.to_datetime(unified_time)

        resampled_df = equity_df.resample(self.strategy.hold_period, on='candle_begin_time').agg({
            'equity': ['first', 'max', 'min', 'last']
        })
        resampled_df.columns = ['open', 'high', 'low', 'close']

        # 清理掉数据对齐填入的空值数据
        resampled_df.dropna(subset=resampled_df.columns, how='any', inplace=True)
        # 清理掉对齐数据填入的日期数据
        equity_df = equity_df[equity_df['candle_begin_time'] > pd.to_datetime(unified_time)]

        if resampled_df.index.min() < equity_df['candle_begin_time'].min():
            resampled_df = resampled_df.iloc[1:]  # 丢弃掉第一个不满周期的数据

        # 去掉最后一个不满周期的数据
        if (
                equity_df['candle_begin_time'].max() - resampled_df.index.max() + pd.to_timedelta('1H')
                != pd.to_timedelta(self.strategy.hold_period)
        ):
            resampled_df = resampled_df.iloc[:-1]

        return resampled_df.reset_index(inplace=False, drop=False)

    def process_equities(self):
        equity_dfs = []
        ratio_dfs = []
        configs = self.factory.config_list

        for conf in configs:
            # ====处理资金曲线
            equity_path = conf.get_final_equity_path()
            equity_df = pd.read_csv(equity_path, parse_dates=['candle_begin_time'], index_col=0)

            self.start_time = equity_df['candle_begin_time'].min()
            self.end_time = equity_df['candle_begin_time'].max()

            # 1. 进行周期转换
            equity_df = self.transfer_equity_period(equity_df, unified_time=conf.unified_time)
            # 2. 添加因子(如有需要计算)
            factor_cols = {}
            for factor in self.strategy.factor_list:
                factor_cols.update(calc_factor_vals(equity_df, factor.name, [factor.param]))

            # 3. 添加回测结果并去掉空值，特别提示，这边会造成equity长度缺失，后续会补充0
            # 如果因为爆仓等原因，因子数值计算为nan，默认采用ffill的方式填充
            equity_df = pd.DataFrame({
                'candle_begin_time': equity_df['candle_begin_time'],
                'open': equity_df['open'].values,
                'high': equity_df['high'].values,
                'low': equity_df['low'].values,
                'close': equity_df['close'].values,
                **factor_cols
            }).ffill().dropna(subset=self.strategy.factor_columns, how='any')
            equity_dfs.append(equity_df)

            # ====处理选币仓位结果
            spot_path = conf.get_result_folder() / 'df_spot_ratio.pkl'
            swap_path = conf.get_result_folder() / 'df_swap_ratio.pkl'
            ratio_dfs.append((pd.read_pickle(spot_path), pd.read_pickle(swap_path)))

            # 保存到本地
            equity_df.to_pickle(get_file_path(self.factory.result_folder, conf.name, 'equity_df.pkl'))

        # 需要对其所有资金曲线数据的长度
        for idx, df in enumerate(equity_dfs):
            equity_dfs[idx] = df[
                (df['candle_begin_time'] <= self.end_time) & (df['candle_begin_time'] >= self.start_time)]

        self.equity_dfs = equity_dfs
        self.ratio_dfs = ratio_dfs

    def smooth_single_ratio(self, df_ratio):
        """
        Smoothly rebalance a single column of position signals, ensuring stepwise adjustment.

        Args:
        - df (pd.DataFrame): Original position signals with index as candle_begin_time (1 column only).
        - rebalance_cap_step (float): Maximum fraction of total adjustment per step.

        Returns:
        - pd.DataFrame: DataFrame of adjusted rebalance positions.
        """
        rebalance_cap_step = self.strategy.rebalance_cap_step
        if rebalance_cap_step > 0.9999:
            return df_ratio

        rebalance_df = df_ratio.astype(float)  # Copy the original DataFrame
        rebalance_df.iloc[0] = df_ratio.iloc[0]  # Initialize with the first row

        for i in range(1, len(df_ratio)):
            prev_signal = rebalance_df.iloc[i - 1, 0].astype(float)  # Previous adjusted signal
            target_signal = df_ratio.iloc[i, 0].astype(float)  # Target signal for the current row

            # Compute the adjustment needed
            difference = target_signal - prev_signal
            adjustment = max(min(difference, rebalance_cap_step), -rebalance_cap_step)

            # Apply the adjustment
            rebalance_df.iloc[i, 0] = prev_signal + adjustment

        return rebalance_df.round(self.pos_ratio_precision)

    def smooth_ratios(self, df_ratio):
        """
        Generate rebalance positions allowing negatives only if target_signal is negative,
        while keeping total increase and decrease equal to rebalance_cap_step.

        Args:
        - df (pd.DataFrame): Original position signals with index as candle_begin_time.
        - rebalance_cap_step (float): Fixed step for total increase/decrease in positions.

        Returns:
        - pd.DataFrame: DataFrame of adjusted rebalance positions.
        """
        rebalance_cap_step = self.strategy.rebalance_cap_step
        if rebalance_cap_step > 0.9999:
            return df_ratio

        rebalance_df = df_ratio.astype(float)  # Copy the original DataFrame
        rebalance_df.iloc[0] = df_ratio.iloc[0]  # First row matches the initial signal

        for i in range(1, len(df_ratio)):
            prev_signal = rebalance_df.iloc[i - 1]  # Previous adjusted signal
            target_signal = df_ratio.iloc[i]  # Target signal for the current row

            # Difference between target and current signal
            difference = target_signal - prev_signal

            # Allowable increase/decrease ranges
            increase_indices = (difference > 0)
            decrease_indices = (difference < 0)

            # Non-negative constraint: For columns where target_signal >= 0, ensure result >= 0
            non_negative_indices = (target_signal >= 0)
            allowable_decrease = prev_signal[non_negative_indices].clip(lower=0)

            # Calculate total increase and decrease
            total_increase = min(difference[increase_indices].sum(), rebalance_cap_step)
            total_decrease = min(-difference[decrease_indices].sum(), rebalance_cap_step)

            # Scale increases and decreases to meet rebalance_cap_step
            if total_increase > 0:
                scaled_increase = difference[increase_indices] * (total_increase / difference[increase_indices].sum())
                pass
            else:
                scaled_increase = pd.Series(0, index=difference.index)

            if total_decrease > 0:
                scaled_decrease = difference[decrease_indices] * (total_decrease / -difference[decrease_indices].sum())
            else:
                scaled_decrease = pd.Series(0, index=difference.index)

            # Apply non-negative constraints
            if non_negative_indices.any():
                scaled_decrease[non_negative_indices] = scaled_decrease[non_negative_indices].clip(
                    lower=-allowable_decrease
                )

            # Apply adjustments
            adjustment = pd.Series(0, index=prev_signal.index)
            adjustment[increase_indices] = scaled_increase
            adjustment[decrease_indices] = scaled_decrease

            # Update rebalance signal
            rebalance_df.iloc[i] = prev_signal + adjustment

            # Normalize to ensure the row sums to 1
            # rebalance_df.iloc[i] = rebalance_df.iloc[i] / rebalance_df.iloc[i].sum()

        return rebalance_df.round(self.pos_ratio_precision)

    def calc_ratios(self):
        logger.info(f'开始使用仓位管理策略，计算选币仓位...')
        # 计算选币仓位，这里是按照持仓周期resample之后的index
        ratios = self.strategy.calc_ratios(self.equity_dfs)

        # **特别说明**，
        # 在仓位管理的hold period不等于1H的时候，我们需要额外做转换处理
        # ratios的结构是：
        # ----------------------------------------
        #                      0    1
        # candle_begin_time
        # 2021-01-01 00:00:00  1.0  0.0
        # 2021-01-01 06:00:00  1.0  0.0
        # 2021-01-01 12:00:00  1.0  0.0
        # 2021-01-01 18:00:00  1.0  0.0
        # 2021-01-02 00:00:00  1.0  0.0
        # ...                  ...  ...
        # 2024-07-23 06:00:00  0.0  1.0
        # 2024-07-23 12:00:00  0.0  1.0
        # 2024-07-23 18:00:00  0.0  1.0
        # ---------------------------------------
        # 但是resample之后的资金曲线，是通过`收盘后的equity`来计算的，也就是每个周期的 'close'，
        # candle_begin_time == '2021-01-01 00:00:00' 的选币仓位是给那个周期最后一个1H来使用的。
        # 上述案例中，持仓周期为6H
        # - 00:00:00 ~ 00:04:00: 没有选币仓位
        # - 00:05:00 ~ 00:10:00: 使用candle_begin_time == '2021-01-01 00:00:00' 的选币仓位
        # - 00:11:00 ~ 00:16:00: 使用candle_begin_time == '2021-01-01 06:00:00' 的选币仓位
        # - 00:17:00 ~ 00:22:00: 使用candle_begin_time == '2021-01-01 12:00:00' 的选币仓位
        # - ...
        # 所以，我们需要把时间label进行调整，并且forward fill

        # 1H的场景下完全正常
        ratios.loc[ratios.index.max() + pd.to_timedelta(self.strategy.hold_period)] = None
        ratios = ratios.shift().fillna(0)  # 把所有动态仓位赋值给下一个周期，并且空出第一个周期

        # 重新自动填充为1H的仓位ratio
        candle_begin_times = pd.date_range(self.start_time, self.end_time, freq='H', inclusive='both')
        df_ratio = ratios.reindex(candle_begin_times, method='ffill')
        df_ratio.fillna(0, inplace=True)

        # 补全数据之后，向上移动1H，并且ffill
        df_ratio = df_ratio.shift(-1).dropna(how='all')

        # 叠加一下再择时的杠杆（如有）
        for idx, conf in enumerate(self.factory.config_list):
            leverage_path = conf.get_result_folder() / '再择时动态杠杆.csv'
            if leverage_path.exists():
                logger.debug(f'⌛️ 加载`{conf.name}`再择时动态杠杆: {leverage_path}')
                leverages = pd.read_csv(leverage_path, index_col='candle_begin_time', encoding='utf-8-sig',
                                        parse_dates=['candle_begin_time'])
                leverages = leverages[leverages.index >= df_ratio.index.min()]
                df_ratio[idx] = df_ratio[idx].mul(leverages['动态杠杆'].astype(float), axis=0)

        df_ratio.to_csv(self.factory.result_folder / '仓位比例-原始.csv')
        # 根据单次换仓限制，平滑换仓比例
        if len(df_ratio.columns) == 1:
            df_ratio = self.smooth_single_ratio(df_ratio)
        else:
            df_ratio = self.smooth_ratios(df_ratio)
        df_ratio.to_csv(self.factory.result_folder / '仓位比例.csv')
        return df_ratio

    def agg_pos_ratio(self, pos_ratio) -> (pd.DataFrame, pd.DataFrame):
        df_spot_ratio_sum = pd.DataFrame()
        df_swap_ratio_sum = pd.DataFrame()
        for idx, (df_spot_ratio, df_swap_ratio) in enumerate(self.ratio_dfs):
            # 获取仓位管理ratio
            group_ratio = pos_ratio[idx]
            # 裁切对应的资金权重
            spot_ratio = df_spot_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)
            swap_ratio = df_swap_ratio.loc[pos_ratio.index, :].mul(group_ratio, axis=0)
            # 累加
            df_spot_ratio_sum = df_spot_ratio_sum.add(spot_ratio, fill_value=0)
            df_swap_ratio_sum = df_swap_ratio_sum.add(swap_ratio, fill_value=0)

        return df_spot_ratio_sum, df_swap_ratio_sum

    def backtest_strategies(self):
        from core.backtest import run_backtest_multi
        logger.debug(f'🗄️ 仓位管理策略: {self}')
        return run_backtest_multi(self.factory)

    def load_fill_strategy_select_results(self, strategy_name):
        """
        加载填充策略的选币结果
        """
        logger.debug(f"尝试加载填充策略 '{strategy_name}' 的选币结果")

        # 根据策略配置加载对应的选币结果
        # 这里需要找到对应策略在me_conf.factory.config_list中的位置
        for conf in self.factory.config_list:
            # 更精确的策略名称匹配
            if conf.original_name == strategy_name:
                spot_path = conf.get_result_folder() / 'df_spot_ratio.pkl'
                swap_path = conf.get_result_folder() / 'df_swap_ratio.pkl'

                df_fill_spot = pd.DataFrame()
                df_fill_swap = pd.DataFrame()
                if spot_path.exists():
                    df_fill_spot = pd.read_pickle(spot_path)
                if swap_path.exists():
                    df_fill_swap = pd.read_pickle(swap_path)

                return df_fill_spot, df_fill_swap

        logger.warning(f"未找到策略 '{strategy_name}' 的选币结果文件")
        return pd.DataFrame(), pd.DataFrame()

    def apply_position_limits(self, df_spot_ratio, df_swap_ratio):
        """
        应用仓位限制，将超过限制的部分分配给填充策略
        """
        symbol_ratio_limit = self.strategy.symbol_ratio_limit
        if not symbol_ratio_limit:
            logger.info("未配置pos_limit，跳过仓位限制处理")
            return df_spot_ratio, df_swap_ratio

        logger.info("开始应用仓位限制...")

        # 处理多头超限
        if 'long' in symbol_ratio_limit:
            long_config = symbol_ratio_limit['long']
            rate_limit = long_config['rate_limit'] / self.leverage
            fill_strategy = long_config['fill_strategy']

            logger.info(f"处理多头仓位限制: rate_limit={rate_limit}, fill_strategy={fill_strategy}")

            # 加载填充策略的选币结果
            df_fill_spot, df_fill_swap = self.load_fill_strategy_select_results(fill_strategy)

            # 🔥 不管填充策略是否为空，都要执行权重限制
            logger.info("处理现货多头超限权重")
            df_spot_ratio = self.redistribute_excess_weight(
                df_spot_ratio, df_fill_spot, rate_limit, direction='long'
            )

            logger.info("处理合约多头超限权重")
            df_swap_ratio = self.redistribute_excess_weight(
                df_swap_ratio, df_fill_swap, rate_limit, direction='long'
            )

        # 处理空头超限
        if 'short' in symbol_ratio_limit:
            short_config = symbol_ratio_limit['short']
            rate_limit = short_config['rate_limit'] / self.leverage
            fill_strategy = short_config['fill_strategy']

            logger.info(f"处理空头仓位限制: rate_limit={rate_limit}, fill_strategy={fill_strategy}")

            # 加载填充策略的选币结果
            df_fill_spot, df_fill_swap = self.load_fill_strategy_select_results(fill_strategy)

            # 🔥 不管填充策略是否为空，都要执行权重限制
            logger.info("处理合约空头超限权重")
            df_swap_ratio = self.redistribute_excess_weight(
                df_swap_ratio, df_fill_swap, rate_limit, direction='short'
            )

        logger.info("仓位限制处理完成")

        return df_spot_ratio, df_swap_ratio

    @staticmethod
    def redistribute_excess_weight(df_ratio, df_fill_ratio, rate_limit, direction='long'):
        """
        重新分配超过限制的权重 - 向量化版本
        按照填充策略中每个币种的权重比例分配超出权重
        """
        logger.debug(f"开始重新分配 {direction} 方向的超限权重，限制值: {rate_limit}")

        # 对齐两个DataFrame的索引
        common_times = df_ratio.index.intersection(df_fill_ratio.index)

        if len(common_times) == 0:
            logger.warning("填充策略没有数据或无共同时间索引，仅执行权重截断，不进行重分配")
            # 🔥 即使无法重分配，也要截断超限权重
            df_result = df_ratio.copy()
            if direction == 'long':
                # 截断多头超限权重
                exceed_mask = df_result > rate_limit
                df_result = df_result.where(~exceed_mask, rate_limit)
            else:
                # 截断空头超限权重  
                exceed_mask = df_result < -rate_limit
                df_result = df_result.where(~exceed_mask, -rate_limit)

            return df_result

        # 裁切到共同时间范围
        df_ratio_aligned = df_ratio.loc[common_times].copy()
        df_fill_aligned = df_fill_ratio.loc[common_times].copy()

        # 对齐列索引，确保所有可能需要的币种都在结果DataFrame中
        all_symbols = set(df_ratio_aligned.columns) | set(df_fill_aligned.columns)

        # 扩展df_ratio_aligned，添加缺失的列
        for symbol in all_symbols:
            if symbol not in df_ratio_aligned.columns:
                df_ratio_aligned[symbol] = 0.0
            if symbol not in df_fill_aligned.columns:
                df_fill_aligned[symbol] = 0.0

        # 重新排序列以保持一致性
        df_ratio_aligned = df_ratio_aligned.reindex(columns=sorted(all_symbols), fill_value=0.0)
        df_fill_aligned = df_fill_aligned.reindex(columns=sorted(all_symbols), fill_value=0.0)

        # 保存原始数据，用于后续检查
        df_ratio_original = df_ratio_aligned.copy()

        # 步骤1: 向量化检测超限权重
        if direction == 'long':
            exceed_mask = df_ratio_aligned > rate_limit
            # 计算超出的权重：只有超限的位置才计算超出值，其他位置为0
            excess_weights = (df_ratio_aligned - rate_limit).where(exceed_mask, 0)
            # 将超出部分设置为限制值
            df_ratio_aligned = df_ratio_aligned.where(~exceed_mask, rate_limit)
            # 找到填充策略的有效币种（权重>0）
            fill_mask = df_fill_aligned > 1e-8
        else:
            exceed_mask = df_ratio_aligned < -rate_limit
            # 计算超出的权重：保持负数符号，只有超限的位置才计算超出值
            excess_weights = (df_ratio_aligned + rate_limit).where(exceed_mask, 0)  # 这里保持负数
            # 将超出部分设置为限制值
            df_ratio_aligned = df_ratio_aligned.where(~exceed_mask, -rate_limit)
            # 找到填充策略的有效币种（权重<0）
            fill_mask = df_fill_aligned < -1e-8

        # 步骤2: 计算每行的超出权重总和
        excess_totals = excess_weights.sum(axis=1)

        # 步骤3: 计算每行填充策略的有效权重总和（用于比例分配）
        # 只考虑有效币种的权重
        # if direction == 'long':
        #     # 多头：只计算权重>0的币种的权重总和
        #     fill_weights_total = (df_fill_aligned * fill_mask).sum(axis=1)
        # else:
        #     # 空头：只计算权重<0的币种的权重总和（取绝对值）
        #     fill_weights_total = (df_fill_aligned.abs() * fill_mask).sum(axis=1)

        # 权重按照 1 来处理，原始策略权重分配
        fill_weights_total = pd.Series(1., index=df_ratio_aligned.index)

        # 步骤4: 向量化分配超出权重
        # 过滤掉没有超出权重或没有可用填充币种的行
        valid_rows = (excess_totals.abs() > 1e-8) & (fill_weights_total > 1e-8)

        if not valid_rows.any():
            logger.info(f"{direction} 方向没有发现超限权重，无需重分配")
            # 返回原始索引范围的数据
            return df_ratio_aligned.reindex(df_ratio.index, fill_value=0.0)[df_ratio.columns]

        redistribution_count = valid_rows.sum()
        total_redistributed = excess_totals[valid_rows].abs().sum()

        # 步骤5: 按比例分配权重
        # 只对有效行进行处理
        valid_fill_aligned = df_fill_aligned.loc[valid_rows]
        valid_fill_mask = fill_mask.loc[valid_rows]
        valid_excess_totals = excess_totals.loc[valid_rows]
        valid_fill_weights_total = fill_weights_total.loc[valid_rows]

        # 计算每个币种的权重比例
        if direction == 'long':
            # 多头：直接使用权重计算比例
            fill_weights_for_ratio = valid_fill_aligned * valid_fill_mask
        else:
            # 空头：使用绝对值计算比例
            fill_weights_for_ratio = valid_fill_aligned.abs() * valid_fill_mask

        # 计算权重比例 (每个币种权重 / 该行总权重)
        weight_ratios = fill_weights_for_ratio.div(valid_fill_weights_total.replace(0, 1), axis=0)

        # 计算每个币种应该分配的权重 (超出总权重 * 该币种比例)
        allocation_matrix = weight_ratios.multiply(valid_excess_totals, axis=0)

        # 确保只有有效币种才分配权重
        allocation_matrix = allocation_matrix * valid_fill_mask

        # 直接应用分配矩阵，不需要额外的符号处理
        df_ratio_aligned.loc[valid_rows] += allocation_matrix

        # 验证：确保空头分配后权重仍为负数或零
        if direction == 'short':
            positive_mask = (df_ratio_aligned.loc[valid_rows] > 1e-8) & (df_ratio_original.loc[valid_rows] <= 0)
            if positive_mask.any().any():
                positive_count = positive_mask.sum().sum()
                logger.warning(f"检测到 {positive_count} 个空头权重分配后变为正数，可能存在逻辑错误")
                logger.debug(f"有问题的权重: {df_ratio_aligned.loc[valid_rows][positive_mask].stack().head()}")

        logger.info(
            f"{direction} 方向权重重分配完成: 处理了 {redistribution_count} 个时间点，总共重分配权重 {total_redistributed:.6f}，按填充策略权重比例分配")

        # 返回原始索引范围的数据，只保留原始列
        result = df_ratio_aligned.reindex(df_ratio.index, fill_value=0.0)

        # 只返回原始DataFrame中存在的列，新列保留
        original_columns = df_ratio.columns
        new_columns = [col for col in result.columns if col not in original_columns]

        if new_columns:
            logger.info(f"添加了新的币种列: {new_columns}")

        # 清理数据：确保数据类型一致
        result = result.fillna(0.0).astype(float)

        return result

    def __repr__(self):
        return self.factory.backtest_name + ' ' + str(self.strategy)
