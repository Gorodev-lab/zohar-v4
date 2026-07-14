import torch
import torch.nn as nn

class LatentPredictor(nn.Module):
    """
    JEPA Predictor model.
    Predicts the representation of the target trajectory during an AIS Gap,
    given the context representation and optional action/gap parameters (e.g. gap duration).
    """
    def __init__(self, embedding_dim=128, action_dim=2, hidden_dim=64):
        super(LatentPredictor, self).__init__()
        # Concatenate context embedding (128d) and action/parameters (e.g. gap duration, direction change)
        self.fc1 = nn.Linear(embedding_dim + action_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.fc3 = nn.Linear(hidden_dim, embedding_dim)
        self.activation = nn.ReLU()

    def forward(self, context_embedding, gap_parameters):
        # context_embedding shape: (batch_size, embedding_dim)
        # gap_parameters shape: (batch_size, action_dim) (e.g. [gap_duration_hours, distance_straight_line])
        combined = torch.cat([context_embedding, gap_parameters], dim=1)
        x = self.activation(self.fc1(combined))
        x = self.activation(self.fc2(x))
        delta = self.fc3(x)
        # Residual skip connection: predict the perturbation caused by the gap over the context state
        predicted_embedding = context_embedding + delta
        # Normalize to unit sphere for cosine similarity compatibility
        return nn.functional.normalize(predicted_embedding, p=2, dim=1)
