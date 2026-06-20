"""Poisson + Dixon-Coles model utilities for football scorelines."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Dict, Iterable, Tuple

import numpy as np
from scipy.special import gammaln  # type: ignore


@dataclass
class DCParams:
    attack: np.ndarray  # size N-1
    defense: np.ndarray  # size N-1
    home_adv: float
    rho_raw: float

    def rho(self) -> float:
        # constrain to (-1, 1)
        return np.tanh(self.rho_raw)


def _expand_params(params: DCParams, n_teams: int) -> Tuple[np.ndarray, np.ndarray, float, float]:
    if n_teams < 2:
        raise ValueError("Need at least 2 teams")
    attack = np.zeros(n_teams)
    defense = np.zeros(n_teams)
    attack[:-1] = params.attack
    defense[:-1] = params.defense
    attack[-1] = -attack[:-1].sum()
    defense[-1] = -defense[:-1].sum()
    return attack, defense, params.home_adv, params.rho()


def dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - (lam * mu * rho)
    if x == 0 and y == 1:
        return 1 + (lam * rho)
    if x == 1 and y == 0:
        return 1 + (mu * rho)
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def poisson_pmf(k: int, lam: float) -> float:
    return np.exp(-lam) * (lam ** k) / math.factorial(k)


def match_ll(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    base = poisson_pmf(x, lam) * poisson_pmf(y, mu)
    tau = dc_tau(x, y, lam, mu, rho)
    val = base * tau
    if val <= 0:
        return -1e9
    return np.log(val)


def build_team_index(teams: Iterable[str]) -> Dict[str, int]:
    return {t: i for i, t in enumerate(sorted(set(teams)))}


def expected_goals(
    home_idx: int,
    away_idx: int,
    attack: np.ndarray,
    defense: np.ndarray,
    home_adv: float,
) -> Tuple[float, float]:
    lam = np.exp(home_adv + attack[home_idx] - defense[away_idx])
    mu = np.exp(attack[away_idx] - defense[home_idx])
    return lam, mu


def time_decay_weights(dates: np.ndarray, xi: float) -> np.ndarray:
    # xi controls decay rate; larger => faster decay
    max_date = dates.max()
    days = (max_date - dates).astype("timedelta64[D]").astype(int)
    return np.exp(-xi * days)


def log_likelihood(
    params_vec: np.ndarray,
    data,
    team_index: Dict[str, int],
    xi: float,
) -> float:
    # Vectorized log-likelihood for speed.
    n_teams = len(team_index)
    n = n_teams - 1
    attack = params_vec[:n]
    defense = params_vec[n : 2 * n]
    home_adv = params_vec[2 * n]
    rho_raw = params_vec[2 * n + 1]
    params = DCParams(attack=attack, defense=defense, home_adv=home_adv, rho_raw=rho_raw)
    attack_full, defense_full, home_adv, rho = _expand_params(params, n_teams)

    dates = data["Date"].values.astype("datetime64[D]")
    weights = time_decay_weights(dates, xi)

    home_idx = data["HomeTeam"].map(team_index).values
    away_idx = data["AwayTeam"].map(team_index).values
    x = data["FTHG"].values.astype(int)
    y = data["FTAG"].values.astype(int)

    lam = np.exp(home_adv + attack_full[home_idx] - defense_full[away_idx])
    mu = np.exp(attack_full[away_idx] - defense_full[home_idx])
    lam = np.clip(lam, 1e-9, None)
    mu = np.clip(mu, 1e-9, None)

    log_p = -lam + x * np.log(lam) - gammaln(x + 1)
    log_p += -mu + y * np.log(mu) - gammaln(y + 1)

    tau = np.ones_like(lam)
    mask00 = (x == 0) & (y == 0)
    mask01 = (x == 0) & (y == 1)
    mask10 = (x == 1) & (y == 0)
    mask11 = (x == 1) & (y == 1)
    tau[mask00] = 1 - (lam[mask00] * mu[mask00] * rho)
    tau[mask01] = 1 + (lam[mask01] * rho)
    tau[mask10] = 1 + (mu[mask10] * rho)
    tau[mask11] = 1 - rho
    tau = np.clip(tau, 1e-9, None)

    ll = np.sum(weights * (log_p + np.log(tau)))
    return float(ll)


def predict_probabilities(
    home: str,
    away: str,
    team_index: Dict[str, int],
    attack: np.ndarray,
    defense: np.ndarray,
    home_adv: float,
    rho: float,
    max_goals: int = 10,
) -> Tuple[float, float, float]:
    h = team_index[home]
    a = team_index[away]
    lam, mu = expected_goals(h, a, attack, defense, home_adv)

    probs = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            probs[i, j] = poisson_pmf(i, lam) * poisson_pmf(j, mu) * dc_tau(i, j, lam, mu, rho)
    probs = probs / probs.sum()

    home_win = probs[np.triu_indices(max_goals + 1, 1)].sum()
    draw = np.trace(probs)
    away_win = probs[np.tril_indices(max_goals + 1, -1)].sum()
    return home_win, draw, away_win
