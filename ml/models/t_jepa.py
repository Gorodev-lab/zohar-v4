import torch
import torch.nn as nn
import torch.nn.functional as F
import copy

class TrajectoryEncoder(nn.Module):
    """
    Encoder for GPS trajectories. Converts coordinates and kinematics into 
    latent embeddings using 1D convolutions and Transformer layers.
    """
    def __init__(self, in_features=5, embed_dim=128, num_layers=3, num_heads=4):
        super(TrajectoryEncoder, self).__init__()
        # 1D Convolution to project input features to embed_dim
        self.input_proj = nn.Conv1d(in_features, embed_dim, kernel_size=3, padding=1)
        
        # Positional encoding for sequence ordering
        self.pos_embed = nn.Parameter(torch.zeros(1, 500, embed_dim)) # supports up to seq_len=500
        
        # Transformer layers
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=embed_dim, 
            nhead=num_heads, 
            dim_feedforward=embed_dim * 4,
            activation='gelu',
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.norm = nn.LayerNorm(embed_dim)

    def forward(self, x):
        # Input shape: [Batch, SeqLen, Features]
        # Transpose for Conv1d: [Batch, Features, SeqLen]
        h = x.transpose(1, 2)
        h = F.gelu(self.input_proj(h))
        # Transpose back: [Batch, SeqLen, EmbedDim]
        h = h.transpose(1, 2)
        
        # Add positional embedding
        seq_len = x.size(1)
        h = h + self.pos_embed[:, :seq_len, :]
        
        # Apply self-attention
        z = self.transformer(h)
        return self.norm(z)


class TrajectoryPredictor(nn.Module):
    """
    Predictor model in the latent space. Predicts target representations 
    from masked context representations and positional/mask tokens.
    """
    def __init__(self, embed_dim=128, num_layers=2, num_heads=4):
        super(TrajectoryPredictor, self).__init__()
        # Learnable mask token
        self.mask_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        
        # Predictor Transformer layers
        predictor_layer = nn.TransformerEncoderLayer(
            d_model=embed_dim, 
            nhead=num_heads, 
            dim_feedforward=embed_dim * 2,
            activation='gelu',
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(predictor_layer, num_layers=num_layers)
        self.proj = nn.Linear(embed_dim, embed_dim)

    def forward(self, z_context, mask):
        # z_context: [Batch, SeqLen, EmbedDim]
        # mask: [Batch, SeqLen] (1.0 for context, 0.0 for masked/target predictions)
        batch_size, seq_len, embed_dim = z_context.size()
        
        # Expand mask token to batch size and sequence length
        mask_tokens = self.mask_token.expand(batch_size, seq_len, embed_dim)
        
        # Combine: keep context embeddings where mask is 1.0, replace with mask_token where mask is 0.0
        mask_expanded = mask.unsqueeze(-1).expand(-1, -1, embed_dim)
        z_input = torch.where(mask_expanded > 0.5, z_context, mask_tokens)
        
        # Predict target embeddings
        z_pred = self.transformer(z_input)
        return self.proj(z_pred)


class TJEPA(nn.Module):
    """
    Trajectory Joint Embedding Predictive Architecture (T-JEPA) for GPS routes.
    Uses an EMA Target Encoder and a Latent Predictor.
    """
    def __init__(self, in_features=5, embed_dim=128, mask_ratio=0.5):
        super(TJEPA, self).__init__()
        self.mask_ratio = mask_ratio
        
        # Context Encoder (active parameters)
        self.context_encoder = TrajectoryEncoder(in_features=in_features, embed_dim=embed_dim)
        
        # Target Encoder (EMA parameters - gradients disabled)
        self.target_encoder = copy.deepcopy(self.context_encoder)
        for p in self.target_encoder.parameters():
            p.requires_grad = False
            
        # Predictor in latent space
        self.predictor = TrajectoryPredictor(embed_dim=embed_dim)

    @torch.no_grad()
    def update_target_encoder(self, momentum=0.996):
        """Updates the target encoder parameters via Exponential Moving Average (EMA)."""
        for p_target, p_context in zip(self.target_encoder.parameters(), self.context_encoder.parameters()):
            p_target.data.copy_(momentum * p_target.data + (1.0 - momentum) * p_context.data)

    def generate_random_mask(self, batch_size, seq_len, device):
        """Generates a binary mask of shape [Batch, SeqLen] with randomly masked blocks."""
        # 1.0 = Keep (Context), 0.0 = Mask (Target prediction)
        mask = torch.ones(batch_size, seq_len, device=device)
        num_masked = int(seq_len * self.mask_ratio)
        
        for b in range(batch_size):
            # We can mask a contiguous block (representing signal gaps) or random points
            # Contiguous block masking is much more representative of GPS gaps
            start = torch.randint(0, seq_len - num_masked + 1, (1,)).item()
            mask[b, start : start + num_masked] = 0.0
            
        return mask

    def forward(self, x):
        # x: [Batch, SeqLen, Features]
        batch_size, seq_len, _ = x.size()
        device = x.device
        
        # 1. Generate Target representations (using the target encoder - no grads)
        with torch.no_grad():
            z_target = self.target_encoder(x)
            
        # 2. Generate Context representations (using the context encoder - with grads)
        z_full = self.context_encoder(x)
        
        # Generate random mask
        mask = self.generate_random_mask(batch_size, seq_len, device)
        
        # Apply mask to context embeddings
        mask_expanded = mask.unsqueeze(-1).expand_as(z_full)
        z_context = z_full * mask_expanded
        
        # 3. Predict Target representations from Context
        z_pred = self.predictor(z_context, mask)
        
        return {
            "z_pred": z_pred,
            "z_target": z_target,
            "mask": mask
        }
