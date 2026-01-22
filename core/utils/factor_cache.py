
import os
import hashlib
import pandas as pd
import polars as pl
from pathlib import Path
from core.utils.log_kit import logger

CACHE_DIR = Path("data/cache/factor_cache")

def get_cache_key(symbol, factor_name, params, first_candle, last_candle):
    """生成因子的唯一缓存键"""
    # 结合币种、因子名、参数、以及数据的起止时间
    content = f"{symbol}_{factor_name}_{str(params)}_{str(first_candle)}_{str(last_candle)}"
    return hashlib.md5(content.encode()).hexdigest()

def load_factor_cache(symbol, factor_name, params, first_candle, last_candle):
    """尝试加载缓存的因子数据"""
    key = get_cache_key(symbol, factor_name, params, first_candle, last_candle)
    cache_path = CACHE_DIR / f"{key}.parquet"
    
    if cache_path.exists():
        try:
            # 使用 polars 加载更快
            return pl.read_parquet(cache_path).to_pandas()
        except:
            return None
    return None

def save_factor_cache(df, symbol, factor_name, params, first_candle, last_candle):
    """保存计算好的因子数据到缓存"""
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
    key = get_cache_key(symbol, factor_name, params, first_candle, last_candle)
    cache_path = CACHE_DIR / f"{key}.parquet"
    
    try:
        # 将结果转为 polars 保存，压缩率和速度更好
        pl.from_pandas(df).write_parquet(cache_path, compression="zstd")
    except Exception as e:
        logger.warning(f"保存因子缓存失败 {symbol} {factor_name}: {e}")

def clear_expired_cache(days=7):
    """清理过期缓存（可选）"""
    pass
