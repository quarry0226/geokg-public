"""AHP elicitation for safety-score weight calibration.

Each author independently completes the 10 pairwise comparisons (5C2)
between the five safety indicators on Saaty's 1-9 scale; the matrices
are aggregated by geometric mean, the principal eigenvector is normalised
to obtain the calibrated weights, and Saaty's Consistency Ratio (CR) is
reported. Result feeds Section III.E.4 / Section VI.E of the paper.

Saaty's intensity scale:
  1 = equal importance
  3 = moderate
  5 = strong
  7 = very strong
  9 = extreme
  2,4,6,8 = intermediate values
  reciprocal = inverse comparison (1/3, 1/5, ...)

Indicator order: [shelter, monitor, transit, park, road]
"""
import numpy as np

INDICATORS = ["shelter", "monitor", "transit", "park", "road"]

# -------------------------------------------------------------------------
# Pairwise comparison matrices (one per author)
# Filled in by the four authors based on:
#   - Korean Ministry of Public Safety Disaster Vulnerability Assessment
#     ordering (shelter access > monitoring > transit > park > road)
#   - FEMA Hazus life-safety dimension (shelter and emergency-facility
#     access as the top determinant)
#   - WHO Healthy-Cities indicators (shelter and basic services > amenities)
# -------------------------------------------------------------------------

# Author 1 — urban-informatics emphasis
A1 = np.array([
    # shelter monitor transit park  road
    [   1,      3,      3,    5,    5  ],   # shelter
    [ 1/3,      1,      1,    3,    3  ],   # monitor
    [ 1/3,      1,      1,    3,    3  ],   # transit
    [ 1/5,    1/3,    1/3,    1,    1  ],   # park
    [ 1/5,    1/3,    1/3,    1,    1  ],   # road
])

# Author 2 — GIS / cadastral emphasis
A2 = np.array([
    [   1,      2,      3,    5,    5  ],
    [ 1/2,      1,      2,    3,    3  ],
    [ 1/3,    1/2,      1,    3,    3  ],
    [ 1/5,    1/3,    1/3,    1,    1  ],
    [ 1/5,    1/3,    1/3,    1,    1  ],
])

# Author 3 — knowledge-graph / analytics emphasis
A3 = np.array([
    [   1,      3,      3,    5,    4  ],
    [ 1/3,      1,      1,    3,    2  ],
    [ 1/3,      1,      1,    3,    3  ],
    [ 1/5,    1/3,    1/3,    1,    1  ],
    [ 1/4,    1/2,    1/3,    1,    1  ],
])

# Author 4 — infrastructure / transportation emphasis
A4 = np.array([
    [   1,      3,      3,    4,    4  ],
    [ 1/3,      1,      1,    2,    2  ],
    [ 1/3,      1,      1,    2,    2  ],
    [ 1/4,    1/2,    1/2,    1,    1  ],
    [ 1/4,    1/2,    1/2,    1,    1  ],
])


def geometric_mean_aggregation(matrices):
    """Aggregate multiple pairwise-comparison matrices via element-wise GM."""
    stack = np.stack(matrices, axis=0)
    log_stack = np.log(stack)
    log_mean = log_stack.mean(axis=0)
    return np.exp(log_mean)


def principal_eigenvector(M):
    """Return the normalised principal right eigenvector of M."""
    eigvals, eigvecs = np.linalg.eig(M)
    # principal eigenvalue = largest real part
    idx = np.argmax(eigvals.real)
    v = np.abs(eigvecs[:, idx].real)
    return v / v.sum(), eigvals[idx].real


def consistency_ratio(M, lambda_max):
    """Saaty Consistency Ratio: CI / RI."""
    n = M.shape[0]
    CI = (lambda_max - n) / (n - 1)
    # Saaty's Random Consistency Index for n=5
    RI_TABLE = {1: 0, 2: 0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32}
    RI = RI_TABLE.get(n, 1.49)
    return CI / RI


def main():
    print("=" * 60)
    print("AHP Elicitation for Safety-Score Weights")
    print("=" * 60)

    matrices = [A1, A2, A3, A4]
    authors = ["Author 1 (urban informatics)",
               "Author 2 (GIS / cadastral)",
               "Author 3 (KG / analytics)",
               "Author 4 (infrastructure)"]

    # Per-author analysis
    for name, A in zip(authors, matrices):
        w, lam = principal_eigenvector(A)
        cr = consistency_ratio(A, lam)
        print(f"\n{name}")
        print(f"  weights: {dict(zip(INDICATORS, np.round(w, 3)))}")
        print(f"  λ_max   = {lam:.4f}")
        print(f"  CR      = {cr:.4f}  ({'consistent' if cr < 0.10 else 'INCONSISTENT — revise'})")

    # Geometric-mean aggregation
    M_consensus = geometric_mean_aggregation(matrices)
    w, lam = principal_eigenvector(M_consensus)
    cr = consistency_ratio(M_consensus, lam)

    print("\n" + "-" * 60)
    print("Aggregated (geometric mean across 4 authors)")
    print("-" * 60)
    print("\nPairwise comparison matrix:")
    print(f"{'':>10} " + " ".join(f"{ind:>8}" for ind in INDICATORS))
    for i, ind in enumerate(INDICATORS):
        row = " ".join(f"{M_consensus[i,j]:>8.3f}" for j in range(5))
        print(f"{ind:>10} {row}")

    print(f"\nConsensus weights:")
    for ind, val in zip(INDICATORS, w):
        print(f"  {ind:>8s}: {val:.4f}")
    print(f"\nλ_max = {lam:.4f}")
    print(f"CR    = {cr:.4f}  ({'consistent' if cr < 0.10 else 'INCONSISTENT'})")

    # Compare with paper's heuristic weights
    paper_weights = {"shelter": 0.30, "monitor": 0.20, "transit": 0.20,
                     "park": 0.15, "road": 0.15}
    print("\n" + "-" * 60)
    print("Cross-comparison with paper's heuristic weights")
    print("-" * 60)
    print(f"{'indicator':>10} {'paper':>8} {'AHP':>8} {'Δ':>8}")
    for ind in INDICATORS:
        pw = paper_weights[ind]
        aw = w[INDICATORS.index(ind)]
        delta = aw - pw
        print(f"{ind:>10} {pw:>8.3f} {aw:>8.4f} {delta:>+8.4f}")

    # Spearman rank correlation between paper and AHP rankings
    paper_arr = np.array([paper_weights[i] for i in INDICATORS])
    ahp_arr = w
    paper_rank = paper_arr.argsort().argsort()
    ahp_rank = ahp_arr.argsort().argsort()
    n = len(INDICATORS)
    d2 = ((paper_rank - ahp_rank) ** 2).sum()
    rho = 1 - 6 * d2 / (n * (n*n - 1))
    print(f"\nSpearman rank correlation (paper vs. AHP): ρ = {rho:.3f}")

    return {"consensus_matrix": M_consensus,
            "weights": dict(zip(INDICATORS, w.tolist())),
            "lambda_max": float(lam),
            "consistency_ratio": float(cr),
            "spearman_rho": float(rho)}


if __name__ == "__main__":
    res = main()
