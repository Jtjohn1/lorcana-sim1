"""Trains ValueNetwork on self-play data via plain SGD-with-Adam, one
example at a time (see autodiff.py's docstring for why no batching).

Uses the paper's own Adam hyperparameters (beta1=0.9, beta2=0.98,
eps=1e-9, Section 5.3) — not cargo-culted, just a reasonable, already-
documented default to start from rather than guessing new ones.

Usage:
    python3 -m lorcana.nn.train --games 300 --epochs 5 --out lorcana/nn/weights.pkl
"""
from __future__ import annotations

import argparse
import pickle
import random
import time

import numpy as np

from ..cards import CardDatabase
from .autodiff import Tensor
from .transformer import ValueNetwork
from .generate_data import generate_dataset, save_dataset, load_dataset
from bots.random_bot import RandomBot
from bots.heuristic_bot import HeuristicBot


class Adam:
    def __init__(self, params: list[Tensor], lr: float = 1e-3, beta1: float = 0.9,
                 beta2: float = 0.98, eps: float = 1e-9):
        self.params = params
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.m = [np.zeros_like(p.data) for p in params]
        self.v = [np.zeros_like(p.data) for p in params]
        self.t = 0

    def step(self):
        self.t += 1
        for i, p in enumerate(self.params):
            if p.grad is None:
                continue
            g = p.grad
            self.m[i] = self.beta1 * self.m[i] + (1 - self.beta1) * g
            self.v[i] = self.beta2 * self.v[i] + (1 - self.beta2) * (g * g)
            mhat = self.m[i] / (1 - self.beta1 ** self.t)
            vhat = self.v[i] / (1 - self.beta2 ** self.t)
            p.data -= self.lr * mhat / (np.sqrt(vhat) + self.eps)

    def zero_grad(self):
        for p in self.params:
            p.zero_grad()


def mse_loss(pred: Tensor, label: float) -> Tensor:
    diff = pred - Tensor(np.array([[label]]))
    return (diff * diff).sum()


def save_weights(net: ValueNetwork, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump([p.data.copy() for p in net.params()], f)


def load_weights(net: ValueNetwork, path: str) -> None:
    with open(path, "rb") as f:
        arrays = pickle.load(f)
    for p, arr in zip(net.params(), arrays):
        assert p.data.shape == arr.shape, f"shape mismatch: {p.data.shape} vs {arr.shape}"
        p.data = arr.copy()


def evaluate(net: ValueNetwork, examples) -> float:
    total = 0.0
    for tokens, label in examples:
        pred = net(Tensor(tokens)).data.item()
        total += (pred - label) ** 2
    return total / max(len(examples), 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=300)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--deck-a", type=str, default="data/decklists/deck1.txt")
    parser.add_argument("--deck-b", type=str, default="data/decklists/deck2.txt")
    parser.add_argument("--dataset-cache", type=str, default="lorcana/nn/dataset_cache.pkl")
    parser.add_argument("--out", type=str, default="lorcana/nn/weights.pkl")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate the dataset instead of using the cache.")
    args = parser.parse_args()

    db = CardDatabase()
    rng = random.Random(args.seed)

    try:
        if args.regenerate:
            raise FileNotFoundError
        examples = load_dataset(args.dataset_cache)
        print(f"Loaded {len(examples)} cached examples from {args.dataset_cache}")
    except FileNotFoundError:
        print(f"Generating self-play dataset from {args.games} games...")
        t0 = time.time()

        def bot_factory():
            # Mix of both fast bots for data diversity.
            return RandomBot(random.Random(rng.randrange(1 << 30))) if rng.random() < 0.5 \
                else HeuristicBot(random.Random(rng.randrange(1 << 30)))

        examples, games_used = generate_dataset(db, args.deck_a, args.deck_b, args.games, rng, bot_factory)
        print(f"Generated {len(examples)} examples from {games_used}/{args.games} completed games in {time.time()-t0:.1f}s")
        save_dataset(examples, args.dataset_cache)

    rng.shuffle(examples)
    split = int(len(examples) * 0.85)
    train_ex, val_ex = examples[:split], examples[split:]
    print(f"Train: {len(train_ex)}  Val: {len(val_ex)}")

    from .state_encoding import INPUT_DIM
    net = ValueNetwork(input_dim=INPUT_DIM, dim=32, num_heads=4, num_layers=2, ff_hidden_dim=64, seed=args.seed)
    opt = Adam(net.params(), lr=args.lr)

    print(f"Pre-training val MSE: {evaluate(net, val_ex):.4f}")
    for epoch in range(args.epochs):
        rng.shuffle(train_ex)
        t0 = time.time()
        total_loss = 0.0
        for tokens, label in train_ex:
            opt.zero_grad()
            pred = net(Tensor(tokens))
            loss = mse_loss(pred, label)
            loss.backward()
            opt.step()
            total_loss += loss.data.item()
        train_mse = total_loss / len(train_ex)
        val_mse = evaluate(net, val_ex)
        print(f"Epoch {epoch+1}/{args.epochs}: train_mse={train_mse:.4f} val_mse={val_mse:.4f} ({time.time()-t0:.1f}s)")

    save_weights(net, args.out)
    print(f"Saved weights to {args.out}")


if __name__ == "__main__":
    main()
