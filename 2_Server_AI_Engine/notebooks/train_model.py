"""
Train Dual-Input LSTM Model for Flood Prediction
Handles large dataset (349k samples, 3716 sensors) by limiting to 50 sensors
Avoids RAM overflow and creates optimized model for real-time prediction
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path
from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split
import joblib

from tensorflow import keras
from tensorflow.keras.models import Model
from tensorflow.keras.layers import Input, LSTM, Dense, Dropout, Concatenate
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.metrics import SparseCategoricalAccuracy

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Training hyperparameters
EPOCHS = 20


def load_and_preprocess_data(data_file):
    """
    Load and preprocess ALL data (no sensor limit for better class balance)
    
    Args:
        data_file (Path): Path to processed_training_data.csv
    
    Returns:
        tuple: (df_full, scaler) - Full preprocessed dataframe and fitted scaler
    """
    logger.info("="*80)
    logger.info("STEP 1: LOAD AND PREPROCESS DATA")
    logger.info("="*80)
    
    logger.info(f"[INFO] Loading data from: {data_file}")
    df = pd.read_csv(data_file)
    logger.info(f"[OK] Data shape: {df.shape}")
    logger.info(f"[OK] Unique sensors: {df['sensor_id'].nunique()}")
    logger.info(f"[OK] Original label distribution:\n{df['label'].value_counts().sort_index()}\n")
    
    # Create and fit scaler on ALL data (5 IoT features)
    logger.info(f"[INFO] Scaling 5 IoT features: ['level', 'flow', 'flow_efficiency', 'delta_level', 'rain_local']")
    scaler = MinMaxScaler()
    
    features_to_scale = ['level', 'flow', 'flow_efficiency', 'delta_level', 'rain_local']
    df[features_to_scale] = scaler.fit_transform(df[features_to_scale])
    logger.info(f"[OK] Features scaled to [0, 1] range")
    
    # Save scaler for later use in ai_predictor.py
    scaler_path = Path("../models/scaler.pkl")
    scaler_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(scaler, scaler_path)
    logger.info(f"[OK] Scaler saved to: {scaler_path}\n")
    
    return df, scaler


def create_sequences(df, timesteps=60):
    """
    Create sliding window sequences for LSTM training from ALL sensors
    Prioritize sensors with flood data (label 1, 2) for better class balance
    
    Args:
        df (DataFrame): Preprocessed data with scaled features
        timesteps (int): Length of each sequence
    
    Returns:
        tuple: (X_iot, X_api, y) - IoT sequences, API sequences, labels
    """
    logger.info("="*80)
    logger.info("STEP 2: CREATE SLIDING WINDOW SEQUENCES (timesteps=60)")
    logger.info("="*80)
    
    X_iot = []      # IoT data: [level, flow, flow_efficiency, delta_level, rain_local]
    X_api = []      # API data: [rain_forecast, time_decay, topo_index]
    y_labels = []   # Target labels
    
    features_iot = ['level', 'flow', 'flow_efficiency', 'delta_level', 'rain_local']
    
    # Strategy: Prioritize sensors with flood events (label 1, 2)
    logger.info("[INFO] Identifying sensors with flood/warning events...")
    sensors_with_flood = df[np.isin(df['label'].to_numpy(), [1, 2])]['sensor_id'].unique()
    sensors_safe = df[df['label'] == 0]['sensor_id'].unique()
    
    logger.info(f"[OK] Sensors with flood/warning: {len(sensors_with_flood)}")
    logger.info(f"[OK] Sensors with only safe data: {len(sensors_safe)}")
    
    # Sort to prioritize flood sensors
    all_sensors = np.concatenate([sensors_with_flood, sensors_safe])
    
    # Group by sensor and create sequences
    total_sequences = 0
    for sensor_id in all_sensors:
        group = df[df['sensor_id'] == sensor_id].reset_index(drop=True)
        
        if len(group) < timesteps:
            continue  # Skip if not enough data
        
        # Create sequences for this sensor
        for i in range(len(group) - timesteps):
            # IoT sequence: 60 timesteps × 5 features
            seq_iot = group.iloc[i:i+timesteps][features_iot].values
            
            # Label: class at timestep 60
            label = group.iloc[i+timesteps]['label']
            
            # Simulate API data: rain_forecast (mean of rain_local), time_decay (random 1-15), topo_index (0.8)
            rain_forecast = group.iloc[i:i+timesteps]['rain_local'].mean()
            time_decay = np.random.randint(1, 16)  # 1 to 15 minutes
            topo_index = 0.8  # Fixed topographic index
            api_data = [rain_forecast, time_decay, topo_index]
            
            X_iot.append(seq_iot)
            X_api.append(api_data)
            y_labels.append(label)
            
            total_sequences += 1
        
        if total_sequences % 10000 == 0:
            logger.info(f"[INFO] Processed {total_sequences:,} sequences so far...")
    
    # Convert to numpy arrays
    X_iot = np.array(X_iot, dtype=np.float32)
    X_api = np.array(X_api, dtype=np.float32)
    y_labels = np.array(y_labels, dtype=np.int32)
    
    logger.info(f"[OK] Total sequences created: {len(X_iot):,}")
    logger.info(f"[OK] X_iot shape: {X_iot.shape} (samples, timesteps, features)")
    logger.info(f"[OK] X_api shape: {X_api.shape} (samples, 3 features)")
    logger.info(f"[OK] y shape: {y_labels.shape}")
    logger.info(f"[WARNING] Imbalanced label distribution BEFORE balancing:\n{pd.Series(y_labels).value_counts().sort_index()}\n")
    
    return X_iot, X_api, y_labels


def balance_dataset(X_iot, X_api, y_labels):
    """
    Balance dataset using under-sampling with intelligent data augmentation
    Keep/duplicate samples with level > 30 AND flow > 10 but label is 0 or 1
    (High water + High flow = Safe drainage, teaches model good patterns)
    
    Args:
        X_iot: IoT input sequences
        X_api: API input sequences
        y_labels: Target labels
    
    Returns:
        tuple: (X_iot_balanced, X_api_balanced, y_balanced) - Balanced datasets
    """
    logger.info("="*80)
    logger.info("STEP 2.5: BALANCE DATASET (Under-sampling + Intelligent Augmentation)")
    logger.info("="*80)
    
    # Identify high-flow safe/warning samples (level > 30, flow > 10, label 0 or 1)
    # Extract the last timestep's level and flow values from sequences
    X_iot_level_last = X_iot[:, -1, 0]  # Last timestep, level (feature 0)
    X_iot_flow_last = X_iot[:, -1, 1]   # Last timestep, flow (feature 1)
    
    high_flow_mask = (X_iot_level_last > 30) & (X_iot_flow_last > 10) & (np.isin(y_labels, [0, 1]))
    high_flow_indices = np.where(high_flow_mask)[0]
    
    logger.info(f"[INFO] Intelligent augmentation: Found {len(high_flow_indices)} high-flow safe/warning samples")
    logger.info(f"[INFO] (level > 30 AND flow > 10 AND label in [0,1] = Good drainage pattern)")
    
    # Find class distribution
    unique_labels, counts = np.unique(y_labels, return_counts=True)
    class_distribution = dict(zip(unique_labels, counts))
    
    logger.info("\n[INFO] Original class distribution:")
    for label, count in sorted(class_distribution.items()):
        label_names = {0: "Safe", 1: "Warning", 2: "Flood"}
        logger.info(f"  Label {label} ({label_names.get(label)}): {count:,} samples")
    
    # Find minority class size
    min_samples = min(counts)
    logger.info(f"\n[INFO] Minority class has {min_samples:,} samples")
    logger.info(f"[INFO] Balancing to {min_samples:,} samples per class...")
    logger.info(f"[INFO] Duplicating high-flow samples to boost intelligence...\n")
    
    X_iot_balanced = []
    X_api_balanced = []
    y_balanced = []
    
    # Sample equal amount from each class
    for label in np.unique(y_labels):
        indices = np.where(y_labels == label)[0]
        
        if len(indices) <= min_samples:
            # Use all if less than min
            sampled_indices = indices
        else:
            # Randomly sample min_samples
            sampled_indices = np.random.choice(indices, size=min_samples, replace=False)
        
        X_iot_balanced.append(X_iot[sampled_indices])
        X_api_balanced.append(X_api[sampled_indices])
        y_balanced.append(np.full(len(sampled_indices), label))
    
    # Add augmented high-flow samples for Safe and Warning classes
    high_flow_mask_class = (X_iot_level_last > 30) & (X_iot_flow_last > 10) & (np.isin(y_labels, [0, 1]))
    high_flow_indices_aug = np.where(high_flow_mask_class)[0]
    
    if len(high_flow_indices_aug) > 0:
        # Duplicate high-flow samples up to 20% of minority class
        augment_size = max(1, int(min_samples * 0.2))
        augment_indices = np.random.choice(
            high_flow_indices_aug,
            size=min(augment_size, len(high_flow_indices_aug)),
            replace=True
        )
        
        logger.info(f"[INFO] Adding {len(augment_indices)} augmented high-flow samples (20% boost)")
        X_iot_balanced.append(X_iot[augment_indices])
        X_api_balanced.append(X_api[augment_indices])
        # Preserve original labels of augmented samples
        y_balanced.append(y_labels[augment_indices])
    
    # Concatenate all classes
    X_iot_balanced = np.concatenate(X_iot_balanced, axis=0)
    X_api_balanced = np.concatenate(X_api_balanced, axis=0)
    y_balanced = np.concatenate(y_balanced, axis=0)
    
    # Shuffle dataset
    logger.info("[INFO] Shuffling balanced dataset...")
    shuffle_indices = np.random.permutation(len(y_balanced))
    X_iot_balanced = X_iot_balanced[shuffle_indices]
    X_api_balanced = X_api_balanced[shuffle_indices]
    y_balanced = y_balanced[shuffle_indices]
    
    # Print balanced distribution
    logger.info("[OK] Balanced label distribution (AFTER balancing + augmentation):")
    balanced_counts = pd.Series(y_balanced).value_counts().sort_index()
    label_names = {0: "Safe", 1: "Warning", 2: "Flood"}
    for label, count in balanced_counts.items():
        percentage = (count / len(y_balanced)) * 100
        logger.info(f"  Label {label} ({label_names.get(label)}): {count:,} samples ({percentage:.2f}%)")
    
    logger.info(f"\n[OK] Balanced X_iot shape: {X_iot_balanced.shape}")
    logger.info(f"[OK] Balanced X_api shape: {X_api_balanced.shape}")
    logger.info(f"[OK] Balanced y shape: {y_balanced.shape}\n")
    
    return X_iot_balanced, X_api_balanced, y_balanced


def build_dual_input_lstm_model(timesteps=60, n_iot_features=5, n_api_features=3, n_classes=3):
    """
    Build Dual-Input LSTM model for flood prediction
    
    Architecture:
    - IoT input: LSTM(64) -> Dropout(0.2)
    - API input: Dense(16, relu)
    - Merged: Dense(64, relu) -> Dense(3, softmax)
    
    Args:
        timesteps (int): Number of timesteps
        n_iot_features (int): Number of IoT features (5)
        n_api_features (int): Number of API features (3)
        n_classes (int): Number of output classes (3)
    
    Returns:
        Model: Compiled Keras model
    """
    logger.info("="*80)
    logger.info("STEP 3: BUILD DUAL-INPUT LSTM MODEL")
    logger.info("="*80)
    
    # IoT input branch
    logger.info("[INFO] Building IoT input branch (LSTM)...")
    input_iot = Input(shape=(timesteps, n_iot_features), name='iot_input')
    lstm_out = LSTM(64, activation='relu', return_sequences=False)(input_iot)
    lstm_out = Dropout(0.2)(lstm_out)
    logger.info("[OK] IoT branch: Input(60,5) -> LSTM(64) -> Dropout(0.2)")
    
    # API input branch
    logger.info("[INFO] Building API input branch (Dense)...")
    input_api = Input(shape=(n_api_features,), name='api_input')
    api_out = Dense(16, activation='relu')(input_api)
    logger.info("[OK] API branch: Input(3) -> Dense(16, relu)")
    
    # Merge branches
    logger.info("[INFO] Merging branches...")
    merged = Concatenate()([lstm_out, api_out])
    merged = Dense(64, activation='relu')(merged)
    output = Dense(n_classes, activation='softmax')(merged)
    logger.info("[OK] Merged: Concat([LSTM(64), Dense(16)]) -> Dense(64) -> Dense(3, softmax)")
    
    # Build model
    model = Model(inputs=[input_iot, input_api], outputs=output)
    
    # Compile
    logger.info("[INFO] Compiling model...")
    model.compile(
        optimizer=Adam(learning_rate=0.001),
        loss='sparse_categorical_crossentropy',
        metrics=[SparseCategoricalAccuracy()]
    )
    logger.info("[OK] Model compiled: Adam, sparse_categorical_crossentropy\n")
    
    model.summary()
    logger.info("")
    
    return model




def train_model(model, X_iot, X_api, y, epochs=10, batch_size=64):
    """
    Train the model with train/test split
    
    Args:
        model: Compiled Keras model
        X_iot: IoT input data
        X_api: API input data
        y: Labels
        epochs (int): Number of training epochs
        batch_size (int): Batch size
    
    Returns:
        History: Training history object
    """
    logger.info("="*80)
    logger.info("STEP 4: TRAIN MODEL")
    logger.info("="*80)
    
    # Train-test split (80-20)
    logger.info("[INFO] Performing 80-20 train-test split...")
    X_iot_train, X_iot_test, X_api_train, X_api_test, y_train, y_test = train_test_split(
        X_iot, X_api, y, test_size=0.2, random_state=42, stratify=y
    )
    
    logger.info(f"[OK] Train set: {len(X_iot_train):,} samples")
    logger.info(f"[OK] Test set: {len(X_iot_test):,} samples")
    logger.info(f"[OK] Train labels distribution:\n{pd.Series(y_train).value_counts().sort_index()}")
    logger.info(f"[OK] Test labels distribution:\n{pd.Series(y_test).value_counts().sort_index()}\n")
    
    # Train model
    logger.info(f"[INFO] Starting training: epochs={epochs}, batch_size={batch_size}")
    logger.info(f"[INFO] Using GPU acceleration if available...\n")
    
    history = model.fit(
        [X_iot_train, X_api_train],
        y_train,
        validation_data=([X_iot_test, X_api_test], y_test),
        epochs=epochs,
        batch_size=batch_size,
        verbose=1
    )
    
    logger.info("\n" + "="*80)
    logger.info("STEP 5: EVALUATE MODEL")
    logger.info("="*80)
    
    # Evaluate on test set
    test_loss, test_accuracy = model.evaluate([X_iot_test, X_api_test], y_test, verbose=0)
    logger.info(f"[OK] Test Loss: {test_loss:.4f}")
    logger.info(f"[OK] Test Accuracy: {test_accuracy:.4f} ({test_accuracy*100:.2f}%)\n")
    
    return history, (X_iot_test, X_api_test, y_test)


def save_model(model, model_path='../models/flood_model.h5'):
    """
    Save trained model to file
    
    Args:
        model: Trained Keras model
        model_path (str): Path to save model
    """
    logger.info("="*80)
    logger.info("STEP 6: SAVE MODEL")
    logger.info("="*80)
    
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    model.save(model_path)
    logger.info(f"[OK] Model saved to: {model_path}")
    logger.info(f"[OK] Model can be loaded with: keras.models.load_model('{model_path}')\n")


def main():
    """Main training pipeline"""
    logger.info("\n")
    logger.info("*"*80)
    logger.info("DUAL-INPUT LSTM MODEL TRAINING")
    logger.info("Dataset: Kaggle Flood Simulation (349k samples, 3716 sensors)")
    logger.info("Strategy: Load ALL data + Under-sampling for balance")
    logger.info("*"*80 + "\n")
    
    try:
        # Step 1: Load and preprocess data
        data_file = Path("../data/processed_training_data.csv")
        df_full, scaler = load_and_preprocess_data(data_file)
        
        # Step 2: Create sequences
        X_iot, X_api, y = create_sequences(df_full, timesteps=60)
        
        # Step 2.5: Balance dataset using under-sampling
        X_iot_balanced, X_api_balanced, y_balanced = balance_dataset(X_iot, X_api, y)
        
        # Step 3: Build model
        model = build_dual_input_lstm_model(timesteps=60, n_iot_features=5, n_api_features=3, n_classes=3)
        
        # Step 4: Train model
        history, test_data = train_model(
            model, X_iot_balanced, X_api_balanced, y_balanced, epochs=EPOCHS, batch_size=64
        )
        
        # Step 5: Save model
        save_model(model, model_path='../models/flood_model.h5')
        
        logger.info("="*80)
        logger.info("[SUCCESS] Training completed successfully!")
        logger.info("="*80)
        logger.info("\nFiles saved:")
        logger.info("  - Model: ../models/flood_model.h5")
        logger.info("  - Scaler: ../models/scaler.pkl")
        logger.info("\nNext step: Run ai_predictor.py for real-time flood prediction\n")
        
    except Exception as e:
        logger.error(f"[FATAL] Error during training: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
