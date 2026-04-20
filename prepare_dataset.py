"""
Dataset Preparation Script - Kaggle Flood Simulation Data
Preprocesses and merges 2D node and edge dynamic data for LSTM model training
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def load_and_preprocess_data():
    """
    Load and preprocess flood simulation data from Kaggle
    Uses ALL sensor data for large dataset with better class balance
    Merges node data (water level, rainfall) and edge data (flow)
    """
    
    # Define data paths
    nodes_file = Path("2_Server_AI_Engine/data/2d_nodes_dynamic_all.csv")
    edges_file = Path("2_Server_AI_Engine/data/2d_edges_dynamic_all.csv")
    output_file = Path("2_Server_AI_Engine/data/processed_training_data.csv")
    
    # Verify files exist
    if not nodes_file.exists():
        logger.error(f"[ERROR] File not found: {nodes_file}")
        return None
    
    if not edges_file.exists():
        logger.error(f"[ERROR] File not found: {edges_file}")
        return None
    
    logger.info(f"[OK] Loading nodes data from: {nodes_file}")
    df_nodes = pd.read_csv(nodes_file)
    logger.info(f"[OK] Nodes data shape: {df_nodes.shape}")
    logger.info(f"[OK] Nodes columns: {list(df_nodes.columns)}")
    
    logger.info(f"[OK] Loading edges data from: {edges_file}")
    df_edges = pd.read_csv(edges_file)
    logger.info(f"[OK] Edges data shape: {df_edges.shape}")
    logger.info(f"[OK] Edges columns: {list(df_edges.columns)}")
    
    # Rename node_idx -> sensor_id and edge_idx -> sensor_id for merging
    logger.info("[INFO] Renaming index columns to sensor_id...")
    df_nodes = df_nodes.rename(columns={'node_idx': 'sensor_id'})
    df_edges = df_edges.rename(columns={'edge_idx': 'sensor_id'})
    logger.info("[OK] Columns renamed successfully")
    
    # Merge dataframes on both timestep and sensor_id
    logger.info("[INFO] Merging nodes and edges data on [timestep, sensor_id]...")
    df_merged = pd.merge(
        df_nodes,
        df_edges,
        on=['timestep', 'sensor_id'],
        how='inner'
    )
    logger.info(f"[OK] Merged data shape: {df_merged.shape}")
    logger.info(f"[OK] Total sensors: {df_merged['sensor_id'].nunique()}")
    
    # Compute new features to match IoT format
    logger.info("[INFO] Computing new features (grouped by sensor)...")
    
    # rain_local = rainfall
    df_merged['rain_local'] = df_merged['rainfall']
    
    # flow = abs(flow)
    df_merged['flow'] = df_merged['flow'].abs()
    
    # level = (water_level - min) * 100 per sensor (convert to relative height in cm)
    # This ensures level is normalized per sensor, not globally
    df_merged['level'] = df_merged.groupby('sensor_id')['water_level'].transform(
        lambda x: (x - x.min()) * 100
    )
    
    # delta_level = rate of level change per sensor
    df_merged['delta_level'] = df_merged.groupby('sensor_id')['level'].transform(
        lambda x: x.diff().fillna(0)
    )
    
    # flow_efficiency = flow / (level + 1)
    # Measures how well water is draining given the water level
    # High value = good drainage (safe), Low value = poor drainage (risky)
    df_merged['flow_efficiency'] = df_merged['flow'] / (df_merged['level'] + 1)
    
    logger.info("[OK] Features computed successfully")
    
    # Create flood labels based on water level thresholds
    logger.info("[INFO] Creating flood classification labels...")
    
    def classify_flood_level(level):
        """
        Classify flood risk based on water level
        
        Returns:
            0: Safe (level < 20 cm)
            1: Warning (20 <= level < 40 cm)
            2: Flood (level >= 40 cm)
        """
        if level < 20:
            return 0  # Safe
        elif level < 40:
            return 1  # Warning
        else:
            return 2  # Flood
    
    df_merged['label'] = df_merged['level'].apply(classify_flood_level)
    logger.info("[OK] Labels created successfully")
    
    # Remove NaN rows if any
    logger.info("[INFO] Checking for NaN values...")
    nan_count = df_merged.isnull().sum().sum()
    if nan_count > 0:
        logger.warning(f"[WARNING] Found {nan_count} NaN values, removing them...")
        df_merged = df_merged.dropna()
        logger.info(f"[OK] NaN rows removed, remaining rows: {len(df_merged)}")
    else:
        logger.info("[OK] No NaN values found")
    
    # Select only required columns
    required_columns = ['timestep', 'sensor_id', 'level', 'flow', 'flow_efficiency', 'delta_level', 'rain_local', 'label']
    df_final = df_merged[required_columns].copy()
    
    logger.info(f"[OK] Final dataset shape: {df_final.shape}")
    logger.info(f"[OK] Final columns: {list(df_final.columns)}")
    
    # Save to CSV
    output_file.parent.mkdir(parents=True, exist_ok=True)
    df_final.to_csv(output_file, index=False)
    logger.info(f"[OK] Dataset saved to: {output_file}")
    
    # Display statistics
    logger.info("\n" + "="*80)
    logger.info("DATASET PREVIEW (First 10 rows):")
    logger.info("="*80)
    print(df_final.head(10).to_string())
    
    logger.info("\n" + "="*80)
    logger.info("DATASET STATISTICS:")
    logger.info("="*80)
    logger.info(f"Total samples: {len(df_final):,}")
    logger.info(f"Time range: {df_final['timestep'].min()} to {df_final['timestep'].max()}")
    logger.info(f"Level range: {df_final['level'].min():.2f} - {df_final['level'].max():.2f} cm")
    logger.info(f"Flow range: {df_final['flow'].min():.3f} - {df_final['flow'].max():.3f}")
    logger.info(f"Flow Efficiency range: {df_final['flow_efficiency'].min():.4f} - {df_final['flow_efficiency'].max():.4f}")
    logger.info(f"Delta Level range: {df_final['delta_level'].min():.4f} - {df_final['delta_level'].max():.4f}")
    logger.info(f"Rain range: {df_final['rain_local'].min():.2f} - {df_final['rain_local'].max():.2f}")
    
    logger.info("\n" + "="*80)
    logger.info("LABEL DISTRIBUTION:")
    logger.info("="*80)
    label_counts = df_final['label'].value_counts().sort_index()
    
    label_names = {0: "Safe", 1: "Warning", 2: "Flood"}
    for label, count in label_counts.items():
        percentage = (count / len(df_final)) * 100
        label_name = label_names.get(label, "Unknown")
        logger.info(f"  Label {label} ({label_name}): {count:,} samples ({percentage:.2f}%)")
    
    logger.info("="*80)
    logger.info("[COMPLETE] Da tao xong dataset khong lo!")
    logger.info("="*80 + "\n")
    
    return df_final


def create_synthetic_data_injection(n_samples=1000):
    """
    Create synthetic data to teach AI: High flow + Moderate-High level = NOT Flood
    
    This prevents overfitting on level alone. The key insight:
    - High water + High flow = Good drainage = SAFE or WARNING (not FLOOD)
    - This is the "Intelligence Boost" pattern
    
    Args:
        n_samples (int): Number of synthetic samples to create
    
    Returns:
        DataFrame: Synthetic data with correct structure
    """
    logger.info("="*80)
    logger.info("SMART DATA INJECTION - Teaching AI about Flow Efficiency")
    logger.info("="*80)
    logger.info("[INFO] Creating synthetic data: High level + High flow = NOT Flood")
    
    np.random.seed(42)
    
    synthetic_data = []
    
    for i in range(n_samples):
        # Level: 30-40 cm (high water but not extreme)
        level = np.random.uniform(30, 40)
        
        # Flow: 15-20 m³/s (VERY HIGH flow = excellent drainage)
        flow = np.random.uniform(15, 20)
        
        # Flow efficiency = flow / (level + 1) - will be HIGH
        flow_efficiency = flow / (level + 1)
        
        # Delta level: small variations
        delta_level = np.random.uniform(-2, 2)
        
        # Rain: light to moderate
        rain_local = np.random.uniform(0, 5)
        
        # BIG TEACHING POINT: Even with level=30-40, high flow means NOT Flood!
        # 70% Warning (1), 30% Safe (0) - to gently guide AI
        label = np.random.choice([0, 1], p=[0.3, 0.7])
        
        # Timestep: use high values to distinguish from Kaggle data
        timestep = 100 + (i % 50)  # Timesteps 100-149
        sensor_id = 5000 + (i % 100)  # Synthetic sensor IDs 5000-5099
        
        synthetic_data.append({
            'timestep': timestep,
            'sensor_id': sensor_id,
            'level': level,
            'flow': flow,
            'flow_efficiency': flow_efficiency,
            'delta_level': delta_level,
            'rain_local': rain_local,
            'label': label
        })
    
    df_synthetic = pd.DataFrame(synthetic_data)
    
    logger.info(f"[OK] Created {len(df_synthetic):,} synthetic samples")
    logger.info(f"[OK] Synthetic level range: {df_synthetic['level'].min():.2f} - {df_synthetic['level'].max():.2f} cm")
    logger.info(f"[OK] Synthetic flow range: {df_synthetic['flow'].min():.2f} - {df_synthetic['flow'].max():.2f} m³/s")
    logger.info(f"[OK] Synthetic flow_efficiency range: {df_synthetic['flow_efficiency'].min():.4f} - {df_synthetic['flow_efficiency'].max():.4f}")
    logger.info(f"[OK] Synthetic label distribution:")
    
    label_names = {0: "Safe", 1: "Warning", 2: "Flood"}
    for label, count in df_synthetic['label'].value_counts().sort_index().items():
        percentage = (count / len(df_synthetic)) * 100
        logger.info(f"      Label {label} ({label_names.get(label)}): {count:,} ({percentage:.1f}%)")
    
    logger.info("[INFO] Purpose: Teach AI that high flow = good drainage = NOT flood\n")
    
    return df_synthetic


def merge_with_synthetic_data(df_original, df_synthetic):
    """
    Merge original Kaggle data with synthetic injection data
    
    Args:
        df_original: Original preprocessed Kaggle data
        df_synthetic: Synthetic injection data
    
    Returns:
        DataFrame: Combined dataset
    """
    logger.info("="*80)
    logger.info("MERGING DATASETS - Kaggle + Synthetic Injection")
    logger.info("="*80)
    
    original_count = len(df_original)
    synthetic_count = len(df_synthetic)
    
    # Concatenate
    df_combined = pd.concat([df_original, df_synthetic], ignore_index=True)
    
    logger.info(f"[OK] Original Kaggle samples: {original_count:,}")
    logger.info(f"[OK] Synthetic injection samples: {synthetic_count:,}")
    logger.info(f"[OK] TOTAL combined samples: {len(df_combined):,}")
    
    logger.info("\n[OK] COMBINED LABEL DISTRIBUTION:")
    label_names = {0: "Safe", 1: "Warning", 2: "Flood"}
    for label, count in df_combined['label'].value_counts().sort_index().items():
        percentage = (count / len(df_combined)) * 100
        logger.info(f"      Label {label} ({label_names.get(label)}): {count:,} ({percentage:.2f}%)")
    
    logger.info("\n")
    return df_combined


if __name__ == "__main__":
    logger.info("="*80)
    logger.info("FLOOD DATASET PREPARATION - Kaggle + Smart Data Injection")
    logger.info("="*80 + "\n")
    
    try:
        # Step 1: Load and preprocess Kaggle data
        df = load_and_preprocess_data()
        
        if df is not None:
            # Step 2: Create synthetic injection data
            df_synthetic = create_synthetic_data_injection(n_samples=1000)
            
            # Step 3: Merge datasets
            df_final = merge_with_synthetic_data(df, df_synthetic)
            
            # Step 4: Save combined dataset
            output_file = Path("2_Server_AI_Engine/data/processed_training_data.csv")
            output_file.parent.mkdir(parents=True, exist_ok=True)
            df_final.to_csv(output_file, index=False)
            
            logger.info("="*80)
            logger.info("FINAL DATASET SAVED")
            logger.info("="*80)
            logger.info(f"[OK] File saved to: {output_file}")
            logger.info(f"[OK] Total samples in final dataset: {len(df_final):,}")
            logger.info(f"[OK] Dataset contains Kaggle data + Smart Injection data")
            logger.info("[INFO] AI will now learn that: High flow + High water = Good drainage = SAFE\n")
            
            logger.info("[OK] Dataset preparation completed successfully!")
        else:
            logger.error("[ERROR] Dataset preparation failed!")
            
    except Exception as e:
        logger.error(f"[FATAL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
