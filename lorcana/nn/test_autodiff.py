"""Numerical gradient checking for autodiff.py.

For each op, build a small computation ending in a scalar, get the
analytic gradient via .backward(), then perturb each input element by
+-eps and compare against the finite-difference estimate. This is the
standard way to catch a backprop bug instead of trusting the math by
eye — run this before trusting the engine for any real training.
"""
from __future__ import annotations

import numpy as np
try:
    from .autodiff import Tensor
except ImportError:  # Also allow running this file directly.
    from autodiff import Tensor

np.random.seed(0)
EPS = 1e-5
TOL = 1e-4


def check(name: str, fn, *shapes):
    """fn(*tensors) -> scalar Tensor. Checks d(fn)/d(input) for every input."""
    inputs = [Tensor(np.random.randn(*s), requires_grad=True) for s in shapes]
    out = fn(*inputs)
    assert out.data.shape == () or out.data.size == 1, f"{name}: fn must return a scalar"
    for t in inputs:
        t.zero_grad()
    out.backward()

    max_err = 0.0
    for idx, t in enumerate(inputs):
        analytic = t.grad.copy()
        numeric = np.zeros_like(t.data)
        it = np.nditer(t.data, flags=["multi_index"])
        for _ in it:
            i = it.multi_index
            orig = t.data[i]
            t.data[i] = orig + EPS
            plus = fn(*inputs).data.reshape(-1)[0]
            t.data[i] = orig - EPS
            minus = fn(*inputs).data.reshape(-1)[0]
            t.data[i] = orig
            numeric[i] = (plus - minus) / (2 * EPS)
        err = np.max(np.abs(analytic - numeric))
        max_err = max(max_err, err)
    status = "OK" if max_err < TOL else "FAIL"
    print(f"{status:5s} {name:30s} max_err={max_err:.2e}")
    return max_err < TOL


results = []
results.append(check("add+broadcast", lambda a, b: (a + b).sum(), (3, 4), (1, 4)))
results.append(check("mul", lambda a, b: (a * b).sum(), (3, 4), (3, 4)))
results.append(check("sub", lambda a, b: (a - b).sum(), (3, 4), (3, 4)))
results.append(check("pow", lambda a: (a ** 3).sum(), (4,)))
results.append(check("matmul", lambda a, b: a.matmul(b).sum(), (3, 4), (4, 5)))
results.append(check("transpose", lambda a: a.transpose().sum(), (3, 4)))
results.append(check("reshape", lambda a: a.reshape(2, 6).sum(), (3, 4)))
results.append(check("sum-axis", lambda a: a.sum(axis=0).sum(), (3, 4)))
results.append(check("mean", lambda a: a.mean(), (3, 4)))
results.append(check("relu", lambda a: (a.relu() * a).sum(), (5,)))  # *a to keep it away from the kink at 0
results.append(check("tanh", lambda a: a.tanh().sum(), (5,)))
results.append(check("softmax", lambda a: (a.softmax(axis=-1) * a).sum(), (3, 4)))

# A composite check: a 2-layer MLP (Linear -> ReLU -> Linear), the actual
# pattern used in the Transformer's feed-forward sublayer.
def mlp(x, w1, b1, w2, b2):
    h = (x.matmul(w1) + b1).relu()
    return (h.matmul(w2) + b2).sum()

results.append(check("mlp (linear-relu-linear)", mlp, (2, 4), (4, 6), (1, 6), (6, 3), (1, 3)))

# A composite check mimicking one self-attention head: Q,K,V projections,
# scaled dot-product attention, softmax, weighted sum.
def attention_head(x, wq, wk, wv):
    q = x.matmul(wq)
    k = x.matmul(wk)
    v = x.matmul(wv)
    scores = q.matmul(k.transpose()) * (1.0 / np.sqrt(q.shape[-1]))
    attn = scores.softmax(axis=-1)
    return attn.matmul(v).sum()

results.append(check("attention head", attention_head, (3, 4), (4, 4), (4, 4), (4, 4)))

print()
if all(results):
    print(f"ALL {len(results)} CHECKS PASSED")
else:
    print(f"{sum(results)}/{len(results)} passed — DO NOT trust this engine for training until all pass")
    raise SystemExit(1)
