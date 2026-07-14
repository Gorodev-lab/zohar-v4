import torch
import torch.nn as nn

class TrajectoryEncoder(nn.Module):
    """
    Spatio-Temporal Trajectory Encoder for vessels.
    Maps a sequence of GPS/AIS points [lat, lon, speed, course] to a 128d embedding.
    """
    def __init__(self, input_dim=4, embedding_dim=128, hidden_dim=64, num_layers=2):
        super(TrajectoryEncoder, self).__init__()
        self.input_projection = nn.Linear(input_dim, hidden_dim)
        self.gru = nn.GRU(
            hidden_dim, 
            hidden_dim, 
            num_layers=num_layers, 
            batch_first=True, 
            bidirectional=True
        )
        self.output_projection = nn.Linear(hidden_dim * 2, embedding_dim)
        self.activation = nn.ReLU()

    def forward(self, x):
        # x shape: (batch_size, sequence_length, input_dim)
        x_proj = self.activation(self.input_projection(x))
        gru_out, _ = self.gru(x_proj)
        # Gather final hidden state representation from bidirectional GRU (mean pool)
        pooled = torch.mean(gru_out, dim=1)
        embedding = self.output_projection(pooled)
        # Normalize to unit sphere for cosine similarity compatibility in Supabase pgvector
        return nn.functional.normalize(embedding, p=2, dim=1)

class AcousticEncoder(nn.Module):
    """
    Acoustic Signature Encoder.
    Maps a 2D spectrogram (e.g., shape [1, frequency_bins, time_frames]) to a 128d embedding.
    """
    def __init__(self, embedding_dim=128):
        super(AcousticEncoder, self).__init__()
        self.conv1 = nn.Conv2d(1, 16, kernel_size=3, stride=1, padding=1)
        self.conv2 = nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1)
        self.conv3 = nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1)
        self.pool = nn.AdaptiveAvgPool2d((4, 4))
        self.fc = nn.Linear(64 * 4 * 4, embedding_dim)
        self.activation = nn.ReLU()

    def forward(self, x):
        # x shape: (batch_size, 1, freq_bins, time_steps)
        x = self.activation(self.conv1(x))
        x = self.activation(self.conv2(x))
        x = self.activation(self.conv3(x))
        x = self.pool(x)
        x = x.view(x.size(0), -1)
        embedding = self.fc(x)
        # Normalize to unit sphere for similarity matching
        return nn.functional.normalize(embedding, p=2, dim=1)
