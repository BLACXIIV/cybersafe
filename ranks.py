"""Rank / badge helpers shared by main and admin views."""

BADGE_TITLES = {
    "Bronze": "Cyber Rookie",
    "Silver": "Cyber Sentinel",
    "Gold": "Cyber Guardian",
    "Platinum": "Cyber Master",
}

BADGE_ORDER = {"Bronze": 0, "Silver": 1, "Gold": 2, "Platinum": 3}


def rank_info(points, max_points):
    """Return current badge, hero title, next badge name and next threshold."""
    if max_points <= 0:
        return "Bronze", BADGE_TITLES["Bronze"], "Silver", BADGE_TITLES["Silver"], 0, 0
    t1 = max_points // 4
    t2 = max_points // 2
    t3 = max_points * 3 // 4
    if points < t1:
        return "Bronze", BADGE_TITLES["Bronze"], "Silver", BADGE_TITLES["Silver"], t1, t1 - points
    if points < t2:
        return "Silver", BADGE_TITLES["Silver"], "Gold", BADGE_TITLES["Gold"], t2, t2 - points
    if points < t3:
        return "Gold", BADGE_TITLES["Gold"], "Platinum", BADGE_TITLES["Platinum"], t3, t3 - points
    return "Platinum", BADGE_TITLES["Platinum"], None, None, max_points, 0
