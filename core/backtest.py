"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import logging
import shutil
import time

import pandas as pd

from config import job_num, raw_data_path, backtest_path
from core.equity import calc_equity, show_plot_performance
from core.model.backtest_config import BacktestConfig
from core.model.backtest_config import BacktestConfigFactory
from core.model.timing_signal import TimingSignal
from core.select_coin import calc_factors, select_coins, concat_select_results, process_select_results, \
    agg_multi_strategy_ratio, calc_cross_sections
from core.utils.functions import load_spot_and_swap_data, save_performance_df_csv
from core.utils.log_kit import logger, divider


def step2_load_data(conf: BacktestConfig):
    """
    读取回测所需数据，并做简单的预处理
    :param conf:
    :return:
    """
    logger.info(f'读取数据中心数据...')
    s_time = time.time()

    # 读取数据
    # 针对现货策略和非现货策略读取的逻辑完全不同。
    # - 如果是纯合约模式，只需要读入 swap 数据并且合并即可
    # - 如果是现货模式，需要读入 spot 和 swap 数据并且合并，然后添加 tag
    load_spot_and_swap_data(conf)  # 串行方式，完全等价
    logger.ok(f'完成读取数据中心数据，花费时间：{time.time() - s_time:.2f}秒')


def step3_calc_factors(conf: BacktestConfig):
    """
    计算因子
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'时序因子计算...')
    calc_factors(conf)
    logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.2f}秒')

    # 计算截面因子
    logger.info('截面因子计算...')
    s_time = time.time()
    calc_cross_sections(conf)
    logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.2f}秒')


def step4_select_coins(conf: BacktestConfig):
    """
    选币
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'选币...')
    select_coins(conf)  # 选币
    logger.ok(f'完成选币，花费时间：{time.time() - s_time:.3f}秒')


def step5_aggregate_select_results(conf: BacktestConfig, save_final_result=False):
    logger.info(f'整理{conf.name}选币结果...')
    # 整理选币结果
    concat_select_results(conf)  # 合并多个策略的选币结果
    select_results = process_select_results(conf)  # 生成整理后的选币结果
    logger.debug(f'💾 {conf.name}选币结果df大小：'
                 f'{select_results.memory_usage(deep=True).sum() / 1024 / 1024 / 1024:.4f} G')
    if save_final_result:
        # 存储最终的选币结果
        select_results.to_pickle(backtest_path / 'final_select_results.pkl')

    # 聚合大杂烩中多策略的权重，以及多offset选币的权重聚合
    s_time = time.time()
    logger.debug(f'🔃 开始{conf.name}权重聚合...')
    df_spot_ratio, df_swap_ratio = agg_multi_strategy_ratio(conf, select_results)

    # 始终输出ratios
    df_spot_ratio.to_pickle(conf.get_result_folder() / 'df_spot_ratio.pkl')
    df_swap_ratio.to_pickle(conf.get_result_folder() / 'df_swap_ratio.pkl')

    logger.ok(f'完成{conf.name}权重聚合，花费时间： {time.time() - s_time:.3f}秒')
    return df_spot_ratio, df_swap_ratio


def step6_simulate_performance(conf: BacktestConfig, df_spot_ratio, df_swap_ratio, pivot_dict_spot, pivot_dict_swap,
                               if_show_plot=False, extra_equities=None, description=None):
    logger.info(f'{conf.name} 开始模拟交易...')
    if extra_equities is None:
        extra_equities = {}
    logger.debug(f'📅 [{conf.name}] 模拟交易，回溯 {len(df_spot_ratio):,} 小时（~{len(df_spot_ratio) / 24:,.0f}天）...')
    account_df, rtn, year_return, month_return, quarter_return = calc_equity(
        conf, pivot_dict_spot, pivot_dict_swap,
        df_spot_ratio, df_swap_ratio
    )
    save_performance_df_csv(conf,
                            资金曲线=account_df,
                            策略评价=rtn,
                            年度账户收益=year_return,
                            季度账户收益=quarter_return,
                            月度账户收益=month_return)

    has_timing_signal = isinstance(conf.timing, TimingSignal)
    prefix = ''

    if has_timing_signal:
        account_df, rtn, year_return = simu_timing(conf, df_spot_ratio, df_swap_ratio, pivot_dict_spot,
                                                   pivot_dict_swap)
        prefix = '再择时: '

    if if_show_plot:
        show_plot_performance(conf, account_df, rtn, year_return, prefix, description=description, **extra_equities)

    return conf.report


def simu_timing(conf: BacktestConfig, df_spot_ratio, df_swap_ratio, pivot_dict_spot, pivot_dict_swap):
    s_time = time.time()
    logger.info(f'{conf.get_fullname(as_folder_name=True)} 资金曲线择时，生成动态杠杆')

    account_df = pd.read_csv(conf.get_result_folder() / '资金曲线.csv', index_col=0, encoding='utf-8-sig')

    # 检查哪个动态杠杆方法有实现，优先使用 get_dynamic_leverage_for_dataframe
    if conf.timing.impl_flags.get('dynamic_leverage_for_dataframe', False):
        leverages = conf.timing.get_dynamic_leverage_for_dataframe(account_df)
    elif conf.timing.impl_flags.get('dynamic_leverage', False):
        leverages = conf.timing.get_dynamic_leverage(account_df['equity'])
    else:
        raise NotImplementedError(f'择时信号 {conf.timing.name} 必须实现 dynamic_leverage 或 dynamic_leverage_for_dataframe 方法之一')

    account_df, rtn, year_return, month_return, quarter_return = calc_equity(
        conf, pivot_dict_spot, pivot_dict_swap, df_spot_ratio, df_swap_ratio, leverages * conf.leverage
    )
    save_performance_df_csv(
        conf,
        资金曲线_再择时=account_df,
        策略评价_再择时=rtn,
        年度账户收益_再择时=year_return,
        季度账户收益_再择时=quarter_return,
        月度账户收益_再择时=month_return,
        再择时动态杠杆=pd.DataFrame({
            'candle_begin_time': account_df['candle_begin_time'],
            '动态杠杆': leverages
        })
    )
    logger.debug(f'⏰ 完成再择时模拟计算，已花费时间{time.time() - s_time:.3f}秒')

    return account_df, rtn, year_return


def load_pivot_data(base_path, market_type):
    """从 Parquet 加载 Pivot 数据 (V2 优化)"""
    res = {}
    prefix = f'market_pivot_{market_type}'
    import polars as pl
    for pq_file in base_path.glob(f"{prefix}_*.parquet"):
        key = pq_file.stem.replace(f"{prefix}_", "")
        # V2 修正: 使用 Polars 加载后再转 Pandas，并恢复 DatetimeIndex
        df = pl.read_parquet(pq_file).to_pandas()
        if 'candle_begin_time' in df.columns:
            df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'])
            df.set_index('candle_begin_time', inplace=True)
        res[key] = df
    return res


def simu_performance_on_select(conf: BacktestConfig, silent=True, pivot_dict_spot=None, pivot_dict_swap=None):
    import logging
    if silent:
        logger.setLevel(logging.WARNING)  # 可以减少中间输出的log
    # ====================================================================================================
    # 5. 整理大杂烩选币结果
    # - 把大杂烩中每一个策略的选币结果聚合成一个df
    # ====================================================================================================
    df_spot_ratio, df_swap_ratio = step5_aggregate_select_results(conf)

    # V2 优化: 优先使用传入的 Pivot 数据，减少磁盘 I/O
    if pivot_dict_spot is None:
        pivot_dict_spot = load_pivot_data(raw_data_path, 'spot')
        if not pivot_dict_spot:
            pivot_dict_spot = pd.read_pickle(raw_data_path / 'market_pivot_spot.pkl')
    
    if pivot_dict_swap is None:
        pivot_dict_swap = load_pivot_data(raw_data_path, 'swap')
        if not pivot_dict_swap:
            pivot_dict_swap = pd.read_pickle(raw_data_path / 'market_pivot_swap.pkl')

    res = step6_simulate_performance(conf, df_spot_ratio, df_swap_ratio, pivot_dict_spot, pivot_dict_swap)
    logger.setLevel(logging.DEBUG)  # 中间结果恢复一下
    return res


# ====================================================================================================
# ** 回测主程序 **
# 1. 准备工作
# 2. 读取数据
# 3. 计算因子
# 4. 选币
# 5. 整理选币数据
# 6. 添加下一个每一个周期需要卖出的币的信息
# 7. 计算资金曲线
# ====================================================================================================
def run_backtest(conf: BacktestConfig):
    # ====================================================================================================
    # 1. 准备工作
    # ====================================================================================================
    divider(conf.name, '*')

    # 删除缓存
    conf.delete_cache()

    # 记录一下时间戳
    r_time = time.time()

    # 缓存当前的config
    conf.save()

    # ====================================================================================================
    # 2. 读取回测所需数据，并做简单的预处理
    # ====================================================================================================
    step2_load_data(conf)

    # ====================================================================================================
    # 3. 计算因子
    # ====================================================================================================
    step3_calc_factors(conf)

    # ====================================================================================================
    # 4. 选币
    # - 注意：选完之后，每一个策略的选币结果会被保存到硬盘
    # ====================================================================================================
    step4_select_coins(conf)

    # ====================================================================================================
    # 5. 整理选币结果并形成目标持仓
    # ====================================================================================================
    df_spot_ratio, df_swap_ratio = step5_aggregate_select_results(conf)
    logger.ok(f'目标持仓信号已完成，花费时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 6. 根据目标持仓计算资金曲线
    # ====================================================================================================
    pivot_dict_spot = load_pivot_data(raw_data_path, 'spot')
    if not pivot_dict_spot:
        pivot_dict_spot = pd.read_pickle(raw_data_path / 'market_pivot_spot.pkl')
        
    pivot_dict_swap = load_pivot_data(raw_data_path, 'swap')
    if not pivot_dict_swap:
        pivot_dict_swap = pd.read_pickle(raw_data_path / 'market_pivot_swap.pkl')

    step6_simulate_performance(conf, df_spot_ratio, df_swap_ratio, pivot_dict_spot, pivot_dict_swap, if_show_plot=True)
    logger.ok(f'完成，回测时间：{time.time() - r_time:.3f}秒')


def run_backtest_multi(factory: BacktestConfigFactory):
    # ====================================================================================================
    # 1. 准备工作
    # ====================================================================================================
    iter_results_folder = factory.result_folder

    # 删除缓存
    shutil.rmtree(iter_results_folder, ignore_errors=True)
    iter_results_folder.mkdir(parents=True, exist_ok=True)

    conf_list = factory.config_list
    for index, conf in enumerate(conf_list):
        logger.debug(f'ℹ️ 策略{index + 1}｜共{len(conf_list)}个')
        logger.debug(f'{conf.get_fullname()}')
        conf.save()
    logger.ok('策略池中需要回测的策略数：{}'.format(len(conf_list)))

    # 记录一下时间戳
    all_start_time = time.time()
    r_time = all_start_time

    # ====================================================================================================
    # 2. 读取回测所需数据，并做简单的预处理
    # ====================================================================================================
    divider('读取数据', sep='-')
    s_time = time.time()
    conf_all = factory.generate_all_factor_config()

    # 读取数据
    # 针对现货策略和非现货策略读取的逻辑完全不同。
    # - 如果是纯合约模式，只需要读入 swap 数据并且合并即可
    # - 如果是现货模式，需要读入 spot 和 swap 数据并且合并，然后添加 tag
    load_spot_and_swap_data(conf_all)  # 串行方式，完全等价
    logger.ok(f'完成读取数据中心数据，花费时间：{time.time() - s_time:.3f}秒')

    # ====================================================================================================
    # 3. 计算因子
    # ====================================================================================================
    divider('时序因子计算', sep='-')
    s_time = time.time()
    calc_factors(conf_all)  # 执行时序因子计算
    logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # 计算截面因子
    s_time = time.time()
    divider(f'截面因子计算', sep='-')
    calc_cross_sections(conf_all)  # 执行截面因子计算
    logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 4. 选币
    # ====================================================================================================
    divider('选币', sep='-')
    s_time = time.time()
    
    # [V2 - L6 极致优化] 统一因子数据大合并 (Master Data Assembly)
    # 在这里一次性把所有 shard 和截面因子的 Parquet 全加载进来并 Join 好
    # 这样子策略在选币时就不需要再做任何硬盘 IO 或 Join，速度将提升一个数量级
    logger.debug("💿 正在构建全局 Master Factor 数据集 (Zero-Wait Selection)...")
    from core.select_coin import ALL_KLINE_PATH_TUPLE
    from core.utils.path_kit import get_file_path
    import polars as pl
    
    all_kline_pq = get_file_path(*ALL_KLINE_PATH_TUPLE, as_path_type=True).with_suffix('.parquet')
    if all_kline_pq.exists():
        master_pl = pl.read_parquet(all_kline_pq)
        cache_dir = get_file_path('data', 'cache', as_path_type=True)
        
        # 1. 合并所有时序因子分片
        for shard_file in cache_dir.glob('factors_shard_*.parquet'):
            f_df = pl.read_parquet(shard_file)
            # 只取主集中不存在的因子列进行 Join
            cols = [c for c in f_df.columns if c not in master_pl.columns and c not in ['candle_begin_time', 'symbol', 'is_spot']]
            if cols:
                master_pl = master_pl.join(f_df.select(['candle_begin_time', 'symbol', 'is_spot'] + cols), 
                                         on=['candle_begin_time', 'symbol', 'is_spot'], how='left')
        
        # 2. 合并所有独立截面因子
        for factor_pq in cache_dir.glob('factor_*.parquet'):
            f_df = pl.read_parquet(factor_pq)
            cols = [c for c in f_df.columns if c not in master_pl.columns and c not in ['candle_begin_time', 'symbol', 'is_spot']]
            if cols:
                master_pl = master_pl.join(f_df.select(['candle_begin_time', 'symbol', 'is_spot'] + cols), 
                                         on=['candle_begin_time', 'symbol', 'is_spot'], how='left')
        
        # 最终转回 Pandas 交付给选币引擎
        master_shared_df = master_pl.to_pandas()
        del master_pl
    else:
        master_shared_df = None

    # [V2 - L4 优化] 并行化多策略选币
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(len(factory.config_list), job_num)) as executor:
        f_list = [executor.submit(select_coins, conf, True, master_shared_df) for conf in factory.config_list]
        for _ in as_completed(f_list):
            pass

    logger.ok(f'完成选币，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 5. 针对选币结果进行聚合
    # ====================================================================================================
    divider('子策略模拟', sep='-')
    logger.setLevel(logging.DEBUG)
    logger.debug(f'注意：主要和选币数量有关...')
    s_time = time.time()
    
    # V2 优化: 预先加载并按时间对齐 Pivot 数据 (L3 级共享内存优化)
    logger.debug("💿 正在初始化预对齐 Pivot 模拟数据 (Time-Aligned)...")
    p_s_time = time.time()
    
    # 确定回测的时间范围，用于预先裁切 Pivot 数据
    # 我们以第一个 config 的时间范围为准（假设多策略回测时间范围一致）
    test_start = pd.to_datetime(conf_list[0].start_date)
    test_end = pd.to_datetime(conf_list[0].end_date)
    
    raw_pivot_spot = load_pivot_data(raw_data_path, 'spot')
    raw_pivot_swap = load_pivot_data(raw_data_path, 'swap')
    
    # 预先按时间裁切，这样子策略模拟只需要按币种 (columns) 裁切，速度极快
    global_pivot_spot = {k: df.loc[test_start:test_end] for k, df in raw_pivot_spot.items()}
    global_pivot_swap = {k: df.loc[test_start:test_end] for k, df in raw_pivot_swap.items()}
    
    logger.debug(f"✅ 全对齐 Pivot 数据准备完成，耗时: {time.time() - p_s_time:.2f}s")

    # [V2 - L4 优化] 并行化多策略模拟 (保持顺序)
    report_list = [None] * len(conf_list)
    with ThreadPoolExecutor(max_workers=min(len(conf_list), job_num)) as executor:
        future_to_idx = {
            executor.submit(simu_performance_on_select, conf, False, global_pivot_spot, global_pivot_swap): i 
            for i, conf in enumerate(conf_list)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            report_list[idx] = future.result()

    if len([r for r in report_list if r is not None]) > 65535:
        logger.debug(f'回测报表数量为 {len(report_list)}，超过 65535，后续可能会占用海量内存')
    total_duration = time.time() - all_start_time
    logger.ok(f'--- 总体回测结束，总计耗时：{total_duration:.2f}s ---')
    
    return report_list
