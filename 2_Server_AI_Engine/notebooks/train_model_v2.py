"""
=========================================================================
 FloodMind-AIoT DSS — Train Model V2 (Hybrid Multi-Output)
=========================================================================
 Model: Conv1D-GRU + Weather + Hydro → class_output (4) + level_5min
 Fixes: scale-threshold bug, data leakage, 4-class labeling
=========================================================================
"""
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import classification_report, confusion_matrix
import joblib

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

EPOCHS = 30
BATCH_SIZE = 64
TIMESTEPS = 60
FUTURE_STEPS = 60  # 60 samples ~ 5 min ahead for level prediction

IOT_FEATURES = ['level', 'flow', 'flow_efficiency', 'delta_level', 'rain_local']
HYDRO_FEATURE_NAMES = [
    'h_avg', 'h_max', 'h_min', 'delta_h', 'slope_h',
    'q_avg', 'q_max', 'delta_q', 'slope_q',
    'r_sum', 'r_max', 'r_avg', 'drainage_eff', 'drainage_stress',
]
WEATHER_FEATURES = ['rain_forecast', 'time_decay', 'topo_index']

MODEL_DIR = Path(__file__).resolve().parent.parent / 'models' if not Path('../models').exists() else Path('../models')


def load_data(data_file):
    logger.info(f"Loading {data_file}")
    df = pd.read_csv(data_file)
    required = ['sensor_id', 'level', 'flow', 'label']
    for c in required:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}")
    if 'rain_local' not in df.columns:
        if 'rain' in df.columns:
            df['rain_local'] = df['rain']
        else:
            logger.warning("No rain column — filling with 0")
            df['rain_local'] = 0.0
    if 'flow_efficiency' not in df.columns:
        df['flow_efficiency'] = df['flow'] / (df['level'] + 1)
    if 'delta_level' not in df.columns:
        df['delta_level'] = df.groupby('sensor_id')['level'].diff().fillna(0)
    logger.info(f"Shape: {df.shape}, Sensors: {df['sensor_id'].nunique()}")
    logger.info(f"Labels:\n{df['label'].value_counts().sort_index()}")
    return df


def map_to_4class(df):
    """
    Map 3-class labels to 4-class + generate WATCH from rules ON RAW DATA.
    Must be called BEFORE scaling.
    """
    label_map = {0: 0, 1: 2, 2: 3}  # Safe→0, Warning→2, Flood→3
    df['label_v2'] = df['label'].map(label_map).fillna(0).astype(int)

    # Generate WATCH (class 1) from Safe samples with rising indicators
    safe_mask = df['label_v2'] == 0
    watch_cond = safe_mask & (
        (df['level'] > 15) & (df['delta_level'] > 0.5) |
        (df['rain_local'] > 3) & (df['level'] > 10)
    )
    n_watch = watch_cond.sum()
    # Limit WATCH generation to avoid noise
    max_watch = int(0.15 * safe_mask.sum())
    if n_watch > max_watch:
        indices = df[watch_cond].index.to_numpy()
        np.random.seed(42)
        keep = np.random.choice(indices, size=max_watch, replace=False)
        watch_cond = df.index.isin(keep)
    df.loc[watch_cond, 'label_v2'] = 1
    logger.info(f"4-class labels:\n{df['label_v2'].value_counts().sort_index()}")
    return df


def compute_hydro_features_for_window(window_df):
    """Compute hydro features from a window (raw values)."""
    levels = window_df['level'].values
    flows = window_df['flow'].values
    rains = window_df['rain_local'].values

    h_now = levels[-1]
    return np.array([
        np.mean(levels), np.max(levels), np.min(levels),
        h_now - levels[0], (h_now - levels[0]) / 5.0,
        np.mean(flows), np.max(flows),
        flows[-1] - flows[0], (flows[-1] - flows[0]) / 5.0,
        np.sum(rains), np.max(rains), np.mean(rains),
        flows[-1] / (h_now + 1), h_now / (flows[-1] + 1),
    ], dtype=np.float32)


def create_sequences(df, timesteps=TIMESTEPS, future=FUTURE_STEPS):
    """Create sequences with future level target, per sensor_id."""
    logger.info("Creating sequences...")
    X_iot, X_hydro, X_weather, y_class, y_level = [], [], [], [], []

    for sid, grp in df.groupby('sensor_id'):
        grp = grp.reset_index(drop=True)
        # Relax requirement for future steps if dataset is small
        usable_future = min(future, max(1, len(grp) - timesteps))
        
        if len(grp) <= timesteps:
            # Not enough data for even one sequence
            continue
            
        for i in range(len(grp) - timesteps - usable_future + 1):
            window = grp.iloc[i:i + timesteps]
            seq = window[IOT_FEATURES].values

            # Label at end of window
            label = int(grp.iloc[i + timesteps - 1]['label_v2'])

            # Future level target (or last available if future is truncated)
            future_level = float(grp.iloc[i + timesteps + usable_future - 1]['level'])

            # Hydro features (from raw window data before scaling)
            hf = compute_hydro_features_for_window(window)

            # Simulated weather
            rf = float(window['rain_local'].mean())
            td = np.random.randint(1, 16)
            ti = 0.8

            X_iot.append(seq)
            X_hydro.append(hf)
            X_weather.append([rf, td, ti])
            y_class.append(label)
            y_level.append(future_level)

    X_iot = np.array(X_iot, dtype=np.float32)
    X_hydro = np.array(X_hydro, dtype=np.float32)
    X_weather = np.array(X_weather, dtype=np.float32)
    y_class = np.array(y_class, dtype=np.int32)
    y_level = np.array(y_level, dtype=np.float32)

    logger.info(f"Sequences: {len(X_iot):,}")
    logger.info(f"X_iot={X_iot.shape} X_hydro={X_hydro.shape} X_weather={X_weather.shape}")
    logger.info(f"y_class distribution:\n{pd.Series(y_class).value_counts().sort_index()}")
    return X_iot, X_hydro, X_weather, y_class, y_level


def balance_classes(X_iot, X_hydro, X_weather, y_class, y_level):
    """Under-sample majority classes."""
    if len(y_class) == 0:
        logger.warning("Empty labels array passed to balance_classes!")
        return X_iot, X_hydro, X_weather, y_class, y_level
        
    unique, counts = np.unique(y_class, return_counts=True)
    if len(counts) == 0:
        return X_iot, X_hydro, X_weather, y_class, y_level
        
    min_c = min(counts)
    logger.info(f"Balancing to {min_c:,} per class")
    idx = []
    rng = np.random.RandomState(42)
    for c in unique:
        c_idx = np.where(y_class == c)[0]
        chosen = rng.choice(c_idx, size=min(min_c, len(c_idx)), replace=False)
        idx.extend(chosen)
    idx = np.array(idx)
    rng.shuffle(idx)
    return X_iot[idx], X_hydro[idx], X_weather[idx], y_class[idx], y_level[idx]


def scale_data(X_iot, X_hydro, X_weather, model_dir):
    """Fit scalers and transform. AFTER sequence creation."""
    n, t, f = X_iot.shape
    flat = X_iot.reshape(-1, f)
    sc_sensor = MinMaxScaler()
    flat_s = sc_sensor.fit_transform(flat)
    X_iot_s = flat_s.reshape(n, t, f)

    sc_hydro = MinMaxScaler()
    X_hydro_s = sc_hydro.fit_transform(X_hydro)

    sc_weather = MinMaxScaler()
    X_weather_s = sc_weather.fit_transform(X_weather)

    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(sc_sensor, model_dir / 'scaler_sensor.pkl')
    joblib.dump(sc_hydro, model_dir / 'scaler_hydro.pkl')
    joblib.dump(sc_weather, model_dir / 'scaler_weather.pkl')
    # Also save as legacy scaler (sensor only)
    joblib.dump(sc_sensor, model_dir / 'scaler.pkl')
    logger.info("Scalers saved")
    return X_iot_s, X_hydro_s, X_weather_s


def build_model(n_sensor=5, n_hydro=14, n_weather=3, n_classes=4):
    from keras.models import Model
    from keras.layers import (Input, Conv1D, GRU, Dense,
                                         Dropout, Concatenate)
    from keras.optimizers import Adam

    # Sensor branch
    inp_s = Input(shape=(TIMESTEPS, n_sensor), name='sensor_input')
    x = Conv1D(32, 3, activation='relu', padding='same')(inp_s)
    x = GRU(64, return_sequences=False)(x)
    x = Dropout(0.2)(x)

    # Weather branch
    inp_w = Input(shape=(n_weather,), name='weather_input')
    w = Dense(16, activation='relu')(inp_w)

    # Hydro branch
    inp_h = Input(shape=(n_hydro,), name='hydro_input')
    h = Dense(32, activation='relu')(inp_h)
    h = Dropout(0.1)(h)

    # Fusion
    merged = Concatenate()([x, w, h])
    f = Dense(64, activation='relu')(merged)
    f = Dropout(0.2)(f)
    f = Dense(32, activation='relu')(f)

    # Outputs
    out_class = Dense(n_classes, activation='softmax', name='class_output')(f)
    out_level = Dense(1, activation='linear', name='level_5min_output')(f)

    model = Model(inputs=[inp_s, inp_w, inp_h], outputs=[out_class, out_level])
    model.compile(
        optimizer=Adam(learning_rate=0.001),
        loss={'class_output': 'sparse_categorical_crossentropy',
              'level_5min_output': 'mse'},
        loss_weights={'class_output': 1.0, 'level_5min_output': 0.3},
        metrics={'class_output': 'sparse_categorical_accuracy',
                 'level_5min_output': 'mae'},
    )
    model.summary()
    return model


def train_and_evaluate(model, data, epochs=EPOCHS, batch_size=BATCH_SIZE):
    from sklearn.model_selection import train_test_split
    X_s, X_h, X_w, yc, yl = data

    # Split by index (time-aware would be better with real timestamps)
    idx = np.arange(len(yc))
    tr, te = train_test_split(idx, test_size=0.2, random_state=42, stratify=yc)

    hist = model.fit(
        [X_s[tr], X_w[tr], X_h[tr]],
        {'class_output': yc[tr], 'level_5min_output': yl[tr]},
        validation_data=(
            [X_s[te], X_w[te], X_h[te]],
            {'class_output': yc[te], 'level_5min_output': yl[te]}),
        epochs=epochs, batch_size=batch_size, verbose=1,
    )

    # Evaluate
    preds = model.predict([X_s[te], X_w[te], X_h[te]], verbose=0)
    pred_class = np.argmax(preds[0], axis=1)
    pred_level = preds[1].flatten()

    print("\n" + "=" * 60)
    print("CLASSIFICATION REPORT")
    print("=" * 60)
    names = ['SAFE', 'WATCH', 'WARNING', 'FLOOD']
    print(classification_report(yc[te], pred_class, target_names=names, zero_division=0))
    print("Confusion Matrix:")
    print(confusion_matrix(yc[te], pred_class))

    mae = np.mean(np.abs(pred_level - yl[te]))
    rmse = np.sqrt(np.mean((pred_level - yl[te]) ** 2))
    print(f"\nLevel forecast: MAE={mae:.2f} cm, RMSE={rmse:.2f} cm")

    # Missed flood rate
    flood_mask = yc[te] == 3
    if flood_mask.sum() > 0:
        missed = (pred_class[flood_mask] != 3).sum() / flood_mask.sum()
        print(f"Missed flood rate: {missed * 100:.1f}%")

    return hist


def save_model(model, model_dir):
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / 'flood_model_v2.keras'
    model.save(path)
    logger.info(f"Model saved: {path}")

    metadata = {
        'model_name': 'FloodMind-AIoT-DSS',
        'version': '2.0',
        'timesteps': TIMESTEPS,
        'window_minutes': 5,
        'sensor_features': IOT_FEATURES,
        'hydro_features': HYDRO_FEATURE_NAMES,
        'weather_features': WEATHER_FEATURES,
        'classes': {'0': 'SAFE', '1': 'WATCH', '2': 'WARNING', '3': 'FLOOD'},
        'outputs': ['class_output', 'level_5min_output'],
    }
    meta_path = model_dir / 'model_metadata.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    logger.info(f"Metadata saved: {meta_path}")


def main():
    logger.info("=" * 70)
    logger.info("FloodMind-AIoT DSS — TRAIN MODEL V2")
    logger.info("=" * 70)

    data_file = Path(__file__).resolve().parent.parent / '2_Server_AI_Engine' / 'data' / 'processed_training_data.csv'
    if not data_file.exists():
        data_file = Path('../data/processed_training_data.csv')
    if not data_file.exists():
        data_file = Path('2_Server_AI_Engine/data/processed_training_data.csv')

    model_dir = Path(__file__).resolve().parent.parent / '2_Server_AI_Engine' / 'models'
    if not model_dir.parent.exists():
        model_dir = Path('../models')
    if not model_dir.parent.exists():
        model_dir = Path('2_Server_AI_Engine/models')

    df = load_data(data_file)
    df = map_to_4class(df)

    X_iot, X_hydro, X_weather, y_class, y_level = create_sequences(df)
    X_iot, X_hydro, X_weather, y_class, y_level = balance_classes(
        X_iot, X_hydro, X_weather, y_class, y_level)

    X_iot, X_hydro, X_weather = scale_data(X_iot, X_hydro, X_weather, model_dir)

    model = build_model(n_sensor=len(IOT_FEATURES), n_hydro=len(HYDRO_FEATURE_NAMES),
                        n_weather=len(WEATHER_FEATURES), n_classes=4)
    train_and_evaluate(model, (X_iot, X_hydro, X_weather, y_class, y_level))
    save_model(model, model_dir)

    logger.info("=" * 70)
    logger.info("[SUCCESS] Training V2 complete!")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
