"""Sport-specific prediction science — domain knowledge per sport.

Each sport has its OWN physics. This module encodes the expert knowledge
that makes predictions accurate. No one-size-fits-all.

Sources: academic literature, professional betting analysis, reference repos,
decades of sports analytics research.
"""
from __future__ import annotations

SPORT_SCIENCE = {

    # ═══════════════════════════════════════════════════════════════════
    # BASKETBALL — Team sport, high scoring, strong favorites win often
    # ═══════════════════════════════════════════════════════════════════
    "basketball": {
        "physics": "High-possession team sport. Variance is LOW relative to other sports. Strong favorites win 85-90% of the time. Back-to-back games degrade performance by ~3-4 points.",
        "key_predictors": [
            ("ELO rating difference", 0.25, "Captures overall team strength over time"),
            ("Market consensus probability", 0.30, "Betexplorer odds aggregate sharp money"),
            ("Recent form (last 5 games)", 0.15, "Captures hot/cold streaks, injury impact"),
            ("Points scored/allowed (last 5)", 0.10, "Pace-adjusted offensive/defensive efficiency"),
            ("Rest days", 0.08, "Back-to-back = fatigue, 3+ days = fresh"),
            ("Home court advantage", 0.07, "Worth ~65 ELO points (~3 points)"),
            ("Head-to-head record", 0.05, "Matchup-specific dynamics"),
        ],
        "model_type": "LightGBM + isotonic calibration",
        "calibration_target": "Raw ML prob → actual win rate per bucket",
        "vig_removal": "Standard 2-way: implied_home + implied_away, normalize to 1.0",
        "edge_lives": [
            "Small leagues (NCAA, Australia NBL1, Philippines MPBL) where bookmakers invest less analysis",
            "Back-to-back fatigue not yet priced in",
            "Late-season games where motivation differs (playoff-bound vs eliminated)",
            "Women's leagues (less market attention = more inefficiency)",
        ],
        "anti_patterns": [
            "Never bet on exhibition games",
            "Be cautious with playoff game 7s (variance increases)",
            "Avoid leagues with match-fixing history",
        ],
        "bankroll_rule": "Quarter-Kelly, max 5% per bet, max 20% daily exposure",
    },

    # ═══════════════════════════════════════════════════════════════════
    # TENNIS — Individual sport, surface-dependent, serve-dominated
    # ═══════════════════════════════════════════════════════════════════
    "tennis": {
        "physics": "Individual sport. Surface is EVERYTHING — a player dominant on clay may be mediocre on grass. Serve dominates: top servers hold 85-90% of service games. Variance is HIGH in best-of-3, LOWER in best-of-5.",
        "key_predictors": [
            ("Surface-specific ELO", 0.30, "Separate ELO for clay/grass/hard. A player's clay ELO tells you nothing about grass"),
            ("Overall ELO", 0.15, "General strength baseline"),
            ("Serve hold % (recent)", 0.15, "How often player holds serve — defines level"),
            ("Break point conversion", 0.10, "Mental strength under pressure"),
            ("Rest/tournament fatigue", 0.10, "Matches in last 7 days, minutes on court"),
            ("Surface fit score", 0.08, "Historical win rate on this surface"),
            ("Head-to-head", 0.07, "Matchup styles (lefty vs righty, big server vs returner)"),
            ("Ranking difference", 0.05, "Less predictive than ELO but captures recent official results"),
        ],
        "model_type": "LightGBM per-surface OR logistic regression with surface-ELO features",
        "surface_dynamics": {
            "clay": "Slow bounce, long rallies, physical endurance matters most. Nadal effect. Favorites win less often.",
            "grass": "Fast, low bounce, serve dominates. Shorter points. Upsets more common. Serve-and-volley rewards.",
            "hard": "Medium pace, predictable bounce. Most balanced surface. Most data available.",
            "indoor": "No wind, predictable conditions. Serve advantage increases.",
        },
        "calibration_target": "Per-surface calibration. Clay predictions need different calibration than grass.",
        "special_considerations": [
            "Doubles is COMPLETELY different from singles — separate model required",
            "Qualification rounds and Challenger events have high variance",
            "Withdrawals/retirements are common in lower-tier events",
            "Surface transition periods (clay→grass in June) cause upset spikes",
            "Best-of-5 (Grand Slams) reduces variance vs best-of-3",
            "Late-round fatigue: player in QF/SF may be exhausted",
        ],
        "edge_lives": [
            "ITF/Challenger events where bookmakers invest less analysis",
            "Surface transition weeks (early grass season after Roland Garros)",
            "Qualifying rounds — bookmakers often use generic player ratings",
            "Players returning from injury (market overreacts to ranking)",
        ],
        "anti_patterns": [
            "Never bet on doubles without pair-level history",
            "Avoid players with recent retirement history",
            "Be cautious in first round of tournaments (upset frequency higher)",
            "Don't trust ATP ranking for surface-specific predictions",
        ],
        "bankroll_rule": "Quarter-Kelly, max 3% per bet (tennis has higher variance), max 15% daily",
    },

    # ═══════════════════════════════════════════════════════════════════
    # FOOTBALL — Low-scoring, draw-heavy, tactical
    # ═══════════════════════════════════════════════════════════════════
    "football": {
        "physics": "Low-scoring sport (2-3 goals per game). Draw frequency: ~25-28% of matches. A single goal changes everything. Home advantage exists but is declining. Tactics matter enormously.",
        "key_predictors": [
            ("Attack strength (Poisson λ_home)", 0.20, "Expected goals scored at home"),
            ("Defense strength (Poisson λ_away)", 0.20, "Expected goals conceded"),
            ("Recent form (last 6 games)", 0.15, "Points per game, goal difference"),
            ("Draw risk score", 0.15, "CRITICAL: 20/29 prediction errors were draws. Separate model needed."),
            ("Home advantage", 0.10, "Declining but still worth ~0.3 expected goals"),
            ("Head-to-head", 0.08, "Tactical matchup history"),
            ("Schedule congestion", 0.07, "3 games in 7 days = fatigue"),
            ("Motivation/Context", 0.05, "Derby, relegation battle, title race — changes effort"),
        ],
        "model_type": "Dixon-Coles Poisson + Draw Risk overlay + ML correction",
        "draw_risk_features": [
            "Probability margin (close odds = high draw risk)",
            "Referee draw rate (some referees draw more)",
            "League draw frequency (Serie A draws more than Bundesliga)",
            "Stage of season (late season = more draws between safe teams)",
            "Goal difference form (both teams low-scoring = draw likely)",
        ],
        "calibration_target": "Per-league calibration ( Serie A ≠ Bundesliga ≠ Premier League)",
        "special_considerations": [
            "Draw is the #1 prediction error source",
            "Red cards change everything but are unpredictable",
            "VAR has reduced home advantage since 2019",
            "Transfer window disruption: new players = chemistry issues",
            "Managerial changes: short-term boost effect (new manager bounce)",
            "Empty stadiums reduced home advantage during COVID",
        ],
        "edge_lives": [
            "Lower-division leagues (less market analysis)",
            "Cup competitions (motivation differs by team priority)",
            "Early season (market uses last year's data)",
            "Post-international-break games (key players fatigued)",
        ],
        "anti_patterns": [
            "Never bet on friendlies",
            "Be cautious with newly promoted teams (market overvalues them)",
            "Avoid matches with confirmed absent key players if not priced in",
            "Don't trust form from different competition (Champions League ≠ league)",
        ],
        "bankroll_rule": "Quarter-Kelly, max 4% per bet, max 15% daily. Extra caution on draw-risk matches.",
    },

    # ═══════════════════════════════════════════════════════════════════
    # DARTS — Individual, form-driven, mental game
    # ═══════════════════════════════════════════════════════════════════
    "darts": {
        "physics": "Pure individual skill sport. The 3-dart average IS the player's level. Very streaky — form fluctuates dramatically. Mental pressure matters: some players are 'TV players'.",
        "key_predictors": [
            ("3-dart average (last 5 matches)", 0.35, "THE predictor. Pro average: 95-110. Elite: 100+. World-class: 105+"),
            ("Checkout percentage", 0.20, "Mental strength under pressure. 40%+ is excellent"),
            ("180s per leg", 0.15, "Maximum scoring power indicator"),
            ("Recent form trend", 0.15, "Darts is VERY streaky — momentum is real"),
            ("Format/leg count", 0.10, "Longer formats favor consistent players"),
            ("TV vs floor performance", 0.05, "Stage presence matters for some players"),
        ],
        "model_type": "Logistic regression on 3-dart average + form trend",
        "special_considerations": [
            "Darts is the MOST form-driven sport — hot/cold streaks define outcomes",
            "Short format (best of 7 legs) = high variance",
            "Long format (best of 15+ legs) = skill dominates",
            "Modus Super Series has different dynamics than PDC tour",
        ],
        "edge_lives": [
            "Modus Super Series (less market attention than PDC)",
            "Early-round matches where casual fans don't track form",
            "Players returning from break (market uses stale form data)",
        ],
        "bankroll_rule": "Quarter-Kelly, max 3% per bet, max 12% daily",
    },

    # ═══════════════════════════════════════════════════════════════════
    # TABLE TENNIS — Fast individual sport, high variance
    # ═══════════════════════════════════════════════════════════════════
    "tabletennis": {
        "physics": "Extremely fast individual sport. Match duration: 15-30 minutes. High variance — upsets common. Best-of-7 sets reduces variance vs best-of-5.",
        "key_predictors": [
            ("ELO rating", 0.30, "General player strength"),
            ("Recent form (last 10 matches)", 0.25, "Table tennis is very form-dependent"),
            ("H2H record", 0.15, "Style matchup matters (lefty, pimpled rubber, etc.)"),
            ("Tournament/league context", 0.15, "Some players perform differently in team events"),
            ("Rest between matches", 0.10, "Tournament fatigue in deep runs"),
            ("Equipment familiarity", 0.05, "Ball/table changes affect some players more"),
        ],
        "model_type": "Logistic regression + ELO",
        "special_considerations": [
            "Russian/Czech leagues have the most data and liquidity",
            "Match fixing risk in some lower-tier events",
            "Pimpled rubber players cause stylistic upsets",
            "Best-of-5 vs best-of-7 changes variance significantly",
        ],
        "edge_lives": [
            "Lower-tier leagues with less analysis",
            "Qualification rounds",
            "Players returning from injury/break",
        ],
        "bankroll_rule": "Quarter-Kelly, max 3% per bet, max 15% daily",
    },
}


def get_sport_config(sport: str) -> dict:
    sport = sport.lower().strip()
    if sport in SPORT_SCIENCE:
        return SPORT_SCIENCE[sport]
    aliases = {
        "bb": "basketball", "bball": "basketball",
        "tn": "tennis", "atp": "tennis", "wta": "tennis",
        "fb": "football", "soccer": "football",
        "dt": "darts",
        "tt": "tabletennis", "ping pong": "tabletennis",
    }
    return SPORT_SCIENCE.get(aliases.get(sport, ""), {})


def predict_draw_risk(
    home_attack: float,
    away_attack: float,
    home_defense: float,
    away_defense: float,
    league_draw_rate: float = 0.26,
    prob_margin: float = 0.0,
) -> float:
    """Football draw risk model. Returns probability of draw (0-1).

    Based on Dixon-Coles + empirical draw frequency analysis.
    Key insight: draws happen when both teams have similar strength AND low scoring.
    """
    lambda_home = max(0.1, home_attack * away_defense)
    lambda_away = max(0.1, away_attack * home_defense)

    total_expected = lambda_home + lambda_away
    goal_diff = abs(lambda_home - lambda_away)

    poisson_draw = 0.0
    for h in range(6):
        from math import exp, factorial
        p_h = exp(-lambda_home) * lambda_home**h / factorial(h)
        for a in range(6):
            if h == a:
                p_a = exp(-lambda_away) * lambda_away**a / factorial(a)
                poisson_draw += p_h * p_a

    margin_factor = max(0.5, 1.0 - prob_margin * 2.0)
    low_scoring_factor = max(0.8, min(1.3, 3.0 / max(total_expected, 0.5)))

    draw_prob = poisson_draw * margin_factor * low_scoring_factor
    draw_prob = draw_prob * 0.7 + league_draw_rate * 0.3

    return min(max(draw_prob, 0.05), 0.45)


def surface_elo_expected(home_surface_elo: float, away_surface_elo: float, best_of: int = 3) -> float:
    """Tennis expected win probability using surface-specific ELO.

    best_of: 3 for standard tournaments, 5 for Grand Slams.
    In best-of-5, the better player wins MORE often (less variance).
    """
    elo_diff = home_surface_elo - away_surface_elo
    base_expected = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

    if best_of == 5:
        if base_expected > 0.5:
            base_expected = 0.5 + (base_expected - 0.5) * 1.15
        else:
            base_expected = 0.5 - (0.5 - base_expected) * 1.15

    return min(max(base_expected, 0.01), 0.99)


if __name__ == "__main__":
    print("=" * 60)
    print("  SPORT PREDICTION SCIENCE — Domain Knowledge Base")
    print("=" * 60)

    for sport, config in SPORT_SCIENCE.items():
        print(f"\n{'─'*50}")
        print(f"  {sport.upper()}")
        print(f"{'─'*50}")
        print(f"  Physics: {config['physics'][:100]}...")
        print(f"  Model: {config['model_type']}")
        print(f"  Top predictors:")
        for name, weight, desc in config["key_predictors"][:3]:
            print(f"    {weight:.0%} — {name}: {desc[:60]}")
        print(f"  Edge lives in: {config['edge_lives'][0][:70]}")

    print(f"\n{'='*60}")
    print(f"  Draw risk test (football): {predict_draw_risk(1.5, 1.3, 0.9, 0.8, 0.26, 0.1):.1%}")
    print(f"  Surface ELO test (tennis): ELO diff=200 → {surface_elo_expected(1700, 1500, 3):.1%} (BO3), {surface_elo_expected(1700, 1500, 5):.1%} (BO5)")
