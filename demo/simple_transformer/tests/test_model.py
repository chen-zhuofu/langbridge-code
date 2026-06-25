import sys
from pathlib import Path

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from simple_transformer import TransformerConfig, TransformerModel


def build_model(**overrides):
    config = TransformerConfig(
        vocab_size=32,
        max_seq_len=64,
        embed_dim=16,
        num_heads=4,
        num_layers=2,
        **overrides,
    )
    return TransformerModel(config)


def test_forward_output_shape_matches_batch_and_vocab():
    model = build_model()
    batch, seq_len = 2, 10
    input_ids = torch.randint(0, model.config.vocab_size, (batch, seq_len))
    logits = model(input_ids)
    assert logits.shape == (batch, seq_len, model.config.vocab_size)


def test_attention_mask_handles_padding_tokens():
    model = build_model()
    input_ids = torch.tensor(
        [
            [1, 2, 3, 0, 0],
            [4, 5, 6, 7, 8],
        ]
    )
    attention_mask = torch.tensor(
        [
            [1, 1, 1, 0, 0],
            [1, 1, 1, 1, 1],
        ]
    )
    logits = model(input_ids, attention_mask=attention_mask)
    assert torch.isfinite(logits).all()


def test_model_backward_pass_updates_gradients():
    torch.manual_seed(0)
    model = build_model()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    input_ids = torch.randint(0, model.config.vocab_size, (4, 12))
    targets = torch.randint(0, model.config.vocab_size, (4, 12))
    logits = model(input_ids)
    loss = torch.nn.functional.cross_entropy(
        logits.reshape(-1, model.config.vocab_size),
        targets.reshape(-1),
    )
    loss.backward()
    assert model.token_embedding.weight.grad is not None
    optimizer.step()
    optimizer.zero_grad()
