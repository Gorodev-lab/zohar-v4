#!/usr/bin/env python3
import os
import sys
import torch
import torch.nn as nn

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ml.loss import VICRegLoss
from ml.dataset import MaritimeMultimodalDataset, get_mock_db_data, chronological_split
from ml.models.t_jepa import TJEPA
from ml.models.cf_jepa import MTSJEPA
from ml.models.charm import CHARM, LinearProbe

def test_vicreg_loss():
    print("[Test] Verifying VICRegLoss...")
    loss_fn = VICRegLoss()
    z_pred = torch.randn(4, 128)
    z_target = torch.randn(4, 128)
    loss = loss_fn(z_pred, z_target)
    assert loss.dim() == 0, "Loss must be a scalar."
    assert not torch.isnan(loss), "Loss contains NaN."
    print("=> VICRegLoss OK.")

def test_dataset():
    print("[Test] Verifying MaritimeMultimodalDataset...")
    db_data = get_mock_db_data(num_vessels=3, points_per_vessel=120)
    train_split, _ = chronological_split(db_data, split_ratio=0.8)
    dataset = MaritimeMultimodalDataset(*train_split, seq_len=32)
    assert len(dataset) > 0, "Dataset should have sliding window samples."
    
    sample = dataset[0]
    assert sample["trajectory"].shape == (32, 5), "Trajectory shape incorrect."
    assert sample["text_embedding"].shape == (128,), "Text embedding shape incorrect."
    assert sample["bio_density"].shape == (32, 1), "Bio density shape incorrect."
    print("=> Dataset OK.")

def test_t_jepa():
    print("[Test] Verifying TJEPA...")
    model = TJEPA(in_features=5, embed_dim=128)
    x = torch.randn(4, 64, 5) # [Batch, SeqLen, Features]
    
    res = model(x)
    assert res["z_pred"].shape == (4, 64, 128), "z_pred shape incorrect."
    assert res["z_target"].shape == (4, 64, 128), "z_target shape incorrect."
    assert res["mask"].shape == (4, 64), "mask shape incorrect."
    
    # Check EMA update
    initial_target_params = [p.clone() for p in model.target_encoder.parameters()]
    # Perform update
    model.update_target_encoder(momentum=0.9)
    updated_target_params = [p.clone() for p in model.target_encoder.parameters()]
    
    # Target encoder parameters should change
    changed = False
    for p_init, p_upd in zip(initial_target_params, updated_target_params):
        if not torch.equal(p_init, p_upd):
            changed = True
            break
    assert changed, "Target encoder parameters did not update via EMA."
    print("=> T-JEPA OK.")

def test_cf_jepa_and_mts_jepa():
    print("[Test] Verifying MTS-JEPA...")
    model = MTSJEPA(in_features=5, embed_dim=128)
    
    x_past = torch.randn(4, 40, 5)
    x_future = torch.randn(4, 24, 5)
    horizon = torch.randn(4, 1)
    
    res = model(x_past, x_future, horizon)
    assert res["fine_pred"].shape == (4, 128), "fine_pred shape incorrect."
    assert res["fine_target"].shape == (4, 128), "fine_target shape incorrect."
    assert res["coarse_pred"].shape == (4, 128), "coarse_pred shape incorrect."
    assert res["coarse_target"].shape == (4, 128), "coarse_target shape incorrect."
    print("=> MTS-JEPA / CF-JEPA OK.")

def test_charm():
    print("[Test] Verifying CHARM & Linear Probe...")
    model = CHARM(kin_features=5, text_dim=128, bio_features=1, embed_dim=128)
    probe = LinearProbe(representation_dim=128, target_dim=1)
    
    trajectory = torch.randn(4, 64, 5)
    text_emb = torch.randn(4, 128)
    bio_dens = torch.randn(4, 64, 1)
    
    z_fused, z_global = model(trajectory, text_emb, bio_dens)
    assert z_fused.shape == (4, 64, 128), "z_fused shape incorrect."
    assert z_global.shape == (4, 128), "z_global shape incorrect."
    
    probe_out = probe(z_global)
    assert probe_out.shape == (4, 1), "probe_out shape incorrect."
    print("=> CHARM OK.")

def main():
    print("====================================================")
    print("        LOGR SSL JEPA Models Integrity Test")
    print("====================================================")
    
    test_vicreg_loss()
    test_dataset()
    test_t_jepa()
    test_cf_jepa_and_mts_jepa()
    test_charm()
    
    print("====================================================")
    print("        ALL INTEGRITY TESTS PASSED SUCCESSFULLY!")
    print("====================================================")

if __name__ == "__main__":
    main()
