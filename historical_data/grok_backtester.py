import pandas as pd
import argparse
from datetime import datetime
from deap import base, creator, tools, algorithms
import random
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import talib  # For ATR, SMA
import numpy as np  # For any numpy needs
from tqdm import tqdm  # For progress bar

# Command-line arguments
parser = argparse.ArgumentParser(description="Backtest sweep detection with optional CSV output.")
parser.add_argument('--csv_files', type=str, nargs='+', default=["merged.csv"], help="List of CSV files to process (space-separated).")
parser.add_argument('--output_csv', action='store_true', help="Flag to output results to new CSV files.")
args = parser.parse_args()

SLIPPAGE = 0.25  # Points per trade for realism
COMMISSION = 0.5  # $ per trade (adjust for broker)

def load_ohlc_from_csv(file_path):
    df = pd.read_csv(file_path)
    df['Time'] = pd.to_datetime(df['Time'])  # Assuming 'Time' column exists
    df = df.set_index('Time')
    df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})  # Standardize to lowercase
    return df

def backtest_setups(ohlc, range_lookback=20, reversal_window=10, wick_sweep=True, higher_tf=None, use_trend_filter=True):
    min_data = range_lookback + 1 + reversal_window
    if len(ohlc) < min_data:
        return pd.DataFrame()  # Empty if insufficient data
    
    # Multi-TF: Resample for higher TF swings/mean if specified
    if higher_tf:
        ohlc_htf = ohlc.resample(higher_tf).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
        rolling_high = ohlc_htf['high'].rolling(range_lookback).max().shift(1)
        rolling_low = ohlc_htf['low'].rolling(range_lookback).min().shift(1)
        rolling_mean = (rolling_high + rolling_low) / 2
        # Map back to 1-min (forward fill)
        rolling_high = rolling_high.reindex(ohlc.index, method='ffill')
        rolling_low = rolling_low.reindex(ohlc.index, method='ffill')
        rolling_mean = rolling_mean.reindex(ohlc.index, method='ffill')
    else:
        rolling_high = ohlc['high'].rolling(range_lookback).max().shift(1)
        rolling_low = ohlc['low'].rolling(range_lookback).min().shift(1)
        rolling_mean = (rolling_high + rolling_low) / 2
    
    # Trend filter: 50-period SMA
    if use_trend_filter:
        sma_50 = ohlc['close'].rolling(50).mean()
    
    # Vectorized sweep conditions
    bearish_sweep = ohlc['high'] > rolling_high
    if wick_sweep:
        bearish_sweep &= ohlc['close'] <= rolling_high
    
    bullish_sweep = ohlc['low'] < rolling_low
    if wick_sweep:
        bullish_sweep &= ohlc['close'] >= rolling_low
    
    # Apply trend filter
    if use_trend_filter:
        bullish_sweep &= ohlc['close'] > sma_50  # Bullish only if above SMA (uptrend)
        bearish_sweep &= ohlc['close'] < sma_50  # Bearish only if below SMA (downtrend)
    
    # Create shifted future columns for vectorized window checks (now includes next candles for entry break)
    for i in range(1, reversal_window + 2):  # +2 for entry confirmation
        ohlc[f'open_{i}'] = ohlc['open'].shift(-i)  # For potential entry
        ohlc[f'close_{i}'] = ohlc['close'].shift(-i)
        ohlc[f'high_{i}'] = ohlc['high'].shift(-i)
        ohlc[f'low_{i}'] = ohlc['low'].shift(-i)
    
    # Add rolling cols to DF
    ohlc['rolling_high'] = rolling_high
    ohlc['rolling_low'] = rolling_low
    ohlc['rolling_mean'] = rolling_mean
    
    # Filter to valid range (after lookback, before window end)
    valid_mask = ~rolling_high.isna() & ~ohlc[f'close_{reversal_window}'].isna()
    ohlc_valid = ohlc[valid_mask].copy()
    
    # Get candidate DFs
    bearish_df = ohlc_valid[bearish_sweep[valid_mask]].copy()
    bullish_df = ohlc_valid[bullish_sweep[valid_mask]].copy()
    
    # Vectorized outcome calculations with new entry logic
    def vectorized_bearish_outcomes(df, reversal_window):
        if df.empty:
            return np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
        
        means = df['rolling_mean'].values
        rhs = df['rolling_high'].values
        closes = np.stack([df[f'close_{i}'].fillna(np.inf).values for i in range(1, reversal_window + 1)], axis=1)
        highs = np.stack([df[f'high_{i}'].fillna(-np.inf).values for i in range(1, reversal_window + 1)], axis=1)
        next_lows = df['low_1'].values
        
        hits = closes < means[:, np.newaxis]
        has_hit = hits.any(axis=1)
        first_hits = np.argmax(hits, axis=1) + 1
        first_hits[~has_hit] = 0
        
        success = np.zeros(len(df), dtype=bool)
        triggered = np.zeros(len(df), dtype=bool)
        entry_prices = rhs - ENTRY_OFFSET  # Stop sell at rh - offset
        sl_prices = df['high'].values  # SL at sweep high (fartest)
        for idx in range(len(df)):
            fh = first_hits[idx]
            if fh > 0:
                pre_viol = highs[idx, :fh-1] > rhs[idx]
                success[idx] = not pre_viol.any()
                # Trigger if next low < entry (breaks back)
                if next_lows[idx] < entry_prices[idx]:
                    triggered[idx] = True
                    # Update SL to max of sweep high or post-sweep highs before hit
                    if fh - 1 > 0:  # Fixed: Check if slice has elements
                        post_sweep_high = np.max(highs[idx, :fh-1])
                        sl_prices[idx] = max(sl_prices[idx], post_sweep_high)
        
        # Only include triggered
        valid = triggered & success
        risks = sl_prices - entry_prices + SLIPPAGE
        rewards = entry_prices - means
        outcomes = np.where(valid, rewards - COMMISSION, -risks)
        
        profitable = rewards > 0
        return valid, outcomes, profitable, triggered, entry_prices, sl_prices
    
    def vectorized_bullish_outcomes(df, reversal_window):
        if df.empty:
            return np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
        
        means = df['rolling_mean'].values
        rls = df['rolling_low'].values
        closes = np.stack([df[f'close_{i}'].fillna(-np.inf).values for i in range(1, reversal_window + 1)], axis=1)
        lows = np.stack([df[f'low_{i}'].fillna(np.inf).values for i in range(1, reversal_window + 1)], axis=1)
        next_highs = df['high_1'].values
        
        hits = closes > means[:, np.newaxis]
        has_hit = hits.any(axis=1)
        first_hits = np.argmax(hits, axis=1) + 1
        first_hits[~has_hit] = 0
        
        success = np.zeros(len(df), dtype=bool)
        triggered = np.zeros(len(df), dtype=bool)
        entry_prices = rls + ENTRY_OFFSET  # Stop buy at rl + offset
        sl_prices = df['low'].values  # SL at sweep low
        for idx in range(len(df)):
            fh = first_hits[idx]
            if fh > 0:
                pre_viol = lows[idx, :fh-1] < rls[idx]
                success[idx] = not pre_viol.any()
                if next_highs[idx] > entry_prices[idx]:
                    triggered[idx] = True
                    if fh - 1 > 0:  # Fixed: Check slice
                        post_sweep_low = np.min(lows[idx, :fh-1])
                        sl_prices[idx] = min(sl_prices[idx], post_sweep_low)
        
        valid = triggered & success
        risks = entry_prices - sl_prices + SLIPPAGE
        rewards = means - entry_prices
        outcomes = np.where(valid, rewards - COMMISSION, -risks)
        
        profitable = rewards > 0
        return valid, outcomes, profitable, triggered, entry_prices, sl_prices
    
    # Apply
    if not bearish_df.empty:
        success, outcomes, profitable, triggered, entry_prices, sl_prices = vectorized_bearish_outcomes(bearish_df, reversal_window)
        bearish_df = bearish_df.assign(Success=success, Outcome=outcomes, Profitable=profitable, Triggered=triggered, Entry=entry_prices, SL=sl_prices)
        bearish_df['Type'] = 'Bearish'
        bearish_df['Sweep Price'] = bearish_df['high']
        bearish_df['Range Level'] = bearish_df['rolling_high']
        bearish_df['Mean'] = bearish_df['rolling_mean']
    
    if not bullish_df.empty:
        success, outcomes, profitable, triggered, entry_prices, sl_prices = vectorized_bullish_outcomes(bullish_df, reversal_window)
        bullish_df = bullish_df.assign(Success=success, Outcome=outcomes, Profitable=profitable, Triggered=triggered, Entry=entry_prices, SL=sl_prices)
        bullish_df['Type'] = 'Bullish'
        bullish_df['Sweep Price'] = bullish_df['low']
        bullish_df['Range Level'] = bullish_df['rolling_low']
        bullish_df['Mean'] = bullish_df['rolling_mean']
    
    # Combine
    if bearish_df.empty and bullish_df.empty:
        setups_df = pd.DataFrame()
    else:
        setups_df = pd.concat([bearish_df, bullish_df])
        setups_df = setups_df[['Type', 'Sweep Price', 'Range Level', 'Mean', 'close', 'Entry', 'SL', 'Success', 'Profitable', 'Triggered', 'Outcome']]
        setups_df = setups_df.rename(columns={'close': 'Sweep Close'})
        setups_df.index.name = 'Time'
    
    # Clean temp
    drop_cols = [col for col in ohlc.columns if col.startswith(('open_', 'close_', 'high_', 'low_')) or col in ['rolling_high', 'rolling_low', 'rolling_mean']]
    ohlc.drop(columns=drop_cols, inplace=True)
    
    return setups_df

# Main backtest loop
for csv_file in args.csv_files:
    try:
        ohlc = load_ohlc_from_csv(csv_file)
        
        df_setups = backtest_setups(ohlc)
        if not df_setups.empty:
            print(f"Setups detected in {csv_file}:")
            print(df_setups.to_string(index=True))  # With timestamps
            
            if args.output_csv:
                output_file = f"{csv_file.replace('.csv', '')}_setups.csv"
                df_setups.to_csv(output_file, index=False)
                print(f"Results saved to {output_file}")
            
            # Basic metrics
            total_setups = len(df_setups)
            success_rate = df_setups['Success'].mean() * 100 if total_setups > 0 else 0
            net_profit = df_setups['Outcome'].sum()
            print(f"Total setups: {total_setups}")
            print(f"Success rate: {success_rate:.2f}%")
            print(f"Net Profit (points): {net_profit:.2f}")
        else:
            print(f"No setups detected or insufficient data in {csv_file}")
    
    except Exception as e:
        print(f"Error processing {csv_file}: {e}")

# Genetic Optimization (run after backtest; uses ohlc from last file or load separately)
# Define fitness
def fitness(individual):
    range_lookback, reversal_window, wick_sweep = individual
    wick_sweep = bool(round(wick_sweep))  # 0/1 to bool
    df_setups = backtest_setups(ohlc, range_lookback=int(range_lookback), reversal_window=int(reversal_window), wick_sweep=wick_sweep)  # Use loaded ohlc
    net_profit = df_setups['Outcome'].sum() if not df_setups.empty else 0
    return net_profit,

creator.create("FitnessMax", base.Fitness, weights=(1.0,))
creator.create("Individual", list, fitness=creator.FitnessMax)
toolbox = base.Toolbox()
toolbox.register("attr_lookback", random.randint, 10, 50)
toolbox.register("attr_window", random.randint, 5, 15)
toolbox.register("attr_wick", random.uniform, 0, 1)
toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_lookback, toolbox.attr_window, toolbox.attr_wick), n=1)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)
toolbox.register("evaluate", fitness)
toolbox.register("mate", tools.cxTwoPoint)
toolbox.register("mutate", tools.mutGaussian, mu=0, sigma=1, indpb=0.2)
toolbox.register("select", tools.selTournament, tournsize=3)

pop = toolbox.population(n=20)  # Reduced from 50 for faster run
hof = tools.HallOfFame(1)
stats = tools.Statistics(lambda ind: ind.fitness.values)
stats.register("max", np.max)  # Use np.max

# Wrap eaSimple with tqdm progress
ngen = 10  # Reduced from 20 for faster run
logbook = tools.Logbook()
logbook.header = ['gen', 'nevals'] + (stats.fields if stats else [])

with tqdm(total=ngen, desc="Optimization Progress") as pbar:
    # Begin the generational process
    # Evaluate the individuals with an invalid fitness
    invalid_ind = [ind for ind in pop if not ind.fitness.valid]
    fitnesses = toolbox.map(toolbox.evaluate, invalid_ind)
    for ind, fit in zip(invalid_ind, fitnesses):
        ind.fitness.values = fit

    # This is just to assign the crowding distance to the individuals
    # no actual selection is done
    if hof is not None:
        hof.update(pop)

    record = stats.compile(pop) if stats else {}
    logbook.record(gen=0, nevals=len(invalid_ind), **record)
    print(logbook.stream)

    pbar.update(1)  # Update for gen 0

    for gen in range(1, ngen + 1):
        # Select the next generation individuals
        offspring = toolbox.select(pop, len(pop))

        # Vary the pool of individuals
        offspring = algorithms.varAnd(offspring, toolbox, cxpb=0.5, mutpb=0.2)

        # Evaluate the individuals with an invalid fitness
        invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
        fitnesses = toolbox.map(toolbox.evaluate, invalid_ind)
        for ind, fit in zip(invalid_ind, fitnesses):
            ind.fitness.values = fit

        # Update the hall of fame with the generated individuals
        if hof is not None:
            hof.update(offspring)

        # Replace the current population by the offspring
        pop[:] = offspring

        # Append the current generation statistics to the logbook
        record = stats.compile(pop) if stats else {}
        logbook.record(gen=gen, nevals=len(invalid_ind), **record)
        print(logbook.stream)

        pbar.update(1)

print("Best Params:", hof[0])

best_lookback = int(round(hof[0][0]))
best_window = int(round(hof[0][1]))
best_wick = round(hof[0][2]) > 0.5  # Threshold for bool
print(f"Rounded Best Params: lookback={best_lookback}, window={best_window}, wick={best_wick}")

# ML Filtering (run after backtest)
if not df_setups.empty:
    df_setups['RR'] = np.abs(df_setups['Mean'] - df_setups['Entry']) / np.abs(df_setups['SL'] - df_setups['Entry'])  # Use 'SL' for risk
    df_setups['RR'] = df_setups['RR'].replace([np.inf, -np.inf], np.nan).fillna(0)  # Handle inf in RR
    df_setups['Hour'] = df_setups.index.to_series().dt.hour  # Fixed: Use index.to_series().dt.hour
    df_setups['Volume_Ratio'] = ohlc.loc[df_setups.index]['volume'] / ohlc['volume'].rolling(20).mean().loc[df_setups.index]  # Fixed: loc[index]
    df_setups['Volume_Ratio'] = df_setups['Volume_Ratio'].replace([np.inf, -np.inf], np.nan).fillna(0)  # Handle inf in Volume_Ratio
    df_setups['ATR'] = talib.ATR(ohlc['high'], ohlc['low'], ohlc['close'], timeperiod=14).loc[df_setups.index]  # Fixed
    df_setups['Dist_to_MA'] = np.abs(df_setups['Entry'] - talib.SMA(ohlc['close'], 50).loc[df_setups.index])  # Use 'Entry'
    df_setups['Momentum'] = ohlc['close'].diff(5).loc[df_setups.index]  # Fixed

    features = ['RR', 'Hour', 'Volume_Ratio', 'ATR', 'Dist_to_MA', 'Momentum']
    X = df_setups[features].fillna(0)  # Handle NaNs
    X = X.replace([np.inf, -np.inf], 0)  # Extra safety for any remaining inf
    y = df_setups['Success'].astype(int)
    
    # Check if y has at least two classes
    if len(np.unique(y)) < 2:
        print("Skipping ML: Only one class in 'Success' - no prediction needed.")
    else:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, shuffle=False)  # Chronological

        # Check train classes
        if len(np.unique(y_train)) < 2:
            print("Skipping ML: Only one class in training data.")
        else:
            model = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')  # Balanced for imbalance
            model.fit(X_train, y_train)
            preds_proba = model.predict_proba(X_test)
            if preds_proba.shape[1] == 1:
                preds = preds_proba[:, 0]  # Single class - use the only prob (or 1 - prob if needed)
            else:
                preds = preds_proba[:, 1]  # Prob of class 1 (Success=True)
            filtered_win_rate = y_test[preds > 0.6].mean() * 100
            print(f"Filtered Win Rate: {filtered_win_rate:.2f}%")
            # Feature importances: print(model.feature_importances_)