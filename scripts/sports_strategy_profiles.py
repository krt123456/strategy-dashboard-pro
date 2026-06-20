"""Central sport strategy profiles for the daily 1xBet advisor."""
from __future__ import annotations

from typing import Any, Dict


SPORT_PROFILES: Dict[str, Dict[str, Any]] = {
    "football": {
        "sport_id": 1,
        "display": "Football",
        "model_status": "ACTIVE",
        "predictability_tier": "A_CORE",
        "predictability_score": 88,
        "public_watch": False,
        "strategy_gate": "Entry only if model pick, current 1xBet odds >= MinEntryOdds, EV >= +1.5%, source coverage is complete, and timing is safe.",
        "model_note": "Decision Brain + EV target price + external source review.",
        "required_context": "Team news, recent form, lineup/weather where relevant, and stale-history exclusion.",
    },
    "basketball": {
        "sport_id": 3,
        "display": "Basketball",
        "model_status": "ACTIVE",
        "predictability_tier": "A_CORE",
        "predictability_score": 86,
        "public_watch": False,
        "strategy_gate": "Entry if local model probability >= 0.72 (strong >= 0.78), probability margin >= 0.20 (strong >= 0.28), current 1xBet odds >= MinEntryOdds, and no roster/news conflict. Paper-trade tier at prob >= 0.60 for learning.",
        "model_note": "Market-consensus model + probability margin + EV target price. 2026-04-29 verified subset strengthened basketball raw memory but EV/source gates still decide.",
        "required_context": "Injuries, rotation/rest, league freshness, and price target.",
    },
    "tabletennis": {
        "sport_id": 10,
        "display": "Table Tennis",
        "model_status": "ACTIVE_SECONDARY",
        "predictability_tier": "B_SECONDARY",
        "predictability_score": 74,
        "public_watch": False,
        "strategy_gate": "Secondary only: probability >= 0.70, margin >= 0.35, odds <= 1.55, exact player/event match on 1xBet, and no result recheck due.",
        "model_note": "Fast individual sport with useful market signals but high schedule/result volatility.",
        "required_context": "Exact player match, event id, recent result state, and quick post-match recheck.",
    },
    "tennis": {
        "sport_id": 4,
        "display": "Tennis",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "B_WATCH",
        "predictability_score": 68,
        "public_watch": True,
        "min_prob": 0.64,
        "min_margin": 0.14,
        "min_odds": 1.25,
        "max_odds": 1.65,
        "haircut": 0.050,
        "decision": "WATCH_TENNIS_MODEL_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require active player model, injury/withdrawal check, surface fit, exact tournament round, and event id before review. Doubles stays deep-lab until pair-level memory reaches 65% over 8+ finished rows.",
        "model_note": "Singles remains a useful watch sport, but withdrawals and surface/form shocks make source checks mandatory. Doubles is isolated into a stricter pair-level segment.",
        "required_context": "Injury/withdrawal, surface, player form, tournament round, event start state, and for doubles the actual pair chemistry/serve-return profile.",
    },
    "hockey": {
        "sport_id": 2,
        "display": "Ice Hockey",
        "model_status": "PARTIAL",
        "predictability_tier": "B_WATCH",
        "predictability_score": 64,
        "public_watch": True,
        "min_prob": 0.58,
        "min_margin": 0.08,
        "min_odds": 1.45,
        "max_odds": 2.05,
        "haircut": 0.035,
        "decision": "WATCH_HOCKEY_GOALIE_REST_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require current fixture refresh, goalie/rest check, no overtime-market confusion, and verified match-winner market.",
        "model_note": "Partial local model exists; current fixtures and market mapping must be clean before promotion.",
        "required_context": "Goalies, rest/back-to-back, overtime rules, and league-specific market mapping.",
    },
    "handball": {
        "sport_id": 8,
        "display": "Handball",
        "model_status": "PARTIAL_BLOCKED",
        "predictability_tier": "C_LAB",
        "predictability_score": 58,
        "public_watch": True,
        "min_prob": 0.62,
        "min_margin": 0.12,
        "min_odds": 1.35,
        "max_odds": 1.85,
        "haircut": 0.050,
        "decision": "WATCH_HANDBALL_RETUNE",
        "strategy_gate": "WATCH_ONLY: require retuned league accuracy >= 72%, current fixtures, and removal of weak leagues.",
        "model_note": "Useful in selected leagues, but current health is not strong enough for entry review.",
        "required_context": "League health, recent fixtures, team strength gap, and weak-league blacklist.",
    },
    "volleyball": {
        "sport_id": 6,
        "display": "Volleyball",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "B_WATCH",
        "predictability_score": 62,
        "public_watch": True,
        "min_prob": 0.62,
        "min_margin": 0.12,
        "min_odds": 1.35,
        "max_odds": 1.90,
        "haircut": 0.050,
        "decision": "WATCH_VOLLEYBALL_SET_VOLATILITY",
        "strategy_gate": "WATCH_ONLY: build set-level volatility model, check rotation/news, and require stable match-winner mapping before review.",
        "model_note": "Promising team sport with enough 1xBet coverage, but set volatility needs a dedicated model.",
        "required_context": "Team rotation, injuries, set volatility, league quality, and event id.",
    },
    "baseball": {
        "sport_id": 5,
        "display": "Baseball",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "B_WATCH",
        "predictability_score": 50,
        "public_watch": True,
        "min_prob": 0.70,
        "min_margin": 0.16,
        "min_odds": 1.45,
        "max_odds": 1.70,
        "haircut": 0.075,
        "decision": "WATCH_BASEBALL_PITCHER_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require confirmed starting pitchers, lineup, bullpen/rest, weather/park, MLB/NCAA split backtest, and 70%+ repaired probability; college midweek rows stay lab-only until rotation risk is modelled.",
        "model_note": "2026-04-28 and 2026-04-29 feedback showed public-market baseball signal is too noisy without pitcher/lineup/weather context.",
        "required_context": "Starting pitchers, bullpen rest, confirmed lineup, weather, park factor, MLB/NCAA split, NCAA midweek rotation risk, and event id.",
    },
    "cricket": {
        "sport_id": 66,
        "display": "Cricket",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "C_LAB",
        "predictability_score": 56,
        "public_watch": True,
        "min_prob": 0.60,
        "min_margin": 0.10,
        "min_odds": 1.40,
        "max_odds": 2.05,
        "haircut": 0.055,
        "decision": "WATCH_CRICKET_TOSS_LINEUP_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require toss, pitch, lineup, format, and weather checks before review.",
        "model_note": "Can be useful, but toss and lineup uncertainty make early picks unsafe.",
        "required_context": "Toss, pitch, format, lineup, venue/weather, and event id.",
    },
    "americanfootball": {
        "sport_id": 13,
        "display": "American Football",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "C_LAB",
        "predictability_score": 54,
        "public_watch": True,
        "min_prob": 0.58,
        "min_margin": 0.09,
        "min_odds": 1.45,
        "max_odds": 2.10,
        "haircut": 0.050,
        "decision": "WATCH_AMFOOTBALL_QB_INJURY_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require QB/injury report, rest, spread movement, and weather before review.",
        "model_note": "Context-heavy; do not promote without injury/QB validation.",
        "required_context": "QB status, injury report, weather, rest, spread movement, and event id.",
    },
    "futsal": {
        "sport_id": 14,
        "display": "Futsal",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "C_LAB",
        "predictability_score": 52,
        "public_watch": True,
        "min_prob": 0.62,
        "min_margin": 0.12,
        "min_odds": 1.35,
        "max_odds": 1.90,
        "haircut": 0.060,
        "decision": "WATCH_FUTSAL_VOLATILITY",
        "strategy_gate": "WATCH_ONLY: high-scoring volatility; require league-specific model and lineup/context checks.",
        "model_note": "Market can identify favourites, but scoring volatility is high.",
        "required_context": "League volatility, lineup, recent goals profile, and event id.",
    },
    "darts": {
        "sport_id": 21,
        "display": "Darts",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "C_LAB",
        "predictability_score": 51,
        "public_watch": True,
        "min_prob": 0.64,
        "min_margin": 0.14,
        "min_odds": 1.30,
        "max_odds": 1.75,
        "haircut": 0.055,
        "decision": "WATCH_DARTS_FORM_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require recent form/leg format model before review.",
        "model_note": "Individual sport with visible favourites, but short-format variance is high.",
        "required_context": "Recent form, leg/set format, tournament stage, and event id.",
    },
    "snooker": {
        "sport_id": 30,
        "display": "Snooker",
        "model_status": "STRATEGY_LAB",
        "predictability_tier": "C_LAB",
        "predictability_score": 46,
        "public_watch": True,
        "min_prob": 0.68,
        "min_margin": 0.16,
        "min_odds": 1.35,
        "max_odds": 1.70,
        "haircut": 0.060,
        "decision": "WATCH_SNOOKER_FORMAT_REQUIRED",
        "strategy_gate": "WATCH_ONLY: require best-of format, recent form model, tournament stage, and no sub-70% favorite/upset band before review.",
        "model_note": "2026-04-29 Higgins-Robertson miss confirmed that format/stage/form context is mandatory before promotion.",
        "required_context": "Best-of format, recent form, tournament stage, and event id.",
    },
}

ALIASES = {
    "icehockey": "hockey",
    "ice_hockey": "hockey",
    "ice hockey": "hockey",
    "table_tennis": "tabletennis",
    "table tennis": "tabletennis",
    "american football": "americanfootball",
    "american_football": "americanfootball",
    "amfootball": "americanfootball",
}


def normalize_sport_key(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    raw = " ".join(raw.split())
    compact = raw.replace(" ", "")
    return ALIASES.get(raw) or ALIASES.get(compact) or compact


def get_profile(value: object) -> Dict[str, Any] | None:
    return SPORT_PROFILES.get(normalize_sport_key(value))


def sport_ids() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for key, profile in SPORT_PROFILES.items():
        out[key] = int(profile["sport_id"])
        out[str(profile["display"]).lower()] = int(profile["sport_id"])
    for alias, key in ALIASES.items():
        if key in SPORT_PROFILES:
            out[alias] = int(SPORT_PROFILES[key]["sport_id"])
    return out


def sport_labels() -> Dict[int, str]:
    return {int(profile["sport_id"]): key for key, profile in SPORT_PROFILES.items()}


def public_watch_configs() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key, profile in SPORT_PROFILES.items():
        if not profile.get("public_watch"):
            continue
        out[key] = {
            "id": int(profile["sport_id"]),
            "label": key,
            "display": profile["display"],
            "min_prob": float(profile["min_prob"]),
            "min_margin": float(profile["min_margin"]),
            "min_odds": float(profile["min_odds"]),
            "max_odds": float(profile["max_odds"]),
            "haircut": float(profile["haircut"]),
            "decision": profile["decision"],
            "strategy_gate": profile["strategy_gate"],
        }
    return out
