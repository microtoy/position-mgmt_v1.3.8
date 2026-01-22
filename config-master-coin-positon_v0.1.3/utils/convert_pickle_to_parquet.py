
import pandas as pd
import polars as pl
from pathlib import Path
import time
from config import fuel_data_path

def convert_to_parquet():
    output_path = Path(fuel_data_path) / "coin-binance-spot-swap-preprocess-pkl-1h"
    
    print(f"Data path: {output_path}")
    
    # 1. Spot Data
    spot_pkl = output_path / "spot_dict.pkl"
    if spot_pkl.exists():
        print("Loading spot_dict.pkl...")
        s = time.time()
        spot_dict = pd.read_pickle(spot_pkl)
        print(f"Loaded spot_dict in {time.time() - s:.2f}s")
        
        if spot_dict:
            print("Converting to Parquet...")
            s = time.time()
            # Use Pandas concat then save
            df_all = pd.concat(spot_dict.values(), ignore_index=True)
            # Ensure data types
            for col in df_all.select_dtypes(['object']).columns:
                df_all[col] = df_all[col].astype('string')
            
            save_path = output_path / "spot.parquet"
            df_all.to_parquet(save_path, index=False, compression='zstd')
            print(f"Saved spot.parquet in {time.time() - s:.2f}s")
            del df_all, spot_dict
        
    # 2. Swap Data
    swap_pkl = output_path / "swap_dict.pkl"
    if swap_pkl.exists():
        print("Loading swap_dict.pkl...")
        s = time.time()
        swap_dict = pd.read_pickle(swap_pkl)
        print(f"Loaded swap_dict in {time.time() - s:.2f}s")
        
        if swap_dict:
            print("Converting to Parquet...")
            s = time.time()
            df_all = pd.concat(swap_dict.values(), ignore_index=True)
            for col in df_all.select_dtypes(['object']).columns:
                df_all[col] = df_all[col].astype('string')
            
            save_path = output_path / "swap.parquet"
            df_all.to_parquet(save_path, index=False, compression='zstd')
            print(f"Saved swap.parquet in {time.time() - s:.2f}s")
            del df_all, swap_dict

    # 3. Pivot Data
    for p_type in ['spot', 'swap']:
        pivot_pkl = output_path / f"market_pivot_{p_type}.pkl"
        if pivot_pkl.exists():
            print(f"Loading market_pivot_{p_type}.pkl...")
            pivot_data = pd.read_pickle(pivot_pkl)
            for k, v in pivot_data.items():
                print(f"Saving market_pivot_{p_type}_{k}.parquet...")
                v.to_parquet(output_path / f"market_pivot_{p_type}_{k}.parquet")

if __name__ == "__main__":
    convert_to_parquet()
