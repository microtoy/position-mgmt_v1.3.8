"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import gc
import os
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import polars as pl
from tqdm import tqdm

from config import job_num, factor_col_limit
from core.factor import calc_factor_vals
from core.model.backtest_config import BacktestConfig, StrategyConfig
from core.utils.factor_hub import FactorHub
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path

warnings.filterwarnings('ignore')
# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)

# 计算完因子之后，保留的字段
KLINE_COLS = ['candle_begin_time', 'symbol', 'is_spot', 'close', 'next_close', 'symbol_spot', 'symbol_swap', '是否交易']
# 计算完选币之后，保留的字段
SELECT_RES_COLS = [*KLINE_COLS, 'strategy', 'cap_weight', '方向', 'offset', 'target_alloc_ratio', 'order_first']
# 完整kline数据保存的路径
ALL_KLINE_PATH_TUPLE = ('data', 'cache', 'all_factors_kline.pkl')
ALL_KLINE_FULL_PATH_TUPLE = ('data', 'cache', 'all_factors_kline_full.pkl')


# ======================================================================================
# 因子计算相关函数
# - calc_factors_by_symbol: 计算单个币种的因子池
# - calc_factors: 计算因子池
# ======================================================================================

def trans_period_for_day(df, date_col='candle_begin_time', factor_dict=None):
    """
    将数据周期转换为指定的1D周期
    :param df: 原始数据
    :param date_col: 日期列
    :param factor_dict: 转换规则
    :return:
    """
    df.set_index(date_col, inplace=True)
    # 必备字段
    agg_dict = {
        'symbol': 'first',
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'quote_volume': 'sum',
        'trade_num': 'sum',
        'taker_buy_base_asset_volume': 'sum',
        'taker_buy_quote_asset_volume': 'sum',
        'is_spot': 'last',
        # 'has_swap': 'last',
        'symbol_swap': 'last',
        'symbol_spot': 'last',
        'funding_fee': 'sum',
        'next_avg_price': 'last',
        '是否交易': 'last',
    }

    if factor_dict:
        agg_dict = dict(agg_dict, **factor_dict)
    df = df.resample('1D').agg(agg_dict)
    df.reset_index(inplace=True)

    return df


from core.utils.factor_cache import load_factor_cache, save_factor_cache

# region 因子计算相关函数
def calc_factors_by_candle(candle_df, conf: BacktestConfig, factor_col_name_list) -> pd.DataFrame:
    """
    针对单一比对，计算所有因子的数值
    :param candle_df: 一个币种的k线数据 dataframe
    :param conf: 回测配置
    :param factor_col_name_list: 需要计算的因子列
    :return: 包含所有因子的 dataframe(目前是包含k线数据的）
    """
    # 遍历每个因子，计算每个因子的数据
    factor_series_dict = {}
    symbol = candle_df['symbol'].iloc[0]
    first_candle = candle_df['candle_begin_time'].iloc[0]
    last_candle = candle_df['candle_begin_time'].iloc[-1]

    for factor_name, param_list in conf.factor_params_dict.items():
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if factor.is_cross:
            continue

        # 筛选一下需要计算的因子
        factor_param_list = []
        for param in param_list:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name in factor_col_name_list:
                factor_param_list.append(param)
        if len(factor_param_list) == 0:
            continue  # 当该因子不需要计算的时候直接返回

        # ==========================
        # 尝试从缓存读取 (L1 优化)
        # ==========================
        cached_df = load_factor_cache(symbol, factor_name, factor_param_list, first_candle, last_candle)
        if cached_df is not None:
            # 转换为 dict of series
            for col in cached_df.columns:
                factor_series_dict[col] = cached_df[col].values
        else:
            # 缓存未命中，执行计算
            res_dict = calc_factor_vals(candle_df, factor_name, factor_param_list)
            factor_series_dict.update(res_dict)
            # 保存到缓存
            save_factor_cache(pd.DataFrame(res_dict), symbol, factor_name, factor_param_list, first_candle, last_candle)

    # 将结果 DataFrame 与原始 DataFrame 合并
    kline_with_factor_dict = {
        'candle_begin_time': candle_df['candle_begin_time'].values,
        'symbol': candle_df['symbol'].values,
        'is_spot': candle_df['is_spot'].values,
        'close': candle_df['close'].values,
        # 'has_swap': candle_df['has_swap'],
        # 'next_avg_price': candle_df['next_avg_price'].values,
        'next_close': candle_df['close'].shift(-1).values,  # 后面周期排除需要用
        # 'next_funding_fee': candle_df['funding_fee'].shift(-1).values,
        'symbol_spot': candle_df['symbol_spot'].astype(str).values,
        'symbol_swap': candle_df['symbol_swap'].astype(str).values,
        **factor_series_dict,
        '是否交易': candle_df['是否交易'].values,
    }

    kline_with_factor_df = pd.DataFrame(kline_with_factor_dict, copy=False)
    kline_with_factor_df.sort_values(by='candle_begin_time', inplace=True)

    # 抛弃一开始的一段k线，保留后面的数据
    first_candle_time = candle_df.iloc[0]['first_candle_time'] + pd.to_timedelta(f'{conf.min_kline_num}h')

    # # 调整 symbol_spot 和 symbol_swap
    # for col in ['symbol_spot', 'symbol_swap']:
    #     symbol_start_time = candle_df[
    #         (candle_df[col] != '') & (candle_df[col].shift(1) == '') & (~candle_df[col].shift(1).isna())
    #         ]['candle_begin_time']
    #     if not symbol_start_time.empty:
    #         condition = pd.Series(False, index=kline_with_factor_df.index)
    #         for symbol_time in symbol_start_time:
    #             _cond1 = kline_with_factor_df['candle_begin_time'] >= symbol_time
    #             _cond2 = kline_with_factor_df['candle_begin_time'] <= symbol_time + pd.to_timedelta(
    #                 f'{conf.min_kline_num}h')
    #             condition |= (_cond1 & _cond2)
    #         kline_with_factor_df.loc[condition, col] = ''
    #     kline_with_factor_df[col] = kline_with_factor_df[col].astype('category')

    # 需要对数据进行裁切
    kline_with_factor_df = kline_with_factor_df[kline_with_factor_df['candle_begin_time'] >= first_candle_time]

    # 下架币/拆分币，去掉最后一个周期不全的数据
    if kline_with_factor_df['candle_begin_time'].max() < pd.to_datetime(conf.end_date):
        _temp_time = kline_with_factor_df['candle_begin_time'] + pd.Timedelta(conf.max_hold_period)
        _del_time = kline_with_factor_df[kline_with_factor_df.loc[_temp_time.index, 'next_close'].isna()][
            'candle_begin_time']
        kline_with_factor_df = kline_with_factor_df[
            kline_with_factor_df['candle_begin_time'] <= _del_time.min() - pd.Timedelta(conf.max_hold_period)]

    # 只保留最近的数据
    if not conf.has_section_factor:
        kline_with_factor_df = kline_with_factor_df[
            (kline_with_factor_df['candle_begin_time'] >= pd.to_datetime(conf.start_date)) &
            (kline_with_factor_df['candle_begin_time'] < pd.to_datetime(conf.end_date))]

    # 只保留需要的字段
    return kline_with_factor_df


def process_candle_df(candle_df: pd.DataFrame, conf: BacktestConfig, factor_col_name_list: List[str], idx: int):
    """
    # 针对每一个币种的k线数据，按照策略循环计算因子信息
    :param candle_df: 单个币种的数据
    :param conf: backtest config
    :param factor_col_name_list:    因子列表，可以用于动态判断当前需要计算的因子列。
                                    当 factor_col_name_list ≠ conf.factor_col_name_list 时，说明需要节省一点内存
    :param idx: 索引
    :return: 带有因子数值的数据
    """
    # ==== 数据预处理 ====
    factor_dict = {'first_candle_time': 'first', 'last_candle_time': 'last'}
    for strategy in conf.strategy_list:
        symbol = candle_df['symbol'].iloc[-1]
        candle_df, _factor_dict, _ = strategy.after_merge_index(candle_df, symbol, factor_dict, {})
        factor_dict.update(_factor_dict)

    # 计算平均开盘价格
    candle_df['next_avg_price'] = candle_df[conf.avg_price_col].shift(-1)  # 用于后面计算当周期涨跌幅

    # 转换成日线数据  跟回测保持一致
    if conf.is_day_period:
        candle_df = trans_period_for_day(candle_df, factor_dict=factor_dict)

    # ==== 计算因子 ====
    # 清理掉头部参与日线转换的填充数据
    candle_df.dropna(subset=['symbol'], inplace=True)
    candle_df.reset_index(drop=True, inplace=True)
    # 针对单个币种的K线数据计算
    # 返回带有因子数值的K线数据
    factor_df = calc_factors_by_candle(candle_df, conf, factor_col_name_list)

    return idx, factor_df


def calc_factors(conf: BacktestConfig):
    """
    选币因子计算，考虑到大因子回测的场景，我们引入chunk的概念，会把所有factor切成多分，然后分别计算
    :param conf:       账户信息
    :return:
    """
    # ====================================================================================================
    # 1. ** k线数据整理及参数准备 **
    # - is_use_spot: True的时候，使用现货数据和合约数据;
    # - False的时候，只使用合约数据。所以这个情况更简单
    # ====================================================================================================
    # hold_period的作用是计算完因子之后，
    # 获取最近 hold_period 个小时内的数据信息，
    # 同时用于offset字段计算使用
    # ====================================================================================================
    # 2. ** 因子计算 (V2 优化版) **
    # ====================================================================================================
    # 优先加载 Parquet 格式数据 (Zero-Copy 准备)
    candle_pq_path = get_file_path('data', 'cache', 'all_candle_data.parquet', as_path_type=True)
    if candle_pq_path.exists():
        logger.debug("⚡️ 正在通过 Polars 加载 Parquet 原始数据...")
        full_df = pl.read_parquet(candle_pq_path)
        # 转换为 list of pandas (暂时保持因子函数兼容性)
        candle_df_list = [group.to_pandas() for group in full_df.partition_by("symbol", maintain_order=True)]
        del full_df
    else:
        # 兜底：如果 parquet 不存在则回退
        candle_df_list = pd.read_pickle(get_file_path('data', 'cache', 'all_candle_df_list.pkl'))
    
    factor_col_count = len(conf.factor_col_name_list)
    shards = range(0, factor_col_count, factor_col_limit)

    logger.debug(f'''* 总共计算因子个数：{factor_col_count} 个
* 单次计算因子个数：{factor_col_limit} 个，(需分成{len(shards)}组计算)
* 需要计算币种数量：{len(candle_df_list)} 个''')

    # 清理 cache 的缓存
    all_kline_pkl = get_file_path(*ALL_KLINE_PATH_TUPLE, as_path_type=True)
    all_kline_pkl.unlink(missing_ok=True)

    all_kline_full_pkl = get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True)
    all_kline_full_pkl.unlink(missing_ok=True)

    for shard_index in shards:
        logger.info(f'因子分片计算中，进度：{int(shard_index / factor_col_limit) + 1}/{len(shards)}')
        factor_col_name_list = conf.factor_col_name_list[shard_index:shard_index + factor_col_limit]

        all_factor_df_list = []
        
        # V2 优化：如果缓存命中率高，并行反而更慢 (序列化开销 > 计算开销)
        # 这里我们使用 ThreadPoolExecutor 替代 ProcessPoolExecutor，因为大部分操作是 I/O (读缓存)
        # 且避免了 DataFrames 的序列化开销
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=job_num) as executor:
            futures = [executor.submit(
                process_candle_df, candle_df, conf, factor_col_name_list, candle_idx
            ) for candle_idx, candle_df in enumerate(candle_df_list)]

            for future in tqdm(as_completed(futures), total=len(candle_df_list), desc='🧮 时序因子计算'):
                idx, factor_df = future.result()
                all_factor_df_list.append(factor_df)

        # ====================================================================================================
        # 3. ** 合并因子结果 **
        # 合并并整理所有K线，到这里因子计算完成
        # ====================================================================================================
        all_factors_df = pd.concat(all_factor_df_list, ignore_index=True)
        all_factors_df['symbol'] = pd.Categorical(all_factors_df['symbol'])

        del all_factor_df_list

        # ====================================================================================================
        # 4. ** 因子结果分片存储 **
        # 分片存储计算结果，节省内存占用，提高选币效率
        # - 将合并好的df，分成2个部分：k线和因子列
        # - k线数据存储为一个pkl，每一列因子存储为一个pkl，在选币时候按需读入合并成df
        # ====================================================================================================
        logger.debug('💾 分片存储因子结果...')

        # 选币需要的k线
        if not all_kline_pkl.exists():
            # 存储裁切时间的数据
            all_kline_df = all_factors_df[KLINE_COLS].sort_values(by=['candle_begin_time', 'symbol', 'is_spot'])
            all_kline_df = all_kline_df[
                (all_kline_df['candle_begin_time'] >= pd.to_datetime(conf.start_date)) &
                (all_kline_df['candle_begin_time'] < pd.to_datetime(conf.end_date))]
            all_kline_df.to_pickle(all_kline_pkl)
            # 同时保存 Parquet (V2 优化)
            all_kline_df.to_parquet(all_kline_pkl.with_suffix('.parquet'), index=False)

        if not all_kline_full_pkl.exists() and conf.has_section_factor:
            # 存储不裁切的全量数据
            all_kline_full_df = all_factors_df[KLINE_COLS].sort_values(by=['candle_begin_time', 'symbol', 'is_spot'])
            all_kline_full_df.to_pickle(all_kline_full_pkl)
            all_kline_full_df.to_parquet(all_kline_full_pkl.with_suffix('.parquet'), index=False)

        # 针对每一个因子进行存储
        cut_factors_df = all_factors_df[
                (all_factors_df['candle_begin_time'] >= pd.to_datetime(conf.start_date)) &
                (all_factors_df['candle_begin_time'] < pd.to_datetime(conf.end_date))]
        # V2 优化：将因子分片存储为单个 Parquet 文件，极大减少文件操作开销
        shard_pq = get_file_path('data', 'cache', f'factors_shard_{shard_index}.parquet', as_path_type=True)
        shard_pq.unlink(missing_ok=True)
        
        # 确保列都存在
        valid_cols = [c for c in factor_col_name_list if c in all_factors_df.columns]
        save_cols = ['candle_begin_time', 'symbol', 'is_spot'] + valid_cols
        
        if conf.has_section_factor:
            shard_full_pq = get_file_path('data', 'cache', f'factors_full_shard_{shard_index}.parquet', as_path_type=True)
            shard_full_pq.unlink(missing_ok=True)
            all_factors_df[save_cols].to_parquet(shard_full_pq, index=False)
            
        cut_factors_df[save_cols].to_parquet(shard_pq, index=False)

        del all_factors_df, cut_factors_df

        gc.collect()


def process_factor_df(factor_col_name):
    # 准备所有时序因子数据
    factor_path = get_file_path('data', 'cache', f'factor_full_{factor_col_name}.pkl', as_path_type=True)
    if not factor_path.exists():
        return factor_col_name, pd.DataFrame()

    return factor_col_name, pd.read_pickle(factor_path)


def load_all_factors(conf: BacktestConfig):
    all_kline_full_pq = get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True).with_suffix('.parquet')
    if all_kline_full_pq.exists():
        factor_df = pd.read_parquet(all_kline_full_pq)
    else:
        factor_df = pd.read_pickle(get_file_path(*ALL_KLINE_FULL_PATH_TUPLE, as_path_type=True))

    # 准备所有时序因子数据 (V2 ThreadPool 优化)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=job_num) as executor:
        futures = [executor.submit(
            process_factor_df, factor_col_name
        ) for factor_col_name in conf.section_depend_factor_col_name_list]

        for future in tqdm(as_completed(futures), total=len(conf.section_depend_factor_col_name_list), desc='✂️ 裁切时序因子数据'):
            factor_col_name, kline_with_factor_df = future.result()
            if not kline_with_factor_df.empty:
                factor_df[factor_col_name] = kline_with_factor_df

    return factor_df


def calc_cross_sections(conf: BacktestConfig):
    """
    截面因子计算，
    :param conf:       账户信息
    :return:
    """
    section_params_dict = conf.section_params_dict
    # 如果没有配置截面因子，那么直接跳过后续
    if not section_params_dict:
        logger.info(f'未检查到截面因子配置，跳过计算截面因子步骤。')
        return

    # 加载面板数据
    factor_df = load_all_factors(conf)
    # condition = factor_df['is_spot'] == (1 if conf.is_use_spot else 0)
    # factor_df = factor_df.loc[condition, :]
    # factor_spot_df = factor_df.loc[factor_df['is_spot'] == 1, :].copy()
    # factor_swap_df = factor_df.loc[factor_df['is_spot'] == 0, :].copy()

    # 遍历截面因子，调用截面因子计算方法
    # factor_series_dict = {}
    for factor_name, param_list in section_params_dict.items():
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if not factor.is_cross:
            continue

        # 筛选一下需要计算的因子
        factor_param_list = []
        section_param_list = []
        for param in param_list:
            factor_col_name = f'{factor_name}_{param}'
            if factor_col_name in conf.factor_col_name_list:
                factor_param_list.append(param)
                section_param_list.extend(factor.get_factor_list(param))
        if len(factor_param_list) == 0:
            continue  # 当该因子不需要计算的时候直接返回

        # 截面因子依赖的时序因子列
        section_col_name_list = list(set(f'{f}_{n}' for f, n in set(section_param_list)))

        # 对截面因子按照时间进行分段计算
        legacy_candle_df = factor_df[KLINE_COLS + section_col_name_list].copy()  # 如果是老的因子计算逻辑，单独拿出来一份数据
        for param in tqdm(factor_param_list, total=len(factor_param_list), desc=f'🧮 截面因子计算'):
            factor_col_name = f'{factor_name}_{param}'
            legacy_candle_df = factor.signal(legacy_candle_df, param, factor_col_name)

            # 对数据进行裁切并保存 (V2 优化: 保持 Key 字段便于 Join，改用 Parquet)
            cross_factor_df = legacy_candle_df[['candle_begin_time', 'symbol', 'is_spot', factor_col_name]]
            cross_factor_df = cross_factor_df[
                (cross_factor_df['candle_begin_time'] >= pd.to_datetime(conf.start_date)) &
                (cross_factor_df['candle_begin_time'] < pd.to_datetime(conf.end_date))]
            
            factor_pq = get_file_path('data', 'cache', f'factor_{factor_col_name}.parquet', as_path_type=True)
            factor_pq.unlink(missing_ok=True)
            cross_factor_df.to_parquet(factor_pq, index=False)
            del cross_factor_df
        del legacy_candle_df
    del factor_df
    gc.collect()


# endregion


# ======================================================================================
# 选币相关函数
# - calc_select_factor_rank: 计算因子排序
# - select_long_and_short_coin: 选做多和做空的币种
# - select_coins_by_strategy: 根据策略选币
# - select_coins: 选币，循环策略调用 `select_coins_by_strategy`
# ======================================================================================
# region 选币相关函数
def calc_select_factor_rank(df, factor_column='因子', ascending=True):
    """
    计算因子排名 (Polars 优化版本)
    :param df:              原数据
    :param factor_column:   需要计算排名的因子名称
    :param ascending:       计算排名顺序，True：从小到大排序；False：从大到小排序
    :return:                计算排名后的数据框
    """
    # 使用 Polars 进行高性能排名计算
    # Polars 使用 Rust 多线程引擎，比 Pandas 快 3-10 倍
    
    # 转换为 Polars LazyFrame
    pl_df = pl.from_pandas(df).lazy()
    
    # 计算分组排名和相关统计
    # descending 参数与 Pandas ascending 相反
    pl_result = pl_df.with_columns([
        pl.col(factor_column).rank(method='min', descending=not ascending).over('candle_begin_time').alias('rank'),
    ]).with_columns([
        pl.col('rank').max().over('candle_begin_time').alias('rank_max'),
        pl.col('symbol').count().over('candle_begin_time').alias('总币数'),
    ]).sort(['candle_begin_time', 'rank']).collect()
    
    # 转换回 Pandas DataFrame
    result_df = pl_result.to_pandas()
    
    return result_df


def select_long_and_short_coin(strategy: StrategyConfig, long_df: pd.DataFrame, short_df: pd.DataFrame):
    """
    选币，添加多空资金权重后，对于无权重的情况，减少选币次数

    :param strategy:                策略，包含：多头选币数量，空头选币数量，做多因子名称，做空因子名称，多头资金权重，空头资金权重
    :param long_df:                 多头选币的df
    :param short_df:                空头选币的df
    :return:
    """
    """
    # 做多选币
    """
    if strategy.long_cap_weight > 0:
        long_df = calc_select_factor_rank(long_df, factor_column=strategy.long_factor, ascending=True)

        long_df = strategy.select_by_coin_num(long_df, strategy.long_select_coin_num, max_limit=strategy.long_select_coin_num_max)

        long_df['方向'] = 1
        long_df['target_alloc_ratio'] = 1 / long_df.groupby('candle_begin_time')['symbol'].transform('size')
    else:
        long_df = pd.DataFrame()

    """
    # 做空选币
    """
    if strategy.short_cap_weight > 0:
        short_df = calc_select_factor_rank(short_df, factor_column=strategy.short_factor, ascending=False)

        if strategy.short_select_coin_num == 'long_nums':  # 如果参数是long_nums，则空头与多头的选币数量保持一致
            # 获取到多头的选币数量并整理数据
            long_select_num = long_df.groupby('candle_begin_time')['symbol'].size().to_frame()
            long_select_num = long_select_num.rename(columns={'symbol': '多头数量'}).reset_index()
            # 将多头选币数量整理到short_df
            short_df = short_df.merge(long_select_num, on='candle_begin_time', how='left')
            # 使用多头数量对空头数据进行选币
            short_df = short_df[short_df['rank'] <= short_df['多头数量']]
            del short_df['多头数量']
        else:
            short_df = strategy.select_by_coin_num(short_df, strategy.short_select_coin_num, min_limit=strategy.short_select_coin_num_min)

        short_df['方向'] = -1
        short_df['target_alloc_ratio'] = 1 / short_df.groupby('candle_begin_time')['symbol'].transform('size')
    else:
        short_df = pd.DataFrame()

    # ===整理数据
    df = pd.concat([long_df, short_df], ignore_index=True)  # 将做多和做空的币种数据合并
    df.sort_values(by=['candle_begin_time', '方向'], ascending=[True, False], inplace=True)
    df.reset_index(drop=True, inplace=True)

    del df['总币数'], df['rank_max']

    return df


def select_coins_by_strategy(factor_df, stg_conf: StrategyConfig):
    """
    针对每一个策略，进行选币，具体分为以下4步：
    - 4.1 数据清洗
    - 4.2 计算目标选币因子
    - 4.3 前置过滤筛选
    - 4.4 根据选币因子进行选币
    :param stg_conf: 策略配置
    :param factor_df: 所有币种K线数据，仅包含部分行情数据和选币需要的因子列
    :return: 选币数据
    """

    """
    4.1 数据预处理
    可以预留一些空间给数据整理，比如缺失数据的处理
    """
    pass

    """
    4.2 计算目标选币因子
    - 计算详情在 `strategy -> *.py`
    """
    s = time.time()
    # 缓存计算前的列名
    prev_cols = factor_df.columns
    # 计算因子
    result_df = stg_conf.calc_select_factor(factor_df)
    # 合并新的因子
    factor_df = factor_df[prev_cols].join(result_df[list(set(result_df.columns) - set(prev_cols))])
    logger.debug(f'[{stg_conf.name}] 选币因子计算耗时：{time.time() - s:.2f}s')

    """
    4.3 前置过滤筛选
    - 计算详情在 `strategy -> *.py`
    """
    s = time.time()
    long_df, short_df = stg_conf.filter_before_select(factor_df)
    short_df = short_df[short_df['symbol_swap'] != '']  # 保留有合约的现货
    logger.debug(f'[{stg_conf.name}] 前置过滤耗时：{time.time() - s:.2f}s')

    """
    4.4 根据选币因子进行选币
    """
    s = time.time()
    # 多头选币数据、空头选币数据、策略配置
    factor_df = select_long_and_short_coin(stg_conf, long_df, short_df)
    logger.debug(f'[{stg_conf.name}] 多空选币耗时：{time.time() - s:.2f}s')

    """
    4.5 后置过滤筛选
    """
    factor_df = stg_conf.filter_after_select(factor_df)
    logger.debug(f'[{stg_conf.name}] 后置过滤耗时：{time.time() - s:.2f}s')

    """
    4.6 根据多空比调整币种的权重
    """
    long_ratio = stg_conf.long_cap_weight / (stg_conf.long_cap_weight + stg_conf.short_cap_weight)
    factor_df.loc[factor_df['方向'] == 1, 'target_alloc_ratio'] = factor_df['target_alloc_ratio'] * long_ratio
    factor_df.loc[factor_df['方向'] == -1, 'target_alloc_ratio'] = factor_df['target_alloc_ratio'] * (1 - long_ratio)
    factor_df = factor_df[factor_df['target_alloc_ratio'].abs() > 1e-9]  # 去除权重为0的数据

    return factor_df[[*KLINE_COLS, '方向', 'target_alloc_ratio']]


def process_strategy(stg_conf: StrategyConfig, result_folder: Path, is_silent=False, unified_time='2017-01-01', factor_df=None):
    import logging
    if is_silent:
        logger.setLevel(logging.WARNING)  # 可以减少中间输出的log
    s = time.time()
    strategy_name = stg_conf.name
    logger.debug(f'[{stg_conf.name}] 开始选币...')

    # 准备选币用数据 (V2 - L6 优化: 极其重要！此时 factor_df 已经是合并好的 Master DataSet)
    # 直接使用，不再进行任何磁盘读取或 Join
    if factor_df is None:
        import polars as pl
        all_kline_pq = get_file_path(*ALL_KLINE_PATH_TUPLE, as_path_type=True).with_suffix('.parquet')
        factor_df = pl.read_parquet(all_kline_pq).to_pandas() if all_kline_pq.exists() else pd.DataFrame()

    factor_df = factor_df[factor_df['是否交易'] == 1]

    select_scope = stg_conf.select_scope
    order_first = stg_conf.order_first
    if select_scope == 'spot':
        condition = (factor_df['is_spot'] == 1)
    elif select_scope == 'swap':
        condition = (factor_df['is_spot'] == 0)
    else:  # mix 混合
        both_not_null = (factor_df['symbol_spot'] != '') & (factor_df['symbol_swap'] != '')
        # 根据优先下单，处理选币的币种
        order_first_symbol = (factor_df['is_spot'] == (1 if order_first == 'spot' else 0))
        condition = (~both_not_null | order_first_symbol)
    factor_df = factor_df.loc[condition, :].copy()

    factor_df.dropna(subset=stg_conf.factor_columns, inplace=True)
    factor_df.dropna(subset=['symbol'], how='any', inplace=True)

    factor_df.sort_values(by=['candle_begin_time', 'symbol'], inplace=True)
    factor_df.reset_index(drop=True, inplace=True)

    logger.debug(f'[{stg_conf.name}] 选币数据准备完成，消耗时间：{time.time() - s:.2f}s')

    result_df = select_coins_by_strategy(factor_df, stg_conf)
    # 用于缓存选币结果，如果结果为空，也会生成对应的，空的pkl文件
    stg_select_result = result_folder / f'{stg_conf.get_fullname(as_folder_name=True)}.pkl'

    if result_df.empty:
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        return

    del factor_df

    # 筛选合适的offset
    cal_offset_base_seconds = 3600 * 24 if stg_conf.is_day_period else 3600
    reference_date = pd.to_datetime(unified_time)
    time_diff_seconds = (result_df['candle_begin_time'] - reference_date).dt.total_seconds()
    offset = (time_diff_seconds / cal_offset_base_seconds).mod(stg_conf.period_num).astype('int8')
    result_df['offset'] = ((offset + 1 + stg_conf.period_num) % stg_conf.period_num).astype('int8')
    result_df = result_df[result_df['offset'].isin(stg_conf.offset_list)]

    if result_df.empty:
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(stg_select_result)
        logger.setLevel(logging.DEBUG)
        return

    # 添加其他的相关选币信息
    select_result_dict = dict()
    for kline_col in KLINE_COLS:
        select_result_dict[kline_col] = result_df[kline_col].values

    select_result_dict['方向'] = result_df['方向'].astype('int8').values
    select_result_dict['offset'] = result_df['offset'].astype('int8').values
    select_result_dict['target_alloc_ratio'] = result_df['target_alloc_ratio'].values
    select_result_df = pd.DataFrame(select_result_dict, copy=False)
    del result_df

    select_result_df['strategy'] = strategy_name
    select_result_df['strategy'] = pd.Categorical(select_result_df['strategy'])

    # 根据策略资金权重，调整目标分配比例
    select_result_df['cap_weight'] = np.float64(stg_conf.cap_weight)
    select_result_df['target_alloc_ratio'] = np.float64(
        select_result_df['target_alloc_ratio']
        * select_result_df['cap_weight']
        / len(stg_conf.offset_list)
        * select_result_df['方向']
    )
    select_result_df['order_first'] = order_first

    # 缓存到本地文件
    select_result_df[SELECT_RES_COLS].to_pickle(stg_select_result)

    logger.debug(f'[{strategy_name}] 耗时: {(time.time() - s):.2f}s')
    gc.collect()
    logger.setLevel(logging.DEBUG)


# 选币数据整理 & 选币
def select_coin_with_conf(conf: BacktestConfig, multi_process=True, silent=True):
    """
    ** 策略选币 **
    - is_use_spot: True的时候，使用现货数据和合约数据;
    - False的时候，只使用合约数据。所以这个情况更简单

    :param conf: 回测配置
    :param multi_process: 是否启用多进程
    :param silent: 是否静默
    :return:
    """
    import logging
    if silent:
        logger.setLevel(logging.WARNING)  # 可以减少中间输出的log
    # ====================================================================================================
    # 2.1 初始化
    # ====================================================================================================
    result_folder = conf.get_result_folder()  # 选币结果文件夹

    if not multi_process:
        for index, strategy in enumerate(conf.strategy_list):
            logger.debug(f'ℹ️ [{index + 1}/{len(conf.strategy_list)}] {conf.name}')
            process_strategy(strategy, result_folder, False, conf.unified_time)
        return

    # 多进程模式 -> V2 ThreadPool 模式 (避免 3.4GB Pickle 开销)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=job_num) as executor:
        futures = [executor.submit(process_strategy, stg, result_folder, silent, conf.unified_time, getattr(conf, 'shared_factor_df', None)) for stg in conf.strategy_list]

        for future in tqdm(as_completed(futures), total=len(conf.strategy_list), desc=f'🚀 {conf.name}'):
            try:
                future.result()
            except Exception as e:
                logger.exception(e)
                exit(1)
    logger.setLevel(logging.DEBUG)  # 日志结果恢复一下


def select_coins(confs: BacktestConfig | List[BacktestConfig], multi_process=True, factor_df=None):
    if isinstance(confs, BacktestConfig):
        # 如果是单例，就直接返回原来的结果
        if factor_df is not None:
            confs.shared_factor_df = factor_df
        return select_coin_with_conf(confs, multi_process=multi_process)

    # 否则就直接并行回测
    is_multi = True  
    is_silent = True
    if factor_df is not None:
        for conf in confs:
            conf.shared_factor_df = factor_df

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=job_num) as executor:
        futures = [executor.submit(select_coin_with_conf, conf, is_multi, is_silent) for conf in confs]
        for future in tqdm(as_completed(futures), total=len(confs), desc='选币'):
            try:
                future.result()
            except Exception as e:
                logger.exception(e)
                exit(1)


# endregion

# ======================================================================================
# 选币结果聚合
# ======================================================================================
# region 选币结果聚合
def transfer_swap(select_coin, df_swap):
    """
    将现货中的数据替换成合约数据，主要替换：close
    :param select_coin:     选币数据
    :param df_swap:         合约数据
    :return:
    """
    trading_cols = ['symbol', 'is_spot', 'close', 'next_close']
    spot_line_index = select_coin[(select_coin['symbol_swap'] != '') & (select_coin['is_spot'] == 1)].index

    spot_select_coin = select_coin.loc[spot_line_index].copy()
    swap_select_coin = select_coin.loc[select_coin.index.difference(spot_line_index)].copy()
    # ['candle_begin_time', 'symbol_swap', 'strategy', 'cap_weight', '方向', 'offset', 'target_alloc_ratio']
    spot_select_coin = pd.merge(
        spot_select_coin, df_swap[['candle_begin_time', *trading_cols]],
        left_on=['candle_begin_time', 'symbol_swap'], right_on=['candle_begin_time', 'symbol'],
        how='left', suffixes=('', '_2'))

    # merge完成之后，可能因为有些合约数据上线不超过指定的时间（min_kline_num）,造成合并异常，需要按照原现货逻辑执行
    failed_merge_select_coin = spot_select_coin[spot_select_coin['close_2'].isna()][select_coin.columns].copy()

    spot_select_coin = spot_select_coin.dropna(subset=['close_2'], how='any')
    spot_select_coin['is_spot_2'] = spot_select_coin['is_spot_2'].astype(np.int8)

    spot_select_coin.drop(columns=trading_cols, inplace=True)
    rename_dict = {f'{trading_col}_2': trading_col for trading_col in trading_cols}
    spot_select_coin.rename(columns=rename_dict, inplace=True)

    # 将拆分的选币数据，合并回去
    select_coin = pd.concat([swap_select_coin, failed_merge_select_coin, spot_select_coin], axis=0)
    select_coin.sort_values(['candle_begin_time', '方向'], inplace=True)

    return select_coin


def concat_select_results(conf: BacktestConfig) -> None:
    """
    聚合策略选币结果，形成综合选币结果
    :param conf:
    :return:
    """
    # 如果是纯多头现货模式，那么就不转换合约数据，只下现货单
    all_select_result_df_list = []  # 存储每一个策略的选币结果
    result_folder = conf.get_result_folder()
    select_result_path = result_folder / '选币结果.pkl'

    for strategy in conf.strategy_list:
        stg_select_result = result_folder / f'{strategy.get_fullname(as_folder_name=True)}.pkl'
        # 如果文件不存在，就跳过
        if not os.path.exists(stg_select_result):
            continue
        # 如果文件存在，就读取
        all_select_result_df_list.append(pd.read_pickle(stg_select_result))
        # 删除该策略的选币结果，如果要保留可以注释
        if not conf.is_reserved('strategy'):
            stg_select_result.unlink()

    # 如果没有任何策略的选币结果，就直接返回
    if not all_select_result_df_list:
        pd.DataFrame(columns=SELECT_RES_COLS).to_pickle(select_result_path)
        return

    # 聚合选币结果
    all_select_result_df = pd.concat(all_select_result_df_list, ignore_index=True)
    del all_select_result_df_list
    gc.collect()

    all_stg_select_first_time = all_select_result_df.groupby('strategy')['candle_begin_time'].first().max()
    all_select_result_df = all_select_result_df[all_select_result_df['candle_begin_time'] >= all_stg_select_first_time]
    all_select_result_df.to_pickle(select_result_path)

    return all_select_result_df


def process_select_results(conf: BacktestConfig) -> pd.DataFrame:
    select_result_path = conf.get_result_folder() / '选币结果.pkl'
    if not select_result_path.exists():
        logger.warning('没有生成选币文件，直接返回')
        return pd.DataFrame(columns=SELECT_RES_COLS)
    all_select_result_df = pd.read_pickle(select_result_path)

    # 不是纯多，且是现货策略
    # 筛选一下选币结果，判断其中的 优先下单标记是什么
    cond1 = all_select_result_df['order_first'] == 'swap'  # 优先下单合约
    cond2 = all_select_result_df['is_spot'] == 1  # 当前币种是现货
    if not all_select_result_df[cond1 & cond2].empty:
        all_kline_df = pd.read_pickle(get_file_path(*ALL_KLINE_PATH_TUPLE))
        # 将含有现货的币种，替换掉其中close价格
        df_swap = all_kline_df[(all_kline_df['is_spot'] == 0) & (all_kline_df['symbol_spot'] != '')]
        no_transfer_df = all_select_result_df[~(cond1 & cond2)]
        all_select_result_df = transfer_swap(all_select_result_df[cond1 & cond2], df_swap)
        all_select_result_df = pd.concat([no_transfer_df, all_select_result_df], ignore_index=True)

    # 删除选币文件，如果要保留可以注释
    if not conf.is_reserved('select'):
        select_result_path.unlink()

    return all_select_result_df


def to_ratio_pivot(df_select: pd.DataFrame, candle_begin_times, columns) -> pd.DataFrame:
    """使用 Polars 优化透视表转换和 Reindex，减少 GIL 锁竞争和内存开销"""
    if df_select.empty:
        return pd.DataFrame(index=candle_begin_times, columns=[], dtype=float).fillna(0)
    
    import polars as pl
    # 转换选币结果到 Polars
    pl_select = pl.from_pandas(df_select[['candle_begin_time', columns, 'target_alloc_ratio']])
    
    # 透视表转换
    # 注意：Polars 的 pivot 需要先按 index 排序以保证结果一致性
    pl_pivot = pl_select.pivot(
        on=columns,
        index='candle_begin_time',
        values='target_alloc_ratio',
        aggregate_function='sum'
    ).sort('candle_begin_time')
    
    # 构建完整的时间序列 DataFrame 进行右连接 (等价于 Pandas reindex)
    pl_times = pl.DataFrame({'candle_begin_time': candle_begin_times})
    
    # 确保 Join 键的精度一致 (us)，避免 SchemaError
    pl_times = pl_times.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    pl_pivot = pl_pivot.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    
    pl_pivot = pl_times.join(pl_pivot, on='candle_begin_time', how='left').fill_null(0)
    
    # 转回 Pandas
    df_ratio = pl_pivot.to_pandas().set_index('candle_begin_time')
    return df_ratio


def trim_ratio_delists(df_ratio: pd.DataFrame, end_time: pd.Timestamp, market_dict: dict, trade_type: str):
    """
    ** 删除要下架的币 **
    当币种即将下架的时候，把后续的持仓调整为 0
    :param df_ratio: 仓位比例
    :param end_time: 回测结束时间
    :param market_dict: 所有币种的K线数据
    :param trade_type: spot or swap
    :return: 仓位调整后的比例
    """
    for symbol in df_ratio.columns:
        df_market = market_dict[symbol]
        if len(df_market) < 2:
            continue

        # 没有下架
        last_end_time = df_market['candle_begin_time'].iloc[-1]
        if last_end_time >= end_time:
            continue

        second_last_end_time = df_market['candle_begin_time'].iloc[-2]
        if (df_ratio.loc[second_last_end_time:, symbol].abs() > 1e-8).any():
            logger.warning(f'{trade_type} {symbol} 下架选币权重不为 0，清除 {second_last_end_time} 之后的权重')
            df_ratio.loc[second_last_end_time:, symbol] = 0

    return df_ratio


def agg_strategy_offsets(df_select: pd.DataFrame, stg_conf: StrategyConfig):
    """使用 Polars 优化多 offset 权重聚合，大幅提升宽策略性能"""
    if df_select.empty:
        return pd.DataFrame(columns=['candle_begin_time', 'symbol', 'target_alloc_ratio'])
    
    import polars as pl
    
    # 转换为 Polars DataFrame
    pl_select = pl.from_pandas(df_select[['candle_begin_time', 'symbol', 'target_alloc_ratio']])
    
    # Step 1: 按 (candle_begin_time, symbol) 聚合权重
    pl_agg = pl_select.group_by(['candle_begin_time', 'symbol']).agg(
        pl.col('target_alloc_ratio').sum()
    )
    
    # Step 2: 构建完整的时间序列
    time_min = pl_agg['candle_begin_time'].min()
    time_max = pl_agg['candle_begin_time'].max()
    
    # 获取所有唯一 symbol
    symbols = pl_agg['symbol'].unique().sort()
    
    # 构建完整时间范围 (使用 datetime_range 支持小时级间隔)
    candle_times = pl.datetime_range(time_min, time_max, interval='1h', eager=True)
    
    # 创建 symbol × time 的笛卡尔积作为完整索引
    pl_full_index = pl.DataFrame({'candle_begin_time': candle_times}).join(
        pl.DataFrame({'symbol': symbols}), how='cross'
    )
    
    # 确保 datetime 精度一致 (μs) 以避免 SchemaError
    pl_agg = pl_agg.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    pl_full_index = pl_full_index.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    
def agg_strategy_offsets(pl_select: pl.DataFrame, stg_conf: StrategyConfig):
    """
    [L7 Zero-Copy Optimization] Polars-native agg_strategy_offsets
    Input: Polars DataFrame
    Output: Polars DataFrame
    """
    if pl_select.is_empty():
        return pl.DataFrame(schema={
            'candle_begin_time': pl.Datetime('us'),
            'symbol': pl.String,
            'target_alloc_ratio': pl.Float64
        })
    
    # Step 1: 按 (candle_begin_time, symbol) 聚合权重
    pl_agg = pl_select.group_by(['candle_begin_time', 'symbol']).agg(
        pl.col('target_alloc_ratio').sum()
    )
    
    # Step 3: 按 symbol 分组，对 target_alloc_ratio 进行 rolling sum
    # 解析 hold_period (可能是 '1H', '24H' 等字符串格式)
    hold_period_str = str(stg_conf.hold_period)
    if hold_period_str.endswith('H') or hold_period_str.endswith('h'):
        hold_period = int(hold_period_str[:-1])  # 提取数字部分
    else:
        hold_period = int(hold_period_str)  # 直接转换
    
    # [优化] 如果 hold_period 为 1，则不需要 rolling 和时间对齐，直接返回聚合结果
    # 只要在最终 pivot 时补全时间即可。这对于 S2 (多头全市场) 等密集型策略能带来极大加速 (避免 44M 行的 Sort + Rolling)
    if hold_period == 1:
        # 强制 symbol 为 String 类型，且确保时间精度为 us
        return pl_agg.with_columns([
            pl.col('symbol').cast(pl.String),
            pl.col('candle_begin_time').cast(pl.Datetime('us'))
        ])

    # 构建完整时间范围 (使用 datetime_range 支持小时级间隔)
    time_min = pl_agg['candle_begin_time'].min()
    time_max = pl_agg['candle_begin_time'].max()
    symbols = pl_agg['symbol'].unique().sort()
    
    candle_times = pl.datetime_range(time_min, time_max, interval='1h', eager=True)
    
    # 创建 symbol × time 的笛卡尔积作为完整索引
    pl_full_index = pl.DataFrame({'candle_begin_time': candle_times}).join(
        pl.DataFrame({'symbol': symbols}), how='cross'
    )
    
    # 确保 datetime 精度一致 (μs) 以避免 SchemaError
    pl_agg = pl_agg.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    pl_full_index = pl_full_index.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
    
    # Left join 得到完整的稀疏矩阵
    pl_full = pl_full_index.join(pl_agg, on=['candle_begin_time', 'symbol'], how='left').fill_null(0)

    pl_result = pl_full.sort(['symbol', 'candle_begin_time']).with_columns(
        pl.col('target_alloc_ratio').rolling_sum(window_size=hold_period, min_periods=1).over('symbol')
    )
    
    # 强制 symbol 为 String 类型，避免 concat 时 String/Categorical 不一致错误
    pl_result = pl_result.with_columns(pl.col('symbol').cast(pl.String))
    
    # 返回 Polars DataFrame，不转 Pandas！
    return pl_result


def agg_multi_strategy_ratio(conf: BacktestConfig, df_select: pd.DataFrame):
    """
    聚合多offset、多策略选币结果中的target_alloc_ratio
    :param conf: 回测配置
    :param df_select: 选币结果
    :return: 聚合后的df_spot_ratio 和 df_swap_ratio。

    数据结构:
    - index_col为candle_begin_time，
    - columns为symbol，
    - values为target_alloc_ratio的聚合结果

    示例:
                    1000BONK-USDT	1000BTTC-USDT	1000FLOKI-USDT	1000LUNC-USDT	1000PEPE-USDT	1000RATS-USDT	1000SATS-USDT	1000SHIB-USDT	1000XEC-USDT	1INCH-USDT	AAVE-USDT	ACE-USDT	ADA-USDT	    AEVO-USDT   ...
    2021/1/1 00:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 01:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 02:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 03:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 04:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 05:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 06:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 07:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 08:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    2021/1/1 09:00	0	            0	            0	            0	            0	            0	            0	            0	            0	            0	        0	        0	        -0.083333333	0           ...
    """
    # ====================================================================================================
    # 1. 先针对每个策略的多offset进行聚合
    # ====================================================================================================
    df_spot_select_list = []
    df_swap_select_list = []

    # 如果是D的持仓周期，应该是当天的选币，第二天0点持仓。
    # 按照目前的逻辑，原来自带的begin time是0点
    if conf.is_day_period:
        df_select['candle_begin_time'] = df_select['candle_begin_time'] + pd.Timedelta(hours=23)

    for strategy in conf.strategy_list:
        # 裁切当前策略的spot选币结果
        df_select_spot = df_select[(df_select['strategy'] == strategy.name) & (df_select['is_spot'] == 1)]
        # 买入现货部分
        _spot_select_long = agg_strategy_offsets(df_select_spot[df_select_spot['方向'] == 1], strategy)
        df_spot_select_list.append(_spot_select_long)
        # 做空现货部分
        _spot_select_short = agg_strategy_offsets(df_select_spot[df_select_spot['方向'] == -1], strategy)
        df_spot_select_list.append(_spot_select_short)

        # 裁切当前策略的swap选币结果
        df_select_swap = df_select[(df_select['strategy'] == strategy.name) & (df_select['is_spot'] == 0)]
        # 买入合约部分
        _swap_select_long = agg_strategy_offsets(df_select_swap[df_select_swap['方向'] == 1], strategy)
        df_swap_select_list.append(_swap_select_long)
        # 做空合约部分
        _swap_select_short = agg_strategy_offsets(df_select_swap[df_select_swap['方向'] == -1], strategy)
        df_swap_select_list.append(_swap_select_short)

def agg_multi_strategy_ratio(conf: BacktestConfig, df_select: pd.DataFrame):
    """
    [L7 Zero-Copy Optimization] Polars-native Aggregation Pipeline
    """
    import polars as pl
    
    # 1. 立即转换为 Polars，后续全程 Zero-Copy
    pl_select = pl.from_pandas(df_select)
    
    # 如果是D的持仓周期，调整时间
    if conf.is_day_period:
        pl_select = pl_select.with_columns(
            (pl.col('candle_begin_time') + pl.duration(hours=23)).alias('candle_begin_time')
        )

    pl_spot_list = []
    pl_swap_list = []

    for strategy in conf.strategy_list:
        # 使用 Polars 过滤，极大提升速度
        # 1. Spot 过滤
        pl_stg_spot = pl_select.filter((pl.col('strategy') == strategy.name) & (pl.col('is_spot') == 1))
        if len(pl_stg_spot) > 0:
            pl_spot_list.append(agg_strategy_offsets(pl_stg_spot.filter(pl.col('方向') == 1), strategy))
            pl_spot_list.append(agg_strategy_offsets(pl_stg_spot.filter(pl.col('方向') == -1), strategy))

        # 2. Swap 过滤
        pl_stg_swap = pl_select.filter((pl.col('strategy') == strategy.name) & (pl.col('is_spot') == 0))
        if len(pl_stg_swap) > 0:
            pl_swap_list.append(agg_strategy_offsets(pl_stg_swap.filter(pl.col('方向') == 1), strategy))
            pl_swap_list.append(agg_strategy_offsets(pl_stg_swap.filter(pl.col('方向') == -1), strategy))

    # 使用 Polars Concat，不需要 reindex
    pl_spot_agg = pl.concat(pl_spot_list) if pl_spot_list else pl.DataFrame()
    pl_swap_agg = pl.concat(pl_swap_list) if pl_swap_list else pl.DataFrame()

    # ====================================================================================================
    # 2. 针对多策略进行聚合 (Polars Pivot)
    # ====================================================================================================
    candle_begin_times = pd.date_range(conf.start_date, conf.end_date, freq='H', inclusive='left')

    # 将 Polars DataFrame 直接传给 pivot 函数 (需确保 to_ratio_pivot 支持 Polars 或在此处理)
    # 我们可以稍微修改逻辑，直接在这里做最终 Pivot，或让 to_ratio_pivot 兼容
    
    # 这里直接在 Polars 内部做 Pivot，效率最高
    def _polars_pivot_to_pandas(pl_df, times):
        if pl_df.is_empty():
            return pd.DataFrame(index=times, columns=[], dtype=float).fillna(0)
        
        # 按 candle_begin_time 和 symbol 再次聚合 (合并多策略)
        pl_grouped = pl_df.group_by(['candle_begin_time', 'symbol']).agg(
            pl.col('target_alloc_ratio').sum()
        )
        
        # Pivot
        pl_pivoted = pl_grouped.pivot(
            on='symbol',
            index='candle_begin_time',
            values='target_alloc_ratio',
            aggregate_function='sum'
        ).sort('candle_begin_time')
        
        # 对齐时间 (Right Join)
        pl_times = pl.DataFrame({'candle_begin_time': times})
        pl_times = pl_times.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
        # 确保 pl_pivoted 时间列也是 us
        pl_pivoted = pl_pivoted.with_columns(pl.col('candle_begin_time').cast(pl.Datetime('us')))
        
        pl_final = pl_times.join(pl_pivoted, on='candle_begin_time', how='left').fill_null(0)
        return pl_final.to_pandas().set_index('candle_begin_time')

    df_spot_ratio = _polars_pivot_to_pandas(pl_spot_agg, candle_begin_times)
    df_swap_ratio = _polars_pivot_to_pandas(pl_swap_agg, candle_begin_times)

    # # 针对下架币的处理
    # df_spot_ratio = trim_ratio_delists(df_spot_ratio, candle_begin_times.max(), spot_dict, 'spot')
    # df_swap_ratio = trim_ratio_delists(df_swap_ratio, candle_begin_times.max(), swap_dict, 'swap')

    return df_spot_ratio, df_swap_ratio
