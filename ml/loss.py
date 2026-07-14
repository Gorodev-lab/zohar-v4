import torch
import torch.nn as nn
import torch.nn.functional as F

class VICRegLoss(nn.Module):
    """
    VICReg Loss (Variance-Covariance-Invariance Regularization) for preventing
    representation collapse in Joint Embedding Predictive Architectures (JEPA).
    
    Reference: Bardes et al., "VICReg: Variance-Covariance Regularization for 
    Self-Supervised Learning", ICLR 2022.
    """
    def __init__(self, sim_weight: float = 25.0, var_weight: float = 25.0, cov_weight: float = 1.0, gamma: float = 1.0, eps: float = 1e-4):
        super(VICRegLoss, self).__init__()
        self.sim_weight = sim_weight
        self.var_weight = var_weight
        self.cov_weight = cov_weight
        self.gamma = gamma
        self.eps = eps

    def forward(self, z_pred: torch.Tensor, z_target: torch.Tensor) -> torch.Tensor:
        # 1. Invariance / Similarity Loss (MSE)
        sim_loss = F.mse_loss(z_pred, z_target)

        # 2. Variance Loss (prevent collapse to a single point)
        # We calculate variance along the batch dimension (dim 0)
        # for both prediction and target embeddings
        var_pred = self._variance_term(z_pred)
        var_target = self._variance_term(z_target)
        var_loss = 0.5 * (var_pred + var_target)

        # 3. Covariance Loss (decorrelate dimensions to prevent redundancy)
        cov_pred = self._covariance_term(z_pred)
        cov_target = self._covariance_term(z_target)
        cov_loss = 0.5 * (cov_pred + cov_target)

        # Total Weighted Loss
        total_loss = (self.sim_weight * sim_loss +
                      self.var_weight * var_loss +
                      self.cov_weight * cov_loss)
        
        return total_loss

    def _variance_term(self, x: torch.Tensor) -> torch.Tensor:
        # If 3D, flatten Batch and SeqLen dimensions
        if x.dim() == 3:
            x = x.reshape(-1, x.size(-1))
        # Standard deviation of each feature across the batch
        std = torch.sqrt(x.var(dim=0) + self.eps)
        # Hinge loss: force std to be >= gamma
        loss = torch.mean(F.relu(self.gamma - std))
        return loss

    def _covariance_term(self, x: torch.Tensor) -> torch.Tensor:
        # If 3D, flatten Batch and SeqLen dimensions
        if x.dim() == 3:
            x = x.reshape(-1, x.size(-1))
        n, d = x.size()
        if n <= 1:
            return torch.tensor(0.0, device=x.device)
            
        # Center x
        x_centered = x - x.mean(dim=0, keepdim=True)
        # Compute covariance matrix: [Dim, Dim]
        cov = (x_centered.T @ x_centered) / (n - 1)
        
        # Select off-diagonal elements
        mask = ~torch.eye(d, dtype=torch.bool, device=x.device)
        off_diag = cov[mask]
        
        # Sum of squares of off-diagonal elements
        loss = (off_diag ** 2).sum() / d
        return loss
