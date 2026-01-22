"""
邢不行｜策略分享会
仓位管理框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

import gc
import shutil
import warnings
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import polars as pl

from config import stable_symbol, swap_path, spot_path
from core.model.backtest_config import BacktestConfig
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path

warnings.filterwarnings('ignore')


# =====策略相关函数
def del_insufficient_data(symbol_candle_data) -> Dict[str, pd.DataFrame]:
    """
    删除数据长度不足的币种信息

    :param symbol_candle_data:
    :return
    """
    # ===删除成交量为0的线数据、k线数不足的币种
    symbol_list = list(symbol_candle_data.keys())
    for symbol in symbol_list:
        # 删除空的数据
        if symbol_candle_data[symbol] is None or symbol_candle_data[symbol].empty:
            del symbol_candle_data[symbol]
            continue
        # 删除该币种成交量=0的k线
        # symbol_candle_data[symbol] = symbol_candle_data[symbol][symbol_candle_data[symbol]['volume'] > 0]

    return symbol_candle_data


def ignore_error(anything):
    return anything


def load_min_qty(file_path: Path) -> (int, Dict[str, int]):
    # 读取min_qty文件并转为dict格式
    min_qty_df = pd.read_csv(file_path, encoding='utf-8-sig')
    min_qty_df['最小下单量'] = -np.log10(min_qty_df['最小下单量']).round().astype(int)
    default_min_qty = min_qty_df['最小下单量'].max()
    min_qty_df.set_index('币种', inplace=True)
    min_qty_dict = min_qty_df['最小下单量'].to_dict()

    return default_min_qty, min_qty_dict


def is_trade_symbol(symbol, black_list, white_list) -> bool:
    """
    过滤掉不能用于交易的币种，比如稳定币、非USDT交易对，以及一些杠杆币
    :param symbol: 交易对
    :param black_list: 黑名单
    :param white_list: 白名单
    :return: 是否可以进入交易，True可以参与选币，False不参与
    """
    symbol = symbol.upper().replace('-USDT', 'USDT')
    if white_list:
        if symbol in white_list:
            return True
        else:
            return False

    # 稳定币和黑名单币不参与
    if not symbol or not symbol.endswith('USDT') or symbol in black_list:
        return False

    # 筛选杠杆币
    base_symbol = symbol[:-4]
    if base_symbol.endswith(('UP', 'DOWN', 'BEAR', 'BULL')) and base_symbol != 'JUP' and base_symbol != 'SYRUP' or base_symbol in stable_symbol:
        return False
    else:
        return True


def align_spot_swap_mapping(df, column_name, n):
    """
    处理spot和swap的映射关系
    :param df: 原始k线数据
    :param column_name: 需要处理的列
    :param n: 需要调整映射的周期数量
    :return: 调整好的k线数据
    """
    # 创建新组标识列
    df['is_new_group'] = (df[column_name].ne('') & df[column_name].shift().eq('')).astype(int)
    # 累积求和生成组号
    df['group'] = df['is_new_group'].cumsum()
    # 将空字符串对应的组号设为NaN
    df.loc[df['symbol_swap'].eq(''), 'group'] = np.nan
    # 通过 groupby 添加 grp_seq
    df['grp_seq'] = df.groupby('group').cumcount()
    # 过滤条件并修改前 n 行
    df.loc[df['grp_seq'] < n, column_name] = ''

    # 删除辅助列
    df.drop(columns=['is_new_group', 'group', 'grp_seq'], inplace=True)

    return df


def pl_is_trade_symbol(symbol_series, black_list):
    """Polars 版本的币种过滤"""
    # 统一格式
    symbols = symbol_series.str.to_uppercase().str.replace("-USDT", "USDT")
    
    # 基础过滤：必须以 USDT 结尾
    mask = symbols.str.ends_with("USDT")
    
    # 黑名单过滤
    if black_list:
        mask = mask & (~symbols.is_in(black_list))
    
    # 提取 base_symbol (V2 修正: Polars slice 第二个参数是长度，不能为负)
    base_symbols = symbols.str.slice(0, symbols.str.len_chars() - 4)
    
    # 杠杆币过滤 (UP/DOWN/BEAR/BULL)
    leverage_mask = (
        base_symbols.str.ends_with("UP") | 
        base_symbols.str.ends_with("DOWN") | 
        base_symbols.str.ends_with("BEAR") | 
        base_symbols.str.ends_with("BULL")
    ) & (base_symbols != "JUP") & (base_symbols != "SYRUP")
    
    mask = mask & (~leverage_mask)
    
    # 稳定币过滤
    if stable_symbol:
        mask = mask & (~base_symbols.is_in(stable_symbol))
        
    return mask


def pl_align_spot_swap_mapping(df, column_name, n):
    """Polars 版本的 spot/swap 映射对齐"""
    col = pl.col(column_name)
    is_not_empty = col != ""
    is_prev_empty = col.shift(1).fill_null("") == ""
    is_new_group = (is_not_empty & is_prev_empty).cast(pl.Int32)
    
    # 累积求和生成组号
    group_ids = is_new_group.cum_sum()
    
    # 只有在该列非空时才有组号
    group_ids = pl.when(is_not_empty).then(group_ids).otherwise(None)
    
    # 计算组内序号并置空前 n 行
    return df.with_columns([
        pl.when(
            (pl.int_range(0, pl.len()).over(group_ids) < n) & is_not_empty
        ).then(pl.lit("")).otherwise(col).alias(column_name)
    ])


def load_spot_and_swap_data(conf: BacktestConfig) -> (pd.DataFrame, pd.DataFrame):
    """
    加载现货和合约数据 (Polars V2 优化版)
    :param conf: 回测配置
    :return:
    """
    cache_path = get_file_path('data', 'cache', as_path_type=True)
    cache_path.mkdir(parents=True, exist_ok=True)
    
    combined_pq = cache_path / "all_candle_data.parquet"
    combined_pkl = cache_path / "all_candle_df_list.pkl"

    # [V2 优化] 缓存一致性检查：如果缓存存在，跳过扫描
    if combined_pq.exists() and combined_pkl.exists():
        logger.ok("🚀 发现现有行情数据缓存，跳过扫描阶段。")
        return # 直接返回，后续流程会通过 select_coin.py 加载这个文件

    logger.debug('💿 加载现货和合约数据 (Parquet Zero-Copy)...')
    
    # 兼容性处理：尝试从 config 导入不同的路径变量
    import config
    if hasattr(config, 'fuel_data_path'):
        parquet_base = Path(config.fuel_data_path) / "coin-binance-spot-swap-preprocess-pkl-1h"
    elif hasattr(config, 'pre_data_path'):
        parquet_base = Path(config.pre_data_path)
    elif hasattr(config, 'raw_data_path'):
        parquet_base = Path(config.raw_data_path)
    else:
        raise ImportError("无法在 config.py 中找到数据路径配置 (fuel_data_path 或 pre_data_path)")

    spot_pq = parquet_base / "spot.parquet"
    swap_pq = parquet_base / "swap.parquet"

    all_dfs = []
    all_symbols = set()

    # 1. 加载合约数据
    if not {'swap', 'mix'}.isdisjoint(conf.select_scope_set) or not {'swap'}.isdisjoint(conf.order_first_set):
        if swap_pq.exists():
            df = pl.read_parquet(swap_pq)
            # 过滤不可交易币种
            mask = pl_is_trade_symbol(df["symbol"], conf.black_list)
            df = df.filter(mask)
            
            # 对齐映射 (按币种分组处理)
            df = df.sort(["symbol", "candle_begin_time"])
            df = df.group_by("symbol", maintain_order=True).map_groups(
                lambda g: pl_align_spot_swap_mapping(g, 'symbol_spot', conf.min_kline_num)
            )
            
            # 类型转换以确保 Schema 一致性 (V2 修正: 解决 Int64 vs Float64 报错)
            num_cols = ["open", "high", "low", "close", "volume", "quote_volume", "trade_num", 
                        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", 
                        "funding_fee", "avg_price_1m", "avg_price_5m"]
            # 过滤不存在的列以防报错
            actual_num_cols = [c for c in num_cols if c in df.columns]
            df = df.with_columns([pl.col(c).cast(pl.Float64) for c in actual_num_cols])
            
            all_symbols.update(df["symbol"].unique().to_list())
            all_dfs.append(df)
            logger.debug(f"已加载合约数据: {len(df['symbol'].unique())} 币种")

    # 2. 加载现货数据
    if not {'spot', 'mix'}.isdisjoint(conf.select_scope_set):
        if spot_pq.exists():
            df = pl.read_parquet(spot_pq)
            # 过滤
            mask = pl_is_trade_symbol(df["symbol"], conf.black_list)
            df = df.filter(mask)
            
            # 对齐
            df = df.sort(["symbol", "candle_begin_time"])
            df = df.group_by("symbol", maintain_order=True).map_groups(
                lambda g: pl_align_spot_swap_mapping(g, 'symbol_swap', conf.min_kline_num)
            )
            
            # 类型转换以确保 Schema 一致性
            actual_num_cols = [c for c in num_cols if c in df.columns]
            df = df.with_columns([pl.col(c).cast(pl.Float64) for c in actual_num_cols])
            
            spot_symbols = df["symbol"].unique().to_list()
            all_symbols.update(spot_symbols)
            all_dfs.append(df)
            logger.debug(f"已加载现货数据: {len(df['symbol'].unique())} 币种")

    # 3. 合并并保存为中间格式
    if all_dfs:
        full_df = pl.concat(all_dfs)
        # 兼容性处理：存为单 Parquet 文件用于后续优化，同时生成 pickle list 以防万一
        combined_pq = cache_path / "all_candle_data.parquet"
        full_df.write_parquet(combined_pq)
        
        # 暂时保留 pickle list 兼容现有代码
        needed_cols = ["candle_begin_time", "symbol", "open", "high", "low", "close", "volume", 
                       "quote_volume", "trade_num", "taker_buy_base_asset_volume", 
                       "taker_buy_quote_asset_volume", "funding_fee", "avg_price_1m", 
                       "avg_price_5m", "是否交易", "first_candle_time", "last_candle_time", 
                       "symbol_spot", "symbol_swap", "is_spot"]
        
        logger.debug("正在生成兼容性数据缓存 (all_candle_df_list.pkl)...")
        # 转换为 pandas 并分组
        pd_df = full_df.select(needed_cols).to_pandas()
        candle_df_list = [group for _, group in pd_df.groupby("symbol")]
        pd.to_pickle(candle_df_list, cache_path / "all_candle_df_list.pkl")
        
        del full_df, pd_df, candle_df_list
    
    gc.collect()
    return tuple(list(all_symbols))


def save_performance_df_csv(conf: BacktestConfig, **kwargs):
    # logger.debug(f'💾 保存回测结果到文件夹: {conf.get_result_folder()}')
    for name, df in kwargs.items():
        file_path = conf.get_result_folder() / f'{name}.csv'
        df.to_csv(file_path, encoding='utf-8-sig')


# ===============================================================================================================
# 额外数据源
# ===============================================================================================================
def merge_data(df: pd.DataFrame, data_name: str, save_cols: List[str], symbol: str = '') -> dict[str, pd.Series]:
    """
    导入数据，最终只返回带有同index的数据
    :param df: （只读）原始的行情数据，主要是对齐数据用的
    :param data_name: 数据中心中的数据英文名
    :param save_cols: 需要保存的列
    :param symbol: 币种
    :return: 合并后的数据
    """
    import core.data_bridge as db
    from config import data_source_dict

    func_name, file_path = data_source_dict[data_name]

    if hasattr(db, func_name):
        extra_df: pd.DataFrame = getattr(db, func_name)(file_path, df, save_cols, symbol)
    else:
        print(f'⚠️ 未实现数据源：{data_name}')
        return {col: pd.Series([np.nan] * len(df)) for col in save_cols}

    if extra_df is None or extra_df.empty:
        return {col: pd.Series([np.nan] * len(df)) for col in save_cols}

    return {col: extra_df[col] for col in save_cols}


def check_cfg():
    """
    检查 data_source_dict 配置
    检查加载数据源函数是否存在
    检查数据源文件是否存在
    :return:
    """
    import core.data_bridge as db
    from config import data_source_dict
    for key, value in data_source_dict.items():
        func_name, file_path = value
        if not hasattr(db, func_name):
            raise Exception(f"【{key}】加载数据源方法未实现：{func_name}")

        if not (file_path and Path(file_path).exists()):
            raise Exception(f"【{key}】数据源文件不存在：{file_path}")

    print('✅ data_source_dict 配置检查通过')


def check_factor(factors: list):
    """
    检查因子中的配置
    检查是否有 extra_data_dict
    检查 extra_data_dict 中的数据源是否在 data_source_dict 中

    因子中的外部数据使用案例:

    extra_data_dict = {
        'coin-cap': ['circulating_supply']
    }

    :param factors:
    :return:
    """
    from core.utils.factor_hub import FactorHub
    for factor_name in factors:
        factor = FactorHub.get_by_name(factor_name)  # 获取因子信息
        if not (hasattr(factor, 'extra_data_dict') and factor.extra_data_dict):
            raise Exception(f"未找到【{factor_name}】因子中 extra_data_dict 配置")

        for data_source in factor.extra_data_dict.keys():
            from config import data_source_dict
            if data_source not in data_source_dict:
                raise Exception(f"未找到 extra_data_dict 配置的数据源：{data_source}")

    print(f'✅ {factors} 因子配置检查通过')
